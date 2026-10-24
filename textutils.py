# -*- coding: utf-8 -*-
"""
textutils.py - Xử lý văn bản: normalize, strip HTML, similarity,
trích thời gian, và các hàm hỗ trợ chung.

Không hàm nào gọi network. Toàn bộ là xử lý chuỗi/thời gian thuần.
"""

import html
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from dateutil import parser as date_parser
from dateutil.tz import gettz

from config import TIMEZONE

# Múi giờ Việt Nam, dùng cho toàn bộ logic "hôm nay" và giới hạn bài/ngày.
VN_TZ = gettz(TIMEZONE)

# =====================================================================
# REGEX
# =====================================================================

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_SCRIPT_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</(script|style)>")
_WHITESPACE_RE = re.compile(r"\s+")
_URL_RE = re.compile(r"https?://\S+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]+")
_ENTITY_RE = re.compile(r"&[a-zA-Z#0-9]+;")

# Các token chỉ có ý nghĩa trong ngữ cảnh IT/security nhưng quá ngắn chung.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "being", "at", "by", "as",
    "from", "that", "this", "these", "those", "it", "its", "into", "about",
    "after", "before", "over", "under", "between", "during", "up", "down",
    "out", "off", "new", "how", "why", "what", "who", "when", "you", "your",
    "youre", "you've", "he", "she", "they", "them", "their", "we", "our",
    "has", "have", "had", "do", "does", "did", "will", "would", "can",
    "could", "should", "may", "might", "not", "no", "more", "most",
}

# Các cụm từ chỉ ra bài PR/quảng cáo/blog chung chung -> loại hoặc trừ điểm.
SPAM_PHRASES = [
    "sponsored",
    "press release",
    "advertorial",
    "advertisement",
    "sponsored post",
    "partner content",
    "paid content",
    "sponsored by",
    "this post sponsored",
]

# Các cụm từ clickbait thường gặp -> trừ điểm.
CLICKBAIT_PHRASES = [
    "you won't believe",
    "you wont believe",
    "shocking",
    "unbelievable",
    "mind-blowing",
    "mind blowing",
    "this changes everything",
    "secret trick",
    "hackers don't want you",
    "hackers dont want you",
    "one weird trick",
    "you need to know",
    "must see",
    "viral",
]


# =====================================================================
# HTML / TEXT CLEANING
# =====================================================================

def strip_html(text):
    """Xóa thẻ HTML, decode entity, trả về chuỗi sạch."""
    if not text:
        return ""
    text = str(text)
    text = _HTML_SCRIPT_RE.sub(" ", text)
    text = re.sub(r"(?i)<br\s*/?>", " ", text)
    text = re.sub(r"(?i)</p\s*>", " ", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    # Decode nốt các entity còn sót kiểu &amp; đã escape lần hai.
    try:
        text = html.unescape(text)
    except Exception:
        pass
    text = _URL_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def normalize_ws(text):
    """Gộp nhiều khoảng trắng thành một, cắt đầu/cuối."""
    if not text:
        return ""
    return _WHITESPACE_RE.sub(" ", str(text)).strip()


def normalize_title(title):
    """
    Chuẩn hóa title để so sánh dedup:
    - lowercase
    - bỏ dấu tiếng Việt
    - bỏ ký tự đặc biệt
    - bỏ khoảng trắng thừa
    """
    if not title:
        return ""
    text = strip_html(title).lower()
    # Bỏ dấu tiếng Việt (NFC -> NFD -> bỏ combining marks).
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = unicodedata.normalize("NFC", text)
    text = _NON_ALNUM_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def tokenize(text):
    """Tách từ đã normalize thành list token, loại stopword và token quá ngắn."""
    norm = normalize_title(text)
    if not norm:
        return []
    tokens = [t for t in norm.split() if len(t) >= 3 and t not in _STOPWORDS]
    return tokens


def token_set(text):
    """Trả về set token (dùng cho Jaccard similarity)."""
    return set(tokenize(text))


# =====================================================================
# SIMILARITY / DEDUP HELPER
# =====================================================================

def title_similarity(title_a, title_b):
    """
    Độ tương tự giữa hai title, kết hợp hai phương pháp:
    - SequenceMatcher trên chuỗi đã normalize.
    - Jaccard trên tập token.
    Trả về float trong [0, 1].
    """
    norm_a = normalize_title(title_a)
    norm_b = normalize_title(title_b)
    if not norm_a or not norm_b:
        return 0.0

    seq = SequenceMatcher(None, norm_a, norm_b).ratio()

    set_a = token_set(title_a)
    set_b = token_set(title_b)
    if set_a and set_b:
        inter = len(set_a & set_b)
        union = len(set_a | set_b)
        jaccard = inter / union if union else 0.0
    else:
        jaccard = 0.0

    # Lấy max vì hai phương pháp bắt được kiểu trùng khác nhau:
    # title gần như giống hệt vs cùng bộ từ khóa chính.
    return max(seq, jaccard)


def jaccard_similarity(set_a, set_b):
    """Jaccard giữa hai set. Trả về 0.0 nếu một trong hai rỗng."""
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 0.0


def significant_overlap(set_a, set_b, min_count=3, min_ratio=0.5):
    """
    Kiểm tra hai tập token có nhiều từ khóa trùng đáng kể không.
    Dùng để bắt bài cùng sự kiện nhưng title khác câu:
      A: "Major ransomware group attacked company X"
      B: "Company X hit by major ransomware attack"
    Điều kiện:
    - Có ít nhất min_count token chung, VÀ
    - Tỷ lệ token chung / tập nhỏ hơn >= min_ratio, VÀ
    - Có ít nhất 1 token "mạnh" (độ dài >= 6) trong phần giao,
      để tránh merge nhầm vì các từ chung chung (the, attack, ...).
    """
    if not set_a or not set_b:
        return False
    inter = set_a & set_b
    if len(inter) < min_count:
        return False
    # Ít nhất một token đủ dài/đặc thù trong phần giao.
    if not any(len(tok) >= 6 for tok in inter):
        return False
    smaller = min(len(set_a), len(set_b))
    if smaller == 0:
        return False
    return (len(inter) / smaller) >= min_ratio


_DISTINCTIVE_NUM_RE = re.compile(r"\d[\d,\.]*\d|\d{3,}")


def extract_distinctive_numbers(title):
    """
    Trích các con số ĐẶC TRƯNG của một sự kiện từ title:
    "1,462" / "10,909" / "$105" / "3000000".
    Loại năm (1900-2099) vì năm xuất hiện ở mọi bài.
    """
    nums = set()
    if not title:
        return nums
    text = strip_html(title)
    for raw in _DISTINCTIVE_NUM_RE.findall(text):
        digits = re.sub(r"[^\d]", "", raw)
        if len(digits) < 3:
            continue
        value = int(digits)
        if 1900 <= value <= 2099:
            continue  # bỏ năm
        nums.add(digits)
    return nums


def _numbers_match(nums_a, nums_b, tolerance=0.05):
    """
    So sánh hai tập số đặc trưng: trùng chính xác, HOẶC xấp xỉ (<=5%)
    với số >= 1000.
    Ví dụ: 10909 ~ 11000 (bài "10,909" vs "nearly 11,000") -> match.
    """
    if not nums_a or not nums_b:
        return False
    for raw_a in nums_a:
        try:
            a = int(raw_a)
        except ValueError:
            continue
        for raw_b in nums_b:
            try:
                b = int(raw_b)
            except ValueError:
                continue
            if a == b:
                return True
            bigger = max(a, b)
            if bigger >= 1000 and abs(a - b) / float(bigger) <= tolerance:
                return True
    return False


def is_same_event(title_a, title_b, text_a=None, text_b=None):
    """
    Quyết định hai bài có khả năng là CÙNG một sự kiện không.

    Trình tự kiểm tra:
    1. similarity title >= 0.82 -> cùng sự kiện.
    2. overlap title đáng kể (>= 3 từ chung, >= 50% token nhỏ hơn, token mạnh).
    3. Title có cùng số liệu đặc trưng (vd "1,462") + >= 2 từ chung.
    4. Nếu truyền text (title + summary): so mức rộng hơn —
       >= 7 từ chung TRONG title+summary VÀ số liệu trùng/xấp xỉ (<=5%)
       -> cùng sự kiện (bắt case 2-3 nguồn đưa tin về 1 vụ việc khác nhau
       về cách viết, vd Operation Serengeti III).

    text_a/text_b tùy chọn (mặc định None -> chỉ so title).
    """
    if not title_a or not title_b:
        return False
    if title_similarity(title_a, title_b) >= 0.82:
        return True

    set_a = token_set(title_a)
    set_b = token_set(title_b)
    if significant_overlap(set_a, set_b):
        return True

    # Cùng số liệu đặc trưng trong TITLE + có từ khóa chung.
    shared_nums_title = (
        extract_distinctive_numbers(title_a) & extract_distinctive_numbers(title_b)
    )
    if shared_nums_title:
        shared_tokens = set_a & set_b
        if len(shared_tokens) >= 2 and any(len(t) >= 5 for t in shared_tokens):
            return True

    # Mức rộng hơn: title + summary (nếu caller truyền vào).
    if text_a or text_b:
        blob_a = normalize_ws("%s %s" % (title_a, text_a or ""))
        blob_b = normalize_ws("%s %s" % (title_b, text_b or ""))
        blob_set_a = token_set(blob_a)
        blob_set_b = token_set(blob_b)
        shared = blob_set_a & blob_set_b
        if len(shared) >= 7 and any(len(t) >= 6 for t in shared):
            nums_a = extract_distinctive_numbers(blob_a)
            nums_b = extract_distinctive_numbers(blob_b)
            if _numbers_match(nums_a, nums_b):
                return True
            # Không có số trùng nhưng có entity rất đặc thù (vd tên chiến dịch).
            rare = [t for t in shared if len(t) >= 8]
            if rare:
                return True

    return False


def extract_named_entities(title):
    """
    Trích các "entity" chính từ title: từ viết hoa đầu câu (ngoài đầu câu),
    số có đơn vị (million, 1000, v.v.), và các cụm organization quen thuộc.
    Dùng làm tín hiệu phụ cho dedup.
    """
    entities = set()
    if not title:
        return entities
    text = strip_html(title)

    # Từ viết hoa (không phải từ đầu câu) -> nhiều khả năng là tên riêng.
    words = text.split()
    for word in words[1:]:
        core = re.sub(r"[^A-Za-z0-9\-.]", "", word)
        if len(core) >= 3 and core[:1].isupper() and not core.isupper():
            entities.add(core.lower())

    # Cụm organization phổ biến trong tin security.
    org_patterns = [
        "microsoft", "google", "apple", "cisco", "cisa", "fbi", "interpol",
        "nato", "un", "oracle", "amazon", "aws", "meta", "facebook",
        "nvidia", "intel", "amd", "vmware", "fortinet", "crowdstrike",
        "sentinelone", "kaspersky", "mandiant", "google project zero",
    ]
    low = text.lower()
    for pat in org_patterns:
        if pat in low:
            entities.add(pat)

    return entities


def entity_overlap(title_a, title_b):
    """Số entity chung giữa hai title (tín hiệu phụ cho dedup)."""
    ent_a = extract_named_entities(title_a)
    ent_b = extract_named_entities(title_b)
    if not ent_a or not ent_b:
        return 0
    return len(ent_a & ent_b)


# =====================================================================
# TIME HELPERS
# =====================================================================

def now_vn():
    """Thời điểm hiện tại theo múi giờ Việt Nam (aware datetime)."""
    return datetime.now(VN_TZ)


def parse_published(raw_value, default=None):
    """
    Parse chuỗi thời gian từ RSS (nhiều định dạng) sang aware datetime (VN tz).
    Trả về default nếu không parse được.
    """
    if raw_value is None:
        return default
    if isinstance(raw_value, datetime):
        dt = raw_value
    elif hasattr(raw_value, "tm_year") or (
        isinstance(raw_value, (tuple, list)) and len(raw_value) == 9
    ):
        # struct_time của feedparser -> đã chuẩn hóa về UTC theo chuẩn RSS.
        try:
            return datetime(*raw_value[:6], tzinfo=timezone.utc).astimezone(VN_TZ)
        except Exception:
            return default
    else:
        text = normalize_ws(str(raw_value))
        if not text:
            return default
        try:
            dt = date_parser.parse(text)
        except (ValueError, OverflowError, TypeError):
            return default
    if dt is None:
        return default
    try:
        if dt.tzinfo is None:
            # RSS không kèm tz -> giả định UTC.
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(VN_TZ)
    except Exception:
        return default


def hours_old(published, reference=None):
    """
    Số giờ giữa published và reference (mặc định now VN).
    Trả về None nếu không tính được (published None hoặc ở tương lai quá xa).
    """
    if published is None:
        return None
    ref = reference or now_vn()
    try:
        delta = ref - published
    except Exception:
        return None
    return delta.total_seconds() / 3600.0


def is_too_old(published, max_age_hours, reference=None):
    """
    True nếu bài quá cũ để đăng, hoặc không rõ thời gian.
    Không đăng bài cũ sau mỗi lần GitHub Actions run.
    """
    age = hours_old(published, reference)
    if age is None:
        return True
    if age < -1:
        # Bài có timestamp ở tương lai -> dữ liệu lỗi, loại.
        return True
    return age > max_age_hours


def today_key(reference=None):
    """
    Chuỗi 'YYYY-MM-DD' theo múi giờ Việt Nam.
    Dùng làm khóa đếm số bài/ngày (23:30 VN và 00:10 VN là 2 ngày khác nhau).
    """
    ref = reference or now_vn()
    return ref.astimezone(VN_TZ).strftime("%Y-%m-%d")


def format_vn_time(dt=None):
    """Định dạng thời gian theo giờ Việt Nam để hiển thị trong forum post."""
    ref = dt or now_vn()
    if ref is None:
        return "không rõ"
    try:
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=VN_TZ)
        # CHỈ dùng ASCII trong format của strftime: trên Windows, strftime
        # encode format string theo codepage ANSI (cp1252) -> ký tự tiếng
        # Việt trong format gây UnicodeEncodeError.
        core = ref.astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y")
        return core + " (giờ VN)"
    except Exception:
        return "không rõ"


def days_ago(days):
    """Helper: thời điểm N ngày trước (VN tz)."""
    return now_vn() - timedelta(days=days)


# =====================================================================
# MATCHING HELPERS
# =====================================================================

def contains_any(text, phrases):
    """
    Tìm cụm từ xuất hiện đầu tiên trong text (đã lowercase).
    Trả về cụm từ tìm thấy, hoặc None.
    """
    if not text:
        return None
    low = text.lower()
    for phrase in phrases:
        if phrase.lower() in low:
            return phrase
    return None


def count_matches(text, phrases):
    """Đếm số cụm từ (khác nhau) xuất hiện trong text."""
    if not text:
        return 0
    low = text.lower()
    return sum(1 for phrase in phrases if phrase.lower() in low)


def excerpt(text, limit=400):
    """Cắt text về độ dài limit, thêm '...' nếu bị cắt."""
    text = normalize_ws(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Cắt tại ranh giới từ gần nhất.
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip() + "..."


def safe_str(value, default=""):
    """Ép giá trị bất kỳ về string an toàn."""
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default
