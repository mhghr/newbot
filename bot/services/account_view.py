"""Shared rendering and delivery of a V2Ray account for the user-facing
"my configs" view and the admin user-search view.

Keeping this in one place guarantees both surfaces show the same, ordered
information and deliver the config the same way.
"""
import logging
from datetime import datetime
from html import escape as _esc
from urllib.parse import quote

from bot.database import db
from bot.services.xui import XUIClient
from bot.utils.jalali import to_jalali
from bot.utils.helpers import format_gb

logger = logging.getLogger(__name__)

ONE_GB = 1024 * 1024 * 1024
RTM = "\u200f"  # RTL mark: keeps each line right-aligned
SEP = f"{RTM}━━━━━━━━━━━━━━━━━"


def type_label(config) -> str:
    return "V2Ray"


def remaining_days(expire_date):
    if not expire_date:
        return None
    if isinstance(expire_date, str):
        try:
            expire_date = datetime.fromisoformat(expire_date)
        except Exception:
            return None
    return max(0, (expire_date - datetime.now()).days)


def _fmt_dt(value) -> str:
    if not value:
        return "-"
    try:
        return to_jalali(value, with_time=True)
    except Exception:
        return str(value)


async def traffic_summary(config) -> dict:
    """Return ``{used, total, remaining}`` in bytes (``None`` = unknown, total 0 = unlimited)."""
    master = await db.get_master_server("v2ray")
    if master:
        try:
            xui = XUIClient(master["url"], api_token=master["api_token"])
            t = await xui.get_client_traffic(config["client_email"])
            total = (t.get("total") or 0) or ((config.get("traffic_gb") or 0) * ONE_GB)
            used = t.get("used", 0)
            remaining = max(0, total - used) if total > 0 else None
            return {"used": used, "total": total or None, "remaining": remaining}
        except Exception as e:
            logger.warning("v2ray traffic fetch failed for config %s: %s", config.get("id"), e)

    total = (config.get("traffic_gb") or 0) * ONE_GB
    used = config.get("used_bytes") or 0
    return {"used": used, "total": total or None, "remaining": None}


async def last_order_time(config):
    """Time of the latest purchase/renewal (order approval), else config creation."""
    oid = config.get("order_id")
    if oid:
        try:
            order = await db.get_order(oid)
        except Exception:
            order = None
        if order:
            return order.get("reviewed_at") or order.get("created_at")
    return config.get("created_at")


async def build_account_text(config) -> str:
    label = "V2Ray"

    lines = [
        f"{RTM}🔑 <b>اکانت {label}</b> <code>#{config['id']}</code>",
        SEP,
        f"{RTM}📦 <b>پلن:</b> {_esc(config.get('plan_name') or '-')}",
        f"{RTM}🔌 <b>نوع اتصال:</b> {label}",
    ]

    days = remaining_days(config.get("expire_date"))
    if config.get("expire_date"):
        lines.append(f"{RTM}📅 <b>روز باقی‌مانده:</b> {days} روز")
        lines.append(f"{RTM}⏳ <b>تاریخ انقضا:</b> <code>{_esc(_fmt_dt(config['expire_date']))}</code>")
    else:
        lines.append(f"{RTM}📅 <b>روز باقی‌مانده:</b> نامحدود")
        lines.append(f"{RTM}⏳ <b>تاریخ انقضا:</b> نامحدود")

    lines.append(f"{RTM}🧾 <b>زمان خرید/تمدید:</b> <code>{_esc(_fmt_dt(await last_order_time(config)))}</code>")
    lines.append(SEP)

    tr = await traffic_summary(config)
    used, total, remaining = tr.get("used"), tr.get("total"), tr.get("remaining")
    lines.append(f"{RTM}📊 <b>حجم مصرفی:</b> {format_gb(used) if used is not None else 'در دسترس نیست'}")
    lines.append(f"{RTM}📈 <b>حجم کل:</b> {format_gb(total) if total else 'نامحدود'}")
    if total and remaining is not None:
        lines.append(f"{RTM}📉 <b>حجم باقی‌مانده:</b> {format_gb(remaining)}")
    else:
        lines.append(f"{RTM}📉 <b>حجم باقی‌مانده:</b> {'نامحدود' if not total else 'در دسترس نیست'}")

    return "\n".join(lines)


async def send_account_config(bot, chat_id: int, config):
    """Deliver the V2Ray subscription link + QR."""
    sub_link = config.get("sub_url") or config.get("config_link")
    if not sub_link:
        await bot.send_message(chat_id=chat_id, text="❌ لینک اشتراک یافت نشد.")
        return

    await bot.send_message(
        chat_id=chat_id,
        text=f"{RTM}🔗 <b>لینک اشتراک V2Ray:</b>\n{RTM}<code>{_esc(sub_link)}</code>",
        parse_mode="HTML",
    )
    qr_url = (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=500x500&qzone=2&margin=10&data={quote(sub_link, safe='')}"
    )
    try:
        await bot.send_photo(chat_id=chat_id, photo=qr_url, caption="📷 QR کد اشتراک V2Ray")
    except Exception as e:
        logger.warning("V2Ray QR send failed: %s", e)
