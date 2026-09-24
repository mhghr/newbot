from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, BufferedInputFile
from aiogram.exceptions import TelegramNetworkError
from urllib.parse import quote
from datetime import datetime, timedelta
import asyncio
import logging

from bot.config import ADMIN_IDS
from bot.database import db
from bot.services.xui import XUIClient, panel_sub_base
from bot.services import wireguard as wg
from bot.utils.jalali import to_jalali
from bot.utils.helpers import admin_order_caption
from bot.keyboards.inline import order_approval_keyboard

logger = logging.getLogger(__name__)

router = Router()

_RETRYABLE = (TelegramNetworkError, ConnectionError, OSError, asyncio.TimeoutError)


async def _retry(coro_factory, attempts: int = 6, delay: float = 3.0):
    last_error = None
    for i in range(attempts):
        try:
            return await coro_factory()
        except _RETRYABLE as e:
            last_error = e
            logger.warning(f"network error (attempt {i + 1}/{attempts}): {type(e).__name__}: {e}")
            await asyncio.sleep(delay)
    if last_error:
        raise last_error


async def _send_config_to_user(bot: Bot, chat_id: int, qr_url: str, caption: str):
    try:
        await _retry(lambda: bot.send_photo(
            chat_id=chat_id, photo=qr_url, caption=caption, parse_mode="Markdown"
        ))
    except Exception as e:
        logger.warning(f"QR photo send failed, falling back to text: {type(e).__name__}: {e}")
        await _retry(lambda: bot.send_message(
            chat_id=chat_id, text=caption, parse_mode="Markdown"
        ))


def _wg_document(config_text: str, filename: str) -> BufferedInputFile:
    return BufferedInputFile(config_text.encode("utf-8"), filename=filename)


async def _send_wg_config(bot: Bot, chat_id: int, config_text: str, caption: str, client_ip: str):
    """Send the WireGuard .conf file (and QR if available) to the user."""
    filename = f"wireguard-{client_ip or 'config'}.conf"

    qr_png = None
    try:
        qr_png = wg.make_qr_png(config_text)
    except Exception as e:
        logger.warning(f"WG QR generation failed: {e}")

    sent = False
    try:
        await _retry(lambda: bot.send_document(
            chat_id=chat_id, document=_wg_document(config_text, filename),
            caption=caption,
        ))
        sent = True
    except Exception as e:
        logger.warning(f"WG document send failed, falling back to text: {e}")

    if qr_png:
        try:
            await _retry(lambda: bot.send_photo(
                chat_id=chat_id,
                photo=BufferedInputFile(qr_png, filename=f"{client_ip or 'config'}.png"),
                caption="📷 QR کانفیگ WireGuard — با اپ WireGuard اسکن کنید.",
            ))
        except Exception as e:
            logger.warning(f"WG QR send failed: {e}")

    if not sent:
        await _retry(lambda: bot.send_message(
            chat_id=chat_id,
            text=caption + f"\n\n{config_text}",
        ))


async def _resolve_wg_server(order):
    server_id = order["server_id"]
    if server_id:
        server = await db.get_server(server_id)
        if server and (server["service_type"] or "v2ray") == "wireguard":
            return server
    return await db.get_active_server_by_type("wireguard")


async def _approve_wireguard_order(callback: CallbackQuery, bot: Bot, order, base_caption: str):
    """Create or renew a WireGuard peer and deliver the config to the user."""
    order_id = order["id"]
    renew_config_id = order["renew_config_id"]
    tg_id = order["telegram_id"]
    traffic_gb = order["traffic_gb"] or 0
    duration_days = order["duration_days"] or 0

    if renew_config_id:
        config = await db.get_config(renew_config_id)
        if not config:
            raise Exception("کانفیگ برای تمدید یافت نشد")

        server = None
        if config["server_id"]:
            server = await db.get_server(config["server_id"])
        if not server:
            server = await db.get_active_server_by_type("wireguard")
        if not server:
            raise Exception("سرور WireGuard فعالی یافت نشد")

        now = datetime.now()
        if duration_days > 0:
            base = config["expire_date"] if config["expire_date"] and config["expire_date"] > now else now
            new_expire = base + timedelta(days=duration_days)
        else:
            new_expire = None

        endpoint = server["wg_endpoint"] or server["url"]
        port = server["wg_port"] or 51820
        server_public_key = config["wg_server_public_key"] or server["wg_server_public_key"] or ""
        config_text = wg.build_config_text(
            private_key=config["wg_private_key"],
            client_ip=config["wg_client_ip"],
            dns=server["wg_dns"] or "1.1.1.1,8.8.8.8",
            server_public_key=server_public_key,
            endpoint=endpoint,
            port=port,
        )

        try:
            await wg.set_peer_enabled(
                server, True, public_key=config["wg_public_key"],
                peer_id=config["wg_peer_id"], client_ip=config["wg_client_ip"],
            )
            await wg.reset_peer(
                server, public_key=config["wg_public_key"],
                peer_id=config["wg_peer_id"], client_ip=config["wg_client_ip"],
            )
        except Exception as e:
            logger.warning(f"WG renew peer update failed for config {renew_config_id}: {e}")

        await db.renew_wg_config(
            renew_config_id, order_id, order["plan_id"], traffic_gb, new_expire
        )

        caption = wg.build_delivery_caption(
            "تمدید",
            plan_name=order["plan_name"] or "",
            duration_days=duration_days,
            traffic_gb=traffic_gb,
            expiry_text=to_jalali(new_expire) if new_expire else "",
        )
        await _send_wg_config(bot, tg_id, config_text, caption, config["wg_client_ip"])
        await db.update_order_status(order_id, "approved", config["wg_client_ip"])

        try:
            await callback.message.edit_caption(
                caption=base_caption + "\n\n✅ تمدید WireGuard انجام و برای کاربر ارسال شد.",
                reply_markup=None,
            )
        except Exception:
            pass
        return

    server = await _resolve_wg_server(order)
    if not server:
        raise Exception("سرور WireGuard فعالی یافت نشد")

    result = await wg.create_account(server, tg_id)
    endpoint = server["wg_endpoint"] or server["url"]
    port = server["wg_port"] or 51820
    dns = server["wg_dns"] or "1.1.1.1,8.8.8.8"

    config_text = wg.build_config_text(
        private_key=result["private_key"],
        client_ip=result["client_ip"],
        dns=dns,
        server_public_key=result["server_public_key"],
        endpoint=endpoint,
        port=port,
    )

    expire_date = datetime.now() + timedelta(days=duration_days) if duration_days > 0 else None
    email = f"{tg_id}-order{order_id}"
    await db.create_wg_config(
        user_id=order["user_id"], order_id=order_id, plan_id=order["plan_id"],
        client_email=email, config_text=config_text, traffic_gb=traffic_gb,
        expire_date=expire_date, server_id=server["id"],
        client_ip=result["client_ip"], public_key=result["public_key"],
        private_key=result["private_key"], server_public_key=result["server_public_key"],
        endpoint=endpoint, port=port, peer_id=result["peer_id"],
    )

    caption = wg.build_delivery_caption(
        "آماده",
        plan_name=order["plan_name"] or "",
        duration_days=duration_days,
        traffic_gb=traffic_gb,
        expiry_text=to_jalali(expire_date) if expire_date else "",
    )
    await _send_wg_config(bot, tg_id, config_text, caption, result["client_ip"])
    await db.update_order_status(order_id, "approved", result["client_ip"])

    try:
        await callback.message.edit_caption(
            caption=base_caption + f"\n\n✅ اکانت WireGuard ساخته و ارسال شد\n🌐 {result['client_ip']}",
            reply_markup=None,
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("approve_order:"))
async def approve_order(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️ دسترسی ندارید!", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])
    order = await db.get_order(order_id)

    if not order:
        await callback.answer("❌ سفارش یافت نشد!", show_alert=True)
        return

    if order["status"] != "pending":
        await callback.answer(f"⚠️ این سفارش قبلا {order['status']} شده!", show_alert=True)
        return

    service_type = order["service_type"] or order["plan_service_type"] or "v2ray"

    # On renewal the existing config's protocol wins; if the user switched to a
    # plan of the other protocol, fall back to creating a brand new account.
    if order["renew_config_id"]:
        existing = await db.get_config(order["renew_config_id"])
        if existing:
            existing_type = existing.get("service_type") or "v2ray"
            if existing_type != service_type:
                order = dict(order)
                order["renew_config_id"] = None
            else:
                service_type = existing_type

    await db.update_order_status(order_id, "processing")
    base_caption = admin_order_caption(order, service_type=service_type)

    try:
        try:
            await callback.message.edit_caption(
                caption=base_caption + (
                    "\n\n⏳ در حال ساخت اکانت WireGuard روی روتر..."
                    if service_type == "wireguard"
                    else "\n\n⏳ در حال ساخت کانفیگ..."
                ),
                reply_markup=order_approval_keyboard(order_id)
            )
        except Exception:
            pass

        if service_type == "wireguard":
            await _approve_wireguard_order(callback, bot, order, base_caption)
            await callback.answer("✅ اکانت WireGuard ساخته و ارسال شد!")
            return

        master = await db.get_master_server("v2ray")
        if not master:
            raise Exception("سرور V2Ray فعالی یافت نشد")

        tg_id = order["telegram_id"]
        location = master["location"]

        xui = XUIClient(master["url"], api_token=master["api_token"])
        sub_base = panel_sub_base(master["url"], master["sub_port"], master["sub_domain"])

        reality_ids = db.parse_inbound_ids(master["inbound_ids"])
        if not reality_ids:
            inbounds = await xui.get_inbounds()
            for ib in inbounds:
                if ib.get("protocol") == "vless" and ib.get("enable", True):
                    reality_ids.append(ib["id"])

        if not reality_ids:
            raise Exception("سرور V2Ray به‌درستی تنظیم نشده. از «مدیریت سرور» اقدام کنید")

        renew_config_id = order["renew_config_id"]
        plan_max_users = order["max_users"] or 0

        if renew_config_id:
            config = await db.get_config(renew_config_id)
            if not config:
                raise Exception("کانفیگ برای تمدید یافت نشد")

            email = config["client_email"]
            sub_token = config["sub_id"]

            if order["duration_days"] and order["duration_days"] > 0:
                now = datetime.now()
                base = config["expire_date"] if config["expire_date"] and config["expire_date"] > now else now
                new_expire = base + timedelta(days=order["duration_days"])
                days_from_now = max(1, (new_expire - now).days)
            else:
                new_expire = None
                days_from_now = 0

            # Update the existing client in place instead of deleting and
            # recreating it.  Deleting first leaves the subscription link
            # dangling if the re-creation fails; an in-place update keeps the
            # same client (and therefore the same link) alive throughout.
            await xui.update_client(
                email=email,
                traffic_gb=order["traffic_gb"],
                expire_days=days_from_now,
                limit_ip=plan_max_users,
                tg_id=order["telegram_id"],
            )
            # Updating the quota does not clear the already-consumed traffic,
            # so reset it to give the user a full renewed allowance.
            if not await xui.reset_traffic(email):
                logger.warning(f"failed to reset used traffic for {email}")

            # Some panel versions drop a client's inbound attachments on
            # update; make sure the client is still attached to every inbound.
            current = set(await xui.get_client_inbounds(email))
            if current != set(reality_ids):
                if not await xui.attach_client(email, reality_ids):
                    raise Exception("بازگرداندن اینباندها پس از تمدید ناموفق بود")

            sub_url = f"{sub_base}/sub/{sub_token}"
            await db.renew_config(
                renew_config_id, order_id, order["plan_id"],
                order["traffic_gb"], new_expire
            )
            action_word = "تمدید"
        else:
            email = f"{tg_id}-order{order_id}"

            try:
                await xui.delete_client(email)
            except Exception:
                pass

            await xui.add_client_full(
                email=email,
                traffic_gb=order["traffic_gb"],
                expire_days=order["duration_days"],
                limit_ip=plan_max_users,
                all_inbound_ids=reality_ids,
            )

            sub_token = await xui.get_client_sub_id(email)
            if not sub_token:
                raise Exception("subId از پنل دریافت نشد")

            sub_url = f"{sub_base}/sub/{sub_token}"
            if order["duration_days"] and order["duration_days"] > 0:
                expire_date = datetime.now() + timedelta(days=order["duration_days"])
            else:
                expire_date = None
            await db.create_config(
                order["user_id"], order_id, order["plan_id"], email,
                sub_token, sub_url, order["traffic_gb"], expire_date,
                server_id=master["id"]
            )
            action_word = "ساخت"

        caption = (
            "✅ اشتراک شما آماده شد!\n\n"
            f"🔗 لینک اشتراک (Subscription):\n`{sub_url}`\n\n"
            "این آدرس را در نرم‌افزار خود وارد کنید یا QR بالا را اسکن کنید."
        )
        qr_url = (
            "https://api.qrserver.com/v1/create-qr-code/"
            f"?size=500x500&qzone=2&margin=10&data={quote(sub_url, safe='')}"
        )

        await _send_config_to_user(bot, order["telegram_id"], qr_url, caption)

        await db.update_order_status(order_id, "approved", sub_url)

        try:
            await callback.message.edit_caption(
                caption=base_caption + f"\n\n✅ تایید و برای کاربر ارسال شد\n🔗 {sub_url}",
                reply_markup=None,
            )
        except Exception:
            pass
        await callback.answer("✅ کانفیگ ساخته و ارسال شد!")

    except Exception as e:
        logger.exception(f"Order approval failed for #{order_id}: {e}")
        await db.update_order_status(order_id, "pending")
        try:
            await callback.message.edit_caption(
                caption=base_caption + f"\n\n❌ خطا (می‌توانید دوباره تایید کنید):\n{str(e)[:250]}",
                reply_markup=order_approval_keyboard(order_id),
            )
        except Exception:
            pass
        await callback.answer("❌ خطا! دوباره تلاش کنید.", show_alert=True)


@router.callback_query(F.data.startswith("reject_order:"))
async def reject_order(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️ دسترسی ندارید!", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])
    order = await db.get_order(order_id)

    if not order:
        await callback.answer("❌ سفارش یافت نشد!", show_alert=True)
        return

    if order["status"] != "pending":
        await callback.answer(f"⚠️ این سفارش قبلا {order['status']} شده!", show_alert=True)
        return

    await db.update_order_status(order_id, "rejected")

    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n❌ رد شد توسط ادمین"
    )

    await bot.send_message(
        chat_id=order["telegram_id"],
        text=(
            f"❌ سفارش #{order_id} رد شد.\n\n"
            "در صورت نیاز با پشتیبانی تماس بگیرید."
        ),
    )

    await callback.answer("❌ سفارش رد شد!")
