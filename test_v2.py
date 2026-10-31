# -*- coding: utf-8 -*-
"""Test cho bản vá v2: event guard theo summary, B5 siết chặt, AI chia lô."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import ai_editor
import analyzer
import hot
from collector import Article

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
POSTED = "$6 Billion Binance Hack Bears Hallmarks of North Korea’s TraderTraitor"


def sc(title, summary=""):
    return SimpleNamespace(title=title, article=SimpleNamespace(summary=summary))


def state_with(*titles):
    return SimpleNamespace(posts=[
        {"title": t, "posted_at": (NOW - timedelta(hours=3)).isoformat()}
        for t in titles])


class EventGuardV2(unittest.TestCase):
    def test_summary_only_mention_is_blocked(self):
        a = sc("Worried About Crypto Hacks? Buy Bitcoin Proxies in Stock Market Instead",
               "Analysts say the record $6.4 billion theft is a Binance security failure, "
               "not a crypto-wide threat.")
        self.assertTrue(hot.is_recent_story_duplicate(a, state_with(POSTED), NOW))

    def test_binance_titles_blocked(self):
        for t in [
            "Binance hackers raced to move billions before it could be frozen",
            "Here’s how an Ethereum upgrade helped drain part of Binance’s $6 billion loss",
            "Binance hacker splits $1.4 billion of bitcoin into 19 wallets that no one can freeze",
            "Binance bị tấn công mạng: Phong tỏa hơn 2,5 tỉ USD, thị trường tiền mã hóa chao đảo",
        ]:
            self.assertTrue(hot.is_recent_story_duplicate(sc(t), state_with(POSTED), NOW), t)

    def test_unrelated_not_blocked(self):
        for t, s in [
            ("Capcom confirms data breach affecting 11.7 million accounts", ""),
            ("Atlassian Patches 75 Flaws in Data Center and Server Products", ""),
            ("Microsoft patches zero-day exploited in attacks",
             "Microsoft said attackers abused the flaw. Google researchers helped."),
        ]:
            self.assertFalse(hot.is_recent_story_duplicate(sc(t, s), state_with(POSTED), NOW), t)

    def test_same_run_cluster_uses_summary(self):
        first = sc(POSTED)
        cand = sc("Buy Bitcoin Proxies Instead", "The Binance hack drained $6 billion in theft.")
        self.assertTrue(hot.is_loose_duplicate_of_any(cand, [first]))


class B5Tighter(unittest.TestCase):
    def _hot(self, title, sources, hard=True):
        s = SimpleNamespace(title=title, hard_signal=hard, corroboration=sources)
        with mock.patch.object(hot, "is_hot_rule", return_value=True):
            return hot.is_hot(s, None, NOW, ai_used=False)

    def test_market_commentary_rejected(self):
        self.assertFalse(self._hot("Worried About Crypto Hacks? Buy Bitcoin Proxies in Stock Market Instead", 4))
        self.assertFalse(self._hot("Coinbase leads crypto stock selloff following $6.3B Binance hack", 4))

    def test_real_incident_with_enough_sources_ok(self):
        self.assertTrue(self._hot("Binance links $6.3 billion crypto theft to compromised DevOps laptop", 4))

    def test_two_sources_not_enough(self):
        self.assertFalse(self._hot("Binance links $6.3 billion crypto theft to compromised DevOps laptop", 2))


class AIBatching(unittest.TestCase):
    cfg = SimpleNamespace(model="main", fallback_model="", provider="groq")

    def _cands(self, n):
        return [object() for _ in range(n)]

    def _decisions(self, size):
        return json.dumps({"decisions": [
            {"id": i, "publish": False, "score": 10, "reason": "x", "severity": "low", "hot": False}
            for i in range(size)]})

    def test_batches_and_offsets(self):
        sizes = []
        def fake(cfg, sp, up, **kw):
            sizes.append(up)
            return self._decisions(int(up))
        with mock.patch("ai_prompts.build_filter_user_payload", side_effect=lambda c: str(len(c))), \
             mock.patch.object(ai_editor, "_call_chat", side_effect=fake), \
             mock.patch.object(ai_editor.time, "sleep"):
            res = ai_editor.ai_filter(self._cands(6), self.cfg)
        self.assertEqual(sizes, ["4", "2"])
        self.assertEqual(sorted(res), [0, 1, 2, 3, 4, 5])
        self.assertTrue(all(d.from_ai for d in res.values()))
        self.assertEqual([res[i].id for i in range(6)], list(range(6)))

    def test_partial_failure_gives_placeholders(self):
        calls = {"n": 0}
        def fake(cfg, sp, up, **kw):
            calls["n"] += 1
            return self._decisions(int(up)) if calls["n"] == 1 else None
        with mock.patch("ai_prompts.build_filter_user_payload", side_effect=lambda c: str(len(c))), \
             mock.patch.object(ai_editor, "_call_chat", side_effect=fake), \
             mock.patch.object(ai_editor.time, "sleep"):
            res = ai_editor.ai_filter(self._cands(6), self.cfg)
        self.assertTrue(res[0].from_ai)
        self.assertFalse(res[5].from_ai)
        self.assertFalse(res[5].publish)

    def test_all_fail_returns_none(self):
        with mock.patch("ai_prompts.build_filter_user_payload", side_effect=lambda c: str(len(c))), \
             mock.patch.object(ai_editor, "_call_chat", return_value=None), \
             mock.patch.object(ai_editor.time, "sleep"):
            self.assertIsNone(ai_editor.ai_filter(self._cands(5), self.cfg))

    def test_fallback_model_used_once(self):
        cfg = SimpleNamespace(model="main", fallback_model="backup", provider="groq")
        models = []
        def fake(cfg_, sp, up, **kw):
            models.append(kw.get("model"))
            return None if kw.get("model") is None else self._decisions(int(up))
        with mock.patch("ai_prompts.build_filter_user_payload", side_effect=lambda c: str(len(c))), \
             mock.patch.object(ai_editor, "_call_chat", side_effect=fake), \
             mock.patch.object(ai_editor.time, "sleep"):
            res = ai_editor.ai_filter(self._cands(3), cfg)
        self.assertEqual(models, [None, "backup"])
        self.assertEqual(len(res), 3)


class ScoringV2(unittest.TestCase):
    def _score(self, title, src="The Record"):
        a = Article(source=src, title=title, link="http://x/" + title[:6],
                    summary="", published=None, age_hours=5.0)
        return analyzer.score_article(a)

    def test_hacker_with_context_not_penalized(self):
        for t in ["Chinese Hackers Use AI-Powered ARTEX to Target Over 200 Organizations Worldwide",
                  "Hackers Publish Work Details of 41,000 Homeland Security Employees"]:
            s = self._score(t)
            self.assertFalse(any("hacker" in p for p in s.penalties), s.penalties)
            self.assertGreaterEqual(s.score, 40)

    def test_bare_hacker_still_penalized(self):
        s = self._score("Hacker Conversations: Corey Thuen and the Evil Bit")
        self.assertEqual(s.score, 0)

    def test_fashion_retailer_breach_not_zeroed(self):
        s = self._score("Fashion retailer Resolve says hackers stole payment card information")
        self.assertGreaterEqual(s.score, 40)

    def test_pure_fashion_news_still_penalized(self):
        s = self._score("Fashion week lifestyle trends for autumn")
        self.assertEqual(s.topic, "OTHER")

    def test_prison_is_cybercrime_topic(self):
        a = Article(source="X", title="Syrian hacker who served Assad regime gets 42 months in US prison",
                    link="l", summary="", published=None, age_hours=5.0)
        self.assertNotEqual(analyzer.classify_topic(a)[0], "OTHER")


if __name__ == "__main__":
    unittest.main(verbosity=2)
