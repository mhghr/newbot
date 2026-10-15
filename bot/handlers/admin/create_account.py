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
from bot.services import account_view, panel_sync
from bot.keyboards.inline import (
    create_account_plans_keyboard, admin_menu_keyboard, cancel_keyboard, locations_keyboard,
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
    waiting_users = State()
    waiting_name = State()


async def _ask_name(message: Message, state: FSMContext):
    await message.answer("📝 نام اکانت را وارد کنید (انگلیسی، بدون فاصله):", reply_markup=cancel_keyboard())
    await state.set_state(CreateAccountStates.waiting_name)


@router.callback_query(F.data.startswith("acc_loc:"))
async def acc_loc(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        return
    loc_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    await state.clear()
    await callback.answer("⏳ در حال ساخت...")
    await _create_location_account(
        callback.message, bot, callback.from_user.id,
        data.get("name") or "user", data.get("traffic_gb", 0),
        data.get("days", 0), data.get("users", 0), data.get("plan_id"), loc_id,
    )


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
        await message.answer(
            "👥 تعداد کاربر (محدودیت IP) را وارد کنید:\n(عدد، برای نامحدود 0)",
            reply_markup=cancel_keyboard()
        )
        await state.set_state(CreateAccountStates.waiting_users)
    except Exception as e:
        logger.exception(f"acc_days failed: {e}")
        await message.answer(f"❌ خطا: {str(e)[:200]}", reply_markup=cancel_keyboard())


@router.message(CreateAccountStates.waiting_users)
async def acc_users(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("⚠️ فقط عدد وارد کنید. برای نامحدود 0", reply_markup=cancel_keyboard())
        return
    await state.update_data(users=int(text))
    await _ask_name(message, state)


async def _create_location_account(message: Message, bot: Bot, chat_id: int, name, traffic_gb, days, users, plan_id, tunnel_id):
    from bot.services import tunnel as tunnel_svc
    tunnel = await db.get_tunnel(tunnel_id)
    node = await db.get_node(tunnel["iran_node_id"]) if (tunnel and tunnel["iran_node_id"]) else None
    if not tunnel or not tunnel.get("inbound_id") or not node:
        await message.answer("❌ لوکیشن/سرور یافت نشد.", reply_markup=admin_menu_keyboard())
        return
    node_url, node_tok = await tunnel_svc._find_node_panel(node["ip"])
    if not node_url:
        panel = await db.get_master_server("v2ray")
        if panel:
            node_url = panel_sync.node_panel_url(panel["url"], node["ip"])
            node_tok = panel.get("api_token")
    if not node_url:
        await message.answer("❌ پنلِ این نود پیدا نشد.", reply_markup=admin_menu_keyboard())
        return
    status = await message.answer("⏳ در حال ساخت اکانت...")
    try:
        xui = XUIClient(node_url, api_token=node_tok or "")
        email = name
        if await xui.client_exists(email):
            email = f"{name}_{int(datetime.now().timestamp())}"
        await xui.add_client_full(
            email=email, traffic_gb=traffic_gb, expire_days=days,
            limit_ip=users, all_inbound_ids=[tunnel["inbound_id"]],
        )
        link = ""
        try:
            links = await xui.get_client_links(email)
            link = next((l for l in links if str(l).startswith("wireguard://")), links[0] if links else "")
        except Exception:
            pass
        if name.isdigit():
            target = await db.add_user(telegram_id=int(name))
            note = f"\n👤 متصل به کاربر {name}"
        else:
            target = await db.get_or_create_unlinked_user()
            note = "\n🗂 ذخیره شد (بدون کاربر تلگرام)"
        expire_date = datetime.now() + timedelta(days=days) if days and days > 0 else None
        await db.create_config(
            target["id"], None, plan_id, email, email, link,
            traffic_gb, expire_date, server_id=None, tunnel_id=tunnel_id,
        )
        await account_view.deliver_wireguard(
            bot, chat_id, link,
            header=f"✅ اکانت ساخته شد\n📝 {name}\n📍 لوکیشن: {tunnel.get('location') or tunnel_id}{note}",
        )
        try:
            await status.delete()
        except Exception:
            pass
        await message.answer("منوی مدیریت:", reply_markup=admin_menu_keyboard())
    except Exception as e:
        logger.exception("location account creation failed")
        await message.answer(f"❌ خطا: {str(e)[:200]}", reply_markup=admin_menu_keyboard())


@router.message(CreateAccountStates.waiting_name)
async def acc_name(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return

    name = message.text.strip().replace(" ", "_")
    if not name:
        await message.answer("⚠️ نام نامعتبر است. دوباره وارد کنید:", reply_markup=cancel_keyboard())
        return

    await state.update_data(name=name)
    locations = await db.get_locations()
    if locations:
        await state.set_state(None)
        await message.answer("📍 لوکیشن را انتخاب کنید:", reply_markup=locations_keyboard(locations))
        return

    data = await state.get_data()
    traffic_gb = data.get("traffic_gb", 0)
    days = data.get("days", 0)
    users = data.get("users", 0)
    plan_id = data.get("plan_id")
    await state.clear()

    master = await db.get_server_for_plan(plan_id, "v2ray")
    if not master:
        await message.answer("❌ سرور فعالی یافت نشد!", reply_markup=admin_menu_keyboard())
        return

    status_msg = await message.answer("⏳ در حال ساخت اکانت...")

    try:
        xui = XUIClient(master["url"], api_token=master["api_token"])

        reality_ids = await xui.resolve_inbound_ids(db.parse_inbound_ids(master["inbound_ids"]))
        if not reality_ids:
            raise Exception("هیچ اینباندی تنظیم نشده. از «مدیریت سرور → اینباندها» اقدام کنید")

        email = name
        if await xui.client_exists(email):
            email = f"{name}_{int(datetime.now().timestamp())}"
            logger.info("Client email %r already exists in panel; using %r", name, email)

        await xui.add_client_full(
            email=email,
            traffic_gb=traffic_gb,
            expire_days=days,
            all_inbound_ids=reality_ids,
            limit_ip=users,
        )

        sub_token = await xui.get_client_sub_id(email)
        if not sub_token:
            raise Exception("subId از پنل دریافت نشد")

        link, kind = await account_view.resolve_delivery(
            xui, email, sub_token, master,
            panel_sub_base(master["url"], master["sub_port"], master["sub_domain"]),
        )

        if name.isdigit():
            target_user = await db.add_user(telegram_id=int(name))
            linked_note = f"\n👤 به کاربر {name} متصل شد (در «کانفیگ‌های من» او دیده می‌شود)."
        else:
            target_user = await db.get_or_create_unlinked_user()
            linked_note = "\n🗂 در دیتابیس ذخیره شد (اکانت مستقل، بدون کاربر تلگرام)."

        expire_date = datetime.now() + timedelta(days=days) if days and days > 0 else None
        await db.create_config(
            user_id=target_user["id"], order_id=None, plan_id=plan_id,
            client_email=email, sub_id=sub_token, sub_url=(link if kind == "sub" else ""),
            traffic_gb=traffic_gb, expire_date=expire_date,
            server_id=master["id"],
        )

        email_note = f"ℹ️ ایمیل پنل: {email}\n" if email != name else ""
        header = (
            f"✅ اکانت ساخته شد!\n\n"
            f"📝 نام: {name}\n"
            f"{email_note}"
            f"📊 حجم: {'نامحدود' if traffic_gb == 0 else str(traffic_gb) + ' GB'}\n"
            f"📅 مدت: {'نامحدود' if days == 0 else str(days) + ' روز'}\n"
            f"👥 تعداد کاربر: {'نامحدود' if users == 0 else users}\n"
            f"🌍 لوکیشن: {master['location']}\n"
            f"{linked_note}"
        )
        if kind == "wg":
            await _retry(lambda: account_view.deliver_wireguard(
                bot, message.from_user.id, link, header=header
            ))
        else:
            await _retry(lambda: account_view.deliver_account(
                bot, message.from_user.id, link, (kind == "sub"), header=header
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
