"""Water Level IE: real-time Irish river gauge data from waterlevel.ie."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_AVAILABLE_SENSORS, DOMAIN
from .coordinator import WaterlevelConfigEntry, WaterlevelCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER]


async def async_setup_entry(hass: HomeAssistant, entry: WaterlevelConfigEntry) -> bool:
    coordinator = WaterlevelCoordinator(hass, entry)
    # Raises ConfigEntryNotReady when the station can't be reached, so setup retries
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Reload for option or sensor changes, not when the threshold suggestions are stored
    reload_key = _reload_key(entry)

    async def _async_entry_updated(hass: HomeAssistant, entry: WaterlevelConfigEntry) -> None:
        if _reload_key(entry) != reload_key:
            await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    entry.async_create_background_task(
        hass, coordinator.async_load_suggested_thresholds(), f"{DOMAIN} thresholds"
    )
    return True


def _reload_key(entry: WaterlevelConfigEntry) -> tuple:
    return (dict(entry.options), tuple(entry.data.get(CONF_AVAILABLE_SENSORS, [])))


async def async_unload_entry(hass: HomeAssistant, entry: WaterlevelConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
