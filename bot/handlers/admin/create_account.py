from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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
from bot.keyboards.inline import (
    create_account_plans_keyboard, admin_menu_keyboard, cancel_keyboard,
)

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
    waiting_name = State()


async def _ask_name(message: Message, state: FSMContext):
    await message.answer("📝 نام اکانت را وارد کنید (انگلیسی، بدون فاصله):", reply_markup=cancel_keyboard())
    await state.set_state(CreateAccountStates.waiting_name)


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
    await state.update_data(custom=True, service_type="v2ray")
    await callback.message.edit_text(
        "🎛 پلن دلخواه\n\n"
        "📊 مقدار ترافیک را به گیگابایت وارد کنید:\n(عدد، مثال: 50 — برای نامحدود 0)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(CreateAccountStates.waiting_traffic)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_custom_type:"))
async def acc_custom_type(callback: CallbackQuery, state: FSMContext):
    # Kept for keyboards sent before the service-type step was removed.
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(custom=True, service_type="v2ray")
    await callback.message.edit_text(
        "🎛 پلن دلخواه\n\n"
        "📊 مقدار ترافیک را به گیگابایت وارد کنید:\n(عدد، مثال: 50 — برای نامحدود 0)",
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
        service_type="v2ray",
    )
    await _ask_name(callback.message, state)
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
    try:
        text = (message.text or "").strip()
        if not text.isdigit():
            await message.answer("⚠️ فقط عدد وارد کنید. مثال: 30", reply_markup=cancel_keyboard())
            return
        await state.update_data(days=int(text))
        await _ask_name(message, state)
    except Exception as e:
        logger.exception(f"acc_days failed: {e}")
        await message.answer(f"❌ خطا: {str(e)[:200]}", reply_markup=cancel_keyboard())


@router.message(CreateAccountStates.waiting_name)
async def acc_name(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return

    name = message.text.strip().replace(" ", "_")
    if not name:
        await message.answer("⚠️ نام نامعتبر است. دوباره وارد کنید:", reply_markup=cancel_keyboard())
        return

    await state.update_data(name=name)
    data = await state.get_data()
    traffic_gb = data.get("traffic_gb", 0)
    days = data.get("days", 0)
    users = data.get("users", 0)
    plan_id = data.get("plan_id")
    await state.clear()

    target = await panel_sync.active_panel_target()
    if not target:
        await message.answer(
            "❌ سرور فعال یا اینباند معتبری یافت نشد. اول یک سرور اضافه/فعال کنید.",
            reply_markup=admin_menu_keyboard(),
        )
        return
    panel_url, panel_tok, active, inbound_ids = target
    node_id = active["id"] if active else None
    node_name = active["name"] if active else "-"
    sub_base = await panel_sync.sub_base_for_node(active) if active else panel_sub_base(panel_url)

    status_msg = await message.answer("⏳ در حال ساخت اکانت...")

    try:
        xui = XUIClient(panel_url, api_token=panel_tok)

        email = name
        if await xui.client_exists(email):
            email = f"{name}_{int(datetime.now().timestamp())}"
            logger.info("Client email %r already exists in panel; using %r", name, email)

        sub_id = uuid.uuid4().hex[:16]
        try:
            await xui.add_client_with_inbounds(
                email=email,
                inbound_ids=inbound_ids,
                sub_id=sub_id,
                traffic_gb=traffic_gb,
                expire_days=days,
                limit_ip=users,
                timeout=120,
            )
        except httpx.ReadTimeout:
            # Attaching a client to many inbounds can be slow; the panel may
            # still have created it even though the HTTP read timed out.
            if not await xui.client_exists(email):
                raise
            logger.warning("add timed out but client %s exists; continuing", email)

        sub_token = await xui.get_client_sub_id(email) or sub_id
        if not sub_token:
            raise Exception("subId از پنل دریافت نشد")

        link, _ = await account_view.fetch_delivery_link(
            xui, email, sub_token, True, sub_base,
        )
        if not link:
            raise Exception("لینک اشتراک از پنل دریافت نشد")

        if name.isdigit():
            deliver_chat_id = int(name)
            target_user = await db.add_user(telegram_id=deliver_chat_id)
            linked_note = f"\n👤 به کاربر {name} متصل شد (در «کانفیگ‌های من» او دیده می‌شود)."
        else:
            deliver_chat_id = message.from_user.id
            target_user = await db.get_or_create_unlinked_user()
            linked_note = "\n🗂 در دیتابیس ذخیره شد (اکانت مستقل، بدون کاربر تلگرام)."

        plan = await db.get_plan(plan_id) if plan_id else None

        expire_date = datetime.now() + timedelta(days=days) if days and days > 0 else None
        await db.create_config(
            user_id=target_user["id"], order_id=None, plan_id=plan_id,
            client_email=email, sub_id=sub_token, sub_url=link,
            traffic_gb=traffic_gb, expire_date=expire_date,
            server_id=None, node_id=node_id,
        )

        email_note = f"ℹ️ ایمیل پنل: {email}\n" if email != name else ""
        header = (
            f"✅ اکانت ساخته شد!\n\n"
            f"📝 نام: {name}\n"
            f"{email_note}"
            f"📦 پلن: {(plan['name'] if plan else 'دلخواه')}\n"
            f"📊 حجم: {'نامحدود' if traffic_gb == 0 else str(traffic_gb) + ' GB'}\n"
            f"📅 مدت: {'نامحدود' if days == 0 else str(days) + ' روز'}\n"
            f"👥 تعداد کاربر: {'نامحدود' if users == 0 else users}\n"
            f"🖥 سرور: {node_name}\n"
            f"{linked_note}"
        )
        try:
            await _retry(lambda: account_view.deliver_subscription(
                bot, deliver_chat_id, link, header=header
            ))
        except Exception as e:
            logger.warning("delivering account to %s failed: %s", deliver_chat_id, e)
            if deliver_chat_id != message.from_user.id:
                await _retry(lambda: account_view.deliver_subscription(
                    bot, message.from_user.id, link,
                    header=header + "\n\n⚠️ ارسال به کاربر ناموفق بود؛ لینک را دستی بفرستید.",
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
