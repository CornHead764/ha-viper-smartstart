"""DataUpdateCoordinator for Viper SmartStart."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import Vehicle, VehicleStatus, ViperApi, ViperApiError, ViperAuthError
from .const import CONF_REFRESH_INTERVAL, CONF_VEHICLES, DEFAULT_REFRESH_INTERVAL, DOMAIN

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceInfo

_LOGGER = logging.getLogger(__name__)

# Boosted polling settings when remote start is active
BOOSTED_INTERVAL = timedelta(seconds=60)
BOOSTED_MAX_DURATION = timedelta(minutes=30)

# Delay before refreshing after an action
ACTION_REFRESH_DELAY = 10


class ViperCoordinator(DataUpdateCoordinator[dict[str, VehicleStatus]]):
    """Coordinator to manage fetching Viper data."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        api: ViperApi,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.api = api
        self._vehicle_ids: list[str] = config_entry.data.get(CONF_VEHICLES, [])
        self._vehicles: dict[str, Vehicle] = {}

        refresh_interval = config_entry.data.get(
            CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL
        )
        # 0 means disabled - set to None for no automatic polling
        self._normal_interval = timedelta(seconds=refresh_interval) if refresh_interval > 0 else None
        self._boosted_until: datetime | None = None
        self._last_updated: datetime | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=self._normal_interval,
        )

    async def _async_setup(self) -> None:
        """Set up the coordinator - fetch vehicle info."""
        try:
            vehicles = await self.api.get_vehicles()
            self._vehicles = {v.id: v for v in vehicles if v.id in self._vehicle_ids}
        except ViperAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ViperApiError as err:
            raise UpdateFailed(f"Error fetching vehicles: {err}") from err

    def start_boosted_polling(self) -> None:
        """Start boosted polling interval for remote start monitoring."""
        self._boosted_until = dt_util.utcnow() + BOOSTED_MAX_DURATION
        self.update_interval = BOOSTED_INTERVAL
        _LOGGER.debug(
            "Boosted polling enabled until %s (interval: %s)",
            self._boosted_until,
            BOOSTED_INTERVAL,
        )

    def _check_and_reset_boosted_polling(self, data: dict[str, VehicleStatus]) -> None:
        """Check if boosted polling should be reset to normal."""
        if self._boosted_until is None:
            return

        now = dt_util.utcnow()

        # Check if max duration exceeded
        if now >= self._boosted_until:
            _LOGGER.debug("Boosted polling max duration reached, resetting to normal")
            self._reset_to_normal_polling()
            return

        # Check if any vehicle still has remote starter active
        any_remote_active = any(
            status.remote_starter_active
            for status in data.values()
            if status.remote_starter_active is not None
        )

        if not any_remote_active:
            _LOGGER.debug("No vehicles have remote start active, resetting to normal polling")
            self._reset_to_normal_polling()

    def _reset_to_normal_polling(self) -> None:
        """Reset polling interval to normal (may be None if disabled)."""
        self._boosted_until = None
        self.update_interval = self._normal_interval
        if self._normal_interval is None:
            _LOGGER.debug("Polling interval reset to disabled (manual refresh only)")
        else:
            _LOGGER.debug("Polling interval reset to %s", self._normal_interval)

    @property
    def is_boosted(self) -> bool:
        """Return True if boosted polling is active."""
        return self._boosted_until is not None

    @property
    def last_updated(self) -> datetime | None:
        """Return the last update timestamp."""
        return self._last_updated

    async def _async_update_data(self) -> dict[str, VehicleStatus]:
        """Fetch data from API."""
        try:
            # Ensure we're authenticated
            if not self.api.is_authenticated:
                await self.api.authenticate()

            data: dict[str, VehicleStatus] = {}
            errors: list[str] = []

            for vehicle_id in self._vehicle_ids:
                try:
                    status = await self.api.get_vehicle_status(vehicle_id)
                    data[vehicle_id] = status
                except ViperAuthError:
                    # Re-authenticate and retry once
                    try:
                        await self.api.authenticate()
                        status = await self.api.get_vehicle_status(vehicle_id)
                        data[vehicle_id] = status
                    except (ViperAuthError, ViperApiError) as err:
                        errors.append(f"Vehicle {vehicle_id}: {err}")
                        # Preserve previous data if available
                        if self.data and vehicle_id in self.data:
                            data[vehicle_id] = self.data[vehicle_id]
                            _LOGGER.warning(
                                "Failed to update vehicle %s, keeping previous data: %s",
                                vehicle_id,
                                err,
                            )
                except ViperApiError as err:
                    errors.append(f"Vehicle {vehicle_id}: {err}")
                    # Preserve previous data if available
                    if self.data and vehicle_id in self.data:
                        data[vehicle_id] = self.data[vehicle_id]
                        _LOGGER.warning(
                            "Failed to update vehicle %s, keeping previous data: %s",
                            vehicle_id,
                            err,
                        )

            # If we got no data at all and had no previous data, this is a real failure
            if not data:
                if errors:
                    raise UpdateFailed(f"Error communicating with API: {'; '.join(errors)}")
                raise UpdateFailed("No data received from API")

            # Check if we should reset boosted polling
            self._check_and_reset_boosted_polling(data)

            # Update last refresh timestamp
            self._last_updated = dt_util.now()

            # Log if there were partial errors but we still have data
            if errors:
                _LOGGER.warning("Partial update failure: %s", "; ".join(errors))

            return data

        except ViperAuthError as err:
            # Authentication failed completely - this requires user action
            raise ConfigEntryAuthFailed from err
        except ViperApiError as err:
            # If we have previous data, preserve it instead of failing
            if self.data:
                _LOGGER.warning(
                    "API error during update, keeping previous data: %s", err
                )
                return self.data
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    def get_vehicle(self, vehicle_id: str) -> Vehicle | None:
        """Get vehicle info."""
        return self._vehicles.get(vehicle_id)

    def get_vehicle_ids(self) -> list[str]:
        """Get list of vehicle IDs."""
        return self._vehicle_ids

    def get_device_info(self, vehicle_id: str) -> DeviceInfo:
        """Get device info for a vehicle."""
        vehicle = self._vehicles.get(vehicle_id)
        name = vehicle.name if vehicle else f"Vehicle {vehicle_id}"

        # Build model string from available info
        model_parts = []
        if vehicle:
            if vehicle.year:
                model_parts.append(vehicle.year)
            if vehicle.make:
                model_parts.append(vehicle.make)
            if vehicle.model:
                model_parts.append(vehicle.model)

        model = " ".join(model_parts) if model_parts else None

        return {
            "identifiers": {(DOMAIN, vehicle_id)},
            "name": name,
            "manufacturer": "Viper SmartStart",
            "model": model,
        }

    async def async_refresh_after_action(self) -> None:
        """Schedule a refresh after an action with a delay."""
        _LOGGER.debug(
            "Scheduling status refresh in %s seconds", ACTION_REFRESH_DELAY
        )
        await asyncio.sleep(ACTION_REFRESH_DELAY)
        await self.async_request_refresh()
