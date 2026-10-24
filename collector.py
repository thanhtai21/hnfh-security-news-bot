# -*- coding: utf-8 -*-
"""
collector.py - Fetch và parse toàn bộ nguồn RSS.

Nguyên tắc:
- Một feed chết KHÔNG được làm crash bot: chỉ log warning và bỏ qua.
- Timeout rõ ràng cho từng request.
- Bỏ bài thiếu title hoặc thiếu link.
- Bỏ bài quá cũ (MAX_ARTICLE_AGE_HOURS) hoặc không rõ thời gian.
- Bỏ trùng lặp trong cùng một lần chạy (URL hoặc title gần giống).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import feedparser
import requests

from config import (
    MAX_ARTICLE_AGE_HOURS,
    MAX_ARTICLES_PER_SOURCE,
    MAX_TOTAL_ARTICLES,
    RSS_SOURCES,
    RSS_TIMEOUT_SECONDS,
    USER_AGENT,
)
from textutils import (
    excerpt,
    is_too_old,
    normalize_title,
    normalize_ws,
    now_vn,
    parse_published,
    safe_str,
    strip_html,
    token_set,
)

logger = logging.getLogger("collector")


# =====================================================================
# DATA MODEL
# =====================================================================

@dataclass
class Article:
    """Một bài viết lấy từ RSS, đã được làm sạch."""
    source: str
    title: str
    link: str
    summary: str
    published: Optional[datetime] = None
    age_hours: Optional[float] = None
    # Các trường phụ, dùng cho dedup nội bộ collector.
    norm_title: str = ""
    tokens: set = field(default_factory=set)

    def text_blob(self):
        """Title + summary đã lowercase, dùng cho scoring trong analyzer."""
        return ((self.title or "") + " " + (self.summary or "")).lower()


# =====================================================================
# FETCH 1 FEED
# =====================================================================

# UA dự phòng: một số nguồn (vd BleepingComputer) chặn UA trình duyệt thông
# thường bằng Cloudflare nhưng chấp nhận UA kiểu feed-reader.
FALLBACK_USER_AGENT = "Mozilla/5.0 (compatible; FeedReader/1.0; +https://github.com/)"


def _fetch_feed_bytes(name, url):
    """
    Fetch nội dung raw của một feed bằng requests (có timeout + UA).
    Thử UA chính trước; nếu bị 403/429 thì thử lại bằng UA dự phòng.
    Trả về bytes, hoặc None nếu lỗi (timeout, HTTP error, rỗng).
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, "
                  "application/xml, text/xml, */*",
    }
    last_error = None

    for attempt, ua in enumerate((USER_AGENT, FALLBACK_USER_AGENT), start=1):
        headers["User-Agent"] = ua
        try:
            response = requests.get(
                url,
                timeout=RSS_TIMEOUT_SECONDS,
                headers=headers,
            )
            if response.status_code in (403, 429) and attempt == 1:
                # Chặn bởi WAF/rate limit -> thử UA dự phòng một lần.
                logger.warning("[RSS] Source HTTP %d, thử UA dự phòng: %s",
                               response.status_code, name)
                last_error = "HTTP %d" % response.status_code
                continue
            response.raise_for_status()
            content = response.content
            if not content:
                logger.warning("[RSS] Source EMPTY: %s (%s)", name, url)
                return None
            return content
        except requests.exceptions.Timeout:
            last_error = "timeout %ss" % RSS_TIMEOUT_SECONDS
            logger.warning("[RSS] Source TIMEOUT after %ss: %s (%s)",
                           RSS_TIMEOUT_SECONDS, name, url)
            break  # timeout thì retry UA cũng vô ích
        except requests.exceptions.HTTPError as exc:
            last_error = str(exc)
            logger.warning("[RSS] Source HTTP ERROR: %s (%s) -> %s",
                           name, url, exc)
            if response.status_code not in (403, 429) or attempt == 2:
                break
        except requests.exceptions.RequestException as exc:
            last_error = str(exc)
            logger.warning("[RSS] Source REQUEST ERROR: %s (%s) -> %s",
                           name, url, exc)
            break
        except Exception as exc:  # Bất kỳ lỗi nào khác cũng không được crash bot.
            last_error = repr(exc)
            logger.warning("[RSS] Source UNEXPECTED ERROR: %s (%s) -> %r",
                           name, url, exc)
            break

    logger.warning("[RSS] Source FAILED (sau retry): %s -> %s", name, last_error)
    return None


def _entry_published(entry):
    """
    Lấy thời gian đăng từ một RSS entry, thử nhiều trường khác nhau.
    Trả về aware datetime (VN tz) hoặc None.
    """
    # Ưu tiên struct_time của feedparser (đã được chuẩn hóa).
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        struct = entry.get(key)
        if struct:
            dt = parse_published(struct)
            if dt is not None:
                return dt
    # Sau đó thử chuỗi thô.
    for key in ("published", "updated", "created", "date"):
        raw = entry.get(key)
        if raw:
            dt = parse_published(raw)
            if dt is not None:
                return dt
    return None


def _entry_summary(entry):
    """
    Lấy summary/description của một entry, thử nhiều trường.
    Trả về chuỗi (có thể rỗng), không bao giờ raise.
    """
    for key in ("summary", "description"):
        value = entry.get(key)
        if value:
            return safe_str(value)
    content = entry.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            value = first.get("value")
            if value:
                return safe_str(value)
    return ""


def _parse_feed(name, raw_bytes, now):
    """
    Parse bytes của một feed thành danh sách Article.
    Không bao giờ raise: mọi lỗi đều được nuốt và log.
    """
    articles = []
    try:
        parsed = feedparser.parse(raw_bytes)
    except Exception as exc:
        logger.warning("[RSS] Source PARSE ERROR: %s -> %r", name, exc)
        return articles

    entries = parsed.get("entries") or []
    if not entries:
        # Feedparser có thể vẫn báo "bozo" nhưng vẫn parse được entries.
        bozo = parsed.get("bozo")
        logger.warning("[RSS] Source NO ENTRIES: %s (bozo=%s)", name, bozo)
        return articles

    for entry in entries[:MAX_ARTICLES_PER_SOURCE]:
        try:
            title = normalize_ws(strip_html(safe_str(entry.get("title"))))
            link = normalize_ws(safe_str(entry.get("link")))

            # Bắt buộc phải có title và link.
            if not title or not link:
                logger.debug("[RSS] SKIP (thiếu title/link) from %s: %r",
                             name, title[:80])
                continue
            if not link.startswith("http"):
                logger.debug("[RSS] SKIP (link không hợp lệ) from %s: %s",
                             name, link)
                continue

            published = _entry_published(entry)

            # Không đăng bài cũ: bỏ qua nếu quá cũ hoặc không rõ thời gian.
            if is_too_old(published, MAX_ARTICLE_AGE_HOURS, now):
                logger.debug("[RSS] SKIP (quá cũ/không rõ TG) from %s: %s",
                             name, title[:80])
                continue

            raw_summary = _entry_summary(entry)
            summary = excerpt(strip_html(safe_str(raw_summary)), 700)

            age_hours = None
            if published is not None and now is not None:
                try:
                    age_hours = (now - published).total_seconds() / 3600.0
                except Exception:
                    age_hours = None

            article = Article(
                source=name,
                title=title,
                link=link,
                summary=summary,
                published=published,
                age_hours=age_hours,
                norm_title=normalize_title(title),
                tokens=token_set(title + " " + summary),
            )
            articles.append(article)
        except Exception as exc:
            logger.warning("[RSS] Entry ERROR in %s: %r", name, exc)
            continue

    return articles


# =====================================================================
# FETCH ALL FEEDS
# =====================================================================

def collect_articles(now=None):
    """
    Fetch và parse toàn bộ nguồn RSS trong config.RSS_SOURCES.

    Trả về: List[Article] đã làm sạch, sắp xếp mới nhất trước,
    đã loại trùng trong lần chạy, và không vượt quá MAX_TOTAL_ARTICLES.
    """
    if now is None:
        now = now_vn()

    all_articles = []
    seen_links = set()
    seen_titles = set()
    ok_sources = 0
    failed_sources = 0

    logger.info("[RSS] Fetching %d sources...", len(RSS_SOURCES))

    for name, url in RSS_SOURCES.items():
        logger.info("[RSS] Fetching source: %s (%s)", name, url)
        raw = _fetch_feed_bytes(name, url)
        if raw is None:
            failed_sources += 1
            logger.warning("[RSS] Source FAILED: %s", name)
            continue

        articles = _parse_feed(name, raw, now)

        # Dedup trong phạm vi feed + giữa các feed (cùng URL hoặc title gần giống).
        new_count = 0
        for article in articles:
            if article.link in seen_links:
                logger.debug("[DEDUP] Bỏ trùng URL: %s", article.link)
                continue
            if article.norm_title and article.norm_title in seen_titles:
                logger.debug("[DEDUP] Bỏ trùng title: %s", article.title[:80])
                continue
            seen_links.add(article.link)
            if article.norm_title:
                seen_titles.add(article.norm_title)
            all_articles.append(article)
            new_count += 1

        ok_sources += 1
        logger.info("[RSS] Source OK: %s (%d bài hợp lệ)", name, new_count)

    # Sắp xếp mới nhất trước.
    def _sort_key(article):
        if article.published is None:
            return datetime.min.replace(tzinfo=None)
        try:
            return article.published.replace(tzinfo=None)
        except Exception:
            return datetime.min.replace(tzinfo=None)

    try:
        all_articles.sort(key=_sort_key, reverse=True)
    except Exception as exc:
        logger.warning("[RSS] Sort error: %r", exc)

    if len(all_articles) > MAX_TOTAL_ARTICLES:
        logger.info("[RSS] Cắt về %d bài (MAX_TOTAL_ARTICLES)",
                    MAX_TOTAL_ARTICLES)
        all_articles = all_articles[:MAX_TOTAL_ARTICLES]

    logger.info("[RSS] Tổng cộng: %d bài từ %d nguồn OK, %d nguồn lỗi",
                len(all_articles), ok_sources, failed_sources)

    return all_articles
