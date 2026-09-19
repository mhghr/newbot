import asyncpg
from bot.config import DATABASE_URL

pool: asyncpg.Pool = None


async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)

    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                sub_token TEXT UNIQUE,
                client_uuid TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS servers (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                username TEXT DEFAULT '',
                password TEXT DEFAULT '',
                api_token TEXT DEFAULT '',
                location TEXT NOT NULL,
                inbound_id INTEGER DEFAULT 0,
                inbound_ids TEXT DEFAULT '',
                sub_domain TEXT DEFAULT '',
                sub_port INTEGER DEFAULT 2096,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                traffic_gb INTEGER NOT NULL,
                duration_days INTEGER NOT NULL,
                price INTEGER NOT NULL,
                max_users INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                plan_id INTEGER NOT NULL REFERENCES plans(id),
                server_id INTEGER REFERENCES servers(id),
                status TEXT DEFAULT 'pending',
                receipt_photo_id TEXT,
                config_link TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                reviewed_at TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS configs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                order_id INTEGER NOT NULL REFERENCES orders(id),
                server_id INTEGER NOT NULL REFERENCES servers(id),
                client_email TEXT NOT NULL,
                config_link TEXT NOT NULL,
                expire_date TIMESTAMP NOT NULL,
                traffic_limit_gb INTEGER NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS apps (
                id SERIAL PRIMARY KEY,
                platform TEXT NOT NULL,
                title TEXT DEFAULT '',
                url TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        await conn.execute("""
            INSERT INTO apps (platform, title, url)
            SELECT * FROM (VALUES
                ('android', 'Happ', 'https://play.google.com/store/apps/details?id=com.happproxy'),
                ('ios', 'Happ', 'https://apps.apple.com/us/app/happ-proxy-utility/id6504287215')
            ) AS v(platform, title, url)
            WHERE NOT EXISTS (SELECT 1 FROM apps WHERE platform = v.platform)
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS refunds (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                config_id INTEGER NOT NULL REFERENCES configs(id),
                card_number TEXT NOT NULL,
                card_holder TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                admin_response TEXT,
                receipt_photo_id TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                reviewed_at TIMESTAMP
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS proxy_sources (
                id SERIAL PRIMARY KEY,
                channel TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                last_scan_id BIGINT DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS proxy_pending (
                id SERIAL PRIMARY KEY,
                text TEXT,
                photo_id TEXT,
                doc_id TEXT,
                target_channel TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_proxies (
                id SERIAL PRIMARY KEY,
                url TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        try:
            await conn.execute("ALTER TABLE servers ADD COLUMN IF NOT EXISTS api_token TEXT DEFAULT ''")
        except Exception:
            pass

        try:
            await conn.execute("ALTER TABLE servers ADD COLUMN IF NOT EXISTS inbound_ids TEXT DEFAULT ''")
        except Exception:
            pass

        try:
            await conn.execute("ALTER TABLE servers ADD COLUMN IF NOT EXISTS sub_port INTEGER DEFAULT 2096")
        except Exception:
            pass

        try:
            await conn.execute("ALTER TABLE servers ADD COLUMN IF NOT EXISTS sub_domain TEXT DEFAULT ''")
        except Exception:
            pass

        try:
            await conn.execute("ALTER TABLE plans ADD COLUMN IF NOT EXISTS max_users INTEGER DEFAULT 0")
        except Exception:
            pass

        try:
            await conn.execute("ALTER TABLE apps ADD COLUMN IF NOT EXISTS title TEXT DEFAULT ''")
        except Exception:
            pass

        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS sub_token TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS client_uuid TEXT")
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS users_sub_token_key ON users (sub_token)"
            )
        except Exception:
            pass

        try:
            await conn.execute("ALTER TABLE orders ALTER COLUMN server_id DROP NOT NULL")
        except Exception:
            pass

        try:
            await conn.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS renew_config_id INTEGER")
        except Exception:
            pass

        for stmt in (
            "ALTER TABLE configs ALTER COLUMN server_id DROP NOT NULL",
            "ALTER TABLE configs ALTER COLUMN config_link DROP NOT NULL",
            "ALTER TABLE configs ALTER COLUMN expire_date DROP NOT NULL",
            "ALTER TABLE configs ALTER COLUMN traffic_limit_gb DROP NOT NULL",
            "ALTER TABLE configs ALTER COLUMN order_id DROP NOT NULL",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS sub_id TEXT",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS plan_id INTEGER",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS reminder_traffic_sent BOOLEAN DEFAULT FALSE",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS reminder_time_sent BOOLEAN DEFAULT FALSE",
        ):
            try:
                await conn.execute(stmt)
            except Exception:
                pass

        # Service-type aware schema (v2ray | wireguard).
        # V2Ray servers use url/api_token/sub_port/inbound_ids; WireGuard
        # servers are MikroTik routers reached through the RouterOS API.
        for stmt in (
            "ALTER TABLE servers ADD COLUMN IF NOT EXISTS service_type TEXT DEFAULT 'v2ray'",
            "ALTER TABLE servers ADD COLUMN IF NOT EXISTS api_port INTEGER DEFAULT 8728",
            "ALTER TABLE servers ADD COLUMN IF NOT EXISTS wg_interface TEXT DEFAULT ''",
            "ALTER TABLE servers ADD COLUMN IF NOT EXISTS wg_server_public_key TEXT DEFAULT ''",
            "ALTER TABLE servers ADD COLUMN IF NOT EXISTS wg_endpoint TEXT DEFAULT ''",
            "ALTER TABLE servers ADD COLUMN IF NOT EXISTS wg_port INTEGER DEFAULT 51820",
            "ALTER TABLE servers ADD COLUMN IF NOT EXISTS wg_client_subnet TEXT DEFAULT ''",
            "ALTER TABLE servers ADD COLUMN IF NOT EXISTS wg_dns TEXT DEFAULT '1.1.1.1,8.8.8.8'",
            "ALTER TABLE servers ADD COLUMN IF NOT EXISTS wg_ip_range_start INTEGER DEFAULT 10",
            "ALTER TABLE servers ADD COLUMN IF NOT EXISTS wg_ip_range_end INTEGER DEFAULT 250",
            "ALTER TABLE plans ADD COLUMN IF NOT EXISTS service_type TEXT DEFAULT 'v2ray'",
            "ALTER TABLE orders ADD COLUMN IF NOT EXISTS service_type TEXT DEFAULT 'v2ray'",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS service_type TEXT DEFAULT 'v2ray'",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS wg_client_ip TEXT",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS wg_public_key TEXT",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS wg_private_key TEXT",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS wg_server_public_key TEXT",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS wg_endpoint TEXT",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS wg_port INTEGER",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS wg_peer_id TEXT",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS wg_last_rx BIGINT DEFAULT 0",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS wg_last_tx BIGINT DEFAULT 0",
            "ALTER TABLE configs ADD COLUMN IF NOT EXISTS used_bytes BIGINT DEFAULT 0",
            "UPDATE servers SET service_type='v2ray' WHERE service_type IS NULL",
            "UPDATE plans SET service_type='v2ray' WHERE service_type IS NULL",
            "UPDATE orders SET service_type='v2ray' WHERE service_type IS NULL",
            "UPDATE configs SET service_type='v2ray' WHERE service_type IS NULL",
        ):
            try:
                await conn.execute(stmt)
            except Exception:
                pass

        try:
            master = await conn.fetchrow(
                "SELECT id FROM servers WHERE is_active = TRUE ORDER BY id LIMIT 1"
            )
            if master:
                await conn.execute(
                    "UPDATE configs SET server_id = $1 WHERE server_id IS NULL",
                    master["id"]
                )
        except Exception:
            pass


async def get_pool() -> asyncpg.Pool:
    return pool
