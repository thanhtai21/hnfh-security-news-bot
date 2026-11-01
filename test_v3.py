# -*- coding: utf-8 -*-
"""Test cho bài đăng dài hơn (tối đa 2000 ký tự) + lấy nội dung bài gốc."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import ai_editor
import ai_prompts
import article_fetch
import discord_poster
import ping_roles

PAGE = """<html><nav><p>Menu menu menu menu menu menu menu menu menu menu menu menu menu menu</p></nav>
<p>Short.</p>
<p>Hãng X xác nhận 4,9 triệu người bị lộ số an sinh xã hội sau khi kẻ tấn công xâm nhập hệ thống từ tháng 3.</p>
<p>Hãng X xác nhận 4,9 triệu người bị lộ số an sinh xã hội sau khi kẻ tấn công xâm nhập hệ thống từ tháng 3.</p>
<p>Subscribe to our newsletter to get the latest news every day and never miss anything again ok.</p>
<p>Cơ quan chức năng đang điều tra và khuyến cáo người dùng theo dõi tín dụng, đổi mật khẩu các tài khoản liên quan.</p>
<script>var a = "<p>không được lấy cái này vì nằm trong script ok ok ok ok ok ok ok ok ok</p>";</script>
</html>"""


def scored(summary="Tóm tắt ngắn.", body=""):
    art = SimpleNamespace(summary=summary, published=None)
    return SimpleNamespace(title="Florida lộ 4,9 triệu số an sinh xã hội", article=art,
                           link="https://example.com/a-123456", source="The Record",
                           score=80, reasons=[], article_text=body, tags=[], is_hot=False,
                           ai_content="", ai_title="")


class Extract(unittest.TestCase):
    def test_extract_filters_boilerplate_and_dupes(self):
        body = article_fetch.extract_paragraphs(PAGE)
        self.assertIn("4,9 triệu", body)
        self.assertEqual(body.count("4,9 triệu"), 1)           # bỏ trùng
        self.assertNotIn("Menu menu", body)                    # bỏ <nav>
        self.assertNotIn("newsletter", body.lower())           # bỏ quảng cáo/cookie
        self.assertNotIn("script", body.lower())
        self.assertIn("đổi mật khẩu", body)

    def test_fetch_failure_returns_empty(self):
        with mock.patch.object(article_fetch, "_is_public_host", return_value=True), \
             mock.patch.object(article_fetch.requests, "get", side_effect=RuntimeError("x")):
            self.assertEqual(article_fetch.fetch_article_text("https://example.com/a"), "")

    def test_internal_host_blocked(self):
        with mock.patch.object(article_fetch.requests, "get") as g:
            self.assertEqual(article_fetch.fetch_article_text("http://127.0.0.1/admin"), "")
            self.assertEqual(article_fetch.fetch_article_text("file:///etc/passwd"), "")
            g.assert_not_called()

    def test_disabled_by_env(self):
        with mock.patch.dict("os.environ", {"ARTICLE_FETCH_ENABLED": "0"}), \
             mock.patch.object(article_fetch.requests, "get") as g:
            self.assertEqual(article_fetch.fetch_article_text("https://example.com/a"), "")
            g.assert_not_called()


class Writer(unittest.TestCase):
    cfg = SimpleNamespace(model="m", provider="groq", base_url="x", api_key="k", timeout=5,
                          fallback_model="")

    def _content(self, number):
        words = " ".join(["từ"] * 60)
        return ("🚨 Tiêu đề\n\nTóm tắt %s. %s\n\n🔗 Nguồn: The Record\nhttps://example.com/a-123456"
                % (number, words))

    def _run(self, s, content):
        reply = json.dumps({"title": "Tiêu đề", "content": content}, ensure_ascii=False)
        with mock.patch("article_fetch.fetch_article_text",
                        return_value="Có 4,9 triệu người bị ảnh hưởng, tháng 3 năm 2026."), \
             mock.patch.object(ai_editor, "_call_chat", return_value=reply):
            return ai_editor.ai_write(s, None, self.cfg)

    def test_number_from_article_text_is_allowed(self):
        self.assertIsNotNone(self._run(scored(), self._content("4,9 triệu")))

    def test_invented_number_still_rejected(self):
        self.assertIsNone(self._run(scored(), self._content("777 nghìn")))

    def test_payload_has_article_text_and_prompt_asks_for_long(self):
        s = scored(body="Nội dung bài gốc dài.")
        payload = ai_prompts.build_writer_user_payload(s, None)
        self.assertIn("Nội dung bài gốc dài.", payload)
        self.assertIn("1700", ai_prompts.WRITER_SYSTEM_PROMPT)
        self.assertIn("article_text", ai_prompts.INJECTION_GUARD)


class Limit(unittest.TestCase):
    def test_total_message_within_2000_with_two_pings(self):
        s = scored()
        s.ai_content = "🚨 T\n\n" + ("x" * 3000) + "\n\n🔗 Nguồn: The Record\nhttps://example.com/a-123456"
        content = discord_poster.build_post_content(s)
        s.is_hot = True
        s.tags = []
        msg, ids = ping_roles.prepend_pings(content, ping_roles.build_ping_roles(s))
        self.assertEqual(len(ids), 2)
        self.assertLessEqual(len(msg), 2000)
        self.assertIn("https://example.com/a-123456", msg)    # footer luôn còn


if __name__ == "__main__":
    unittest.main(verbosity=2)
