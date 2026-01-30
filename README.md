# Viper SmartStart for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

> **WARNING: This integration was completely vibe coded by AI based on a hand-written Python script. It works, but review the code at your own discretion. Use at your own risk.**

A Home Assistant custom integration for Viper SmartStart vehicle remote start systems.

## Features

- **Remote Start/Stop** - Start or stop your vehicle remotely
- **Lock/Unlock** - Lock or unlock your vehicle doors
- **Vehicle Status** - Monitor battery voltage, door status, ignition, trunk, and hood
- **GPS Location** - Track your vehicle's location on the map
- **Manual Refresh** - Refresh status on-demand to conserve API calls

## API Rate Limits

**Important:** The Viper SmartStart API has a limit of approximately 5,000 calls per year. This integration defaults to **manual refresh only** to conserve your API quota. Each status check uses 2 API calls (active + current status).

Automatic polling is available but should be used sparingly. After remote start commands, the integration automatically refreshes status to verify the action.

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu and select "Custom repositories"
3. Add `https://github.com/CornHead764/ha-viper-smartstart` with category "Integration"
4. Click "Install"
5. Restart Home Assistant

### Manual Installation

1. Download the latest release
2. Copy the `custom_components/viper_smartstart` folder to your Home Assistant `custom_components` directory
3. Restart Home Assistant

## Configuration

1. Go to **Settings** > **Devices & Services**
2. Click **Add Integration**
3. Search for "Viper SmartStart"
4. Enter your Viper SmartStart credentials
5. Select your vehicle(s)
6. Set refresh interval (0 = disabled, recommended)

## Entities

For each vehicle, the following entities are created:

| Entity Type | Name | Description |
|-------------|------|-------------|
| Switch | Remote Start | Turn on/off to remote start/stop vehicle |
| Button | Lock | Lock the vehicle |
| Button | Unlock | Unlock the vehicle |
| Button | Refresh | Manually refresh vehicle status |
| Binary Sensor | Doors Open | Whether any door is open |
| Binary Sensor | Ignition | Whether ignition is on |
| Binary Sensor | Trunk Open | Whether trunk is open |
| Binary Sensor | Hood Open | Whether hood is open |
| Sensor | Battery Voltage | Vehicle battery voltage |
| Sensor | Last Updated | When status was last refreshed |
| Device Tracker | Location | GPS location of vehicle |

## Services

### `viper_smartstart.refresh`

Manually refresh all vehicle status data.

## Troubleshooting

### Icon not showing
Home Assistant loads integration icons from the [home-assistant/brands](https://github.com/home-assistant/brands) repository, **not** from the custom_components folder. Until a PR is submitted and merged there, you'll see a placeholder icon. This is a Home Assistant limitation, not a bug in this integration.

### Sensors unavailable
The integration preserves previous data during temporary API failures. If sensors remain unavailable, try the manual refresh button.

## Credits

This integration uses the unofficial Viper SmartStart API. Use at your own risk.

## License

MIT License
