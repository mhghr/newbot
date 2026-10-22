from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramNetworkError
from datetime import datetime, timedelta
import asyncio
import httpx
import logging

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
    await message.answer("ðŸ“ Ù†Ø§Ù… Ø§Ú©Ø§Ù†Øª Ø±Ø§ ÙˆØ§Ø±Ø¯ Ú©Ù†ÛŒØ¯ (Ø§Ù†Ú¯Ù„ÛŒØ³ÛŒØŒ Ø¨Ø¯ÙˆÙ† ÙØ§ØµÙ„Ù‡):", reply_markup=cancel_keyboard())
    await state.set_state(CreateAccountStates.waiting_name)


@router.callback_query(F.data == "admin:create_account")
async def create_account_menu(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("â›”ï¸ Ø¯Ø³ØªØ±Ø³ÛŒ Ù†Ø¯Ø§Ø±ÛŒØ¯!", show_alert=True)
        return
    await state.clear()
    plans = await db.get_active_plans()
    await callback.message.edit_text(
        "ðŸ›  Ø³Ø§Ø®Øª Ø§Ú©Ø§Ù†Øª\n\nÛŒÚ© Ù¾Ù„Ù† Ø±Ø§ Ø§Ù†ØªØ®Ø§Ø¨ Ú©Ù†ÛŒØ¯ ÛŒØ§ Â«Ù¾Ù„Ù† Ø¯Ù„Ø®ÙˆØ§Ù‡Â» Ø±Ø§ Ø¨Ø²Ù†ÛŒØ¯:",
        reply_markup=create_account_plans_keyboard(plans)
    )
    await callback.answer()


@router.callback_query(F.data == "acc_plan:custom")
async def acc_custom(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(custom=True, service_type="v2ray")
    await callback.message.edit_text(
        "ðŸŽ› Ù¾Ù„Ù† Ø¯Ù„Ø®ÙˆØ§Ù‡\n\n"
        "ðŸ“Š Ù…Ù‚Ø¯Ø§Ø± ØªØ±Ø§ÙÛŒÚ© Ø±Ø§ Ø¨Ù‡ Ú¯ÛŒÚ¯Ø§Ø¨Ø§ÛŒØª ÙˆØ§Ø±Ø¯ Ú©Ù†ÛŒØ¯:\n(Ø¹Ø¯Ø¯ØŒ Ù…Ø«Ø§Ù„: 50 â€” Ø¨Ø±Ø§ÛŒ Ù†Ø§Ù…Ø­Ø¯ÙˆØ¯ 0)",
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
        "ðŸŽ› Ù¾Ù„Ù† Ø¯Ù„Ø®ÙˆØ§Ù‡\n\n"
        "ðŸ“Š Ù…Ù‚Ø¯Ø§Ø± ØªØ±Ø§ÙÛŒÚ© Ø±Ø§ Ø¨Ù‡ Ú¯ÛŒÚ¯Ø§Ø¨Ø§ÛŒØª ÙˆØ§Ø±Ø¯ Ú©Ù†ÛŒØ¯:\n(Ø¹Ø¯Ø¯ØŒ Ù…Ø«Ø§Ù„: 50 â€” Ø¨Ø±Ø§ÛŒ Ù†Ø§Ù…Ø­Ø¯ÙˆØ¯ 0)",
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
        await callback.answer("âŒ Ù¾Ù„Ù† ÛŒØ§ÙØª Ù†Ø´Ø¯!", show_alert=True)
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
        await message.answer("âš ï¸ ÙÙ‚Ø· Ø¹Ø¯Ø¯ ÙˆØ§Ø±Ø¯ Ú©Ù†ÛŒØ¯. Ù…Ø«Ø§Ù„: 50", reply_markup=cancel_keyboard())
        return
    await state.update_data(traffic_gb=int(text))
    await message.answer(
        "ðŸ“… ØªØ¹Ø¯Ø§Ø¯ Ø±ÙˆØ² Ø±Ø§ ÙˆØ§Ø±Ø¯ Ú©Ù†ÛŒØ¯:\n(Ø¹Ø¯Ø¯ØŒ Ù…Ø«Ø§Ù„: 30)",
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
            await message.answer("âš ï¸ ÙÙ‚Ø· Ø¹Ø¯Ø¯ ÙˆØ§Ø±Ø¯ Ú©Ù†ÛŒØ¯. Ù…Ø«Ø§Ù„: 30", reply_markup=cancel_keyboard())
            return
        await state.update_data(days=int(text))
        await _ask_name(message, state)
    except Exception as e:
        logger.exception(f"acc_days failed: {e}")
        await message.answer(f"âŒ Ø®Ø·Ø§: {str(e)[:200]}", reply_markup=cancel_keyboard())


@router.message(CreateAccountStates.waiting_name)
async def acc_name(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return

    name = message.text.strip().replace(" ", "_")
    if not name:
        await message.answer("âš ï¸ Ù†Ø§Ù… Ù†Ø§Ù…Ø¹ØªØ¨Ø± Ø§Ø³Øª. Ø¯ÙˆØ¨Ø§Ø±Ù‡ ÙˆØ§Ø±Ø¯ Ú©Ù†ÛŒØ¯:", reply_markup=cancel_keyboard())
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
            "âŒ Ø³Ø±ÙˆØ± ÙØ¹Ø§Ù„ ÛŒØ§ Ø§ÛŒÙ†Ø¨Ø§Ù†Ø¯ Ù…Ø¹ØªØ¨Ø±ÛŒ ÛŒØ§ÙØª Ù†Ø´Ø¯. Ø§ÙˆÙ„ ÛŒÚ© Ø³Ø±ÙˆØ± Ø§Ø¶Ø§ÙÙ‡/ÙØ¹Ø§Ù„ Ú©Ù†ÛŒØ¯.",
            reply_markup=admin_menu_keyboard(),
        )
        return
    panel_url, panel_tok, active, inbound_ids = target
    node_id = active["id"] if active else None
    node_name = active["name"] if active else "-"
    sub_base = await panel_sync.sub_base_for_node(active) if active else panel_sub_base(panel_url)

    status_msg = await message.answer("â³ Ø¯Ø± Ø­Ø§Ù„ Ø³Ø§Ø®Øª Ø§Ú©Ø§Ù†Øª...")

    try:
        xui = XUIClient(panel_url, api_token=panel_tok)

        email = name
        if await xui.client_exists(email):
            email = f"{name}_{int(datetime.now().timestamp())}"
            logger.info("Client email %r already exists in panel; using %r", name, email)

        try:
            await xui.add_client_full(
                email=email,
                traffic_gb=traffic_gb,
                expire_days=days,
                all_inbound_ids=inbound_ids,
                limit_ip=users,
                timeout=120,
            )
        except httpx.ReadTimeout:
            # Attaching a client to many inbounds can be slow; the panel may
            # still have created it even though the HTTP read timed out.
            if not await xui.client_exists(email):
                raise
            logger.warning("add timed out but client %s exists; continuing", email)

        sub_token = await xui.get_client_sub_id(email)
        if not sub_token:
            raise Exception("subId Ø§Ø² Ù¾Ù†Ù„ Ø¯Ø±ÛŒØ§ÙØª Ù†Ø´Ø¯")

        link, _ = await account_view.fetch_delivery_link(
            xui, email, sub_token, True, sub_base,
        )
        if not link:
            raise Exception("Ù„ÛŒÙ†Ú© Ø§Ø´ØªØ±Ø§Ú© Ø§Ø² Ù¾Ù†Ù„ Ø¯Ø±ÛŒØ§ÙØª Ù†Ø´Ø¯")

        if name.isdigit():
            target_user = await db.add_user(telegram_id=int(name))
            linked_note = f"\nðŸ‘¤ Ø¨Ù‡ Ú©Ø§Ø±Ø¨Ø± {name} Ù…ØªØµÙ„ Ø´Ø¯ (Ø¯Ø± Â«Ú©Ø§Ù†ÙÛŒÚ¯â€ŒÙ‡Ø§ÛŒ Ù…Ù†Â» Ø§Ùˆ Ø¯ÛŒØ¯Ù‡ Ù…ÛŒâ€ŒØ´ÙˆØ¯)."
        else:
            target_user = await db.get_or_create_unlinked_user()
            linked_note = "\nðŸ—‚ Ø¯Ø± Ø¯ÛŒØªØ§Ø¨ÛŒØ³ Ø°Ø®ÛŒØ±Ù‡ Ø´Ø¯ (Ø§Ú©Ø§Ù†Øª Ù…Ø³ØªÙ‚Ù„ØŒ Ø¨Ø¯ÙˆÙ† Ú©Ø§Ø±Ø¨Ø± ØªÙ„Ú¯Ø±Ø§Ù…)."

        plan = await db.get_plan(plan_id) if plan_id else None

        expire_date = datetime.now() + timedelta(days=days) if days and days > 0 else None
        await db.create_config(
            user_id=target_user["id"], order_id=None, plan_id=plan_id,
            client_email=email, sub_id=sub_token, sub_url=link,
            traffic_gb=traffic_gb, expire_date=expire_date,
            server_id=None, node_id=node_id,
        )

        email_note = f"â„¹ï¸ Ø§ÛŒÙ…ÛŒÙ„ Ù¾Ù†Ù„: {email}\n" if email != name else ""
        header = (
            f"âœ… Ø§Ú©Ø§Ù†Øª Ø³Ø§Ø®ØªÙ‡ Ø´Ø¯!\n\n"
            f"ðŸ“ Ù†Ø§Ù…: {name}\n"
            f"{email_note}"
            f"ðŸ“¦ Ù¾Ù„Ù†: {(plan['name'] if plan else 'Ø¯Ù„Ø®ÙˆØ§Ù‡')}\n"
            f"ðŸ“Š Ø­Ø¬Ù…: {'Ù†Ø§Ù…Ø­Ø¯ÙˆØ¯' if traffic_gb == 0 else str(traffic_gb) + ' GB'}\n"
            f"ðŸ“… Ù…Ø¯Øª: {'Ù†Ø§Ù…Ø­Ø¯ÙˆØ¯' if days == 0 else str(days) + ' Ø±ÙˆØ²'}\n"
            f"ðŸ‘¥ ØªØ¹Ø¯Ø§Ø¯ Ú©Ø§Ø±Ø¨Ø±: {'Ù†Ø§Ù…Ø­Ø¯ÙˆØ¯' if users == 0 else users}\n"
            f"ðŸ–¥ Ø³Ø±ÙˆØ±: {node_name}\n"
            f"{linked_note}"
        )
        await _retry(lambda: account_view.deliver_subscription(
            bot, message.from_user.id, link, header=header
        ))

        try:
            await status_msg.delete()
        except Exception:
            pass
        await message.answer("Ù…Ù†ÙˆÛŒ Ù…Ø¯ÛŒØ±ÛŒØª:", reply_markup=admin_menu_keyboard())

    except Exception as e:
        logger.exception(f"Admin account creation failed: {e}")
        try:
            await status_msg.edit_text(f"âŒ Ø®Ø·Ø§ Ø¯Ø± Ø³Ø§Ø®Øª Ø§Ú©Ø§Ù†Øª:\n{str(e)[:250]}")
        except Exception:
            await message.answer(f"âŒ Ø®Ø·Ø§ Ø¯Ø± Ø³Ø§Ø®Øª Ø§Ú©Ø§Ù†Øª:\n{str(e)[:250]}")
        await message.answer("Ù…Ù†ÙˆÛŒ Ù…Ø¯ÛŒØ±ÛŒØª:", reply_markup=admin_menu_keyboard())
