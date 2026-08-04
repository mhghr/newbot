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


CHANNEL_ID = _parse_channel(os.getenv("CHANNEL_ID", ""))
CHANNEL_URL = os.getenv("CHANNEL_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/newbot")
PROXY_URL = os.getenv("PROXY_URL", "")
BASE_URL = os.getenv("BASE_URL", "")
SUB_HOST = os.getenv("SUB_HOST", "0.0.0.0")
SUB_PORT = int(os.getenv("SUB_PORT", "8080"))
