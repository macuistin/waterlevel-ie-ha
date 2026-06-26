"""Config flow for Waterlevel.ie — multi-step river search UI."""
from __future__ import annotations
import logging, re
import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from .const import (
    DOMAIN, BASE_URL, STATION_PAGE_URL, SUMMARY_URL, GEOJSON_URL,
    SENSOR_TYPES, SUMMARY_SENSOR_TYPES,
    CONF_STATION, CONF_STATION_NAME, CONF_AVAILABLE_SENSORS,
    CONF_LATITUDE, CONF_LONGITUDE,
)

_LOGGER = logging.getLogger(__name__)
GROUP_LIST_URL = "https://waterlevel.ie/group/list/"
GROUP_URL = "https://waterlevel.ie/group/{group_id}/"
TIMEOUT = aiohttp.ClientTimeout(total=15)


async def _fetch_groups() -> dict[str, str]:
    async with aiohttp.ClientSession() as session:
        async with session.get(GROUP_LIST_URL, timeout=TIMEOUT) as resp:
            resp.raise_for_status()
            html = await resp.text()
    groups: dict[str, str] = {}
    for m in re.finditer(r'href="/group/(\d+)/">([^<]+)</a>', html):
        gid, name = m.group(1), m.group(2).strip()
        display = re.sub(r"\s*[-\u2013]\s*water level.*$", "", name, flags=re.I).strip()
        groups[gid] = display or name
    return dict(sorted(groups.items(), key=lambda kv: kv[1]))


async def _fetch_stations(group_id: str) -> dict[str, str]:
    url = GROUP_URL.format(group_id=group_id)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=TIMEOUT) as resp:
            resp.raise_for_status()
            html = await resp.text()
    stations: dict[str, str] = {}
    for m in re.finditer(r'href="/(\d{10})/">([^<]+)</a>', html):
        num = (m.group(1).lstrip("0") or "0").zfill(5)
        stations[num] = m.group(2).strip()
    return dict(sorted(stations.items(), key=lambda kv: kv[1]))


async def _fetch_station_coords(
    session: aiohttp.ClientSession, station: str
) -> tuple[float, float] | None:
    """Return (latitude, longitude) for the station from the OPW GeoJSON, or None."""
    padded = station.zfill(5)
    try:
        async with session.get(GEOJSON_URL, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
        for feature in data.get("features", []):
            ref = str(feature.get("properties", {}).get("ref", "")).zfill(5)
            if ref == padded:
                lon, lat = feature["geometry"]["coordinates"]  # GeoJSON is [lon, lat]
                return (float(lat), float(lon))
    except Exception as err:
        _LOGGER.debug("Could not fetch station coordinates: %s", err)
    return None


async def _detect_station(station: str, name_hint: str | None = None) -> dict:
    """Probe a station for available sensors and metadata.

    name_hint: preferred display name (e.g. from the river group picker).
    Falls back to the station page title, then to 'Station {station}'.
    """
    padded = station.zfill(10)
    page_url = STATION_PAGE_URL.format(station_padded=padded)
    station_name = name_hint or f"Station {station}"
    available: list[str] = []
    lat: float | None = None
    lon: float | None = None
    async with aiohttp.ClientSession() as session:
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
                    page_codes = set(
                        re.findall(r'/\d{10}/([A-Za-z0-9]+)/(?:week|day|summary)/', html)
                    )
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
        for summary_key, cfg in SUMMARY_SENSOR_TYPES.items():
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
        # Fetch coordinates
        coords = await _fetch_station_coords(session, station)
        if coords:
            lat, lon = coords
    if not available:
        raise ValueError(f"No sensor data found for station {station}")
    result = {
        CONF_STATION: station,
        CONF_STATION_NAME: station_name,
        CONF_AVAILABLE_SENSORS: available,
    }
    if lat is not None and lon is not None:
        result[CONF_LATITUDE] = lat
        result[CONF_LONGITUDE] = lon
    return result


class WaterlevelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Two-step config flow: pick river → pick station."""
    VERSION = 1

    def __init__(self) -> None:
        self._groups: dict[str, str] = {}
        self._selected_group: str | None = None
        self._stations: dict[str, str] = {}
        self._station_name_hint: str | None = None

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if not self._groups:
            try:
                self._groups = await _fetch_groups()
            except Exception as err:
                _LOGGER.error("Failed to fetch river groups: %s", err)
                errors["base"] = "cannot_connect"
        if user_input is not None and not errors:
            station_raw = user_input.get(CONF_STATION, "").strip()
            group_id = user_input.get("group", "")
            if station_raw:
                if not station_raw.isdigit():
                    errors[CONF_STATION] = "invalid_station"
                else:
                    return await self._finish_with_station(station_raw)
            elif group_id:
                self._selected_group = group_id
                self._stations = {}
                return await self.async_step_select_station()
            else:
                errors["base"] = "select_river_or_station"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Optional("group"): vol.In({gid: n for gid, n in self._groups.items()}),
                vol.Optional(CONF_STATION): str,
            }),
            errors=errors,
            description_placeholders={"groups_url": "https://waterlevel.ie/group/list/", "example": "14029"},
        )

    async def async_step_select_station(self, user_input=None):
        errors: dict[str, str] = {}
        if not self._stations:
            try:
                self._stations = await _fetch_stations(self._selected_group)
            except Exception as err:
                _LOGGER.error("Failed to fetch stations: %s", err)
                errors["base"] = "cannot_connect"
        if user_input is not None and not errors:
            station_num = user_input.get(CONF_STATION, "")
            self._station_name_hint = self._stations.get(station_num)
            return await self._finish_with_station(station_num)
        return self.async_show_form(
            step_id="select_station",
            data_schema=vol.Schema({vol.Required(CONF_STATION): vol.In(self._stations)}),
            errors=errors,
            description_placeholders={"river_name": self._groups.get(self._selected_group, "selected river")},
        )

    async def _finish_with_station(self, raw: str):
        station = raw.strip().zfill(5)
        await self.async_set_unique_id(f"{DOMAIN}_{station}")
        self._abort_if_unique_id_configured()
        try:
            info = await _detect_station(station, name_hint=self._station_name_hint)
        except ValueError:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Optional("group"): vol.In(self._groups), vol.Optional(CONF_STATION): str}),
                errors={CONF_STATION: "station_not_found"},
            )
        except aiohttp.ClientError:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({vol.Optional("group"): vol.In(self._groups), vol.Optional(CONF_STATION): str}),
                errors={"base": "cannot_connect"},
            )
        return self.async_create_entry(title=info[CONF_STATION_NAME], data=info)
