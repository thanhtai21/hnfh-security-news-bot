"""Test v5: nguồn Google News (tách tên báo gốc) và nhật ký [MISSED]."""
import logging
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import collector
import main
from textutils import display_source


class FakeEntry(dict):
    pass


class GNews(unittest.TestCase):
    def test_publisher_split_and_display(self):
        title, pub = collector.split_publisher(
            "GNews VN Tan cong mang",
            "Ngan hang Indonesia bi tan cong mang - Tuoi Tre", {})
        self.assertEqual(title, "Ngan hang Indonesia bi tan cong mang")
        self.assertEqual(pub, "Tuoi Tre")
        art = NS(publisher=pub)
        self.assertEqual(display_source(NS(source="GNews VN Tan cong mang", article=art)),
                         "Tuoi Tre")

    def test_source_field_preferred(self):
        title, pub = collector.split_publisher(
            "GNews EN Breach", "Bank hit by ransomware - Reuters",
            {"source": {"title": "Reuters"}})
        self.assertEqual((title, pub), ("Bank hit by ransomware", "Reuters"))

    def test_normal_feed_untouched(self):
        title, pub = collector.split_publisher(
            "BleepingComputer", "Hack - the sequel", {})
        self.assertEqual((title, pub), ("Hack - the sequel", ""))
        self.assertEqual(display_source(NS(source="BleepingComputer", article=NS(publisher=""))),
                         "BleepingComputer")

    def test_long_tail_not_treated_as_publisher(self):
        t = "Hack - " + "x" * 80
        self.assertEqual(collector.split_publisher("GNews EN Breach", t, {}), (t, ""))


class CrossLang(unittest.TestCase):
    def test_meta_fine_vn_vs_en(self):
        import hot
        vn = hot.story_keys("Meta bị phạt 805 triệu euro vì truy cập trái phép dữ liệu người dùng Android", "")
        en = hot.story_keys("Italy fines Meta €805m over unauthorised access to Android users' data", "")
        self.assertTrue(hot.keys_same_story(vn, en))

    def test_same_brand_different_story(self):
        import hot
        a = hot.story_keys("Meta launches new AI glasses", "")
        b = hot.story_keys("Meta bị phạt 805 triệu euro", "")
        self.assertFalse(hot.keys_same_story(a, b))


class Missed(unittest.TestCase):
    def _s(self, title, score, hard=False):
        return NS(title=title, score=score, hard_signal=hard, source="X",
                  article=NS(age_hours=3.0))

    def test_logs_unselected_high_score_with_reason(self):
        a = self._s("Outlook zero-day exploited", 70, True)
        b = self._s("Posted already", 90, True)
        c = self._s("Low", 10)
        with self.assertLogs(main.logger, level="INFO") as cm:
            main.log_missed_news([a, b, c], [a], [b], [])
        text = "\n".join(cm.output)
        self.assertIn("Outlook zero-day exploited", text)
        self.assertIn("không đạt cổng", text)
        self.assertNotIn("Posted already", text)
        self.assertNotIn("Low", text)

    def test_never_raises(self):
        main.log_missed_news(None, None, None, None)
        main.log_missed_news([object()], [], [], [])


if __name__ == "__main__":
    unittest.main(verbosity=1)


class Gate(unittest.TestCase):
    def test_drop_unwritten_when_ai_configured(self):
        w = NS(title="written", ai_content="Nội dung AI")
        u = NS(title="unwritten", ai_content="")
        main._ai_configured = lambda: True
        main.ALLOW_FALLBACK_NORMAL_POSTS = False
        self.assertEqual([x.title for x in main.drop_unwritten([w, u])], ["written"])
        main.ALLOW_FALLBACK_NORMAL_POSTS = True
        self.assertEqual(len(main.drop_unwritten([w, u])), 2)
        main.ALLOW_FALLBACK_NORMAL_POSTS = False
        main._ai_configured = lambda: False   # AI chưa cấu hình: giữ hành vi cũ
        self.assertEqual(len(main.drop_unwritten([w, u])), 2)


class Embed(unittest.TestCase):
    def test_google_news_link_wrapped(self):
        import discord_poster as dp
        link = "https://news.google.com/rss/articles/CBM123?oc=5"
        sc = NS(link=link, ai_content="Tin\n🔗 Nguồn: X\n" + link,
                article=NS(summary="", title="t"))
        out = dp.build_post_content(sc)
        self.assertIn("<%s>" % link, out)
        self.assertNotIn("<<", out)

    def test_normal_link_untouched(self):
        import discord_poster as dp
        link = "https://example.com/a"
        sc = NS(link=link, ai_content="Tin\n🔗 Nguồn: X\n" + link,
                article=NS(summary="", title="t"))
        self.assertNotIn("<" + link, dp.build_post_content(sc))
