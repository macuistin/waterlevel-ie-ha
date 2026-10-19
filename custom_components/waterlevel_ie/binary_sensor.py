"""Binary sensor platform for Water Level IE: flood alert on/off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SENSOR_TYPES, WATER_LEVEL
from .coordinator import WaterlevelConfigEntry, WaterlevelCoordinator
from .entity import WaterlevelEntity

# Entities only read the coordinator's data, so updates need no limit
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WaterlevelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    if WATER_LEVEL in coordinator.available_sensors:
        entities.append(FloodAlertBinarySensor(coordinator))
    if any(key in SENSOR_TYPES for key in coordinator.available_sensors):
        entities.append(DataStaleBinarySensor(coordinator))
    async_add_entities(entities)


class FloodAlertBinarySensor(WaterlevelEntity, BinarySensorEntity):
    """On when the flood stage reaches the alert stage option (default watch).

    Unknown with no water level.
    """

    _attr_translation_key = "flood_alert"
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(self, coordinator: WaterlevelCoordinator) -> None:
        super().__init__(coordinator, "flood_alert")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.is_alert()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._location_attributes()


class DataStaleBinarySensor(WaterlevelEntity, BinarySensorEntity):
    """On when the station hasn't sent a reading for a while (likely offline).

    It stays available during an outage, which is exactly when it turns on.
    """

    _attr_translation_key = "data_stale"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: WaterlevelCoordinator) -> None:
        super().__init__(coordinator, "data_stale")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_stale()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last = self.coordinator.last_reading_time
        return {"last_reading": last.isoformat() if last else None}
