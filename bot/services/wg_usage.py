"""Multi-router WireGuard usage accounting.

A user's peer is provisioned on every entry router of a cluster (so any router
can serve the endpoint domain). RouterOS peer counters are cumulative **per
router** and reset on reboot or peer recreation, so the "last seen" counter is
kept per ``(config, server)`` in ``wg_router_usage`` and only the delta is added
to ``configs.used_bytes``.

Delta rule (per direction, independently):

    if current >= last:  delta = current - last
    else:                delta = current        # counter was reset

The very first time a ``(config, server)`` pair is seen the baseline is seeded
from the current counter (no history is counted); for the config's primary
server the previously stored ``wg_last_rx/tx`` is used to keep continuity.
"""
import logging

from bot.database import db
from bot.services import wireguard as wg

logger = logging.getLogger(__name__)


def _val(server, key):
    try:
        value = server[key]
    except Exception:
        value = None
    return value or ""


def cluster_key(server):
    return (_val(server, "wg_interface"), _val(server, "wg_server_public_key"))


def group_servers(servers):
    """Group active WireGuard routers by their shared interface + key."""
    groups = {}
    for server in servers:
        groups.setdefault(cluster_key(server), []).append(server)
    return groups


def cluster_for(server, groups):
    if not server:
        return []
    return groups.get(cluster_key(server)) or [server]


def _delta(config, server_id, base_row, rx, tx):
    if base_row is None:
        if server_id == config["server_id"]:
            prev_rx = int(config["wg_last_rx"] or 0)
            prev_tx = int(config["wg_last_tx"] or 0)
        else:
            # First time we track this router for the config: the peer was
            # created at zero and only this router's own traffic is counted,
            # so count its whole counter (not seed it to the current value,
            # which used to drop the volume used before the router joined the
            # cluster / the cluster key was fixed).
            prev_rx, prev_tx = 0, 0
    else:
        prev_rx = int(base_row["last_rx"] or 0)
        prev_tx = int(base_row["last_tx"] or 0)
    return wg.merge_usage(0, prev_rx, prev_tx, rx, tx)[0]


async def accumulate(config, cluster, usage_cache):
    """Add every router's delta to the config's used_bytes and persist baselines.

    Returns the new total. Never raises for a single router failure.
    """
    used = int(config["used_bytes"] or 0)
    public_key = config.get("wg_public_key")
    if not public_key:
        return used

    for server in cluster:
        usage = usage_cache.get(server["id"])
        peer = usage.get(public_key) if usage else None
        if not peer:
            continue
        rx = peer.get("rx", 0)
        tx = peer.get("tx", 0)
        try:
            base = await db.get_wg_router_usage(config["id"], server["id"])
            used += _delta(config, server["id"], base, rx, tx)
            await db.upsert_wg_router_usage(
                config["id"], server["id"], public_key, rx, tx
            )
        except Exception as e:
            logger.warning(
                "WG usage update failed for config %s on server %s: %s",
                config["id"], server["id"], e,
            )
    return used


async def live_used(config):
    """Fresh total for display: polled value plus live deltas (not persisted)."""
    used = int(config["used_bytes"] or 0)
    public_key = config.get("wg_public_key")
    server_id = config.get("server_id")
    if not public_key or not server_id:
        return used

    server = await db.get_server(server_id)
    if not server:
        return used
    cluster = await db.get_wg_cluster(server) or [server]
    baselines = {row["server_id"]: row for row in await db.get_wg_router_usages(config["id"])}

    for srv in cluster:
        try:
            usage = await wg.fetch_usage(srv)
        except Exception:
            continue
        peer = usage.get(public_key)
        if not peer:
            continue
        rx = peer.get("rx", 0)
        tx = peer.get("tx", 0)
        used += _delta(config, srv["id"], baselines.get(srv["id"]), rx, tx)
    return used
