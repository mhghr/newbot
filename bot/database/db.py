import bot.database.models as models
from datetime import datetime
import ipaddress
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


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


async def search_user(query: str):
    query = (query or "").strip()
    if not query:
        return []
    pattern = _like_pattern(query)
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            """SELECT DISTINCT u.*
               FROM users u
               LEFT JOIN configs c ON c.user_id = u.id
               WHERE u.telegram_id::text ILIKE $1
                  OR u.username ILIKE $1
                  OR u.first_name ILIKE $1
                  OR u.last_name ILIKE $1
                  OR c.client_email ILIKE $1
                  OR c.sub_id ILIKE $1
                  OR c.wg_client_ip ILIKE $1
                  OR c.wg_public_key ILIKE $1
               ORDER BY u.id""",
            pattern,
        )


async def add_server(name: str, url: str, location: str, api_token: str = "", username: str = "", password: str = "", inbound_id: int = 0, sub_port: int = 2096, sub_domain: str = "", service_type: str = "v2ray", api_port: int = 8728, wg_interface: str = "", wg_server_public_key: str = "", wg_endpoint: str = "", wg_port: int = 51820, wg_client_subnet: str = "", wg_dns: str = "1.1.1.1,8.8.8.8", wg_ip_range_start: int = 10, wg_ip_range_end: int = 250):
    async with models.pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO servers
                   (name, url, username, password, api_token, location, inbound_id,
                    sub_port, sub_domain, service_type, api_port, wg_interface,
                    wg_server_public_key, wg_endpoint, wg_port, wg_client_subnet,
                    wg_dns, wg_ip_range_start, wg_ip_range_end)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                       $13, $14, $15, $16, $17, $18, $19) RETURNING id""",
            name, url, username, password, api_token, location, inbound_id,
            sub_port, sub_domain, service_type, api_port, wg_interface,
            wg_server_public_key, wg_endpoint, wg_port, wg_client_subnet,
            wg_dns, wg_ip_range_start, wg_ip_range_end
        )


async def get_active_servers(service_type: str = None):
    async with models.pool.acquire() as conn:
        if service_type:
            return await conn.fetch(
                "SELECT * FROM servers WHERE is_active=TRUE AND service_type=$1",
                service_type
            )
        return await conn.fetch("SELECT * FROM servers WHERE is_active=TRUE")


async def get_active_server_by_type(service_type: str):
    async with models.pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM servers WHERE is_active=TRUE AND service_type=$1 ORDER BY id LIMIT 1",
            service_type
        )


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


async def update_server_field(server_id: int, field: str, value):
    allowed = {
        "name", "url", "api_token", "location", "inbound_id", "sub_port", "sub_domain",
        "service_type", "api_port", "wg_interface", "wg_server_public_key", "wg_endpoint",
        "wg_port", "wg_client_subnet", "wg_dns", "wg_ip_range_start", "wg_ip_range_end",
    }
    if field not in allowed:
        raise ValueError("invalid field")
    if field in ("inbound_id", "sub_port", "api_port", "wg_port", "wg_ip_range_start", "wg_ip_range_end"):
        value = int(value)
    else:
        value = str(value)
    async with models.pool.acquire() as conn:
        await conn.execute(f"UPDATE servers SET {field}=$1 WHERE id=$2", value, server_id)


async def add_plan(name: str, traffic_gb: int, duration_days: int, price: int, max_users: int = 0, service_type: str = "v2ray"):
    async with models.pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO plans (name, traffic_gb, duration_days, price, max_users, service_type)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            name, traffic_gb, duration_days, price, max_users, service_type
        )


async def get_active_plans(service_type: str = None):
    async with models.pool.acquire() as conn:
        if service_type:
            return await conn.fetch(
                "SELECT * FROM plans WHERE is_active=TRUE AND service_type=$1 ORDER BY price",
                service_type
            )
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
    allowed = {"name", "traffic_gb", "duration_days", "max_users", "price", "service_type"}
    if field not in allowed:
        raise ValueError("invalid field")
    async with models.pool.acquire() as conn:
        await conn.execute(f"UPDATE plans SET {field}=$1 WHERE id=$2", value, plan_id)


async def delete_plan(plan_id: int):
    async with models.pool.acquire() as conn:
        await conn.execute("DELETE FROM plans WHERE id=$1", plan_id)


async def create_order(user_id: int, plan_id: int, receipt_photo_id: str, server_id: int = None,
                       renew_config_id: int = None, service_type: str = "v2ray"):
    async with models.pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO orders (user_id, plan_id, server_id, receipt_photo_id, renew_config_id, service_type)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
            user_id, plan_id, server_id, receipt_photo_id, renew_config_id, service_type
        )


async def get_order(order_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetchrow(
            """SELECT o.*, u.telegram_id, u.username, u.first_name,
                      p.name as plan_name, p.traffic_gb, p.duration_days, p.price, p.max_users,
                      p.service_type as plan_service_type,
                      s.name as server_name, s.location, s.url as server_url,
                      s.username as server_username, s.password as server_password,
                      s.api_token as server_api_token, s.inbound_id,
                      s.service_type as server_service_type, s.api_port,
                      s.wg_interface, s.wg_server_public_key, s.wg_endpoint,
                      s.wg_port, s.wg_client_subnet, s.wg_dns,
                      s.wg_ip_range_start, s.wg_ip_range_end
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


async def get_proxy_target() -> str:
    from bot.config import CHANNEL_ID
    target = await get_setting("proxy_target_channel", "")
    if not target and CHANNEL_ID:
        target = str(CHANNEL_ID)
    return target


async def get_master_server(service_type: str = None):
    async with models.pool.acquire() as conn:
        if service_type:
            return await conn.fetchrow(
                "SELECT * FROM servers WHERE is_active=TRUE AND service_type=$1 ORDER BY id LIMIT 1",
                service_type
            )
        return await conn.fetchrow(
            "SELECT * FROM servers WHERE is_active=TRUE ORDER BY id LIMIT 1"
        )


async def ensure_user_sub_token(user_id: int) -> str:
    identity = await get_or_create_sub_identity(user_id)
    return identity["sub_token"]


async def set_user_sub_token(user_id: int, sub_token: str):
    async with models.pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET sub_token=$1 WHERE id=$2", sub_token, user_id
        )


async def create_config(user_id: int, order_id: int, plan_id: int, client_email: str,
                        sub_id: str, sub_url: str, traffic_gb: int, expire_date,
                        server_id: int = None):
    if isinstance(expire_date, str):
        expire_date = datetime.fromisoformat(expire_date)
    async with models.pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO configs
                   (user_id, order_id, plan_id, client_email, sub_id, config_link,
                    traffic_limit_gb, expire_date, server_id, service_type, is_active,
                    reminder_traffic_sent, reminder_time_sent)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'v2ray', TRUE, FALSE, FALSE)
               RETURNING id""",
            user_id, order_id, plan_id, client_email, sub_id, sub_url,
            traffic_gb, expire_date, server_id
        )


async def create_wg_config(user_id: int, order_id: int, plan_id: int, client_email: str,
                           config_text: str, traffic_gb: int, expire_date,
                           server_id: int, client_ip: str, public_key: str,
                           private_key: str, server_public_key: str,
                           endpoint: str, port: int, peer_id: str = None):
    """Insert a WireGuard config row. ``config_text`` is the .conf body."""
    if isinstance(expire_date, str):
        expire_date = datetime.fromisoformat(expire_date)
    async with models.pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO configs
                   (user_id, order_id, plan_id, client_email, config_link, sub_id,
                    traffic_limit_gb, expire_date, server_id, service_type,
                    wg_client_ip, wg_public_key, wg_private_key, wg_server_public_key,
                    wg_endpoint, wg_port, wg_peer_id, is_active,
                    reminder_traffic_sent, reminder_time_sent)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'wireguard',
                       $10, $11, $12, $13, $14, $15, $16, TRUE, FALSE, FALSE)
               RETURNING id""",
            user_id, order_id, plan_id, client_email, config_text, client_ip,
            traffic_gb, expire_date, server_id,
            client_ip, public_key, private_key, server_public_key,
            endpoint, port, peer_id
        )


async def get_config(config_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetchrow(
            """SELECT c.*, c.config_link AS sub_url, c.traffic_limit_gb AS traffic_gb,
                      p.name AS plan_name, p.duration_days, p.service_type AS plan_service_type,
                      s.service_type AS server_service_type, s.url AS server_url,
                      s.username AS server_username, s.password AS server_password,
                      s.api_token AS server_api_token, s.api_port AS server_api_port,
                      s.wg_interface AS server_wg_interface, s.wg_endpoint AS server_wg_endpoint,
                      s.wg_port AS server_wg_port, s.wg_client_subnet AS server_wg_client_subnet,
                      s.wg_dns AS server_wg_dns,
                      s.wg_ip_range_start AS server_wg_ip_range_start,
                      s.wg_ip_range_end AS server_wg_ip_range_end
               FROM configs c
               LEFT JOIN plans p ON c.plan_id = p.id
               LEFT JOIN servers s ON c.server_id = s.id
               WHERE c.id=$1""",
            config_id
        )


async def get_configs_by_telegram_id(telegram_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            """SELECT c.*, c.config_link AS sub_url, c.traffic_limit_gb AS traffic_gb,
                      p.name AS plan_name, p.duration_days,
                      s.service_type AS server_service_type, s.url AS server_url,
                      s.username AS server_username, s.password AS server_password,
                      s.api_token AS server_api_token, s.api_port AS server_api_port,
                      s.wg_interface AS server_wg_interface,
                      s.wg_endpoint AS server_wg_endpoint, s.wg_port AS server_wg_port
               FROM configs c
               JOIN users u ON c.user_id = u.id
               LEFT JOIN plans p ON c.plan_id = p.id
               LEFT JOIN servers s ON c.server_id = s.id
               WHERE u.telegram_id=$1 AND c.is_active=TRUE
               ORDER BY c.created_at DESC""",
            telegram_id
        )


async def get_configs_by_user_id(user_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            """SELECT c.*, c.config_link AS sub_url, c.traffic_limit_gb AS traffic_gb,
                      p.name AS plan_name, p.duration_days,
                      s.service_type AS server_service_type, s.url AS server_url,
                      s.username AS server_username, s.password AS server_password,
                      s.api_token AS server_api_token, s.api_port AS server_api_port,
                      s.wg_interface AS server_wg_interface,
                      s.wg_endpoint AS server_wg_endpoint, s.wg_port AS server_wg_port
               FROM configs c
               LEFT JOIN plans p ON c.plan_id = p.id
               LEFT JOIN servers s ON c.server_id = s.id
                WHERE c.user_id=$1 AND c.is_active=TRUE
               ORDER BY c.created_at DESC""",
            user_id
        )


async def delete_config(config_id: int):
    async with models.pool.acquire() as conn:
        await conn.execute(
            "UPDATE configs SET is_active=FALSE WHERE id=$1",
            config_id
        )


async def get_all_active_configs():
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            """SELECT c.*, c.config_link AS sub_url, c.traffic_limit_gb AS traffic_gb,
                      u.telegram_id, p.name AS plan_name,
                      s.service_type AS server_service_type, s.url AS server_url,
                      s.username AS server_username, s.password AS server_password,
                      s.api_token AS server_api_token, s.api_port AS server_api_port,
                      s.wg_interface AS server_wg_interface,
                      s.wg_endpoint AS server_wg_endpoint, s.wg_port AS server_wg_port,
                      s.wg_client_subnet AS server_wg_client_subnet
               FROM configs c
               JOIN users u ON c.user_id = u.id
               LEFT JOIN plans p ON c.plan_id = p.id
               LEFT JOIN servers s ON c.server_id = s.id
               WHERE c.is_active=TRUE"""
        )


async def get_configs_by_server_id(server_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM configs WHERE server_id = $1 AND is_active = TRUE", server_id
        )


async def get_config_by_server_and_email(server_id: int, client_email: str):
    async with models.pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM configs WHERE server_id=$1 AND client_email=$2",
            server_id, client_email
        )


async def upsert_config(server_id: int, user_id: int, client_email: str,
                        sub_id: str = "", sub_url: str = None, traffic_gb: int = 0,
                        expire_date=None, is_active: bool = True, plan_id: int = None) -> dict:
    """Insert or update a config by (server_id, client_email). Merge-safe."""
    if isinstance(expire_date, str):
        expire_date = datetime.fromisoformat(expire_date)
    existing = await get_config_by_server_and_email(server_id, client_email)
    async with models.pool.acquire() as conn:
        if existing:
            await conn.execute(
                """UPDATE configs SET
                       user_id=$1, sub_id=$2, config_link=$3, traffic_limit_gb=$4,
                       expire_date=$5, is_active=$6, plan_id=$7
                   WHERE id=$8""",
                user_id, sub_id, sub_url, traffic_gb, expire_date, is_active, plan_id,
                existing["id"]
            )
            return {"id": existing["id"], "created": False}
        return {
            "id": await conn.fetchval(
                """INSERT INTO configs
                       (server_id, user_id, client_email, sub_id, config_link,
                        traffic_limit_gb, expire_date, is_active, plan_id,
                        reminder_traffic_sent, reminder_time_sent)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, FALSE, FALSE)
                   RETURNING id""",
                server_id, user_id, client_email, sub_id, sub_url,
                traffic_gb, expire_date, is_active, plan_id
            ),
            "created": True,
        }


async def upsert_order(order_id: int, user_id: int, plan_id: int, config_link: str = None) -> dict:
    """Insert (or update) an order keeping its original id from the old database."""
    async with models.pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id FROM orders WHERE id=$1", order_id)
        if existing:
            await conn.execute(
                """UPDATE orders SET user_id=$1, plan_id=$2, status='approved',
                       config_link=$3, reviewed_at=NOW()
                   WHERE id=$4""",
                user_id, plan_id, config_link, order_id
            )
            return {"id": order_id, "created": False}
        await conn.execute(
            """INSERT INTO orders (id, user_id, plan_id, status, config_link, reviewed_at)
               VALUES ($1, $2, $3, 'approved', $4, NOW())""",
            order_id, user_id, plan_id, config_link
        )
        await conn.execute(
            """SELECT setval(pg_get_serial_sequence('orders', 'id'),
                            GREATEST((SELECT COALESCE(MAX(id), 1) FROM orders), 1))"""
        )
        return {"id": order_id, "created": True}


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
                      c.traffic_limit_gb AS traffic_gb, p.name AS plan_name,
                      c.service_type, c.wg_client_ip, c.wg_public_key, c.used_bytes,
                      c.server_id, s.service_type AS server_service_type,
                      s.url AS server_url, s.username AS server_username,
                      s.password AS server_password, s.api_token AS server_api_token,
                      s.api_port AS server_api_port,
                      s.wg_interface AS server_wg_interface
               FROM refunds r
               JOIN users u ON r.user_id = u.id
               JOIN configs c ON r.config_id = c.id
               LEFT JOIN plans p ON c.plan_id = p.id
               LEFT JOIN servers s ON c.server_id = s.id
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


# --------------------------------------------------------------------------- #
# WireGuard helpers
# --------------------------------------------------------------------------- #

def parse_wg_network(subnet: str):
    """Parse a subnet into an ip_network. Accepts '10.0.0.0/24' or '10.0.0.0'."""
    value = (subnet or "").strip()
    if not value:
        return None
    if "/" not in value:
        value += "/24"
    try:
        return ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None


def _host_number(ip: str, net):
    try:
        return int(ipaddress.ip_address(ip)) - int(net.network_address)
    except (ValueError, TypeError):
        return None


async def get_wg_used_host_numbers(server_id: int, subnet: str) -> set:
    """Host offsets already used by active WireGuard configs of a server."""
    net = parse_wg_network(subnet)
    if net is None:
        return set()
    async with models.pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT wg_client_ip FROM configs
               WHERE server_id=$1 AND service_type='wireguard' AND is_active=TRUE
                 AND wg_client_ip IS NOT NULL""",
            server_id
        )
    used = set()
    for row in rows:
        number = _host_number(row["wg_client_ip"], net)
        if number is not None and number > 0:
            used.add(number)
    return used


async def get_wg_configs_by_server(server_id: int):
    async with models.pool.acquire() as conn:
        return await conn.fetch(
            """SELECT * FROM configs
               WHERE server_id=$1 AND service_type='wireguard'""",
            server_id
        )


async def update_wg_usage(config_id: int, last_rx: int, last_tx: int, used_bytes: int):
    async with models.pool.acquire() as conn:
        await conn.execute(
            """UPDATE configs SET wg_last_rx=$1, wg_last_tx=$2, used_bytes=$3
               WHERE id=$4""",
            last_rx, last_tx, used_bytes, config_id
        )


async def set_config_peer_id(config_id: int, peer_id: str):
    async with models.pool.acquire() as conn:
        await conn.execute(
            "UPDATE configs SET wg_peer_id=$1 WHERE id=$2", peer_id, config_id
        )


async def renew_wg_config(config_id: int, order_id: int, plan_id: int,
                          traffic_gb: int, expire_date):
    """Renew a WireGuard config, resetting its consumed-traffic baseline."""
    if isinstance(expire_date, str):
        expire_date = datetime.fromisoformat(expire_date)
    async with models.pool.acquire() as conn:
        await conn.execute(
            """UPDATE configs
               SET order_id=$1, plan_id=$2, traffic_limit_gb=$3, expire_date=$4,
                   is_active=TRUE, used_bytes=0, wg_last_rx=0, wg_last_tx=0,
                   reminder_traffic_sent=FALSE, reminder_time_sent=FALSE
               WHERE id=$5""",
            order_id, plan_id, traffic_gb, expire_date, config_id
        )
