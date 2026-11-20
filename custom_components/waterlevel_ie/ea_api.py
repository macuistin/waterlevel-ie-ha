"""Client for the Environment Agency real-time flood-monitoring API (England).

Everything that talks to the Environment Agency or parses its JSON lives here, so it can
be tested without Home Assistant. The parse_* functions are pure. The data is published
under the Open Government Licence v3.0.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

import aiohttp

from .api import Reading, StationNotFound, WaterlevelError, parse_time
from .const import (
    EA_LATEST_URL,
    EA_READINGS_URL,
    EA_STATION_URL,
    EA_STATIONS_URL,
)

_LOGGER = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=30)
# Level qualifiers worth showing as a Water Level, best first. Groundwater and the
# like are left out.
LEVEL_QUALIFIERS = ("Stage", "Downstream Stage", "Tidal Level")
# Stage-scale values, by the name kept in the config entry
STAGE_SCALE_FIELDS = {
    "typicalRangeHigh": "typical_high",
    "typicalRangeLow": "typical_low",
    "highestRecent": "highest_recent",
    "maxOnRecord": "max_on_record",
    "minOnRecord": "min_on_record",
}


def _tail(url: object) -> str:
    """The last part of a URL, which is a station reference or a measure's notation."""
    return str(url).rsplit("/", 1)[-1]


def _number(raw: object) -> float | None:
    """A JSON number as a float, or None for anything else (a bool is not a reading)."""
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    return float(raw)


def parse_latest(payload: object) -> dict[str, Reading]:
    """Parse the latest-readings feed into {measure notation: Reading}."""
    latest: dict[str, Reading] = {}
    items = payload.get("items") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        value = _number(item.get("value"))
        time = parse_time(str(item.get("dateTime", "")))
        if value is None or time is None or not item.get("measure"):
            continue
        latest[_tail(item["measure"])] = Reading(time, value)
    return latest


def parse_readings(payload: object) -> list[Reading]:
    """Parse a measure's readings into a list, oldest first."""
    readings: list[Reading] = []
    items = payload.get("items") if isinstance(payload, dict) else None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        value = _number(item.get("value"))
        if value is not None and (time := parse_time(str(item.get("dateTime", "")))):
            readings.append(Reading(time, value))
    return sorted(readings, key=lambda reading: reading.time)


def pick_measures(measures: object) -> tuple[str | None, str | None]:
    """Choose a station's level measure and its flow measure, as notations.

    The level is the instantaneous Stage reading, on the stage datum (mASD) where
    there is a choice, because that is the datum the stage scale is on. Then Downstream
    Stage, then Tidal Level. The flow is an instantaneous reading in m³/s. Either is
    None when the station has no such measure.
    """
    levels: list[tuple[tuple[int, int], str]] = []
    flows: list[tuple[int, str]] = []
    for measure in measures if isinstance(measures, list) else []:
        if not isinstance(measure, dict) or not measure.get("@id"):
            continue
        if measure.get("valueType", "instantaneous") != "instantaneous":
            continue
        notation = _tail(measure["@id"])
        qualifier = str(measure.get("qualifier") or "")
        if measure.get("parameter") == "level" and qualifier in LEVEL_QUALIFIERS:
            rank = (LEVEL_QUALIFIERS.index(qualifier), measure.get("unitName") != "mASD")
            levels.append(((rank[0], int(rank[1])), notation))
        elif measure.get("parameter") == "flow" and measure.get("unitName") == "m3/s":
            # A measure with no qualifier is the plain flow reading
            flows.append((int(bool(qualifier)), notation))
    level = min(levels)[1] if levels else None
    flow = min(flows)[1] if flows else None
    return level, flow


def parse_stage_scale(scale: object) -> dict[str, float]:
    """The numbers in a station's stage scale, by name; missing or odd ones are left out."""
    levels: dict[str, float] = {}
    if not isinstance(scale, dict):
        return levels
    for field, name in STAGE_SCALE_FIELDS.items():
        raw = scale.get(field)
        value = _number(raw.get("value") if isinstance(raw, dict) else raw)
        if value is not None:
            levels[name] = value
    return levels


def parse_station(item: object) -> dict[str, Any] | None:
    """Turn one station from the API into what the config entry keeps, or None.

    None when it has no coordinates or no level measure worth showing.
    """
    if not isinstance(item, dict):
        return None
    ref = str(item.get("stationReference") or "").strip()
    lat, lon = _number(item.get("lat")), _number(item.get("long"))
    level, flow = pick_measures(item.get("measures"))
    if not ref or lat is None or lon is None or level is None:
        return None
    return {
        "station": ref,
        "name": str(item.get("label") or f"Station {ref}").strip(),
        "latitude": lat,
        "longitude": lon,
        "rloi_id": str(item.get("RLOIid") or ""),
        "river": str(item.get("riverName") or ""),
        "town": str(item.get("town") or ""),
        "catchment": str(item.get("catchmentName") or ""),
        "level_measure": level,
        "flow_measure": flow,
        "stage_scale": parse_stage_scale(item.get("stageScale")),
    }


def parse_stations(payload: object) -> list[dict[str, Any]]:
    """Parse a station list, leaving out stations that have no usable level."""
    items = payload.get("items") if isinstance(payload, dict) else None
    stations = [parse_station(item) for item in items] if isinstance(items, list) else []
    return [station for station in stations if station]


class EnvironmentAgencyApi:
    """Async access to the Environment Agency flood-monitoring API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _get_json(self, url: str, params: dict[str, str | int] | None = None) -> Any:
        try:
            async with self._session.get(url, params=params, timeout=TIMEOUT) as resp:
                if resp.status == 404:
                    raise StationNotFound(f"Not found: {url}")
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise WaterlevelError(f"Error fetching {url}: {err}") from err

    async def latest(self) -> dict[str, Reading]:
        """The latest reading of every measure on every station, in one call."""
        return parse_latest(await self._get_json(EA_LATEST_URL))

    async def readings_since(self, measure: str, since: datetime) -> list[Reading]:
        """A measure's readings from a time onward, oldest first."""
        params: dict[str, str | int] = {
            "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "_sorted": "",
            "_limit": 200,
        }
        return parse_readings(await self._get_json(EA_READINGS_URL.format(measure=measure), params))

    async def stations_near(
        self, latitude: float, longitude: float, distance_km: float
    ) -> list[dict[str, Any]]:
        """Active stations with a river level within a distance, in the API's order."""
        params: dict[str, str | int] = {
            "parameter": "level",
            "status": "Active",
            "lat": f"{latitude:.5f}",
            "long": f"{longitude:.5f}",
            "dist": f"{distance_km:.1f}",
            "_limit": 500,
            "_view": "full",
        }
        return parse_stations(await self._get_json(EA_STATIONS_URL, params))

    async def station(self, ref: str) -> dict[str, Any]:
        """One station by its reference. Raises StationNotFound, or if it has no level."""
        payload = await self._get_json(EA_STATION_URL.format(ref=ref))
        station = parse_station(payload.get("items") if isinstance(payload, dict) else None)
        if station is None:
            raise StationNotFound(f"No level data for station {ref}")
        return station
