"""API client for Viper SmartStart."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import (
    API_COMMAND_URL,
    API_DEVICES_URL,
    API_LOGIN_URL,
    CMD_ARM,
    CMD_DISARM,
    CMD_READ_ACTIVE,
    CMD_READ_CURRENT,
    CMD_REMOTE,
)

_LOGGER = logging.getLogger(__name__)


class ViperAuthError(Exception):
    """Authentication error."""


class ViperApiError(Exception):
    """API error."""


@dataclass
class VehicleStatus:
    """Vehicle status data."""

    latitude: float | None = None
    longitude: float | None = None
    speed: str | None = None
    heading: int | None = None
    battery_voltage: float | None = None
    doors_locked: bool | None = None
    doors_open: bool | None = None
    remote_starter_active: bool | None = None
    ignition_on: bool | None = None
    trunk_open: bool | None = None
    hood_open: bool | None = None
    security_system_armed: bool | None = None
    panic_on: bool | None = None
    valet_on: bool | None = None


@dataclass
class Vehicle:
    """Vehicle data."""

    id: str
    name: str
    make: str | None = None
    model: str | None = None
    year: str | None = None
    status: VehicleStatus | None = None


class ViperApi:
    """Viper SmartStart API client."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the API client."""
        self._username = username
        self._password = password
        self._session = session
        self._access_token: str | None = None
        self._token_expiration: int | None = None
        self._own_session = False

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def close(self) -> None:
        """Close the session if we own it."""
        if self._own_session and self._session:
            await self._session.close()
            self._session = None

    @property
    def is_authenticated(self) -> bool:
        """Check if the client has a valid access token."""
        return self._access_token is not None

    async def authenticate(self) -> bool:
        """Authenticate with the API."""
        session = await self._get_session()

        _LOGGER.debug("Attempting authentication for user: %s", self._username)

        try:
            async with session.post(
                API_LOGIN_URL,
                data={"username": self._username, "password": self._password},
            ) as response:
                _LOGGER.debug("Auth response status: %s", response.status)

                if response.status != 200:
                    response_text = await response.text()
                    _LOGGER.debug("Auth error response: %s", response_text)
                    raise ViperAuthError(f"Authentication failed: {response.status}")

                data = await response.json()
                _LOGGER.debug("Auth response keys: %s", list(data.keys()))

                if "results" not in data or "authToken" not in data["results"]:
                    _LOGGER.debug("Invalid auth response structure: %s", data)
                    raise ViperAuthError("Invalid authentication response")

                self._access_token = data["results"]["authToken"]["accessToken"]
                self._token_expiration = data["results"]["authToken"]["expiration"]
                _LOGGER.debug("Authentication successful")
                return True

        except aiohttp.ContentTypeError as err:
            _LOGGER.debug("Invalid content type during auth: %s", err)
            raise ViperApiError(f"Invalid response format: {err}") from err
        except aiohttp.ClientError as err:
            _LOGGER.debug("Connection error during auth: %s", err)
            raise ViperApiError(f"Connection error: {err}") from err

    def _get_headers(self) -> dict[str, str]:
        """Get authorization headers."""
        if not self._access_token:
            raise ViperAuthError("Not authenticated")
        return {"Authorization": f"Bearer {self._access_token}"}

    async def get_vehicles(self) -> list[Vehicle]:
        """Get list of vehicles."""
        session = await self._get_session()

        try:
            async with session.get(
                API_DEVICES_URL,
                headers=self._get_headers(),
            ) as response:
                if response.status == 401:
                    raise ViperAuthError("Token expired")
                if response.status != 200:
                    raise ViperApiError(f"API error: {response.status}")

                data = await response.json()
                devices = data.get("results", {}).get("devices", [])

                vehicles = []
                for device in devices:
                    device_id = device.get("id")
                    vehicle = Vehicle(
                        id=str(device_id),
                        name=device.get("name", f"Vehicle {device_id}"),
                        make=device.get("make"),
                        model=device.get("model"),
                        year=device.get("year"),
                    )
                    vehicles.append(vehicle)

                return vehicles

        except aiohttp.ClientError as err:
            raise ViperApiError(f"Connection error: {err}") from err

    async def _send_command(self, device_id: str, command: str) -> dict[str, Any]:
        """Send a command to a vehicle."""
        session = await self._get_session()

        try:
            async with session.post(
                API_COMMAND_URL,
                headers=self._get_headers(),
                json={"command": command, "deviceId": device_id},
            ) as response:
                if response.status == 401:
                    raise ViperAuthError("Token expired")
                if response.status != 200:
                    raise ViperApiError(f"Command failed: {response.status}")

                return await response.json()

        except aiohttp.ClientError as err:
            raise ViperApiError(f"Connection error: {err}") from err

    async def get_vehicle_status(self, device_id: str) -> VehicleStatus:
        """Get vehicle status by combining active and current status."""
        # Fetch both status types concurrently
        active_task = self._send_command(device_id, CMD_READ_ACTIVE)
        current_task = self._send_command(device_id, CMD_READ_CURRENT)

        active_result, current_result = await asyncio.gather(
            active_task, current_task, return_exceptions=True
        )

        _LOGGER.debug("Active result for %s: %s", device_id, active_result)
        _LOGGER.debug("Current result for %s: %s", device_id, current_result)

        status = VehicleStatus()

        # Process active status (GPS, door state, ignition, etc.)
        if not isinstance(active_result, Exception) and active_result is not None:
            active_data = active_result.get("results", {}).get("device", {})
            active_status = active_data.get("deviceStatus", {})

            # Parse latitude/longitude
            lat = active_data.get("latitude")
            lon = active_data.get("longitude")
            if lat is not None:
                try:
                    status.latitude = float(lat)
                except (ValueError, TypeError):
                    pass
            if lon is not None:
                try:
                    status.longitude = float(lon)
                except (ValueError, TypeError):
                    pass

            status.speed = active_data.get("speed")
            status.heading = active_data.get("heading")
            status.battery_voltage = active_data.get("batteryVoltage")

            # Door/vehicle states from active status
            if active_status.get("doorsOpen") is not None:
                status.doors_open = bool(active_status.get("doorsOpen"))
            if active_status.get("ignitionOn") is not None:
                status.ignition_on = bool(active_status.get("ignitionOn"))
            if active_status.get("trunkOpen") is not None:
                status.trunk_open = bool(active_status.get("trunkOpen"))
            if active_status.get("hoodOpen") is not None:
                status.hood_open = bool(active_status.get("hoodOpen"))
        elif active_result is None:
            _LOGGER.warning("Active status returned None for device %s", device_id)
        else:
            _LOGGER.warning("Failed to get active status: %s", active_result)

        # Process current status (remote starter, security system, etc.)
        if not isinstance(current_result, Exception) and current_result is not None:
            current_data = current_result.get("results", {}).get("device", {})
            current_status = current_data.get("deviceStatus", {})

            if current_status.get("doorsLocked") is not None:
                status.doors_locked = bool(current_status.get("doorsLocked"))
            if current_status.get("remoteStarterActive") is not None:
                status.remote_starter_active = bool(
                    current_status.get("remoteStarterActive")
                )
            if current_status.get("securitySystemArmed") is not None:
                status.security_system_armed = bool(
                    current_status.get("securitySystemArmed")
                )
            if current_status.get("panicOn") is not None:
                status.panic_on = bool(current_status.get("panicOn"))
            if current_status.get("valetOn") is not None:
                status.valet_on = bool(current_status.get("valetOn"))
        elif current_result is None:
            _LOGGER.warning("Current status returned None for device %s", device_id)
        else:
            _LOGGER.warning("Failed to get current status: %s", current_result)

        return status

    async def remote_start(self, device_id: str) -> bool:
        """Send remote start command."""
        result = await self._send_command(device_id, CMD_REMOTE)
        return "results" in result

    async def lock(self, device_id: str) -> bool:
        """Send lock (arm) command."""
        result = await self._send_command(device_id, CMD_ARM)
        return "results" in result

    async def unlock(self, device_id: str) -> bool:
        """Send unlock (disarm) command."""
        result = await self._send_command(device_id, CMD_DISARM)
        return "results" in result
