"""Rebuild users / configs / orders in the database from 3x-ui panel clients.

Used by the "پر کردن دیتابیس" admin button after a fresh deploy where the
PostgreSQL database was lost but the panels still hold every client.
"""

import json
import logging
import re
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ONE_GB = 1024 * 1024 * 1024

# Clients created from an approved order are named "{telegram_id}-order{order_id}".
EMAIL_ORDER_RE = re.compile(r"^(\d+)-order(\d+)$")
EMAIL_TG_RE = re.compile(r"^\d+$")


@dataclass
class ParsedEmail:
    kind: str  # "order" | "user" | "orphan"
    telegram_id: int = None
    order_id: int = None


def parse_email(email: str) -> ParsedEmail:
    m = EMAIL_ORDER_RE.match(email)
    if m:
        return ParsedEmail(kind="order", telegram_id=int(m.group(1)), order_id=int(m.group(2)))
    if EMAIL_TG_RE.match(email):
        return ParsedEmail(kind="user", telegram_id=int(email))
    return ParsedEmail(kind="orphan")


def traffic_bytes_to_gb(total_bytes) -> int:
    try:
        return int(total_bytes) // ONE_GB
    except (TypeError, ValueError):
        return 0


def match_plan(plans: list, traffic_gb: int, duration_days: int) -> dict:
    """Pick the plan matching a client's traffic quota (and optionally duration).

    Plans are matched on traffic_gb first (the panel has no plan name); duration
    is used as a tiebreaker when known. Active plans are preferred.
    """
    candidates = [p for p in plans if (p.get("traffic_gb") or 0) == traffic_gb]
    if not candidates:
        return None
    if duration_days and duration_days > 0:
        exact = [p for p in candidates if (p.get("duration_days") or 0) == duration_days]
        if exact:
            candidates = exact
    active = [p for p in candidates if p.get("is_active")]
    if active:
        candidates = active
    return candidates[0]


def _parse_settings(settings) -> dict:
    if isinstance(settings, str):
        try:
            settings = json.loads(settings)
        except Exception:
            return {}
    return settings if isinstance(settings, dict) else {}


def collect_clients(inbounds: list) -> list:
    """Flatten every client across all inbounds into plain dicts."""
    clients = []
    for inbound in inbounds:
        settings = _parse_settings(inbound.get("settings"))
        for c in settings.get("clients") or []:
            if not c.get("email"):
                continue
            clients.append({
                "email": c.get("email"),
                "sub_id": c.get("subId") or "",
                "uuid": c.get("id") or "",
                "enable": bool(c.get("enable", True)),
                "total_bytes": c.get("totalGB", 0) or 0,
                "expiry_time": c.get("expiryTime", 0) or 0,
                "limit_ip": c.get("limitIp", 0) or 0,
            })
    return clients


def expiry_ms_to_datetime(expiry_ms) -> datetime:
    if not expiry_ms or expiry_ms <= 0:
        return None
    return datetime.fromtimestamp(expiry_ms / 1000)


async def sync_from_panels(report) -> dict:
    """Scan every active server's panel and upsert users/configs/orders.

    report(text) is called with human-readable progress lines.
    Returns a stats dict with added/updated counts.
    """
    from bot.database import db
    from bot.services.xui import XUIClient, panel_sub_base

    stats = {
        "servers": 0,
        "errors": 0,
        "users_added": 0,
        "configs_added": 0,
        "configs_updated": 0,
        "orders_added": 0,
        "orders_updated": 0,
        "orphans": 0,
    }
    failed_servers = []

    servers = await db.get_active_servers("v2ray")
    if not servers:
        report("⚠️ سرور فعال V2Ray در دیتابیس نیست. اول سرورها را اضافه کنید.")
        return stats

    plans = await db.get_all_plans()
    placeholder = await db.add_user(telegram_id=0, username="imported")

    for server in servers:
        stats["servers"] += 1
        report(f"🖥 در حال خواندن سرور «{server['name']}»...")
        xui = XUIClient(
            server["url"],
            username=server["username"] or "",
            password=server["password"] or "",
            api_token=server["api_token"] or "",
        )
        try:
            inbounds = await xui.get_inbounds()
        except Exception as e:
            stats["errors"] += 1
            failed_servers.append(f"{server['name']} ({e})")
            report(f"  ❌ خطا در اتصال: {e}")
            continue

        clients = collect_clients(inbounds)
        report(f"  ✅ {len(clients)} کلاینت پیدا شد.")
        sub_base = panel_sub_base(server["url"], server["sub_port"], server["sub_domain"])

        for client in clients:
            email = client["email"]
            parsed = parse_email(email)
            traffic_gb = traffic_bytes_to_gb(client["total_bytes"])
            duration_days = _duration_days(client["expiry_time"])
            plan = match_plan(plans, traffic_gb, duration_days)

            if parsed.kind == "orphan":
                user_id = placeholder["id"]
                stats["orphans"] += 1
            else:
                user = await db.add_user(telegram_id=parsed.telegram_id)
                user_id = user["id"]
                stats["users_added"] += 1

            sub_url = f"{sub_base}/sub/{client['sub_id']}" if client["sub_id"] else None
            expire_date = expiry_ms_to_datetime(client["expiry_time"])

            cfg = await db.upsert_config(
                server_id=server["id"],
                user_id=user_id,
                client_email=email,
                sub_id=client["sub_id"],
                sub_url=sub_url,
                traffic_gb=traffic_gb,
                expire_date=expire_date,
                is_active=client["enable"],
                plan_id=plan["id"] if plan else None,
            )
            if cfg.get("created"):
                stats["configs_added"] += 1
            else:
                stats["configs_updated"] += 1

            if parsed.kind == "order":
                if plan is None:
                    report(
                        f"  ⚠️ سفارش #{parsed.order_id} ({email}): پلنی با ترافیک "
                        f"{traffic_gb}GB یافت نشد؛ فقط کانفیگ ذخیره شد."
                    )
                    continue
                order = await db.upsert_order(
                    order_id=parsed.order_id,
                    user_id=user_id,
                    plan_id=plan["id"],
                    config_link=sub_url,
                )
                if order.get("created"):
                    stats["orders_added"] += 1
                else:
                    stats["orders_updated"] += 1

    if failed_servers:
        report("")
        report("⚠️ سرورهای ناموفق:")
        for s in failed_servers:
            report(f"  ❌ {s}")

    report("")
    report(
        f"✅ تمام شد!\n"
        f"🖥 سرورها: {stats['servers']}\n"
        f"👤 کاربران: {stats['users_added']}\n"
        f"🔑 کانفیگ‌ها: {stats['configs_added']} جدید، {stats['configs_updated']} آپدیت\n"
        f"🧾 سفارش‌ها: {stats['orders_added']} جدید، {stats['orders_updated']} آپدیت\n"
        f"📦 اکانت‌های مستقل: {stats['orphans']}"
    )
    return stats


def _duration_days(expiry_ms) -> int:
    expire = expiry_ms_to_datetime(expiry_ms)
    if not expire:
        return 0
    return max(0, (expire - datetime.now()).days)
