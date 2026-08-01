"""Tests for bot.services.xui."""

import unittest
from unittest import mock

from bot.services import xui

ONE_GB = 1024 * 1024 * 1024


class UpdateClientTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = xui.XUIClient("https://panel.example.com", api_token="tok")

    async def test_update_client_sends_expected_payload(self):
        with mock.patch.object(
            self.client, "_request", new=mock.AsyncMock(return_value={"success": True})
        ) as req:
            ok = await self.client.update_client(
                email="alice@example.com",
                traffic_gb=100,
                expire_days=30,
                tg_id=123456789,
            )

        self.assertTrue(ok)
        method, path = req.await_args.args
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/panel/api/clients/update/alice@example.com")
        payload = req.await_args.kwargs["json"]
        self.assertEqual(payload["email"], "alice@example.com")
        self.assertEqual(payload["totalGB"], 100 * ONE_GB)
        self.assertGreater(payload["expiryTime"], 0)
        self.assertEqual(payload["tgId"], 123456789)
        self.assertTrue(payload["enable"])

    async def test_update_client_zero_quota_is_unlimited(self):
        with mock.patch.object(
            self.client, "_request", new=mock.AsyncMock(return_value={"success": True})
        ) as req:
            await self.client.update_client(email="bob@example.com", traffic_gb=0, expire_days=0)

        payload = req.await_args.kwargs["json"]
        self.assertEqual(payload["totalGB"], 0)
        self.assertEqual(payload["expiryTime"], 0)

    async def test_update_client_raises_when_panel_rejects(self):
        with mock.patch.object(
            self.client, "_request", new=mock.AsyncMock(return_value={"success": False})
        ):
            with self.assertRaises(Exception):
                await self.client.update_client(email="carol@example.com", traffic_gb=10, expire_days=1)


if __name__ == "__main__":
    unittest.main()
