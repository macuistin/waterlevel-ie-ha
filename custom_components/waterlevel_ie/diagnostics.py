"""Diagnostics for Water Level IE.

Everything here is public data about an OPW gauge station, so nothing is redacted.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import THRESHOLD_ENTITIES
from .coordinator import WaterlevelConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WaterlevelConfigEntry
) -> dict[str, Any]:
    """Return the entry, the latest readings and the coordinator's state."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": repr(coordinator.last_exception)
            if coordinator.last_exception
            else None,
            "update_interval_seconds": coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None,
            "latitude": coordinator.latitude,
            "longitude": coordinator.longitude,
        },
        "data": coordinator.data,
        "sensor_attributes": coordinator.sensor_attrs,
        "thresholds": {key: coordinator.get_threshold(key) for key in THRESHOLD_ENTITIES},
        "flood_stage": coordinator.flood_stage(),
    }
