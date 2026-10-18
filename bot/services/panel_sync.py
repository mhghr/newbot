"""Keep the Iran nodes' WireGuard clients in sync and switch the entry domain (Arvan).

A location lives in one WireGuard inbound per Iran node. For a failover the
active node's peers (clients) are copied verbatim into the other nodes' inbound
(the inbound was cloned with the same server keys), and only then is the entry
domain's A record switched — so users reconnect to the new node without a new
config.
"""
import json
import logging
from urllib.parse import urlparse

from bot.database import db
from bot.services import arvan
from bot.services.xui import XUIClient

logger = logging.getLogger(__name__)


def node_panel_url(panel_url: str, node_ip: str) -> str:
    """Panel URL targeting a specific node's IP (same scheme/port/path)."""
    u = urlparse(panel_url if "://" in panel_url else "https://" + panel_url)
    scheme = u.scheme or "https"
    port = u.port or (443 if scheme == "https" else 80)
    path = (u.path or "").rstrip("/")
    return f"{scheme}://{node_ip}:{port}{path}"


def _as_obj(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return value or {}


async def _node_panel(node):
    """(url, token) of a node's panel; prefers the info stored on the node."""
    url = (node.get("panel_url") or "").strip()
    if url:
        return url, node.get("panel_token") or ""
    from bot.services import tunnel as tunnel_svc
    return await tunnel_svc._find_node_panel(node["ip"])


async def _wg_inbound(xui):
    for ib in await xui.get_inbounds():
        if (ib.get("protocol") or "").lower() == "wireguard":
            return ib
    return {}


async def _active_node():
    active_id = await db.get_setting("active_node_id", "")
    nodes = await db.get_iran_nodes()
    if active_id:
        for n in nodes:
            if str(n["id"]) == str(active_id):
                return n
    return nodes[0] if nodes else None


async def sync_wg_clients(active_node, target_node) -> bool:
    """Mirror the active node's WireGuard inbound (with its clients) to the target.

    The panel's inbound update does not persist WireGuard peers reliably, so the
    target inbound is recreated from the active one (same remark/port/settings,
    including the clients list). The inbound tag is derived from the port, so the
    routing rules keep matching.
    """
    if not active_node or not target_node:
        return False
    a_url, a_tok = await _node_panel(active_node)
    t_url, t_tok = await _node_panel(target_node)
    if not a_url or not t_url:
        return False
    a_xui = XUIClient(a_url, api_token=a_tok or "")
    t_xui = XUIClient(t_url, api_token=t_tok or "")
    a_ib = await _wg_inbound(a_xui)
    if not a_ib:
        logger.warning("wg sync: active node has no WireGuard inbound")
        return False

    settings = _as_obj(a_ib.get("settings"))
    settings.setdefault("clients", [])
    settings.setdefault("peers", [])

    t_ib = await _wg_inbound(t_xui)
    if t_ib:
        await t_xui.delete_inbound(t_ib["id"])

    payload = {
        "remark": a_ib.get("remark") or "loc",
        "enable": True,
        "port": a_ib.get("port") or 8585,
        "protocol": "wireguard",
        "listen": a_ib.get("listen") or "",
        "total": 0, "up": 0, "down": 0, "expiryTime": 0,
        "settings": settings,
        "streamSettings": _as_obj(a_ib.get("streamSettings")),
        "sniffing": _as_obj(a_ib.get("sniffing")),
    }
    res = await t_xui.add_inbound(payload)
    ok = bool(res.get("success")) if isinstance(res, dict) else bool(res)
    if ok:
        logger.info("wg sync: recreated %s with %d clients",
                    target_node.get("name"), len(settings.get("clients") or []))
    else:
        logger.warning("wg sync add failed for %s: %s", target_node.get("name"), res)
    return ok


async def switch_active(target_node) -> dict:
    """Sync clients to ``target_node`` first, then point the entry domain at it."""
    result = {"arvan": False, "sync": False, "error": ""}
    active = await _active_node()
    try:
        result["sync"] = await sync_wg_clients(active, target_node)
    except Exception as e:
        result["error"] += f"sync: {e}; "
    if not result["sync"]:
        return result
    try:
        result["arvan"] = await arvan.set_a_record(target_node["ip"])
    except Exception as e:
        result["error"] += f"arvan: {e}; "
    if result["arvan"]:
        await db.set_setting("active_node_id", str(target_node["id"]))
    return result


async def sync_standby() -> list:
    """Mirror the active node's WG clients to every other Iran node."""
    active = await _active_node()
    if not active:
        return []
    out = []
    for n in await db.get_iran_nodes():
        if str(n["id"]) == str(active["id"]):
            continue
        try:
            out.append((n["name"], await sync_wg_clients(active, n)))
        except Exception as e:
            out.append((n["name"], f"error: {e}"))
    return out
