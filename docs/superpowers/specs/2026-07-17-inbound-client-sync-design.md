# Inbound-Client Sync Design

## Summary
When admin updates server inbound IDs, all clients on that server are automatically re-attached to the new inbound list via background task, with a completion notification to the admin.

## Motivation
Currently, admin can add/remove/replace inbound IDs per server, but only the database is updated. Existing clients remain attached to the old inbound set until manually deleted and re-created. This feature ensures inbound changes are immediately propagated to all clients.

## Design

### Data Flow
```
Admin changes inbound(s) → DB updated → background task created
    → loop all configs for the server → xui.attach_client(email, new_inbound_ids)
    → send summary message to admin
```

### Changes (4 files)

#### 1. `bot/services/xui.py` — Update `attach_client`
```python
async def attach_client(self, email: str, inbound_ids: list) -> bool:
    data = await self._request(
        "POST",
        f"/panel/api/clients/{email}/attach",
        json={"inboundIds": inbound_ids}
    )
    return data.get("success", False)
```
Current signature `attach_client(self, email)` is extended to accept `inbound_ids` list.
**Backward compat:** existing callers are zero — the method is defined but never called.

#### 2. `bot/database/db.py` — New query
```python
async def get_configs_by_server_id(server_id: int):
    """Return all configs for a server, regardless of active status."""
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM configs WHERE server_id = $1", server_id
        )
```

#### 3. `bot/handlers/admin/servers.py` — Background sync task + triggers
New helper function:
```python
async def _sync_clients_to_inbounds(server_id: int, inbound_ids: list, admin_id: int):
    """Background task: re-attach all server clients to new inbound set."""
    configs = await db.get_configs_by_server_id(server_id)
    success = 0
    failed = 0

    xui = XUIClient(server["url"], server["username"], server["password"], server["api_token"])
    for cfg in configs:
        try:
            ok = await xui.attach_client(cfg["client_email"], inbound_ids)
            if ok:
                success += 1
            else:
                failed += 1
        except Exception:
            failed += 1
            logger.exception(f"Sync failed for {cfg['client_email']}")

    await bot.send_message(
        admin_id,
        f"همگام‌سازی اینباندها کامل شد\n"
        f"موفق: {success}\nخطا: {failed}"
    )
```

Trigger points (after each DB mutation):
- `inbound_delete` (line 266): after `db.remove_server_inbound_id()`
- `inbound_add_save` (line 293): after `db.add_server_inbound_id()`
- `save_inbounds` (line 324): after `db.set_server_inbound_ids()`

Each trigger adds: `asyncio.ensure_future(_sync_clients_to_inbounds(server_id, new_ids, callback.from_user.id))`

#### 4. No auth/model changes needed.
All infrastructure exists: XUIClient, db pool, background task support.

### Error Handling
- Individual client failures are logged and counted; do not abort the batch.
- XUIClient errors (auth, timeout) are caught per-request.
- Failed count > 0 → admin sees the count in completion message.

### Scope
- Only the server whose inbounds were edited is affected.
- All configs (active + inactive + expired) are updated.
- One background task per edit operation (independently triggered).

### Testing
- Manual: change inbound on a server with existing clients, verify attach calls succeed, verify notification message accuracy.
- Automated: unit test `_sync_clients_to_inbounds` with mocked XUIClient.
