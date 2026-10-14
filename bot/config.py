import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")


def _parse_admin_ids(value: str):
    ids = []
    for x in (value or "").split(","):
        x = x.strip()
        if x.lstrip("-").isdigit():
            ids.append(int(x))
    return ids


ADMIN_IDS = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))


def _parse_channel(value: str):
    value = (value or "").strip()
    if not value:
        return 0
    if value.lstrip("-").isdigit():
        return int(value)
    return value if value.startswith("@") else "@" + value


def _parse_channel_url(value: str) -> str:
    """Normalize CHANNEL_URL into a valid clickable https://t.me/... link.

    Telegram rejects a bare ``@username`` (or ``username``) as an inline button
    URL ("Wrong HTTP URL"), so accept all common forms and normalize them:
    ``https://t.me/x``, ``t.me/x``, ``@x`` and ``x`` all become
    ``https://t.me/x``.
    """
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    username = value.lstrip("@").lstrip("/")
    if username.startswith("t.me/"):
        return "https://" + username
    return f"https://t.me/{username}"


CHANNEL_ID = _parse_channel(os.getenv("CHANNEL_ID", ""))
CHANNEL_URL = _parse_channel_url(os.getenv("CHANNEL_URL", ""))
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/newbot")
PROXY_URL = os.getenv("PROXY_URL", "")
BASE_URL = os.getenv("BASE_URL", "")
SUB_HOST = os.getenv("SUB_HOST", "0.0.0.0")
SUB_PORT = int(os.getenv("SUB_PORT", "8080"))

# Telethon userbot used to read proxy source channels the bot itself is not a
# member of. Leave empty to disable the proxy relay entirely.
try:
    TG_API_ID = int(os.getenv("TG_API_ID", "0") or 0)
except ValueError:
    TG_API_ID = 0
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_PHONE = os.getenv("TG_PHONE", "")
TG_SESSION = os.getenv("TG_SESSION", "proxy_userbot")


def _parse_csv(value: str) -> list:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


PROXY_SOURCE_CHANNELS = _parse_csv(os.getenv("PROXY_SOURCE_CHANNELS", ""))
