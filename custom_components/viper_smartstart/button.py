"""Button platform for Viper SmartStart."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ViperApi
from .const import DOMAIN
from .coordinator import ViperCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class ViperButtonEntityDescription(ButtonEntityDescription):
    """Describes a Viper button entity."""

    press_fn: Callable[[ViperApi, str], Awaitable[bool]]


BUTTONS: tuple[ViperButtonEntityDescription, ...] = (
    ViperButtonEntityDescription(
        key="lock",
        translation_key="lock",
        name="Lock",
        icon="mdi:car-door-lock",
        press_fn=lambda api, device_id: api.lock(device_id),
    ),
    ViperButtonEntityDescription(
        key="unlock",
        translation_key="unlock",
        name="Unlock",
        icon="mdi:car-door-lock-open",
        press_fn=lambda api, device_id: api.unlock(device_id),
    ),
)


@dataclass(frozen=True, kw_only=True)
class ViperRefreshButtonEntityDescription(ButtonEntityDescription):
    """Describes a Viper refresh button entity."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Viper SmartStart buttons."""
    coordinator: ViperCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[ButtonEntity] = []
    for vehicle_id in coordinator.get_vehicle_ids():
        for description in BUTTONS:
            entities.append(ViperButton(coordinator, vehicle_id, description))
        entities.append(ViperRefreshButton(coordinator, vehicle_id))

    async_add_entities(entities)


class ViperButton(CoordinatorEntity[ViperCoordinator], ButtonEntity):
    """Representation of a Viper button."""

    entity_description: ViperButtonEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ViperCoordinator,
        vehicle_id: str,
        description: ViperButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{vehicle_id}_{description.key}"
        self._attr_device_info = coordinator.get_device_info(vehicle_id)

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.debug(
            "Pressing %s button for vehicle %s",
            self.entity_description.key,
            self._vehicle_id,
        )
        success = await self.entity_description.press_fn(
            self.coordinator.api, self._vehicle_id
        )
        if success:
            # Refresh data after command with delay
            self.hass.async_create_task(
                self.coordinator.async_refresh_after_action()
            )
        else:
            _LOGGER.warning(
                "Command %s failed for vehicle %s",
                self.entity_description.key,
                self._vehicle_id,
            )


class ViperRefreshButton(CoordinatorEntity[ViperCoordinator], ButtonEntity):
    """Button to manually refresh vehicle status."""

    _attr_has_entity_name = True
    _attr_name = "Refresh Status"
    _attr_icon = "mdi:refresh"

    def __init__(
        self,
        coordinator: ViperCoordinator,
        vehicle_id: str,
    ) -> None:
        """Initialize the refresh button."""
        super().__init__(coordinator)
        self._vehicle_id = vehicle_id
        self._attr_unique_id = f"{vehicle_id}_refresh"
        self._attr_device_info = coordinator.get_device_info(vehicle_id)

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.debug("Manual refresh requested for vehicle %s", self._vehicle_id)
        await self.coordinator.async_request_refresh()
