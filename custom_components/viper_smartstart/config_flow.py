"""Config flow for Viper SmartStart integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import Vehicle, ViperApi, ViperApiError, ViperAuthError
from .const import (
    CONF_REFRESH_INTERVAL,
    CONF_VEHICLES,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class ViperSmartStartConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Viper SmartStart."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._username: str | None = None
        self._password: str | None = None
        self._vehicles: list[Vehicle] = []
        self._api: ViperApi | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - authentication."""
        _LOGGER.info("Viper SmartStart config flow started")
        errors: dict[str, str] = {}

        if user_input is not None:
            _LOGGER.info("Processing login attempt for user: %s", user_input.get(CONF_USERNAME))
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            # Attempt to authenticate
            session = async_get_clientsession(self.hass)
            self._api = ViperApi(self._username, self._password, session)

            try:
                await self._api.authenticate()
                self._vehicles = await self._api.get_vehicles()

                if not self._vehicles:
                    errors["base"] = "no_vehicles"
                else:
                    # Move to vehicle selection step
                    return await self.async_step_vehicles()

            except ViperAuthError as err:
                _LOGGER.warning("Authentication failed: %s", err)
                errors["base"] = "invalid_auth"
            except ViperApiError as err:
                _LOGGER.warning("API error: %s", err)
                errors["base"] = "cannot_connect"
            except (aiohttp.ClientError, aiohttp.ContentTypeError) as err:
                _LOGGER.warning("Connection error: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during authentication")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_vehicles(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle vehicle selection and refresh interval."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_vehicles = user_input.get(CONF_VEHICLES, [])

            if not selected_vehicles:
                errors["base"] = "no_vehicles_selected"
            else:
                # Create unique ID based on username
                await self.async_set_unique_id(self._username)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Viper SmartStart ({self._username})",
                    data={
                        CONF_USERNAME: self._username,
                        CONF_PASSWORD: self._password,
                        CONF_VEHICLES: selected_vehicles,
                        CONF_REFRESH_INTERVAL: int(user_input.get(
                            CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL
                        )),
                    },
                )

        # Build vehicle options for selector
        vehicle_options = []
        for vehicle in self._vehicles:
            label_parts = [vehicle.name]
            extra_parts = []
            if vehicle.year:
                extra_parts.append(vehicle.year)
            if vehicle.make:
                extra_parts.append(vehicle.make)
            if vehicle.model:
                extra_parts.append(vehicle.model)
            if extra_parts:
                label_parts.append(f"({' '.join(extra_parts)})")
            vehicle_options.append({
                "value": vehicle.id,
                "label": " ".join(label_parts),
            })

        # Default to all vehicles selected
        default_vehicles = [v.id for v in self._vehicles]

        return self.async_show_form(
            step_id="vehicles",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VEHICLES, default=default_vehicles): SelectSelector(
                        SelectSelectorConfig(
                            options=vehicle_options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    ),
                    vol.Optional(
                        CONF_REFRESH_INTERVAL, default=DEFAULT_REFRESH_INTERVAL
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=86400,
                            step=60,
                            unit_of_measurement="seconds",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "vehicle_count": str(len(self._vehicles)),
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication."""
        self._username = entry_data.get(CONF_USERNAME)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reauthentication confirmation."""
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = ViperApi(self._username, user_input[CONF_PASSWORD], session)

            try:
                await api.authenticate()

                # Update the config entry with new password
                entry = self.hass.config_entries.async_get_entry(
                    self.context["entry_id"]
                )
                if entry:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            **entry.data,
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                        },
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

            except ViperAuthError:
                errors["base"] = "invalid_auth"
            except ViperApiError:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={"username": self._username},
        )
