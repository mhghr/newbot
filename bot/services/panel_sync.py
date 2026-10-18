"""Export the active node's clients and switch the entry domain (Arvan).

Every minute the active Iran node's panel is asked for
``GET /panel/api/clients/export`` and the returned array
(``[{client, inboundIds, traffic}]``) is saved on disk. When the active IP is
switched, the A record is updated in Arvan and the saved array is imported into
the new node with ``POST /panel/api/clients/import`` (``{"data": "<json>"}``).
"""
import json
import logging
import os
from urllib.parse import urlparse

from bot.database import db
from bot.services import arvan
from bot.services.xui import XUIClient

logger = logging.getLogger(__name__)

EXPORT_PATH = "/root/newbot/backups/clients_export.json"


def node_panel_url(panel_url: str, node_ip: str) -> str:
    """Panel URL targeting a specific node's IP (same scheme/port/path)."""
    u = urlparse(panel_url if "://" in panel_url else "https://" + panel_url)
    scheme = u.scheme or "https"
    port = u.port or (443 if scheme == "https" else 80)
    path = (u.path or "").rstrip("/")
    return f"{scheme}://{node_ip}:{port}{path}"


async def _node_panel(node):
    """(url, token) of a node's panel; prefers the info stored on the node."""
    url = (node.get("panel_url") or "").strip()
    if url:
        return url, node.get("panel_token") or ""
    from bot.services import tunnel as tunnel_svc
    return await tunnel_svc._find_node_panel(node["ip"])


async def active_node():
    """The Iran node the entry domain currently points to."""
    active_id = await db.get_setting("active_node_id", "")
    nodes = await db.get_iran_nodes()
    if active_id:
        for n in nodes:
            if str(n["id"]) == str(active_id):
                return n
    return nodes[0] if nodes else None


async def export_active(active=None) -> list:
    """Return the active node's export array ([{client, inboundIds, traffic}])."""
    node = active or await active_node()
    if not node:
        return []
    url, tok = await _node_panel(node)
    if not url:
        return []
    return await XUIClient(url, api_token=tok or "").export_clients()


def save_export(clients: list) -> str:
    """Persist the export array on disk (atomic write)."""
    os.makedirs(os.path.dirname(EXPORT_PATH), exist_ok=True)
    tmp = EXPORT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(clients, f, ensure_ascii=False)
    os.replace(tmp, EXPORT_PATH)
    return EXPORT_PATH


def load_export() -> list:
    try:
        with open(EXPORT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def refresh_export() -> int:
    """Export the active node's clients and save them; returns the count."""
    clients = await export_active()
    save_export(clients)
    return len(clients)


async def _remap_inbound_ids(clients: list, source_node, target_node) -> list:
    """Remap each client's ``inboundIds`` from the source node to the target.

    The same location has a different inbound id on every node; the mapping is
    stored in ``node_inbounds`` when a location is created/synced.
    """
    try:
        src_rows = await db.get_node_inbounds(source_node["id"])
        dst_rows = await db.get_node_inbounds(target_node["id"])
    except Exception as e:
        logger.warning("inbound map load failed: %s", e)
        return clients
    old_to_tunnel = {int(r["inbound_id"]): int(r["tunnel_id"]) for r in src_rows}
    tunnel_to_new = {int(r["tunnel_id"]): int(r["inbound_id"]) for r in dst_rows}
    if not old_to_tunnel or not tunnel_to_new:
        logger.warning("inbound map empty (source=%d target=%d)",
                       len(old_to_tunnel), len(tunnel_to_new))
        return clients

    out, skipped = [], 0
    for item in clients:
        ids = item.get("inboundIds") or []
        if not ids:
            continue
        new_ids = []
        for old in ids:
            try:
                tid = old_to_tunnel.get(int(old))
            except Exception:
                tid = None
            if tid is not None and tid in tunnel_to_new:
                new_ids.append(tunnel_to_new[tid])
        if not new_ids:
            skipped += 1
            continue
        item = dict(item)
        item["inboundIds"] = new_ids
        out.append(item)
    if skipped:
        logger.info("client import: skipped %d clients with unmapped inbounds", skipped)
    return out


async def import_to_node(target_node, clients: list, source_node=None) -> bool:
    if not clients:
        return False
    url, tok = await _node_panel(target_node)
    if not url:
        return False
    src = source_node or await active_node()
    mapped = await _remap_inbound_ids(clients, src, target_node) if src else clients
    if not mapped:
        logger.warning("import: nothing left after inbound remap")
        return False
    return await XUIClient(url, api_token=tok or "").import_clients(mapped)


async def switch_active(target_node) -> dict:
    """Change the Arvan A record to ``target_node``, then import the clients."""
    result = {"arvan": False, "sync": False, "error": "", "count": 0}

    # Fresh export from the node the domain currently points to (also on disk).
    active = await active_node()
    clients = []
    try:
        clients = await export_active(active) if active else []
        if clients:
            save_export(clients)
    except Exception as e:
        result["error"] += f"export: {e}; "
        clients = load_export()
    result["count"] = len(clients)

    # 1) point the domain at the new node
    try:
        result["arvan"] = await arvan.set_a_record(target_node["ip"])
    except Exception as e:
        result["error"] += f"arvan: {e}; "

    # 2) import the clients into the new node
    try:
        result["sync"] = await import_to_node(target_node, clients, source_node=active)
    except Exception as e:
        result["error"] += f"import: {e}; "

    if result["sync"]:
        await db.set_setting("active_node_id", str(target_node["id"]))
    return result
