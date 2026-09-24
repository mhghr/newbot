"""One-time Telethon login for the proxy userbot.

Run from the project root:

    python scripts/telethon_login.py request
    # Telegram sends a login code to your account; then:
    python scripts/telethon_login.py signin <code> [2fa_password]

The session file (named after ``TG_SESSION``, default ``proxy_userbot``) is
created in the current directory. Copy it to the server next to the project so
the running bot can reuse the login without asking for a code again.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import TG_API_ID, TG_API_HASH, TG_PHONE, TG_SESSION  # noqa: E402

HASH_FILE = f"{TG_SESSION}.login.json"


def _client():
    from telethon import TelegramClient
    if not (TG_API_ID and TG_API_HASH):
        raise SystemExit("TG_API_ID / TG_API_HASH are not set in .env")
    return TelegramClient(TG_SESSION, TG_API_ID, TG_API_HASH)


async def request_code():
    client = _client()
    await client.connect()
    try:
        if await client.is_user_authorized():
            print("ALREADY_AUTHORIZED")
            return
        sent = await client.send_code_request(TG_PHONE)
        with open(HASH_FILE, "w", encoding="utf-8") as f:
            json.dump({"phone_code_hash": sent.phone_code_hash}, f)
        print("CODE_SENT")
    finally:
        await client.disconnect()


async def sign_in(code, password=None):
    try:
        with open(HASH_FILE, encoding="utf-8") as f:
            phone_code_hash = json.load(f)["phone_code_hash"]
    except FileNotFoundError:
        raise SystemExit("No pending login. Run 'request' first.")

    from telethon.errors import SessionPasswordNeededError

    client = _client()
    await client.connect()
    try:
        try:
            await client.sign_in(
                phone=TG_PHONE, code=code, phone_code_hash=phone_code_hash
            )
        except SessionPasswordNeededError:
            if not password:
                print("NEED_PASSWORD")
                return
            await client.sign_in(password=password)
        print("SIGNED_IN" if await client.is_user_authorized() else "FAILED")
    finally:
        await client.disconnect()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "request":
        asyncio.run(request_code())
    elif mode == "signin":
        if len(sys.argv) < 3:
            raise SystemExit("Usage: python scripts/telethon_login.py signin <code> [2fa_password]")
        asyncio.run(sign_in(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
