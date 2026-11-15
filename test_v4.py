"""Test v4: khóa sự kiện (cùng chuyện khác tiêu đề/ngôn ngữ) và chặn bài rule-based khi AI lỗi."""
import unittest
from types import SimpleNamespace as NS
from datetime import datetime, timedelta, timezone

import hot
import main


class StoryKeys(unittest.TestCase):
    def test_endgame_vs_gootloader_same_story(self):
        a = hot.story_keys(
            "Operation Endgame disrupts malware network, 6 million accounts recovered",
            "Law enforcement took down Gootloader and CastleRAT infrastructure.")
        b = hot.story_keys(
            "Law enforcement dismantles Gootloader, CastleRAT, StealC infrastructure",
            "The action is part of Operation Endgame led by Europol.")
        self.assertTrue(hot.keys_same_story(a, b), (a, b))

    def test_tornado_cross_language_with_summary(self):
        vn = hot.story_keys(
            "Cảnh sát bắt giữ 354 nghi phạm trong chiến dịch phá hoại thị trường đen trên Dark Web",
            "Chiến dịch Operation TorNado do Europol điều phối.")
        en = hot.story_keys(
            "354 dark web vendors and buyers arrested in Operation TorNado", "")
        self.assertTrue(hot.keys_same_story(vn, en), (vn, en))

    def test_unrelated_not_merged(self):
        a = hot.story_keys("Microsoft fixes exploited Outlook zero-day", "Patch Tuesday fixes 1,094 flaws.")
        b = hot.story_keys("Binance users withdraw $3.6bn after hack", "Lazarus Group blamed.")
        self.assertFalse(hot.keys_same_story(a, b))

    def test_only_number_is_not_enough(self):
        a = ["#354"]
        b = ["#354", "#900"]
        self.assertFalse(hot.keys_same_story(a, b))

    def test_years_are_not_keys(self):
        self.assertNotIn("#2026", hot.story_keys("Report 2026 shows 2025 trends", ""))


class RecentDuplicate(unittest.TestCase):
    NOW = datetime(2026, 11, 16, 1, 0, tzinfo=timezone(timedelta(hours=7)))

    def _state(self, title, keys, hours_ago=3):
        return NS(posts=[{
            "title": title, "keys": keys,
            "posted_at": (self.NOW - timedelta(hours=hours_ago)).isoformat()}])

    def test_duplicate_by_keys(self):
        posted_keys = hot.story_keys(
            "Operation Endgame disrupts malware network",
            "Gootloader and CastleRAT infrastructure seized.")
        st = self._state("Operation Endgame disrupts malware network", posted_keys)
        cand = NS(title="Police dismantle Gootloader, CastleRAT, StealC",
                  article=NS(summary="Part of Operation Endgame."))
        self.assertTrue(hot.is_recent_story_duplicate(cand, st, self.NOW))

    def test_old_post_ignored(self):
        keys = hot.story_keys("Operation Endgame disrupts", "Gootloader CastleRAT")
        st = self._state("Operation Endgame disrupts", keys, hours_ago=100)
        cand = NS(title="Police dismantle Gootloader, CastleRAT",
                  article=NS(summary="Operation Endgame."))
        self.assertFalse(hot.is_recent_story_duplicate(cand, st, self.NOW))

    def test_legacy_post_without_keys_still_works(self):
        st = self._state("Binance hack drains billions", None)
        cand = NS(title="Totally different Outlook flaw exploited",
                  article=NS(summary=""))
        self.assertFalse(hot.is_recent_story_duplicate(cand, st, self.NOW))


class FallbackGate(unittest.TestCase):
    def test_normal_fallback_blocked_by_default(self):
        items = [NS(slot="none", title="a"), NS(slot="none", title="b")]
        orig = main.allocate_slots
        main.allocate_slots = lambda pairs, **kw: [
            NS(slot="normal", title="a"), NS(slot="hot", title="b")]
        try:
            main.ALLOW_FALLBACK_NORMAL_POSTS = False
            out = main.ensure_slots(items, 1, 1, None)
            self.assertEqual([s.slot for s in out], ["hot"])
            main.ALLOW_FALLBACK_NORMAL_POSTS = True
            out = main.ensure_slots(items, 1, 1, None)
            self.assertEqual(len(out), 2)
        finally:
            main.allocate_slots = orig
            main.ALLOW_FALLBACK_NORMAL_POSTS = False


if __name__ == "__main__":
    unittest.main(verbosity=1)
