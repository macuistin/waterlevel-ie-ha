"""Data coordinator for the Waterlevel.ie integration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Reading, WaterlevelApi, WaterlevelError
from .const import (
    CONF_AVAILABLE_SENSORS,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_STATION,
    CONF_STATION_NAME,
    DOMAIN,
    SENSOR_TYPES,
    SUMMARY_SENSOR_TYPES,
    THRESHOLD_ENTITIES,
    WATER_LEVEL,
)

_LOGGER = logging.getLogger(__name__)
UPDATE_INTERVAL = timedelta(seconds=900)
# OPW asks for bulk files to be fetched no more than once every 15 minutes
LATEST_FEED_TTL = timedelta(minutes=15)
# Daily summaries only change once a day
SUMMARY_TTL = timedelta(hours=1)
# Trend threshold: ±this many metres vs yesterday mean = rising/falling
TREND_THRESHOLD = 0.02
DATA_LATEST = f"{DOMAIN}_latest"

type WaterlevelConfigEntry = ConfigEntry[WaterlevelCoordinator]


class LatestReadings:
    """Shared copy of the all-stations latest-readings feed.

    Every station's coordinator reads from one copy, so the feed is fetched at
    most once per LATEST_FEED_TTL however many stations are configured.
    """

    def __init__(self, api: WaterlevelApi) -> None:
        self._api = api
        self._data: dict[str, dict[str, Reading]] = {}
        self._fetched: datetime | None = None
        self._lock = asyncio.Lock()

    async def get(self, station: str) -> dict[str, Reading]:
        """Latest readings for one station, as {sensor: Reading}."""
        async with self._lock:
            now = dt_util.utcnow()
            if self._fetched is None or now - self._fetched >= LATEST_FEED_TTL:
                # Set before fetching so a broken feed isn't retried by every station
                self._fetched = now
                try:
                    self._data = await self._api.latest()
                except WaterlevelError as err:
                    _LOGGER.debug("Latest-readings feed unavailable: %s", err)
        return self._data.get(station, {})


def async_get_latest(hass: HomeAssistant) -> LatestReadings:
    """Return the shared latest-readings feed, creating it on first use.

    It is shared by every config entry, so it lives in hass.data rather than in one
    entry's runtime_data.
    """
    if DATA_LATEST not in hass.data:
        hass.data[DATA_LATEST] = LatestReadings(WaterlevelApi(async_get_clientsession(hass)))
    return hass.data[DATA_LATEST]


class WaterlevelCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that fetches data for all sensors on one station."""

    config_entry: WaterlevelConfigEntry

    def __init__(self, hass: HomeAssistant, entry: WaterlevelConfigEntry) -> None:
        self.station: str = entry.data[CONF_STATION]
        self.station_name: str = entry.data[CONF_STATION_NAME]
        self.available_sensors: list[str] = entry.data.get(CONF_AVAILABLE_SENSORS, [])
        self.latitude: float | None = entry.data.get(CONF_LATITUDE)
        self.longitude: float | None = entry.data.get(CONF_LONGITUDE)
        self.api = WaterlevelApi(async_get_clientsession(hass))
        self._latest = async_get_latest(hass)
        # Flood thresholds: THRESHOLD_ENTITIES defaults, overridden by the number entities
        self._thresholds: dict[str, float | None] = {
            key: cfg["default"] for key, cfg in THRESHOLD_ENTITIES.items()
        }
        # Extra per-sensor metadata (e.g. last_reported, week_min/max, trend)
        self.sensor_attrs: dict[str, dict] = {}
        # Daily summaries by source sensor, refreshed every SUMMARY_TTL
        self._summaries: dict[str, dict] = {}
        self._summaries_fetched: datetime | None = None
        # Only attempt the GeoJSON coordinate fetch once per session
        self._coord_fetch_attempted: bool = False
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.station}",
            update_interval=UPDATE_INTERVAL,
        )

    def get_threshold(self, key: str) -> float | None:
        """Return the current flood threshold (set by number entity)."""
        return self._thresholds.get(key)

    def set_threshold(self, key: str, value: float | None) -> None:
        """Store a flood threshold from the number entity and refresh the flood entities.

        The stage is worked out from the reading already held, so this makes no request.
        """
        self._thresholds[key] = value
        self.async_update_listeners()

    def flood_stage(self) -> str | None:
        """Return the flood stage for the latest water level, or None when it is unknown."""
        level = (self.data or {}).get(WATER_LEVEL)
        if level is None:
            return None
        stage = "normal"
        # Thresholds in ascending order of severity: the highest one reached wins
        for key in THRESHOLD_ENTITIES:
            threshold = self._thresholds.get(key)
            if threshold is not None and level >= threshold:
                stage = key
        return stage

    async def _async_update_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        # Reset per-update metadata — prevents stale attrs surviving a failed fetch
        self.sensor_attrs = {}

        # Lazily fetch GPS coordinates once per session if missing at setup time
        if self.latitude is None and not self._coord_fetch_attempted:
            self._coord_fetch_attempted = True
            if coords := await self.api.station_coords(self.station):
                self.latitude, self.longitude = coords

        live_keys = [k for k in self.available_sensors if k in SENSOR_TYPES]
        summary_keys = [k for k in self.available_sensors if k in SUMMARY_SENSOR_TYPES]

        latest = await self._latest.get(self.station)
        week = await self._fetch_week(WATER_LEVEL) if WATER_LEVEL in live_keys else []
        await self._refresh_summaries(summary_keys)

        for key in live_keys:
            reading = latest.get(key)
            if key == WATER_LEVEL and week:
                # The week CSV has the history the trend and week stats need;
                # use whichever of it and the feed is newer for the current value
                if reading is None or week[-1].time > reading.time:
                    reading = week[-1]
                values = [r.value for r in week]
                self.sensor_attrs.setdefault(key, {}).update(
                    week_min=round(min(values), 3), week_max=round(max(values), 3)
                )
            if reading is None:
                reading = await self._fetch_last_reading(key)
            data[key] = reading.value if reading else None
            if reading:
                self.sensor_attrs.setdefault(key, {})["last_reported"] = reading.time.isoformat()

        if live_keys and all(data[key] is None for key in live_keys):
            # Nothing came back: mark the entities unavailable rather than unknown.
            # The coordinator logs this once, and again when the station recovers.
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={
                    "station": self.station,
                    "error": "no sensor returned a reading",
                },
            )

        # Populate daily-summary sensor values
        for key in summary_keys:
            cfg = SUMMARY_SENSOR_TYPES[key]
            data[key] = self._summaries.get(cfg["summary_source"], {}).get(cfg["summary_col"])

        # Compute trend for water level sensor (0001) vs yesterday's daily mean
        if data.get(WATER_LEVEL) is not None:
            yesterday_mean = self._summaries.get(WATER_LEVEL, {}).get("mean")
            if yesterday_mean is not None:
                delta = data[WATER_LEVEL] - yesterday_mean
                if delta > TREND_THRESHOLD:
                    trend = "rising"
                elif delta < -TREND_THRESHOLD:
                    trend = "falling"
                else:
                    trend = "steady"
                self.sensor_attrs.setdefault(WATER_LEVEL, {})["trend"] = trend

        return data

    async def _fetch_week(self, sensor: str) -> list[Reading]:
        """Week of readings for a sensor, or [] if it can't be fetched."""
        try:
            return await self.api.series(self.station, sensor, "week")
        except WaterlevelError as err:
            _LOGGER.debug("Failed to fetch %s for station %s: %s", sensor, self.station, err)
            return []

    async def _fetch_last_reading(self, sensor: str) -> Reading | None:
        """Last reading from the day CSV, falling back to the week CSV."""
        for period in ("day", "week"):
            try:
                if readings := await self.api.series(self.station, sensor, period):
                    return readings[-1]
            except WaterlevelError as err:
                _LOGGER.debug(
                    "Failed to fetch %s %s for station %s: %s", period, sensor, self.station, err
                )
        return None

    async def _refresh_summaries(self, summary_keys: list[str]) -> None:
        """Re-fetch daily summaries when older than SUMMARY_TTL; keep old ones on failure."""
        now = dt_util.utcnow()
        if not summary_keys or (
            self._summaries_fetched and now - self._summaries_fetched < SUMMARY_TTL
        ):
            return
        self._summaries_fetched = now
        sources = list(
            dict.fromkeys(SUMMARY_SENSOR_TYPES[k]["summary_source"] for k in summary_keys)
        )
        results = await asyncio.gather(
            *(self.api.daily_summary(self.station, src) for src in sources),
            return_exceptions=True,
        )
        for src, result in zip(sources, results, strict=True):
            if isinstance(result, BaseException):
                _LOGGER.debug(
                    "Failed to fetch summary for station %s / %s: %s", self.station, src, result
                )
            elif result:
                self._summaries[src] = result
