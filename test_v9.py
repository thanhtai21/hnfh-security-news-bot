# -*- coding: utf-8 -*-
"""Test nút link Google News (python test_v9.py). Không cần mạng/Discord thật."""
import asyncio
import types
import unittest

import discord
import discord_poster as dp

GN = "https://news.google.com/rss/articles/CBMi" + "A" * 300 + "?oc=5"
DIRECT = "https://www.bleepingcomputer.com/news/security/x/"


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FakeChannel(object):
    def __init__(self, fail_with_view=None):
        self.calls = []
        self.fail_with_view = fail_with_view

    async def create_thread(self, **kw):
        self.calls.append(kw)
        if "view" in kw and self.fail_with_view is not None:
            raise self.fail_with_view
        return types.SimpleNamespace(thread=types.SimpleNamespace(id=1))


def payload(content, link):
    async def _f():
        return dp._link_button_payload(content, link)
    return run(_f())


class _Forbidden(discord.Forbidden):
    def __init__(self):               # discord.Forbidden thật cần (response, message)
        Exception.__init__(self, "forbidden")


class PayloadTest(unittest.TestCase):
    def test_markdown_footer_removed_and_button_built(self):
        content = "Tóm tắt.\n\n🔗 [Đọc bài gốc](<%s>)" % GN
        new, view = payload(content, GN)
        self.assertNotIn(GN, new)
        self.assertIn("🔗", new)
        self.assertIsNotNone(view)

    def test_source_line_kept_url_line_dropped(self):
        content = "Tóm tắt.\n\n🔗 Nguồn: Lag.vn\n<%s>" % GN
        new, view = payload(content, GN)
        self.assertEqual(new, "Tóm tắt.\n\n🔗 Nguồn: Lag.vn")
        self.assertIsNotNone(view)

    def test_no_link_in_content_means_no_button(self):
        new, view = payload("Chỉ có chữ", GN)
        self.assertEqual(new, "Chỉ có chữ")
        self.assertIsNone(view)

    def test_aggregator_link_rules(self):
        self.assertEqual(dp._aggregator_link(types.SimpleNamespace(link=GN)), GN)
        self.assertIsNone(dp._aggregator_link(types.SimpleNamespace(link=DIRECT)))
        too_long = "https://news.google.com/rss/articles/" + "B" * 600
        self.assertIsNone(dp._aggregator_link(types.SimpleNamespace(link=too_long)))


class ThreadTest(unittest.TestCase):
    def test_uses_button_when_possible(self):
        ch = FakeChannel()
        content = "Tóm tắt.\n\n🔗 Nguồn: X\n<%s>" % GN
        run(dp._create_thread_with_link(ch, "T", content, [], [], GN))
        self.assertEqual(len(ch.calls), 1)
        self.assertIn("view", ch.calls[0])
        self.assertNotIn(GN, ch.calls[0]["content"])

    def test_falls_back_when_button_rejected(self):
        ch = FakeChannel(fail_with_view=RuntimeError("components not allowed"))
        content = "Tóm tắt.\n\n🔗 Nguồn: X\n<%s>" % GN
        run(dp._create_thread_with_link(ch, "T", content, [], [], GN))
        self.assertEqual(len(ch.calls), 2)
        self.assertNotIn("view", ch.calls[1])
        self.assertIn(GN, ch.calls[1]["content"])      # link vẫn có trong bài

    def test_forbidden_is_not_retried(self):
        ch = FakeChannel(fail_with_view=_Forbidden())
        content = "Tóm tắt.\n\n🔗 Nguồn: X\n<%s>" % GN
        with self.assertRaises(_Forbidden):
            run(dp._create_thread_with_link(ch, "T", content, [], [], GN))
        self.assertEqual(len(ch.calls), 1)

    def test_direct_link_untouched(self):
        ch = FakeChannel()
        content = "Tóm tắt.\n\n🔗 Nguồn: X\n%s" % DIRECT
        run(dp._create_thread_with_link(ch, "T", content, [], [], None))
        self.assertEqual(len(ch.calls), 1)
        self.assertNotIn("view", ch.calls[0])
        self.assertEqual(ch.calls[0]["content"], content)


class FooterTest(unittest.TestCase):
    def test_missing_link_gets_source_name(self):
        art = types.SimpleNamespace(publisher="Lag.vn", source="GNews VN", summary="", title="t",
                                    published=None, age_hours=1.0)
        sc = types.SimpleNamespace(article=art, link=GN, ai_content="Tóm tắt bài.",
                                   title="t", is_hot=False, tags=[])
        out = dp.build_post_content(sc)
        self.assertIn("🔗 Nguồn: Lag.vn", out)
        self.assertIn(GN, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
