from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from bot.config import ADMIN_IDS


def main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="🛒 خرید"), KeyboardButton(text="📋 اکانت های من")],
        [KeyboardButton(text="📖 آموزش اتصال")],
    ]
    if user_id in ADMIN_IDS:
        buttons.append([KeyboardButton(text="⚙️ مدیریت")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
