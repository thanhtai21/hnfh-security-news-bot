# -*- coding: utf-8 -*-
"""Test vá chất lượng bài đăng (python test_v7.py). Không cần mạng/AI."""
import json
import types
import unittest

import ai_prompts
import discord_poster as dp

URL = "https://news.google.com/rss/articles/CBMiXXX?oc=5"

SAMPLE = (
    "🚨 Lỗ hổng zero-day SignDrop trên Windows\n\n"
    "Lỗ hổng SignDrop cho phép kẻ tấn công nâng quyền lên SYSTEM.\n\n"
    "📌 Diễn biến chi tiết:\n• Lỗ hổng được đặt tên là SignDrop.\n\n"
    "🔎 Đáng chú ý:\n"
    "• Đây là lỗ hổng nghiêm trọng (severity: high) mới được phát hiện.\n"
    "• Severity: high, rule_score 76.\n\n"
    "🛡️ Tác động & khuyến nghị:\n"
    "Nguồn chưa cung cấp đủ dữ liệu để đánh giá tác động.\n\n"
    "🔗 Nguồn: Lag.vn\n" + URL
)


def scored(summary="", article_text=""):
    art = types.SimpleNamespace(summary=summary, title="t", published=None,
                                source="Lag.vn")
    return types.SimpleNamespace(
        title="t", article=art, article_text=article_text, link=URL,
        score=80, reasons=["Zero-day (+14)", "Mới <=3h (+5)"], source="Lag.vn")


class CleanTest(unittest.TestCase):
    def test_leak_and_filler_removed(self):
        out = dp._clean_ai_content(SAMPLE)
        self.assertNotIn("severity", out.lower())
        self.assertNotIn("rule_score", out)
        self.assertNotIn("Nguồn chưa cung cấp", out)
        self.assertNotIn("🛡", out)
        self.assertNotIn("🔎", out)            # mục rỗng bị bỏ
        self.assertIn("📌 Diễn biến chi tiết:", out)
        self.assertIn("• Lỗ hổng được đặt tên là SignDrop.", out)

    def test_footer_always_kept(self):
        out = dp._clean_ai_content(SAMPLE)
        self.assertIn("🔗 Nguồn: Lag.vn", out)
        self.assertIn(URL, out)

    def test_clean_content_unchanged(self):
        good = ("🚨 Tiêu đề\n\nTóm tắt.\n\n📌 Diễn biến chi tiết:\n• Ý 1\n• Ý 2\n\n"
                "🛡️ Tác động & khuyến nghị:\nHãy cập nhật bản vá.\n\n🔗 Nguồn: X\n" + URL)
        self.assertEqual(dp._clean_ai_content(good), good)

    def test_parenthetical_leak_stripped_in_prose(self):
        text = "🚨 T\n\nLỗ hổng nghiêm trọng (severity: high) được công bố.\n\n🔗 Nguồn: X\n" + URL
        out = dp._clean_ai_content(text)
        self.assertNotIn("severity", out)
        self.assertIn("Lỗ hổng nghiêm trọng được công bố.", out)

    def test_never_raises_on_bad_input(self):
        self.assertEqual(dp._clean_ai_content(""), "")
        self.assertEqual(dp._clean_ai_content(None), None)


class PromptTest(unittest.TestCase):
    def test_region_rule_and_hint_rename(self):
        self.assertIn("VÙNG (region)", ai_prompts.FILTER_SYSTEM_PROMPT)
        self.assertIn("rule_vietnam_hint", ai_prompts.FILTER_SYSTEM_PROMPT)
        self.assertNotIn("rule_is_vietnam", ai_prompts.FILTER_SYSTEM_PROMPT)

    def test_writer_rules(self):
        w = ai_prompts.WRITER_SYSTEM_PROMPT
        self.assertIn("title_only", w)
        self.assertIn("KHÔNG đưa vào bài các chỉ số", w)
        self.assertNotIn('ghi "Nguồn chưa cung cấp đủ dữ liệu', w)

    def test_data_level(self):
        def level(sc):
            payload = ai_prompts.build_writer_user_payload(sc, None)
            body = payload.split("BÀI VIẾT:", 1)[1]
            return json.loads(body)["data_level"]
        self.assertEqual(level(scored("Tiêu đề · Lag.vn")), "title_only")
        self.assertEqual(level(scored("x" * 300)), "summary")
        self.assertEqual(level(scored("x" * 300, "y" * 700)), "full")


if __name__ == "__main__":
    unittest.main(verbosity=2)
