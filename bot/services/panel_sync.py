"""Export the active node's clients and switch the entry domain (Arvan).

Every minute the active Iran node's panel is asked for
``GET /panel/api/clients/export`` and the returned array
(``[{client, inboundIds, traffic}]``) is saved on disk. When the active IP is
switched, the A record is updated in Arvan and the saved array is imported into
the new node with ``POST /panel/api/clients/import`` (``{"data": "<json>"}``).
"""
import asyncio
import json
import logging
import os
from urllib.parse import urlparse

from bot.config import ENDPOINT_DOMAIN
from bot.database import db
from bot.services import arvan
from bot.services.xui import XUIClient, panel_sub_base

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


async def active_panel_inbound():
    """Return ``(url, token, inbound_id)`` for the active entry server.

    Accounts are created on the active server's own panel, in its WireGuard
    inbound. The inbound is taken from ``node_inbounds`` when recorded, and
    otherwise detected from the panel (first enabled WireGuard inbound).
    """
    node = await active_node()
    if not node:
        return None
    url, tok = await _node_panel(node)
    if not url:
        return None
    xui = XUIClient(url, api_token=tok or "")
    inbound_id = 0
    try:
        rows = await db.get_node_inbounds(node["id"])
        if rows:
            inbound_id = int(rows[0]["inbound_id"])
    except Exception:
        inbound_id = 0
    try:
        inbounds = await xui.get_inbounds()
    except Exception:
        inbounds = []
    ids = {int(ib["id"]) for ib in inbounds if ib.get("id") is not None}
    if inbound_id and inbound_id not in ids:
        inbound_id = 0
    if not inbound_id:
        for ib in inbounds:
            if (ib.get("protocol") or "").lower() == "wireguard" and ib.get("enable", True):
                inbound_id = int(ib["id"])
                break
    if not inbound_id:
        for ib in inbounds:
            if ib.get("enable", True):
                inbound_id = int(ib["id"])
                break
    if not inbound_id:
        return None
    return url, tok or "", inbound_id


async def sub_base_for_node(node):
    """Subscription base URL for a server (old-style ``.../sub/<subId>``)."""
    url = (node.get("panel_url") or "").strip()
    if not url:
        return ""
    host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    try:
        for server in await db.get_all_servers():
            s_url = (server.get("url") or "").strip()
            s_host = (urlparse(s_url if "://" in s_url else "https://" + s_url).hostname or "").lower()
            if s_host and s_host == host:
                return panel_sub_base(
                    s_url, server.get("sub_port") or 2096,
                    server.get("sub_domain") or "",
                )
    except Exception as e:
        logger.warning("sub base lookup failed: %s", e)
    return panel_sub_base(url)


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


def _client_fingerprint(clients: list) -> frozenset:
    """(email, public key) pairs — enough to tell whether two nodes match."""
    out = []
    for item in clients or []:
        c = item.get("client") if isinstance(item, dict) and "client" in item else item
        if not isinstance(c, dict):
            continue
        out.append((c.get("email") or "", c.get("publicKey") or c.get("id") or ""))
    return frozenset(out)


async def _verify_dns(expected_ip: str, attempts: int = 6, delay: float = 5.0) -> str:
    """Resolve the entry domain until it returns ``expected_ip`` (best effort).

    The A record has a 120s TTL, so the answer is taken as soon as any resolver
    replies with the new IP.  Returns the last observed address list.
    """
    domain = ""
    try:
        domain = await db.get_setting("endpoint_domain", "") or ENDPOINT_DOMAIN
    except Exception:
        domain = ENDPOINT_DOMAIN
    last = ""
    for _ in range(attempts):
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(domain, None)
            ips = {i[4][0] for i in infos}
            last = ",".join(sorted(ips))
            if expected_ip in ips:
                return expected_ip
        except Exception as e:
            last = f"resolve error: {e}"
        await asyncio.sleep(delay)
    return last


async def _master_panel():
    """(url, token) of the panel that owns every node's inbounds.

    The master panel lists the other nodes' inbounds (tags ``n2-…``, ``n3-…``)
    and is the only place where a client can be attached to another node's
    inbound.  It is remembered in ``master_node_id``.
    """
    node = None
    master_id = ""
    try:
        master_id = await db.get_setting("master_node_id", "")
    except Exception:
        master_id = ""
    if master_id:
        try:
            node = await db.get_node(int(master_id))
        except Exception:
            node = None
    if not node:
        node = await active_node()
    if not node:
        return None, ""
    url, tok = await _node_panel(node)
    return url, tok or ""


async def _inbound_clients(xui, inbound_id: int) -> list:
    """Emails of the clients attached to ``inbound_id`` on the master panel."""
    out = []
    for ib in await xui.get_inbounds():
        try:
            if int(ib.get("id", -1)) != int(inbound_id):
                continue
        except (TypeError, ValueError):
            continue
        s = ib.get("settings")
        if isinstance(s, str):
            try:
                s = json.loads(s)
            except Exception:
                s = {}
        for c in (s or {}).get("clients", []) or []:
            em = c.get("email")
            if em:
                out.append(em)
    return out


async def switch_active(target_node) -> dict:
    """Move the entry domain to ``target_node`` by re-attaching its clients.

    The master panel already holds every node's inbounds, and all WireGuard
    inbounds share one server key — so nothing has to be copied: the client
    simply gets attached to the target node's inbound (and then detached from
    the old one) before the A record moves.

    Order matters: the domain is flipped **after** the clients are on the
    target, and ``active_node_id`` is written only when the record really
    changed, so the admin's "active server" and the entry domain never drift.
    """
    result = {"arvan": False, "sync": False, "error": "", "count": 0, "dns": ""}
    emails, src_id, dst_id = [], 0, 0

    active = await active_node()
    if not active:
        result["error"] = "no active node"
        return result

    if str(active["id"]) == str(target_node["id"]):
        result["sync"] = True
    else:
        url, tok = await _master_panel()
        if not url:
            result["error"] += "master panel not found; "
            return result
        xui = XUIClient(url, api_token=tok or "")
        try:
            src_rows = await db.get_node_inbounds(active["id"])
            dst_rows = await db.get_node_inbounds(target_node["id"])
        except Exception as e:
            result["error"] += f"inbound map: {e}; "
            src_rows, dst_rows = [], []
        src_id = int(src_rows[0]["inbound_id"]) if src_rows else 0
        dst_id = int(dst_rows[0]["inbound_id"]) if dst_rows else 0
        if not src_id or not dst_id:
            result["error"] += f"inbound mapping missing (src={src_id} dst={dst_id})"
            return result

        try:
            emails = await _inbound_clients(xui, src_id)
        except Exception as e:
            result["error"] += f"list clients: {e}; "
            emails = []
        result["count"] = len(emails)

        moved = 0
        for em in emails:
            try:
                # attach first: the client is valid on both nodes meanwhile
                await xui.attach_client(em, [dst_id])
                moved += 1
            except Exception as e:
                result["error"] += f"{em}: {e}; "
        result["moved"] = moved
        result["src_inbound"] = src_id
        result["dst_inbound"] = dst_id
        logger.info("switch_active: attached %d/%d clients to inbound %s",
                    moved, len(emails), dst_id)
        result["sync"] = (moved == len(emails))

    if not result["sync"]:
        result["error"] += "re-attach failed; domain left on the current node"
        logger.warning("switch_active(%s): re-attach failed, domain not changed", target_node["id"])
        return result

    # 2) only now move the A record
    try:
        result["arvan"] = await arvan.set_a_record(target_node["ip"])
    except Exception as e:
        result["error"] += f"arvan: {e}; "

    if result["arvan"]:
        result["dns"] = await _verify_dns(target_node["ip"])
        await db.set_setting("active_node_id", str(target_node["id"]))
        # 3) the domain really points at the new node now, so the old inbound is
        #    no longer needed.  Best effort: leaving the client on both nodes is
        #    harmless, cutting it too early is not.
        if result["dns"] == target_node["ip"] and src_id and dst_id and emails:
            url, tok = await _master_panel()
            if url:
                xui = XUIClient(url, api_token=tok or "")
                done = 0
                for em in emails:
                    try:
                        await xui.detach_client(em, [src_id])
                        done += 1
                    except Exception as e:
                        result["error"] += f"detach {em}: {e}; "
                logger.info("switch_active: detached %d/%d clients from inbound %s",
                            done, len(emails), src_id)
    else:
        result["error"] += "domain not changed; active server left unchanged"
    return result
