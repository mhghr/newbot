from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import (
    admin_plans_keyboard, plan_actions_keyboard, plan_edit_keyboard,
    plans_list_keyboard, cancel_keyboard, service_type_keyboard, service_label,
)

router = Router()


class AddPlanStates(StatesGroup):
    waiting_service_type = State()
    waiting_name = State()
    waiting_traffic = State()
    waiting_duration = State()
    waiting_price = State()
    waiting_users = State()


class EditPlanStates(StatesGroup):
    waiting_value = State()


FIELD_LABELS = {
    "name": "نام",
    "traffic_gb": "حجم (گیگابایت)",
    "duration_days": "مدت (روز)",
    "max_users": "تعداد کاربر",
    "price": "قیمت (تومان)",
    "service_type": "نوع سرویس",
}


@router.callback_query(F.data == "admin:plans")
async def plans_menu(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    plans = await db.get_all_plans()
    if not plans:
        await callback.message.edit_text(
            "📦 هیچ پلنی ثبت نشده است.\nبرای افزودن روی دکمه زیر بزنید.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ افزودن پلن", callback_data="admin:add_plan")],
                [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")],
            ])
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "📦 مدیریت پلن‌ها\nروی هر پلن بزنید تا ویرایش/تنظیم کنید:",
        reply_markup=plans_list_keyboard(plans)
    )
    await callback.answer()


@router.callback_query(F.data == "admin:add_plan")
async def add_plan_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.clear()
    await callback.message.edit_text(
        "📦 افزودن پلن جدید\n\nابتدا نوع سرویس این پلن را انتخاب کنید:",
        reply_markup=service_type_keyboard("new_plan_type")
    )
    await state.set_state(AddPlanStates.waiting_service_type)
    await callback.answer()


@router.callback_query(F.data.startswith("new_plan_type:"))
async def add_plan_service_type(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    service_type = callback.data.split(":")[1]
    if service_type not in ("v2ray", "wireguard"):
        await callback.answer("❌ نوع نامعتبر", show_alert=True)
        return
    await state.update_data(service_type=service_type)
    await callback.message.edit_text(
        f"📦 افزودن پلن جدید ({service_label(service_type)})\n\nنام پلن را وارد کنید:",
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
        service_type=data.get("service_type", "v2ray"),
    )
    users_txt = "نامحدود" if max_users == 0 else f"{max_users} کاربر"
    plans = await db.get_all_plans()
    await message.answer(
        f"✅ پلن «{data['name']}» ({service_label(data.get('service_type'))}) اضافه شد!",
        reply_markup=plans_list_keyboard(plans)
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
        stype = "WireGuard" if p.get("service_type") == "wireguard" else "V2Ray"
        buttons.append([InlineKeyboardButton(
            text=f"{status} [{stype}] {p['name']} | {p['traffic_gb']}GB | {p['duration_days']}d | {p['price']:,}T",
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
    await callback.message.edit_text(
        f"📦 {plan['name']}  ({status})\n"
        f"🧩 نوع سرویس: {service_label(plan.get('service_type'))}\n"
        "روی هر مورد بزنید تا ویرایش شود:",
        reply_markup=plan_actions_keyboard(plan)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:edit_plan:"))
async def edit_plan_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    plan_id = int(callback.data.split(":")[2])
    plan = await db.get_plan(plan_id)
    if not plan:
        await callback.answer("❌ پلن یافت نشد!", show_alert=True)
        return

    users_txt = "نامحدود" if (plan["max_users"] or 0) == 0 else f"{plan['max_users']}"
    await callback.message.edit_text(
        f"✏️ ویرایش پلن «{plan['name']}»\n\n"
        f"🧩 نوع سرویس: {service_label(plan.get('service_type'))}\n"
        f"📊 حجم: {plan['traffic_gb']} GB\n"
        f"📅 مدت: {plan['duration_days']} روز\n"
        f"👥 تعداد کاربر: {users_txt}\n"
        f"💰 قیمت: {plan['price']:,} تومان\n\n"
        "کدام مورد را می‌خواهید ویرایش کنید؟",
        reply_markup=plan_edit_keyboard(plan_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:edit_plan_field:"))
async def edit_plan_field(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split(":")
    plan_id = int(parts[2])
    field = parts[3]
    label = FIELD_LABELS.get(field, field)

    if field == "service_type":
        await state.update_data(edit_plan_id=plan_id)
        await callback.message.edit_text(
            "🧩 نوع سرویس جدید را انتخاب کنید:",
            reply_markup=service_type_keyboard(f"edit_plan_type:{plan_id}")
        )
        await callback.answer()
        return

    await state.update_data(edit_plan_id=plan_id, edit_field=field)
    hint = ""
    if field in ("traffic_gb", "duration_days", "max_users"):
        hint = "\n(عدد؛ 0 = نامحدود)"
    elif field == "price":
        hint = "\n(فقط عدد)"
    await callback.message.edit_text(
        f"✏️ مقدار جدید برای «{label}» را وارد کنید:{hint}",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(EditPlanStates.waiting_value)
    await callback.answer()


@router.callback_query(F.data.startswith("edit_plan_type:"))
async def edit_plan_type_save(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    parts = callback.data.split(":")
    plan_id = int(parts[1])
    service_type = parts[2]
    if service_type not in ("v2ray", "wireguard"):
        await callback.answer("❌ نوع نامعتبر", show_alert=True)
        return
    await db.update_plan_field(plan_id, "service_type", service_type)
    await state.clear()
    plan = await db.get_plan(plan_id)
    await callback.message.edit_text(
        f"✅ نوع سرویس به {service_label(service_type)} تغییر کرد.\n\n"
        f"📦 {plan['name']}\nروی هر مورد بزنید تا ویرایش شود:",
        reply_markup=plan_actions_keyboard(plan)
    )
    await callback.answer("✅ ذخیره شد!")


@router.message(EditPlanStates.waiting_value)
async def edit_plan_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    plan_id = data.get("edit_plan_id")
    field = data.get("edit_field")

    value = message.text.strip()
    if field != "name":
        if not value.isdigit():
            await message.answer("⚠️ لطفا فقط عدد وارد کنید:", reply_markup=cancel_keyboard())
            return
        value = int(value)

    await db.update_plan_field(plan_id, field, value)
    await state.clear()

    plan = await db.get_plan(plan_id)
    await message.answer(
        "✅ پلن به‌روزرسانی شد!\nروی هر مورد بزنید تا ویرایش شود:",
        reply_markup=plan_actions_keyboard(plan)
    )


@router.callback_query(F.data.startswith("admin:toggle_plan:"))
async def toggle_plan(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    plan_id = int(callback.data.split(":")[2])
    await db.toggle_plan(plan_id)
    await callback.answer("✅ وضعیت پلن تغییر کرد!")

    plan = await db.get_plan(plan_id)
    status = "فعال 🟢" if plan["is_active"] else "غیرفعال 🔴"
    await callback.message.edit_text(
        f"📦 {plan['name']}  ({status})\nروی هر مورد بزنید تا ویرایش شود:",
        reply_markup=plan_actions_keyboard(plan)
    )


@router.callback_query(F.data.startswith("admin:delete_plan:"))
async def delete_plan(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    plan_id = int(callback.data.split(":")[2])
    await db.delete_plan(plan_id)
    await callback.answer("✅ پلن حذف شد!")
    plans = await db.get_all_plans()
    if plans:
        await callback.message.edit_text(
            "📦 مدیریت پلن‌ها", reply_markup=plans_list_keyboard(plans)
        )
    else:
        await callback.message.edit_text(
            "📦 هیچ پلنی ثبت نشده است.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ افزودن پلن", callback_data="admin:add_plan")],
                [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")],
            ])
        )
