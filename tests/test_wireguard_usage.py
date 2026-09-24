"""Tests for WireGuard usage accounting and traffic formatting helpers."""

import unittest

from bot.services import wireguard as wg
from bot.utils.helpers import format_gb

GB = 1024 ** 3


class MergeUsageTest(unittest.TestCase):
    def test_accumulates_delta_from_zero(self):
        used, rx, tx = wg.merge_usage(0, 0, 0, GB, 0)
        self.assertEqual(used, GB)
        self.assertEqual((rx, tx), (GB, 0))

    def test_delta_between_checks(self):
        used, rx, tx = wg.merge_usage(2 * GB, GB, GB, 3 * GB, 2 * GB)
        self.assertEqual(used, 5 * GB)
        self.assertEqual((rx, tx), (3 * GB, 2 * GB))

    def test_counter_reset_counts_as_fresh_usage(self):
        # rx reset from 5GB to 1GB (peer reset/reboot), tx unchanged.
        used, rx, tx = wg.merge_usage(5 * GB, 5 * GB, 0, GB, 0)
        self.assertEqual(used, 6 * GB)
        self.assertEqual(rx, GB)

    def test_only_one_direction_resets(self):
        used, _, _ = wg.merge_usage(0, 4 * GB, 0, GB, 2 * GB)
        self.assertEqual(used, 3 * GB)  # 1GB (reset rx) + 2GB (tx delta)

    def test_handles_none_values(self):
        used, rx, tx = wg.merge_usage(None, None, None, None, None)
        self.assertEqual((used, rx, tx), (0, 0, 0))

    def test_never_negative(self):
        used, _, _ = wg.merge_usage(0, 0, 0, 0, 0)
        self.assertEqual(used, 0)


class FormatGbTest(unittest.TestCase):
    def test_formats_gigabytes(self):
        self.assertEqual(format_gb(int(1.5 * GB)), "1.50 GB")

    def test_zero(self):
        self.assertEqual(format_gb(0), "0.00 GB")

    def test_none_and_garbage(self):
        self.assertEqual(format_gb(None), "0.00 GB")
        self.assertEqual(format_gb("n/a"), "0.00 GB")


if __name__ == "__main__":
    unittest.main()
