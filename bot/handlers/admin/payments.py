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


async def _approve_location(bot: Bot, callback: CallbackQuery, order, base_caption: str):
    """Approve an order that targets a location (WireGuard inbound on an Iran node)."""
    order_id = order["id"]
    try:
        tunnel = await db.get_tunnel(order["tunnel_id"])
        if not tunnel:
            raise Exception("لوکیشن یافت نشد")

        # Create the client on the ACTIVE Iran node (the entry domain points
        # there), so the peer is reachable immediately.
        active = await panel_sync.active_node()
        node = active or (await db.get_node(tunnel["iran_node_id"]) if tunnel["iran_node_id"] else None)
        if not node:
            raise Exception("سرور فعال یافت نشد")

        inbound_id = None
        try:
            mapping = await db.get_tunnel_inbound_on_node(node["id"], tunnel["id"])
            if mapping:
                inbound_id = mapping["inbound_id"]
        except Exception:
            inbound_id = None
        if not inbound_id and tunnel["iran_node_id"] and node["id"] == tunnel["iran_node_id"]:
            inbound_id = tunnel.get("inbound_id")
        if not inbound_id:
            raise Exception("اینباند این لوکیشن روی سرور فعال پیدا نشد")

        from bot.services import tunnel as tunnel_svc
        node_url, node_tok = await tunnel_svc._find_node_panel(node["ip"])
        if not node_url:
            panel = await db.get_master_server("v2ray")
            if panel:
                node_url = panel_sync.node_panel_url(panel["url"], node["ip"])
                node_tok = panel.get("api_token")
        if not node_url:
            raise Exception("پنل این نود یافت نشد")

        xui = XUIClient(node_url, api_token=node_tok or "")

        email = f"{order['telegram_id']}-order{order_id}"
        try:
            await xui.delete_client(email)
        except Exception:
            pass
        await xui.add_client_full(
            email=email,
            traffic_gb=order["traffic_gb"],
            expire_days=order["duration_days"],
            limit_ip=order["max_users"] or 0,
            all_inbound_ids=[inbound_id],
        )
        link = ""
        try:
            ls = await xui.get_client_links(email)
            link = next((l for l in ls if str(l).startswith("wireguard://")), ls[0] if ls else "")
        except Exception:
            pass

        if order["duration_days"] and order["duration_days"] > 0:
            expire_date = datetime.now() + timedelta(days=order["duration_days"])
        else:
            expire_date = None
        await db.create_config(
            order["user_id"], order_id, order["plan_id"], email, email, link,
            order["traffic_gb"], expire_date, server_id=None, tunnel_id=tunnel["id"],
        )

        await account_view.deliver_wireguard(
            bot, order["telegram_id"], link,
            plan_name=order.get("plan_name") or "",
            days=order.get("duration_days"),
            traffic_gb=order.get("traffic_gb"),
            user_id=order["telegram_id"],
        )
        await db.update_order_status(order_id, "approved", link)
        try:
            await callback.message.edit_caption(
                caption=base_caption + "\n\n✅ تایید و ارسال شد", reply_markup=None
            )
        except Exception:
            pass
        await callback.answer("✅ ارسال شد!")
    except Exception as e:
        logger.exception("location order approval failed")
        await db.update_order_status(order_id, "pending")
        try:
            await callback.message.edit_caption(
                caption=base_caption + f"\n\n❌ خطا: {str(e)[:200]}",
                reply_markup=order_approval_keyboard(order_id),
            )
        except Exception:
            pass
        await callback.answer("❌ خطا! دوباره تلاش کنید.", show_alert=True)


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

    if order.get("tunnel_id"):
        await _approve_location(bot, callback, order, base_caption)
        return

    try:
        try:
            await callback.message.edit_caption(
                caption=base_caption + "\n\n⏳ در حال ساخت کانفیگ...",
                reply_markup=order_approval_keyboard(order_id)
            )
        except Exception:
            pass

        master = await db.get_server_for_plan(order["plan_id"], "v2ray")
        if not master:
            raise Exception("سرور فعالی یافت نشد")

        tg_id = order["telegram_id"]
        location = master["location"]

        xui = XUIClient(master["url"], api_token=master["api_token"])
        sub_base = panel_sub_base(master["url"], master["sub_port"], master["sub_domain"])

        reality_ids = await xui.resolve_inbound_ids(db.parse_inbound_ids(master["inbound_ids"]))
        if not reality_ids:
            raise Exception("سرور به‌درستی تنظیم نشده. از «مدیریت پنل» اقدام کنید")

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
            # update; make sure the client is still attached to every inbound.
            # Only the missing (and panel-existing) ids are re-attached, and a
            # failure here no longer aborts the renewal: the client already has
            # a valid quota/expiry, so the user keeps a working config.
            current = set(await xui.get_client_inbounds(email))
            missing = [i for i in reality_ids if i not in current]
            if missing and not await xui.attach_client(email, missing):
                logger.warning(f"failed to re-attach inbounds {missing} for {email} after renewal")

            link, kind = await account_view.resolve_delivery(xui, email, sub_token, master, sub_base)
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

            link, kind = await account_view.resolve_delivery(xui, email, sub_token, master, sub_base)
            if order["duration_days"] and order["duration_days"] > 0:
                expire_date = datetime.now() + timedelta(days=order["duration_days"])
            else:
                expire_date = None
            await db.create_config(
                order["user_id"], order_id, order["plan_id"], email,
                sub_token, (link if kind == "sub" else ""), order["traffic_gb"], expire_date,
                server_id=master["id"]
            )
            action_word = "ساخت"

        if kind == "wg":
            await account_view.deliver_wireguard(
                bot, order["telegram_id"], link,
                plan_name=order.get("plan_name") or "",
                days=order.get("duration_days"),
                traffic_gb=order.get("traffic_gb"),
                user_id=order["telegram_id"],
            )
        else:
            await account_view.deliver_account(
                bot, order["telegram_id"], link, (kind == "sub"),
                header="✅ اشتراک شما آماده شد!",
            )

        await db.update_order_status(order_id, "approved", link)

        try:
            await callback.message.edit_caption(
                caption=base_caption + f"\n\n✅ تایید و برای کاربر ارسال شد\n🔗 {link}",
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
