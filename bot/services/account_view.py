"""Shared rendering and delivery of a V2Ray account for the user-facing
"my configs" view and the admin user-search view.

Keeping this in one place guarantees both surfaces show the same, ordered
information and deliver the config the same way.
"""
import logging
from datetime import datetime
from html import escape as _esc
from urllib.parse import quote, urlparse, parse_qs, unquote

from aiogram.types import BufferedInputFile

from bot.database import db
from bot.services.xui import XUIClient
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
    tunnel_id = config.get("tunnel_id")
    if tunnel_id:
        try:
            t = await db.get_tunnel(tunnel_id)
            if t and t.get("iran_node_id"):
                node = await db.get_node(t["iran_node_id"])
                if node:
                    from bot.services import tunnel as tunnel_svc
                    url, tok = await tunnel_svc._find_node_panel(node["ip"])
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


async def send_account_config(bot, chat_id: int, config):
    """Deliver the config: WireGuard device config, or the subscription link + QR."""
    emails = [e for e in (config.get("client_emails") or "").split(",") if e]
    if config.get("tunnel_id") or emails:
        em = emails[0] if emails else config.get("client_email")
        url, tok = await _panel_for_config(config)
        link = ""
        if url and em:
            try:
                xui = XUIClient(url, api_token=tok or "")
                ls = await xui.get_client_links(em)
                link = next((l for l in ls if str(l).startswith("wireguard://")), ls[0] if ls else "")
            except Exception:
                pass
        if not link:
            link = config.get("sub_url") or config.get("config_link") or ""
        await deliver_wireguard(bot, chat_id, link, header="✅ کانفیگ شما آماده شد")
        return

    sub_link = config.get("sub_url") or config.get("config_link")
    if not sub_link:
        await bot.send_message(chat_id=chat_id, text="❌ لینک اشتراک یافت نشد.")
        return

    await bot.send_message(
        chat_id=chat_id,
        text=f"{RTM}🔗 <b>لینک اشتراک:</b>\n{RTM}<code>{_esc(sub_link)}</code>",
        parse_mode="HTML",
    )
    qr_url = (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=500x500&qzone=2&margin=10&data={quote(sub_link, safe='')}"
    )
    try:
        await bot.send_photo(chat_id=chat_id, photo=qr_url, caption="📷 QR کد اشتراک")
    except Exception as e:
        logger.warning("V2Ray QR send failed: %s", e)


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
            link = links[0] if links else ""
        except Exception as e:
            logger.warning("subLinks fetch failed for %s: %s", email, e)
        if not link:
            try:
                link = await xui.get_sub_link(email)
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


def wg_conf_from_link(link: str) -> str:
    """Convert a 3x-ui ``wireguard://`` client link into a WireGuard .conf body."""
    u = urlparse(link)
    private_key = unquote(u.username or "")
    host = u.hostname or ""
    port = u.port or 51820
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    address = q.get("address", "")
    public_key = unquote(q.get("publickey", ""))
    keepalive = q.get("keepalive", "25")
    mtu = q.get("mtu", "1420")
    lines = ["[Interface]", f"PrivateKey = {private_key}", f"Address = {address}"]
    if mtu:
        lines.append(f"MTU = {mtu}")
    lines += [
        "",
        "[Peer]",
        f"PublicKey = {public_key}",
        f"Endpoint = {host}:{port}",
        "AllowedIPs = 0.0.0.0/0",
    ]
    if keepalive:
        lines.append(f"PersistentKeepalive = {keepalive}")
    return "\n".join(lines) + "\n"


async def deliver_wireguard(bot, chat_id: int, link: str, header: str):
    """Send the WireGuard .conf file + a QR code image of it."""
    if not link:
        await bot.send_message(chat_id=chat_id, text=f"{header}\n\n(کانفیگ وایرگارد یافت نشد)")
        return
    conf = wg_conf_from_link(link)
    qr_url = (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=500x500&qzone=2&margin=10&data={quote(conf, safe='')}"
    )
    try:
        await bot.send_document(
            chat_id=chat_id,
            document=BufferedInputFile(conf.encode("utf-8"), filename="wireguard.conf"),
            caption=header,
        )
    except Exception as e:
        logger.warning("WireGuard .conf send failed: %s", e)
        await bot.send_message(chat_id=chat_id, text=f"{header}\n\n{conf}")
    try:
        await bot.send_photo(chat_id=chat_id, photo=qr_url, caption="📷 QR کانفیگ وایرگارد")
    except Exception as e:
        logger.warning("WireGuard QR send failed: %s", e)


async def resolve_delivery(xui, email: str, sub_id: str, server, sub_base: str = ""):
    """Return ``(link, kind)`` for a fresh account.

    kind is ``"wg"`` when the server is a WireGuard server, otherwise ``"sub"``
    or ``"direct"`` (subscription vs. plain config link) depending on the
    server's subscription toggle.
    """
    if rec_get(server, "wireguard", False):
        try:
            links = await xui.get_client_links(email)
            link = next((l for l in links if str(l).startswith("wireguard://")), "")
            if not link and links:
                link = links[0]
        except Exception as e:
            logger.warning("WireGuard link fetch failed for %s: %s", email, e)
            link = ""
        return link, "wg"

    sub_enabled = bool(rec_get(server, "sub_enabled", True))
    link, is_sub = await fetch_delivery_link(xui, email, sub_id, sub_enabled, sub_base)
    return link, ("sub" if is_sub else "direct")
