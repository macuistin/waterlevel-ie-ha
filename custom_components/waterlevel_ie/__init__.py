"""Water Level IE: real-time Irish river gauge data from waterlevel.ie."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import WaterlevelConfigEntry, WaterlevelCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER]


async def async_setup_entry(hass: HomeAssistant, entry: WaterlevelConfigEntry) -> bool:
    coordinator = WaterlevelCoordinator(hass, entry)
    # Raises ConfigEntryNotReady when the station can't be reached, so setup retries
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: WaterlevelConfigEntry) -> None:
    """Reload the station when its options or its sensor list change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WaterlevelConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
