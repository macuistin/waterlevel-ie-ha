"""Base entity for Water Level IE."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, STATION_PAGE_URL
from .coordinator import WaterlevelCoordinator


class WaterlevelEntity(CoordinatorEntity[WaterlevelCoordinator]):
    """An entity on one gauge station's device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WaterlevelCoordinator, unique_suffix: str) -> None:
        super().__init__(coordinator)
        station = coordinator.station
        # Unique ids are f"{DOMAIN}_{station}_{suffix}" and must never change once released
        self._attr_unique_id = f"{DOMAIN}_{station}_{unique_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, station)},
            name=coordinator.station_name,
            manufacturer="OPW Ireland",
            model=f"Gauge Station {station}",
            configuration_url=STATION_PAGE_URL.format(station_padded=station.zfill(10)),
        )

    def _location_attributes(self) -> dict[str, Any]:
        """Return the station's coordinates, so it shows on map cards."""
        if self.coordinator.latitude is None:
            return {}
        return {"latitude": self.coordinator.latitude, "longitude": self.coordinator.longitude}
