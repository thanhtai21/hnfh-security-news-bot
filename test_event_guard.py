# -*- coding: utf-8 -*-
"""Test EVENT GUARD (chạy: python test_event_guard.py). Không cần mạng/AI."""
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

import hot

VN = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 31, 12, 22, tzinfo=VN)

POSTED_BINANCE = "$6 Billion Binance Hack Bears Hallmarks of North Korea’s TraderTraitor"
# Tiêu đề lấy từ log dry run 2026-10-31 (cùng vụ Binance).
BINANCE = [
    "Inside the largest crypto hack on record: How attackers breached Binance for $6.3 billion",
    "Binance Hack: Why Bitcoin Didn't Crash Like It Did in 2018",
    "Who could be behind the Binance hack and what we know so far",
    "Binance links $6.3 billion crypto theft to compromised DevOps laptop",
    "Crypto giant Binance hit by $6.3bn hack suspected to be largest in history",
    "President Trump says they’re looking into Binance hack as calls grow for crypto regulation",
    "Morning Minute: Binance’s $6.4B Hack Overshadows Hyperliquid’s Biggest Day",
]
# Chuyện KHÁC - không được bị chặn.
OTHER = [
    "Critical Linux kernel flaw allows remote root attacks on MPTCP servers",
    "Florida warns that 4.9 million people had Social Security numbers leaked in financial breach",
    "Arista patches new VeloCloud Orchestrator zero-day exploited in attacks",
    "Microsoft blames ConnectWise attacks on Chinese state hacker using ARTEX AI agent",
    "Drupal warns of critical Commerce Core flaw exploited in attacks",
    "Japan Airlines suffers third-party data breach exposing passenger info",
]


def art(title):
    return types.SimpleNamespace(title=title)


class FakeState(object):
    def __init__(self, posts):
        self.posts = posts


def post(title, hours_ago):
    return {"title": title,
            "posted_at": (NOW - timedelta(hours=hours_ago)).isoformat()}


class EventGuardTest(unittest.TestCase):
    def setUp(self):
        hot.EVENT_GUARD_ENABLED = True
        hot.EVENT_GUARD_HOURS = 24

    def test_binance_variants_blocked_after_post(self):
        state = FakeState([post(POSTED_BINANCE, 1.2)])
        for title in BINANCE:
            self.assertTrue(hot.is_recent_story_duplicate(art(title), state, NOW), title)

    def test_other_stories_not_blocked(self):
        state = FakeState([post(POSTED_BINANCE, 1.2)])
        for title in OTHER:
            self.assertFalse(hot.is_recent_story_duplicate(art(title), state, NOW), title)

    def test_old_post_does_not_block(self):
        state = FakeState([post(POSTED_BINANCE, 40)])
        self.assertFalse(hot.is_recent_story_duplicate(art(BINANCE[0]), state, NOW))

    def test_unreadable_time_is_fail_closed(self):
        state = FakeState([{"title": POSTED_BINANCE, "posted_at": "???"}])
        self.assertTrue(hot.is_recent_story_duplicate(art(BINANCE[0]), state, NOW))

    def test_state_error_is_fail_closed(self):
        class Bad(object):
            @property
            def posts(self):
                raise RuntimeError("boom")
        self.assertTrue(hot.is_recent_story_duplicate(art(BINANCE[0]), Bad(), NOW))

    def test_cluster_keeps_only_first(self):
        kept = []
        for title in BINANCE + OTHER:
            s = art(title)
            if not hot.is_loose_duplicate_of_any(s, kept):
                kept.append(s)
        titles = [k.title for k in kept]
        self.assertEqual(sum(1 for t in titles if "Binance" in t), 1)
        self.assertEqual(titles[0], BINANCE[0])
        for t in OTHER:
            self.assertIn(t, titles)

    def test_disabled_changes_nothing(self):
        hot.EVENT_GUARD_ENABLED = False
        state = FakeState([post(POSTED_BINANCE, 1)])
        self.assertFalse(hot.is_recent_story_duplicate(art(BINANCE[0]), state, NOW))
        self.assertFalse(hot.is_loose_duplicate_of_any(art(BINANCE[1]), [art(BINANCE[0])]))

    def test_no_state_is_not_blocked(self):
        self.assertFalse(hot.is_recent_story_duplicate(art(BINANCE[0]), None, NOW))


if __name__ == "__main__":
    unittest.main(verbosity=2)
