"""Keep the identical Iran panels in sync and switch the entry domain (Arvan)."""
import logging
from urllib.parse import urlparse

from bot.database import db
from bot.services.xui import XUIClient
from bot.services import arvan

logger = logging.getLogger(__name__)


def node_panel_url(panel_url: str, node_ip: str) -> str:
    """Panel URL targeting a specific node's IP (same scheme/port/path)."""
    u = urlparse(panel_url if "://" in panel_url else "https://" + panel_url)
    scheme = u.scheme or "https"
    port = u.port or (443 if scheme == "https" else 80)
    path = (u.path or "").rstrip("/")
    return f"{scheme}://{node_ip}:{port}{path}"


def _panel(panel_rec):
    return XUIClient(panel_rec["url"], api_token=panel_rec.get("api_token") or "")


async def export_active(panel_rec) -> dict:
    return await _panel(panel_rec).export_clients()


async def import_to_node(panel_rec, node) -> bool:
    url = node_panel_url(panel_rec["url"], node["ip"])
    xui = XUIClient(url, api_token=panel_rec.get("api_token") or "")
    payload = await export_active(panel_rec)
    return await xui.import_clients(payload)


async def switch_active(panel_rec, node) -> dict:
    """Point the entry domain at ``node`` (Arvan) and sync clients to that node."""
    result = {"arvan": False, "sync": False, "error": ""}
    try:
        result["arvan"] = await arvan.set_a_record(node["ip"])
    except Exception as e:
        result["error"] += f"arvan: {e}; "
    try:
        result["sync"] = await import_to_node(panel_rec, node)
    except Exception as e:
        result["error"] += f"sync: {e}; "
    if result["sync"]:
        await db.set_setting("active_node_id", str(node["id"]))
    return result


async def sync_standby(panel_rec) -> list:
    """Export the active panel and import into every other Iran node."""
    active_id = await db.get_setting("active_node_id", "")
    nodes = await db.get_iran_nodes()
    out = []
    for n in nodes:
        if str(n["id"]) == str(active_id):
            continue
        try:
            out.append((n["name"], await import_to_node(panel_rec, n)))
        except Exception as e:
            out.append((n["name"], f"error: {e}"))
    return out
