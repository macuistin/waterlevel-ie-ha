"""Base entity for Water Level Gauges."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import StationCoordinator


class WaterlevelEntity(CoordinatorEntity[StationCoordinator]):
    """An entity on one gauge station's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: StationCoordinator, unique_suffix: str) -> None:
        super().__init__(coordinator)
        station = coordinator.device_key
        # Unique ids are f"{DOMAIN}_{station}_{suffix}" and must never change once released
        self._attr_unique_id = f"{DOMAIN}_{station}_{unique_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, station)},
            name=coordinator.station_name,
            manufacturer=coordinator.manufacturer,
            model=coordinator.device_model,
            configuration_url=coordinator.configuration_url,
        )

    def _location_attributes(self) -> dict[str, Any]:
        """Return the station's coordinates, so it shows on map cards."""
        if self.coordinator.latitude is None:
            return {}
        return {"latitude": self.coordinator.latitude, "longitude": self.coordinator.longitude}
