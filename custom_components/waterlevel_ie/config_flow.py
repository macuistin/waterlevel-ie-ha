"""Config flow for Waterlevel.ie — browse by river or enter a station number."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_LOCATION, CONF_RADIUS
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    LocationSelector,
    LocationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util.location import distance

# Home Assistant 2026.10 replaced voluptuous with probatio, which has the same API.
try:
    import probatio as vol
except ImportError:  # pragma: no cover - Home Assistant before 2026.10
    import voluptuous as vol  # type: ignore[no-redef]

from .api import StationNotFound, WaterlevelApi, WaterlevelError
from .const import (
    CONF_ALERT_STAGE,
    CONF_AVAILABLE_SENSORS,
    CONF_FLOW_MEASURE,
    CONF_FLOW_TS,
    CONF_LATITUDE,
    CONF_LEVEL_MEASURE,
    CONF_LEVEL_TS,
    CONF_LONGITUDE,
    CONF_REDETECT,
    CONF_RLOI_ID,
    CONF_SCAN_INTERVAL,
    CONF_SOURCE,
    CONF_STALE_HOURS,
    CONF_STATION,
    CONF_STATION_INFO,
    CONF_STATION_NAME,
    DEFAULT_ALERT_STAGE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_HOURS,
    DOMAIN,
    FLOOD_STAGES,
    GROUP_LIST_URL,
    MIN_SCAN_INTERVAL,
    SEPA_SCAN_INTERVAL,
    SOURCE_EA,
    SOURCE_OPW,
    SOURCE_SEPA,
    WATER_LEVEL,
)
from .ea_api import EnvironmentAgencyApi
from .sepa_api import SepaApi

_LOGGER = logging.getLogger(__name__)
CONF_GROUP = "group"
DEFAULT_NEARBY_RADIUS = 15000  # metres
# The Environment Agency search is limited to this distance, in km
MAX_EA_DISTANCE_KM = 100


def normalize_station(raw: str) -> str | None:
    """Turn user input into a 5-digit station number, or None if it isn't one.

    Accepts a plain number ("14029", "0000014029") or a pasted station URL
    ("https://waterlevel.ie/0000014029/").
    """
    raw = raw.strip()
    if m := re.search(r"\d{10}", raw):
        digits = m.group(0)
    elif raw.isdigit():
        digits = raw
    else:
        return None
    if int(digits) == 0:
        return None
    return str(int(digits)).zfill(5)


async def _detect_station(
    api: WaterlevelApi, station: str, name_hint: str | None = None
) -> dict[str, Any]:
    """Probe a station and build its config entry data.

    name_hint: preferred display name (e.g. from the river group picker).
    Falls back to the station page title, then to 'Station {station}'.
    """
    page_name, available = await api.detect_station(station)
    result: dict[str, Any] = {
        CONF_STATION: station,
        CONF_STATION_NAME: name_hint or page_name or f"Station {station}",
        CONF_AVAILABLE_SENSORS: available,
    }
    if coords := await api.station_coords(station):
        result[CONF_LATITUDE], result[CONF_LONGITUDE] = coords
    return result


def _england_entry_data(station: dict[str, Any]) -> dict[str, Any]:
    """The config entry data for an Environment Agency station from EnvironmentAgencyApi."""
    available = [WATER_LEVEL] + (["discharge"] if station["flow_measure"] else [])
    details = {"status": "active"}
    if station["catchment"]:
        details["catchment"] = station["catchment"]
    return {
        CONF_SOURCE: SOURCE_EA,
        CONF_STATION: station["station"],
        CONF_STATION_NAME: station["name"],
        CONF_AVAILABLE_SENSORS: available,
        CONF_LATITUDE: station["latitude"],
        CONF_LONGITUDE: station["longitude"],
        CONF_LEVEL_MEASURE: station["level_measure"],
        CONF_FLOW_MEASURE: station["flow_measure"],
        CONF_RLOI_ID: station["rloi_id"],
        # The stage scale becomes the default flood thresholds (see thresholds.py)
        CONF_STATION_INFO: {"details": details, "levels": station["stage_scale"]},
    }


def _scotland_entry_data(
    station: dict[str, Any], level_ts: str, flow_ts: str | None
) -> dict[str, Any]:
    """The config entry data for a SEPA station and its time series."""
    return {
        CONF_SOURCE: SOURCE_SEPA,
        CONF_STATION: station["station"],
        CONF_STATION_NAME: station["name"],
        CONF_AVAILABLE_SENSORS: [WATER_LEVEL] + (["discharge"] if flow_ts else []),
        CONF_LATITUDE: station["latitude"],
        CONF_LONGITUDE: station["longitude"],
        CONF_LEVEL_TS: level_ts,
        CONF_FLOW_TS: flow_ts,
        CONF_STATION_INFO: {"details": {"river": station["river"]} if station["river"] else {}},
    }


class WaterlevelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow: choose how to find a station, then add it."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> WaterlevelOptionsFlow:
        return WaterlevelOptionsFlow()

    def __init__(self) -> None:
        self._groups: dict[str, str] = {}
        self._selected_group: str | None = None
        self._stations: dict[str, str] = {}
        self._distances: dict[str, float] = {}
        self._ea_stations: dict[str, dict[str, Any]] = {}
        self._sepa_stations: dict[str, dict[str, Any]] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask whether to find stations nearby, browse by river, or type a station number."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["nearby", "river", "station_number", "england", "scotland"],
        )

    async def async_step_nearby(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a location and radius (the home location by default)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            location = user_input[CONF_LOCATION]
            try:
                stations = await WaterlevelApi(async_get_clientsession(self.hass)).stations()
            except WaterlevelError as err:
                _LOGGER.warning("Failed to fetch the station list: %s", err)
                errors["base"] = "cannot_connect"
            else:
                configured = self._async_current_ids()
                nearby = []
                for sid, (name, lat, lon) in stations.items():
                    if f"{DOMAIN}_{sid}" in configured:
                        continue
                    dist = distance(location[CONF_LATITUDE], location[CONF_LONGITUDE], lat, lon)
                    if dist is not None and dist <= location[CONF_RADIUS]:
                        nearby.append((dist, sid, name))
                nearby.sort()
                if nearby:
                    self._stations = {sid: name for _, sid, name in nearby}
                    self._distances = {sid: dist for dist, sid, _ in nearby}
                    return await self.async_step_nearby_station()
                errors["base"] = "no_nearby_stations"
        default = (user_input or {}).get(CONF_LOCATION) or {
            CONF_LATITUDE: self.hass.config.latitude,
            CONF_LONGITUDE: self.hass.config.longitude,
            CONF_RADIUS: DEFAULT_NEARBY_RADIUS,
        }
        return self.async_show_form(
            step_id="nearby",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOCATION, default=default): LocationSelector(
                        LocationSelectorConfig(radius=True)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_nearby_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one of the stations found nearby, closest first."""
        errors: dict[str, str] = {}
        if user_input is not None:
            station = user_input[CONF_STATION]
            result = await self._async_add_station(
                station, errors, name_hint=self._stations.get(station)
            )
            if result is not None:
                return result
        return self.async_show_form(
            step_id="nearby_station",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=sid,
                                    label=f"{name} ({sid}), {self._distances[sid] / 1000:.1f} km",
                                )
                                for sid, name in self._stations.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_england(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a location and radius to look for Environment Agency stations in England."""
        errors: dict[str, str] = {}
        if user_input is not None:
            location = user_input[CONF_LOCATION]
            try:
                stations = await EnvironmentAgencyApi(
                    async_get_clientsession(self.hass)
                ).stations_near(
                    location[CONF_LATITUDE],
                    location[CONF_LONGITUDE],
                    min(location[CONF_RADIUS] / 1000, MAX_EA_DISTANCE_KM),
                )
            except WaterlevelError as err:
                _LOGGER.warning("Failed to fetch the Environment Agency station list: %s", err)
                errors["base"] = "england_unreachable"
            else:
                configured = self._async_current_ids()
                nearby = []
                for station in stations:
                    if f"{DOMAIN}_ea_{station['station']}" in configured:
                        continue
                    dist = distance(
                        location[CONF_LATITUDE],
                        location[CONF_LONGITUDE],
                        station["latitude"],
                        station["longitude"],
                    )
                    if dist is not None and dist <= location[CONF_RADIUS]:
                        nearby.append((dist, station))
                nearby.sort(key=lambda found: found[0])
                if nearby:
                    self._ea_stations = {station["station"]: station for _, station in nearby}
                    self._distances = {station["station"]: dist for dist, station in nearby}
                    return await self.async_step_england_station()
                errors["base"] = "no_nearby_stations"
        default = (user_input or {}).get(CONF_LOCATION) or {
            CONF_LATITUDE: self.hass.config.latitude,
            CONF_LONGITUDE: self.hass.config.longitude,
            CONF_RADIUS: DEFAULT_NEARBY_RADIUS,
        }
        return self.async_show_form(
            step_id="england",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOCATION, default=default): LocationSelector(
                        LocationSelectorConfig(radius=True)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_england_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one of the Environment Agency stations found nearby, closest first."""
        errors: dict[str, str] = {}
        if user_input is not None:
            ref = user_input[CONF_STATION]
            await self.async_set_unique_id(f"{DOMAIN}_ea_{ref}")
            self._abort_if_unique_id_configured()
            try:
                # The station list leaves out some measures, so read the station itself
                station = await EnvironmentAgencyApi(async_get_clientsession(self.hass)).station(
                    ref
                )
            except StationNotFound:
                errors[CONF_STATION] = "england_station_not_found"
            except WaterlevelError:
                errors["base"] = "england_unreachable"
            else:
                return self.async_create_entry(
                    title=station["name"], data=_england_entry_data(station)
                )
        return self.async_show_form(
            step_id="england_station",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=ref,
                                    label=(
                                        f"{station['name']}"
                                        f"{', ' + station['river'] if station['river'] else ''}"
                                        f" ({ref}), {self._distances[ref] / 1000:.1f} km"
                                    ),
                                )
                                for ref, station in self._ea_stations.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_scotland(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a location and radius to look for SEPA stations in Scotland."""
        errors: dict[str, str] = {}
        if user_input is not None:
            location = user_input[CONF_LOCATION]
            try:
                stations = await SepaApi(async_get_clientsession(self.hass)).stations()
            except WaterlevelError as err:
                _LOGGER.warning("Failed to fetch the SEPA station list: %s", err)
                errors["base"] = "scotland_unreachable"
            else:
                configured = self._async_current_ids()
                nearby = []
                for station in stations:
                    if f"{DOMAIN}_sepa_{station['station']}" in configured:
                        continue
                    dist = distance(
                        location[CONF_LATITUDE],
                        location[CONF_LONGITUDE],
                        station["latitude"],
                        station["longitude"],
                    )
                    if dist is not None and dist <= location[CONF_RADIUS]:
                        nearby.append((dist, station))
                nearby.sort(key=lambda found: found[0])
                if nearby:
                    self._sepa_stations = {station["station"]: station for _, station in nearby}
                    self._distances = {station["station"]: dist for dist, station in nearby}
                    return await self.async_step_scotland_station()
                errors["base"] = "no_nearby_stations"
        default = (user_input or {}).get(CONF_LOCATION) or {
            CONF_LATITUDE: self.hass.config.latitude,
            CONF_LONGITUDE: self.hass.config.longitude,
            CONF_RADIUS: DEFAULT_NEARBY_RADIUS,
        }
        return self.async_show_form(
            step_id="scotland",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOCATION, default=default): LocationSelector(
                        LocationSelectorConfig(radius=True)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_scotland_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one of the SEPA stations found nearby, closest first."""
        errors: dict[str, str] = {}
        if user_input is not None:
            ref = user_input[CONF_STATION]
            await self.async_set_unique_id(f"{DOMAIN}_sepa_{ref}")
            self._abort_if_unique_id_configured()
            try:
                level_ts, flow_ts = await SepaApi(
                    async_get_clientsession(self.hass)
                ).station_series(ref)
            except StationNotFound:
                errors[CONF_STATION] = "scotland_station_not_found"
            except WaterlevelError:
                errors["base"] = "scotland_unreachable"
            else:
                station = self._sepa_stations[ref]
                return self.async_create_entry(
                    title=station["name"],
                    data=_scotland_entry_data(station, level_ts, flow_ts),
                )
        return self.async_show_form(
            step_id="scotland_station",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=ref,
                                    label=(
                                        f"{station['name']}"
                                        f"{', ' + station['river'] if station['river'] else ''}"
                                        f" ({ref}), {self._distances[ref] / 1000:.1f} km"
                                    ),
                                )
                                for ref, station in self._sepa_stations.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_river(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a river, then load its stations."""
        errors: dict[str, str] = {}
        api = WaterlevelApi(async_get_clientsession(self.hass))
        if user_input is not None:
            group_id = user_input[CONF_GROUP]
            try:
                stations = await api.group_stations(group_id)
            except WaterlevelError as err:
                _LOGGER.warning("Failed to fetch stations for river %s: %s", group_id, err)
                errors["base"] = "cannot_connect"
            else:
                configured = self._async_current_ids()
                stations = {
                    sid: name
                    for sid, name in stations.items()
                    if f"{DOMAIN}_{sid}" not in configured
                }
                if stations:
                    self._selected_group = group_id
                    self._stations = stations
                    return await self.async_step_select_station()
                errors[CONF_GROUP] = "no_stations"
        if not self._groups:
            try:
                self._groups = await api.groups()
            except WaterlevelError as err:
                _LOGGER.warning("Failed to fetch river list: %s", err)
            if not self._groups:
                # Nothing to pick from; the user can still type a station number.
                return self.async_abort(reason="cannot_load_rivers")
        return self.async_show_form(
            step_id="river",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GROUP): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=gid, label=name)
                                for gid, name in self._groups.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_select_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a station on the chosen river."""
        errors: dict[str, str] = {}
        if user_input is not None:
            station = user_input[CONF_STATION]
            result = await self._async_add_station(
                station, errors, name_hint=self._stations.get(station)
            )
            if result is not None:
                return result
        return self.async_show_form(
            step_id="select_station",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=sid, label=f"{name} ({sid})")
                                for sid, name in self._stations.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "river_name": self._groups.get(self._selected_group or "", "selected river"),
            },
        )

    async def async_step_station_number(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter a station number (or paste a station URL) directly."""
        errors: dict[str, str] = {}
        if user_input is not None:
            station = normalize_station(user_input[CONF_STATION])
            if station is None:
                errors[CONF_STATION] = "invalid_station"
            else:
                result = await self._async_add_station(station, errors)
                if result is not None:
                    return result
        return self.async_show_form(
            step_id="station_number",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_STATION): TextSelector(
                            TextSelectorConfig(
                                type=TextSelectorType.TEXT,
                            )
                        ),
                    }
                ),
                user_input,
            ),
            errors=errors,
            description_placeholders={"stations_url": GROUP_LIST_URL, "example": "14029"},
        )

    async def _async_add_station(
        self, station: str, errors: dict[str, str], name_hint: str | None = None
    ) -> ConfigFlowResult | None:
        """Probe the station and create the entry; on failure fill errors and return None."""
        await self.async_set_unique_id(f"{DOMAIN}_{station}")
        self._abort_if_unique_id_configured()
        try:
            info = await _detect_station(
                WaterlevelApi(async_get_clientsession(self.hass)), station, name_hint=name_hint
            )
        except StationNotFound:
            errors[CONF_STATION] = "station_not_found"
        except WaterlevelError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error adding station %s", station)
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(title=info[CONF_STATION_NAME], data=info)
        return None


class WaterlevelOptionsFlow(config_entries.OptionsFlow):
    """Change how often a station updates and when it counts as stale."""

    @property
    def _default_interval(self) -> int:
        """The source's own update interval, which SEPA stations make longer."""
        return SEPA_SCAN_INTERVAL if self._is_sepa else DEFAULT_SCAN_INTERVAL

    @property
    def _min_interval(self) -> int:
        return SEPA_SCAN_INTERVAL if self._is_sepa else MIN_SCAN_INTERVAL

    @property
    def _is_sepa(self) -> bool:
        return bool(self.config_entry.data.get(CONF_SOURCE) == SOURCE_SEPA)

    @property
    def _is_opw(self) -> bool:
        """Only OPW stations can look for sensors again; the other sources' are fixed."""
        return bool(self.config_entry.data.get(CONF_SOURCE, SOURCE_OPW) == SOURCE_OPW)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            options = {
                CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                CONF_STALE_HOURS: int(user_input[CONF_STALE_HOURS]),
                CONF_ALERT_STAGE: user_input.get(CONF_ALERT_STAGE, DEFAULT_ALERT_STAGE),
            }
            if user_input.get(CONF_REDETECT) and self._is_opw:
                api = WaterlevelApi(async_get_clientsession(self.hass))
                try:
                    _, available = await api.detect_station(self.config_entry.data[CONF_STATION])
                except StationNotFound:
                    errors["base"] = "station_not_found"
                except WaterlevelError:
                    errors["base"] = "cannot_connect"
                else:
                    # Sensors and options in one update, so the station reloads once
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={**self.config_entry.data, CONF_AVAILABLE_SENSORS: available},
                        options=options,
                    )
            if not errors:
                # Saving reloads the station, which picks up the settings and any new sensors
                return self.async_create_entry(data=options)
        saved = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=saved.get(CONF_SCAN_INTERVAL, self._default_interval),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=self._min_interval,
                            max=180,
                            step=5,
                            unit_of_measurement="min",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_STALE_HOURS,
                        default=saved.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=72,
                            step=1,
                            unit_of_measurement="h",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_ALERT_STAGE,
                        default=saved.get(CONF_ALERT_STAGE, DEFAULT_ALERT_STAGE),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[stage for stage in FLOOD_STAGES if stage != "normal"],
                            translation_key=CONF_ALERT_STAGE,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    **(
                        {vol.Optional(CONF_REDETECT, default=False): BooleanSelector()}
                        if self._is_opw
                        else {}
                    ),
                }
            ),
            errors=errors,
        )
