"""Tests for ViperApi.get_vehicle_status null-handling.

The Viper command endpoint (read_active / read_current) can return HTTP 200
with a body of ``{"results": null}`` (and other partially-null nestings) when
no live device data is available. ``get_vehicle_status`` must tolerate this and
return a VehicleStatus instead of raising AttributeError.

Self-contained: loads the ``const`` and ``api`` modules into a synthetic
package so the real package ``__init__`` (which imports homeassistant) never
runs. Run with: ``python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest

PKG_DIR = (
    pathlib.Path(__file__).resolve().parent.parent
    / "custom_components"
    / "viper_smartstart"
)

# Build a synthetic package so ``from .const import ...`` resolves without
# executing the real integration __init__ (which depends on homeassistant).
_pkg = types.ModuleType("viper_pkg")
_pkg.__path__ = [str(PKG_DIR)]
sys.modules["viper_pkg"] = _pkg


def _load(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"viper_pkg.{name}", PKG_DIR / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"viper_pkg.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("const")
api = _load("api")
ViperApi = api.ViperApi
VehicleStatus = api.VehicleStatus


def _make_api(responses: dict[str, object]) -> ViperApi:
    """Return a ViperApi whose _send_command yields canned responses by command."""
    client = ViperApi("user", "pass")

    async def fake_send_command(device_id: str, command: str):
        return responses[command]

    client._send_command = fake_send_command  # type: ignore[method-assign]
    return client


class _FakeResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class _FakeCtx:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    def get(self, *args, **kwargs):
        return _FakeCtx(self._response)

    def post(self, *args, **kwargs):
        return _FakeCtx(self._response)


def _make_api_with_session(status: int, payload: object) -> ViperApi:
    client = ViperApi("user", "pass", session=_FakeSession(_FakeResponse(status, payload)))
    client._access_token = "tok"  # bypass _get_headers auth check
    return client


class GetVehicleStatusNullTest(unittest.TestCase):
    def test_results_null_for_both_commands_returns_empty_status(self):
        """The real production payload: {"results": null} from both reads."""
        client = _make_api(
            {
                const.CMD_READ_ACTIVE: {"results": None},
                const.CMD_READ_CURRENT: {"results": None},
            }
        )

        status = asyncio.run(client.get_vehicle_status("123"))

        self.assertIsInstance(status, VehicleStatus)
        self.assertIsNone(status.latitude)
        self.assertIsNone(status.doors_locked)

    def test_device_null_under_results_returns_empty_status(self):
        """results present but device is null."""
        client = _make_api(
            {
                const.CMD_READ_ACTIVE: {"results": {"device": None}},
                const.CMD_READ_CURRENT: {"results": {"device": None}},
            }
        )

        status = asyncio.run(client.get_vehicle_status("123"))

        self.assertIsInstance(status, VehicleStatus)
        self.assertIsNone(status.latitude)

    def test_device_status_null_still_parses_top_level_fields(self):
        """deviceStatus is null but top-level device fields are present."""
        client = _make_api(
            {
                const.CMD_READ_ACTIVE: {
                    "results": {
                        "device": {
                            "deviceStatus": None,
                            "latitude": "12.5",
                            "longitude": "-1.25",
                        }
                    }
                },
                const.CMD_READ_CURRENT: {"results": None},
            }
        )

        status = asyncio.run(client.get_vehicle_status("123"))

        self.assertEqual(status.latitude, 12.5)
        self.assertEqual(status.longitude, -1.25)
        # deviceStatus was null, so door/ignition stay unknown rather than crash
        self.assertIsNone(status.doors_open)

    def test_full_data_still_parses(self):
        """Regression guard: well-formed payloads still parse correctly."""
        client = _make_api(
            {
                const.CMD_READ_ACTIVE: {
                    "results": {
                        "device": {
                            "latitude": "40.0",
                            "longitude": "-75.0",
                            "deviceStatus": {"doorsOpen": True, "ignitionOn": False},
                        }
                    }
                },
                const.CMD_READ_CURRENT: {
                    "results": {
                        "device": {
                            "deviceStatus": {
                                "doorsLocked": True,
                                "remoteStarterActive": True,
                            }
                        }
                    }
                },
            }
        )

        status = asyncio.run(client.get_vehicle_status("123"))

        self.assertEqual(status.latitude, 40.0)
        self.assertEqual(status.longitude, -75.0)
        self.assertTrue(status.doors_open)
        self.assertFalse(status.ignition_on)
        self.assertTrue(status.doors_locked)
        self.assertTrue(status.remote_starter_active)


class GetVehiclesNullTest(unittest.TestCase):
    def test_results_null_returns_empty_list(self):
        """The devices endpoint returning {"results": null} must not crash setup."""
        client = _make_api_with_session(200, {"results": None})

        vehicles = asyncio.run(client.get_vehicles())

        self.assertEqual(vehicles, [])

    def test_well_formed_devices_parse(self):
        """Regression guard: normal device payloads still parse."""
        client = _make_api_with_session(
            200,
            {"results": {"devices": [{"id": 7, "name": "Truck", "make": "Dodge"}]}},
        )

        vehicles = asyncio.run(client.get_vehicles())

        self.assertEqual(len(vehicles), 1)
        self.assertEqual(vehicles[0].id, "7")
        self.assertEqual(vehicles[0].name, "Truck")
        self.assertEqual(vehicles[0].make, "Dodge")


if __name__ == "__main__":
    unittest.main()
