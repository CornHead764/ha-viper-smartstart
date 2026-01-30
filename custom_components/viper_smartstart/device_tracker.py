"""Device tracker platform for Viper SmartStart."""

from __future__ import annotations

import logging

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ViperCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Viper SmartStart device trackers."""
    coordinator: ViperCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[ViperDeviceTracker] = []
    for vehicle_id in coordinator.get_vehicle_ids():
        entities.append(ViperDeviceTracker(coordinator, vehicle_id))

    async_add_entities(entities)


class ViperDeviceTracker(CoordinatorEntity[ViperCoordinator], TrackerEntity):
    """Representation of a Viper vehicle tracker."""

    _attr_has_entity_name = True
    _attr_name = "Location"

    def __init__(
        self,
        coordinator: ViperCoordinator,
        vehicle_id: str,
    ) -> None:
        """Initialize the device tracker."""
        super().__init__(coordinator)
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{vehicle_id}_location"
        self._attr_device_info = coordinator.get_device_info(vehicle_id)

    @property
    def source_type(self) -> SourceType:
        """Return the source type."""
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        """Return latitude value of the device."""
        if self.coordinator.data is None:
            return None
        status = self.coordinator.data.get(self._vehicle_id)
        if status is None:
            return None
        return status.latitude

    @property
    def longitude(self) -> float | None:
        """Return longitude value of the device."""
        if self.coordinator.data is None:
            return None
        status = self.coordinator.data.get(self._vehicle_id)
        if status is None:
            return None
        return status.longitude

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:car"
