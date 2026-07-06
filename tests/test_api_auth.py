"""Tests for ViperApi silent re-authentication and token-expiry tracking.

The API client must transparently keep its session alive: parse the login
token's expiration, proactively refresh an expired token before issuing a
request, and on an HTTP 401 clear the token, re-login once, and retry the
request once. Callers only ever see ``ViperAuthError`` when credentials are
genuinely rejected.

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
from collections import deque
from datetime import datetime, timedelta, timezone

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
ViperAuthError = api.ViperAuthError
ViperApiError = api.ViperApiError


def _login_payload(expiration: object = None) -> dict:
    return {
        "results": {
            "authToken": {
                "accessToken": "fresh-token",
                "expiration": expiration,
            }
        }
    }


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


class _RecordingSession:
    """Fake aiohttp session that dispatches on URL and counts POSTs.

    Login POSTs and command POSTs are counted separately so tests can assert
    exactly how many logins/commands happened. Responses for commands and the
    devices GET are consumed from a queue (falling back to a default) so tests
    can script status sequences like [401, 200].
    """

    def __init__(
        self,
        login_response: _FakeResponse | None = None,
        command_responses: list[_FakeResponse] | None = None,
        get_responses: list[_FakeResponse] | None = None,
    ) -> None:
        self.login_calls = 0
        self.command_calls = 0
        self.get_calls = 0
        self._login_response = login_response or _FakeResponse(200, _login_payload())
        self._command_responses = deque(command_responses or [])
        self._get_responses = deque(get_responses or [])

    def post(self, url, *args, **kwargs):
        if url == const.API_LOGIN_URL:
            self.login_calls += 1
            return _FakeCtx(self._login_response)
        if url == const.API_COMMAND_URL:
            self.command_calls += 1
            resp = (
                self._command_responses.popleft()
                if self._command_responses
                else _FakeResponse(200, {"results": {"ok": True}})
            )
            return _FakeCtx(resp)
        raise AssertionError(f"Unexpected POST url: {url}")

    def get(self, url, *args, **kwargs):
        if url == const.API_DEVICES_URL:
            self.get_calls += 1
            resp = (
                self._get_responses.popleft()
                if self._get_responses
                else _FakeResponse(200, {"results": {"devices": []}})
            )
            return _FakeCtx(resp)
        raise AssertionError(f"Unexpected GET url: {url}")


def _future() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=1)


def _past() -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=1)


class ExpirationParsingTest(unittest.TestCase):
    def setUp(self):
        self.client = ViperApi("user", "pass")

    def test_epoch_seconds(self):
        parsed = self.client._parse_expiration(1_700_000_000)
        self.assertIsNotNone(parsed)
        self.assertEqual(
            parsed, datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
        )

    def test_epoch_milliseconds(self):
        parsed = self.client._parse_expiration(1_700_000_000_000)
        self.assertIsNotNone(parsed)
        # ms value must be scaled to the same instant as the seconds value
        self.assertEqual(
            parsed, datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
        )

    def test_iso_string_with_z(self):
        parsed = self.client._parse_expiration("2030-01-02T03:04:05Z")
        self.assertEqual(
            parsed,
            datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        )

    def test_iso_naive_string_assumed_utc(self):
        parsed = self.client._parse_expiration("2030-01-02T03:04:05")
        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_garbage_string_returns_none(self):
        self.assertIsNone(self.client._parse_expiration("not-a-date"))

    def test_missing_returns_none(self):
        self.assertIsNone(self.client._parse_expiration(None))


class TokenValidityTest(unittest.TestCase):
    def test_no_token_not_authenticated(self):
        client = ViperApi("user", "pass")
        self.assertFalse(client.is_authenticated)

    def test_token_without_expiry_is_valid(self):
        client = ViperApi("user", "pass")
        client._access_token = "tok"
        client._token_expires_at = None
        self.assertTrue(client.is_authenticated)

    def test_expired_token_not_authenticated(self):
        client = ViperApi("user", "pass")
        client._access_token = "tok"
        client._token_expires_at = _past()
        self.assertFalse(client.is_authenticated)

    def test_token_inside_safety_margin_not_valid(self):
        client = ViperApi("user", "pass")
        client._access_token = "tok"
        # 30s from now is inside the 60s safety margin -> treated as expired
        client._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)
        self.assertFalse(client.is_authenticated)


class SilentReauthTest(unittest.TestCase):
    def test_expired_token_triggers_login_before_request(self):
        session = _RecordingSession()
        client = ViperApi("user", "pass", session=session)
        client._access_token = "stale"
        client._token_expires_at = _past()

        result = asyncio.run(client._send_command("dev", const.CMD_REMOTE))

        self.assertEqual(session.login_calls, 1)
        self.assertEqual(session.command_calls, 1)
        self.assertEqual(client._access_token, "fresh-token")
        self.assertTrue(result)

    def test_valid_token_no_extra_login(self):
        session = _RecordingSession()
        client = ViperApi("user", "pass", session=session)
        client._access_token = "good"
        client._token_expires_at = _future()

        asyncio.run(client._send_command("dev", const.CMD_REMOTE))

        self.assertEqual(session.login_calls, 0)
        self.assertEqual(session.command_calls, 1)

    def test_401_triggers_one_relogin_and_one_retry(self):
        session = _RecordingSession(
            command_responses=[
                _FakeResponse(401, None),
                _FakeResponse(200, {"results": {"ok": True}}),
            ]
        )
        client = ViperApi("user", "pass", session=session)
        client._access_token = "good"
        client._token_expires_at = _future()  # proactively valid; server rejects it

        result = asyncio.run(client._send_command("dev", const.CMD_REMOTE))

        self.assertEqual(session.login_calls, 1)
        self.assertEqual(session.command_calls, 2)
        self.assertEqual(result, {"results": {"ok": True}})

    def test_retry_also_401_raises_auth_error(self):
        session = _RecordingSession(
            command_responses=[
                _FakeResponse(401, None),
                _FakeResponse(401, None),
            ]
        )
        client = ViperApi("user", "pass", session=session)
        client._access_token = "good"
        client._token_expires_at = _future()

        with self.assertRaises(ViperAuthError):
            asyncio.run(client._send_command("dev", const.CMD_REMOTE))

        self.assertEqual(session.login_calls, 1)
        self.assertEqual(session.command_calls, 2)

    def test_concurrent_expired_requests_login_once(self):
        session = _RecordingSession()
        client = ViperApi("user", "pass", session=session)
        client._access_token = "stale"
        client._token_expires_at = _past()

        async def run():
            await asyncio.gather(
                client._send_command("dev", const.CMD_READ_ACTIVE),
                client._send_command("dev", const.CMD_READ_CURRENT),
                client._send_command("dev", const.CMD_REMOTE),
            )

        asyncio.run(run())

        self.assertEqual(session.login_calls, 1)
        self.assertEqual(session.command_calls, 3)

    def test_get_vehicles_401_relogins_and_retries(self):
        session = _RecordingSession(
            get_responses=[
                _FakeResponse(401, None),
                _FakeResponse(200, {"results": {"devices": [{"id": 3, "name": "X"}]}}),
            ]
        )
        client = ViperApi("user", "pass", session=session)
        client._access_token = "good"
        client._token_expires_at = _future()

        vehicles = asyncio.run(client.get_vehicles())

        self.assertEqual(session.login_calls, 1)
        self.assertEqual(session.get_calls, 2)
        self.assertEqual(len(vehicles), 1)
        self.assertEqual(vehicles[0].id, "3")


class AuthenticateErrorTest(unittest.TestCase):
    def test_bad_credentials_raise_auth_error(self):
        session = _RecordingSession(login_response=_FakeResponse(401, None))
        client = ViperApi("user", "pass", session=session)
        with self.assertRaises(ViperAuthError):
            asyncio.run(client.authenticate())

    def test_forbidden_raises_auth_error(self):
        session = _RecordingSession(login_response=_FakeResponse(403, None))
        client = ViperApi("user", "pass", session=session)
        with self.assertRaises(ViperAuthError):
            asyncio.run(client.authenticate())

    def test_server_error_raises_api_error_not_auth(self):
        session = _RecordingSession(login_response=_FakeResponse(500, None))
        client = ViperApi("user", "pass", session=session)
        with self.assertRaises(ViperApiError):
            asyncio.run(client.authenticate())

    def test_successful_login_parses_expiration(self):
        session = _RecordingSession(
            login_response=_FakeResponse(200, _login_payload("2030-01-02T03:04:05Z"))
        )
        client = ViperApi("user", "pass", session=session)
        asyncio.run(client.authenticate())
        self.assertEqual(client._access_token, "fresh-token")
        self.assertEqual(
            client._token_expires_at,
            datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        )


class CommandSuccessDetectionTest(unittest.TestCase):
    def _client(self, response: object) -> ViperApi:
        client = ViperApi("user", "pass")

        async def fake_send_command(device_id, command):
            return response

        client._send_command = fake_send_command  # type: ignore[method-assign]
        return client

    def test_null_results_is_failure(self):
        client = self._client({"results": None})
        self.assertFalse(asyncio.run(client.remote_start("dev")))

    def test_missing_results_is_failure(self):
        client = self._client({})
        self.assertFalse(asyncio.run(client.lock("dev")))

    def test_truthy_results_is_success(self):
        client = self._client({"results": {"status": "ok"}})
        self.assertTrue(asyncio.run(client.remote_start("dev")))
        self.assertTrue(asyncio.run(client.lock("dev")))
        self.assertTrue(asyncio.run(client.unlock("dev")))


if __name__ == "__main__":
    unittest.main()
