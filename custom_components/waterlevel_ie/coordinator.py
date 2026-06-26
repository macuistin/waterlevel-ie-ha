"""Data coordinator for the Waterlevel.ie integration."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN, BASE_URL, SUMMARY_URL, GEOJSON_URL, SUMMARY_SENSOR_TYPES,
    CONF_STATION, CONF_STATION_NAME, CONF_AVAILABLE_SENSORS,
    CONF_LATITUDE, CONF_LONGITUDE, THRESHOLD_ENTITIES,
)

_LOGGER = logging.getLogger(__name__)
UPDATE_INTERVAL = timedelta(seconds=900)
# Trend threshold: ±this many metres vs yesterday mean = rising/falling
TREND_THRESHOLD = 0.02


class WaterlevelCoordinator(DataUpdateCoordinator):
    """Coordinator that fetches data for all sensors on one station."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._entry = entry
        self.station: str = entry.data[CONF_STATION]
        self.station_name: str = entry.data[CONF_STATION_NAME]
        self.available_sensors: list[str] = entry.data.get(CONF_AVAILABLE_SENSORS, [])
        self.latitude: float | None = entry.data.get(CONF_LATITUDE)
        self.longitude: float | None = entry.data.get(CONF_LONGITUDE)
        # Flood threshold values — seeded from THRESHOLD_ENTITIES defaults, overridden by number entities
        self._thresholds: dict[str, float | None] = {
            key: cfg["default"] for key, cfg in THRESHOLD_ENTITIES.items()
        }
        # Extra per-sensor metadata (e.g. last_reported, week_min/max, trend)
        self.sensor_attrs: dict[str, dict] = {}
        # Only attempt the GeoJSON coordinate fetch once per session
        self._coord_fetch_attempted: bool = False
        super().__init__(
            hass, _LOGGER,
            name=f"{DOMAIN}_{self.station}",
            update_interval=UPDATE_INTERVAL,
        )

    def get_threshold(self, key: str) -> float | None:
        """Return the current flood threshold (set by number entity)."""
        return self._thresholds.get(key)

    def set_threshold(self, key: str, value: float | None) -> None:
        """Store a flood threshold value from the number entity."""
        self._thresholds[key] = value

    async def _async_update_data(self) -> dict:
        data: dict[str, float | None] = {}
        # Reset per-update metadata — prevents stale attrs surviving a failed fetch
        self.sensor_attrs = {}

        # Lazily fetch GPS coordinates once per session if missing at setup time
        if self.latitude is None and not self._coord_fetch_attempted:
            await self._fetch_coords_if_missing()

        # Split keys: live sensors vs daily-summary sensors
        live_keys = [k for k in self.available_sensors if k not in SUMMARY_SENSOR_TYPES]
        summary_keys = [k for k in self.available_sensors if k in SUMMARY_SENSOR_TYPES]

        # Fetch which source sensors need a summary CSV (deduplicated, order-stable)
        summary_sources = list(dict.fromkeys(
            SUMMARY_SENSOR_TYPES[k]["summary_source"] for k in summary_keys
        ))

        async with aiohttp.ClientSession() as session:
            # Parallel fetch: live CSVs + one summary CSV per source sensor
            live_coros = [self._fetch_sensor(session, k) for k in live_keys]
            summary_coros = [self._fetch_summary(session, src) for src in summary_sources]

            all_results = await asyncio.gather(
                *live_coros, *summary_coros,
                return_exceptions=True,
            )

        # Unpack live results
        # _fetch_sensor now returns (value, last_reported, week_min, week_max)
        for key, result in zip(live_keys, all_results[: len(live_keys)]):
            if isinstance(result, Exception):
                _LOGGER.warning("Failed to fetch %s for station %s: %s", key, self.station, result)
                data[key] = None
            else:
                value, last_reported, week_min, week_max = result
                data[key] = value
                attrs: dict = {}
                if last_reported:
                    attrs["last_reported"] = last_reported
                if week_min is not None:
                    attrs["week_min"] = round(week_min, 3)
                if week_max is not None:
                    attrs["week_max"] = round(week_max, 3)
                if attrs:
                    self.sensor_attrs[key] = attrs

        # Unpack summary results (one dict per source sensor)
        summary_by_source: dict[str, dict] = {}
        for src, result in zip(summary_sources, all_results[len(live_keys):]):
            if isinstance(result, Exception):
                _LOGGER.warning("Failed to fetch summary for station %s / %s: %s", self.station, src, result)
                summary_by_source[src] = {}
            else:
                summary_by_source[src] = result or {}

        # Populate daily-summary sensor values
        for key in summary_keys:
            cfg = SUMMARY_SENSOR_TYPES[key]
            src = cfg["summary_source"]
            col = cfg["summary_col"]  # already lowercase: "min" / "mean" / "max"
            data[key] = summary_by_source.get(src, {}).get(col)

        # Compute trend for water level sensor (0001) vs yesterday's daily mean
        if "0001" in data and data["0001"] is not None:
            yesterday_mean = summary_by_source.get("0001", {}).get("mean")
            if yesterday_mean is not None:
                delta = data["0001"] - yesterday_mean
                if delta > TREND_THRESHOLD:
                    trend = "rising"
                elif delta < -TREND_THRESHOLD:
                    trend = "falling"
                else:
                    trend = "steady"
                self.sensor_attrs.setdefault("0001", {})["trend"] = trend

        # Compute flood stage from thresholds set via number entities
        level = data.get("0001")
        watch = self._thresholds.get("watch")
        alert = self._thresholds.get("alert")
        serious = self._thresholds.get("serious")
        if level is not None and any(t is not None for t in (watch, alert, serious)):
            if serious is not None and level >= serious:
                stage = "serious"
            elif alert is not None and level >= alert:
                stage = "alert"
            elif watch is not None and level >= watch:
                stage = "watch"
            else:
                stage = "normal"
            data["flood_stage"] = stage
        else:
            data["flood_stage"] = "normal"

        return data

    async def _fetch_coords_if_missing(self) -> None:
        """Fetch GPS coordinates from the OPW GeoJSON if not stored at setup time."""
        self._coord_fetch_attempted = True
        padded = self.station.zfill(10)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(GEOJSON_URL, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status != 200:
                        return
                    geo = await resp.json(content_type=None)
            for feature in geo.get("features", []):
                ref = str(feature.get("properties", {}).get("ref", ""))
                if ref == padded:
                    lon, lat = feature["geometry"]["coordinates"]
                    self.latitude = float(lat)
                    self.longitude = float(lon)
                    _LOGGER.debug("Fetched coordinates for station %s: %.5f, %.5f", self.station, lat, lon)
                    break
        except Exception as err:
            _LOGGER.debug("Could not fetch coordinates for station %s: %s", self.station, err)

    async def _fetch_sensor(
        self, session: aiohttp.ClientSession, sensor_key: str
    ) -> tuple[float | None, str | None, float | None, float | None]:
        """Fetch the latest live reading plus week stats.

        Returns (value, last_reported_str, week_min, week_max).
        Falls back to week CSV if today has no data.
        """
        value: float | None = None
        last_reported: str | None = None
        week_min: float | None = None
        week_max: float | None = None

        for period in ("day", "week"):
            url = BASE_URL.format(period=period, station=self.station, sensor=sensor_key)
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    resp.raise_for_status()
                    text = await resp.text()
            except aiohttp.ClientError as err:
                if period == "week":
                    raise UpdateFailed(f"HTTP error fetching {url}: {err}") from err
                continue

            lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]

            # For the week CSV, compute min/max across all valid readings
            if period == "week":
                week_vals: list[float] = []
                for line in lines:
                    parts = line.split(",")
                    if len(parts) >= 2 and parts[1].strip():
                        try:
                            week_vals.append(float(parts[1].strip()))
                        except ValueError:
                            pass
                if week_vals:
                    week_min = min(week_vals)
                    week_max = max(week_vals)

            # Walk back to find the last row with a real value
            for line in reversed(lines):
                parts = line.split(",")
                if len(parts) >= 2 and parts[1].strip():
                    try:
                        v = float(parts[1].strip())
                        if value is None:
                            value = v
                            last_reported = parts[0].strip()
                            if period == "week":
                                _LOGGER.debug(
                                    "Station %s sensor %s: no data today, last known from week: %s",
                                    self.station, sensor_key, last_reported,
                                )
                        break
                    except ValueError:
                        continue

            # Got a value from day CSV — still fetch week for stats if it's 0001
            if value is not None and period == "day" and sensor_key == "0001":
                week_url = BASE_URL.format(period="week", station=self.station, sensor=sensor_key)
                try:
                    async with session.get(week_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            wtext = await resp.text()
                            week_vals = []
                            for line in wtext.strip().splitlines():
                                parts = line.strip().split(",")
                                if len(parts) >= 2 and parts[1].strip():
                                    try:
                                        week_vals.append(float(parts[1].strip()))
                                    except ValueError:
                                        pass
                            if week_vals:
                                week_min = min(week_vals)
                                week_max = max(week_vals)
                except Exception:
                    pass
                break  # Don't also iterate with period="week" now

            if value is not None:
                break

        return value, last_reported, week_min, week_max

    async def _fetch_summary(self, session: aiohttp.ClientSession, sensor_key: str) -> dict:
        """Fetch the most recent daily stats (min/mean/max) from the summary CSV."""
        padded = self.station.zfill(10)
        url = SUMMARY_URL.format(station_padded=padded, sensor=sensor_key)
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                text = await resp.text()
        except aiohttp.ClientError as err:
            _LOGGER.warning("Failed to fetch summary %s: %s", url, err)
            return {}

        # CSV columns: Datetime,Value,Min,Mean,Max
        # Rows with date only (no time) are daily aggregates with Min/Mean/Max populated.
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        # Walk back to find the most recent daily aggregate row (non-empty Mean)
        for line in reversed(lines):
            if line.startswith("Datetime"):
                break
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5 and parts[3]:  # Mean column (index 3) is non-empty
                try:
                    return {
                        "min": float(parts[2]) if parts[2] else None,
                        "mean": float(parts[3]) if parts[3] else None,
                        "max": float(parts[4]) if parts[4] else None,
                    }
                except ValueError:
                    continue

        return {}
