"""Sensor platform for Water Level IE."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import FLOOD_STAGES, SENSOR_TYPES, THRESHOLD_ENTITIES, TREND_RATE, WATER_LEVEL
from .coordinator import WaterlevelConfigEntry, WaterlevelCoordinator
from .entity import WaterlevelEntity

# Entities only read the coordinator's data, so updates need no limit
PARALLEL_UPDATES = 0


# m³/s is a volume flow rate unit only in newer Home Assistant releases; on older ones
# Discharge has no device class, so the unit isn't rejected
CUBIC_METERS_PER_SECOND = "m³/s"
_FLOW_DEVICE_CLASS = (
    SensorDeviceClass.VOLUME_FLOW_RATE
    if CUBIC_METERS_PER_SECOND in {unit.value for unit in UnitOfVolumeFlowRate}
    else None
)


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
        WaterlevelSensorEntityDescription(
            key="discharge",
            translation_key="discharge",
            native_unit_of_measurement=CUBIC_METERS_PER_SECOND,
            device_class=_FLOW_DEVICE_CLASS,
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
        ),
    )
}

TREND_RATE_DESCRIPTION = WaterlevelSensorEntityDescription(
    key=TREND_RATE,
    translation_key="water_level_trend",
    native_unit_of_measurement="m/h",
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=3,
)

FLOOD_STAGE_DESCRIPTION = SensorEntityDescription(
    key="flood_stage",
    translation_key="flood_stage",
    device_class=SensorDeviceClass.ENUM,
    options=FLOOD_STAGES,
)

LAST_READING_DESCRIPTION = SensorEntityDescription(
    key="last_reading",
    translation_key="last_reading",
    device_class=SensorDeviceClass.TIMESTAMP,
)

DATA_AGE_DESCRIPTION = SensorEntityDescription(
    key="data_age",
    translation_key="data_age",
    device_class=SensorDeviceClass.DURATION,
    native_unit_of_measurement=UnitOfTime.MINUTES,
    state_class=SensorStateClass.MEASUREMENT,
    entity_category=EntityCategory.DIAGNOSTIC,
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
        entities += [
            FloodStageSensor(coordinator),
            WaterlevelSensor(coordinator, TREND_RATE_DESCRIPTION),
        ]
    if any(key in SENSOR_TYPES for key in coordinator.available_sensors):
        entities += [LastReadingSensor(coordinator), DataAgeSensor(coordinator)]
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
        suggested = self.coordinator.suggested or {}
        if "typical" in suggested:
            attrs["typical_level_m"] = suggested["typical"]
            attrs["record_daily_mean_m"] = suggested["record"]
        attrs.update(self._location_attributes())
        return attrs


class _FreshnessSensor(WaterlevelEntity, SensorEntity):
    """Reports on the station's last reading; usable during an outage, when it matters most."""

    def __init__(self, coordinator: WaterlevelCoordinator, description: SensorEntityDescription):
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return self.coordinator.last_reading_time is not None


class LastReadingSensor(_FreshnessSensor):
    """When the station last sent a reading."""

    def __init__(self, coordinator: WaterlevelCoordinator) -> None:
        super().__init__(coordinator, LAST_READING_DESCRIPTION)

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_reading_time


class DataAgeSensor(_FreshnessSensor):
    """Minutes since the station last sent a reading, as of the last update."""

    def __init__(self, coordinator: WaterlevelCoordinator) -> None:
        super().__init__(coordinator, DATA_AGE_DESCRIPTION)

    @property
    def native_value(self) -> int | None:
        if (last := self.coordinator.last_reading_time) is None:
            return None
        return int((dt_util.utcnow() - last).total_seconds() // 60)
