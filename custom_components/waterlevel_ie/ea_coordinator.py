"""Data coordinator for Environment Agency (England) stations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Reading, WaterlevelError
from .const import (
    CONF_FLOW_MEASURE,
    CONF_LEVEL_MEASURE,
    CONF_RLOI_ID,
    DOMAIN,
    EA_STATION_PAGE_URL,
    TREND_RATE,
    WATER_LEVEL,
)
from .coordinator import (
    LATEST_FEED_TTL,
    TREND_WINDOW,
    StationCoordinator,
    WaterlevelConfigEntry,
    trend_rate,
)
from .ea_api import EnvironmentAgencyApi
from .thresholds import stage_scale_thresholds

_LOGGER = logging.getLogger(__name__)
DATA_EA_LATEST = f"{DOMAIN}_ea_latest"
# Readings are fetched from this far back for the trend, which needs TREND_WINDOW of them
_TREND_FETCH = TREND_WINDOW + timedelta(minutes=30)


class EaLatestReadings:
    """Shared copy of the all-stations latest-readings feed.

    Every station's coordinator reads from one copy, so the feed is fetched at most
    once per LATEST_FEED_TTL however many stations are configured. The API asks for
    this instead of one call per station.
    """

    def __init__(self, api: EnvironmentAgencyApi) -> None:
        self._api = api
        self._data: dict[str, Reading] = {}
        self._fetched: datetime | None = None
        self._lock = asyncio.Lock()

    async def get(self, measure: str) -> Reading | None:
        """The latest reading of one measure, or None if the feed has none."""
        async with self._lock:
            now = dt_util.utcnow()
            if self._fetched is None or now - self._fetched >= LATEST_FEED_TTL:
                # Set before fetching so a broken feed isn't retried by every station
                self._fetched = now
                try:
                    self._data = await self._api.latest()
                except WaterlevelError as err:
                    _LOGGER.debug("Latest-readings feed unavailable: %s", err)
        return self._data.get(measure)


def async_get_ea_latest(hass: HomeAssistant) -> EaLatestReadings:
    """Return the shared latest-readings feed, creating it on first use.

    It is shared by every config entry, so it lives in hass.data.
    """
    latest: EaLatestReadings | None = hass.data.get(DATA_EA_LATEST)
    if latest is None:
        latest = hass.data[DATA_EA_LATEST] = EaLatestReadings(
            EnvironmentAgencyApi(async_get_clientsession(hass))
        )
    return latest


class EnvironmentAgencyCoordinator(StationCoordinator):
    """Coordinator that fetches the Water Level and Discharge of one England station."""

    manufacturer = "Environment Agency"
    # Levels are on the station's own datum, which can be well above sea level
    threshold_limits = (-50.0, 1000.0)

    def __init__(self, hass: HomeAssistant, entry: WaterlevelConfigEntry) -> None:
        super().__init__(hass, entry)
        self.api = EnvironmentAgencyApi(async_get_clientsession(hass))
        self._latest = async_get_ea_latest(hass)
        self.level_measure: str = entry.data[CONF_LEVEL_MEASURE]
        self.flow_measure: str | None = entry.data.get(CONF_FLOW_MEASURE)
        self.rloi_id: str | None = entry.data.get(CONF_RLOI_ID) or None

    @property
    def device_key(self) -> str:
        return f"ea_{self.station}"

    @property
    def device_model(self) -> str:
        return "River gauge station"

    @property
    def configuration_url(self) -> str | None:
        if self.rloi_id is None:
            return None
        return EA_STATION_PAGE_URL.format(rloi_id=self.rloi_id)

    def default_thresholds(self) -> tuple[dict[str, float | None], str]:
        """Thresholds from the station's stage scale; unset where it has none.

        An Environment Agency station has no fixed fallback, because every station
        measures from its own datum, so the flood entities stay unknown until the user
        sets thresholds.
        """
        levels = (self.station_info or {}).get("levels", {})
        if (scale := stage_scale_thresholds(levels)) is not None:
            return dict(scale), "stage_scale"
        return {"watch": None, "alert": None, "serious": None}, "fallback"

    async def _async_update_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        # Reset per-update metadata, so stale attributes don't survive a failed fetch
        self.sensor_attrs = {}

        level = await self._latest.get(self.level_measure)
        # The last few hours give the trend, and may be newer than the shared feed
        try:
            recent = await self.api.readings_since(
                self.level_measure, dt_util.utcnow() - _TREND_FETCH
            )
        except WaterlevelError as err:
            _LOGGER.debug("Failed to fetch %s for station %s: %s", WATER_LEVEL, self.station, err)
            recent = []
        if recent and (level is None or recent[-1].time > level.time):
            level = recent[-1]
        data[WATER_LEVEL] = self._keep_reading(WATER_LEVEL, level)

        if "discharge" in self.available_sensors and self.flow_measure:
            data["discharge"] = self._keep_reading(
                "discharge", await self._latest.get(self.flow_measure)
            )

        if self.last_reading_time is None:
            # Nothing has ever been read, so there is no last value to show. Entities
            # go unavailable (and setup retries); once there is a reading, it is kept
            # through any outage and Data Stale reports its age.
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={
                    "station": self.station,
                    "error": "no reading available",
                },
            )

        if current := self._readings.get(WATER_LEVEL):
            if not recent or current.time > recent[-1].time:
                recent = [*recent, current]
            data[TREND_RATE] = trend_rate(recent)
        return data
