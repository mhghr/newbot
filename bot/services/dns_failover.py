"""Automatic DNS failover between WireGuard entry routers (ArvanCloud CDN API).

The endpoint domain used by WireGuard clients is read from the routers'
``servers.wg_endpoint`` column (a shared domain, not an IP). When the router the
domain currently points to stops serving (SSH down, tunnel to the exit router
unhealthy, or users' path to the internet broken) the A record is moved to a
healthy router.

Health is judged from the router's own point of view (the bot has no access to
the exit router): SSH reachability, freshness of the upstream WireGuard
handshake, tunnel ping loss and an end-to-end ping through the users' routing
table. Switching requires several consecutive unhealthy checks and a minimum
interval to avoid flapping.
"""
import asyncio
import ipaddress
import json
import logging
import socket
import time

import aiohttp

from bot.config import (
    ADMIN_IDS,
    ARVAN_API_BASE,
    ARVAN_API_KEY,
    DNS_FAILOVER_ENABLED,
    DNS_FAILOVER_DOMAIN,
    DNS_FAILOVER_HANDSHAKE_MAX,
    DNS_FAILOVER_INTERVAL,
    DNS_FAILOVER_LOSS_DEGRADED,
    DNS_FAILOVER_LOSS_UNHEALTHY,
    DNS_FAILOVER_MIN_SWITCH_INTERVAL,
    DNS_FAILOVER_RTT_DEGRADED,
    DNS_FAILOVER_STREAK,
    DNS_FAILOVER_TTL,
)
from bot.database import db
from bot.services import wireguard as wg
from bot.services import wg_usage

logger = logging.getLogger(__name__)

TTL_ENUM = {120, 180, 300, 600, 900, 1800, 3600, 7200, 18000, 43200, 86400, 172800, 432000}
UNHEALTHY_STREAK = DNS_FAILOVER_STREAK
MIN_SWITCH_INTERVAL = DNS_FAILOVER_MIN_SWITCH_INTERVAL
_STATE: dict = {}
_HEALTH_STATE: dict = {}


class ArvanError(Exception):
    pass


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


class ArvanDNS:
    def __init__(self, api_key: str, base: str = ARVAN_API_BASE):
        self.api_key = api_key
        self.base = (base or ARVAN_API_BASE).rstrip("/")

    def _headers(self):
        return {
            "Authorization": f"apikey {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, payload=None):
        url = f"{self.base}{path}"
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method, url, headers=self._headers(), json=payload
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise ArvanError(f"HTTP {resp.status}: {text[:300]}")
                if not text:
                    return {}
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {}

    async def list_records(self, domain: str, record_type: str = "a"):
        query = f"?type={record_type}" if record_type else ""
        data = await self._request("GET", f"/domains/{domain}/dns-records{query}")
        return data.get("data") or []

    async def update_record(self, domain: str, record_id: str, payload: dict):
        return await self._request(
            "PUT", f"/domains/{domain}/dns-records/{record_id}", payload
        )


def _endpoint_domain(servers):
    if DNS_FAILOVER_DOMAIN:
        return DNS_FAILOVER_DOMAIN.strip()
    for server in servers:
        endpoint = (server["wg_endpoint"] or "").strip()
        host = endpoint.split(":")[0]
        if host and not _is_ip(host):
            return host
    return None


def _public_ip(server):
    host = (server["url"] or "").strip()
    if not host:
        return None
    if _is_ip(host):
        return host
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def _record_payload(record, new_ip):
    ttl = record.get("ttl") or DNS_FAILOVER_TTL
    if ttl not in TTL_ENUM:
        ttl = DNS_FAILOVER_TTL if DNS_FAILOVER_TTL in TTL_ENUM else 120
    payload = {
        "type": "a",
        "name": record.get("name") or "@",
        "ttl": ttl,
        "cloud": bool(record.get("cloud", False)),
        "value": [{"ip": new_ip}],
    }
    if record.get("ip_filter_mode"):
        payload["ip_filter_mode"] = record["ip_filter_mode"]
    if record.get("upstream_https"):
        payload["upstream_https"] = record["upstream_https"]
    return payload


async def _find_record(arvan: ArvanDNS, hostname: str):
    """Return ``(zone, record)`` for the A record behind *hostname*."""
    labels = hostname.strip(".").split(".")
    for i in range(0, len(labels) - 1):
        zone = ".".join(labels[i:])
        try:
            records = await arvan.list_records(zone, "a")
        except ArvanError as e:
            logger.debug("Arvan zone probe %s failed: %s", zone, e)
            continue
        relative = ".".join(labels[:i]) if i > 0 else "@"
        for record in records:
            name = (record.get("name") or "").strip()
            if name in (relative, hostname) or (relative == "@" and name in ("", "@")):
                return zone, record
        if len(records) == 1:
            return zone, records[0]
    return None, None


async def _notify(bot, text: str):
    if bot is None:
        return
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except Exception as e:
            logger.warning("DNS failover notify to %s failed: %s", admin_id, e)


async def _notify_health_changes(bot, cluster, health, ip_of, active_ids):
    """Notify admins when any router (active or backup) changes state."""
    for server in cluster:
        sid = server["id"]
        metrics = health.get(sid) or {}
        if "healthy" not in metrics:
            continue
        now_state = (bool(metrics.get("healthy")), bool(metrics.get("degraded")))
        prev_state = _HEALTH_STATE.get(sid)
        if prev_state is None:
            _HEALTH_STATE[sid] = now_state
            continue
        was_h, was_d = prev_state
        now_h, now_d = now_state
        role = _role_label(sid, active_ids)
        label = server.get("name") or str(sid)
        ip = ip_of.get(sid) or "?"
        reason = metrics.get("reason") or "نامشخص"
        if was_h and not now_h:
            await _notify(bot, f"🔴 قطع شدن ارتباط با {role}\n🖥 {label} ({ip})\n📉 دلیل: {reason}")
        elif (not was_h) and now_h:
            extra = f"\n⚠️ کیفیت: {reason}" if now_d else ""
            await _notify(bot, f"🟢 برقراری ارتباط با {role}\n🖥 {label} ({ip}){extra}")
        elif now_h and not was_d and now_d:
            await _notify(bot, f"🟠 اختلال در {role}\n🖥 {label} ({ip}) — {reason}")
        elif now_h and was_d and not now_d:
            await _notify(bot, f"🟢 رفع اختلال {role}\n🖥 {label} ({ip})")
        _HEALTH_STATE[sid] = now_state


def _role_label(server_id, active_ids):
    return "روتر فعال" if server_id in active_ids else "روتر بک‌آپ"


def _server_label(cluster, server_id, ip_of):
    for s in cluster:
        if s["id"] == server_id:
            return s["name"] or str(server_id)
    return str(server_id)


def _health_line(metrics, ip):
    if not metrics:
        return f"{ip or '?'} (بدون داده)"
    loss = metrics.get("loss")
    rtt = metrics.get("rtt")
    bits = []
    if loss is not None:
        bits.append(f"loss {loss}%")
    if rtt is not None:
        bits.append(f"rtt {rtt:.0f}ms")
    detail = "، ".join(bits) if bits else "بدون داده"
    reason = metrics.get("reason") or ""
    return f"{ip or '?'} ({detail}{'، ' + reason if reason else ''})"


def _router_line(server, metrics, ip, active_ids):
    sid = server["id"]
    role = _role_label(sid, active_ids)
    name = server.get("name") or str(sid)
    if not metrics or "healthy" not in metrics:
        icon, detail = "⚪️", "بدون داده"
    elif not metrics.get("healthy"):
        icon, detail = "🔴", (metrics.get("reason") or "نامشخص")
    elif metrics.get("degraded"):
        icon, detail = "🟠", (metrics.get("reason") or "کاهش کیفیت")
    else:
        icon, detail = "🟢", "سالم"
    return f"{icon} {role} | {name} ({ip or '?'}) — {detail}"


def _routers_block(cluster, health, ip_of, active_ids):
    return "\n".join(
        _router_line(s, health.get(s["id"]), ip_of.get(s["id"]), active_ids) for s in cluster
    )


async def get_active_router_ids(servers=None):
    """IDs of the entry routers the shared endpoint domain currently points to.

    Used by the admin panel to label the router that is serving users right now
    ("روتر فعال") versus the standby ones ("روتر بک‌آپ")."""
    if servers is None:
        servers = await db.get_active_wg_servers()
    active = set()
    for cluster in wg_usage.group_servers(servers).values():
        if len(cluster) < 2:
            continue
        domain = _endpoint_domain(cluster)
        if not domain:
            continue
        try:
            ip = socket.gethostbyname(domain)
        except OSError:
            continue
        for server in cluster:
            if _public_ip(server) == ip:
                active.add(server["id"])
    return active


async def _check_cluster(bot, arvan: ArvanDNS, cluster):
    domain = _endpoint_domain(cluster)
    if not domain:
        return

    ip_of = {s["id"]: _public_ip(s) for s in cluster}
    health = {}
    for server in cluster:
        health[server["id"]] = await wg.check_health(
            server,
            handshake_max=DNS_FAILOVER_HANDSHAKE_MAX,
            loss_degraded=DNS_FAILOVER_LOSS_DEGRADED,
            rtt_degraded=DNS_FAILOVER_RTT_DEGRADED,
            loss_unhealthy=DNS_FAILOVER_LOSS_UNHEALTHY,
        )

    lookup_failed = False
    try:
        zone, record = await _find_record(arvan, domain)
    except Exception as e:
        logger.warning("Arvan lookup failed for %s: %s", domain, e)
        lookup_failed = True
        zone, record = None, None

    current_ips = [
        v.get("ip") for v in ((record or {}).get("value") or [])
        if isinstance(v, dict) and v.get("ip")
    ]
    active_ids = [sid for sid, ip in ip_of.items() if ip and ip in current_ips]

    # Report per-router health (active vs backup) before deciding on a switch.
    await _notify_health_changes(bot, cluster, health, ip_of, active_ids)

    if lookup_failed or not record or not record.get("id"):
        if not lookup_failed:
            logger.warning("DNS failover: no A record found for %s", domain)
        return

    state = _STATE.setdefault(domain, {"streak": 0, "last_switch": 0.0, "alerted": False})
    now = time.time()

    def _is_good(sid):
        m = health.get(sid, {})
        return bool(m.get("healthy") and not m.get("degraded"))

    def _is_unhealthy(sid):
        return not health.get(sid, {}).get("healthy")

    # Active router is fine: nothing to do.
    if any(_is_good(sid) for sid in active_ids):
        state["streak"] = 0
        state["alerted"] = False
        return

    # Active router is down or degraded (slow / lossy).
    state["streak"] += 1
    active_reason = ", ".join(
        sorted({(health.get(sid, {}).get("reason") or "unknown") for sid in active_ids})
    )
    logger.info(
        "DNS failover: %s active=%s not-good (streak %d/%d) reason=%s",
        domain, active_ids, state["streak"], UNHEALTHY_STREAK, active_reason,
    )
    if state["streak"] < UNHEALTHY_STREAK:
        return

    candidates = [
        sid for sid in health
        if _is_good(sid) and ip_of.get(sid) and ip_of[sid] not in current_ips
    ]
    if not candidates:
        if any(_is_unhealthy(sid) for sid in active_ids) and not state["alerted"]:
            await _notify(
                bot,
                "⚠️ هیچ روتر سالمی برای دامنه پیدا نشد؛ سوییچ انجام نشد.\n"
                f"🌐 دامنه: {domain}\n"
                f"{_routers_block(cluster, health, ip_of, active_ids)}",
            )
            state["alerted"] = True
        return

    if now - state["last_switch"] < MIN_SWITCH_INTERVAL:
        return

    # Prefer the candidate with the lowest loss, then lowest latency.
    target = min(
        candidates,
        key=lambda sid: (
            (health.get(sid, {}).get("loss") if health.get(sid, {}).get("loss") is not None else 999),
            (health.get(sid, {}).get("rtt") if health.get(sid, {}).get("rtt") is not None else 1e9),
        ),
    )
    new_ip = ip_of[target]
    try:
        await arvan.update_record(zone, record["id"], _record_payload(record, new_ip))
    except Exception as e:
        logger.error("DNS update failed for %s -> %s: %s", domain, new_ip, e)
        return

    old_lines = "; ".join(
        _health_line(health.get(sid), ip_of.get(sid)) for sid in active_ids
    ) or "نامشخص"
    new_metrics = health.get(target, {})
    state.update({"last_switch": now, "streak": 0, "alerted": False})
    logger.info("DNS failover applied: %s -> %s (%s)", domain, new_ip, active_reason)
    await _notify(
        bot,
        "🔀 سوییچ روتر انجام شد\n"
        f"🌐 دامنه: {domain}\n"
        f"➡️ از: {old_lines}\n"
        f"✅ به: {_server_label(cluster, target, ip_of)} — {_health_line(new_metrics, new_ip)}\n"
        f"📉 دلیل: {active_reason}",
    )


async def _check_once(bot, arvan: ArvanDNS):
    servers = await db.get_active_wg_servers()
    groups = wg_usage.group_servers(servers)
    for cluster in groups.values():
        if len(cluster) < 2:
            continue
        try:
            await _check_cluster(bot, arvan, cluster)
        except Exception as e:
            logger.error("DNS failover check crashed: %s: %s", type(e).__name__, e)


async def _loop(bot):
    arvan = ArvanDNS(ARVAN_API_KEY)
    logger.info("DNS failover scheduler started (interval=%ss)", DNS_FAILOVER_INTERVAL)
    while True:
        try:
            await _check_once(bot, arvan)
        except Exception as e:
            logger.error("DNS failover loop error: %s: %s", type(e).__name__, e)
        await asyncio.sleep(DNS_FAILOVER_INTERVAL)


def start_dns_failover(bot):
    if not DNS_FAILOVER_ENABLED:
        logger.info("DNS failover disabled (set ARVAN_API_KEY / DNS_FAILOVER_ENABLED)")
        return None
    if not ARVAN_API_KEY:
        logger.info("DNS failover disabled: ARVAN_API_KEY is empty")
        return None
    return asyncio.create_task(_loop(bot))
