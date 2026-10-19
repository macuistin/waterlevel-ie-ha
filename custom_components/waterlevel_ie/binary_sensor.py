"""Binary sensor platform for Water Level IE: flood alert on/off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import WATER_LEVEL
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
    if WATER_LEVEL in coordinator.available_sensors:
        async_add_entities([FloodAlertBinarySensor(coordinator)])


class FloodAlertBinarySensor(WaterlevelEntity, BinarySensorEntity):
    """On when the flood stage is watch, alert or serious; unknown with no level."""

    _attr_translation_key = "flood_alert"
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(self, coordinator: WaterlevelCoordinator) -> None:
        super().__init__(coordinator, "flood_alert")

    @property
    def is_on(self) -> bool | None:
        stage = self.coordinator.flood_stage()
        return None if stage is None else stage != "normal"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._location_attributes()
