"""Client for the waterlevel.ie (OPW) data files.

Everything that talks to waterlevel.ie or parses its CSV, GeoJSON and HTML lives
here, so it can be tested without Home Assistant. The parse_* functions are pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
import re
from typing import Any

import aiohttp

from .const import (
    BASE_URL,
    GEOJSON_URL,
    GROUP_LIST_URL,
    GROUP_URL,
    HYDRO_SENSOR_TYPES,
    HYDRO_SERIES_URL,
    LATEST_GEOJSON_URL,
    SENSOR_TYPES,
    STATION_PAGE_URL,
    SUMMARY_SENSOR_TYPES,
    SUMMARY_URL,
)

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=30)
PROBE_TIMEOUT = aiohttp.ClientTimeout(total=10)
# A Hydro-Data series with nothing newer than this is treated as discontinued
HYDRO_MAX_AGE = timedelta(days=7)


class WaterlevelError(Exception):
    """waterlevel.ie could not be reached or returned something unusable."""


class StationNotFound(WaterlevelError):
    """No sensor data exists for the requested station."""


@dataclass(frozen=True)
class Reading:
    """One timestamped value from a gauge sensor."""

    time: datetime
    value: float


def parse_time(raw: str) -> datetime | None:
    """Parse a waterlevel.ie timestamp ("2026-10-19T18:15:00Z" or "2026-10-19 18:15")."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    # All waterlevel.ie timestamps are UTC
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_series(text: str) -> list[Reading]:
    """Parse a day/week/month CSV (datetime,value) into readings, oldest first.

    Rows with a blank or non-numeric value, and the header, are skipped.
    """
    readings: list[Reading] = []
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 2 or not parts[1].strip():
            continue
        try:
            value = float(parts[1])
        except ValueError:
            continue
        if (time := parse_time(parts[0])) is not None:
            readings.append(Reading(time, value))
    return readings


def parse_summary(text: str) -> dict[str, float | None]:
    """Return the most recent daily min/mean/max from a summary CSV, or {}.

    Columns are Datetime,Value,Min,Mean,Max. Date-only rows are daily aggregates;
    rows with a time only carry Value.
    """
    for line in reversed(text.splitlines()):
        if line.startswith("Datetime"):
            break
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5 and parts[3]:
            try:
                return {
                    "min": float(parts[2]) if parts[2] else None,
                    "mean": float(parts[3]),
                    "max": float(parts[4]) if parts[4] else None,
                }
            except ValueError:
                continue
    return {}


def summary_has_daily_rows(text: str) -> bool:
    """True if a summary CSV has at least one daily aggregate row."""
    return bool(parse_summary(text))


def parse_latest(geo: dict) -> dict[str, dict[str, Reading]]:
    """Parse the latest-readings GeoJSON into {station: {sensor: Reading}}.

    Station numbers are normalised to 5 digits to match config entries.
    """
    latest: dict[str, dict[str, Reading]] = {}
    for feature in geo.get("features", []):
        props = feature.get("properties") or {}
        ref = str(props.get("station_ref", ""))
        sensor = str(props.get("sensor_ref", ""))
        if not ref.isdigit() or not sensor:
            continue
        try:
            value = float(props.get("value"))
        except (TypeError, ValueError):
            continue
        if (time := parse_time(str(props.get("datetime", "")))) is None:
            continue
        latest.setdefault(str(int(ref)).zfill(5), {})[sensor] = Reading(time, value)
    return latest


def parse_hydro_series(payload: object, shortname: str | None = None) -> list[Reading]:
    """Parse an OPW Hydro-Data series file into readings, oldest first.

    The file is a list of series ({ts_shortname, columns, data: [[time, value, ...]]}).
    Uses the series named shortname, or the first series with data.
    """
    for series in payload if isinstance(payload, list) else []:
        if not isinstance(series, dict):
            continue
        if shortname and series.get("ts_shortname") != shortname:
            continue
        readings = []
        for row in series.get("data") or []:
            if len(row) < 2 or row[1] is None:
                continue
            try:
                value = float(row[1])
            except (TypeError, ValueError):
                continue
            if (time := parse_time(str(row[0]))) is not None:
                readings.append(Reading(time, value))
        if readings:
            return readings
    return []


def parse_latest_stations(geo: dict) -> dict[str, tuple[str, float, float]]:
    """Every station in the latest-readings GeoJSON as {station: (name, lat, lon)}."""
    stations: dict[str, tuple[str, float, float]] = {}
    for feature in geo.get("features", []):
        props = feature.get("properties") or {}
        ref = str(props.get("station_ref", ""))
        if not ref.isdigit() or not int(ref):
            continue
        try:
            lon, lat = feature["geometry"]["coordinates"]  # GeoJSON is [lon, lat]
            location = (float(lat), float(lon))
        except (KeyError, TypeError, ValueError):
            continue
        name = str(props.get("station_name") or f"Station {int(ref)}")
        stations[str(int(ref)).zfill(5)] = (name, *location)
    return stations


def find_station_coords(geo: dict, station: str) -> tuple[float, float] | None:
    """Return (latitude, longitude) for a station from the station GeoJSON."""
    for feature in geo.get("features", []):
        # The ref may be zero-padded to 10 digits or not; compare numerically
        ref = str((feature.get("properties") or {}).get("ref", ""))
        if ref.isdigit() and int(ref) == int(station):
            lon, lat = feature["geometry"]["coordinates"]  # GeoJSON is [lon, lat]
            return (float(lat), float(lon))
    return None


def parse_groups(html: str) -> dict[str, str]:
    """Parse the river group list page into {group_id: river name}, sorted by name."""
    groups: dict[str, str] = {}
    for m in re.finditer(r'href="/group/(\d+)/">([^<]+)</a>', html):
        gid, name = m.group(1), m.group(2).strip()
        display = re.sub(r"\s*[-\u2013]\s*water level.*$", "", name, flags=re.I).strip()
        groups[gid] = display or name
    return dict(sorted(groups.items(), key=lambda kv: kv[1]))


def parse_group_stations(html: str) -> dict[str, str]:
    """Parse a river group page into {station: station name}, sorted by name."""
    stations: dict[str, str] = {}
    for m in re.finditer(r'href="/(\d{10})/">([^<]+)</a>', html):
        if int(m.group(1)):
            stations[str(int(m.group(1))).zfill(5)] = m.group(2).strip()
    return dict(sorted(stations.items(), key=lambda kv: kv[1]))


def parse_station_page(html: str) -> tuple[str | None, set[str]]:
    """Return (station name, sensor codes linked) from a station page."""
    name = None
    if m := re.search(r"Sensors on Station\s+\d+\s+([^<]+)", html):
        name = m.group(1).strip()
    # Links look like /0000014029/0001/week/ or /0000026021/0014/day/
    codes = set(re.findall(r"/\d{10}/([A-Za-z0-9]+)/(?:week|day|summary)/", html))
    return name, codes


class WaterlevelApi:
    """Async access to the waterlevel.ie data files."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _get_text(self, url: str, client_timeout: aiohttp.ClientTimeout = TIMEOUT) -> str:
        try:
            async with self._session.get(url, timeout=client_timeout) as resp:
                if resp.status == 404:
                    raise StationNotFound(f"Not found: {url}")
                resp.raise_for_status()
                return await resp.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WaterlevelError(f"Error fetching {url}: {err}") from err

    async def _get_json(self, url: str) -> Any:
        try:
            async with self._session.get(url, timeout=TIMEOUT) as resp:
                if resp.status == 404:
                    raise StationNotFound(f"Not found: {url}")
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise WaterlevelError(f"Error fetching {url}: {err}") from err

    async def series(self, station: str, sensor: str, period: str) -> list[Reading]:
        """Readings for one sensor over a period: "day", "week" or "month" (35 days)."""
        url = BASE_URL.format(period=period, station=station, sensor=sensor)
        return parse_series(await self._get_text(url))

    async def daily_summary(self, station: str, sensor: str) -> dict[str, float | None]:
        """Most recent daily min/mean/max for a sensor, or {}."""
        url = SUMMARY_URL.format(station_padded=station.zfill(10), sensor=sensor)
        return parse_summary(await self._get_text(url))

    async def hydro_series(
        self, station: str, param: str, period: str, shortname: str | None = None
    ) -> list[Reading]:
        """Readings from OPW Hydro-Data: param "S" (level, absolute) or "Q" (flow)."""
        url = HYDRO_SERIES_URL.format(station=station, param=param, period=period)
        return parse_hydro_series(await self._get_json(url), shortname)

    async def latest(self) -> dict[str, dict[str, Reading]]:
        """Latest reading of every sensor on every station (one shared file)."""
        return parse_latest(await self._get_json(LATEST_GEOJSON_URL))

    async def stations(self) -> dict[str, tuple[str, float, float]]:
        """Every station with its name and location, as {station: (name, lat, lon)}."""
        return parse_latest_stations(await self._get_json(LATEST_GEOJSON_URL))

    async def station_coords(self, station: str) -> tuple[float, float] | None:
        """(latitude, longitude) for a station, or None if unavailable."""
        try:
            return find_station_coords(await self._get_json(GEOJSON_URL), station)
        except (WaterlevelError, KeyError, TypeError, ValueError) as err:
            _LOGGER.debug("Could not fetch coordinates for station %s: %s", station, err)
            return None

    async def groups(self) -> dict[str, str]:
        """River groups as {group_id: name}."""
        return parse_groups(await self._get_text(GROUP_LIST_URL))

    async def group_stations(self, group_id: str) -> dict[str, str]:
        """Stations on a river group as {station: name}."""
        return parse_group_stations(await self._get_text(GROUP_URL.format(group_id=group_id)))

    async def detect_station(self, station: str) -> tuple[str | None, list[str]]:
        """Return (station name from its page, available sensor keys).

        Sensors are read from the station page's links; if the page can't be read,
        each sensor's day CSV is probed instead. Daily summary sensors are added when
        the summary CSV has daily rows, and discharge when Hydro-Data still reports it.
        Raises StationNotFound if nothing is found, or WaterlevelError if waterlevel.ie
        can't be reached at all.
        """
        name: str | None = None
        available: list[str] = []
        try:
            html = await self._get_text(
                STATION_PAGE_URL.format(station_padded=station.zfill(10)), PROBE_TIMEOUT
            )
        except WaterlevelError as err:
            _LOGGER.debug("Could not fetch station page: %s", err)
        else:
            name, codes = parse_station_page(html)
            available = [key for key in SENSOR_TYPES if key in codes]
        if not available:
            unreachable = 0
            for key in SENSOR_TYPES:
                try:
                    url = BASE_URL.format(period="day", station=station, sensor=key)
                    if (await self._get_text(url, PROBE_TIMEOUT)).strip():
                        available.append(key)
                except StationNotFound:
                    pass
                except WaterlevelError:
                    unreachable += 1
            if unreachable == len(SENSOR_TYPES):
                # Not one answer: waterlevel.ie is down, not the station missing
                raise WaterlevelError(f"Could not reach waterlevel.ie for station {station}")
        # Daily min/mean/max, for each source sensor that has a summary
        sources = dict.fromkeys(cfg["summary_source"] for cfg in SUMMARY_SENSOR_TYPES.values())
        for source in sources:
            if source not in available:
                continue
            url = SUMMARY_URL.format(station_padded=station.zfill(10), sensor=source)
            try:
                text = await self._get_text(url, PROBE_TIMEOUT)
            except WaterlevelError:
                continue
            if summary_has_daily_rows(text):
                available.extend(
                    key
                    for key, cfg in SUMMARY_SENSOR_TYPES.items()
                    if cfg["summary_source"] == source
                )
        if not available:
            raise StationNotFound(f"No sensor data found for station {station}")
        # Flow from OPW Hydro-Data, where it's measured and still reported
        for key, cfg in HYDRO_SENSOR_TYPES.items():
            try:
                readings = await self.hydro_series(
                    station, cfg["hydro_param"], "week", cfg["hydro_series"]
                )
            except WaterlevelError:
                continue
            if readings and datetime.now(UTC) - readings[-1].time < HYDRO_MAX_AGE:
                available.append(key)
        return name, available
