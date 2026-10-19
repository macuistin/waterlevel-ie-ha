"""Config flow for Waterlevel.ie — browse by river or enter a station number."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import StationNotFound, WaterlevelApi, WaterlevelError
from .const import (
    CONF_AVAILABLE_SENSORS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_STATION,
    CONF_STATION_NAME,
    DOMAIN,
    GROUP_LIST_URL,
)

_LOGGER = logging.getLogger(__name__)
CONF_GROUP = "group"


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


async def _detect_station(api: WaterlevelApi, station: str, name_hint: str | None = None) -> dict:
    """Probe a station and build its config entry data.

    name_hint: preferred display name (e.g. from the river group picker).
    Falls back to the station page title, then to 'Station {station}'.
    """
    page_name, available = await api.detect_station(station)
    result = {
        CONF_STATION: station,
        CONF_STATION_NAME: name_hint or page_name or f"Station {station}",
        CONF_AVAILABLE_SENSORS: available,
    }
    if coords := await api.station_coords(station):
        result[CONF_LATITUDE], result[CONF_LONGITUDE] = coords
    return result


class WaterlevelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow: choose how to find a station, then add it."""

    VERSION = 1

    def __init__(self) -> None:
        self._groups: dict[str, str] = {}
        self._selected_group: str | None = None
        self._stations: dict[str, str] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Ask whether to browse by river or type a station number."""
        return self.async_show_menu(step_id="user", menu_options=["river", "station_number"])

    async def async_step_river(self, user_input: dict[str, Any] | None = None):
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
                                {"value": gid, "label": name} for gid, name in self._groups.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_select_station(self, user_input: dict[str, Any] | None = None):
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
                                {"value": sid, "label": f"{name} ({sid})"}
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

    async def async_step_station_number(self, user_input: dict[str, Any] | None = None):
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
    ):
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
