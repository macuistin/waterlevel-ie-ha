"""Sensor platform for Water Level IE."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import FLOOD_STAGES, THRESHOLD_ENTITIES, WATER_LEVEL
from .coordinator import WaterlevelConfigEntry, WaterlevelCoordinator
from .entity import WaterlevelEntity

# Entities only read the coordinator's data, so updates need no limit
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class WaterlevelSensorEntityDescription(SensorEntityDescription):
    """Describes a sensor; key is the OPW sensor code or summary key it reads."""


def _level(key: str, translation_key: str) -> WaterlevelSensorEntityDescription:
    return WaterlevelSensorEntityDescription(
        key=key,
        translation_key=translation_key,
        native_unit_of_measurement=UnitOfLength.METERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
    )


SENSOR_DESCRIPTIONS: dict[str, WaterlevelSensorEntityDescription] = {
    description.key: description
    for description in (
        _level("0001", "water_level"),
        _level("0011", "backup_water_level"),
        _level("OD", "water_level_od"),
        WaterlevelSensorEntityDescription(
            key="0002",
            translation_key="temperature",
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT,
        ),
        WaterlevelSensorEntityDescription(
            key="0003",
            translation_key="battery_voltage",
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            device_class=SensorDeviceClass.VOLTAGE,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
        ),
        WaterlevelSensorEntityDescription(
            key="0014",
            translation_key="signal_strength",
            native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
            device_class=SensorDeviceClass.SIGNAL_STRENGTH,
            state_class=SensorStateClass.MEASUREMENT,
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
        ),
        _level("0001_daily_mean", "daily_mean_water_level"),
        _level("0001_daily_min", "daily_min_water_level"),
        _level("0001_daily_max", "daily_max_water_level"),
    )
}

FLOOD_STAGE_DESCRIPTION = SensorEntityDescription(
    key="flood_stage",
    translation_key="flood_stage",
    device_class=SensorDeviceClass.ENUM,
    options=FLOOD_STAGES,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WaterlevelConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        WaterlevelSensor(coordinator, SENSOR_DESCRIPTIONS[key])
        for key in coordinator.available_sensors
        if key in SENSOR_DESCRIPTIONS
    ]
    if WATER_LEVEL in coordinator.available_sensors:
        entities.append(FloodStageSensor(coordinator))
    async_add_entities(entities)


class WaterlevelSensor(WaterlevelEntity, SensorEntity):
    """A reading from the station's live or daily summary data."""

    entity_description: WaterlevelSensorEntityDescription

    def __init__(
        self,
        coordinator: WaterlevelCoordinator,
        description: WaterlevelSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the reading's time, week range and trend, and the station location."""
        attrs = dict(self.coordinator.sensor_attrs.get(self.entity_description.key, {}))
        attrs.update(self._location_attributes())
        return attrs


class FloodStageSensor(WaterlevelEntity, SensorEntity):
    """The current flood stage: normal, watch, alert or serious."""

    entity_description = FLOOD_STAGE_DESCRIPTION

    def __init__(self, coordinator: WaterlevelCoordinator) -> None:
        super().__init__(coordinator, "flood_stage")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.flood_stage()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            f"{key}_level_m": value
            for key in THRESHOLD_ENTITIES
            if (value := self.coordinator.get_threshold(key)) is not None
        }
        attrs.update(self._location_attributes())
        return attrs
