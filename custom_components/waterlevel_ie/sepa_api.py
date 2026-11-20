"""Client for SEPA's KiWIS time series API (Scotland).

Everything that talks to SEPA or parses its JSON lives here, so it can be tested without
Home Assistant. The parse_* functions are pure. The data is published under the Open
Government Licence.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from .api import Reading, StationNotFound, WaterlevelError, parse_time
from .const import SEPA_BASE_URL, SEPA_PARAMS

TIMEOUT = aiohttp.ClientTimeout(total=30)
# The 15-minute series: S is stage (level, m) and Q is flow (m³/s)
LIVE_SERIES = "15m.Cmd"


def _rows(payload: object) -> list[dict[str, Any]]:
    """A KiWIS table ([header, row, ...]) as a list of {column: value}."""
    if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[0], list):
        return []
    header = [str(name) for name in payload[0]]
    return [dict(zip(header, row, strict=False)) for row in payload[1:] if isinstance(row, list)]


def _float(raw: object) -> float | None:
    """A number or numeric string as a float, or None."""
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_stations(payload: object) -> list[dict[str, Any]]:
    """Parse a station list into dicts with a station number, name, location and river."""
    stations: list[dict[str, Any]] = []
    for row in _rows(payload):
        ref = str(row.get("station_no") or "").strip()
        lat, lon = _float(row.get("station_latitude")), _float(row.get("station_longitude"))
        if not ref or lat is None or lon is None:
            continue
        stations.append(
            {
                "station": ref,
                "name": str(row.get("station_name") or f"Station {ref}").strip(),
                "latitude": lat,
                "longitude": lon,
                "river": str(row.get("river_name") or "").strip(),
            }
        )
    return stations


def parse_series_ids(payload: object) -> dict[str, str]:
    """Parse a time series list into {station number: time series id}."""
    return {
        str(row["station_no"]): str(row["ts_id"])
        for row in _rows(payload)
        if row.get("station_no") and row.get("ts_id")
    }


def parse_values(payload: object) -> dict[str, list[Reading]]:
    """Parse getTimeseriesValues into {time series id: readings, oldest first}."""
    result: dict[str, list[Reading]] = {}
    for series in payload if isinstance(payload, list) else []:
        if not isinstance(series, dict) or not series.get("ts_id"):
            continue
        readings = []
        for row in series.get("data") or []:
            if not isinstance(row, list) or len(row) < 2:
                continue
            value = _float(row[1])
            if value is not None and (time := parse_time(str(row[0]))) is not None:
                readings.append(Reading(time, value))
        result[str(series["ts_id"])] = sorted(readings, key=lambda reading: reading.time)
    return result


class SepaApi:
    """Async access to SEPA's KiWIS time series API, without an account."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _get_json(self, request: str, **params: str) -> Any:
        try:
            async with self._session.get(
                SEPA_BASE_URL, params={**SEPA_PARAMS, "request": request, **params}, timeout=TIMEOUT
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise WaterlevelError(f"Error fetching {request} from SEPA: {err}") from err
        if isinstance(payload, dict):
            # KiWIS reports a bad request as an object with a message, not an HTTP error
            raise WaterlevelError(f"SEPA rejected {request}: {payload.get('message', payload)}")
        return payload

    async def stations(self) -> list[dict[str, Any]]:
        """Every station that measures a river level."""
        return parse_stations(
            await self._get_json(
                "getStationList",
                parametertype_name="S",
                returnfields="station_no,station_name,station_latitude,station_longitude,river_name",
            )
        )

    async def series_ids(self, parameter: str, station: str | None = None) -> dict[str, str]:
        """The 15-minute series of stations for a parameter, "S" or "Q": all, or one station."""
        params = {"station_no": station} if station else {}
        return parse_series_ids(
            await self._get_json(
                "getTimeseriesList",
                parametertype_name=parameter,
                ts_shortname=LIVE_SERIES,
                returnfields="ts_id,station_no",
                **params,
            )
        )

    async def values(self, ts_ids: list[str], period: str) -> dict[str, list[Reading]]:
        """The readings of one or more series over an ISO 8601 period, such as PT3H."""
        return parse_values(
            await self._get_json(
                "getTimeseriesValues",
                ts_id=",".join(ts_ids),
                period=period,
                returnfields="Timestamp,Value",
            )
        )

    async def daily_means(self, station: str) -> list[Reading]:
        """A station's whole record of daily mean levels, oldest first (may be empty)."""
        ids = parse_series_ids(
            await self._get_json(
                "getTimeseriesList",
                station_no=station,
                parametertype_name="S",
                ts_shortname="HDay.Mean",
                returnfields="ts_id,station_no",
            )
        )
        if station not in ids:
            return []
        return (await self.values([ids[station]], "complete")).get(ids[station], [])

    async def station_series(self, station: str) -> tuple[str, str | None]:
        """A station's level and flow series ids. Raises StationNotFound without a level."""
        levels = await self.series_ids("S", station)
        if station not in levels:
            raise StationNotFound(f"No level series for SEPA station {station}")
        return levels[station], (await self.series_ids("Q", station)).get(station)
