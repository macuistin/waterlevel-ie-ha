"""Data coordinator for SEPA (Scotland) stations."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .api import Reading, WaterlevelError
from .const import (
    CONF_FLOW_TS,
    CONF_LEVEL_TS,
    CONF_SUGGESTED_THRESHOLDS,
    DOMAIN,
    SEPA_SCAN_INTERVAL,
    THRESHOLD_ENTITIES,
    TREND_RATE,
    WATER_LEVEL,
)
from .coordinator import StationCoordinator, WaterlevelConfigEntry, trend_rate
from .sepa_api import SepaApi
from .thresholds import suggest_thresholds

_LOGGER = logging.getLogger(__name__)
# Each update reads this much history: enough for the trend, and the latest reading
RECENT_PERIOD = "PT3H"


class SepaCoordinator(StationCoordinator):
    """Coordinator that fetches the Water Level and Discharge of one Scottish station."""

    manufacturer = "SEPA"
    # Levels are in metres above Ordnance Datum, which can be high in the Highlands
    threshold_limits = (-50.0, 1500.0)
    # SEPA limits anonymous access to a daily allowance, so update half as often
    default_scan_interval = SEPA_SCAN_INTERVAL
    min_scan_interval = SEPA_SCAN_INTERVAL

    def __init__(self, hass: HomeAssistant, entry: WaterlevelConfigEntry) -> None:
        super().__init__(hass, entry)
        self.api = SepaApi(async_get_clientsession(hass))
        self.level_ts: str = entry.data[CONF_LEVEL_TS]
        self.flow_ts: str | None = entry.data.get(CONF_FLOW_TS)

    @property
    def device_key(self) -> str:
        return f"sepa_{self.station}"

    @property
    def device_model(self) -> str:
        return "River gauge station"

    @property
    def configuration_url(self) -> str | None:
        return None

    def default_thresholds(self) -> tuple[dict[str, float | None], str]:
        """Thresholds from the percentiles of the station's daily record; unset without one.

        SEPA publishes no alarm levels, and every station measures from its own datum, so
        a station without enough history has no defaults and the flood entities stay
        unknown until the user sets thresholds.
        """
        if self.suggested and all(key in self.suggested for key in THRESHOLD_ENTITIES):
            return {key: self.suggested[key] for key in THRESHOLD_ENTITIES}, "history"
        return {key: None for key in THRESHOLD_ENTITIES}, "fallback"

    async def async_load_suggested_thresholds(self) -> None:
        """Look up thresholds from the station's daily record, once.

        The result is kept in the config entry, so the long history is only read once.
        """
        if self.suggested is not None:
            return
        try:
            history = await self.api.daily_means(self.station)
        except WaterlevelError as err:
            # Try again next time the station is set up
            _LOGGER.debug("Could not fetch history for station %s: %s", self.station, err)
            return
        # SEPA's levels are on one datum, so no offset is needed
        self.suggested = suggest_thresholds(history, 0.0) or {}
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_SUGGESTED_THRESHOLDS: self.suggested},
        )
        self._apply_default_thresholds()
        self.async_update_listeners()

    async def _async_update_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        # Reset per-update metadata, so stale attributes don't survive a failed fetch
        self.sensor_attrs = {}
        ids = [self.level_ts] + ([self.flow_ts] if self.flow_ts else [])
        try:
            series = await self.api.values(ids, RECENT_PERIOD)
        except WaterlevelError as err:
            _LOGGER.debug("Failed to fetch readings for station %s: %s", self.station, err)
            series = {}
        level: list[Reading] = series.get(self.level_ts, [])
        data[WATER_LEVEL] = self._keep_reading(WATER_LEVEL, level[-1] if level else None)
        if self.flow_ts:
            flow = series.get(self.flow_ts, [])
            data["discharge"] = self._keep_reading("discharge", flow[-1] if flow else None)

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
        data[TREND_RATE] = trend_rate(level)
        return data
