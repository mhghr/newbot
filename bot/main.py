import logging
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, MenuButtonCommands

from bot.config import BOT_TOKEN, PROXY_URL, SUB_HOST, SUB_PORT, ADMIN_IDS
from bot.database.models import init_db
from bot.database import db
from bot.services.reminders import start_reminders
from bot.services.proxy_scanner import router as proxy_scanner_router
from bot.services.subserver import start_sub_server

from bot.handlers.start import router as start_router
from bot.handlers.buy import router as buy_router
from bot.handlers.my_configs import router as my_configs_router
from bot.handlers.tutorial import router as tutorial_router
from bot.handlers.apps import router as apps_router
from bot.handlers.refund import router as refund_router
from bot.handlers.proxy import router as proxy_router
from bot.handlers.download import router as download_router
from bot.handlers.admin.menu import router as admin_menu_router
from bot.handlers.admin.servers import router as admin_servers_router
from bot.handlers.admin.plans import router as admin_plans_router
from bot.handlers.admin.users import router as admin_users_router
from bot.handlers.admin.payments import router as admin_payments_router
from bot.handlers.admin.settings import router as admin_settings_router
from bot.handlers.admin.create_account import router as admin_create_account_router
from bot.handlers.admin.transfer import router as admin_transfer_router
from bot.handlers.admin.restore import router as admin_restore_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)


async def _notify_transfer_done(bot: Bot):
    """Announce a completed server transfer to admins, once."""
    try:
        pending = await db.get_setting("transfer_notify_pending", "")
        if pending != "1":
            return
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    "✅ انتقال سرور انجام شد و ربات روی سرور جدید فعال است.\n"
                    "از این پس ربات روی سرور جدید کار می‌کند.",
                )
            except Exception as e:
                logging.warning(f"transfer notify to {admin_id} failed: {e}")
        await db.set_setting("transfer_notify_pending", "0")
    except Exception as e:
        logging.warning(f"transfer notify check failed: {e}")


async def main():
    await init_db()

    session = AiohttpSession(proxy=PROXY_URL) if PROXY_URL else None
    bot = Bot(token=BOT_TOKEN, session=session)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_routers(
        start_router,
        buy_router,
        my_configs_router,
        tutorial_router,
        apps_router,
        refund_router,
        proxy_router,
        download_router,
        admin_menu_router,
        admin_servers_router,
        admin_plans_router,
        admin_users_router,
        admin_payments_router,
        admin_settings_router,
        admin_create_account_router,
        admin_transfer_router,
        admin_restore_router,
        proxy_scanner_router,
    )

    start_reminders(bot)

    await start_sub_server(SUB_HOST, SUB_PORT)

    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="🏠 شروع / منوی اصلی"),
        ])
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e:
        logging.warning(f"Failed to set bot commands/menu: {e}")

    logging.info("Bot starting...")
    await _notify_transfer_done(bot)
    await dp.start_polling(bot)
