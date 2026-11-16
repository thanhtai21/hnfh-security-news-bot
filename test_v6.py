"""Test v6: gửi log về kênh admin (log-botnews)."""
import logging
import os
import unittest

import admin_log

LINES = [
    "2026-11-16 15:05:01 [INFO   ] main       BẮT ĐẦU CHẠY BOT",
    "2026-11-16 15:05:02 [WARNING] collector  [RSS] Source ERROR The Cyber Express: timeout",
    "2026-11-16 15:05:03 [WARNING] collector  [RSS] Source OK: Dan Tri Cong nghe (0 bài hợp lệ)",
    "2026-11-16 15:05:04 [INFO   ] hot        [EVENT] Bỏ (cùng chuyện với bài đã đăng): a ~ b",
    "2026-11-16 15:05:05 [WARNING] main       [AI] Chưa có bài do AI viết -> KHÔNG đăng bản rule-based: Meta",
    "2026-11-16 15:05:06 [INFO   ] main       [MISSED] score=88 hard=True age=3h src=X | Outlook zero-day -> bị loại",
    "2026-11-16 15:05:07 [ERROR  ] main       LỖI: gsk_ABCDEFGHIJKLMNOPQRSTUVWX1234 token=abc123",
]
INFO = {"now": "15:05 16/11/2026", "exit_code": 0, "selected": 2, "posted": 1,
        "hot": 0, "rejected": 3, "posted_titles": ["Tin A"]}


class Summary(unittest.TestCase):
    def test_contains_key_sections_and_fits(self):
        s = admin_log.build_summary(LINES, INFO)
        self.assertLessEqual(len(s), admin_log.SUMMARY_MAX)
        for needle in ("đăng 1/2", "Tin A", "Outlook zero-day", "AI", "Nguồn", "Lỗi"):
            self.assertIn(needle, s)

    def test_secrets_redacted(self):
        s = admin_log.build_summary(LINES, INFO)
        self.assertNotIn("gsk_ABCDEFGHIJKLMNOPQRSTUVWX1234", s)
        self.assertNotIn("abc123", s)

    def test_failure_exit_code_shown(self):
        s = admin_log.build_summary(LINES, dict(INFO, exit_code=1))
        self.assertIn("❌", s)
        self.assertIn("Mã thoát", s)

    def test_huge_input_is_clipped(self):
        many = LINES + ["[WARNING] x " + "y" * 400] * 500
        self.assertLessEqual(len(admin_log.build_summary(many, INFO)),
                             admin_log.SUMMARY_MAX)


class Capture(unittest.TestCase):
    def test_capture_collects_and_removes(self):
        h = admin_log.install_capture()
        try:
            logging.getLogger("zzz").warning("xin chào %s", "log")
        finally:
            admin_log.remove_capture(h)
        self.assertTrue(any("xin chào log" in ln for ln in h.lines))
        logging.getLogger("zzz").warning("sau khi gỡ")
        self.assertFalse(any("sau khi gỡ" in ln for ln in h.lines))


class Send(unittest.TestCase):
    def test_dry_run_never_sends(self):
        self.assertFalse(admin_log.send_admin_log("tok", 1, LINES, INFO, dry_run=True))

    def test_disabled(self):
        os.environ["ADMIN_LOG_ENABLED"] = "0"
        try:
            self.assertFalse(admin_log.send_admin_log("tok", 1, LINES, INFO))
        finally:
            del os.environ["ADMIN_LOG_ENABLED"]

    def test_missing_token(self):
        self.assertFalse(admin_log.send_admin_log("", 1, LINES, INFO))

    def test_only_issues_skips_quiet_run(self):
        os.environ["ADMIN_LOG_ONLY_ISSUES"] = "1"
        try:
            quiet = ["2026-11-16 15:05:01 [INFO   ] main       chạy bình thường"]
            self.assertFalse(admin_log.send_admin_log(
                "tok", 1, quiet, dict(INFO, posted=0)))
        finally:
            del os.environ["ADMIN_LOG_ONLY_ISSUES"]

    def test_send_failure_never_raises(self):
        orig = admin_log._send
        async def boom(*a, **k):
            raise RuntimeError("mạng lỗi")
        admin_log._send = boom
        try:
            self.assertFalse(admin_log.send_admin_log("tok", 1, LINES, INFO))
        finally:
            admin_log._send = orig

    def test_send_ok_path(self):
        orig = admin_log._send
        seen = {}
        async def fake(token, forum_id, summary, full, filename):
            seen.update(summary=summary, full=full, filename=filename)
            return {"ok": True, "error": ""}
        admin_log._send = fake
        try:
            self.assertTrue(admin_log.send_admin_log("tok", 1, LINES, INFO))
        finally:
            admin_log._send = orig
        self.assertTrue(seen["filename"].endswith(".txt"))
        self.assertNotIn("gsk_ABCDEFGHIJKLMNOPQRSTUVWX1234", seen["full"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
