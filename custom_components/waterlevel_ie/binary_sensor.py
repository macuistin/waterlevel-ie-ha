"""Binary sensor platform for Waterlevel.ie — flood alert on/off."""
from __future__ import annotations
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, STATION_PAGE_URL
from .coordinator import WaterlevelCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: WaterlevelCoordinator = hass.data[DOMAIN][entry.entry_id]
    if "0001" in coordinator.available_sensors:
        async_add_entities([FloodAlertBinarySensor(coordinator)])


class FloodAlertBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor: on when flood stage is watch / alert / serious."""

    _attr_has_entity_name = True
    _attr_name = "Flood Alert"
    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(self, coordinator: WaterlevelCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.station}_flood_alert"
        station_url = STATION_PAGE_URL.format(station_padded=coordinator.station.zfill(10))
        device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.station)},
            name=coordinator.station_name,
            manufacturer="OPW Ireland",
            model=f"Gauge Station {coordinator.station}",
            configuration_url=station_url,
        )
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool:
        if self.coordinator.data is None:
            return False
        stage = self.coordinator.data.get("flood_stage", "normal")
        return stage != "normal"

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {}
        if self.coordinator.latitude is not None:
            attrs["latitude"] = self.coordinator.latitude
            attrs["longitude"] = self.coordinator.longitude
        return attrs
