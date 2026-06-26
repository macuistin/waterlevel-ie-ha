"""Number platform for Waterlevel.ie — adjustable flood threshold entities."""
from __future__ import annotations
from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, STATION_PAGE_URL, THRESHOLD_ENTITIES
from .coordinator import WaterlevelCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: WaterlevelCoordinator = hass.data[DOMAIN][entry.entry_id]
    # Only create threshold entities if the station has a water level sensor
    if "0001" not in coordinator.available_sensors:
        return
    async_add_entities([
        WaterlevelThresholdNumber(coordinator, key, cfg)
        for key, cfg in THRESHOLD_ENTITIES.items()
    ])


def _make_device_info(coordinator: WaterlevelCoordinator) -> DeviceInfo:
    station_url = STATION_PAGE_URL.format(station_padded=coordinator.station.zfill(10))
    info = DeviceInfo(
        identifiers={(DOMAIN, coordinator.station)},
        name=coordinator.station_name,
        manufacturer="OPW Ireland",
        model=f"Gauge Station {coordinator.station}",
        configuration_url=station_url,
    )
    return info


class WaterlevelThresholdNumber(CoordinatorEntity, RestoreNumber):
    """A number entity for one flood threshold (watch / alert / serious).

    The value is stored in the coordinator so the flood stage sensor can read
    it immediately, and it is also persisted via HA state restore across restarts.
    """

    _attr_has_entity_name = True
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = "m"
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: WaterlevelCoordinator,
        threshold_key: str,
        cfg: dict,
    ) -> None:
        super().__init__(coordinator)
        self._threshold_key = threshold_key
        self._attr_name = cfg["name"]
        self._attr_icon = cfg["icon"]
        self._attr_native_min_value = cfg["min"]
        self._attr_native_max_value = cfg["max"]
        self._attr_unique_id = f"{DOMAIN}_{coordinator.station}_{threshold_key}_threshold"
        self._attr_device_info = _make_device_info(coordinator)

    async def async_added_to_hass(self) -> None:
        """Restore the previous threshold value when HA starts."""
        await super().async_added_to_hass()
        if (last_data := await self.async_get_last_number_data()) is not None:
            if last_data.native_value is not None:
                self.coordinator.set_threshold(self._threshold_key, float(last_data.native_value))

    @property
    def native_value(self) -> float | None:
        return self.coordinator.get_threshold(self._threshold_key)

    async def async_set_native_value(self, value: float) -> None:
        """Update the threshold and immediately recalculate flood stage."""
        self.coordinator.set_threshold(self._threshold_key, value)
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()
