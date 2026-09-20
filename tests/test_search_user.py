"""Integration tests for db.search_user against a scratch PostgreSQL database.

Creates a temporary database, seeds users + configs, then asserts the
admin search finds users by partial / case-insensitive matches across
telegram_id, username, first/last name and config client_email.
"""

import asyncio
import os
import unittest
import uuid

import asyncpg

ADMIN_DSN = os.getenv("PG_ADMIN_DSN", "postgresql://postgres:123@localhost:5432/postgres")
DB_PASSWORD = "123"
HOST = "localhost"
PORT = 5432


class SearchUserDbTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dbname = f"newbot_search_{uuid.uuid4().hex[:8]}"
        cls.url = f"postgresql://postgres:{DB_PASSWORD}@{HOST}:{PORT}/{cls.dbname}"
        try:
            asyncio.run(cls._create_and_seed())
        except Exception as e:
            raise unittest.SkipTest(f"PostgreSQL not available for search tests: {e}")

    @classmethod
    def _admin_connect(cls):
        return asyncpg.connect(ADMIN_DSN)

    @classmethod
    async def _create_and_seed(cls):
        conn = await cls._admin_connect()
        try:
            await conn.execute(f'CREATE DATABASE "{cls.dbname}"')
        finally:
            await conn.close()

        import bot.database.models as models
        models.DATABASE_URL = cls.url
        await models.init_db()

        async with models.pool.acquire() as c:
            await c.execute(
                "INSERT INTO users (telegram_id, username, first_name, last_name) VALUES "
                "($1,$2,$3,$4),($5,$6,$7,$8),($9,$10,$11,$12),($13,$14,$15,$16)",
                7538342633, "MehrdadGhourchian", "Mehrdad", None,
                6245412936, "migmigadmins", "migmig", None,
                9123456789, "sara_vpn", "Sara", "Ahmadi",
                5000111222, "beta_one", "Ali", None,
            )
            rows = await c.fetch("SELECT id, telegram_id FROM users")
            id_by_tg = {r["telegram_id"]: r["id"] for r in rows}

            await c.execute(
                "INSERT INTO configs (user_id, client_email, service_type) VALUES "
                "($1,$2,'v2ray'),($3,$4,'v2ray')",
                id_by_tg[9123456789], "sara.account@vpn.example.com",
                id_by_tg[5000111222], "7538342633-renews@example.com",
            )
            await c.execute(
                "INSERT INTO configs (user_id, client_email, service_type, wg_client_ip) "
                "VALUES ($1,$2,'wireguard',$3)",
                id_by_tg[9123456789], "9123456789-order99", "10.66.66.7",
            )

        await models.pool.close()

    @classmethod
    def tearDownClass(cls):
        async def _drop():
            conn = await cls._admin_connect()
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{cls.dbname}" WITH (FORCE)')
            finally:
                await conn.close()
        try:
            asyncio.run(_drop())
        except Exception:
            pass

    def _search(self, query):
        async def run():
            import bot.database.models as models
            import bot.database.db as db
            models.pool = await asyncpg.create_pool(self.url, min_size=1)
            try:
                rows = await db.search_user(query)
                return [r["telegram_id"] for r in rows]
            finally:
                await models.pool.close()
        return run()

    def test_partial_numeric_id_returns_user(self):
        self.assertIn(7538342633, asyncio.run(self._search("7538")))

    def test_numeric_query_also_matches_email_substring(self):
        self.assertIn(5000111222, asyncio.run(self._search("7538")))

    def test_username_partial_case_insensitive(self):
        self.assertIn(6245412936, asyncio.run(self._search("MIGMIG")))
        self.assertIn(6245412936, asyncio.run(self._search("migmigadm")))

    def test_first_name_partial(self):
        self.assertIn(7538342633, asyncio.run(self._search("Mehrdad")))
        self.assertIn(9123456789, asyncio.run(self._search("sara")))

    def test_client_email_substring(self):
        self.assertIn(9123456789, asyncio.run(self._search("account@vpn")))
        self.assertIn(9123456789, asyncio.run(self._search("sara.account")))

    def test_client_email_case_insensitive(self):
        self.assertIn(9123456789, asyncio.run(self._search("ACCOUNT@VPN.EXAMPLE")))
        self.assertIn(5000111222, asyncio.run(self._search("Renews@Example")))

    def test_wireguard_client_ip_matches_user(self):
        self.assertIn(9123456789, asyncio.run(self._search("10.66.66.7")))
        self.assertIn(9123456789, asyncio.run(self._search("10.66.66")))

    def test_wireguard_client_email_matches_user(self):
        self.assertIn(9123456789, asyncio.run(self._search("order99")))

    def test_unmatched_query_returns_nothing(self):
        self.assertEqual(asyncio.run(self._search("zzzznomatch")), [])


if __name__ == "__main__":
    unittest.main()
