"""Resolve the panel and inbounds used to create accounts.

Accounts are created on the server's own 3x-ui panel, attached to the inbound
ids the admin configured for that server.
"""
import logging
from urllib.parse import urlparse

from bot.database import db
from bot.services.xui import XUIClient, panel_sub_base

logger = logging.getLogger(__name__)


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
    return await _find_node_panel(node["ip"])


async def normalize_panel_url(url: str) -> str:
    """Upgrade an ``http://`` panel URL to ``https://`` when the panel enforces TLS."""
    url = (url or "").strip().rstrip("/")
    if not url.startswith("http://"):
        return url
    https_url = "https://" + url[len("http://"):]
    try:
        import httpx
        async with httpx.AsyncClient(verify=False, timeout=8) as client:
            resp = await client.get(f"{https_url}/", follow_redirects=False)
        if resp.status_code < 500:
            return https_url
    except Exception:
        pass
    return url


async def _find_node_panel(node_ip: str):
    """Find (url, token) of a panel reachable at the node's IP.

    Prefers the panel URL/token stored on the node; otherwise tries every
    configured panel's api_token against the node IP and returns the first
    that authenticates.
    """
    try:
        for n in await db.get_all_nodes():
            if n.get("ip") == node_ip and (n.get("panel_url") or "").strip():
                return n["panel_url"], (n.get("panel_token") or "")
    except Exception:
        pass

    base_urls = []
    for s in await db.get_all_servers():
        if s.get("api_token"):
            base_urls.append(s["url"])
    for base in base_urls:
        url = node_panel_url(base, node_ip)
        for s in await db.get_all_servers():
            tok = s.get("api_token")
            if not tok:
                continue
            try:
                xui = XUIClient(url, api_token=tok)
                data = await xui._request("GET", "/panel/api/inbounds/list")
                if isinstance(data, dict) and data.get("success"):
                    return url, tok
            except Exception:
                continue
    return None, None


async def active_node():
    """The server panel new accounts are created on (first active node)."""
    nodes = await db.get_iran_nodes()
    return nodes[0] if nodes else None


async def active_panel_inbound():
    """Return ``(url, token, inbound_id)`` for the active server.

    The inbound is taken from ``nodes.inbound_ids`` when recorded, and
    otherwise detected from the panel (first enabled inbound).
    """
    node = await active_node()
    if not node:
        return None
    url, tok = await _node_panel(node)
    if not url:
        return None
    xui = XUIClient(url, api_token=tok or "")
    configured = db.parse_inbound_ids(node.get("inbound_ids") or "")
    try:
        inbounds = await xui.get_inbounds()
    except Exception:
        inbounds = []
    existing = {int(ib["id"]) for ib in inbounds if ib.get("id") is not None}
    inbound_id = configured[0] if configured and configured[0] in existing else 0
    if not inbound_id:
        valid = [ib for ib in inbounds if ib.get("enable", True) and ib.get("id") is not None]
        valid.sort(key=lambda ib: (ib.get("protocol") or "").lower() != "vless")
        if valid:
            inbound_id = int(valid[0]["id"])
    if not inbound_id:
        return None
    return url, tok or "", inbound_id


async def active_panel_target():
    """``(url, token, node, inbound_ids)`` used to create a new client.

    Uses the active node's saved inbounds (``nodes.inbound_ids``), resolved
    against its panel. Falls back to the master server's saved inbounds and,
    as a last resort, to the old single-inbound behaviour.
    """
    node = await active_node()
    if node:
        url, tok = await _node_panel(node)
        if url:
            configured = db.parse_inbound_ids(node.get("inbound_ids") or "")
            xui = XUIClient(url, api_token=tok or "")
            try:
                ids = await xui.resolve_inbound_ids(configured)
            except Exception as e:
                logger.warning("active node inbound resolve failed: %s", e)
                ids = configured
            if ids:
                return url, tok or "", node, ids

    master = await db.get_master_server("v2ray")
    if master:
        url = master["url"]
        tok = master.get("api_token") or ""
        configured = db.parse_inbound_ids(master.get("inbound_ids") or "")
        xui = XUIClient(url, api_token=tok)
        try:
            ids = await xui.resolve_inbound_ids(configured)
        except Exception as e:
            logger.warning("master inbound resolve failed: %s", e)
            ids = configured
        if ids:
            return url, tok, None, ids

    single = await active_panel_inbound()
    if single:
        url, tok, inbound_id = single
        return url, tok, node, [inbound_id]
    return None


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
