"""Number platform for Water Level IE: adjustable flood thresholds."""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import THRESHOLD_ENTITIES, WATER_LEVEL
from .coordinator import WaterlevelConfigEntry, WaterlevelCoordinator
from .entity import WaterlevelEntity

# Setting a threshold makes no request, so there is nothing to limit
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WaterlevelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    # Only create threshold entities if the station has a water level sensor
    if WATER_LEVEL not in coordinator.available_sensors:
        return
    async_add_entities(
        WaterlevelThresholdNumber(coordinator, key, cfg) for key, cfg in THRESHOLD_ENTITIES.items()
    )


class WaterlevelThresholdNumber(WaterlevelEntity, RestoreNumber):
    """A number entity for one flood threshold (watch / alert / serious).

    The value is stored in the coordinator so the flood stage sensor can read
    it immediately, and it is also persisted via HA state restore across restarts.
    """

    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: WaterlevelCoordinator,
        threshold_key: str,
        cfg: dict[str, float],
    ) -> None:
        super().__init__(coordinator, f"{threshold_key}_threshold")
        self._threshold_key = threshold_key
        self._attr_translation_key = f"{threshold_key}_level"
        self._attr_native_min_value = cfg["min"]
        self._attr_native_max_value = cfg["max"]

    @property
    def available(self) -> bool:
        """A threshold is a local setting, so it stays usable while the station is down."""
        return True

    async def async_added_to_hass(self) -> None:
        """Restore the previous threshold value when HA starts."""
        await super().async_added_to_hass()
        last_data = await self.async_get_last_number_data()
        if last_data is not None and last_data.native_value is not None:
            self.coordinator.set_threshold(self._threshold_key, float(last_data.native_value))

    @property
    def native_value(self) -> float | None:
        return self.coordinator.get_threshold(self._threshold_key)

    async def async_set_native_value(self, value: float) -> None:
        """Update the threshold; the flood entities recalculate from the held reading."""
        self.coordinator.set_threshold(self._threshold_key, value)
