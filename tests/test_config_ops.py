"""Tests for bot.services.config_ops remote deletion routing."""

import sys
import types
import unittest
from unittest import mock

try:
    import asyncpg  # noqa: F401
except ModuleNotFoundError:  # minimal local env; the db modules only need the name
    _stub = types.ModuleType("asyncpg")
    _stub.Pool = object
    _stub.Record = object
    sys.modules["asyncpg"] = _stub

from bot.services import config_ops

SERVER = {
    "id": 1, "url": "https://panel.example.com",
    "username": "u", "password": "p", "api_token": "t",
}


class DeleteConfigTest(unittest.IsolatedAsyncioTestCase):
    async def test_wireguard_deletes_peer_and_row(self):
        config = {
            "id": 5, "service_type": "wireguard", "server_id": 1,
            "wg_public_key": "pub", "wg_peer_id": None, "wg_client_ip": "10.0.0.2",
        }
        with mock.patch.object(config_ops.db, "get_server", new=mock.AsyncMock(return_value=SERVER)), \
             mock.patch.object(config_ops.wg, "delete_peer", new=mock.AsyncMock(return_value=True)) as dp, \
             mock.patch.object(config_ops.db, "delete_config", new=mock.AsyncMock()) as dc:
            ok = await config_ops.delete_config(config)

        self.assertTrue(ok)
        kwargs = dp.await_args.kwargs
        self.assertEqual(kwargs.get("public_key"), "pub")
        self.assertEqual(kwargs.get("client_ip"), "10.0.0.2")
        dc.assert_awaited_once_with(5)

    async def test_v2ray_deletes_client_and_row(self):
        config = {"id": 7, "service_type": "v2ray", "server_id": 1, "client_email": "1-order2"}
        with mock.patch.object(config_ops.db, "get_server", new=mock.AsyncMock(return_value=SERVER)), \
             mock.patch.object(config_ops, "XUIClient") as XUI, \
             mock.patch.object(config_ops.db, "delete_config", new=mock.AsyncMock()) as dc:
            XUI.return_value.delete_client = mock.AsyncMock(return_value=True)
            ok = await config_ops.delete_config(config)

        self.assertTrue(ok)
        XUI.return_value.delete_client.assert_awaited_once_with("1-order2")
        dc.assert_awaited_once_with(7)

    async def test_remote_failure_still_deactivates_row(self):
        config = {"id": 9, "service_type": "v2ray", "server_id": 1, "client_email": "x"}
        with mock.patch.object(config_ops.db, "get_server", new=mock.AsyncMock(return_value=SERVER)), \
             mock.patch.object(config_ops, "XUIClient") as XUI, \
             mock.patch.object(config_ops.db, "delete_config", new=mock.AsyncMock()) as dc:
            XUI.return_value.delete_client = mock.AsyncMock(side_effect=RuntimeError("boom"))
            ok = await config_ops.delete_config(config)

        self.assertFalse(ok)
        dc.assert_awaited_once_with(9)

    async def test_v2ray_falls_back_to_master_server(self):
        config = {"id": 11, "service_type": "v2ray", "server_id": None, "client_email": "c"}
        with mock.patch.object(config_ops.db, "get_server", new=mock.AsyncMock()) as gs, \
             mock.patch.object(config_ops.db, "get_master_server", new=mock.AsyncMock(return_value=SERVER)), \
             mock.patch.object(config_ops, "XUIClient") as XUI, \
             mock.patch.object(config_ops.db, "delete_config", new=mock.AsyncMock()):
            XUI.return_value.delete_client = mock.AsyncMock(return_value=True)
            ok = await config_ops.delete_config(config)

        self.assertTrue(ok)
        gs.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
