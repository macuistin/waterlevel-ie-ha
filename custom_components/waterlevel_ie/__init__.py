"""Water Level Gauges: real-time river gauge data for Ireland, England and Scotland."""

from __future__ import annotations

from typing import Any

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_AVAILABLE_SENSORS,
    CONF_SOURCE,
    DOMAIN,
    SOURCE_EA,
    SOURCE_OPW,
    SOURCE_SEPA,
)
from .coordinator import StationCoordinator, WaterlevelConfigEntry, WaterlevelCoordinator
from .ea_coordinator import EnvironmentAgencyCoordinator
from .sepa_coordinator import SepaCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER]


async def async_setup_entry(hass: HomeAssistant, entry: WaterlevelConfigEntry) -> bool:
    coordinator: StationCoordinator
    source = entry.data.get(CONF_SOURCE, SOURCE_OPW)
    if source == SOURCE_EA:
        coordinator = EnvironmentAgencyCoordinator(hass, entry)
    elif source == SOURCE_SEPA:
        coordinator = SepaCoordinator(hass, entry)
    else:
        coordinator = WaterlevelCoordinator(hass, entry)
        # Before the first update, so the percentile has its flows from the start
        await coordinator.async_load_flow_percentiles()
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        # Retry setup when the station can't be read at all. A station that has only
        # stopped reporting still sets up, so Data Stale and Last Reading can show it.
        if coordinator.last_reading_time is None:
            raise
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Reload for option or sensor changes, not when the threshold suggestions are stored
    reload_key = _reload_key(entry)

    async def _async_entry_updated(hass: HomeAssistant, entry: WaterlevelConfigEntry) -> None:
        if _reload_key(entry) != reload_key:
            await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    if isinstance(coordinator, WaterlevelCoordinator):
        entry.async_create_background_task(
            hass, _async_load_history(coordinator), f"{DOMAIN} history"
        )
    elif isinstance(coordinator, SepaCoordinator):
        entry.async_create_background_task(
            hass, coordinator.async_load_suggested_thresholds(), f"{DOMAIN} history"
        )
    return True


async def _async_load_history(coordinator: WaterlevelCoordinator) -> None:
    """Look up what Hydro-Data holds about the station, once: thresholds, then details."""
    await coordinator.async_load_suggested_thresholds()
    await coordinator.async_load_station_info()


def _reload_key(entry: WaterlevelConfigEntry) -> tuple[dict[str, Any], tuple[str, ...]]:
    return (dict(entry.options), tuple(entry.data.get(CONF_AVAILABLE_SENSORS, [])))


async def async_unload_entry(hass: HomeAssistant, entry: WaterlevelConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
