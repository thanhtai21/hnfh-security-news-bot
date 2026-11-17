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
import base64
import json
from urllib.parse import quote, urlparse

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


# =====================================================================
# [GNEWS] GIẢI MÃ LINK GOOGLE NEWS -> LINK BÁO GỐC
# =====================================================================

def _is_valid_resolved_url(url):
    """Kiểm tra URL sau giải mã có hợp lệ không (chống SSRF / URL rác)."""
    if not url or not isinstance(url, str):
        return False
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        host = p.hostname.lower()
        if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
            return False
        return "." in host
    except Exception:
        return False


def _decode_gnews_token_offline(token):
    """
    Giải link Google News dạng cũ (base64 chứa URL trực tiếp).
    Hoàn toàn offline, không gọi mạng.
    """
    try:
        padding = (4 - len(token) % 4) % 4
        decoded = base64.urlsafe_b64decode((token + "=" * padding).encode("ascii", "ignore"))
        m = re.search(rb"https?://[^\s\x00-\x1f\x7f-\xff<\"'()]+", decoded)
        if m:
            candidate = m.group(0).decode("utf-8", "ignore").rstrip(",.;\"'\)")
            if _is_valid_resolved_url(candidate):
                return candidate
    except Exception:
        pass
    return None


def _resolve_gnews_online(token, original_url=None):
    """
    Giải link Google News dạng mới qua Google batchexecute RPC (Fbv4je).
    Best effort: timeout hoặc lỗi -> trả None.
    """
    try:
        headers = {
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        url = "https://news.google.com/articles/%s" % token
        resp = requests.get(url, headers=headers, timeout=TIMEOUT_SECONDS, allow_redirects=True)

        # 1. Nếu Google redirect trực tiếp ra ngoài domain google.
        final_url = resp.url
        final_host = urlparse(final_url).hostname or ""
        if "google.com" not in final_host and _is_valid_resolved_url(final_url):
            return final_url

        # 2. Tìm signature và timestamp từ HTML
        text = resp.text
        sg_m = re.search(r'data-n-a-sg=["\']([^"\']+)["\']', text)
        ts_m = re.search(r'data-n-a-ts=["\']([^"\']+)["\']', text)
        if not sg_m or not ts_m:
            return None

        sg = sg_m.group(1)
        ts = ts_m.group(1)

        # 3. Gửi payload batchexecute RPC (Fbv4je)
        articles_req = [
            "Fbv4je",
            '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'            '"X","X",1,[1,1,1],1,1,null,0,0,null,0],"%s",%s,"%s"]' % (token, ts, sg),
        ]
        payload = "f.req=%s" % quote(json.dumps([[articles_req]]))
        post_headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": _UA,
        }
        rpc_resp = requests.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers=post_headers,
            data=payload,
            timeout=TIMEOUT_SECONDS,
        )
        if rpc_resp.status_code != 200:
            return None

        raw = rpc_resp.text
        m = re.search(r'\["garturlres"\s*,\s*"([^"]+)"', raw)
        if m:
            candidate = m.group(1).replace(r"\/", "/")
            if _is_valid_resolved_url(candidate):
                return candidate

        for line in raw.splitlines():
            line = line.strip()
            if not line or line.isdigit():
                continue
            try:
                outer = json.loads(line)
                if isinstance(outer, list):
                    for item in outer:
                        if isinstance(item, list) and len(item) >= 3 and item[1] == "Fbv4je":
                            inner = json.loads(item[2])
                            if isinstance(inner, list) and len(inner) >= 2 and inner[0] == "garturlres":
                                candidate = inner[1]
                                if _is_valid_resolved_url(candidate):
                                    return candidate
            except Exception:
                continue
    except Exception as exc:
        logger.debug("[GNEWS] batchexecute thất bại (%r): %s", exc, token)

    return None


def resolve_gnews_url(url):
    """
    Đổi link Google News thành link báo gốc (best effort).
    - Dạng cũ (base64 chứa URL): giải offline ngay.
    - Dạng mới: gọi batchexecute lên Google News.
    - Lỗi / không giải được -> trả về url ban đầu (không crash).
    """
    if not url or "news.google.com" not in url:
        return url

    token_m = re.search(r"/(?:articles|read)/([A-Za-z0-9\-_]+)", url)
    if not token_m:
        return url

    token = token_m.group(1)

    try:
        decoded = _decode_gnews_token_offline(token)
        if decoded:
            logger.info("[GNEWS] Giải link gốc: %s", decoded)
            return decoded
    except Exception as exc:
        logger.debug("[GNEWS] Giải offline thất bại (%r): %s", exc, url[:80])

    try:
        resolved = _resolve_gnews_online(token, url)
        if resolved:
            logger.info("[GNEWS] Giải link gốc: %s", resolved)
            return resolved
    except Exception as exc:
        logger.debug("[GNEWS] Giải online thất bại (%r): %s", exc, url[:80])

    return url
