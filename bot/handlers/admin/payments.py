from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramNetworkError
from datetime import datetime, timedelta
import asyncio
import httpx
import logging
import uuid

from bot.config import ADMIN_IDS
from bot.database import db
from bot.services.xui import XUIClient, panel_sub_base
from bot.services import account_view, panel_sync
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

    # A legacy renewal pointing at a non-V2Ray config is treated as a brand new
    # account (the old protocol is no longer offered).
    if order["renew_config_id"]:
        existing = await db.get_config(order["renew_config_id"])
        if existing and (existing.get("service_type") or "v2ray") != "v2ray":
            order = dict(order)
            order["renew_config_id"] = None

    await db.update_order_status(order_id, "processing")
    base_caption = admin_order_caption(order, service_type="v2ray")

    try:
        try:
            await callback.message.edit_caption(
                caption=base_caption + "\n\n⏳ در حال ساخت کانفیگ...",
                reply_markup=order_approval_keyboard(order_id)
            )
        except Exception:
            pass

        renew_config_id = order["renew_config_id"]
        plan_max_users = order["max_users"] or 0
        node_id = None

        if renew_config_id:
            config = await db.get_config(renew_config_id)
            if not config:
                raise Exception("کانفیگ برای تمدید یافت نشد")

            cfg_url, cfg_tok = await account_view._panel_for_config(config)
            if not cfg_url:
                target = await panel_sync.active_panel_target()
                if not target:
                    raise Exception("پنل کانفیگ یا سرور فعال یافت نشد")
                cfg_url, cfg_tok, _, _ = target
            panel_url, panel_tok = cfg_url, cfg_tok or ""
            active = await panel_sync.active_node()
            node_id = active["id"] if active else config.get("node_id")
            sub_base = await panel_sync.sub_base_for_node(active) if active else panel_sub_base(panel_url)
            xui = XUIClient(panel_url, api_token=panel_tok)
            email = config["client_email"]
            sub_token = config["sub_id"] or await xui.get_client_sub_id(email)

            if order["duration_days"] and order["duration_days"] > 0:
                now = datetime.now()
                # Renewal resets the period: count the plan duration from now,
                # ignoring any remaining days on the old expiry.
                new_expire = now + timedelta(days=order["duration_days"])
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
            # update. Re-attach the server's configured inbounds when needed.
            try:
                current = set(await xui.get_client_inbounds(email))
                if not current:
                    ids = []
                    if node_id:
                        node = await db.get_node(int(node_id))
                        if node:
                            ids = db.parse_inbound_ids(node.get("inbound_ids") or "")
                    if not ids:
                        ids = await xui.resolve_inbound_ids([])
                    if ids:
                        await xui.attach_client(email, ids)
            except Exception as e:
                logger.warning(f"failed to re-attach inbounds for {email}: {e}")

            link, _ = await account_view.fetch_delivery_link(
                xui, email, sub_token, True, sub_base,
            )
            await db.renew_config(
                renew_config_id, order_id, order["plan_id"],
                order["traffic_gb"], new_expire
            )
            action_word = "تمدید"
        else:
            target = await panel_sync.active_panel_target()
            if not target:
                raise Exception("سرور فعال یا اینباند معتبری یافت نشد")
            panel_url, panel_tok, active, inbound_ids = target
            node_id = active["id"] if active else None
            sub_base = await panel_sync.sub_base_for_node(active) if active else panel_sub_base(panel_url)
            xui = XUIClient(panel_url, api_token=panel_tok)
            email = f"{order['telegram_id']}-order{order_id}"

            try:
                await xui.delete_client(email)
            except Exception:
                pass

            sub_id = uuid.uuid4().hex[:16]
            try:
                await xui.add_client_with_inbounds(
                    email=email,
                    inbound_ids=inbound_ids,
                    sub_id=sub_id,
                    traffic_gb=order["traffic_gb"],
                    expire_days=order["duration_days"],
                    limit_ip=plan_max_users,
                    timeout=120,
                )
            except httpx.ReadTimeout:
                if not await xui.client_exists(email):
                    raise
                logger.warning("add timed out but client %s exists; continuing", email)

            sub_token = await xui.get_client_sub_id(email) or sub_id
            if not sub_token:
                raise Exception("subId از پنل دریافت نشد")

            link, _ = await account_view.fetch_delivery_link(
                xui, email, sub_token, True, sub_base,
            )
            if order["duration_days"] and order["duration_days"] > 0:
                expire_date = datetime.now() + timedelta(days=order["duration_days"])
            else:
                expire_date = None
            await db.create_config(
                order["user_id"], order_id, order["plan_id"], email,
                sub_token, link, order["traffic_gb"], expire_date,
                server_id=None, node_id=node_id,
            )
            action_word = "ساخت"

        if not link:
            raise Exception("لینک اشتراک از پنل دریافت نشد")

        delivered = True
        try:
            await account_view.deliver_subscription(
                bot, order["telegram_id"], link,
                header=(
                    f"✅ {action_word} اشتراک شما انجام شد!\n"
                    f"📦 پلن: {order['plan_name']}"
                ),
            )
        except Exception as e:
            delivered = False
            logger.warning("delivering order #%s to user %s failed: %s",
                           order_id, order["telegram_id"], e)

        await db.update_order_status(order_id, "approved", link)

        try:
            if delivered:
                result_caption = base_caption + f"\n\n✅ تایید و برای کاربر ارسال شد\n🔗 {link}"
            else:
                result_caption = (
                    base_caption
                    + f"\n\n⚠️ کانفیگ ساخته شد ولی ارسال به کاربر ناموفق بود\n🔗 {link}"
                )
            await callback.message.edit_caption(
                caption=result_caption,
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
