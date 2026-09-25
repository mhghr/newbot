from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from datetime import datetime
from urllib.parse import quote

from bot.database import db
from bot.keyboards.inline import (
    my_configs_keyboard, account_detail_keyboard,
    renew_choice_keyboard, renew_plans_keyboard, back_to_menu_keyboard
)
from bot.middlewares.membership import check_membership
from bot.services.xui import XUIClient
from bot.services import wireguard as wg
from bot.utils.jalali import to_jalali
from bot.utils.helpers import format_gb
from html import escape as _esc

router = Router()

ONE_GB = 1024 * 1024 * 1024


async def _get_owned_config(telegram_id: int, config_id: int):
    configs = await db.get_configs_by_telegram_id(telegram_id)
    for c in configs:
        if c["id"] == config_id:
            return c
    return None


async def _config_traffic(config):
    """Return {used, total, remaining} for either service type.

    For WireGuard the value stored in the DB is only refreshed by the background
    reminder loop, so we merge in the live router counters (delta since the last
    stored baseline) to show an up-to-date number without double counting.
    """
    service = config.get("service_type") or "v2ray"
    if service == "wireguard":
        total = (config.get("traffic_gb") or 0) * ONE_GB
        used = config.get("used_bytes") or 0
        server = await db.get_server(config["server_id"]) if config.get("server_id") else None
        if server and config.get("wg_public_key"):
            try:
                usage = await wg.fetch_usage(server)
                peer = usage.get(config["wg_public_key"])
                if peer:
                    used, _, _ = wg.merge_usage(
                        used, config.get("wg_last_rx"), config.get("wg_last_tx"),
                        peer.get("rx", 0), peer.get("tx", 0),
                    )
            except Exception:
                pass
        remaining = max(0, total - used) if total > 0 else 0
        return {"used": used, "total": total, "remaining": remaining}

    master = await db.get_master_server("v2ray")
    if not master:
        return None
    try:
        xui = XUIClient(master["url"], api_token=master["api_token"])
        return await xui.get_client_traffic(config["client_email"])
    except Exception:
        return None


def _wg_config_text(config, server) -> str:
    endpoint = (server["wg_endpoint"] if server else None) or config.get("wg_endpoint") or ""
    port = (server["wg_port"] if server else None) or config.get("wg_port") or 51820
    server_public_key = config.get("wg_server_public_key") or (server["wg_server_public_key"] if server else "") or ""
    dns = (server["wg_dns"] if server else None) or "1.1.1.1,8.8.8.8"
    return wg.build_config_text(
        private_key=config["wg_private_key"],
        client_ip=config["wg_client_ip"],
        dns=dns,
        server_public_key=server_public_key,
        endpoint=endpoint,
        port=port,
    )


@router.callback_query(F.data == "main:my_configs")
async def my_configs(callback: CallbackQuery, bot: Bot):
    is_member = await check_membership(bot, callback.from_user.id)
    if not is_member:
        await callback.answer("⚠️ ابتدا در کانال ما عضو شوید. /start", show_alert=True)
        return

    configs = await db.get_configs_by_telegram_id(callback.from_user.id)
    if not configs:
        await callback.message.edit_text(
            "📋 شما هیچ اکانت فعالی ندارید.\nاز بخش «خرید» اقدام کنید.",
            reply_markup=back_to_menu_keyboard()
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "📋 اکانت‌های شما:\nروی هرکدام بزنید تا جزئیاتش را ببینید.",
        reply_markup=my_configs_keyboard(configs)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cfg:"))
async def view_config(callback: CallbackQuery):
    config_id = int(callback.data.split(":")[1])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return

    service = config.get("service_type") or "v2ray"
    plan_name = _esc(config.get("plan_name") or "-")

    if config["expire_date"]:
        days_str = f"{max(0, (config['expire_date'] - datetime.now()).days)} روز"
        expire_str = to_jalali(config["expire_date"])
    else:
        days_str = "نامحدود"
        expire_str = "نامحدود"

    traffic = await _config_traffic(config)
    if traffic is None:
        used_str, total_str = None, None
    else:
        used_str = format_gb(traffic["used"])
        total_str = format_gb(traffic["total"]) if traffic.get("total", 0) > 0 else None

    rtl = "\u200f"  # RTL mark: keeps every line right-aligned even when it starts with LTR text
    sep = f"{rtl}━━━━━━━━━━━━━━━━━"
    service_name = "WireGuard" if service == "wireguard" else "V2Ray"

    lines = [
        f"{rtl}🔑 <b>اکانت {service_name}</b> <code>#{config['id']}</code>",
        sep,
        f"{rtl}📦 <b>پلن:</b> {plan_name}",
        f"{rtl}📅 <b>روز باقی‌مانده:</b> {days_str}",
        f"{rtl}⏳ <b>انقضا:</b> <code>{expire_str}</code>",
        sep,
    ]
    if used_str is None:
        lines.append(f"{rtl}📊 <b>مصرف:</b> در دسترس نیست")
    else:
        lines.append(f"{rtl}📊 <b>مصرف شده:</b> <code>{used_str}</code>")
        total_text = f"<code>{total_str}</code>" if total_str else "نامحدود"
        lines.append(f"{rtl}📈 <b>حجم کل:</b> {total_text}")

    if service == "wireguard":
        lines.append(f"{rtl}🌐 <b>آی‌پی:</b> <code>{_esc(config.get('wg_client_ip') or '-')}</code>")
    else:
        sub_link = config.get("sub_url") or config.get("config_link") or "-"
        lines += [sep, f"{rtl}🔗 <b>لینک اشتراک:</b>", f"{rtl}<code>{_esc(sub_link)}</code>"]

    await callback.message.edit_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=account_detail_keyboard(config["id"], service),
    )
    await callback.answer()


@router.callback_query(F.data == "cfg_noop")
async def cfg_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("qr:"))
async def show_qr(callback: CallbackQuery, bot: Bot):
    config_id = int(callback.data.split(":")[1])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config or (config.get("service_type") or "v2ray") != "v2ray":
        await callback.answer("❌ اکانت V2Ray یافت نشد!", show_alert=True)
        return
    sub_url = config.get("sub_url") or config.get("config_link")
    if not sub_url:
        await callback.answer("❌ لینک اشتراک یافت نشد!", show_alert=True)
        return
    qr_url = (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=500x500&qzone=2&margin=10&data={quote(sub_url, safe='')}"
    )
    try:
        await bot.send_photo(
            chat_id=callback.from_user.id,
            photo=qr_url,
            caption="📷 QR کد اشتراک V2Ray\nبا اپلیکیشن خود اسکن کنید.",
        )
    except Exception:
        await callback.answer("❌ ارسال تصویر ناموفق بود.", show_alert=True)
        return
    await callback.answer("✅ ارسال شد!")


@router.callback_query(F.data.startswith("wg_resend:"))
async def wg_resend(callback: CallbackQuery, bot: Bot):
    config_id = int(callback.data.split(":")[1])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config or (config.get("service_type") or "v2ray") != "wireguard":
        await callback.answer("❌ کانفیگ WireGuard یافت نشد!", show_alert=True)
        return

    server = await db.get_server(config["server_id"]) if config["server_id"] else None
    config_text = _wg_config_text(config, server)
    filename = f"wireguard-{config.get('wg_client_ip') or config_id}.conf"

    try:
        await bot.send_document(
            chat_id=callback.from_user.id,
            document=BufferedInputFile(config_text.encode("utf-8"), filename=filename),
            caption=(
                "📄 فایل کانفیگ WireGuard شما\n"
                f"🌐 IP: `{config.get('wg_client_ip') or '-'}`"
            ),
            parse_mode="Markdown",
        )
    except Exception:
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f"📄 کانفیگ WireGuard:\n\n```\n{config_text}\n```",
            parse_mode="Markdown",
        )
    await callback.answer("✅ ارسال شد!")


@router.callback_query(F.data.startswith("renew:"))
async def renew_start(callback: CallbackQuery):
    config_id = int(callback.data.split(":")[1])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return
    await callback.message.edit_text(
        "🔄 تمدید اشتراک\n\nمی‌خواهید با همان پلن فعلی تمدید کنید یا پلن را تغییر دهید؟",
        reply_markup=renew_choice_keyboard(config_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_same:"))
async def renew_same(callback: CallbackQuery, state: FSMContext):
    from bot.handlers.buy import show_payment_and_wait
    config_id = int(callback.data.split(":")[1])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return
    plan = await db.get_plan(config["plan_id"]) if config["plan_id"] else None
    if not plan:
        await callback.answer("❌ پلن فعلی در دسترس نیست، «تغییر پلن» را انتخاب کنید.", show_alert=True)
        return
    await show_payment_and_wait(callback, state, plan, renew_config_id=config_id)
    await callback.answer()


@router.callback_query(F.data.startswith("renew_change:"))
async def renew_change(callback: CallbackQuery):
    config_id = int(callback.data.split(":")[1])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return
    plans = await db.get_active_plans()
    if not plans:
        await callback.answer("❌ پلنی موجود نیست!", show_alert=True)
        return
    await callback.message.edit_text(
        "🔀 پلن جدید را انتخاب کنید:",
        reply_markup=renew_plans_keyboard(config_id, plans)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("renew_plan:"))
async def renew_plan(callback: CallbackQuery, state: FSMContext):
    from bot.handlers.buy import show_payment_and_wait
    parts = callback.data.split(":")
    config_id = int(parts[1])
    plan_id = int(parts[2])
    config = await _get_owned_config(callback.from_user.id, config_id)
    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return
    plan = await db.get_plan(plan_id)
    if not plan:
        await callback.answer("❌ پلن یافت نشد!", show_alert=True)
        return
    await show_payment_and_wait(callback, state, plan, renew_config_id=config_id)
    await callback.answer()
