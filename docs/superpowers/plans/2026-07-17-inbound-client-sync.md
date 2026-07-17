# Inbound-Client Auto-Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When admin changes server inbounds, all clients on that server are automatically re-attached to the new inbound list via background task.

**Architecture:** After each inbound mutation (add/remove/replace) in the admin panel, a background `asyncio.create_task` iterates all configs for the server and calls the 3x-ui API `attach` endpoint with the updated inbound IDs. A summary message is sent to the admin on completion.

**Tech Stack:** Python, aiogram 3.x, httpx, asyncpg (existing project stack)

---

### File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `bot/services/xui.py` | Modify | Extend `attach_client()` to accept `inbound_ids` |
| `bot/database/db.py` | Modify | Add `get_configs_by_server_id()` query |
| `bot/handlers/admin/servers.py` | Modify | Add sync function + trigger calls at 3 mutation points |

---

### Task 1: Extend `attach_client` in XUIClient

**Files:**
- Modify: `bot/services/xui.py:232-234`

- [ ] **Step 1: Update `attach_client` method**

```python
async def attach_client(self, email: str, inbound_ids: list) -> bool:
    data = await self._request(
        "POST",
        f"/panel/api/clients/{email}/attach",
        json={"inboundIds": inbound_ids}
    )
    return data.get("success", False)
```

This sends the new inbound IDs in the request body. The `_request` method already passes `**kwargs` (including `json`) to `httpx.client.request`.

- [ ] **Step 2: Commit**

```bash
git add bot/services/xui.py
git commit -m "feat: extend attach_client to accept inbound_ids parameter"
```

---

### Task 2: Add `get_configs_by_server_id` to db

**Files:**
- Modify: `bot/database/db.py` (insert after `get_all_active_configs` at line 395)

- [ ] **Step 1: Add the query function**

```python
async def get_configs_by_server_id(server_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM configs WHERE server_id = $1", server_id
        )
```

Returns all configs (active + inactive + expired) for a given server.

- [ ] **Step 2: Commit**

```bash
git add bot/database/db.py
git commit -m "feat: add get_configs_by_server_id query"
```

---

### Task 3: Add sync function and triggers in admin servers handler

**Files:**
- Modify: `bot/handlers/admin/servers.py` (add import + sync function, modify 3 handlers)

- [ ] **Step 1: Add imports at top of `servers.py`**

Add `import asyncio` and `import logging` after line 1:

```python
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import asyncio
import logging

from bot.config import ADMIN_IDS
from bot.database import db
from bot.keyboards.inline import (
    servers_list_keyboard, server_actions_keyboard, server_inbounds_keyboard,
    server_delete_confirm_keyboard, cancel_keyboard
)
from bot.services.xui import XUIClient

router = Router()
logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Add `_sync_clients_to_inbounds` function**

Insert after the router definition (after line 14, before `FIELD_LABELS`):

```python
async def _sync_clients_to_inbounds(server: dict, inbound_ids: list, admin_chat_id: int, bot):
    success = 0
    failed = 0

    configs = await db.get_configs_by_server_id(server["id"])
    if not configs:
        await bot.send_message(admin_chat_id, "هیچ کانفیگی برای این سرور یافت نشد.")
        return

    xui = XUIClient(server["url"], server["username"], server["password"], server["api_token"])

    for cfg in configs:
        try:
            ok = await xui.attach_client(cfg["client_email"], inbound_ids)
            if ok:
                success += 1
            else:
                failed += 1
                logger.warning(f"attach_client returned false for {cfg['client_email']}")
        except Exception:
            failed += 1
            logger.exception(f"Sync failed for client {cfg['client_email']}")

    await bot.send_message(
        admin_chat_id,
        f"همگام‌سازی اینباندهای «{server['name']}» کامل شد\n"
        f"موفق: {success}\nخطا: {failed}"
    )
```

- [ ] **Step 3: Add trigger in `inbound_delete` (after line 274)**

Change the handler to get new inbound IDs and fire the sync after the db mutation:

```python
@router.callback_query(F.data.startswith("admin:inbound_del:"))
async def inbound_delete(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS: return
    parts = callback.data.split(":")
    server_id = int(parts[2]); inbound_id = int(parts[3])
    await db.remove_server_inbound_id(server_id, inbound_id)
    server = await db.get_server(server_id)
    ids = db.parse_inbound_ids(server["inbound_ids"])
    await callback.message.edit_text(
        f"📡 اینباندهای «{server['name']}»\n\n"
        f"فعلی: {', '.join(str(i) for i in ids) if ids else 'خالی'}",
        reply_markup=server_inbounds_keyboard(server_id, ids)
    )
    await callback.answer(f"✅ {inbound_id} حذف شد!")

    asyncio.create_task(_sync_clients_to_inbounds(server, ids, callback.from_user.id, callback.bot))
```

- [ ] **Step 4: Add trigger in `inbound_add_save` (after line 300)**

```python
@router.message(InboundStates.waiting_add_inbound)
async def inbound_add_save(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    data = await state.get_data(); server_id = data["inbounds_server_id"]
    ids = db.parse_inbound_ids(message.text)
    if not ids: await message.answer("⚠️ ورودی نامعتبر.", reply_markup=cancel_keyboard()); return
    for iid in ids: await db.add_server_inbound_id(server_id, iid)
    await state.clear()
    server = await db.get_server(server_id)
    cur_ids = db.parse_inbound_ids(server["inbound_ids"])
    await message.answer(
        f"✅ اضافه شد!\n📡 فعلی: {', '.join(str(i) for i in cur_ids) if cur_ids else 'خالی'}",
        reply_markup=server_inbounds_keyboard(server_id, cur_ids)
    )

    asyncio.create_task(_sync_clients_to_inbounds(server, cur_ids, message.from_user.id, message.bot))
```

- [ ] **Step 5: Add trigger in `save_inbounds` (after line 327)**

```python
@router.message(InboundStates.waiting_inbound_ids)
async def save_inbounds(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    data = await state.get_data(); server_id = data["inbounds_server_id"]
    ids = db.parse_inbound_ids(message.text)
    if not ids: await message.answer("⚠️ نامعتبر.", reply_markup=cancel_keyboard()); return
    cleaned = ",".join(str(i) for i in ids)
    await db.set_server_inbound_ids(server_id, cleaned)
    await state.clear()
    await message.answer(f"✅ ذخیره شد: {cleaned}", reply_markup=server_inbounds_keyboard(server_id, ids))

    asyncio.create_task(_sync_clients_to_inbounds(await db.get_server(server_id), ids, message.from_user.id, message.bot))
```

Note: In `save_inbounds`, we re-fetch the server via `await db.get_server(server_id)` to pass full server info to the sync function.

- [ ] **Step 6: Commit**

```bash
git add bot/handlers/admin/servers.py
git commit -m "feat: auto-sync client inbounds on server inbound change"
```

---

### Manual Verification

1. Ensure the bot has at least one server with inbounds configured
2. Create a client config on that server (via normal order flow)
3. Go to admin panel → Servers → select server → Inbounds
4. Add a new inbound ID
5. Check: after a few seconds, admin receives "همگام‌سازی اینباندها کامل شد" message
6. Repeat for delete and replace operations
