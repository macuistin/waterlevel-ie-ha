"""Config flow for Waterlevel.ie — browse by river or enter a station number."""

from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp
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

from .const import (
    BASE_URL,
    CONF_AVAILABLE_SENSORS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_STATION,
    CONF_STATION_NAME,
    DOMAIN,
    SENSOR_TYPES,
    STATION_PAGE_URL,
    SUMMARY_SENSOR_TYPES,
    SUMMARY_URL,
)
from .coordinator import fetch_station_coords

_LOGGER = logging.getLogger(__name__)
GROUP_LIST_URL = "https://waterlevel.ie/group/list/"
GROUP_URL = "https://waterlevel.ie/group/{group_id}/"
TIMEOUT = aiohttp.ClientTimeout(total=15)
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


async def _fetch_groups(session: aiohttp.ClientSession) -> dict[str, str]:
    async with session.get(GROUP_LIST_URL, timeout=TIMEOUT) as resp:
        resp.raise_for_status()
        html = await resp.text()
    groups: dict[str, str] = {}
    for m in re.finditer(r'href="/group/(\d+)/">([^<]+)</a>', html):
        gid, name = m.group(1), m.group(2).strip()
        display = re.sub(r"\s*[-\u2013]\s*water level.*$", "", name, flags=re.I).strip()
        groups[gid] = display or name
    return dict(sorted(groups.items(), key=lambda kv: kv[1]))


async def _fetch_stations(session: aiohttp.ClientSession, group_id: str) -> dict[str, str]:
    url = GROUP_URL.format(group_id=group_id)
    async with session.get(url, timeout=TIMEOUT) as resp:
        resp.raise_for_status()
        html = await resp.text()
    stations: dict[str, str] = {}
    for m in re.finditer(r'href="/(\d{10})/">([^<]+)</a>', html):
        if num := normalize_station(m.group(1)):
            stations[num] = m.group(2).strip()
    return dict(sorted(stations.items(), key=lambda kv: kv[1]))


async def _detect_station(
    session: aiohttp.ClientSession, station: str, name_hint: str | None = None
) -> dict:
    """Probe a station for available sensors and metadata.

    name_hint: preferred display name (e.g. from the river group picker).
    Falls back to the station page title, then to 'Station {station}'.
    """
    padded = station.zfill(10)
    page_url = STATION_PAGE_URL.format(station_padded=padded)
    station_name = name_hint or f"Station {station}"
    available: list[str] = []
    try:
        async with session.get(page_url, timeout=TIMEOUT) as resp:
            if resp.status == 200:
                html = await resp.text()
                # Extract station name from page if no group-picker hint
                if not name_hint:
                    m = re.search(r"Sensors on Station\s+\d+\s+([^<]+)", html)
                    if m:
                        station_name = m.group(1).strip()
                # Detect all sensor types by parsing table links on the station page.
                # Links look like /0000014029/0001/ or /0000026021/0014/
                # This is far more efficient than probing each sensor CSV individually.
                page_codes = set(re.findall(r"/\d{10}/([A-Za-z0-9]+)/(?:week|day|summary)/", html))
                for sensor_key in SENSOR_TYPES:
                    if sensor_key in page_codes:
                        available.append(sensor_key)
    except Exception as err:
        _LOGGER.debug("Could not fetch station page: %s", err)
    # Fall back to CSV probing if page parsing gave nothing (e.g. HTTP error above)
    if not available:
        for sensor_key in SENSOR_TYPES:
            url = BASE_URL.format(period="day", station=station, sensor=sensor_key)
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200 and (await resp.text()).strip():
                        available.append(sensor_key)
            except Exception:
                pass
    # Probe summary (daily min/mean/max) for each sensor that supports it
    already_probed: set[str] = set()
    for cfg in SUMMARY_SENSOR_TYPES.values():
        source = cfg["summary_source"]
        if source not in available or source in already_probed:
            continue
        already_probed.add(source)
        sum_url = SUMMARY_URL.format(station_padded=padded, sensor=source)
        try:
            async with session.get(sum_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # Check that there's at least one row with a Mean value
                    has_mean = any(
                        len(p := line.split(",")) >= 5 and p[3].strip()
                        for line in text.splitlines()
                        if line.strip() and not line.startswith("Datetime")
                    )
                    if has_mean:
                        # Add all summary keys for this source sensor
                        for sk, sc in SUMMARY_SENSOR_TYPES.items():
                            if sc["summary_source"] == source:
                                available.append(sk)
        except Exception:
            pass
    if not available:
        raise ValueError(f"No sensor data found for station {station}")
    result = {
        CONF_STATION: station,
        CONF_STATION_NAME: station_name,
        CONF_AVAILABLE_SENSORS: available,
    }
    if coords := await fetch_station_coords(session, station):
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
        session = async_get_clientsession(self.hass)
        if user_input is not None:
            group_id = user_input[CONF_GROUP]
            try:
                stations = await _fetch_stations(session, group_id)
            except (aiohttp.ClientError, TimeoutError) as err:
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
                self._groups = await _fetch_groups(session)
            except (aiohttp.ClientError, TimeoutError) as err:
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
                async_get_clientsession(self.hass), station, name_hint=name_hint
            )
        except ValueError:
            errors[CONF_STATION] = "station_not_found"
        except (aiohttp.ClientError, TimeoutError):
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error adding station %s", station)
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(title=info[CONF_STATION_NAME], data=info)
        return None
