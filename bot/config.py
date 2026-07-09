import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]


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
