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


async def get_pool() -> asyncpg.Pool:
    return pool
