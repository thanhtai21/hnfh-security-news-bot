# -*- coding: utf-8 -*-
import unittest
from types import SimpleNamespace as NS

import article_fetch
from article_fetch import resolve_gnews_url
import ai_editor


class TestGNewsResolve(unittest.TestCase):
    def test_legacy_url_resolved_offline(self):
        # Link dạng cũ có sẵn URL gốc trong chuỗi base64 -> đổi được offline
        legacy = (
            "https://news.google.com/rss/articles/"
            "CBMiSGh0dHBzOi8vdGVjaGNydW5jaC5jb20vMjAyMi8xMC8yNy9uZXcteW9yay1wb3N0LWhhY2tlZC1vZmZlbnNpdmUtdHdlZXRzL9IBAA"
            "?oc=5"
        )
        resolved = resolve_gnews_url(legacy)
        self.assertEqual(resolved, "https://techcrunch.com/2022/10/27/new-york-post-hacked-offensive-tweets/")

    def test_direct_non_gnews_url_kept(self):
        # Bài từ báo trực tiếp không bị đổi
        direct = "https://www.bleepingcomputer.com/news/security/article-123"
        self.assertEqual(resolve_gnews_url(direct), direct)

    def test_empty_or_none(self):
        self.assertEqual(resolve_gnews_url(""), "")
        self.assertIsNone(resolve_gnews_url(None))

    def test_malformed_token_no_crash(self):
        # Gặp lỗi format -> không crash, giữ nguyên link ban đầu
        bad = "https://news.google.com/rss/articles/invalid-token-!@#$%^&*()?oc=5"
        self.assertEqual(resolve_gnews_url(bad), bad)

    def test_offline_failure_falls_back_gracefully(self):
        # Token mới nhưng không có mạng ra ngoài -> trả link cũ, không crash
        new_token_url = (
            "https://news.google.com/rss/articles/"
            "CBMiowFBVV95cUxPTjljeVUwa1U5MnNueUlPOWZraUM2TzhfMnVLbnQxT3VVWWkxaWowU1RxRzlSRUd2MHgzYnlSUlJ0bEJld25WMHZfZ3BHUUdrUklfcV9HUDFrTHM0ejd0MzkxbERhSWNaeXlic2lWLVVPYzVMVUNXZ20xZjZwbzlPcjZaRC1kVDJsR1VNRnJpbTB6RUpMamlRSjEwNUtVNGZ4OUZn"
            "?oc=5"
        )
        self.assertEqual(resolve_gnews_url(new_token_url), new_token_url)


class TestAiEditorGNewsIntegration(unittest.TestCase):
    def test_gnews_link_resolved_before_writing(self):
        legacy = (
            "https://news.google.com/rss/articles/"
            "CBMiSGh0dHBzOi8vdGVjaGNydW5jaC5jb20vMjAyMi8xMC8yNy9uZXcteW9yay1wb3N0LWhhY2tlZC1vZmZlbnNpdmUtdHdlZXRzL9IBAA"
            "?oc=5"
        )
        expected_resolved = "https://techcrunch.com/2022/10/27/new-york-post-hacked-offensive-tweets/"

        art = NS(summary="TechCrunch report on New York Post breach", published=None, link=legacy)
        scored = NS(title="New York Post hacked", link=legacy, article=art, article_text="")

        called_fetch_with = []

        def fake_fetch(url):
            called_fetch_with.append(url)
            return "This is a detailed paragraph about the breach with sufficient words to pass length tests."

        orig_call = ai_editor._call_chat
        orig_fetch = article_fetch.fetch_article_text
        article_fetch.fetch_article_text = fake_fetch

        body = " ".join(["New York Post bị tấn công mạng nghiêm trọng vào ngày hôm qua."] * 5)
        raw_response = '{"title":"New York Post bị tấn công","content":"%s"}' % body
        ai_editor._call_chat = lambda *a, **k: raw_response

        try:
            import ai_prompts
            orig_build = ai_prompts.build_writer_user_payload
            ai_prompts.build_writer_user_payload = lambda s, d: ""

            res = ai_editor.ai_write(scored, NS(), None)

            # Link đã được đổi sang link báo gốc
            self.assertEqual(scored.link, expected_resolved)
            self.assertEqual(scored.article.link, expected_resolved)
            # fetch_article_text được gọi với link báo gốc
            self.assertEqual(called_fetch_with, [expected_resolved])
            self.assertIsNotNone(res)
        finally:
            ai_editor._call_chat = orig_call
            article_fetch.fetch_article_text = orig_fetch
            ai_prompts.build_writer_user_payload = orig_build


if __name__ == "__main__":
    unittest.main(verbosity=1)
