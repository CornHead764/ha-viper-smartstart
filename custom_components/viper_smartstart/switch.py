"""Switch platform for Viper SmartStart."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
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
    """Set up Viper SmartStart switches."""
    coordinator: ViperCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[ViperRemoteStartSwitch] = []
    for vehicle_id in coordinator.get_vehicle_ids():
        entities.append(ViperRemoteStartSwitch(coordinator, vehicle_id))

    async_add_entities(entities)


class ViperRemoteStartSwitch(CoordinatorEntity[ViperCoordinator], SwitchEntity):
    """Representation of the remote start switch."""

    _attr_has_entity_name = True
    _attr_name = "Remote Start"
    _attr_icon = "mdi:car-key"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        coordinator: ViperCoordinator,
        vehicle_id: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{vehicle_id}_remote_start"
        self._attr_device_info = coordinator.get_device_info(vehicle_id)

    @property
    def is_on(self) -> bool | None:
        """Return true if remote starter is active."""
        if self.coordinator.data is None:
            return None
        status = self.coordinator.data.get(self._vehicle_id)
        if status is None:
            return None
        return status.remote_starter_active

    @property
    def available(self) -> bool:
        """Return if the switch is available."""
        if not super().available:
            return False
        # Check if we have status data
        if self.coordinator.data is None:
            return False
        status = self.coordinator.data.get(self._vehicle_id)
        if status is None:
            return False
        return True

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on remote start."""
        status = self.coordinator.data.get(self._vehicle_id) if self.coordinator.data else None

        if status:
            # Don't start if already running (either remote or ignition)
            if status.remote_starter_active:
                _LOGGER.debug("Remote starter already active for %s", self._vehicle_id)
                return
            if status.ignition_on:
                _LOGGER.warning(
                    "Cannot remote start %s - ignition is already on",
                    self._vehicle_id,
                )
                return

        _LOGGER.debug("Sending remote start command to %s", self._vehicle_id)
        success = await self.coordinator.api.remote_start(self._vehicle_id)
        if success:
            # Enable boosted polling to monitor remote start status
            self.coordinator.start_boosted_polling()
            # Refresh data after command with delay
            self.hass.async_create_task(
                self.coordinator.async_refresh_after_action()
            )
        else:
            _LOGGER.warning("Remote start command failed for %s", self._vehicle_id)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off remote start (stop the engine)."""
        status = self.coordinator.data.get(self._vehicle_id) if self.coordinator.data else None

        if status:
            # Can only stop if remote started (not if ignition is on from key)
            if not status.remote_starter_active:
                _LOGGER.debug("Remote starter not active for %s", self._vehicle_id)
                return
            if status.ignition_on:
                _LOGGER.warning(
                    "Cannot stop %s - ignition is on (key in vehicle)",
                    self._vehicle_id,
                )
                return

        _LOGGER.debug("Sending remote stop command to %s", self._vehicle_id)
        # The 'remote' command toggles - sends same command to stop
        success = await self.coordinator.api.remote_start(self._vehicle_id)
        if success:
            # Refresh data after command with delay
            self.hass.async_create_task(
                self.coordinator.async_refresh_after_action()
            )
        else:
            _LOGGER.warning("Remote stop command failed for %s", self._vehicle_id)
