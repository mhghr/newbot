# VPN Sales Telegram Bot - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python Telegram bot for selling VPN configs via 3x-ui panel integration with card-to-card payment and admin approval.

**Architecture:** aiogram 3 bot with SQLite database, modular handler structure using Routers, FSM for multi-step conversations, and httpx for 3x-ui API calls.

**Tech Stack:** Python 3.11+, aiogram 3, aiosqlite, httpx, python-dotenv

---

### Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `bot/__init__.py`
- Create: `bot/config.py`
- Create: `run.py`

- [ ] **Step 1: Create requirements.txt**

```
aiogram==3.15.0
aiosqlite==0.20.0
httpx==0.28.1
python-dotenv==1.0.1
```

- [ ] **Step 2: Create .env.example**

```
BOT_TOKEN=your_bot_token_here
ADMIN_IDS=123456789,987654321
CHANNEL_ID=-1001234567890
CHANNEL_URL=https://t.me/your_channel
```

- [ ] **Step 3: Create bot/__init__.py**

Empty file.

- [ ] **Step 4: Create bot/config.py**

```python
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
CHANNEL_URL = os.getenv("CHANNEL_URL", "")
DB_PATH = os.getenv("DB_PATH", "bot.db")
```

- [ ] **Step 5: Create run.py**

```python
import asyncio
from bot.main import main

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: Install dependencies**

Run: `pip install -r requirements.txt`

- [ ] **Step 7: Create .env file from example**

Copy `.env.example` to `.env` and fill in actual values.

---

### Task 2: Database Layer

**Files:**
- Create: `bot/database/__init__.py`
- Create: `bot/database/models.py`
- Create: `bot/database/db.py`

- [ ] **Step 1: Create bot/database/__init__.py**

Empty file.

- [ ] **Step 2: Create bot/database/models.py**

```python
import aiosqlite
from bot.config import DB_PATH

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                location TEXT NOT NULL,
                inbound_id INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                traffic_gb INTEGER NOT NULL,
                duration_days INTEGER NOT NULL,
                price INTEGER NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                server_id INTEGER NOT NULL,
                status TEXT DEFAULT 'pending',
                receipt_photo_id TEXT,
                config_link TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (plan_id) REFERENCES plans(id),
                FOREIGN KEY (server_id) REFERENCES servers(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                order_id INTEGER NOT NULL,
                server_id INTEGER NOT NULL,
                client_email TEXT NOT NULL,
                config_link TEXT NOT NULL,
                expire_date TIMESTAMP NOT NULL,
                traffic_limit_gb INTEGER NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (order_id) REFERENCES orders(id),
                FOREIGN KEY (server_id) REFERENCES servers(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        await db.commit()
```

- [ ] **Step 3: Create bot/database/db.py**

```python
import aiosqlite
from bot.config import DB_PATH
from datetime import datetime


async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def add_user(telegram_id: int, username: str = None, first_name: str = None, last_name: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """INSERT OR IGNORE INTO users (telegram_id, username, first_name, last_name)
               VALUES (?, ?, ?, ?)""",
            (telegram_id, username, first_name, last_name)
        )
        await db.execute(
            """UPDATE users SET username=?, first_name=?, last_name=? WHERE telegram_id=?""",
            (username, first_name, last_name, telegram_id)
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
        return await cursor.fetchone()


async def get_user_by_telegram_id(telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,))
        return await cursor.fetchone()


async def get_user_by_id(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM users WHERE id=?", (user_id,))
        return await cursor.fetchone()


async def search_user(query: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if query.isdigit():
            cursor = await db.execute("SELECT * FROM users WHERE telegram_id=?", (int(query),))
        else:
            cursor = await db.execute("SELECT * FROM users WHERE username LIKE ?", (f"%{query}%",))
        return await cursor.fetchall()


async def add_server(name: str, url: str, username: str, password: str, location: str, inbound_id: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO servers (name, url, username, password, location, inbound_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, url, username, password, location, inbound_id)
        )
        await db.commit()
        return cursor.lastrowid


async def get_active_servers():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM servers WHERE is_active=1")
        return await cursor.fetchall()


async def get_all_servers():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM servers ORDER BY created_at DESC")
        return await cursor.fetchall()


async def get_server(server_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM servers WHERE id=?", (server_id,))
        return await cursor.fetchone()


async def toggle_server(server_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE servers SET is_active = NOT is_active WHERE id=?", (server_id,))
        await db.commit()


async def delete_server(server_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM servers WHERE id=?", (server_id,))
        await db.commit()


async def add_plan(name: str, traffic_gb: int, duration_days: int, price: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO plans (name, traffic_gb, duration_days, price)
               VALUES (?, ?, ?, ?)""",
            (name, traffic_gb, duration_days, price)
        )
        await db.commit()
        return cursor.lastrowid


async def get_active_plans():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM plans WHERE is_active=1 ORDER BY price")
        return await cursor.fetchall()


async def get_all_plans():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM plans ORDER BY created_at DESC")
        return await cursor.fetchall()


async def get_plan(plan_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM plans WHERE id=?", (plan_id,))
        return await cursor.fetchone()


async def toggle_plan(plan_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE plans SET is_active = NOT is_active WHERE id=?", (plan_id,))
        await db.commit()


async def delete_plan(plan_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM plans WHERE id=?", (plan_id,))
        await db.commit()


async def create_order(user_id: int, plan_id: int, server_id: int, receipt_photo_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO orders (user_id, plan_id, server_id, receipt_photo_id)
               VALUES (?, ?, ?, ?)""",
            (user_id, plan_id, server_id, receipt_photo_id)
        )
        await db.commit()
        return cursor.lastrowid


async def get_order(order_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT o.*, u.telegram_id, u.username, u.first_name,
                      p.name as plan_name, p.traffic_gb, p.duration_days, p.price,
                      s.name as server_name, s.location, s.url as server_url,
                      s.username as server_username, s.password as server_password,
                      s.inbound_id
               FROM orders o
               JOIN users u ON o.user_id = u.id
               JOIN plans p ON o.plan_id = p.id
               JOIN servers s ON o.server_id = s.id
               WHERE o.id=?""",
            (order_id,)
        )
        return await cursor.fetchone()


async def update_order_status(order_id: int, status: str, config_link: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if config_link:
            await db.execute(
                "UPDATE orders SET status=?, config_link=?, reviewed_at=? WHERE id=?",
                (status, config_link, datetime.now().isoformat(), order_id)
            )
        else:
            await db.execute(
                "UPDATE orders SET status=?, reviewed_at=? WHERE id=?",
                (status, datetime.now().isoformat(), order_id)
            )
        await db.commit()


async def get_pending_orders():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT o.*, u.telegram_id, u.username, u.first_name,
                      p.name as plan_name, p.traffic_gb, p.duration_days, p.price,
                      s.name as server_name, s.location
               FROM orders o
               JOIN users u ON o.user_id = u.id
               JOIN plans p ON o.plan_id = p.id
               JOIN servers s ON o.server_id = s.id
               WHERE o.status='pending'
               ORDER BY o.created_at DESC""",
        )
        return await cursor.fetchall()


async def add_config(user_id: int, order_id: int, server_id: int, client_email: str,
                     config_link: str, expire_date: str, traffic_limit_gb: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO configs (user_id, order_id, server_id, client_email, config_link,
                                   expire_date, traffic_limit_gb)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, order_id, server_id, client_email, config_link, expire_date, traffic_limit_gb)
        )
        await db.commit()
        return cursor.lastrowid


async def get_user_configs(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT c.*, s.name as server_name, s.location, s.url as server_url,
                      s.username as server_username, s.password as server_password
               FROM configs c
               JOIN servers s ON c.server_id = s.id
               WHERE c.user_id=? AND c.is_active=1
               ORDER BY c.created_at DESC""",
            (user_id,)
        )
        return await cursor.fetchall()


async def get_user_configs_by_telegram_id(telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT c.*, s.name as server_name, s.location, s.url as server_url,
                      s.username as server_username, s.password as server_password
               FROM configs c
               JOIN servers s ON c.server_id = s.id
               JOIN users u ON c.user_id = u.id
               WHERE u.telegram_id=? AND c.is_active=1
               ORDER BY c.created_at DESC""",
            (telegram_id,)
        )
        return await cursor.fetchall()


async def get_user_orders(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT o.*, p.name as plan_name, p.traffic_gb, p.duration_days, p.price,
                      s.name as server_name, s.location
               FROM orders o
               JOIN plans p ON o.plan_id = p.id
               JOIN servers s ON o.server_id = s.id
               WHERE o.user_id=?
               ORDER BY o.created_at DESC
               LIMIT 20""",
            (user_id,)
        )
        return await cursor.fetchall()


async def get_setting(key: str, default: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else default


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        await db.commit()
```

- [ ] **Step 4: Verify database creation works**

Run: `python -c "import asyncio; from bot.database.models import init_db; asyncio.run(init_db()); print('DB OK')"` from `D:\newbot`
Expected: `DB OK`

---

### Task 3: Keyboards

**Files:**
- Create: `bot/keyboards/__init__.py`
- Create: `bot/keyboards/reply.py`
- Create: `bot/keyboards/inline.py`

- [ ] **Step 1: Create bot/keyboards/__init__.py**

Empty file.

- [ ] **Step 2: Create bot/keyboards/reply.py**

```python
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from bot.config import ADMIN_IDS


def main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="🛒 خرید کانفیگ"), KeyboardButton(text="📋 کانفیگ های من")],
        [KeyboardButton(text="📖 آموزش اتصال")],
    ]
    if user_id in ADMIN_IDS:
        buttons.append([KeyboardButton(text="⚙️ مدیریت")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
```

- [ ] **Step 3: Create bot/keyboards/inline.py**

```python
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.config import CHANNEL_URL


def join_channel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 عضویت در کانال", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ بررسی عضویت", callback_data="check_membership")],
    ])


def plans_keyboard(plans: list) -> InlineKeyboardMarkup:
    buttons = []
    for plan in plans:
        text = f"{plan['name']} | {plan['traffic_gb']}GB | {plan['duration_days']} روز | {plan['price']:,} تومان"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"buy_plan:{plan['id']}")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="cancel_buy")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def locations_keyboard(servers: list) -> InlineKeyboardMarkup:
    buttons = []
    for server in servers:
        text = f"📍 {server['location']} - {server['name']}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"buy_server:{server['id']}")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="cancel_buy")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def order_approval_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ تایید", callback_data=f"approve_order:{order_id}"),
            InlineKeyboardButton(text="❌ رد", callback_data=f"reject_order:{order_id}"),
        ]
    ])


def configs_keyboard(configs: list) -> InlineKeyboardMarkup:
    buttons = []
    for config in configs:
        text = f"📍 {config['location']} | {config['server_name']}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"view_config:{config['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def tutorial_platforms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 اندروید", callback_data="tutorial:android")],
        [InlineKeyboardButton(text="🍎 iOS", callback_data="tutorial:ios")],
        [InlineKeyboardButton(text="💻 ویندوز", callback_data="tutorial:windows")],
    ])


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🖥 مدیریت سرورها", callback_data="admin:servers")],
        [InlineKeyboardButton(text="📦 مدیریت پلن‌ها", callback_data="admin:plans")],
        [InlineKeyboardButton(text="🔍 جستجوی کاربر", callback_data="admin:search_user")],
        [InlineKeyboardButton(text="📖 مدیریت آموزش", callback_data="admin:tutorials")],
        [InlineKeyboardButton(text="⚙️ تنظیمات", callback_data="admin:settings")],
    ])


def admin_servers_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ افزودن سرور", callback_data="admin:add_server")],
        [InlineKeyboardButton(text="📋 لیست سرورها", callback_data="admin:list_servers")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")],
    ])


def admin_plans_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ افزودن پلن", callback_data="admin:add_plan")],
        [InlineKeyboardButton(text="📋 لیست پلن‌ها", callback_data="admin:list_plans")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")],
    ])


def server_actions_keyboard(server_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 غیرفعال کردن" if is_active else "🟢 فعال کردن"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data=f"admin:toggle_server:{server_id}")],
        [InlineKeyboardButton(text="🗑 حذف", callback_data=f"admin:delete_server:{server_id}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:servers")],
    ])


def plan_actions_keyboard(plan_id: int, is_active: bool) -> InlineKeyboardMarkup:
    toggle_text = "🔴 غیرفعال کردن" if is_active else "🟢 فعال کردن"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data=f"admin:toggle_plan:{plan_id}")],
        [InlineKeyboardButton(text="🗑 حذف", callback_data=f"admin:delete_plan:{plan_id}")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:plans")],
    ])


def admin_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 شماره کارت", callback_data="admin:set_card_number")],
        [InlineKeyboardButton(text="👤 نام صاحب کارت", callback_data="admin:set_card_holder")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")],
    ])


def admin_tutorial_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 اندروید", callback_data="admin:edit_tutorial:android")],
        [InlineKeyboardButton(text="🍎 iOS", callback_data="admin:edit_tutorial:ios")],
        [InlineKeyboardButton(text="💻 ویندوز", callback_data="admin:edit_tutorial:windows")],
        [InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")],
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_action")],
    ])
```

---

### Task 4: 3x-ui API Service

**Files:**
- Create: `bot/services/__init__.py`
- Create: `bot/services/xui.py`

- [ ] **Step 1: Create bot/services/__init__.py**

Empty file.

- [ ] **Step 2: Create bot/services/xui.py**

```python
import httpx
import uuid
import json
from datetime import datetime, timedelta


class XUIClient:
    def __init__(self, url: str, username: str, password: str):
        self.base_url = url.rstrip("/")
        self.username = username
        self.password = password
        self.session_cookie = None

    async def login(self) -> bool:
        async with httpx.AsyncClient(verify=False) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/login",
                    data={"username": self.username, "password": self.password},
                    timeout=10
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success"):
                        self.session_cookie = resp.cookies.get("3x-ui") or resp.cookies.get("session")
                        if not self.session_cookie:
                            for name, value in resp.cookies.items():
                                self.session_cookie = value
                                break
                        return True
                return False
            except Exception:
                return False

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.session_cookie:
            logged_in = await self.login()
            if not logged_in:
                raise Exception("Failed to login to 3x-ui panel")

        async with httpx.AsyncClient(verify=False) as client:
            cookies = {"3x-ui": self.session_cookie} if self.session_cookie else {}
            resp = await client.request(
                method,
                f"{self.base_url}{path}",
                cookies=cookies,
                timeout=15,
                **kwargs
            )
            if resp.status_code == 401 or (resp.status_code == 200 and not resp.json().get("success", True)):
                await self.login()
                cookies = {"3x-ui": self.session_cookie} if self.session_cookie else {}
                resp = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    cookies=cookies,
                    timeout=15,
                    **kwargs
                )
            return resp.json()

    async def get_inbounds(self) -> list:
        data = await self._request("GET", "/panel/api/inbounds/list")
        return data.get("obj", [])

    async def add_client(self, inbound_id: int, email: str, traffic_gb: int, expire_days: int) -> str:
        client_uuid = str(uuid.uuid4())
        expire_ms = int((datetime.now() + timedelta(days=expire_days)).timestamp() * 1000)
        traffic_bytes = traffic_gb * 1024 * 1024 * 1024

        settings = json.dumps({
            "clients": [{
                "id": client_uuid,
                "email": email,
                "limitIp": 0,
                "totalGB": traffic_bytes,
                "expiryTime": expire_ms,
                "enable": True,
                "tgId": "",
                "subId": email,
            }]
        })

        data = await self._request(
            "POST",
            f"/panel/api/inbounds/addClient",
            data={
                "id": inbound_id,
                "settings": settings,
            }
        )
        if data.get("success"):
            return client_uuid
        raise Exception(f"Failed to add client: {data}")

    async def get_client_traffic(self, email: str) -> dict:
        data = await self._request("GET", f"/panel/api/inbounds/getClientTraffics/{email}")
        obj = data.get("obj", {})
        if obj:
            up = obj.get("up", 0)
            down = obj.get("down", 0)
            total = obj.get("total", 0)
            return {
                "up": up,
                "down": down,
                "total": total,
                "used": up + down,
                "remaining": max(0, total - (up + down)) if total > 0 else 0,
            }
        return {"up": 0, "down": 0, "total": 0, "used": 0, "remaining": 0}

    async def get_client_ips(self, email: str) -> str:
        data = await self._request("POST", f"/panel/api/inbounds/clientIps/{email}")
        return data.get("obj", "")

    async def get_sub_link(self, email: str) -> str:
        return f"{self.base_url}/sub/{email}"


def format_bytes(b: int) -> str:
    if b <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(b)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"
```

---

### Task 5: Start Handler + Membership Check

**Files:**
- Create: `bot/handlers/__init__.py`
- Create: `bot/handlers/start.py`
- Create: `bot/middlewares/__init__.py`
- Create: `bot/middlewares/membership.py`

- [ ] **Step 1: Create bot/handlers/__init__.py**

Empty file.

- [ ] **Step 2: Create bot/middlewares/__init__.py**

Empty file.

- [ ] **Step 3: Create bot/middlewares/membership.py**

```python
from aiogram import Bot
from bot.config import CHANNEL_ID


async def check_membership(bot: Bot, user_id: int) -> bool:
    if CHANNEL_ID == 0:
        return True
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False
```

- [ ] **Step 4: Create bot/handlers/start.py**

```python
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery

from bot.middlewares.membership import check_membership
from bot.keyboards.reply import main_menu_keyboard
from bot.keyboards.inline import join_channel_keyboard
from bot.database import db

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot):
    is_member = await check_membership(bot, message.from_user.id)

    if not is_member:
        await message.answer(
            "⚠️ برای استفاده از ربات، ابتدا در کانال ما عضو شوید و سپس دکمه بررسی عضویت را بزنید.",
            reply_markup=join_channel_keyboard()
        )
        return

    await db.add_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )

    await message.answer(
        f"سلام {message.from_user.first_name}! 👋\n"
        "به ربات فروش VPN خوش آمدید.\n"
        "از منوی زیر گزینه مورد نظر خود را انتخاب کنید:",
        reply_markup=main_menu_keyboard(message.from_user.id)
    )


@router.callback_query(F.data == "check_membership")
async def check_membership_callback(callback: CallbackQuery, bot: Bot):
    is_member = await check_membership(bot, callback.from_user.id)

    if not is_member:
        await callback.answer("❌ شما هنوز عضو کانال نشده‌اید!", show_alert=True)
        return

    await db.add_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        first_name=callback.from_user.first_name,
        last_name=callback.from_user.last_name,
    )

    await callback.message.delete()
    await callback.message.answer(
        f"سلام {callback.from_user.first_name}! 👋\n"
        "به ربات فروش VPN خوش آمدید.\n"
        "از منوی زیر گزینه مورد نظر خود را انتخاب کنید:",
        reply_markup=main_menu_keyboard(callback.from_user.id)
    )
    await callback.answer()
```

---

### Task 6: Buy Config Flow

**Files:**
- Create: `bot/handlers/buy.py`

- [ ] **Step 1: Create bot/handlers/buy.py**

```python
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.database import db
from bot.keyboards.inline import plans_keyboard, locations_keyboard, cancel_keyboard
from bot.keyboards.reply import main_menu_keyboard
from bot.keyboards.inline import order_approval_keyboard
from bot.config import ADMIN_IDS
from bot.middlewares.membership import check_membership

router = Router()


class BuyStates(StatesGroup):
    waiting_receipt = State()


@router.message(F.text == "🛒 خرید کانفیگ")
async def buy_config(message: Message, bot: Bot):
    is_member = await check_membership(bot, message.from_user.id)
    if not is_member:
        await message.answer("⚠️ ابتدا در کانال ما عضو شوید. /start")
        return

    plans = await db.get_active_plans()
    if not plans:
        await message.answer("❌ در حال حاضر پلنی تعریف نشده است.")
        return

    await message.answer("📦 پلن مورد نظر خود را انتخاب کنید:", reply_markup=plans_keyboard(plans))


@router.callback_query(F.data.startswith("buy_plan:"))
async def select_plan(callback: CallbackQuery, state: FSMContext):
    plan_id = int(callback.data.split(":")[1])
    plan = await db.get_plan(plan_id)
    if not plan:
        await callback.answer("❌ پلن یافت نشد!", show_alert=True)
        return

    servers = await db.get_active_servers()
    if not servers:
        await callback.answer("❌ سروری فعال نیست!", show_alert=True)
        return

    await state.update_data(plan_id=plan_id)
    await callback.message.edit_text(
        f"📦 پلن انتخابی: {plan['name']}\n"
        f"📊 حجم: {plan['traffic_gb']} گیگابایت\n"
        f"📅 مدت: {plan['duration_days']} روز\n"
        f"💰 قیمت: {plan['price']:,} تومان\n\n"
        "📍 لوکیشن مورد نظر خود را انتخاب کنید:",
        reply_markup=locations_keyboard(servers)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buy_server:"))
async def select_server(callback: CallbackQuery, state: FSMContext):
    server_id = int(callback.data.split(":")[1])
    server = await db.get_server(server_id)
    if not server:
        await callback.answer("❌ سرور یافت نشد!", show_alert=True)
        return

    data = await state.get_data()
    plan_id = data.get("plan_id")
    plan = await db.get_plan(plan_id)

    await state.update_data(server_id=server_id)

    card_number = await db.get_setting("card_number", "تنظیم نشده")
    card_holder = await db.get_setting("card_holder", "تنظیم نشده")

    await callback.message.edit_text(
        f"💳 اطلاعات پرداخت:\n\n"
        f"شماره کارت: `{card_number}`\n"
        f"به نام: {card_holder}\n"
        f"مبلغ: **{plan['price']:,} تومان**\n\n"
        f"📦 پلن: {plan['name']} | {plan['traffic_gb']}GB | {plan['duration_days']} روز\n"
        f"📍 لوکیشن: {server['location']}\n\n"
        "⬇️ لطفا پس از واریز، **تصویر رسید** را ارسال کنید:",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(BuyStates.waiting_receipt)
    await callback.answer()


@router.message(BuyStates.waiting_receipt, F.photo)
async def receive_receipt(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    plan_id = data.get("plan_id")
    server_id = data.get("server_id")

    user = await db.get_user_by_telegram_id(message.from_user.id)
    if not user:
        await message.answer("❌ خطا! لطفا /start بزنید.")
        await state.clear()
        return

    photo_id = message.photo[-1].file_id

    order_id = await db.create_order(
        user_id=user["id"],
        plan_id=plan_id,
        server_id=server_id,
        receipt_photo_id=photo_id,
    )

    await message.answer(
        f"✅ رسید شما ثبت شد!\n"
        f"شماره سفارش: #{order_id}\n\n"
        "پس از بررسی توسط ادمین، کانفیگ برای شما ارسال خواهد شد.",
        reply_markup=main_menu_keyboard(message.from_user.id)
    )
    await state.clear()

    order = await db.get_order(order_id)

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                chat_id=admin_id,
                photo=photo_id,
                caption=(
                    f"🆕 سفارش جدید #{order_id}\n\n"
                    f"👤 کاربر: {order['first_name']} (@{order['username'] or 'ندارد'})\n"
                    f"🆔 آیدی: {order['telegram_id']}\n"
                    f"📦 پلن: {order['plan_name']} | {order['traffic_gb']}GB | {order['duration_days']} روز\n"
                    f"💰 مبلغ: {order['price']:,} تومان\n"
                    f"📍 سرور: {order['server_name']} ({order['location']})"
                ),
                reply_markup=order_approval_keyboard(order_id),
            )
        except Exception:
            pass


@router.message(BuyStates.waiting_receipt)
async def invalid_receipt(message: Message):
    await message.answer("⚠️ لطفا **تصویر رسید** را ارسال کنید.", parse_mode="Markdown")


@router.callback_query(F.data == "cancel_buy")
async def cancel_buy(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ عملیات خرید لغو شد.")
    await callback.answer()


@router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ عملیات لغو شد.")
    await callback.answer()
```

---

### Task 7: My Configs Handler

**Files:**
- Create: `bot/handlers/my_configs.py`

- [ ] **Step 1: Create bot/handlers/my_configs.py**

```python
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from datetime import datetime

from bot.database import db
from bot.keyboards.inline import configs_keyboard
from bot.middlewares.membership import check_membership
from bot.services.xui import XUIClient, format_bytes

router = Router()


@router.message(F.text == "📋 کانفیگ های من")
async def my_configs(message: Message, bot: Bot):
    is_member = await check_membership(bot, message.from_user.id)
    if not is_member:
        await message.answer("⚠️ ابتدا در کانال ما عضو شوید. /start")
        return

    configs = await db.get_user_configs_by_telegram_id(message.from_user.id)
    if not configs:
        await message.answer("📋 شما هیچ کانفیگ فعالی ندارید.")
        return

    await message.answer("📋 کانفیگ‌های فعال شما:", reply_markup=configs_keyboard(configs))


@router.callback_query(F.data.startswith("view_config:"))
async def view_config(callback: CallbackQuery):
    config_id = int(callback.data.split(":")[1])

    configs = await db.get_user_configs_by_telegram_id(callback.from_user.id)
    config = None
    for c in configs:
        if c["id"] == config_id:
            config = c
            break

    if not config:
        await callback.answer("❌ کانفیگ یافت نشد!", show_alert=True)
        return

    expire_date = datetime.fromisoformat(config["expire_date"])
    remaining_days = (expire_date - datetime.now()).days
    remaining_days = max(0, remaining_days)

    traffic_info = ""
    try:
        xui = XUIClient(config["server_url"], config["server_username"], config["server_password"])
        traffic = await xui.get_client_traffic(config["client_email"])
        traffic_info = (
            f"📊 ترافیک مصرفی: {format_bytes(traffic['used'])}\n"
            f"📊 ترافیک باقیمانده: {format_bytes(traffic['remaining'])}\n"
        )
    except Exception:
        traffic_info = "📊 ترافیک: خطا در دریافت اطلاعات\n"

    text = (
        f"📋 جزئیات کانفیگ:\n\n"
        f"🖥 سرور: {config['server_name']}\n"
        f"📍 لوکیشن: {config['location']}\n"
        f"📅 روز باقیمانده: {remaining_days} روز\n"
        f"{traffic_info}\n"
        f"🔗 لینک کانفیگ:\n`{config['config_link']}`"
    )

    await callback.message.edit_text(text, parse_mode="Markdown")
    await callback.answer()
```

---

### Task 8: Tutorial Handler

**Files:**
- Create: `bot/handlers/tutorial.py`

- [ ] **Step 1: Create bot/handlers/tutorial.py**

```python
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery

from bot.database import db
from bot.keyboards.inline import tutorial_platforms_keyboard
from bot.middlewares.membership import check_membership

router = Router()

PLATFORM_NAMES = {
    "android": "اندروید",
    "ios": "iOS",
    "windows": "ویندوز",
}


@router.message(F.text == "📖 آموزش اتصال")
async def tutorial_menu(message: Message, bot: Bot):
    is_member = await check_membership(bot, message.from_user.id)
    if not is_member:
        await message.answer("⚠️ ابتدا در کانال ما عضو شوید. /start")
        return

    await message.answer("📖 پلتفرم مورد نظر خود را انتخاب کنید:", reply_markup=tutorial_platforms_keyboard())


@router.callback_query(F.data.startswith("tutorial:"))
async def show_tutorial(callback: CallbackQuery):
    platform = callback.data.split(":")[1]
    platform_name = PLATFORM_NAMES.get(platform, platform)

    tutorial_text = await db.get_setting(f"tutorial_{platform}", "")

    if not tutorial_text:
        await callback.message.edit_text(
            f"📖 آموزش {platform_name}:\n\n"
            "⚠️ متن آموزش هنوز توسط ادمین تنظیم نشده است."
        )
    else:
        await callback.message.edit_text(
            f"📖 آموزش {platform_name}:\n\n{tutorial_text}",
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    await callback.answer()
```

---

### Task 9: Admin Menu Handler

**Files:**
- Create: `bot/handlers/admin/__init__.py`
- Create: `bot/handlers/admin/menu.py`

- [ ] **Step 1: Create bot/handlers/admin/__init__.py**

Empty file.

- [ ] **Step 2: Create bot/handlers/admin/menu.py**

```python
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery

from bot.config import ADMIN_IDS
from bot.keyboards.inline import admin_menu_keyboard

router = Router()


@router.message(F.text == "⚙️ مدیریت")
async def admin_menu(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.answer("⚙️ پنل مدیریت:", reply_markup=admin_menu_keyboard())


@router.callback_query(F.data == "admin:back")
async def admin_back(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️ دسترسی ندارید!", show_alert=True)
        return

    await callback.message.edit_text("⚙️ پنل مدیریت:", reply_markup=admin_menu_keyboard())
    await callback.answer()
```

---

### Task 10: Admin Server Management

**Files:**
- Create: `bot/handlers/admin/servers.py`

- [ ] **Step 1: Create bot/handlers/admin/servers.py**

```python
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import (
    admin_servers_keyboard, server_actions_keyboard, admin_menu_keyboard, cancel_keyboard
)
from bot.services.xui import XUIClient

router = Router()


class AddServerStates(StatesGroup):
    waiting_url = State()
    waiting_username = State()
    waiting_password = State()
    waiting_location = State()


@router.callback_query(F.data == "admin:servers")
async def servers_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔️", show_alert=True)
        return
    await callback.message.edit_text("🖥 مدیریت سرورها:", reply_markup=admin_servers_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin:add_server")
async def add_server_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "🖥 افزودن سرور جدید\n\n"
        "لطفا آدرس پنل 3x-ui را وارد کنید:\n"
        "(مثال: https://panel.example.com:2053)",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AddServerStates.waiting_url)
    await callback.answer()


@router.message(AddServerStates.waiting_url)
async def add_server_url(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    url = message.text.strip().rstrip("/")
    await state.update_data(url=url)
    await message.answer("👤 یوزرنیم پنل را وارد کنید:", reply_markup=cancel_keyboard())
    await state.set_state(AddServerStates.waiting_username)


@router.message(AddServerStates.waiting_username)
async def add_server_username(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(username=message.text.strip())
    await message.answer("🔑 پسورد پنل را وارد کنید:", reply_markup=cancel_keyboard())
    await state.set_state(AddServerStates.waiting_password)


@router.message(AddServerStates.waiting_password)
async def add_server_password(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(password=message.text.strip())
    await message.answer("📍 لوکیشن سرور را وارد کنید:\n(مثال: آلمان، هلند، آمریکا)", reply_markup=cancel_keyboard())
    await state.set_state(AddServerStates.waiting_location)


@router.message(AddServerStates.waiting_location)
async def add_server_location(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    location = message.text.strip()
    data = await state.get_data()
    url = data["url"]
    username = data["username"]
    password = data["password"]

    await message.answer("⏳ در حال تست اتصال به پنل...")

    xui = XUIClient(url, username, password)
    logged_in = await xui.login()

    if not logged_in:
        await message.answer(
            "❌ اتصال به پنل ناموفق بود!\n"
            "لطفا اطلاعات را بررسی و مجددا تلاش کنید.",
            reply_markup=admin_servers_keyboard()
        )
        await state.clear()
        return

    inbound_id = 0
    try:
        inbounds = await xui.get_inbounds()
        if inbounds:
            inbound_id = inbounds[0].get("id", 0)
    except Exception:
        pass

    server_name = f"Server-{location}"
    await db.add_server(
        name=server_name,
        url=url,
        username=username,
        password=password,
        location=location,
        inbound_id=inbound_id,
    )

    await message.answer(
        f"✅ سرور با موفقیت اضافه شد!\n\n"
        f"📍 لوکیشن: {location}\n"
        f"🔗 آدرس: {url}\n"
        f"📡 Inbound ID: {inbound_id}",
        reply_markup=admin_servers_keyboard()
    )
    await state.clear()


@router.callback_query(F.data == "admin:list_servers")
async def list_servers(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    servers = await db.get_all_servers()
    if not servers:
        await callback.message.edit_text(
            "📋 هیچ سروری ثبت نشده است.",
            reply_markup=admin_servers_keyboard()
        )
        await callback.answer()
        return

    text = "📋 لیست سرورها:\n\n"
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    buttons = []
    for s in servers:
        status = "🟢" if s["is_active"] else "🔴"
        text += f"{status} {s['name']} | {s['location']} | {s['url']}\n"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {s['name']} - {s['location']}",
            callback_data=f"admin:server_detail:{s['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:servers")])

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:server_detail:"))
async def server_detail(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    server_id = int(callback.data.split(":")[2])
    server = await db.get_server(server_id)
    if not server:
        await callback.answer("❌ سرور یافت نشد!", show_alert=True)
        return

    status = "فعال 🟢" if server["is_active"] else "غیرفعال 🔴"
    await callback.message.edit_text(
        f"🖥 جزئیات سرور:\n\n"
        f"📛 نام: {server['name']}\n"
        f"📍 لوکیشن: {server['location']}\n"
        f"🔗 آدرس: {server['url']}\n"
        f"📡 Inbound: {server['inbound_id']}\n"
        f"وضعیت: {status}",
        reply_markup=server_actions_keyboard(server_id, bool(server["is_active"]))
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:toggle_server:"))
async def toggle_server(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    server_id = int(callback.data.split(":")[2])
    await db.toggle_server(server_id)
    await callback.answer("✅ وضعیت سرور تغییر کرد!")

    server = await db.get_server(server_id)
    status = "فعال 🟢" if server["is_active"] else "غیرفعال 🔴"
    await callback.message.edit_text(
        f"🖥 جزئیات سرور:\n\n"
        f"📛 نام: {server['name']}\n"
        f"📍 لوکیشن: {server['location']}\n"
        f"🔗 آدرس: {server['url']}\n"
        f"📡 Inbound: {server['inbound_id']}\n"
        f"وضعیت: {status}",
        reply_markup=server_actions_keyboard(server_id, bool(server["is_active"]))
    )


@router.callback_query(F.data.startswith("admin:delete_server:"))
async def delete_server(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    server_id = int(callback.data.split(":")[2])
    await db.delete_server(server_id)
    await callback.answer("✅ سرور حذف شد!")
    await callback.message.edit_text("🖥 مدیریت سرورها:", reply_markup=admin_servers_keyboard())
```

---

### Task 11: Admin Plan Management

**Files:**
- Create: `bot/handlers/admin/plans.py`

- [ ] **Step 1: Create bot/handlers/admin/plans.py**

```python
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

    data = await state.get_data()
    await db.add_plan(
        name=data["name"],
        traffic_gb=data["traffic_gb"],
        duration_days=data["duration_days"],
        price=price,
    )
    await message.answer(
        f"✅ پلن با موفقیت اضافه شد!\n\n"
        f"📛 نام: {data['name']}\n"
        f"📊 حجم: {data['traffic_gb']} GB\n"
        f"📅 مدت: {data['duration_days']} روز\n"
        f"💰 قیمت: {price:,} تومان",
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
    await callback.message.edit_text(
        f"📦 جزئیات پلن:\n\n"
        f"📛 نام: {plan['name']}\n"
        f"📊 حجم: {plan['traffic_gb']} GB\n"
        f"📅 مدت: {plan['duration_days']} روز\n"
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
    await callback.message.edit_text(
        f"📦 جزئیات پلن:\n\n"
        f"📛 نام: {plan['name']}\n"
        f"📊 حجم: {plan['traffic_gb']} GB\n"
        f"📅 مدت: {plan['duration_days']} روز\n"
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
```

---

### Task 12: Admin User Search

**Files:**
- Create: `bot/handlers/admin/users.py`

- [ ] **Step 1: Create bot/handlers/admin/users.py**

```python
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import cancel_keyboard, admin_menu_keyboard
from bot.services.xui import XUIClient, format_bytes

router = Router()


class SearchUserStates(StatesGroup):
    waiting_query = State()


@router.callback_query(F.data == "admin:search_user")
async def search_user_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "🔍 آیدی عددی تلگرام یا یوزرنیم کاربر را وارد کنید:",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(SearchUserStates.waiting_query)
    await callback.answer()


@router.message(SearchUserStates.waiting_query)
async def search_user_result(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return

    query = message.text.strip().lstrip("@")
    users = await db.search_user(query)

    if not users:
        await message.answer(
            "❌ کاربری یافت نشد.",
            reply_markup=admin_menu_keyboard()
        )
        await state.clear()
        return

    if len(users) == 1:
        user = users[0]
        configs = await db.get_user_configs(user["id"])
        orders = await db.get_user_orders(user["id"])

        text = (
            f"👤 اطلاعات کاربر:\n\n"
            f"🆔 آیدی: {user['telegram_id']}\n"
            f"📛 نام: {user['first_name'] or '-'} {user['last_name'] or ''}\n"
            f"👤 یوزرنیم: @{user['username'] or 'ندارد'}\n"
            f"📅 تاریخ عضویت: {user['created_at']}\n\n"
        )

        if configs:
            text += f"📋 کانفیگ‌های فعال ({len(configs)}):\n"
            for c in configs:
                expire_date = datetime.fromisoformat(c["expire_date"])
                remaining = max(0, (expire_date - datetime.now()).days)
                text += f"  • {c['server_name']} ({c['location']}) - {remaining} روز مانده\n"
        else:
            text += "📋 کانفیگ فعالی ندارد.\n"

        text += f"\n📦 تعداد سفارشات: {len(orders)}\n"
        if orders:
            for o in orders:
                status_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(o["status"], "❓")
                text += f"  {status_emoji} #{o['id']} | {o['plan_name']} | {o['price']:,}T | {o['status']}\n"

        await message.answer(text, reply_markup=admin_menu_keyboard())
    else:
        buttons = []
        for u in users[:10]:
            buttons.append([InlineKeyboardButton(
                text=f"{u['first_name'] or '-'} | @{u['username'] or 'N/A'} | {u['telegram_id']}",
                callback_data=f"admin:user_detail:{u['id']}"
            )])
        buttons.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:back")])
        await message.answer(
            f"🔍 {len(users)} کاربر یافت شد:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )

    await state.clear()


@router.callback_query(F.data.startswith("admin:user_detail:"))
async def user_detail(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    user_id = int(callback.data.split(":")[2])

    user = await db.get_user_by_id(user_id)

    if not user:
        await callback.answer("❌ کاربر یافت نشد!", show_alert=True)
        return

    configs = await db.get_user_configs(user["id"])
    orders = await db.get_user_orders(user["id"])

    text = (
        f"👤 اطلاعات کاربر:\n\n"
        f"🆔 آیدی: {user['telegram_id']}\n"
        f"📛 نام: {user['first_name'] or '-'} {user['last_name'] or ''}\n"
        f"👤 یوزرنیم: @{user['username'] or 'ندارد'}\n"
        f"📅 تاریخ عضویت: {user['created_at']}\n\n"
    )

    if configs:
        text += f"📋 کانفیگ‌های فعال ({len(configs)}):\n"
        for c in configs:
            expire_date = datetime.fromisoformat(c["expire_date"])
            remaining = max(0, (expire_date - datetime.now()).days)
            text += f"  • {c['server_name']} ({c['location']}) - {remaining} روز مانده\n"
    else:
        text += "📋 کانفیگ فعالی ندارد.\n"

    text += f"\n📦 تعداد سفارشات: {len(orders)}\n"

    await callback.message.edit_text(text, reply_markup=admin_menu_keyboard())
    await callback.answer()
```

---

### Task 13: Admin Payment Approval

**Files:**
- Create: `bot/handlers/admin/payments.py`

- [ ] **Step 1: Create bot/handlers/admin/payments.py**

```python
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery
from datetime import datetime, timedelta

from bot.config import ADMIN_IDS
from bot.database import db
from bot.services.xui import XUIClient
from bot.keyboards.reply import main_menu_keyboard

router = Router()


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

    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n⏳ در حال ساخت کانفیگ..."
    )

    try:
        xui = XUIClient(order["server_url"], order["server_username"], order["server_password"])

        email = f"user_{order['telegram_id']}_{order_id}"

        client_uuid = await xui.add_client(
            inbound_id=order["inbound_id"],
            email=email,
            traffic_gb=order["traffic_gb"],
            expire_days=order["duration_days"],
        )

        config_link = await xui.get_sub_link(email)

        expire_date = (datetime.now() + timedelta(days=order["duration_days"])).isoformat()

        await db.add_config(
            user_id=order["user_id"] if isinstance(order["user_id"], int) else order["user_id"],
            order_id=order_id,
            server_id=order["server_id"] if isinstance(order["server_id"], int) else order["server_id"],
            client_email=email,
            config_link=config_link,
            expire_date=expire_date,
            traffic_limit_gb=order["traffic_gb"],
        )

        await db.update_order_status(order_id, "approved", config_link)

        await callback.message.edit_caption(
            caption=callback.message.caption.replace(
                "⏳ در حال ساخت کانفیگ...",
                f"✅ تایید شد توسط ادمین\n🔗 {config_link}"
            )
        )

        await bot.send_message(
            chat_id=order["telegram_id"],
            text=(
                f"✅ سفارش #{order_id} تایید شد!\n\n"
                f"📦 پلن: {order['plan_name']}\n"
                f"📊 حجم: {order['traffic_gb']} GB\n"
                f"📅 مدت: {order['duration_days']} روز\n"
                f"📍 لوکیشن: {order['location']}\n\n"
                f"🔗 لینک کانفیگ:\n`{config_link}`\n\n"
                "از بخش «آموزش اتصال» نحوه استفاده را ببینید."
            ),
            parse_mode="Markdown",
        )

        await callback.answer("✅ کانفیگ ساخته و ارسال شد!")

    except Exception as e:
        await callback.message.edit_caption(
            caption=callback.message.caption.replace(
                "⏳ در حال ساخت کانفیگ...",
                f"❌ خطا در ساخت کانفیگ:\n{str(e)}"
            )
        )
        await callback.answer("❌ خطا!", show_alert=True)


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
```

---

### Task 14: Admin Settings & Tutorial Management

**Files:**
- Create: `bot/handlers/admin/settings.py`

- [ ] **Step 1: Create bot/handlers/admin/settings.py**

```python
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import (
    admin_settings_keyboard, admin_tutorial_keyboard,
    admin_menu_keyboard, cancel_keyboard
)

router = Router()

PLATFORM_NAMES = {
    "android": "اندروید",
    "ios": "iOS",
    "windows": "ویندوز",
}


class SettingsStates(StatesGroup):
    waiting_card_number = State()
    waiting_card_holder = State()
    waiting_tutorial_text = State()


@router.callback_query(F.data == "admin:settings")
async def settings_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return

    card_number = await db.get_setting("card_number", "تنظیم نشده")
    card_holder = await db.get_setting("card_holder", "تنظیم نشده")

    await callback.message.edit_text(
        f"⚙️ تنظیمات:\n\n"
        f"💳 شماره کارت: {card_number}\n"
        f"👤 صاحب کارت: {card_holder}\n",
        reply_markup=admin_settings_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "admin:set_card_number")
async def set_card_number(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "💳 شماره کارت جدید را وارد کنید:",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(SettingsStates.waiting_card_number)
    await callback.answer()


@router.message(SettingsStates.waiting_card_number)
async def save_card_number(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await db.set_setting("card_number", message.text.strip())
    await message.answer("✅ شماره کارت ذخیره شد!", reply_markup=admin_settings_keyboard())
    await state.clear()


@router.callback_query(F.data == "admin:set_card_holder")
async def set_card_holder(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "👤 نام صاحب کارت را وارد کنید:",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(SettingsStates.waiting_card_holder)
    await callback.answer()


@router.message(SettingsStates.waiting_card_holder)
async def save_card_holder(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await db.set_setting("card_holder", message.text.strip())
    await message.answer("✅ نام صاحب کارت ذخیره شد!", reply_markup=admin_settings_keyboard())
    await state.clear()


@router.callback_query(F.data == "admin:tutorials")
async def tutorials_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.edit_text(
        "📖 مدیریت آموزش‌ها:\nپلتفرم مورد نظر را انتخاب کنید:",
        reply_markup=admin_tutorial_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:edit_tutorial:"))
async def edit_tutorial(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        return
    platform = callback.data.split(":")[2]
    platform_name = PLATFORM_NAMES.get(platform, platform)

    current_text = await db.get_setting(f"tutorial_{platform}", "تنظیم نشده")

    await state.update_data(tutorial_platform=platform)
    await callback.message.edit_text(
        f"📖 آموزش {platform_name}:\n\n"
        f"متن فعلی:\n{current_text}\n\n"
        "متن جدید را ارسال کنید:",
        reply_markup=cancel_keyboard()
    )
    await state.set_state(SettingsStates.waiting_tutorial_text)
    await callback.answer()


@router.message(SettingsStates.waiting_tutorial_text)
async def save_tutorial(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    platform = data.get("tutorial_platform")
    await db.set_setting(f"tutorial_{platform}", message.text)
    platform_name = PLATFORM_NAMES.get(platform, platform)
    await message.answer(
        f"✅ آموزش {platform_name} ذخیره شد!",
        reply_markup=admin_tutorial_keyboard()
    )
    await state.clear()
```

---

### Task 15: Main Entry Point

**Files:**
- Create: `bot/main.py`
- Create: `bot/utils/__init__.py`
- Create: `bot/utils/helpers.py`

- [ ] **Step 1: Create bot/utils/__init__.py**

Empty file.

- [ ] **Step 2: Create bot/utils/helpers.py**

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger("vpn_bot")
```

- [ ] **Step 3: Create bot/main.py**

```python
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import BOT_TOKEN
from bot.database.models import init_db

from bot.handlers.start import router as start_router
from bot.handlers.buy import router as buy_router
from bot.handlers.my_configs import router as my_configs_router
from bot.handlers.tutorial import router as tutorial_router
from bot.handlers.admin.menu import router as admin_menu_router
from bot.handlers.admin.servers import router as admin_servers_router
from bot.handlers.admin.plans import router as admin_plans_router
from bot.handlers.admin.users import router as admin_users_router
from bot.handlers.admin.payments import router as admin_payments_router
from bot.handlers.admin.settings import router as admin_settings_router

logging.basicConfig(level=logging.INFO)


async def main():
    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_routers(
        start_router,
        buy_router,
        my_configs_router,
        tutorial_router,
        admin_menu_router,
        admin_servers_router,
        admin_plans_router,
        admin_users_router,
        admin_payments_router,
        admin_settings_router,
    )

    logging.info("Bot starting...")
    await dp.start_polling(bot)
```

- [ ] **Step 4: Run the bot to verify it starts**

First create a `.env` file with your bot token and admin ID, then run:

```bash
python run.py
```

Expected: Bot starts polling, logs `Bot starting...`

---

### Task 16: Verification

- [ ] **Step 1: Verify all imports work**

Run: `python -c "from bot.main import main; print('All imports OK')"` from `D:\newbot`
Expected: `All imports OK`

- [ ] **Step 2: Verify database initialization**

Run: `python -c "import asyncio; from bot.database.models import init_db; asyncio.run(init_db()); print('DB initialized')"` from `D:\newbot`
Expected: `DB initialized`

- [ ] **Step 3: Manual bot test checklist**

1. Send `/start` → should ask to join channel (if CHANNEL_ID configured) or show menu
2. Join channel → tap "بررسی عضویت" → should show menu
3. Check that "مدیریت" button only appears for admin IDs
4. Test admin flow: add server, add plan, set card number
5. Test buy flow: select plan → select server → upload receipt → admin approves
6. Test "کانفیگ های من" → should show config with traffic info
7. Test "آموزش اتصال" → should show platform buttons
