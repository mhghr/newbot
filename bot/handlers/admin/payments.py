from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramNetworkError
from urllib.parse import quote
from datetime import datetime, timedelta
import asyncio
import logging

from bot.config import ADMIN_IDS
from bot.database import db
from bot.services.xui import XUIClient, panel_sub_base
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


def _order_caption(order) -> str:
    return (
        f"\u200f🆕 سفارش #{order['id']}\n\n"
        f"\u200f👤 کاربر: {order['first_name']} (@{order['username'] or 'ندارد'})\n"
        f"\u200f🆔 آیدی: {order['telegram_id']}\n"
        f"\u200f📦 پلن: {order['plan_name']}\n"
        f"\u200f💰 مبلغ: {order['price']:,} تومان\n"
        f"\u200f🌍 لوکیشن: همه سرورهای فعال"
    )


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

    master = await db.get_master_server()
    if not master:
        await callback.answer("❌ سرور مستر فعالی یافت نشد!", show_alert=True)
        return

    await db.update_order_status(order_id, "processing")
    base_caption = _order_caption(order)

    try:
        try:
            await callback.message.edit_caption(
                caption=base_caption + "\n\n⏳ در حال ساخت کانفیگ روی همه اینباندها...",
                reply_markup=order_approval_keyboard(order_id)
            )
        except Exception:
            pass

        tg_id = order["telegram_id"]
        location = master["location"]

        xui = XUIClient(master["url"], api_token=master["api_token"])
        sub_base = panel_sub_base(master["url"], master["sub_port"])

        reality_ids = db.parse_inbound_ids(master["inbound_ids"])
        if not reality_ids:
            inbounds = await xui.get_inbounds()
            for ib in inbounds:
                if ib.get("protocol") == "vless" and ib.get("enable", True):
                    reality_ids.append(ib["id"])

        if not reality_ids:
            raise Exception("هیچ اینباندی تنظیم نشده. از «مدیریت سرور → اینباندها» اقدام کنید")

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

            try:
                await xui.delete_client(email)
            except Exception:
                pass

            await xui.add_client_full(
                email=email,
                sub_id=sub_token,
                traffic_gb=order["traffic_gb"],
                expire_days=days_from_now,
                limit_ip=plan_max_users,
                all_inbound_ids=reality_ids,
            )

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
                sub_token, sub_url, order["traffic_gb"], expire_date
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
                caption=base_caption + f"\n\n✅ تایید و برای کاربر ارسال شد | {len(reality_ids)} اینباند\n🔗 {sub_url}",
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
