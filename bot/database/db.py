import bot.database.models as models
from datetime import datetime
import secrets
import uuid as uuidlib


async def add_user(telegram_id: int, username: str = None, first_name: str = None, last_name: str = None):
    async with models.pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO users (telegram_id, username, first_name, last_name)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (telegram_id) DO UPDATE
               SET username=$2, first_name=$3, last_name=$4""",
            telegram_id, username, first_name, last_name
        )
        return await conn.fetchrow("SELECT * FROM users WHERE telegram_id=$1", telegram_id)


async def get_user_by_telegram_id(telegram_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE telegram_id=$1", telegram_id)


async def get_user_by_id(user_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE id=$1", user_id)


async def get_user_by_sub_token(sub_token: str):
    async with models.pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE sub_token=$1", sub_token)


async def get_or_create_sub_identity(user_id: int):
    async with models.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT sub_token, client_uuid FROM users WHERE id=$1", user_id
        )
        sub_token = row["sub_token"] if row else None
        client_uuid = row["client_uuid"] if row else None
        if not sub_token:
            sub_token = secrets.token_hex(16)
        if not client_uuid:
            client_uuid = str(uuidlib.uuid4())
        await conn.execute(
            "UPDATE users SET sub_token=$1, client_uuid=$2 WHERE id=$3",
            sub_token, client_uuid, user_id,
        )
        return {"sub_token": sub_token, "client_uuid": client_uuid}


async def search_user(query: str):
    async with models.pool.acquire() as conn:
        if query.isdigit():
            return await conn.fetch("SELECT * FROM users WHERE telegram_id=$1", int(query))
        else:
            return await conn.fetch("SELECT * FROM users WHERE username ILIKE $1", f"%{query}%")


async def add_server(name: str, url: str, location: str, api_token: str = "", username: str = "", password: str = "", inbound_id: int = 0, sub_port: int = 2096):
    async with models.pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO servers (name, url, username, password, api_token, location, inbound_id, sub_port)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id""",
            name, url, username, password, api_token, location, inbound_id, sub_port
        )


async def get_active_servers():
    async with models.pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM servers WHERE is_active=TRUE")


async def get_all_servers():
    async with models.pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM servers ORDER BY created_at DESC")


async def get_server(server_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM servers WHERE id=$1", server_id)


async def toggle_server(server_id: int):
    async with models.pool.acquire() as conn:
        await conn.execute("UPDATE servers SET is_active = NOT is_active WHERE id=$1", server_id)


async def set_server_inbound_ids(server_id: int, inbound_ids: str):
    async with models.pool.acquire() as conn:
        await conn.execute("UPDATE servers SET inbound_ids=$1 WHERE id=$2", inbound_ids, server_id)


async def add_server_inbound_id(server_id: int, inbound_id: int) -> str:
    server = await get_server(server_id)
    ids = parse_inbound_ids(server["inbound_ids"] if server else "")
    if inbound_id not in ids:
        ids.append(inbound_id)
    cleaned = ",".join(str(i) for i in ids)
    await set_server_inbound_ids(server_id, cleaned)
    return cleaned


async def remove_server_inbound_id(server_id: int, inbound_id: int) -> str:
    server = await get_server(server_id)
    ids = [i for i in parse_inbound_ids(server["inbound_ids"] if server else "") if i != inbound_id]
    cleaned = ",".join(str(i) for i in ids)
    await set_server_inbound_ids(server_id, cleaned)
    return cleaned


def parse_inbound_ids(value: str) -> list:
    if not value:
        return []
    ids = []
    for part in value.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


async def delete_server(server_id: int):
    async with models.pool.acquire() as conn:
        await conn.execute("DELETE FROM servers WHERE id=$1", server_id)


async def add_plan(name: str, traffic_gb: int, duration_days: int, price: int, max_users: int = 0):
    async with models.pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO plans (name, traffic_gb, duration_days, price, max_users)
               VALUES ($1, $2, $3, $4, $5) RETURNING id""",
            name, traffic_gb, duration_days, price, max_users
        )


async def get_active_plans():
    async with models.pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM plans WHERE is_active=TRUE ORDER BY price")


async def get_all_plans():
    async with models.pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM plans ORDER BY created_at DESC")


async def get_plan(plan_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM plans WHERE id=$1", plan_id)


async def toggle_plan(plan_id: int):
    async with models.pool.acquire() as conn:
        await conn.execute("UPDATE plans SET is_active = NOT is_active WHERE id=$1", plan_id)


async def update_plan_field(plan_id: int, field: str, value):
    allowed = {"name", "traffic_gb", "duration_days", "max_users", "price"}
    if field not in allowed:
        raise ValueError("invalid field")
    async with models.pool.acquire() as conn:
        await conn.execute(f"UPDATE plans SET {field}=$1 WHERE id=$2", value, plan_id)


async def delete_plan(plan_id: int):
    async with models.pool.acquire() as conn:
        await conn.execute("DELETE FROM plans WHERE id=$1", plan_id)


async def create_order(user_id: int, plan_id: int, receipt_photo_id: str, server_id: int = None,
                       renew_config_id: int = None):
    async with models.pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO orders (user_id, plan_id, server_id, receipt_photo_id, renew_config_id)
               VALUES ($1, $2, $3, $4, $5) RETURNING id""",
            user_id, plan_id, server_id, receipt_photo_id, renew_config_id
        )


async def get_order(order_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetchrow(
            """SELECT o.*, u.telegram_id, u.username, u.first_name,
                      p.name as plan_name, p.traffic_gb, p.duration_days, p.price, p.max_users,
                      s.name as server_name, s.location, s.url as server_url,
                      s.username as server_username, s.password as server_password,
                      s.api_token as server_api_token, s.inbound_id
               FROM orders o
               JOIN users u ON o.user_id = u.id
               JOIN plans p ON o.plan_id = p.id
               LEFT JOIN servers s ON o.server_id = s.id
               WHERE o.id=$1""",
            order_id
        )


async def update_order_status(order_id: int, status: str, config_link: str = None):
    async with models.pool.acquire() as conn:
        if config_link:
            await conn.execute(
                "UPDATE orders SET status=$1, config_link=$2, reviewed_at=$3 WHERE id=$4",
                status, config_link, datetime.now(), order_id
            )
        else:
            await conn.execute(
                "UPDATE orders SET status=$1, reviewed_at=$2 WHERE id=$3",
                status, datetime.now(), order_id
            )


async def get_pending_orders():
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            """SELECT o.*, u.telegram_id, u.username, u.first_name,
                      p.name as plan_name, p.traffic_gb, p.duration_days, p.price,
                      s.name as server_name, s.location
               FROM orders o
               JOIN users u ON o.user_id = u.id
               JOIN plans p ON o.plan_id = p.id
               LEFT JOIN servers s ON o.server_id = s.id
               WHERE o.status='pending'
               ORDER BY o.created_at DESC"""
        )


async def add_config(user_id: int, order_id: int, server_id: int, client_email: str,
                     config_link: str, expire_date, traffic_limit_gb: int):
    if isinstance(expire_date, str):
        expire_date = datetime.fromisoformat(expire_date)
    async with models.pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO configs (user_id, order_id, server_id, client_email, config_link,
                                   expire_date, traffic_limit_gb)
               VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
            user_id, order_id, server_id, client_email, config_link, expire_date, traffic_limit_gb
        )


async def get_user_configs(user_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            """SELECT c.*, s.name as server_name, s.location, s.url as server_url,
                      s.username as server_username, s.password as server_password,
                      s.api_token as server_api_token
               FROM configs c
               JOIN servers s ON c.server_id = s.id
               WHERE c.user_id=$1 AND c.is_active=TRUE
               ORDER BY c.created_at DESC""",
            user_id
        )


async def get_user_configs_by_telegram_id(telegram_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            """SELECT c.*, s.name as server_name, s.location, s.url as server_url,
                      s.username as server_username, s.password as server_password,
                      s.api_token as server_api_token
               FROM configs c
               JOIN servers s ON c.server_id = s.id
               JOIN users u ON c.user_id = u.id
               WHERE u.telegram_id=$1 AND c.is_active=TRUE
               ORDER BY c.created_at DESC""",
            telegram_id
        )


async def get_user_orders(user_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            """SELECT o.*, p.name as plan_name, p.traffic_gb, p.duration_days, p.price,
                      s.name as server_name, s.location
               FROM orders o
               JOIN plans p ON o.plan_id = p.id
               LEFT JOIN servers s ON o.server_id = s.id
               WHERE o.user_id=$1
               ORDER BY o.created_at DESC
               LIMIT 20""",
            user_id
        )


async def get_setting(key: str, default: str = ""):
    async with models.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM settings WHERE key=$1", key)
        return row["value"] if row else default


async def set_setting(key: str, value: str):
    async with models.pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO settings (key, value) VALUES ($1, $2)
               ON CONFLICT (key) DO UPDATE SET value=$2""",
            key, value
        )


async def get_master_server():
    async with models.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM servers WHERE is_active=TRUE ORDER BY id LIMIT 1"
        )
        return row


async def ensure_user_sub_token(user_id: int) -> str:
    identity = await get_or_create_sub_identity(user_id)
    return identity["sub_token"]


async def set_user_sub_token(user_id: int, sub_token: str):
    async with models.pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET sub_token=$1 WHERE id=$2", sub_token, user_id
        )


async def create_config(user_id: int, order_id: int, plan_id: int, client_email: str,
                        sub_id: str, sub_url: str, traffic_gb: int, expire_date):
    if isinstance(expire_date, str):
        expire_date = datetime.fromisoformat(expire_date)
    async with models.pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO configs
                   (user_id, order_id, plan_id, client_email, sub_id, config_link,
                    traffic_limit_gb, expire_date, is_active,
                    reminder_traffic_sent, reminder_time_sent)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE, FALSE, FALSE)
               RETURNING id""",
            user_id, order_id, plan_id, client_email, sub_id, sub_url,
            traffic_gb, expire_date
        )


async def get_config(config_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetchrow(
            """SELECT c.*, c.config_link AS sub_url, c.traffic_limit_gb AS traffic_gb,
                      p.name AS plan_name, p.duration_days
               FROM configs c
               LEFT JOIN plans p ON c.plan_id = p.id
               WHERE c.id=$1""",
            config_id
        )


async def get_configs_by_telegram_id(telegram_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            """SELECT c.*, c.config_link AS sub_url, c.traffic_limit_gb AS traffic_gb,
                      p.name AS plan_name, p.duration_days
               FROM configs c
               JOIN users u ON c.user_id = u.id
               LEFT JOIN plans p ON c.plan_id = p.id
               WHERE u.telegram_id=$1 AND c.is_active=TRUE
               ORDER BY c.created_at DESC""",
            telegram_id
        )


async def get_configs_by_user_id(user_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            """SELECT c.*, c.config_link AS sub_url, c.traffic_limit_gb AS traffic_gb,
                      p.name AS plan_name, p.duration_days
               FROM configs c
               LEFT JOIN plans p ON c.plan_id = p.id
               WHERE c.user_id=$1 AND c.is_active=TRUE
               ORDER BY c.created_at DESC""",
            user_id
        )


async def get_all_active_configs():
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            """SELECT c.*, c.config_link AS sub_url, c.traffic_limit_gb AS traffic_gb,
                      u.telegram_id, p.name AS plan_name
               FROM configs c
               JOIN users u ON c.user_id = u.id
               LEFT JOIN plans p ON c.plan_id = p.id
               WHERE c.is_active=TRUE"""
        )


async def renew_config(config_id: int, order_id: int, plan_id: int, traffic_gb: int, expire_date):
    if isinstance(expire_date, str):
        expire_date = datetime.fromisoformat(expire_date)
    async with models.pool.acquire() as conn:
        await conn.execute(
            """UPDATE configs
               SET order_id=$1, plan_id=$2, traffic_limit_gb=$3, expire_date=$4,
                   is_active=TRUE, reminder_traffic_sent=FALSE, reminder_time_sent=FALSE
               WHERE id=$5""",
            order_id, plan_id, traffic_gb, expire_date, config_id
        )


async def mark_config_reminder(config_id: int, kind: str):
    field = "reminder_traffic_sent" if kind == "traffic" else "reminder_time_sent"
    async with models.pool.acquire() as conn:
        await conn.execute(
            f"UPDATE configs SET {field}=TRUE WHERE id=$1", config_id
        )


async def add_app(platform: str, url: str, title: str = ""):
    async with models.pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO apps (platform, url, title) VALUES ($1, $2, $3) RETURNING id",
            platform, url, title
        )


async def get_app(app_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM apps WHERE id=$1", app_id)


async def get_apps(platform: str):
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM apps WHERE platform=$1 ORDER BY id", platform
        )


async def update_app(app_id: int, title: str = None, url: str = None):
    async with models.pool.acquire() as conn:
        await conn.execute(
            """UPDATE apps SET
                   title = COALESCE($2, title),
                   url = COALESCE($3, url)
               WHERE id=$1""",
            app_id, title, url
        )


async def delete_app(app_id: int):
    async with models.pool.acquire() as conn:
        await conn.execute("DELETE FROM apps WHERE id=$1", app_id)


async def has_active_refund(config_id: int) -> bool:
    async with models.pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM refunds WHERE config_id=$1 AND status IN ('pending','approved') LIMIT 1",
            config_id
        )
        return row is not None


async def create_refund(user_id: int, config_id: int, card_number: str, card_holder: str):
    async with models.pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO refunds (user_id, config_id, card_number, card_holder)
               VALUES ($1, $2, $3, $4) RETURNING id""",
            user_id, config_id, card_number, card_holder
        )


async def get_refund(refund_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetchrow(
            """SELECT r.*, u.telegram_id, u.first_name, u.username,
                      c.client_email, c.created_at AS config_created_at,
                      c.expire_date, c.config_link AS sub_url,
                      c.traffic_limit_gb AS traffic_gb, p.name AS plan_name
               FROM refunds r
               JOIN users u ON r.user_id = u.id
               JOIN configs c ON r.config_id = c.id
               LEFT JOIN plans p ON c.plan_id = p.id
               WHERE r.id=$1""",
            refund_id
        )


async def update_refund(refund_id: int, status: str = None, admin_response: str = None,
                        receipt_photo_id: str = None):
    async with models.pool.acquire() as conn:
        await conn.execute(
            """UPDATE refunds SET
                   status = COALESCE($2, status),
                   admin_response = COALESCE($3, admin_response),
                   receipt_photo_id = COALESCE($4, receipt_photo_id),
                   reviewed_at = NOW()
               WHERE id=$1""",
            refund_id, status, admin_response, receipt_photo_id
        )
