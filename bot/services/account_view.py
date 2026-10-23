"""Shared rendering and delivery of a V2Ray account for the user-facing
"my configs" view and the admin user-search view.

Keeping this in one place guarantees both surfaces show the same, ordered
information and deliver the config the same way.
"""
import logging
from datetime import datetime
from html import escape as _esc
from urllib.parse import quote

from aiogram.types import BufferedInputFile

from bot.database import db
from bot.services.xui import XUIClient, panel_sub_base
from bot.utils.jalali import to_jalali
from bot.utils.helpers import format_gb

logger = logging.getLogger(__name__)

ONE_GB = 1024 * 1024 * 1024
RTM = "\u200f"  # RTL mark: keeps each line right-aligned
SEP = f"{RTM}━━━━━━━━━━━━━━━━━"


def type_label(config) -> str:
    return ""


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


async def _panel_for_config(config):
    """Resolve (url, token) of the panel that hosts this config's clients."""
    node_id = config.get("node_id")
    if node_id:
        try:
            node = await db.get_node(int(node_id))
            if node:
                from bot.services import panel_sync
                url, tok = await panel_sync._node_panel(node)
                if url:
                    return url, tok
        except Exception:
            pass
    master = await db.get_master_server("v2ray")
    if master:
        return master["url"], master.get("api_token")
    return None, None


async def traffic_summary(config) -> dict:
    """Traffic of an account = sum over all of its device clients (``client_emails``)."""
    emails = [e for e in (config.get("client_emails") or "").split(",") if e]
    if not emails:
        emails = [config.get("client_email")] if config.get("client_email") else []

    url, tok = await _panel_for_config(config)
    if url and emails:
        try:
            xui = XUIClient(url, api_token=tok or "")
            used = 0
            total = 0
            for em in emails:
                t = await xui.get_client_traffic(em)
                used += t.get("used", 0)
                total += (t.get("total") or 0)
            if total == 0:
                total = (config.get("traffic_gb") or 0) * ONE_GB
            remaining = max(0, total - used) if total > 0 else None
            return {"used": used, "total": total or None, "remaining": remaining}
        except Exception as e:
            logger.warning("traffic fetch failed for config %s: %s", config.get("id"), e)

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
    lines = [
        f"{RTM}🔑 <b>اکانت</b> <code>#{config['id']}</code>",
        SEP,
        f"{RTM}📦 <b>پلن:</b> {_esc(config.get('plan_name') or '-')}",
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


async def deliver_subscription(bot, chat_id: int, link: str,
                               header: str = "✅ لینک اشتراک شما"):
    """Send the subscription link (as text) together with its QR code."""
    if not link:
        await bot.send_message(chat_id=chat_id, text="❌ لینک اشتراک یافت نشد.")
        return
    await bot.send_message(
        chat_id=chat_id,
        text=f"{header}\n\n{RTM}🔗 <b>لینک اشتراک:</b>\n{RTM}<code>{_esc(link)}</code>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    try:
        # Generate the QR locally so delivery never depends on an external
        # image service.
        import qrcode
        from io import BytesIO

        buf = BytesIO()
        qrcode.make(link).save(buf, format="PNG")
        await bot.send_photo(
            chat_id=chat_id,
            photo=BufferedInputFile(buf.getvalue(), filename="qrcode.png"),
            caption="📷 QR لینک اشتراک",
        )
    except Exception as e:
        logger.warning("Local QR generation failed (%s); using QR service", e)
        qr_url = (
            "https://api.qrserver.com/v1/create-qr-code/"
            f"?size=500x500&qzone=2&margin=10&data={quote(link, safe='')}"
        )
        try:
            await bot.send_photo(chat_id=chat_id, photo=qr_url, caption="📷 QR لینک اشتراک")
        except Exception as e2:
            logger.warning("Subscription QR send failed: %s", e2)


async def _sub_base_for_config(config):
    node_id = config.get("node_id")
    if node_id:
        try:
            node = await db.get_node(int(node_id))
            if node:
                from bot.services import panel_sync
                return await panel_sync.sub_base_for_node(node)
        except Exception:
            pass
    url, _ = await _panel_for_config(config)
    return panel_sub_base(url) if url else ""


async def send_account_config(bot, chat_id: int, config):
    """Deliver the subscription link."""
    sub_link = config.get("sub_url") or ""
    if sub_link and not sub_link.startswith(("http://", "https://")):
        sub_link = ""
    if not sub_link:
        emails = [e for e in (config.get("client_emails") or "").split(",") if e]
        em = emails[0] if emails else config.get("client_email")
        url, tok = await _panel_for_config(config)
        if url and em:
            try:
                xui = XUIClient(url, api_token=tok or "")
                sub_id = config.get("sub_id") or em
                sub_base = await _sub_base_for_config(config)
                sub_link, _ = await fetch_delivery_link(
                    xui, em, sub_id, True, sub_base,
                )
            except Exception as e:
                logger.warning("subscription link fetch failed for %s: %s", em, e)
    if not sub_link:
        await bot.send_message(chat_id=chat_id, text="❌ لینک اشتراک یافت نشد.")
        return
    await deliver_subscription(bot, chat_id, sub_link, header="✅ لینک اشتراک شما")


def rec_get(rec, key, default=None):
    """Safe getter for an asyncpg Record (which has no ``.get``)."""
    try:
        value = rec[key]
        return default if value is None else value
    except (KeyError, TypeError, IndexError):
        return default


async def fetch_delivery_link(xui, email: str, sub_id: str, sub_enabled: bool,
                              fallback_base: str = ""):
    """Return ``(link, is_sub)`` to deliver to the user for a fresh account.

    * When the server has the subscription enabled the subscription link is
      fetched straight from the panel (so the port / path are always correct);
      ``is_sub`` is True.
    * Otherwise the first direct config link (e.g. ``vless://...``) is returned
      and ``is_sub`` is False.
    """
    if sub_enabled:
        link = ""
        try:
            links = await xui.get_sub_links(sub_id or email)
            link = next(
                (lnk for lnk in (links or [])
                 if str(lnk).startswith(("http://", "https://"))),
                "",
            )
        except Exception as e:
            logger.warning("subLinks fetch failed for %s: %s", email, e)
        if not link:
            try:
                links = await xui.get_client_links(email)
                link = next(
                    (lnk for lnk in links if str(lnk).startswith(("http://", "https://"))),
                    "",
                )
            except Exception:
                link = ""
        if not link and fallback_base:
            link = f"{fallback_base.rstrip('/')}/sub/{sub_id or email}"
        return link, True

    try:
        links = await xui.get_client_links(email)
        return (links[0] if links else ""), False
    except Exception as e:
        logger.warning("clientLinks fetch failed for %s: %s", email, e)
        return "", False


async def deliver_account(bot, chat_id: int, link: str, is_sub: bool, header: str):
    """Send the subscription link (+QR) or the direct config link to the user."""
    if not link:
        await bot.send_message(chat_id=chat_id, text=f"{header}\n\n(کانفیگ به‌زودی ارسال می‌شود)")
        return
    label = "🔗 لینک اشتراک (Subscription):" if is_sub else "🔗 کانفیگ:"
    caption = f"{header}\n\n{label}\n`{link}`"
    qr_url = (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=500x500&qzone=2&margin=10&data={quote(link, safe='')}"
    )
    try:
        await bot.send_photo(chat_id=chat_id, photo=qr_url, caption=caption, parse_mode="Markdown")
        return
    except Exception as e:
        logger.warning("delivery QR send failed: %s", e)
    await bot.send_message(chat_id=chat_id, text=caption, parse_mode="Markdown")


async def resolve_delivery(xui, email: str, sub_id: str, server, sub_base: str = ""):
    """Return the subscription ``(link, "sub")`` for a fresh account."""
    link, _ = await fetch_delivery_link(xui, email, sub_id, True, sub_base)
    return link, "sub"
