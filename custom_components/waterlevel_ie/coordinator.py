"""Data coordinator for the Waterlevel.ie integration."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Reading, StationNotFound, WaterlevelApi, WaterlevelError
from .const import (
    CONF_ALERT_STAGE,
    CONF_AVAILABLE_SENSORS,
    CONF_FLOW_PERCENTILES,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_SCAN_INTERVAL,
    CONF_STALE_HOURS,
    CONF_STATION,
    CONF_STATION_INFO,
    CONF_STATION_NAME,
    CONF_SUGGESTED_THRESHOLDS,
    DEFAULT_ALERT_STAGE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_HOURS,
    DOMAIN,
    FLOOD_STAGES,
    FLOW_PERCENTILE,
    HYDRO_SENSOR_TYPES,
    MIN_SCAN_INTERVAL,
    SENSOR_TYPES,
    STATION_PAGE_URL,
    SUMMARY_SENSOR_TYPES,
    THRESHOLD_ENTITIES,
    TREND_RATE,
    WATER_LEVEL,
)
from .flow import flow_percentile
from .thresholds import alarm_thresholds, datum_offset, suggest_thresholds

_LOGGER = logging.getLogger(__name__)
# OPW asks for bulk files to be fetched no more than once every 15 minutes
LATEST_FEED_TTL = timedelta(minutes=15)
# Daily summaries only change once a day
SUMMARY_TTL = timedelta(hours=1)
# Trend threshold: ±this many metres vs yesterday mean = rising/falling
TREND_THRESHOLD = 0.02
DATA_LATEST = f"{DOMAIN}_latest"
# The trend rate is fitted over this much recent history...
TREND_WINDOW = timedelta(hours=3)
# ...and needs readings spanning at least this long
TREND_MIN_SPAN = timedelta(minutes=45)

type WaterlevelConfigEntry = ConfigEntry[StationCoordinator]


def trend_rate(readings: list[Reading]) -> float | None:
    """Rate of change in m/h: the least-squares slope over the last TREND_WINDOW.

    readings must be oldest first. Returns None without enough recent readings.
    """
    if not readings:
        return None
    recent = [r for r in readings if r.time >= readings[-1].time - TREND_WINDOW]
    if len(recent) < 3 or recent[-1].time - recent[0].time < TREND_MIN_SPAN:
        return None
    hours = [(r.time - recent[0].time).total_seconds() / 3600 for r in recent]
    mean_h = sum(hours) / len(hours)
    mean_v = sum(r.value for r in recent) / len(recent)
    slope = sum((h - mean_h) * (r.value - mean_v) for h, r in zip(hours, recent, strict=True))
    return round(slope / sum((h - mean_h) ** 2 for h in hours), 3)


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
    latest: LatestReadings | None = hass.data.get(DATA_LATEST)
    if latest is None:
        latest = hass.data[DATA_LATEST] = LatestReadings(
            WaterlevelApi(async_get_clientsession(hass))
        )
    return latest


class StationCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """What every source's coordinator shares: one station's readings and flood stage.

    A source (OPW, the Environment Agency, ...) subclasses this, reads its own API in
    _async_update_data and says how its station appears as a device. The platforms only
    use what is defined here, so they work for every source.
    """

    config_entry: WaterlevelConfigEntry
    # How the station appears in Home Assistant's device registry
    manufacturer: str = "OPW Ireland"
    # Limits of the flood threshold number entities, in metres
    threshold_limits: tuple[float, float] = (0.0, 15.0)
    # Update interval in minutes: the default, and the shortest the options allow
    default_scan_interval: int = DEFAULT_SCAN_INTERVAL
    min_scan_interval: int = MIN_SCAN_INTERVAL

    def __init__(self, hass: HomeAssistant, entry: WaterlevelConfigEntry) -> None:
        self.station: str = entry.data[CONF_STATION]
        self.station_name: str = entry.data[CONF_STATION_NAME]
        self.available_sensors: list[str] = entry.data.get(CONF_AVAILABLE_SENSORS, [])
        self.latitude: float | None = entry.data.get(CONF_LATITUDE)
        self.longitude: float | None = entry.data.get(CONF_LONGITUDE)
        # Thresholds suggested from the station's history: None until looked up,
        # {} when the station has no usable history
        self.suggested: dict[str, float] | None = entry.data.get(CONF_SUGGESTED_THRESHOLDS)
        # What the source publishes about the station, None until looked up: its
        # details, and reference levels in the same units as the Water Level sensor
        self.station_info: dict[str, Any] | None = entry.data.get(CONF_STATION_INFO)
        # The station's flow percentiles ({"p50": m³/s, ...}): None until looked up,
        # {} when the source publishes none
        self.flow_percentiles: dict[str, float] | None = entry.data.get(CONF_FLOW_PERCENTILES)
        # Flood thresholds start at the defaults (see default_thresholds) and are
        # overridden by the number entities
        self._thresholds: dict[str, float | None] = dict(self.default_thresholds()[0])
        # Thresholds set by the user (or restored from a previous run)
        self._user_thresholds: set[str] = set()
        # Lowest flood stage that turns Flood Alert on
        self.alert_stage: str = entry.options.get(CONF_ALERT_STAGE, DEFAULT_ALERT_STAGE)
        # Extra per-sensor metadata (e.g. last_reported, week_min/max, trend)
        self.sensor_attrs: dict[str, dict[str, Any]] = {}
        # Last good reading per sensor, kept across updates so a short outage doesn't
        # blank the sensors
        self._readings: dict[str, Reading] = {}
        # A station counts as stale (likely offline) after this long without a reading.
        # Readings are kept through any outage; stale ones are flagged, not hidden.
        self.stale_after = timedelta(hours=entry.options.get(CONF_STALE_HOURS, DEFAULT_STALE_HOURS))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.device_key}",
            update_interval=timedelta(
                minutes=entry.options.get(CONF_SCAN_INTERVAL, self.default_scan_interval)
            ),
        )

    @property
    def device_key(self) -> str:
        """The station's key in unique ids and device identifiers.

        OPW stations use their 5-digit number, which has never changed. Every other
        source prefixes its own station reference, so the keys can't collide.
        """
        return self.station

    @property
    def device_model(self) -> str:
        """The device's model, shown in the device registry."""
        return f"Gauge Station {self.station}"

    @property
    def configuration_url(self) -> str | None:
        """A page about the station on its publisher's site."""
        return STATION_PAGE_URL.format(station_padded=self.station.zfill(10))

    def default_thresholds(self) -> tuple[dict[str, float | None], str]:
        """The thresholds used until the user sets their own, and where they come from.

        The base class has no data to suggest from, so it returns the fixed fallback.
        """
        return {key: cfg["default"] for key, cfg in THRESHOLD_ENTITIES.items()}, "fallback"

    def _apply_default_thresholds(self) -> None:
        """Move every threshold the user hasn't set to the current defaults."""
        defaults, _ = self.default_thresholds()
        for key, value in defaults.items():
            if key not in self._user_thresholds:
                self._thresholds[key] = value

    def get_threshold(self, key: str) -> float | None:
        """Return the current flood threshold (set by number entity)."""
        return self._thresholds.get(key)

    def set_threshold(self, key: str, value: float | None) -> None:
        """Store a flood threshold from the number entity and refresh the flood entities.

        The stage is worked out from the reading already held, so this makes no request.
        """
        self._thresholds[key] = value
        self._user_thresholds.add(key)
        self.async_update_listeners()

    @property
    def last_reading_time(self) -> datetime | None:
        """Time of the newest reading from any of the station's live sensors."""
        times = [r.time for k, r in self._readings.items() if k in SENSOR_TYPES]
        return max(times, default=None)

    def is_stale(self) -> bool:
        """True when no live sensor has a reading newer than stale_after."""
        last = self.last_reading_time
        return last is None or dt_util.utcnow() - last > self.stale_after

    def is_alert(self) -> bool | None:
        """True when the flood stage is at or above the alert stage option; None if unknown."""
        if (stage := self.flood_stage()) is None:
            return None
        return FLOOD_STAGES.index(stage) >= FLOOD_STAGES.index(self.alert_stage)

    def flood_stage(self) -> str | None:
        """Return the flood stage for the latest water level, or None when it is unknown."""
        level = (self.data or {}).get(WATER_LEVEL)
        if level is None or all(value is None for value in self._thresholds.values()):
            return None
        stage = "normal"
        # Thresholds in ascending order of severity: the highest one reached wins
        for key in THRESHOLD_ENTITIES:
            threshold = self._thresholds.get(key)
            if threshold is not None and level >= threshold:
                stage = key
        return stage

    def _keep_reading(self, key: str, reading: Reading | None) -> float | None:
        """Record a sensor's reading and return its value.

        Keeps the last good reading through outages (its timestamp shows its age),
        and never lets an older reading replace a newer one.
        """
        if (previous := self._readings.get(key)) and (
            reading is None or previous.time > reading.time
        ):
            reading = previous
        if reading is None:
            return None
        self._readings[key] = reading
        attrs = self.sensor_attrs.setdefault(key, {})
        attrs["last_reported"] = reading.time.isoformat()
        # True once this reading is older than stale_after, so automations can tell
        # a kept last value from a current one
        attrs["stale"] = dt_util.utcnow() - reading.time > self.stale_after
        return reading.value


class WaterlevelCoordinator(StationCoordinator):
    """Coordinator that fetches data for all sensors on one OPW station."""

    def __init__(self, hass: HomeAssistant, entry: WaterlevelConfigEntry) -> None:
        super().__init__(hass, entry)
        self.api = WaterlevelApi(async_get_clientsession(hass))
        self._latest = async_get_latest(hass)
        # The last good week of water levels, kept across updates like the readings
        self._week: dict[str, list[Reading]] = {}
        # Daily summaries by source sensor, refreshed every SUMMARY_TTL
        self._summaries: dict[str, dict[str, float | None]] = {}
        self._summaries_fetched: datetime | None = None
        # Only attempt the GeoJSON coordinate fetch once per session
        self._coord_fetch_attempted: bool = False

    def default_thresholds(self) -> tuple[dict[str, float | None], str]:
        """The thresholds used until the user sets their own, and where they come from.

        The source is "alarm_levels" (the station's OPW alarm levels), "history"
        (percentiles of its daily record) or "fallback" (the fixed Barrow values).
        """
        if alarm := alarm_thresholds((self.station_info or {}).get("levels", {})):
            return dict(alarm), "alarm_levels"
        if self.suggested and all(key in self.suggested for key in THRESHOLD_ENTITIES):
            return {key: self.suggested[key] for key in THRESHOLD_ENTITIES}, "history"
        return super().default_thresholds()

    async def async_load_suggested_thresholds(self) -> None:
        """Look up thresholds from the station's OPW Hydro-Data history, once.

        Thresholds the user hasn't set move to the suggestions. The result is kept in
        the config entry so the (large) history file is only fetched once per station.
        """
        if self.suggested is not None or WATER_LEVEL not in self.available_sensors:
            return
        try:
            history = await self.api.hydro_series(self.station, "S", "year", "WEB.Day.Mean")
            recent = await self.api.hydro_series(self.station, "S", "week")
        except StationNotFound:
            history = recent = []
        except WaterlevelError as err:
            # Try again next time the station is set up
            _LOGGER.debug("Could not fetch history for station %s: %s", self.station, err)
            return
        offset = datum_offset(self._week.get(WATER_LEVEL, []), recent)
        self.suggested = (suggest_thresholds(history, offset) if offset is not None else None) or {}
        _LOGGER.debug("Suggested thresholds for station %s: %s", self.station, self.suggested)
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_SUGGESTED_THRESHOLDS: self.suggested},
        )
        self._apply_default_thresholds()
        self.async_update_listeners()

    async def async_load_station_info(self) -> None:
        """Look up the station's Hydro-Data details and alarm levels, once.

        The alarm levels are on Hydro-Data's absolute datum, so they are shifted onto
        the staff gauge the Water Level sensor reads, the same way the threshold
        suggestions are. The result is kept in the config entry.
        """
        if self.station_info is not None:
            return
        info: dict[str, str | float] = {}
        absolute: dict[str, float] = {}
        recent: list[Reading] = []
        try:
            with contextlib.suppress(StationNotFound):
                info = await self.api.station_info(self.station)
            if WATER_LEVEL in self.available_sensors:
                with contextlib.suppress(StationNotFound):
                    absolute = await self.api.alarm_levels(self.station)
                if absolute:
                    with contextlib.suppress(StationNotFound):
                        recent = await self.api.hydro_series(self.station, "S", "week")
        except WaterlevelError as err:
            # Try again next time the station is set up
            _LOGGER.debug("Could not fetch details for station %s: %s", self.station, err)
            return
        levels: dict[str, float] = {}
        if (offset := datum_offset(self._week.get(WATER_LEVEL, []), recent)) is not None:
            levels = {name: round(value + offset, 2) for name, value in absolute.items()}
        self.station_info = {"details": info, "levels": levels}
        _LOGGER.debug("Details for station %s: %s", self.station, self.station_info)
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_STATION_INFO: self.station_info},
        )
        self._apply_default_thresholds()
        self.async_update_listeners()

    async def async_load_flow_percentiles(self) -> None:
        """Look up the station's flow percentiles from Hydro-Data, once.

        They describe the station's whole record, so the result is kept in the config entry.
        """
        if self.flow_percentiles is not None or "discharge" not in self.available_sensors:
            return
        try:
            percentiles = await self.api.flow_percentiles(self.station)
        except StationNotFound:
            percentiles = {}
        except WaterlevelError as err:
            # Try again next time the station is set up
            _LOGGER.debug("Could not fetch flow percentiles for station %s: %s", self.station, err)
            return
        self.flow_percentiles = percentiles
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={**self.config_entry.data, CONF_FLOW_PERCENTILES: percentiles},
        )

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
        if WATER_LEVEL in live_keys and (fetched := await self._fetch_week(WATER_LEVEL)):
            self._week[WATER_LEVEL] = fetched
        await self._refresh_summaries(summary_keys)

        for key in live_keys:
            reading = latest.get(key)
            if key == WATER_LEVEL and (week := self._week.get(WATER_LEVEL)):
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
            data[key] = self._keep_reading(key, reading)

        for key in (k for k in self.available_sensors if k in HYDRO_SENSOR_TYPES):
            cfg = HYDRO_SENSOR_TYPES[key]
            try:
                readings = await self.api.hydro_series(
                    self.station, cfg["hydro_param"], "week", cfg["hydro_series"]
                )
            except WaterlevelError as err:
                _LOGGER.debug("Failed to fetch %s for station %s: %s", key, self.station, err)
                readings = []
            data[key] = self._keep_reading(key, readings[-1] if readings else None)

        if self.flow_percentiles and "discharge" in data:
            data[FLOW_PERCENTILE] = flow_percentile(data["discharge"], self.flow_percentiles)
            # The percentile is as old as the flow it comes from
            self.sensor_attrs[FLOW_PERCENTILE] = {
                k: v
                for k, v in self.sensor_attrs.get("discharge", {}).items()
                if k in ("last_reported", "stale")
            }

        # Rate of change from the last few hours of water levels
        if week := self._week.get(WATER_LEVEL):
            current = self._readings.get(WATER_LEVEL)
            if current and current.time > week[-1].time:
                week = [*week, current]
            data[TREND_RATE] = trend_rate(week)

        if live_keys and self.last_reading_time is None:
            # Nothing has ever been read, so there is no last value to show. Entities
            # go unavailable (and setup retries); once there is a reading, it is kept
            # through any outage and Data Stale reports its age.
            # The coordinator logs this once, and again when the station recovers.
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={
                    "station": self.station,
                    "error": "no reading available",
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
