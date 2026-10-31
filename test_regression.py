# -*- coding: utf-8 -*-
"""Regression test: ca Binance + tin thuong ngay + cong nguon + HARD_MAX.
Chay:  python -m unittest discover tests -v   (tu thu muc goc repo)"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer import ScoredArticle, detect_hard_signals, score_article  # noqa: E402
from collector import Article  # noqa: E402
from config import HARD_MAX_CANDIDATES  # noqa: E402
from hot import annotate_corroboration, hot_rule_explain  # noqa: E402
from textutils import now_vn  # noqa: E402

NOW = now_vn()


def art(title, summary="", source="The Record", age=2.0, link=None):
    return Article(source=source, title=title,
                   link=link or "https://example.test/" + str(abs(hash(title))),
                   summary=summary, published=NOW, age_hours=age)


class MustBeHard(unittest.TestCase):
    CASES = [
        ("Binance confirms security incident, freezes withdrawals",
         "Unauthorized transactions were detected."),
        ("Coinbase confirms customer data stolen in insider bribery scheme", ""),
        ("Hackers breached the US Treasury, officials confirm", ""),
        ("AWS outage takes down thousands of sites worldwide", ""),
        ("$6.3 billion Binance hack is largest crypto theft on record", ""),
        ("Telegram bot leaks personal data of 50 million Vietnamese citizens", ""),
    ]

    def test_hard(self):
        for title, summary in self.CASES:
            with self.subTest(title=title):
                hard, reasons = detect_hard_signals(art(title, summary))
                self.assertTrue(hard, "phai la hard_signal: %s" % title)
                s = score_article(art(title, summary), NOW)
                self.assertGreater(s.score, 0)
                self.assertTrue(s.is_special)


class MustNotBeHard(unittest.TestCase):
    CASES = [
        "Microsoft patches exploited zero-day in Windows",
        "CISA adds exploited Cisco flaw to KEV catalog",
        "FBI arrests hacker behind botnet",
        "Google fixes Chrome zero-day exploited in the wild",
        "Cisco warns of attacks on ASA firewalls",
    ]

    def test_not_hard(self):
        for title in self.CASES:
            with self.subTest(title=title):
                hard, reasons = detect_hard_signals(art(title))
                self.assertFalse(hard, "KHONG duoc la hard: %s | %s" % (title, reasons))


class SourceGates(unittest.TestCase):
    def test_crypto_price_news_dropped(self):
        for t in ("Bitcoin price rises 5% as ETF inflows grow",
                  "Binance launches new staking product",
                  "Coinbase stock rises after earnings"):
            with self.subTest(t=t):
                s = score_article(art(t, source="CoinDesk"), NOW)
                self.assertEqual(s.score, 0)
                self.assertFalse(s.hard_signal)

    def test_vn_gate(self):
        s = score_article(art("Ra mắt điện thoại mới giá rẻ", source="VnExpress Cong nghe"), NOW)
        self.assertEqual(s.score, 0)
        s = score_article(art("Lộ lọt dữ liệu 50 triệu người dùng Việt Nam",
                              source="Tuoi Tre Cong nghe"), NOW)
        self.assertTrue(s.is_vietnam)
        self.assertTrue(s.hard_signal)

    def test_vn_mining_not_security(self):
        # "khai thac dau khi" khong duoc lot cong nguon VN
        s = score_article(art("Khai thác dầu khí tăng sản lượng quý 3",
                              source="Vietnamnet Cong nghe"), NOW)
        self.assertEqual(s.score, 0)


class HotRules(unittest.TestCase):
    def test_binance_three_sources_hot(self):
        titles = [
            ("Binance loses $6.3 billion in biggest crypto hack on record", "The Block"),
            ("Biggest crypto hack on record: Binance loses $6.3 billion", "CoinDesk"),
            ("Binance loses $6.3 billion in the biggest crypto hack on record", "BleepingComputer"),
        ]
        items = [score_article(art(t, "Hackers stole funds from Binance.", source=src), NOW)
                 for t, src in titles]
        annotate_corroboration(items)
        self.assertTrue(all(i.hard_signal for i in items))
        ok, fails = hot_rule_explain(items[2])
        self.assertTrue(ok, fails)

    def test_old_article_not_hot(self):
        s = score_article(art("$6.3 billion Binance hack is largest crypto theft on record",
                              age=20.0), NOW)
        s.corroboration = 3
        ok, _ = hot_rule_explain(s)
        self.assertFalse(ok)


class HardMax(unittest.TestCase):
    def test_hard_cut_without_ai_rejection(self):
        from main import build_rule_candidates
        from state import NewsState
        topics = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot",
                  "Golf", "Hotel", "India", "Juliet", "Kilo", "Lima"]
        words = ["wallet drained", "database exposed", "bank breached",
                 "hospital offline", "router botnet", "cloud outage",
                 "token stolen", "ledger siphoned", "portal hacked",
                 "telecom intrusion", "exchange frozen", "registry leaked"]
        scored = []
        for i, (tp, w) in enumerate(zip(topics, words)):
            a = art("%s Corp %s worldwide %d" % (tp, w, i), link="https://example.test/h%d" % i)
            scored.append(ScoredArticle(article=a, score=60 + i, is_special=True,
                                        hard_signal=True, fingerprint="fp-hard-%d" % i))
        state = NewsState()
        cands = build_rule_candidates(scored, state, NOW, hot_only=False)
        self.assertLessEqual(sum(1 for c in cands if c.hard_signal), HARD_MAX_CANDIDATES)
        self.assertEqual(len(state.ai_rejected), 0)


if __name__ == "__main__":
    unittest.main()
