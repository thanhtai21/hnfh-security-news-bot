# -*- coding: utf-8 -*-
"""
article_fetch.py - Lấy phần chữ (các đoạn <p>) của bài gốc để AI viết bài đầy đủ
hơn, vì summary trong RSS thường chỉ 1-2 câu.

An toàn:
- BEST EFFORT: bất kỳ lỗi nào (403, timeout, paywall, HTML lạ) -> trả "" và bot
  dùng summary như trước. KHÔNG BAO GIỜ làm bot lỗi.
- Chỉ http/https; từ chối host trỏ về IP nội bộ (chống SSRF).
- Giới hạn dung lượng tải và độ dài trả về.
- Nội dung tải về là DỮ LIỆU không tin cậy (có thể chứa prompt injection) -> chỉ
  được đưa cho AI trong trường dữ liệu, prompt đã dặn bỏ qua mọi chỉ dẫn trong đó.
- Tắt bằng biến môi trường ARTICLE_FETCH_ENABLED=0.
"""

import html
import ipaddress
import logging
import os
import re
import socket
from urllib.parse import urlparse

import requests

from textutils import strip_html

logger = logging.getLogger("article_fetch")

MAX_DOWNLOAD_BYTES = 1_500_000
MAX_TEXT_CHARS = 4000
MIN_PARAGRAPH_CHARS = 60
MIN_TOTAL_CHARS = 300
TIMEOUT_SECONDS = 10

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_P_RE = re.compile(r"(?is)<p\b[^>]*>(.*?)</p>")
_DROP_RE = re.compile(r"(?is)<(script|style|nav|footer|header|aside|form|noscript)\b.*?</\1>")
_BOILER = ("cookie", "subscribe", "newsletter", "đăng ký nhận", "all rights reserved",
           "bản quyền", "sign up", "follow us", "advertisement", "quảng cáo")


def _enabled():
    return os.environ.get("ARTICLE_FETCH_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off")


def _is_public_host(host):
    """False nếu host trỏ tới IP nội bộ/loopback/link-local."""
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast):
                return False
        return True
    except Exception:
        return False


def extract_paragraphs(page_html):
    """Lấy các đoạn văn dài đủ, bỏ trùng và dòng quảng cáo/cookie. Hàm thuần."""
    page = _DROP_RE.sub(" ", page_html or "")
    seen = set()
    out = []
    total = 0
    for raw in _P_RE.findall(page):
        text = html.unescape(strip_html(raw)).strip()
        if len(text) < MIN_PARAGRAPH_CHARS:
            continue
        low = text.lower()
        if any(b in low for b in _BOILER):
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(text)
        total += len(text)
        if total >= MAX_TEXT_CHARS:
            break
    body = "\n".join(out)
    return body[:MAX_TEXT_CHARS].rstrip()


def fetch_article_text(url):
    """Trả về chuỗi nội dung bài (<= MAX_TEXT_CHARS) hoặc "" nếu không lấy được."""
    if not _enabled() or not url:
        return ""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ""
        if not _is_public_host(parsed.hostname):
            logger.warning("[FETCH] Bỏ qua host nội bộ/không hợp lệ: %s",
                           parsed.hostname)
            return ""
        resp = requests.get(url, headers={"User-Agent": _UA,
                                          "Accept": "text/html,*/*;q=0.8"},
                            timeout=TIMEOUT_SECONDS, stream=True,
                            allow_redirects=True)
        # Sau redirect vẫn phải là host công khai.
        final_host = urlparse(resp.url).hostname or ""
        if final_host != parsed.hostname and not _is_public_host(final_host):
            return ""
        if resp.status_code != 200:
            logger.info("[FETCH] HTTP %d, dùng summary RSS: %s",
                        resp.status_code, url[:80])
            return ""
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "html" not in ctype:
            return ""
        chunks, size = [], 0
        for chunk in resp.iter_content(chunk_size=65536):
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_DOWNLOAD_BYTES:
                break
        resp.close()
        page = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
        body = extract_paragraphs(page)
        if len(body) < MIN_TOTAL_CHARS:
            logger.info("[FETCH] Nội dung quá ngắn/paywall (%d ký tự): %s",
                        len(body), url[:80])
            return ""
        logger.info("[FETCH] Lấy được %d ký tự từ bài gốc: %s", len(body), url[:80])
        return body
    except Exception as exc:
        logger.info("[FETCH] Không lấy được bài gốc (%r): %s", exc, (url or "")[:80])
        return ""
