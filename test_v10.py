# -*- coding: utf-8 -*-
"""Test đếm nguồn RSS trong tin nhắn admin (python test_v10.py)."""
import unittest

import admin_log

INFO = {"now": "07:43", "exit_code": 0, "selected": 0, "posted": 0, "hot": 0,
        "rejected": 0, "posted_titles": []}


def L(text, level="INFO"):
    return "2026-11-17 00:43:30 [%-7s] collector  %s" % (level, text)


class RssCountTest(unittest.TestCase):
    def count(self, lines):
        text = admin_log.build_summary(lines, INFO)
        part = text.split("nguồn RSS lỗi/0 bài ")[1].split(" ")[0]
        return int(part), text

    def test_stale_feed_is_not_an_error(self):
        n, _ = self.count([L("[RSS] Source OK: Krebs (0 bài hợp lệ) | thô=10, quá cũ/không rõ TG=10, "
                             "thiếu link=0, trùng nguồn khác=0, bozo=False")])
        self.assertEqual(n, 0)

    def test_empty_feed_is_an_error(self):
        n, _ = self.count([L("[RSS] Source OK: A (0 bài hợp lệ) | thô=0, quá cũ/không rõ TG=0, "
                             "thiếu link=0, trùng nguồn khác=0, bozo=True")])
        self.assertEqual(n, 1)

    def test_bozo_with_entries_zero_valid_is_an_error(self):
        n, _ = self.count([L("[RSS] Source OK: B (0 bài hợp lệ) | thô=5, quá cũ/không rõ TG=5, "
                             "thiếu link=0, trùng nguồn khác=0, bozo=True")])
        self.assertEqual(n, 1)

    def test_failed_and_no_entries_and_old_format(self):
        n, _ = self.count([
            L("[RSS] Source FAILED: C", "WARNING"),
            L("[RSS] Source NO ENTRIES: D (bozo=True)", "WARNING"),
            L("[RSS] Source OK: E (0 bài hợp lệ)"),          # log kiểu cũ
            L("[RSS] Source OK: F (7 bài hợp lệ) | thô=10, quá cũ/không rõ TG=3, "
              "thiếu link=0, trùng nguồn khác=0, bozo=False"),
        ])
        self.assertEqual(n, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
