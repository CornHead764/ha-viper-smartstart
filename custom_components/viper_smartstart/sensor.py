"""Sensor platform for Viper SmartStart."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import VehicleStatus
from .const import DOMAIN
from .coordinator import ViperCoordinator


@dataclass(frozen=True, kw_only=True)
class ViperSensorEntityDescription(SensorEntityDescription):
    """Describes a Viper sensor entity."""

    value_fn: Callable[[VehicleStatus], Any]


SENSORS: tuple[ViperSensorEntityDescription, ...] = (
    ViperSensorEntityDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        name="Battery Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        value_fn=lambda status: status.battery_voltage,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Viper SmartStart sensors."""
    coordinator: ViperCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []
    for vehicle_id in coordinator.get_vehicle_ids():
        for description in SENSORS:
            entities.append(ViperSensor(coordinator, vehicle_id, description))
        entities.append(ViperLastUpdatedSensor(coordinator, vehicle_id))

    async_add_entities(entities)


class ViperSensor(CoordinatorEntity[ViperCoordinator], SensorEntity):
    """Representation of a Viper sensor."""

    entity_description: ViperSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ViperCoordinator,
        vehicle_id: str,
        description: ViperSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{vehicle_id}_{description.key}"
        self._attr_device_info = coordinator.get_device_info(vehicle_id)

    @property
    def native_value(self) -> Any:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        status = self.coordinator.data.get(self._vehicle_id)
        if status is None:
            return None
        return self.entity_description.value_fn(status)


class ViperLastUpdatedSensor(CoordinatorEntity[ViperCoordinator], SensorEntity):
    """Sensor showing when vehicle status was last updated."""

    _attr_has_entity_name = True
    _attr_name = "Last Updated"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"

    def __init__(
        self,
        coordinator: ViperCoordinator,
        vehicle_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{vehicle_id}_last_updated"
        self._attr_device_info = coordinator.get_device_info(vehicle_id)

    @property
    def native_value(self) -> datetime | None:
        """Return the last update timestamp."""
        return self.coordinator.last_updated
