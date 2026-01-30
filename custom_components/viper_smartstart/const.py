"""Constants for the Viper SmartStart integration."""

DOMAIN = "viper_smartstart"

# API URLs
API_BASE_URL = "https://www.vcp.cloud/v1"
API_LOGIN_URL = f"{API_BASE_URL}/auth/login"
API_DEVICES_URL = f"{API_BASE_URL}/devices/search/null"
API_COMMAND_URL = f"{API_BASE_URL}/devices/command"

# Commands
CMD_ARM = "arm"
CMD_DISARM = "disarm"
CMD_REMOTE = "remote"
CMD_READ_ACTIVE = "read_active"
CMD_READ_CURRENT = "read_current"

# Config keys
CONF_REFRESH_INTERVAL = "refresh_interval"
CONF_VEHICLES = "vehicles"

# Defaults
DEFAULT_REFRESH_INTERVAL = 0  # Disabled by default to conserve API calls (5000/year limit)

# Services
SERVICE_REFRESH = "refresh"
