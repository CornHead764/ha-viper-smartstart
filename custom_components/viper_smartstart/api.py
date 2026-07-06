"""API client for Viper SmartStart."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

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

# Refresh the token this many seconds before its stated expiry so an in-flight
# request never races the expiration boundary.
TOKEN_EXPIRY_MARGIN = 60

# Values >= this are treated as epoch milliseconds; below it, epoch seconds.
_EPOCH_MS_THRESHOLD = 1e12


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
        self._token_expires_at: datetime | None = None
        self._own_session = False
        self._auth_lock = asyncio.Lock()

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
        """Whether the client holds a present, unexpired access token."""
        return self._is_token_valid()

    def _is_token_valid(self) -> bool:
        """Return True iff a token exists and has not (nearly) expired."""
        if not self._access_token:
            return False
        if self._token_expires_at is None:
            return True
        margin = timedelta(seconds=TOKEN_EXPIRY_MARGIN)
        return datetime.now(timezone.utc) < self._token_expires_at - margin

    def _clear_token(self) -> None:
        """Drop the current token so ``is_authenticated`` reflects reality."""
        self._access_token = None
        self._token_expires_at = None

    @staticmethod
    def _parse_expiration(value: Any) -> datetime | None:
        """Parse a login-response expiration into a UTC datetime, defensively.

        int/float below ``_EPOCH_MS_THRESHOLD`` are epoch seconds, at or above
        it epoch milliseconds. Strings are ISO 8601 (a trailing ``Z`` is
        accepted and a naive result is assumed UTC). Anything unparseable or
        missing returns ``None`` and never raises.
        """
        if value is None or isinstance(value, bool):
            return None
        try:
            if isinstance(value, (int, float)):
                seconds = value / 1000.0 if value >= _EPOCH_MS_THRESHOLD else float(value)
                return datetime.fromtimestamp(seconds, tz=timezone.utc)
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return None
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                parsed = datetime.fromisoformat(text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
        except (ValueError, OverflowError, OSError):
            _LOGGER.debug("Could not parse token expiration value")
            return None
        _LOGGER.debug("Unrecognized token expiration type: %s", type(value).__name__)
        return None

    async def _ensure_authenticated(self) -> None:
        """Proactively (re)authenticate if the token is missing or expired.

        Double-checked under a lock so concurrent callers trigger exactly one
        login POST rather than a stampede.
        """
        if self._is_token_valid():
            return
        async with self._auth_lock:
            if self._is_token_valid():
                return
            await self.authenticate()

    async def authenticate(self) -> bool:
        """Perform the login POST and store the resulting token.

        A 401/403 means bad credentials (raises ``ViperAuthError``); any other
        non-200 is a server-side fault (raises ``ViperApiError``) and must not
        trigger Home Assistant's reauth flow.
        """
        session = await self._get_session()

        _LOGGER.debug("Attempting authentication for user: %s", self._username)

        # Clear first so a failed login leaves ``is_authenticated`` False.
        self._clear_token()

        try:
            async with session.post(
                API_LOGIN_URL,
                data={"username": self._username, "password": self._password},
            ) as response:
                _LOGGER.debug("Auth response status: %s", response.status)

                if response.status in (401, 403):
                    raise ViperAuthError(f"Authentication failed: {response.status}")
                if response.status != 200:
                    raise ViperApiError(
                        f"Authentication server error: {response.status}"
                    )

                data = await response.json()
                _LOGGER.debug("Auth response keys: %s", list(data.keys()))

                auth_token = (data.get("results") or {}).get("authToken")
                access_token = (auth_token or {}).get("accessToken")
                if not access_token:
                    # A 200 with a body missing the token is a server anomaly,
                    # not a credential rejection (those are 401/403). Raise
                    # ViperApiError so this does not escalate to HA's reauth
                    # prompt.
                    _LOGGER.debug(
                        "Invalid auth response structure; top-level keys: %s",
                        list(data.keys()),
                    )
                    raise ViperApiError("Invalid authentication response")

                self._access_token = access_token
                self._token_expires_at = self._parse_expiration(
                    auth_token.get("expiration")
                )
                _LOGGER.debug("Authentication successful")
                return True

        except aiohttp.ContentTypeError as err:
            _LOGGER.debug("Invalid content type during auth: %s", err)
            raise ViperApiError(f"Invalid response format: {err}") from err
        except aiohttp.ClientError as err:
            _LOGGER.debug("Connection error during auth: %s", err)
            raise ViperApiError(f"Connection error: {err}") from err

    async def _authenticated_request(
        self,
        request_factory: Callable[[aiohttp.ClientSession, dict[str, str]], Any],
        *,
        error_label: str,
    ) -> dict[str, Any]:
        """Issue an authenticated request, refreshing the token as needed.

        Proactively ensures a valid token, then on an HTTP 401 clears the
        token, re-logs-in once, and retries the request once. Only a second
        401 surfaces as ``ViperAuthError`` — routine expiries are invisible to
        callers.
        """
        await self._ensure_authenticated()
        session = await self._get_session()

        try:
            # Snapshot the token we are about to use so that, on a 401, we can
            # tell whether a concurrent caller has already refreshed it.
            token_snapshot = self._access_token
            status, data = await self._issue(session, request_factory)
            if status == 401:
                # Serialize the reactive re-login: only clear and re-authenticate
                # if the token is still the one we used (or was cleared). If a
                # concurrent 401 handler already obtained a fresh token, reuse it
                # rather than clobbering it and firing a redundant login.
                async with self._auth_lock:
                    if self._access_token is None or self._access_token == token_snapshot:
                        await self.authenticate()
                status, data = await self._issue(session, request_factory)
                if status == 401:
                    raise ViperAuthError("Authentication rejected after re-login")
            if status != 200:
                raise ViperApiError(f"{error_label}: {status}")
            if data is None:
                # A 200 with a null/empty JSON body; callers would crash on
                # ``.get()``. Treat it as a server anomaly.
                raise ViperApiError(f"{error_label}: empty response body")
            return data
        except aiohttp.ClientError as err:
            raise ViperApiError(f"Connection error: {err}") from err

    async def _issue(
        self,
        session: aiohttp.ClientSession,
        request_factory: Callable[[aiohttp.ClientSession, dict[str, str]], Any],
    ) -> tuple[int, dict[str, Any] | None]:
        """Send one request; return (status, parsed json or None)."""
        async with request_factory(session, self._get_headers()) as response:
            if response.status == 200:
                return response.status, await response.json()
            return response.status, None

    def _get_headers(self) -> dict[str, str]:
        """Get authorization headers."""
        if not self._access_token:
            raise ViperAuthError("Not authenticated")
        return {"Authorization": f"Bearer {self._access_token}"}

    async def get_vehicles(self) -> list[Vehicle]:
        """Get list of vehicles."""

        def factory(session: aiohttp.ClientSession, headers: dict[str, str]):
            return session.get(API_DEVICES_URL, headers=headers)

        data = await self._authenticated_request(factory, error_label="API error")
        devices = (data.get("results") or {}).get("devices") or []

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

    async def _send_command(self, device_id: str, command: str) -> dict[str, Any]:
        """Send a command to a vehicle."""

        def factory(session: aiohttp.ClientSession, headers: dict[str, str]):
            return session.post(
                API_COMMAND_URL,
                headers=headers,
                json={"command": command, "deviceId": device_id},
            )

        return await self._authenticated_request(factory, error_label="Command failed")

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

        active_ok = not isinstance(active_result, Exception) and active_result is not None
        current_ok = (
            not isinstance(current_result, Exception) and current_result is not None
        )

        # If both status reads failed, surface the error instead of silently
        # returning an empty status. Swallowing it would wipe good state to
        # "unknown" and, on token expiry, never trigger re-authentication.
        if not active_ok and not current_ok:
            for result in (active_result, current_result):
                if isinstance(result, ViperAuthError):
                    raise result
            for result in (active_result, current_result):
                if isinstance(result, Exception):
                    raise ViperApiError(
                        f"Failed to read status for {device_id}: {result}"
                    ) from result
            raise ViperApiError(f"No status returned for {device_id}")

        status = VehicleStatus()

        # Process active status (GPS, door state, ignition, etc.)
        if active_ok:
            # The API can return ``{"results": null}`` (or null at the device /
            # deviceStatus level) when no live data is available. ``.get(k, {})``
            # would return that null, not the default, so guard each level.
            active_data = (active_result.get("results") or {}).get("device") or {}
            active_status = active_data.get("deviceStatus") or {}

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
        if current_ok:
            current_data = (current_result.get("results") or {}).get("device") or {}
            current_status = current_data.get("deviceStatus") or {}

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
        return bool(result.get("results"))

    async def lock(self, device_id: str) -> bool:
        """Send lock (arm) command."""
        result = await self._send_command(device_id, CMD_ARM)
        return bool(result.get("results"))

    async def unlock(self, device_id: str) -> bool:
        """Send unlock (disarm) command."""
        result = await self._send_command(device_id, CMD_DISARM)
        return bool(result.get("results"))
