"""Tests for the RouterOS SSH/CLI parsing layer in bot.services.wireguard."""

import unittest

from bot.services import wireguard as wg

INTERFACES_TERSE = (
    "0 R name=wg-user mtu=1280 listen-port=443 "
    "public-key=XEq4YQ9PPe6qnD1ETkFwHonfd3DSb0Eq9kg3NMLdi2E="
)

PEERS_TERSE = "\n".join([
    "0 comment=koooo-wg-2 interface=wg-user name=peer9 "
    "public-key=QKbSUGs7Ks+FWEMW860AedxV0ytvDTRmM0jgvu5EnXg= "
    "allowed-address=192.168.30.2/32 rx=35.6MiB tx=340.5MiB last-handshake=1h21m",
    "1 comment=user=81708658 wg=192.168.30.5 interface=wg-user name=peer12 "
    "public-key=U+ajFeR7/JYjD3y9dAQx7pmwFKnG2W70TThF1/Oz50U= "
    "allowed-address=192.168.30.5/32 persistent-keepalive=25s rx=69.1MiB tx=593.0MiB",
])

PK_A = "QKbSUGs7Ks+FWEMW860AedxV0ytvDTRmM0jgvu5EnXg="
PK_B = "U+ajFeR7/JYjD3y9dAQx7pmwFKnG2W70TThF1/Oz50U="


class TerseParserTest(unittest.TestCase):
    def test_interface_fields(self):
        rows = wg._parse_terse(INTERFACES_TERSE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "wg-user")
        self.assertEqual(rows[0]["listen-port"], "443")
        self.assertEqual(rows[0]["public-key"],
                         "XEq4YQ9PPe6qnD1ETkFwHonfd3DSb0Eq9kg3NMLdi2E=")

    def test_peer_fields_with_spaces_in_comment(self):
        rows = wg._parse_terse(PEERS_TERSE)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["public-key"], PK_A)
        self.assertEqual(rows[0]["allowed-address"], "192.168.30.2/32")
        self.assertEqual(rows[1]["public-key"], PK_B)
        self.assertEqual(rows[1]["allowed-address"], "192.168.30.5/32")

    def test_usage_counters_parsed(self):
        rows = wg._parse_terse(PEERS_TERSE)
        self.assertEqual(wg._to_int(rows[0]["rx"]), int(35.6 * 1024 * 1024))
        self.assertEqual(wg._to_int(rows[0]["tx"]), int(340.5 * 1024 * 1024))


class FindPeerTest(unittest.TestCase):
    def setUp(self):
        self.peers = wg._parse_terse(PEERS_TERSE)

    def test_find_by_public_key(self):
        peer = wg._find_peer(self.peers, public_key=PK_B)
        self.assertIsNotNone(peer)
        self.assertEqual(peer["public-key"], PK_B)

    def test_find_by_client_ip(self):
        peer = wg._find_peer(self.peers, client_ip="192.168.30.2")
        self.assertIsNotNone(peer)
        self.assertEqual(peer["public-key"], PK_A)

    def test_find_missing(self):
        self.assertIsNone(wg._find_peer(self.peers, public_key="nope"))

    def test_find_expr(self):
        self.assertEqual(wg._find_expr(public_key="abc"), '[find public-key="abc"]')
        self.assertEqual(
            wg._find_expr(client_ip="192.168.30.2/32"),
            '[find allowed-address="192.168.30.2/32"]',
        )


if __name__ == "__main__":
    unittest.main()
