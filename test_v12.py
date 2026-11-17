# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace as NS
import ai_editor


def run(src_title, content_title, cfg_raw):
    scored = NS(title=src_title, link="", article_text="x",
                article=NS(summary="", published=None))
    orig = ai_editor._call_chat
    ai_editor._call_chat = lambda *a, **k: cfg_raw
    try:
        import ai_prompts
        ai_prompts.build_writer_user_payload = lambda s, d: ""
        return ai_editor.ai_write(scored, NS(), None)
    finally:
        ai_editor._call_chat = orig


BODY = " ".join(["Binance mở lại 87 mạng lưới sau khi rút vốn."] * 8)


class WriterForbidden(unittest.TestCase):
    def test_phrase_from_source_is_allowed(self):
        raw = '{"title":"Binance vượt cú sốc rút vốn","content":"%s"}' % BODY
        self.assertIsNotNone(run("Binance vượt cú sốc 6,3 tỷ USD, mở lại 87 mạng lưới", "", raw))

    def test_invented_clickbait_still_blocked(self):
        raw = '{"title":"Binance gây sốc","content":"%s"}' % BODY
        self.assertIsNone(run("Binance mở lại 87 mạng lưới", "", raw))


if __name__ == "__main__":
    unittest.main(verbosity=1)
