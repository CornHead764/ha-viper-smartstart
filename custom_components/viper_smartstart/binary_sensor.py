"""Binary sensor platform for Viper SmartStart."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import VehicleStatus
from .const import DOMAIN
from .coordinator import ViperCoordinator


@dataclass(frozen=True, kw_only=True)
class ViperBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Viper binary sensor entity."""

    value_fn: Callable[[VehicleStatus], bool | None]


BINARY_SENSORS: tuple[ViperBinarySensorEntityDescription, ...] = (
    ViperBinarySensorEntityDescription(
        key="doors_open",
        translation_key="doors_open",
        name="Doors Open",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda status: status.doors_open,
    ),
    ViperBinarySensorEntityDescription(
        key="ignition_on",
        translation_key="ignition_on",
        name="Ignition",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda status: status.ignition_on,
    ),
    ViperBinarySensorEntityDescription(
        key="trunk_open",
        translation_key="trunk_open",
        name="Trunk Open",
        device_class=BinarySensorDeviceClass.OPENING,
        value_fn=lambda status: status.trunk_open,
    ),
    ViperBinarySensorEntityDescription(
        key="hood_open",
        translation_key="hood_open",
        name="Hood Open",
        device_class=BinarySensorDeviceClass.OPENING,
        value_fn=lambda status: status.hood_open,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Viper SmartStart binary sensors."""
    coordinator: ViperCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[ViperBinarySensor] = []
    for vehicle_id in coordinator.get_vehicle_ids():
        for description in BINARY_SENSORS:
            entities.append(
                ViperBinarySensor(coordinator, vehicle_id, description)
            )

    async_add_entities(entities)


class ViperBinarySensor(CoordinatorEntity[ViperCoordinator], BinarySensorEntity):
    """Representation of a Viper binary sensor."""

    entity_description: ViperBinarySensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ViperCoordinator,
        vehicle_id: str,
        description: ViperBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{vehicle_id}_{description.key}"
        self._attr_device_info = coordinator.get_device_info(vehicle_id)

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        if self.coordinator.data is None:
            return None
        status = self.coordinator.data.get(self._vehicle_id)
        if status is None:
            return None
        return self.entity_description.value_fn(status)
