"""Tests for bot.services.restore."""

import unittest

from bot.services import restore

ONE_GB = 1024 * 1024 * 1024


class ParseEmailTest(unittest.TestCase):
    def test_order_pattern_extracts_tg_id_and_order(self):
        parsed = restore.parse_email("123456789-order5")
        self.assertEqual(parsed.kind, "order")
        self.assertEqual(parsed.telegram_id, 123456789)
        self.assertEqual(parsed.order_id, 5)

    def test_numeric_email_is_linked_user(self):
        parsed = restore.parse_email("987654321")
        self.assertEqual(parsed.kind, "user")
        self.assertEqual(parsed.telegram_id, 987654321)
        self.assertIsNone(parsed.order_id)

    def test_custom_name_is_orphan(self):
        parsed = restore.parse_email("myuser")
        self.assertEqual(parsed.kind, "orphan")
        self.assertIsNone(parsed.telegram_id)
        self.assertIsNone(parsed.order_id)

    def test_malformed_order_pattern_is_orphan(self):
        parsed = restore.parse_email("123-orderabc")
        self.assertEqual(parsed.kind, "orphan")

    def test_missing_tg_prefix_is_orphan(self):
        parsed = restore.parse_email("order7")
        self.assertEqual(parsed.kind, "orphan")


class TrafficConversionTest(unittest.TestCase):
    def test_bytes_to_gb(self):
        self.assertEqual(restore.traffic_bytes_to_gb(50 * ONE_GB), 50)

    def test_zero_is_unlimited(self):
        self.assertEqual(restore.traffic_bytes_to_gb(0), 0)

    def test_partial_gb_rounds_up(self):
        self.assertEqual(restore.traffic_bytes_to_gb(int(1.5 * ONE_GB)), 1)


class MatchPlanTest(unittest.TestCase):
    def setUp(self):
        self.plans = [
            {"id": 1, "name": "Basic", "traffic_gb": 30, "duration_days": 30, "max_users": 1, "is_active": True},
            {"id": 2, "name": "Basic60", "traffic_gb": 30, "duration_days": 60, "max_users": 1, "is_active": True},
            {"id": 3, "name": "Pro", "traffic_gb": 100, "duration_days": 30, "max_users": 0, "is_active": True},
        ]

    def test_matches_exact_traffic_and_duration(self):
        plan = restore.match_plan(self.plans, 30, 30)
        self.assertEqual(plan["id"], 1)

    def test_matches_by_traffic_when_duration_unknown(self):
        plan = restore.match_plan(self.plans, 30, 0)
        self.assertIsNotNone(plan)
        self.assertEqual(plan["traffic_gb"], 30)

    def test_returns_none_when_no_traffic_match(self):
        self.assertIsNone(restore.match_plan(self.plans, 500, 30))

    def test_unlimited_traffic_matches_unlimited_plan(self):
        plans = [
            {"id": 9, "name": "Unlimited", "traffic_gb": 0, "duration_days": 30, "max_users": 0, "is_active": True},
        ]
        plan = restore.match_plan(plans, 0, 30)
        self.assertEqual(plan["id"], 9)

    def test_prefers_active_plan(self):
        plans = [
            {"id": 5, "name": "old", "traffic_gb": 50, "duration_days": 30, "max_users": 1, "is_active": False},
            {"id": 6, "name": "new", "traffic_gb": 50, "duration_days": 30, "max_users": 1, "is_active": True},
        ]
        plan = restore.match_plan(plans, 50, 30)
        self.assertEqual(plan["id"], 6)


class CollectClientsTest(unittest.TestCase):
    def test_extracts_clients_from_inbound_settings(self):
        inbounds = [
            {
                "id": 1,
                "settings": (
                    '{"clients": ['
                    '{"email": "123-order1", "id": "uuid-1", "subId": "sub-1", '
                    '"totalGB": 100, "expiryTime": 0, "enable": true, "limitIp": 2},'
                    '{"email": "456-order2", "id": "uuid-2", "subId": "sub-2", '
                    '"totalGB": 0, "expiryTime": 1710000000000, "enable": false, "limitIp": 0}'
                    "]}"
                ),
            }
        ]
        clients = restore.collect_clients(inbounds)
        self.assertEqual(len(clients), 2)
        self.assertEqual(clients[0]["email"], "123-order1")
        self.assertEqual(clients[0]["sub_id"], "sub-1")
        self.assertEqual(clients[1]["expiry_time"], 1710000000000)

    def test_handles_settings_as_dict(self):
        inbounds = [
            {
                "id": 1,
                "settings": {
                    "clients": [
                        {"email": "789-order3", "id": "uuid-3", "subId": "sub-3",
                         "totalGB": 10, "expiryTime": 0, "enable": True, "limitIp": 0}
                    ]
                },
            }
        ]
        clients = restore.collect_clients(inbounds)
        self.assertEqual(clients[0]["email"], "789-order3")

    def test_empty_inbounds(self):
        self.assertEqual(restore.collect_clients([]), [])


if __name__ == "__main__":
    unittest.main()
