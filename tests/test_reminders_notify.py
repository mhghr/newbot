"""Tests for WireGuard-router connectivity alerts sent to admins."""

import sys
import types
import unittest
from unittest import mock


def _stub_module(name, attrs=None):
    mod = types.ModuleType(name)
    for a in (attrs or []):
        setattr(mod, a, object)
    sys.modules[name] = mod
    return mod


try:
    import aiogram  # noqa: F401
except ModuleNotFoundError:
    _aiogram = _stub_module("aiogram", ["Bot"])
    _aiogram.types = _stub_module("aiogram.types", ["InlineKeyboardMarkup", "InlineKeyboardButton"])

try:
    import asyncpg  # noqa: F401
except ModuleNotFoundError:
    _stub_module("asyncpg", ["Pool", "Record"])

from bot.services import reminders  # noqa: E402

SERVER = {"id": 2, "name": "wg-prg", "url": "5.160.252.178", "api_port": 32}


class AlertStateTest(unittest.TestCase):
    def setUp(self):
        reminders._wg_server_alerted.clear()

    def test_failure_message_contains_ip(self):
        msg = reminders._wg_failure_message(SERVER, RuntimeError("timed out"))
        self.assertIn("5.160.252.178", msg)
        self.assertIn("برقرار نیست", msg)

    def test_alerted_only_once_per_outage(self):
        self.assertTrue(reminders._mark_alerted(SERVER))
        self.assertFalse(reminders._mark_alerted(SERVER))

    def test_clear_alert_allows_a_new_alert(self):
        reminders._mark_alerted(SERVER)
        reminders._clear_alert(SERVER)
        self.assertTrue(reminders._mark_alerted(SERVER))


class FetchRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_after_1min_then_2min_then_succeeds(self):
        state = {"n": 0}

        async def fake_fetch(server):
            state["n"] += 1
            if state["n"] < 3:
                raise RuntimeError("timeout")
            return {"peer": {"rx": 1}}

        with mock.patch.object(reminders.wg, "fetch_usage", new=mock.AsyncMock(side_effect=fake_fetch)), \
             mock.patch.object(reminders.asyncio, "sleep", new=mock.AsyncMock()) as sl:
            usage, err = await reminders._fetch_usage_checked(SERVER)

        self.assertEqual(usage, {"peer": {"rx": 1}})
        self.assertIsNone(err)
        self.assertEqual([c.args[0] for c in sl.await_args_list], [60, 120])

    async def test_alerts_only_after_three_failures(self):
        async def fake_fetch(server):
            raise RuntimeError("timeout")

        with mock.patch.object(reminders.wg, "fetch_usage", new=mock.AsyncMock(side_effect=fake_fetch)), \
             mock.patch.object(reminders.asyncio, "sleep", new=mock.AsyncMock()) as sl:
            usage, err = await reminders._fetch_usage_checked(SERVER)

        self.assertIsNone(usage)
        self.assertIsInstance(err, RuntimeError)
        self.assertEqual([c.args[0] for c in sl.await_args_list], [60, 120])


if __name__ == "__main__":
    unittest.main()
