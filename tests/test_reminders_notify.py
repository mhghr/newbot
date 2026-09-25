"""Tests for WireGuard-router connectivity alerts sent to admins."""

import sys
import types
import unittest


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

SERVER = {"id": 2, "name": "wg-prg", "url": "5.1.2.3", "api_port": 32}


class WgConnectivityAlertTest(unittest.TestCase):
    def setUp(self):
        reminders._wg_server_down.clear()

    def test_first_failure_alerts(self):
        msg = reminders._wg_connectivity_message(SERVER, False, RuntimeError("boom"))
        self.assertIsNotNone(msg)
        self.assertIn("برقرار نشد", msg)
        self.assertIn("wg-prg", msg)

    def test_repeated_failure_is_not_resent(self):
        reminders._wg_connectivity_message(SERVER, False, RuntimeError("boom"))
        self.assertIsNone(
            reminders._wg_connectivity_message(SERVER, False, RuntimeError("boom again"))
        )

    def test_recovery_after_failure_alerts(self):
        reminders._wg_connectivity_message(SERVER, False, RuntimeError("boom"))
        msg = reminders._wg_connectivity_message(SERVER, True)
        self.assertIsNotNone(msg)
        self.assertIn("برقرار شد", msg)

    def test_success_without_prior_failure_is_silent(self):
        self.assertIsNone(reminders._wg_connectivity_message(SERVER, True))

    def test_recovery_is_sent_only_once(self):
        reminders._wg_connectivity_message(SERVER, False, RuntimeError("boom"))
        reminders._wg_connectivity_message(SERVER, True)
        self.assertIsNone(reminders._wg_connectivity_message(SERVER, True))


if __name__ == "__main__":
    unittest.main()
