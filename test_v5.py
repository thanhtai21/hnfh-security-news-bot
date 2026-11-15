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
