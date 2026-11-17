# -*- coding: utf-8 -*-
"""Test log chẩn đoán RSS (python test_v8.py). Không cần mạng."""
import logging
import unittest
from datetime import datetime, timedelta, timezone

import collector

NOW = datetime(2026, 11, 16, 8, 0, tzinfo=timezone.utc)


def entry(title, link, hours_ago):
    when = NOW - timedelta(hours=hours_ago)
    return {"title": title, "link": link, "published": when.strftime("%a, %d %b %Y %H:%M:%S GMT")}


class Fake(object):
    """Thay feedparser.parse: trả dict có 'entries' và 'bozo'."""
    def __init__(self, entries, bozo=False):
        self.entries, self.bozo = entries, bozo

    def parse(self, _raw):
        return {"entries": self.entries, "bozo": self.bozo}


class Capture(logging.Handler):
    def __init__(self):
        logging.Handler.__init__(self)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class DiagTest(unittest.TestCase):
    def run_one(self, entries, bozo=False):
        collector.feedparser = Fake(entries, bozo)
        articles = collector._parse_feed("SrcTest", b"x", NOW)
        return articles, collector._LAST_STATS["SrcTest"]

    def test_all_old_counted(self):
        arts, st = self.run_one([entry("Old %d" % i, "https://a/%d" % i, 200) for i in range(3)])
        self.assertEqual(arts, [])
        self.assertEqual((st["raw"], st["old"], st["nolink"]), (3, 3, 0))

    def test_mixed(self):
        arts, st = self.run_one([
            entry("Fresh story about malware", "https://a/1", 2),
            entry("Old", "https://a/2", 300),
            {"title": "No link", "link": "", "published": ""},
        ])
        self.assertEqual(len(arts), 1)
        self.assertEqual((st["raw"], st["old"], st["nolink"]), (3, 1, 1))

    def test_no_entries_sets_bozo(self):
        arts, st = self.run_one([], bozo=True)
        self.assertEqual(arts, [])
        self.assertTrue(st["bozo"])
        self.assertEqual(st["raw"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
