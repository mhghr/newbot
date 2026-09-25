"""Remove a config from its backing 3x-ui panel or WireGuard router.

The database row is only soft-deleted (``is_active=FALSE``) so history stays
intact, but the actual client/peer is removed from the panel/router here.
"""

import logging

from bot.database import db
from bot.services.xui import XUIClient
from bot.services import wireguard as wg

logger = logging.getLogger(__name__)


def _service_of(config) -> str:
    return (config.get("service_type") or "v2ray") if config else "v2ray"


async def delete_remote(config) -> bool:
    """Delete the client/peer from the panel or router.

    Best-effort: never raises. Returns True when the remote deletion succeeded,
    False on failure (callers keep the refund/delete result regardless).
    """
    if not config:
        return False

    service = _service_of(config)
    server_id = config.get("server_id")
    try:
        server = await db.get_server(server_id) if server_id else None

        if service == "wireguard":
            if not server:
                logger.warning("WG delete: server %s not found for config %s", server_id, config.get("id"))
                return False
            return bool(await wg.delete_peer(
                server,
                public_key=config.get("wg_public_key"),
                peer_id=config.get("wg_peer_id"),
                client_ip=config.get("wg_client_ip"),
            ))

        if not server:
            server = await db.get_master_server("v2ray")
        if not server:
            logger.warning("V2Ray delete: no server for config %s", config.get("id"))
            return False

        xui = XUIClient(
            server["url"],
            username=server.get("username") or "",
            password=server.get("password") or "",
            api_token=server.get("api_token") or "",
        )
        return bool(await xui.delete_client(config.get("client_email")))
    except Exception as e:
        logger.warning(
            "remote delete failed for config %s (%s): %s: %s",
            config.get("id"), service, type(e).__name__, e,
        )
        return False


async def delete_config(config) -> bool:
    """Delete a config from its panel/router, then deactivate it in the database.

    Pass a real config row (e.g. from ``db.get_config``). Returns True when the
    remote deletion succeeded, False otherwise.
    """
    remote_ok = await delete_remote(config)
    if config and config.get("id") is not None:
        await db.delete_config(config["id"])
    return remote_ok
