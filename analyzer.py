# -*- coding: utf-8 -*-
"""
analyzer.py - Phân loại, chấm điểm (0-100), chọn tag, lọc trùng, áp quota.

NGUYÊN TẮC CỐT LÕI:
- Bot KHÔNG đăng mọi bài có chữ "hacker".
- Chỉ bài ĐẠT ĐIỂM MỚI được đăng:
    * score >= MIN_SCORE_TO_POST (85), HOẶC
    * score >= SPECIAL_MIN_SCORE (70) VÀ có "yếu tố đặc biệt" (is_special).
- Tin Việt Nam được cộng điểm ưu tiên, nhưng không được cộng điểm chỉ vì
  nó xảy ra tại Việt Nam.
- Bài quảng cáo / press release / clickbait / tin đời sống -> LOẠI.

API (dùng bởi main.py / state.py ở Phần 3):
- ScoredArticle            : dataclass kết quả phân tích.
- analyze_articles(articles) -> List[ScoredArticle]  (đã sort theo điểm giảm dần)
- select_articles(scored, state, now) -> List[ScoredArticle]  (đã lọc dedup + quota)
- build_tags(...)          : List[str] tên tag Forum.
- make_fingerprint(...)    : str fingerprint lưu vào state (dedup giữa các run).
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List, Optional

from config import (
    AI_CANDIDATE_MIN_SCORE,
    AI_RESCUE_MIN_SCORE,
    DEFAULT_TAG_NAMES,
    FORUM_TAG_NAMES,
    HARD_SIGNAL_MIN_USERS,
    HARD_SIGNAL_MIN_USD,
    MAX_POSTS_PER_DAY,
    MIN_SCORE_TO_POST,
    ORGS_TIER1,
    ORGS_TIER2,
    SOURCE_KIND,
    SOURCE_REQUIRES_HARD_SIGNAL,
    SOURCE_TRUST_SCORE,
    SPECIAL_MIN_SCORE,
)
from textutils import (
    CLICKBAIT_PHRASES,
    SPAM_PHRASES,
    contains_any,
    count_matches,
    entity_overlap,
    is_same_event,
    normalize_title,
    now_vn,
    title_similarity,
    today_key,
)

logger = logging.getLogger("analyzer")


# =====================================================================
# 1. TỪ KHOÁ THEO CHỦ ĐỀ (phân loại: IT / CYBERSECURITY / CYBERCRIME)
# =====================================================================

# Chỉ xuất hiện 1 lần trong title -> vẫn tính là match (phrase đủ đặc thù).
TOPIC_PHRASES = {
    "CYBERSECURITY": [
        # Vulnerability / exploit
        "cve-", "zero-day", "zero day", "zeroday", "0-day", "exploit",
        "vulnerability", "vulnerable", "remote code execution", "rce",
        "privilege escalation", "buffer overflow", "patch", "advisory",
        "security flaw", "security bug", "flaw", "backdoor", "trojan",
        # Malware / ransomware
        "malware", "ransomware", "ransomware attack", "encrypt", "worm",
        "botnet", "rootkit", "keylogger", "infostealer", "stealer",
        "cobalt strike", "lockbit", "blackcat", "alphv", "conti", "revil",
        "cl0p", "clop", "rhysida", "akira", "play ransomware",
        # Phishing / scam
        "phishing", "phish", "scam", "spearphishing", "business email compromise",
        "bec", "credential stuffing", "account takeover", "smishing",
        # Network / infra security
        "ddos", "botnet attack", "intrusion", "breach", "compromised",
        "cyberattack", "cyber attack", "cyber-attack", "attack",
        "network security", "cloud security", "endpoint security",
        "iot security", "mobile security", "supply chain attack",
        # Threat intel
        "apt", "threat actor", "threat group", "cyber espionage", "espionage",
        "nation-state", "state-sponsored", "hacking group", "cybercriminal",
        # Data
        "data breach", "data leak", "data leak", "leaked data", "data theft",
        "stolen data", "exposed database", "exposed", "leak",
        # Misc
        "bug bounty", "cisa", "nvd", "kev catalog", "actively exploited",
        "in the wild", "threat intelligence", "infosec", "cybersecurity",
        "cyber security", "security researcher", "incident response",
        # Lộ bí mật / khóa / thông tin xác thực (hay bị bỏ sót)
        "secret", "api key", "private key", "signing key", "credentials",
        "access token", "unauthorized access", "hijack", "compromise",
        "malicious", "steal", "stole", "supply chain",
        # A1: từ khóa vụ trộm/sự cố (thiếu nhóm này -> tin Binance hack bị điểm 0)
        "hack", "hacked", "hackers", "stolen", "theft", "heist",
        "drain", "drained", "siphon", "siphoned",
        "security incident", "unauthorized transactions", "exploited",
        # D2: từ khóa tiếng Việt (nguồn VN - để classify_topic + detect_vietnam
        # hoạt động đúng với bài tiếng Việt)
        "tấn công mạng", "tấn công", "lộ lọt dữ liệu", "lộ dữ liệu",
        "rò rỉ dữ liệu", "rò rỉ", "mã độc", "phần mềm độc hại",
        "lỗ hổng", "khai thác lỗ hổng", "đánh cắp dữ liệu", "cửa hậu",
        "an ninh mạng", "mối đe dọa", "tống tiền", "truy cập trái phép",
        "mật khẩu", "thông tin cá nhân",
    ],
    "CYBERCRIME": [
        # Law enforcement
        "arrested", "arrest", "charged", "indicted", "indictment", "sentenced",
        "pleaded guilty", "guilty", "takedown", "taken down", "shut down",
        "seized", "seizure", "dismantled", "busted", "raid", "operation",
        "law enforcement", "police", "fbi", "doj", "europol", "interpol",
        "national crime agency", "nca", "prosecutor", "court",
        "criminal complaint", "extradited", "custody", "convicted",
        # Vietnam-specific law enforcement (thêm dấu tiếng Việt)
        "công an", "bắt giữ", "khởi tố", "triệt phá", "tội phạm mạng",
        "cơ quan điều tra", "bộ công an", "điều tra",
        # D2: từ khóa tội phạm mạng tiếng Việt (nguồn VN)
        "lừa đảo", "lừa đảo trực tuyến", "lừa đảo mạng", "mạo danh",
        "chiếm đoạt", "trộm cắp", "chuyển tiền bất hợp pháp",
        # Cybercrime activity
        "cybercrime", "cyber crime", "online fraud", "fraud", "scammer",
        "scammers", "extortion", "blackmail", "dark web", "darknet",
        "stolen credentials", "identity theft", "money laundering",
        "cryptocurrency laundering", "ransomware group", "hacking group",
        "gang", "syndicate", "crime ring", "takedown operation",
    ],
    "IT": [
        # Software / OS
        "software", "windows", "linux", "macos", "android", "ios",
        "operating system", "update", "updates", "release", "kernel",
        "open source", "google", "microsoft", "apple", "oracle",
        # Hardware
        "hardware", "processor", "chip", "cpu", "gpu", "nvidia", "intel",
        "amd", "firmware", "uefi", "bios", "router", "modem",
        # Network / cloud / server / db
        "network", "cloud", "server", "datacenter", "data center", "database",
        "internet", "broadband", "dns", "bgp", "router", "firewall",
        "aws", "azure", "kubernetes", "docker", "virtualization",
        "outage", "downtime", "service disruption",
        # AI liên quan trực tiếp IT
        "ai model", "llm", "chatgpt", "artificial intelligence",
        "machine learning", "ai-powered",
    ],
}

# Từ khóa chỉ match nếu xuất hiện >= 2 lần (hoặc đi kèm tín hiệu khác),
# vì chúng quá phổ biến / quá chung.
WEAK_PHRASES = [
    "attack", "hacked", "hacker", "hacking", "hackers", "hack",
    "security", "threat", "breach", "leak", "flaw", "patch",
    "update", "release", "warning", "risk", "abuse", "bypass",
]

# Chấm điểm theo cụm từ MẠNH (tăng điểm đáng kể khi match).
# Mỗi tuple: (cụm từ, điểm cộng, lý do ghi log).
POSITIVE_SIGNALS = [
    # Tín hiệu "đang hot" (Section 24 của yêu cầu)
    ("actively exploited", 14, "Đang bị khai thác thực tế"),
    ("in the wild", 12, "Đang lan truyền thực tế"),
    ("zero-day", 14, "Zero-day"),
    ("zero day", 14, "Zero-day"),
    ("zeroday", 14, "Zero-day"),
    ("0-day", 14, "Zero-day"),
    ("actively being exploited", 14, "Đang bị khai thác thực tế"),
    ("critical vulnerability", 13, "Lỗ hổng nghiêm trọng"),
    ("remote code execution", 9, "Remote code execution"),
    ("rce", 6, "Remote code execution"),
    ("ransomware", 12, "Ransomware"),
    ("ransomware attack", 12, "Ransomware attack"),
    ("ransomware group", 12, "Nhóm ransomware"),
    ("data breach", 12, "Rò rỉ dữ liệu"),
    ("data leak", 11, "Rò rỉ dữ liệu"),
    ("leaked data", 10, "Dữ liệu bị rò rỉ"),
    ("stolen data", 10, "Dữ liệu bị đánh cắp"),
    ("million users", 11, "Ảnh hưởng hàng triệu người dùng"),
    ("million records", 11, "Hàng triệu bản ghi"),
    ("million devices", 11, "Hàng triệu thiết bị"),
    ("million accounts", 11, "Ảnh hưởng hàng triệu người dùng"),
    ("private key", 8, "Lộ khóa riêng"),
    ("api key", 6, "Lộ khóa API"),
    ("secrets", 6, "Lộ bí mật/thông tin xác thực"),
    ("millions of", 9, "Quy mô hàng triệu"),
    ("critical infrastructure", 13, "Hạ tầng quan trọng"),
    ("apt", 12, "Chiến dịch APT"),
    ("apt28", 12, "APT28"),
    ("apt29", 12, "APT29"),
    ("apt41", 12, "APT41"),
    ("lazarus", 12, "Nhóm Lazarus"),
    ("state-sponsored", 11, "Nhà nước bảo trợ"),
    ("nation-state", 11, "Nhà nước bảo trợ"),
    ("threat actor", 9, "Threat actor"),
    ("cyber espionage", 10, "Điện tử gián điệp"),
    ("supply chain attack", 11, "Tấn công chuỗi cung ứng"),
    ("widespread", 8, "Lan rộng"),
    ("massive", 8, "Quy mô lớn"),
    ("large-scale", 9, "Quy mô lớn"),
    ("unauthenticated", 6, "Không cần xác thực"),
    ("wormable", 10, "Có khả năng lan truyền kiểu worm"),
    ("backdoor", 8, "Cửa hậu"),
    ("botnet", 8, "Botnet"),
    ("ddos", 7, "DDoS"),
    ("phishing", 7, "Phishing campaign"),
    ("credential stuffing", 7, "Credential stuffing"),
    ("account takeover", 7, "Takeover tài khoản"),
    ("exposed database", 9, "Database bị lộ"),
    ("cloud security", 6, "Cloud security"),
    ("iot", 5, "IoT"),
    ("hospital", 8, "Bệnh viện"),
    ("banking", 7, "Ngân hàng"),
    ("bank", 5, "Ngân hàng"),
    ("telecom", 8, "Viễn thông"),
    ("government", 7, "Chính phủ"),
    ("ministry", 7, "Bộ/ngành"),
    ("airport", 8, "Sân bay"),
    ("power grid", 10, "Lưới điện"),
    ("energy sector", 8, "Ngành năng lượng"),
    ("major company", 8, "Doanh nghiệp lớn"),
    ("fortune 500", 9, "Doanh nghiệp lớn (Fortune 500)"),
    # Law enforcement / cybercrime operation
    ("arrested", 13, "Bị bắt giữ"),
    ("charged", 10, "Bị truy tố"),
    ("indicted", 10, "Bị truy tố"),
    ("sentenced", 10, "Bị kết án"),
    ("takedown", 12, "Triệt phá"),
    ("taken down", 12, "Triệt phá"),
    ("dismantled", 12, "Triệt phá"),
    ("shut down", 9, "Bị sập"),
    ("seized", 9, "Bị thu giữ"),
    ("law enforcement", 11, "Cơ quan thực thi pháp luật"),
    ("police", 8, "Cảnh sát"),
    ("fbi", 9, "FBI"),
    ("doj", 8, "DOJ"),
    ("europol", 10, "Europol"),
    ("interpol", 9, "Interpol"),
    ("pleaded guilty", 10, "Nhận tội"),
    ("extradited", 9, "Bị dẫn độ"),
    ("crime ring", 10, "Ổ nhóm tội phạm"),
    ("scam network", 10, "Mạng lưới lừa đảo"),
    ("cybercrime", 10, "Tội phạm mạng"),
    ("dark web", 8, "Dark web"),
    ("extortion", 8, "Bắt nạt tống tiền"),
    # Vụ trộm / tấn công sàn tiền mã hóa (ví dụ vụ Binance, Bybit).
    # Chỉ cụm có NGỮ CẢNH tấn công mới được cộng điểm nhiều và tính là "đặc biệt";
    # tin giá coin / tin thị trường bình thường KHÔNG match các cụm này.
    ("crypto heist", 22, "Vụ trộm tiền mã hóa quy mô lớn"),
    ("crypto theft", 16, "Vụ trộm tiền mã hóa quy mô lớn"),
    ("cryptocurrency theft", 16, "Vụ trộm tiền mã hóa quy mô lớn"),
    ("crypto hack", 16, "Vụ trộm tiền mã hóa quy mô lớn"),
    ("exchange hack", 16, "Vụ trộm tiền mã hóa quy mô lớn"),
    ("exchange hacked", 16, "Vụ trộm tiền mã hóa quy mô lớn"),
    ("stolen crypto", 14, "Vụ trộm tiền mã hóa quy mô lớn"),
    ("stolen cryptocurrency", 14, "Vụ trộm tiền mã hóa quy mô lớn"),
    ("hot wallet", 10, "Ví nóng bị tấn công"),
    ("north korea", 10, "Nhà nước bảo trợ"),
    ("north korean", 10, "Nhà nước bảo trợ"),
    ("tradertraitor", 12, "Nhóm Lazarus"),
    ("biggest crypto", 16, "Quy mô kỷ lục"),
    ("largest crypto", 16, "Quy mô kỷ lục"),
    ("biggest hack", 16, "Quy mô kỷ lục"),
    ("largest hack", 16, "Quy mô kỷ lục"),
    ("in history", 8, "Quy mô kỷ lục"),
]

# Tín hiệu "yếu tố đặc biệt" — bài 70-84 chỉ được đăng nếu có ít nhất 1 cái.
# Đây là nhóm tín hiệu mạnh, KHÔNG phải cách lách threshold.
SPECIAL_SIGNALS = [
    "Đang bị khai thác thực tế",
    "Zero-day",
    "Lỗ hổng nghiêm trọng",
    "Hạ tầng quan trọng",
    "Triệt phá",
    "Bị bắt giữ",
    "Cơ quan thực thi pháp luật",
    "Ransomware",
    "Rò rỉ dữ liệu",
    "Chiến dịch APT",
    "Quy mô hàng triệu",
    "Hàng triệu người dùng",
    "Hàng triệu bản ghi",
    "Hàng triệu thiết bị",
    "Quy mô lớn",
    "Doanh nghiệp lớn",
    "Chính phủ",
    "Ngân hàng",
    "Bệnh viện",
    "Viễn thông",
    "Mạng lưới lừa đảo",
    "Ổ nhóm tội phạm",
    "Tội phạm mạng",
    "Nhóm ransomware",
    "Điện tử gián điệp",
    "Tấn công chuỗi cung ứng",
    "Liên quan Việt Nam",
    "Vụ trộm tiền mã hóa quy mô lớn",
    "Quy mô kỷ lục",
    # A3(b): lý do của detect_hard_signals -> luôn làm is_special=True.
    "Tổn thất lớn",
    "Tổ chức trọng yếu",
]


def _specials_in_text(text):
    """
    E2 helper: tập SPECIAL_SIGNALS xuất hiện trong MỘT chuỗi lý do (case-sensitive,
    đúng ngữ nghĩa so khớp cũ nhưng áp dụng trên TỪNG lý do riêng lẻ thay vì
    cả chuỗi reasons_joined -> không thể match "lọt" qua ranh giới 2 lý do).
    """
    if not text:
        return set()
    return {s for s in SPECIAL_SIGNALS if s in text}


# E2: ánh xạ (lý do của POSITIVE_SIGNALS) -> tập SPECIAL_SIGNALS mà lý do đó kích hoạt.
# Dùng để xác định is_special TRỰC TIẾP từ các tín hiệu đã match trong lúc chấm điểm.
SPECIALS_FROM_SIGNAL = {}
for _phrase, _points, _reason in POSITIVE_SIGNALS:
    SPECIALS_FROM_SIGNAL.setdefault(
        _reason, set()).update(_specials_in_text(_reason))

# Tín hiệu tiêu cực — LOẠI hoặc trừ điểm mạnh.
# A4: các từ MƠ HỒ (có thể xuất hiện vô hại trong tin an ninh, vd "raises
# concerns") chỉ được so với TITLE - xem NEGATIVE_TITLE_ONLY bên dưới.
NEGATIVE_SIGNALS = [
    ("sponsored", -50, "Quảng cáo/sponsored"),
    ("press release", -50, "Press release"),
    ("advertorial", -50, "Quảng cáo"),
    ("paid content", -50, "Nội dung trả phí"),
    ("partner content", -50, "Nội dung đối tác"),
    ("review", -30, "Bài đánh giá"),
    ("opinion", -30, "Bài quan điểm"),
    ("editorial", -25, "Bài editorial"),
    ("how to", -25, "Bài hướng dẫn"),
    ("how-to", -25, "Bài hướng dẫn"),
    ("tips", -18, "Mẹo vặt"),
    ("best vpn", -40, "Quảng cáo VPN"),
    ("best antivirus", -40, "Quảng cáo antivirus"),
    ("discount", -30, "Khuyến mãi"),
    ("coupon", -30, "Mã giảm giá"),
    ("giveaway", -25, "Giveaway"),
    ("product launch", -30, "Ra mắt sản phẩm"),
    ("gaming", -40, "Tin game"),
    ("video game", -40, "Tin game"),
    ("esports", -40, "Esports"),
    ("hollywood", -40, "Giải trí"),
    ("celebrity", -40, "Giải trí"),
    ("kardashian", -40, "Giải trí"),
    ("movie", -35, "Phim"),
    ("box office", -35, "Phim"),
    ("football", -40, "Thể thao"),
    ("soccer", -40, "Thể thao"),
    ("nba", -40, "Thể thao"),
    ("premier league", -40, "Thể thao"),
    ("olympics", -40, "Thể thao"),
    ("senator", -25, "Chính trị"),
    ("lifestyle", -35, "Đời sống"),
    ("recipe", -40, "Đời sống"),
    ("travel", -35, "Đời sống"),
    ("fashion", -35, "Đời sống"),
    ("smartphone launch", -30, "Ra mắt điện thoại"),
    ("hands-on", -25, "Bài trải nghiệm"),
    ("unboxing", -30, "Unboxing"),
]

# A4: từ MƠ HỒ - chỉ so với TITLE (không áp lên summary, vì summary hay có
# các cụm vô hại như "raises concerns", "stock up on security patches"...).
NEGATIVE_TITLE_ONLY = [
    ("unveils", -18, "Ra mắt sản phẩm"),
    ("introduces", -15, "Ra mắt sản phẩm"),
    ("announces", -10, "Công bố (có thể PR)"),
    ("partnership", -20, "Hợp tác thương mại"),
    ("funding", -25, "Gọi vốn"),
    ("raises", -20, "Gọi vốn"),
    ("valuation", -25, "Định giá công ty"),
    ("earnings", -30, "Kết quả kinh doanh"),
    ("stock", -25, "Chứng khoán"),
    ("election", -30, "Chính trị"),
    ("president", -20, "Chính trị"),
]

# Các cụm từ chỉ ra "tin đời sống / công nghệ thông thường, không security".
IRRELEVANT_PHRASES = [
    "iphone", "ipad", "apple watch", "samsung galaxy", "pixel launch",
    "netflix", "streaming", "spotify", "tiktok challenge", "youtube",
    "price hike", "pre-order", "release date", "colors revealed",
]


# =====================================================================
# MATCH THEO RANH GIỚI TỪ (tránh false positive kiểu "rce" trong "enforcement")
# =====================================================================

@lru_cache(maxsize=2048)
def _compile_phrase(phrase):
    """
    Biên dịch cụm từ thành regex có ranh giới từ ở hai đầu.

    Lưu ý: cụm kết thúc bằng ký tự không phải chữ/số (vd "cve-")
    sẽ KHÔNG thêm ranh giới phải, để còn match "cve-2024-1234".
    """
    p = phrase.lower()
    body = re.escape(p)

    # Cho phép biến thể số nhiều / chia động từ ở từ CUỐI của cụm
    # ("exposes", "leaked", "attackers", "vulnerabilities"...); nếu không,
    # tin như "Railway exposes secrets" bị loại nhầm là OTHER.
    # Chỉ áp dụng cho cụm >= 4 ký tự, kết thúc bằng chữ (tránh "rce"...).
    if len(p) >= 4 and p[-1].isalpha():
        suffix = r"(?:s|es|ed|d|ing|er|ers)?"
        if p.endswith("y") and p[-2] not in "aeiou":
            # phụ âm + y -> ies (vulnerability -> vulnerabilities)
            body = "(?:" + body + suffix + "|" + re.escape(p[:-1]) + "ies)"
        else:
            body = body + suffix

    pattern = body
    if p and p[0].isalnum():
        pattern = r"(?<![a-z0-9])" + pattern
    if p and p[-1].isalnum():
        pattern = pattern + r"(?![a-z0-9])"
    return re.compile(pattern)


def match_phrase(text, phrase):
    """
    Kiểm tra phrase có xuất hiện trong text như MỘT TỪ/CỤM TỪ độc lập không.
    Ví dụ: match_phrase("law enforcement", "rce") -> False (không match "enforcement").
    """
    if not text or not phrase:
        return False
    try:
        return bool(_compile_phrase(phrase).search(text.lower()))
    except Exception:
        return phrase.lower() in text.lower()


def match_any(text, phrases):
    """Trả về cụm từ đầu tiên match theo ranh giới từ, hoặc None."""
    if not text:
        return None
    for phrase in phrases:
        if match_phrase(text, phrase):
            return phrase
    return None


def count_matches_bounded(text, phrases):
    """Đếm số cụm từ khác nhau match theo ranh giới từ."""
    if not text:
        return 0
    return sum(1 for p in phrases if match_phrase(text, p))


# =====================================================================
# 3a. TÍN HIỆU CỨNG (HARD SIGNAL) - "tin quy mô lớn" (A3 / D1)
# =====================================================================

# Điểm cộng khi bài CÓ tín hiệu cứng (A3b).
HARD_SIGNAL_POINTS = 25

# Từ khóa tấn công/sự cố (A3.1 - ngữ cảnh bắt buộc khi tính số tiền,
# và cổng lọc nguồn crypto/tổng hợp/VIỆT NAM D1/5).
HARD_ATTACK_PHRASES = [
    # Tiếng Anh
    "hack", "stolen", "theft", "heist", "drain", "siphon",
    "breach", "breached", "exploit", "exploited", "attack", "compromised",
    "intrusion", "ransomware", "malware", "backdoor", "phishing", "botnet",
    "apt", "lazarus", "tradertraitor", "security incident",
    "data breach", "data leak", "unauthorized access",
    "denial of service", "ddos", "zero-day",
    # Tiếng Việt (dùng cho cổng nguồn "vn" + ngữ cảnh tính tiền)
    "tấn công", "đánh cắp", "trộm", "rò rỉ", "lộ lọt", "mã độc",
    "lừa đảo", "khai thác", "tống tiền", "phần mềm độc hại",
    "lỗ hổng", "an ninh mạng", "sự cố bảo mật", "truy cập trái phép",
]

# Từ khóa SỰ CỐ NẠN NHÂN dùng để GHÉP với ORGS_TIER1 trong nhóm (4)
# (chỉ xét khi TÊN ORG nằm trong TITLE).
# CHỈ gồm ngữ cảnh nạn nhân: bị breach / bị hack / bị đánh cắp / mất dịch vụ.
# LOẠI CÓ CHỦ ĐÍCH: "exploit"/"exploited" (tin vá lỗ hổng), "attack" trần,
# "patch"/"fixes" (Patch Tuesday), "zero-day", "advisory"/"KEV" (CISA),
# "arrest"/"charged" (tin bắt người) -> các tin thường ngày có tên
# Microsoft/CISA/FBI/Cisco KHÔNG thành hard_signal.
# Khớp theo ranh giới từ (match_phrase).
ORG_INCIDENT_PHRASES = [
    # Tiếng Anh - ngữ cảnh NẠN NHÂN
    "breach", "breached", "hacked", "stolen", "theft", "heist",
    "drain", "drained", "siphon",
    "security incident", "unauthorized access", "unauthorized transactions",
    "ransomware attack", "outage", "service disruption",
    "suspend", "suspends", "freeze", "freezes", "froze",
    # Tiếng Việt - ngữ cảnh NẠN NHÂN
    "tấn công", "đánh cắp", "rò rỉ", "lộ lọt", "tống tiền",
    "ngừng hoạt động", "sập",
]

# Regex số tiền: 2 nhánh -
#   (a) có ký tự tiền tệ ($ € £) + số + hậu tố tùy chọn ($6.3 billion, $400M)
#   (b) không ký tự tiền tệ nhưng có đơn vị rõ (6.36 billion, 400 triệu USD)
_MONEY_RE = re.compile(
    r"([$€£])\s*(\d[\d.,]*|\d)\s*(billion|trillion|million|bn|mn|b|m|k)?\b"
    r"|(\d[\d.,]*|\d)\s*(billion|trillion|million|bn|mn|tỷ|tỉ|triệu)\b",
    re.IGNORECASE,
)

_MONEY_MULTIPLIER = {
    "trillion": 1e12, "tn": 1e12,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mn": 1e6, "m": 1e6,
    "k": 1e3,
    "tỷ": 1e9, "tỉ": 1e9, "triệu": 1e6,
}

# Regex quy mô người dùng/bản ghi: <số> <million/billion/triệu> [+ tối đa 3
# từ] <users/accounts/records/installs/downloads/citizens/...>.
# VD: "50 million Vietnamese citizens", "134 million installs",
# "2 billion weekly downloads".
_USER_SCALE_RE = re.compile(
    r"(\d[\d.,]*|\d)\s*(million|billion|triệu|tỷ|tỉ)"
    r"(?:\s+\w+){0,3}?\s+"
    r"(users?|accounts?|records?|devices?|installs?|downloads?|citizens?"
    r"|people|customers?|victims?|subscribers?|members?|households?"
    r"|người\s?dùng|bản\s?ghi|tài\s?khoản|công\s?dân|khách\s?hàng"
    r"|lượt\s?tải|lượt\s?cài|thiết\s?bị)",
    re.IGNORECASE,
)

# Cụm kỷ lục: "largest/biggest ... (hack|heist|breach|theft|attack) ...
# (in history|on record|ever)" (A3.3).
_RECORD_RE = re.compile(
    r"\b(?:largest|biggest|biggest-ever|largest-ever|record-breaking)\b"
    r"[^.!?\n]{0,60}?"
    r"\b(?:hack|hacking|hacked|heist|breach|theft|attack|scam|robbery)\b"
    r"[^.!?\n]{0,60}?"
    r"\b(?:in history|on record|ever)\b",
    re.IGNORECASE,
)


def _parse_amount(raw):
    """
    Chuyển chuỗi số tiền/người ("6.3", "6,3", "1,462", "1.462.567") -> float.
    Phân biệt dấu phân cách hàng nghìn (1,462 / 1.462.567) và dấu thập phân
    (6,3 / 6.3). Không parse được -> None.
    """
    s = str(raw or "").strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        # Dấu SAU CÙNG là dấu thập phân.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.partition(",")
        if len(tail) == 3 and 1 <= len(head) <= 3:
            s = head + tail          # hàng nghìn: 1,462
        else:
            s = head + "." + tail    # thập phân: 6,3
    elif "." in s:
        parts = s.split(".")
        if len(parts) > 2:
            s = "".join(parts)       # hàng nghìn: 1.462.567
    try:
        return float(s)
    except ValueError:
        return None


# Từ chỉ người/bản ghi xuất hiện NGAY SAU một cụm số (dùng phân biệt
# "134 million installs" (quy mô người) với "134 million USD" (tiền)).
_FOLLOW_USER_RE = re.compile(
    r"\s+(?:of\s+)?(?:\w+\s+){0,3}?"
    r"(users?|accounts?|installs?|downloads?|records?|people|citizens?"
    r"|customers?|victims?|subscribers?|members?|người|bản ghi|tài khoản|lượt)",
    re.IGNORECASE,
)


def _max_money_value(blob):
    """
    Trả về (value_usd, matched_text) của số tiền LỚN NHẤT trong blob hợp lệ
    với HARD_SIGNAL_MIN_USD (mọi số tiền được quy đổi về USD), hoặc (0, "").
    - Số tiền CHỈ được tính khi blob có từ khóa tấn công (A3.1) - hàm này đã
      kiểm tra ngữ cảnh trước khi gọi.
    - Nhánh KHÔNG có ký tự tiền tệ ($ € £) bị loại khi tiếp theo là từ chỉ
      người/bản ghi ("134 million installs" -> KHÔNG phải USD).
    """
    best_value = 0.0
    best_text = ""
    if not blob:
        return best_value, best_text
    for m in _MONEY_RE.finditer(blob):
        currency, num_a, unit_a, num_b, unit_b = m.groups()
        raw_num = num_a if num_a is not None else num_b
        unit = (unit_a or unit_b or "").lower()
        value = _parse_amount(raw_num)
        if value is None:
            continue
        if currency is None:
            # Nhánh (b): đơn vị bắt buộc phải rõ nghĩa (billion/million/...).
            if unit not in _MONEY_MULTIPLIER:
                continue
            tail = blob[m.end():m.end() + 40]
            if _FOLLOW_USER_RE.search(tail):
                continue   # "134 million installs" -> không phải USD
        multiplier = _MONEY_MULTIPLIER.get(unit, 1 if currency else 0)
        if not multiplier:
            continue
        usd = value * multiplier
        if usd > best_value:
            best_value = usd
            best_text = m.group(0).strip()
    return best_value, best_text


def detect_hard_signals(article):
    """
    A3: nhận diện "tin quy mô lớn" trên title + summary.

    Trả về (has_hard_signal: bool, reasons: list[str]) - reasons là các lý do
    tiếng Việt (mỗi lý do chứa một nhãn SPECIAL_SIGNALS nên khi thêm vào
    scored.reasons sẽ làm is_special=True - A3b):

    1. "Tổn thất lớn"    : số tiền >= HARD_SIGNAL_MIN_USD (50 triệu USD)
                           ĐI CÙNG từ khóa hack/stolen/theft/drain/heist/
                           breach/exploit/siphon trong cùng blob.
    2. "Quy mô hàng triệu": quy mô người dùng/bản ghi >= HARD_SIGNAL_MIN_USERS.
    3. "Quy mô kỷ lục"   : cụm largest/biggest ... hack/heist/... ... in
                           history/on record/ever.
    4. "Tổ chức trọng yếu" (2 TẦNG, tên org BẮT BUỘC ở trong TITLE):
       - ORGS_TIER1 (tầng 1 - tự kích hoạt): org trong TITLE + từ khóa
         SỰ CỐ NẠN NHÂN (ORG_INCIDENT_PHRASES) trong blob là đủ.
       - ORGS_TIER2 (tường 2 - KHÔNG đủ một mình): org trong TITLE CHỈ tính
         khi đi kèm tiền >= HARD_SIGNAL_MIN_USD, HOẶC quy mô người
         >= HARD_SIGNAL_MIN_USERS, HOẶC cụm kỷ lục.
         -> tin "Microsoft patch Tuesday", "CISA thêm KEV", "FBI bắt hacker",
            "Google vá zero-day", "Cisco cảnh báo tấn công" KHÔNG phải
            hard_signal (không có tiền / quy mô / kỷ lục nào đi kèm).

    KHÔNG bao giờ raise (mọi lỗi -> (False, [])).
    """
    try:
        title = getattr(article, "title", "") or ""
        summary = getattr(article, "summary", "") or ""
        blob = (title + " " + summary).lower()
        title_l = title.lower()
        if not blob.strip():
            return False, []
        reasons = []

        has_attack_ctx = match_any(blob, HARD_ATTACK_PHRASES) is not None

        # (1) Số tiền lớn + ngữ cảnh tấn công.
        has_money = False
        if has_attack_ctx:
            money_value, money_text = _max_money_value(blob)
            if money_value >= HARD_SIGNAL_MIN_USD:
                has_money = True
                reasons.append(
                    "Tổn thất lớn: %s (>= %d USD, kèm từ khóa tấn công)"
                    % (money_text or ("%.0f USD" % money_value),
                       HARD_SIGNAL_MIN_USD))

        # (2) Quy mô người dùng / bản ghi >= 1 triệu.
        has_users = False
        for m in _USER_SCALE_RE.finditer(blob):
            value = _parse_amount(m.group(1))
            if value is None:
                continue
            unit = (m.group(2) or "").lower()
            multiplier = 1e9 if unit in ("billion", "tỷ", "tỉ") else 1e6
            if value * multiplier >= HARD_SIGNAL_MIN_USERS:
                has_users = True
                reasons.append(
                    "Quy mô hàng triệu: %s (%d người/bản ghi)"
                    % (m.group(0).strip(), HARD_SIGNAL_MIN_USERS))
                break

        # (3) Cụm kỷ lục.
        has_record = False
        rec = _RECORD_RE.search(blob)
        if rec:
            has_record = True
            reasons.append("Quy mô kỷ lục: %s" % rec.group(0).strip())

        # (4) TỔ CHỨC TRỌNG YẾU - 2 TẦNG, org PHẢI Ở TRONG TITLE.
        # (chỉ xét title -> "Microsoft" chỉ xuất hiện trong summary của một
        #  bài khác không kích hoạt nhóm này).
        tier1_org = None
        for org in ORGS_TIER1:
            if match_phrase(title_l, org):
                tier1_org = org
                break
        if tier1_org is not None:
            # Tầng 1: cần thêm từ khóa sự cố NẠN NHÂN (trong title+summary).
            incident_hit = match_any(blob, ORG_INCIDENT_PHRASES)
            if incident_hit:
                reasons.append(
                    "Tổ chức trọng yếu (tầng 1) liên quan sự cố: %s (%s)"
                    % (tier1_org, incident_hit))
        else:
            # Tầng 2: org CHỈ tính khi đi kèm tiền / quy mô người / kỷ lục.
            for org in ORGS_TIER2:
                if match_phrase(title_l, org):
                    if has_money:
                        why = "tiền >= %d USD" % HARD_SIGNAL_MIN_USD
                    elif has_users:
                        why = "quy mô >= %d người" % HARD_SIGNAL_MIN_USERS
                    elif has_record:
                        why = "cụm kỷ lục"
                    else:
                        why = None
                    if why:
                        reasons.append(
                            "Tổ chức trọng yếu (tầng 2) + %s: %s"
                            % (why, org))
                    break

        return bool(reasons), reasons
    except Exception as exc:
        logger.warning("[HARD] Lỗi detect_hard_signals %r: %r",
                       getattr(article, "title", "?"), exc)
        return False, []


# =====================================================================
# DATA MODEL
# =====================================================================

@dataclass
class ScoredArticle:
    """Kết quả phân tích một Article."""
    article: object                       # collector.Article
    score: int = 0                        # 0-100
    topic: str = "OTHER"                  # IT / CYBERSECURITY / CYBERCRIME / OTHER
    is_vietnam: bool = False
    is_special: bool = False              # có yếu tố đặc biệt (Section 26)
    tags: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)   # vì sao được điểm đó
    penalties: List[str] = field(default_factory=list) # tín hiệu tiêu cực gặp phải
    fingerprint: str = ""                 # dùng dedup giữa các lần chạy
    # --- TÍN HIỆM CỨNG (A3) - "tin quy mô lớn" do detect_hard_signals gán ---
    hard_signal: bool = False             # bài CÓ tín hiệu cứng (mặc định False)
    hard_signal_reasons: List[str] = field(default_factory=list)
    # --- TIN NÓNG (hot.py ghi vào; giá trị mặc định khi chưa annotate/cấp slot) ---
    corroboration: int = 0                # số nguồn RSS KHÁC NHAU cùng đưa sự kiện
    slot: str = "none"                    # "hot" | "normal" | "none" (chưa cấp)
    is_hot: bool = False                  # bài có phải tin nóng không

    @property
    def title(self):
        return self.article.title

    @property
    def link(self):
        return self.article.link

    @property
    def source(self):
        return self.article.source


# =====================================================================
# 2. NHẬN DIỆN VIỆT NAM
# =====================================================================

VIETNAM_PHRASES = [
    # Địa danh / cơ quan / công ty Việt Nam
    # (bản có DẤU và không dấu - bài tiếng Việt hay viết "Việt Nam")
    "vietnam", "viet nam", "việt nam", "vietnamese", "viet-nam", "việt-nam",
    "công an", "bo cong an", "bocongan", "cơ quan điều tra",
    "khởi tố", "triệt phá", "tội phạm mạng", "an ninh mang", "an ninh mạng",
    "bộ quốc phòng", "bộ thông tin", "vncert", "vincert",
    "fpt", "viettel", "vnpt", "mobifone", "vinaphone", "cmc", "onepay",
    "ngan hang", "ngân hàng", "bac a bank", "techcombank", "vietcombank",
    "vpbank", "mbbank", "tpbank", "acb bank",
    "ha noi", "hà nội", "hanoi", "ho chi minh", "hồ chí minh", "saigon",
    "da nang", "đà nang", "can tho", "cần thơ", "hai phong", "hải phòng",
    "binh duong", "bình dương", "dong nai", "đồng nai", "lai chau", "lai châu",
    # Tên đệm / cách gọi riêng
    "vinfast", "vingroup", "sun group", "thanh nien", "tuoi tre",
    "vnexpress", "cafebiz", "ictvietnam", "congan", "antoan",
    ".vn", "vietnamnet", "dan tri", "nguoi lao dong",
]

# Token có thể gặp trong URL domain .vn -> dùng làm tín hiệu phụ.
VN_URL_HINTS = [".vn/", ".vn?", ".vn#", ".com.vn", ".net.vn", ".org.vn", ".edu.vn"]


def detect_vietnam(article):
    """
    Phát hiện bài có liên quan Việt Nam hay không.
    Kết hợp: title + summary (lowercase) + URL.
    Trả về True/False.
    """
    try:
        blob = article.text_blob()
        url = (article.link or "").lower()
        if match_any(blob, VIETNAM_PHRASES):
            return True
        for hint in VN_URL_HINTS:
            if hint in url:
                return True
    except Exception:
        return False
    return False


# =====================================================================
# 3. PHÂN LOẠI CHỦ ĐỀ
# =====================================================================

def classify_topic(article, has_hard_signal=False):
    """
    Phân loại article vào IT / CYBERSECURITY / CYBERCRIME / OTHER.
    Trả về: (topic: str, score_base: int, matched: List[str])

    E1: so khớp theo RANH GIỚI TỪ (match_phrase) thay vì `phrase in text`,
    tránh false positive kiểu "apt" match "adapting", "cve" match "cvein"...

    A2: bài CHỈ có từ yếu (hacker/breach/leak...) nhưng CÓ "tín hiệu cứng"
    (detect_hard_signals -> has_hard_signal=True) KHÔNG được trả về OTHER:
    -> tin như "Telegram bot leaks personal data of 50 million citizens"
       hay "Hackers breached the US Treasury" không chết trước khi AI thấy.
    """
    title = (article.title or "").lower()
    summary = (article.summary or "").lower()
    # Title có trọng số cao hơn summary.
    title_hits = {}
    summary_hits = {}

    for topic, phrases in TOPIC_PHRASES.items():
        for phrase in phrases:
            if match_phrase(title, phrase):
                title_hits.setdefault(topic, []).append(phrase)
            elif match_phrase(summary, phrase):
                summary_hits.setdefault(topic, []).append(phrase)

    # Điểm cơ sở theo số hit (title nặng hơn summary), có trần.
    totals = {}
    for topic in TOPIC_PHRASES:
        t = min(len(title_hits.get(topic, [])) * 4, 20)
        s = min(len(summary_hits.get(topic, [])) * 2, 10)
        totals[topic] = t + s

    # Bài chỉ có từ khóa YẾU (hacker/hacked/attack...) KHÔNG được
    # tự động xếp vào CYBERSECURITY/CYBERCRIME.
    # (Biến này không dùng để quyết định kết quả - dòng dưới giữ nguyên
    #  logic; đổi sang match_phrase chỉ để nhất quán kiểu khớp.)
    weak_in_title = [w for w in WEAK_PHRASES if match_phrase(title, w)]
    strong_topic = max(totals, key=lambda k: totals[k]) if totals else "OTHER"
    strong_score = totals.get(strong_topic, 0)

    base_by_topic = {
        "CYBERSECURITY": 30,
        "CYBERCRIME": 30,
        "IT": 15,
    }

    if strong_score == 0:
        if has_hard_signal:
            # A2: không có hit từ khóa nào NHƯNG có tín hiệu cứng ->
            # vẫn là bài cần xét (gán CYBERSECURITY, điểm cơ sở cao gần
            # bằng bài có từ khóa mạnh - hard signal đã là bằng chứng lớn).
            return "CYBERSECURITY", 25, []
        # Không có từ khóa đủ mạnh -> OTHER, điểm cơ sở thấp.
        return "OTHER", 0, []

    # Nếu CHỈ có weak phrases và tổng hit rất thấp -> coi như không đủ mạnh.
    matched = title_hits.get(strong_topic, []) + summary_hits.get(strong_topic, [])
    if strong_score < 6 and not [m for m in matched if m not in WEAK_PHRASES]:
        if has_hard_signal:
            # A2: chỉ có từ yếu nhưng CÓ tín hiệu cứng -> không loại OTHER,
            # cho điểm cơ sở đầy đủ theo chủ đề (tín hiệu cứng cộng thêm
            # ở scoring) -> vụ việc đủ lớn đạt ngưỡng đăng cả khi AI tắt.
            return strong_topic, base_by_topic.get(strong_topic, 25), matched
        return "OTHER", 0, matched

    base = base_by_topic.get(strong_topic, 0)

    # Trộn chủ đề: nếu 2 chủ đề đều có hit đáng kể -> cộng thêm.
    secondary = [tp for tp in totals
                 if tp != strong_topic and totals[tp] >= 8]
    if secondary:
        base += 5

    _ = weak_in_title  # đã dùng để kiểm tra ở trên
    return strong_topic, base, matched


# =====================================================================
# 4. SCORING CHÍNH
# =====================================================================

def score_article(article, now=None):
    """
    Chấm điểm một Article theo thang 0-100.

    Công thức:
      base (theo chủ đề)
      + điểm các tín hiệu mạnh (POSITIVE_SIGNALS)
      + điểm tin cậy nguồn (SOURCE_TRUST_SCORE)
      + điểm ưu tiên Việt Nam (nếu có tín hiệu mạnh)
      - điểm tiêu cực (NEGATIVE_SIGNALS / spam / clickbait)
      + bonus mới mẻ (bài mới hơn)
    Cắt về [0, 100].
    """
    ref = now or now_vn()
    scored = ScoredArticle(article=article)

    # Loại nguồn (D1/5). Nguồn kind "vn" -> LUÔN coi là bài Việt Nam
    # (gán TRƯỚC mọi nhánh return sớm -> bài bị cổng vẫn giữ cờ is_vietnam).
    src_kind = SOURCE_KIND.get(article.source, "security")
    scored.is_vietnam = (src_kind == "vn")

    title = (article.title or "").lower()
    summary = (article.summary or "").lower()
    blob = title + " " + summary

    # E2: tập SPECIAL_SIGNALS kích hoạt TRỰC TIẾP trong lúc chấm điểm,
    # tích lũy khi append từng lý do (KHÔNG dò chuỗi reasons_joined).
    specials_hit = set()

    # --- 4.1. LOẠI cứng: spam / quảng cáo / clickbait trong TITLE ---
    spam_hit = match_any(title, SPAM_PHRASES)
    if spam_hit:
        logger.info("[FILTER] Loại (spam/PR) '%s': %s", spam_hit, article.title[:80])
        scored.score = 0
        scored.penalties.append("Spam/PR trong title: %s" % spam_hit)
        scored.fingerprint = make_fingerprint(article)
        return scored

    clickbait_hit = match_any(title, CLICKBAIT_PHRASES)
    if clickbait_hit:
        logger.info("[FILTER] Loại (clickbait) '%s': %s", clickbait_hit, article.title[:80])
        scored.score = 0
        scored.penalties.append("Clickbait: %s" % clickbait_hit)
        scored.fingerprint = make_fingerprint(article)
        return scored

    # --- 4.1b. TÍN HIỆN CỨNG (A3) - tính TRƯỚC khi phân loại ---
    hard, hard_reasons = detect_hard_signals(article)
    scored.hard_signal = hard
    scored.hard_signal_reasons = list(hard_reasons)

    # --- 4.1c. CỔNG NGUỒN CRYPTO/TỔNG HỢP/VIỆT NAM (D1 + item 5) ---
    # Nguồn crypto / tổng hợp / báo VN CHỈ vào pipeline khi có hard_signal
    # HOẶC có từ khóa tấn công/sự cố rõ ràng (không bị ngập tin giá coin,
    # tin thị trường, tin đời sống/thương mại của báo VN).
    if src_kind in SOURCE_REQUIRES_HARD_SIGNAL and not hard:
        if match_any(blob, HARD_ATTACK_PHRASES) is None:
            logger.info("[FILTER] Loại (nguồn %s, không có hard_signal/từ khóa "
                        "tấn công): %s", src_kind, article.title[:80])
            scored.score = 0
            scored.penalties.append(
                "Nguồn %s: không có hard_signal hay từ khóa tấn công"
                % src_kind)
            scored.fingerprint = make_fingerprint(article)
            return scored

    # --- 4.2. Phân loại chủ đề ---
    topic, base, matched = classify_topic(article, has_hard_signal=hard)
    scored.topic = topic

    if topic == "OTHER":
        # Không liên quan IT/security/cybercrime -> KHÔNG ĐĂNG.
        # Ví dụ: "iPhone mới ra mắt" -> OTHER -> loại.
        logger.info("[FILTER] Loại (không liên quan IT/security): %s",
                    article.title[:80])
        scored.score = 0
        scored.penalties.append("Không liên quan IT/security/cybercrime")
        scored.fingerprint = make_fingerprint(article)
        return scored

    scored.score = base
    topic_reason = "Chủ đề %s (cơ sở +%d)" % (topic, base)
    scored.reasons.append(topic_reason)
    specials_hit |= _specials_in_text(topic_reason)

    # --- 4.3. Tín hiệu mạnh (tăng điểm) ---
    for phrase, points, reason in POSITIVE_SIGNALS:
        if match_phrase(blob, phrase):
            scored.score += points
            scored.reasons.append("%s (+%d)" % (reason, points))
            specials_hit |= SPECIALS_FROM_SIGNAL.get(reason, set())

    # --- 4.3b. TÍN HIỆN CỨNG (A3b): +25 và luôn làm is_special=True ---
    if scored.hard_signal:
        scored.score += HARD_SIGNAL_POINTS
        scored.reasons.append("Tín hiệu cứng (quy mô lớn) (+%d)"
                              % HARD_SIGNAL_POINTS)
        for reason in scored.hard_signal_reasons:
            scored.reasons.append(reason)
            specials_hit |= _specials_in_text(reason)

    # --- 4.4. Tin cậy nguồn ---
    trust = SOURCE_TRUST_SCORE.get(article.source, SOURCE_TRUST_SCORE.get("Unknown", 5))
    scored.score += trust
    source_reason = "Nguồn %s (+%d)" % (article.source, trust)
    scored.reasons.append(source_reason)
    specials_hit |= _specials_in_text(source_reason)

    # --- 4.5. Bonus mới mẻ ---
    age = getattr(article, "age_hours", None)
    if age is not None:
        if age <= 3:
            scored.score += 6
            fresh_reason = "Rất mới <=3h (+6)"
            scored.reasons.append(fresh_reason)
            specials_hit |= _specials_in_text(fresh_reason)
        elif age <= 12:
            scored.score += 3
            fresh_reason = "Mới <=12h (+3)"
            scored.reasons.append(fresh_reason)
            specials_hit |= _specials_in_text(fresh_reason)

    # --- 4.6. Ưu tiên Việt Nam ---
    # Nguồn kind "vn" đã gán is_vietnam=True từ đầu (item 5); detect_vietnam
    # chỉ BỔ SUNG cho nguồn khác viết về Việt Nam (KHÔNG ghi đè về False).
    if not scored.is_vietnam and detect_vietnam(article):
        scored.is_vietnam = True
    if scored.is_vietnam:
        # Chỉ cộng điểm nếu bài ĐÃ có tín hiệu mạnh (score >= 40),
        # tránh đăng mọi tin Việt Nam chỉ vì nó ở Việt Nam.
        if scored.score >= 40:
            scored.score += 12
            vn_reason = "Liên quan Việt Nam (+12)"
            scored.reasons.append(vn_reason)
            specials_hit |= _specials_in_text(vn_reason)
        else:
            vn_reason = "Việt Nam nhưng chưa đủ tín hiệu mạnh -> không cộng điểm"
            scored.reasons.append(vn_reason)
            specials_hit |= _specials_in_text(vn_reason)

    # --- 4.7. Tín hiệu tiêu cực (A4) ---
    # KHÔNG áp penalty nào lên bài có hard_signal (tin quy mô lớn không bị
    # trừ oan do từ mơ hồ); từ mơ hồ (NEGATIVE_TITLE_ONLY) CHỈ so với TITLE.
    if not scored.hard_signal:
        for phrase, points, reason in NEGATIVE_SIGNALS:
            if match_phrase(blob, phrase):
                scored.score += points  # points âm
                scored.penalties.append("%s (%d)" % (reason, points))
        for phrase, points, reason in NEGATIVE_TITLE_ONLY:
            if match_phrase(title, phrase):
                scored.score += points  # points âm
                scored.penalties.append("%s (%d) (title)" % (reason, points))

        # Bài có chữ "hacker" nhưng KHÔNG có tín hiệu bảo mật nào khác -> trừ mạnh.
        # (Không đăng bài chỉ vì có chữ "hacker".)
        hacker_only = (
            match_phrase(title, "hacker")
            or match_phrase(title, "hackers")
            or match_phrase(title, "hacking")
            or match_phrase(title, "hacked")
        )
        has_security_signal = any(
            match_phrase(blob, p) for p, _, _ in POSITIVE_SIGNALS
        )
        if hacker_only and not has_security_signal:
            scored.score -= 25
            scored.penalties.append(
                "Chỉ có chữ 'hacker', không có tín hiệu security khác (-25)")

    # --- 4.8. Cắt về [0, 100] ---
    if scored.score < 0:
        scored.score = 0
    if scored.score > 100:
        scored.score = 100

    # --- 4.9. Yếu tố đặc biệt (Section 26) ---
    # E2: xác định TRỰC TIẾP từ specials_hit (tập SPECIAL_SIGNALS đã được
    # kích hoạt khi append từng lý do), KHÔNG còn dò chuỗi reasons_joined.
    scored.is_special = bool(specials_hit)

    # --- 4.10. Tags + fingerprint ---
    scored.tags = build_tags(article, scored.topic, scored.is_vietnam, blob)
    scored.fingerprint = make_fingerprint(article)

    logger.info("[SCORE] %d | %s | %s | VN=%s | special=%s | hard=%s | %s",
                scored.score, scored.topic, article.source,
                scored.is_vietnam, scored.is_special, scored.hard_signal,
                article.title[:80])

    return scored


def analyze_articles(articles, now=None):
    """
    Chấm điểm toàn bộ danh sách Article.
    Trả về List[ScoredArticle] đã sort theo điểm giảm dần.
    """
    ref = now or now_vn()
    results = []
    for article in articles:
        try:
            scored = score_article(article, ref)
            results.append(scored)
        except Exception as exc:
            logger.warning("[SCORE] Lỗi chấm điểm %r: %r",
                           getattr(article, "title", "?"), exc)
            continue

    results.sort(key=lambda s: s.score, reverse=True)
    logger.info("[SCORE] Đã chấm %d bài. Cao nhất: %d",
                len(results), results[0].score if results else 0)
    return results


# =====================================================================
# 5. BUILD TAGS
# =====================================================================

def build_tags(article, topic, is_vietnam, blob=None):
    """
    Chọn tag Forum phù hợp (tối đa 3 tag, đúng thứ tự hiển thị đẹp).

    Quy tắc theo yêu cầu:
    - Tin Việt Nam  -> 🇻🇳 Việt Nam
    - Tin quốc tế  -> 🌎 Quốc tế
    - Có tín hiệu security/cybercrime -> 🔐 An ninh mạng
    - Cybercrime (bắt bớ, lừa đảo, ransomware group) -> 🚨 Tội phạm mạng
    - IT thuần túy (hạ tầng, cloud, server, network) -> 💻 Công nghệ
    - Malware -> 🦠 Malware
    - Vulnerability / zero-day / exploit -> 🔓 Lỗ hổng
    - Data breach / leak -> 💾 Rò rỉ dữ liệu
    """
    if blob is None:
        blob = ((article.title or "") + " " + (article.summary or "")).lower()

    tags = []

    # (a) Địa lý
    if is_vietnam:
        tags.append("🇻🇳 Việt Nam")
    else:
        tags.append("🌎 Quốc tế")

    # (b) Malware / Lỗ hổng / Rò rỉ dữ liệu (ưu tiên cụ thể trước chung)
    malware_keywords = [
        "malware", "ransomware", "trojan", "botnet", "worm", "rootkit",
        "keylogger", "stealer", "infostealer", "cobalt strike", "backdoor",
        "lockbit", "blackcat", "alphv", "cl0p", "clop", "lazarus",
    ]
    vuln_keywords = [
        "cve-", "zero-day", "zero day", "zeroday", "0-day", "vulnerability",
        "vulnerable", "exploit", "remote code execution", "rce",
        "privilege escalation", "security flaw", "patch",
    ]
    leak_keywords = [
        "data breach", "data leak", "leaked data", "stolen data",
        "exposed database", "exposed", "data theft", "rò rỉ dữ liệu",
    ]
    cybercrime_keywords = [
        "arrested", "charged", "indicted", "sentenced", "takedown",
        "taken down", "dismantled", "law enforcement", "fbi", "doj",
        "europol", "interpol", "cybercrime", "fraud", "scam", "extortion",
        "dark web", "darknet", "triệt phá", "công an", "khởi tố",
        "tội phạm mạng", "crime ring", "scam network",
    ]
    tech_keywords = [
        "cloud", "server", "datacenter", "data center", "network",
        "database", "internet", "outage", "downtime", "firmware",
        "operating system", "windows", "linux", "router", "firewall",
        "aws", "azure", "kubernetes", "broadband", "dns", "bgp",
    ]

    if match_any(blob, malware_keywords):
        tags.append("🦠 Malware")
    elif match_any(blob, vuln_keywords):
        tags.append("🔓 Lỗ hổng")
    elif match_any(blob, leak_keywords):
        tags.append("💾 Rò rỉ dữ liệu")
    elif topic == "CYBERCRIME" or match_any(blob, cybercrime_keywords):
        tags.append("🚨 Tội phạm mạng")
    elif topic == "IT" or match_any(blob, tech_keywords):
        tags.append("💻 Công nghệ")

    # (c) Tag "chung" cuối cùng: 🔐 An ninh mạng nếu là chủ đề security/cybercrime.
    if topic in ("CYBERSECURITY", "CYBERCRIME") and "🔐 An ninh mạng" not in tags:
        tags.append("🔐 An ninh mạng")

    # Giới hạn 3 tag và chỉ giữ tag có trong FORUM_TAG_NAMES.
    valid = [t for t in tags if t in FORUM_TAG_NAMES]
    # Bỏ tag "🌎 Quốc tế" nếu đã có tag địa lý khác (chỉ giữ 1 tag địa lý).
    result = []
    for t in valid:
        if t not in result:
            result.append(t)
    result = result[:3]

    if not result:
        result = list(DEFAULT_TAG_NAMES)

    return result


# =====================================================================
# 6. FINGERPRINT (DEDUP GIỮA CÁC LẦN CHẠY)
# =====================================================================

def make_fingerprint(article):
    """
    Tạo fingerprint ổn định cho một article, dùng dedup giữa các lần chạy.

    Fingerprint dựa trên: normalized title + entity chính (KHÔNG dựa hoàn toàn
    vào URL, vì hai nguồn khác nhau sẽ có URL khác nhau cho cùng sự kiện).

    Trả về: chuỗi hex 16 ký tự.
    """
    norm = normalize_title(article.title or "")
    # Lấy các entity chính để fingerprint bền hơn với cách viết title khác nhau.
    from textutils import extract_named_entities
    ents = sorted(extract_named_entities(article.title or ""))
    raw = norm + "||" + "|".join(ents)
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


# =====================================================================
# 7. CHỌN BÀI ĐỂ ĐĂNG (dedup + quota + threshold)
# =====================================================================

def is_duplicate_of_any(scored, accepted):
    """
    Kiểm tra scored có trùng sự kiện với bài đã accepted trong lần chạy này không.
    Dùng cả URL, title similarity và entity overlap.
    """
    for prev in accepted:
        # Cùng URL.
        if scored.link and scored.link == prev.link:
            logger.info("[DEDUP] Trùng URL: %s", scored.title[:80])
            return True
        # Cùng fingerprint.
        if scored.fingerprint and scored.fingerprint == prev.fingerprint:
            logger.info("[DEDUP] Trùng fingerprint: %s", scored.title[:80])
            return True
        # Cùng sự kiện (title similarity hoặc entity overlap lớn).
        # Truyền thêm summary để so mức rộng hơn (bắt case cùng vụ việc
        # khác nhau về cách viết giữa các nguồn).
        prev_summary = getattr(prev.article, "summary", "")
        cur_summary = getattr(scored.article, "summary", "")
        if is_same_event(scored.title, prev.title, cur_summary, prev_summary):
            logger.info("[DEDUP] Cùng sự kiện (title similarity): %s ~ %s",
                        scored.title[:60], prev.title[:60])
            return True
        if entity_overlap(scored.title, prev.title) >= 2 and \
                title_similarity(scored.title, prev.title) >= 0.45:
            logger.info("[DEDUP] Cùng sự kiện (entity overlap): %s ~ %s",
                        scored.title[:60], prev.title[:60])
            return True
    return False


def is_already_posted(scored, state):
    """
    Kiểm tra scored đã từng được đăng trong các lần chạy trước không
    (dựa trên state đã lưu: posted_links, posted_fingerprints, posted_titles).
    """
    try:
        if state is None:
            return False
        if state.is_posted_link(scored.link):
            logger.info("[DEDUP] Đã đăng rồi (link): %s", scored.title[:80])
            return True
        if state.is_posted_fingerprint(scored.fingerprint):
            logger.info("[DEDUP] Đã đăng rồi (fingerprint): %s", scored.title[:80])
            return True
        if state.is_posted_title(scored.title):
            logger.info("[DEDUP] Đã đăng rồi (title): %s", scored.title[:80])
            return True
        return False
    except Exception as exc:
        # FAIL-CLOSED (A4): không kiểm tra được state -> coi như ĐÃ ĐĂNG,
        # KHÔNG đăng lại (trước đây return False = fail-open).
        logger.warning("[DEDUP] Lỗi kiểm tra state (%r) -> coi như ĐÃ ĐĂNG, "
                       "không đăng lại.", exc)
        return True


def meets_threshold(scored):
    """
    Kiểm tra một bài có đủ điều kiện đăng theo ngưỡng không.

    - score >= MIN_SCORE_TO_POST (85): ĐƯỢC đăng.
    - SPECIAL_MIN_SCORE (70) <= score < 85: CHỈ được đăng nếu is_special
      (Section 26 — không phải cách lách threshold).
    - score < 70: KHÔNG đăng.
    """
    if scored.score >= MIN_SCORE_TO_POST:
        return True
    if scored.score >= SPECIAL_MIN_SCORE and scored.is_special:
        return True
    return False


def _ai_rescue_ok(scored):
    """
    Bài CHƯA đạt ngưỡng rule có được "cứu" nhờ AI không. True khi TẤT CẢ:
    - Có .ai_decision thật (AI đã đánh giá; AI lỗi/tắt -> không có -> False).
    - decision.publish == True và decision.score >= AI_RESCUE_MIN_SCORE (80).
    - điểm rule scored.score >= AI_CANDIDATE_MIN_SCORE (45) - dưới 45 không cứu.
      (Ngoại lệ B3: bài hard_signal KHÔNG bị chặn bởi ngưỡng 45 - AI chấm
       >= 80 là đủ để đăng ở slot thường khi slot nóng đã hết.)
    """
    decision = getattr(scored, "ai_decision", None)
    if decision is None:
        return False
    if not getattr(decision, "publish", False):
        return False
    if int(getattr(decision, "score", 0) or 0) < AI_RESCUE_MIN_SCORE:
        return False
    if scored.score < AI_CANDIDATE_MIN_SCORE and not getattr(
            scored, "hard_signal", False):
        return False
    return True


def select_articles(scored_list, state, now=None):
    """
    Chọn tối đa MAX_POSTS_PER_DAY bài để đăng.

    Lọc theo thứ tự:
    1. meets_threshold (85, hoặc 70-84 với yếu tố đặc biệt) - HOẶC được
       AI cứu (_ai_rescue_ok: publish=true + AI >= 85 + rule >= 45).
    2. Chưa từng đăng (state).
    3. Không trùng sự kiện với bài đã chọn trong lần này.
    4. Còn quota bài trong ngày (theo VN timezone).

    Trả về: List[ScoredArticle] cần đăng (tối đa MAX_POSTS_PER_DAY).
    """
    ref = now or now_vn()
    day = today_key(ref)

    try:
        posted_today = state.posts_today(day) if state is not None else 0
    except Exception as exc:
        logger.warning("[STATE] Lỗi đọc posts_today: %r", exc)
        posted_today = 0

    remaining = max(0, MAX_POSTS_PER_DAY - posted_today)
    logger.info("[FILTER] Ngày %s: đã đăng %d/%d, còn quota %d",
                day, posted_today, MAX_POSTS_PER_DAY, remaining)

    accepted = []
    for scored in scored_list:
        if remaining <= 0:
            break

        # 1. Ngưỡng điểm (hoặc bài được AI cứu).
        if not meets_threshold(scored):
            if _ai_rescue_ok(scored):
                decision = getattr(scored, "ai_decision", None)
                logger.info(
                    "[FILTER] AI_RESCUE: %s rule=%d ai=%d",
                    scored.title[:80], scored.score,
                    int(getattr(decision, "score", 0) or 0))
            else:
                logger.debug(
                    "[FILTER] Bỏ (rule %d, chưa đạt ngưỡng và AI không cứu): %s",
                    scored.score, scored.title[:80])
                continue

        # 2. Đã đăng chưa?
        if is_already_posted(scored, state):
            continue

        # 3. Trùng với bài vừa chọn?
        if is_duplicate_of_any(scored, accepted):
            continue

        # Hợp lệ -> nhận.
        accepted.append(scored)
        remaining -= 1
        logger.info("[FILTER] CHỌN đăng (%d/%d): [%d] %s",
                    len(accepted), MAX_POSTS_PER_DAY,
                    scored.score, scored.title[:80])

    if not accepted:
        logger.info("[FILTER] Không có bài nào đạt yêu cầu hôm nay.")

    return accepted