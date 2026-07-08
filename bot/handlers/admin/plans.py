from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import admin_plans_keyboard, plan_actions_keyboard, cancel_keyboard

router = Router()


class AddPlanStates(StatesGroup):
    waiting_name = State()
    waiting_traffic = State()
    waiting_duration = State()
    waiting_price = State()
    waiting_users = State()


@router.callback_query(F.data == "admin:plans")
async def plans_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text("📦 مدیریت پلن‌ها:", reply_markup=admin_plans_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin:add_plan")
async def add_plan_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "📦 افزودن پلن جدید\n\nنام پلن را وارد کنید:",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AddPlanStates.waiting_name)
    await callback.answer()


@router.message(AddPlanStates.waiting_name)
async def add_plan_name(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(name=message.text.strip())
    await message.answer("📊 حجم ترافیک (گیگابایت) را وارد کنید:\n(فقط عدد)", reply_markup=cancel_keyboard())
    await state.set_state(AddPlanStates.waiting_traffic)


@router.message(AddPlanStates.waiting_traffic)
async def add_plan_traffic(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        traffic = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ لطفا فقط عدد وارد کنید:")
        return
    await state.update_data(traffic_gb=traffic)
    await message.answer("📅 مدت زمان (روز) را وارد کنید:\n(فقط عدد)", reply_markup=cancel_keyboard())
    await state.set_state(AddPlanStates.waiting_duration)


@router.message(AddPlanStates.waiting_duration)
async def add_plan_duration(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        duration = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ لطفا فقط عدد وارد کنید:")
        return
    await state.update_data(duration_days=duration)
    await message.answer("💰 قیمت (تومان) را وارد کنید:\n(فقط عدد)", reply_markup=cancel_keyboard())
    await state.set_state(AddPlanStates.waiting_price)


@router.message(AddPlanStates.waiting_price)
async def add_plan_price(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        price = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ لطفا فقط عدد وارد کنید:")
        return

    await state.update_data(price=price)
    await message.answer(
        "👥 تعداد کاربر مجاز را وارد کنید:\n"
        "(عدد؛ برای مثال 1 = تک‌کاربره، و 0 = نامحدود)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AddPlanStates.waiting_users)


@router.message(AddPlanStates.waiting_users)
async def add_plan_users(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        max_users = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ لطفا فقط عدد وارد کنید:")
        return
    if max_users < 0:
        max_users = 0

    data = await state.get_data()
    await db.add_plan(
        name=data["name"],
        traffic_gb=data["traffic_gb"],
        duration_days=data["duration_days"],
        price=data["price"],
        max_users=max_users,
    )
    users_txt = "نامحدود" if max_users == 0 else f"{max_users} کاربر"
    await message.answer(
        f"✅ پلن با موفقیت اضافه شد!\n\n"
        f"📛 نام: {data['name']}\n"
        f"📊 حجم: {data['traffic_gb']} GB\n"
        f"📅 مدت: {data['duration_days']} روز\n"
        f"👥 تعداد کاربر: {users_txt}\n"
        f"💰 قیمت: {data['price']:,} تومان",
        reply_markup=admin_plans_keyboard()
    )
    await state.clear()


@router.callback_query(F.data == "admin:list_plans")
async def list_plans(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    plans = await db.get_all_plans()
    if not plans:
        await callback.message.edit_text(
            "📋 هیچ پلنی ثبت نشده است.",
            reply_markup=admin_plans_keyboard()
        )
        await callback.answer()
        return

    buttons = []
    for p in plans:
        status = "🟢" if p["is_active"] else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {p['name']} | {p['traffic_gb']}GB | {p['duration_days']}d | {p['price']:,}T",
            callback_data=f"admin:plan_detail:{p['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:plans")])

    await callback.message.edit_text(
        "📋 لیست پلن‌ها:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:plan_detail:"))
async def plan_detail(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    plan_id = int(callback.data.split(":")[2])
    plan = await db.get_plan(plan_id)
    if not plan:
        await callback.answer("❌ پلن یافت نشد!", show_alert=True)
        return

    status = "فعال 🟢" if plan["is_active"] else "غیرفعال 🔴"
    users_txt = "نامحدود" if (plan["max_users"] or 0) == 0 else f"{plan['max_users']} کاربر"
    await callback.message.edit_text(
        f"📦 جزئیات پلن:\n\n"
        f"📛 نام: {plan['name']}\n"
        f"📊 حجم: {plan['traffic_gb']} GB\n"
        f"📅 مدت: {plan['duration_days']} روز\n"
        f"👥 تعداد کاربر: {users_txt}\n"
        f"💰 قیمت: {plan['price']:,} تومان\n"
        f"وضعیت: {status}",
        reply_markup=plan_actions_keyboard(plan_id, bool(plan["is_active"]))
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:toggle_plan:"))
async def toggle_plan(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    plan_id = int(callback.data.split(":")[2])
    await db.toggle_plan(plan_id)
    await callback.answer("✅ وضعیت پلن تغییر کرد!")

    plan = await db.get_plan(plan_id)
    status = "فعال 🟢" if plan["is_active"] else "غیرفعال 🔴"
    users_txt = "نامحدود" if (plan["max_users"] or 0) == 0 else f"{plan['max_users']} کاربر"
    await callback.message.edit_text(
        f"📦 جزئیات پلن:\n\n"
        f"📛 نام: {plan['name']}\n"
        f"📊 حجم: {plan['traffic_gb']} GB\n"
        f"📅 مدت: {plan['duration_days']} روز\n"
        f"👥 تعداد کاربر: {users_txt}\n"
        f"💰 قیمت: {plan['price']:,} تومان\n"
        f"وضعیت: {status}",
        reply_markup=plan_actions_keyboard(plan_id, bool(plan["is_active"]))
    )


@router.callback_query(F.data.startswith("admin:delete_plan:"))
async def delete_plan(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    plan_id = int(callback.data.split(":")[2])
    await db.delete_plan(plan_id)
    await callback.answer("✅ پلن حذف شد!")
    await callback.message.edit_text("📦 مدیریت پلن‌ها:", reply_markup=admin_plans_keyboard())
