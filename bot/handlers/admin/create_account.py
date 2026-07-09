from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramNetworkError
from urllib.parse import quote
from datetime import datetime, timedelta
import asyncio
import logging

from bot.config import ADMIN_IDS
from bot.database import db
from bot.services.xui import XUIClient, panel_sub_base
from bot.keyboards.inline import create_account_plans_keyboard, admin_menu_keyboard, cancel_keyboard

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


class CreateAccountStates(StatesGroup):
    waiting_traffic = State()
    waiting_days = State()
    waiting_users = State()
    waiting_name = State()


@router.callback_query(F.data == "admin:create_account")
async def create_account_menu(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️ دسترسی ندارید!", show_alert=True)
        return
    await state.clear()
    plans = await db.get_active_plans()
    await callback.message.edit_text(
        "🛠 ساخت اکانت\n\nیک پلن را انتخاب کنید یا «پلن دلخواه» را بزنید:",
        reply_markup=create_account_plans_keyboard(plans)
    )
    await callback.answer()


@router.callback_query(F.data == "acc_plan:custom")
async def acc_custom(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(custom=True)
    await callback.message.edit_text(
        "🎛 پلن دلخواه\n\n📊 مقدار ترافیک را به گیگابایت وارد کنید:\n(عدد، مثال: 50 — برای نامحدود 0)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(CreateAccountStates.waiting_traffic)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_plan:"))
async def acc_preset(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    plan_id = int(callback.data.split(":")[1])
    plan = await db.get_plan(plan_id)
    if not plan:
        await callback.answer("❌ پلن یافت نشد!", show_alert=True)
        return

    await state.update_data(
        custom=False,
        plan_id=plan_id,
        traffic_gb=plan["traffic_gb"],
        days=plan["duration_days"],
        users=plan["max_users"] or 0,
    )
    await callback.message.edit_text(
        f"📦 پلن: {plan['name']} | {plan['traffic_gb']}GB | {plan['duration_days']} روز\n\n"
        "📝 نام اکانت را وارد کنید (انگلیسی، بدون فاصله):",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(CreateAccountStates.waiting_name)
    await callback.answer()


@router.message(CreateAccountStates.waiting_traffic)
async def acc_traffic(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("⚠️ فقط عدد وارد کنید. مثال: 50", reply_markup=cancel_keyboard())
        return
    await state.update_data(traffic_gb=int(text))
    await message.answer(
        "📅 تعداد روز را وارد کنید:\n(عدد، مثال: 30)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(CreateAccountStates.waiting_days)


@router.message(CreateAccountStates.waiting_days)
async def acc_days(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("⚠️ فقط عدد وارد کنید. مثال: 30", reply_markup=cancel_keyboard())
        return
    await state.update_data(days=int(text))
    await message.answer(
        "👥 تعداد کاربر (محدودیت IP) را وارد کنید:\n(عدد، برای نامحدود 0)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(CreateAccountStates.waiting_users)


@router.message(CreateAccountStates.waiting_users)
async def acc_users(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("⚠️ فقط عدد وارد کنید. برای نامحدود 0", reply_markup=cancel_keyboard())
        return
    await state.update_data(users=int(text))
    await message.answer(
        "📝 نام اکانت را وارد کنید (انگلیسی، بدون فاصله):",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(CreateAccountStates.waiting_name)


@router.message(CreateAccountStates.waiting_name)
async def acc_name(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return

    name = message.text.strip().replace(" ", "_")
    if not name:
        await message.answer("⚠️ نام نامعتبر است. دوباره وارد کنید:", reply_markup=cancel_keyboard())
        return

    data = await state.get_data()
    traffic_gb = data.get("traffic_gb", 0)
    days = data.get("days", 0)
    users = data.get("users", 0)
    plan_id = data.get("plan_id")
    await state.clear()

    master = await db.get_master_server()
    if not master:
        await message.answer("❌ سرور مستر فعالی یافت نشد!", reply_markup=admin_menu_keyboard())
        return

    status_msg = await message.answer("⏳ در حال ساخت اکانت...")

    try:
        xui = XUIClient(master["url"], api_token=master["api_token"])

        reality_ids = db.parse_inbound_ids(master["inbound_ids"])
        if not reality_ids:
            inbounds = await xui.get_inbounds()
            for ib in inbounds:
                if ib.get("protocol") == "vless" and ib.get("enable", True):
                    reality_ids.append(ib["id"])
        if not reality_ids:
            raise Exception("هیچ اینباندی تنظیم نشده. از «مدیریت سرور → اینباندها» اقدام کنید")

        await xui.add_client_full(
            email=name,
            traffic_gb=traffic_gb,
            expire_days=days,
            all_inbound_ids=reality_ids,
            limit_ip=users,
        )

        sub_token = await xui.get_client_sub_id(name)
        if not sub_token:
            raise Exception("subId از پنل دریافت نشد")

        sub_url = f"{panel_sub_base(master['url'], master['sub_port'], master['sub_domain'])}/sub/{sub_token}"

        linked_note = ""
        if name.isdigit():
            target_user = await db.add_user(telegram_id=int(name))
            expire_date = datetime.now() + timedelta(days=days) if days and days > 0 else None
            await db.create_config(
                user_id=target_user["id"], order_id=None, plan_id=plan_id,
                client_email=name, sub_id=sub_token, sub_url=sub_url,
                traffic_gb=traffic_gb, expire_date=expire_date,
            )
            linked_note = f"\n👤 به کاربر {name} متصل شد (در «کانفیگ‌های من» او دیده می‌شود)."

        caption = (
            f"✅ اکانت ساخته شد!\n\n"
            f"📝 نام: {name}\n"
            f"📊 حجم: {'نامحدود' if traffic_gb == 0 else str(traffic_gb) + ' GB'}\n"
            f"📅 مدت: {'نامحدود' if days == 0 else str(days) + ' روز'}\n"
            f"👥 تعداد کاربر: {'نامحدود' if users == 0 else users}\n"
            f"🌍 لوکیشن: {master['location']}\n"
            f"{linked_note}\n"
            f"🔗 لینک اشتراک:\n`{sub_url}`"
        )
        qr_url = (
            "https://api.qrserver.com/v1/create-qr-code/"
            f"?size=500x500&qzone=2&margin=10&data={quote(sub_url, safe='')}"
        )

        try:
            await _retry(lambda: bot.send_photo(
                chat_id=message.from_user.id, photo=qr_url, caption=caption, parse_mode="Markdown"
            ))
        except Exception:
            await _retry(lambda: bot.send_message(
                chat_id=message.from_user.id, text=caption, parse_mode="Markdown"
            ))

        try:
            await status_msg.delete()
        except Exception:
            pass
        await message.answer("منوی مدیریت:", reply_markup=admin_menu_keyboard())

    except Exception as e:
        logger.exception(f"Admin account creation failed: {e}")
        try:
            await status_msg.edit_text(f"❌ خطا در ساخت اکانت:\n{str(e)[:250]}")
        except Exception:
            await message.answer(f"❌ خطا در ساخت اکانت:\n{str(e)[:250]}")
        await message.answer("منوی مدیریت:", reply_markup=admin_menu_keyboard())
