"""Sensor platform for Waterlevel.ie."""
from __future__ import annotations
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, STATION_PAGE_URL, ALL_SENSOR_TYPES, FLOOD_STAGE_ICONS
from .coordinator import WaterlevelCoordinator

_DC_MAP = {"temperature": SensorDeviceClass.TEMPERATURE, "voltage": SensorDeviceClass.VOLTAGE}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: WaterlevelCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [WaterlevelSensor(coordinator, k) for k in coordinator.available_sensors]
    # Flood stage sensor is always created if water level is available
    if "0001" in coordinator.available_sensors:
        entities.append(FloodStageSensor(coordinator))
    async_add_entities(entities)


class WaterlevelSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WaterlevelCoordinator, sensor_key: str) -> None:
        super().__init__(coordinator)
        cfg = ALL_SENSOR_TYPES[sensor_key]
        self._sensor_key = sensor_key
        self._attr_unique_id = f"{DOMAIN}_{coordinator.station}_{sensor_key}"
        self._attr_name = cfg["name"]
        self._attr_icon = cfg["icon"]
        self._attr_native_unit_of_measurement = cfg["unit"]
        dc = cfg.get("device_class")
        if dc:
            self._attr_device_class = _DC_MAP.get(dc)

        # Link device to the specific station page on waterlevel.ie
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
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._sensor_key)

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra attributes stored by the coordinator for this sensor."""
        if self.coordinator.data is None:
            return {}
        attrs = dict(self.coordinator.sensor_attrs.get(self._sensor_key, {}))
        if self.coordinator.latitude is not None:
            attrs["latitude"] = self.coordinator.latitude
            attrs["longitude"] = self.coordinator.longitude
        return attrs


class FloodStageSensor(CoordinatorEntity, SensorEntity):
    """Sensor reporting the current flood stage: normal / watch / alert / serious."""

    _attr_has_entity_name = True
    _attr_name = "Flood Stage"

    def __init__(self, coordinator: WaterlevelCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.station}_flood_stage"
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
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("flood_stage", "normal")

    @property
    def icon(self) -> str:
        stage = (self.coordinator.data or {}).get("flood_stage", "normal")
        return FLOOD_STAGE_ICONS.get(stage, "mdi:waves")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {}
        for key, label in (
            ("watch", "watch_level_m"),
            ("alert", "alert_level_m"),
            ("serious", "serious_level_m"),
        ):
            if (val := self.coordinator.get_threshold(key)) is not None:
                attrs[label] = val
        if self.coordinator.latitude is not None:
            attrs["latitude"] = self.coordinator.latitude
            attrs["longitude"] = self.coordinator.longitude
        return attrs
