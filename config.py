# -*- coding: utf-8 -*-
"""
config.py - Cấu hình chung cho Discord Cybersecurity News Bot.

QUAN TRỌNG:
- File này KHÔNG chứa Discord Bot Token.
- Token được đọc từ environment variable DISCORD_BOT_TOKEN
  (GitHub Secrets cung cấp trong CI, hoặc bạn tự set khi chạy local).
"""

import os

# =====================================================================
# 1. DISCORD
# =====================================================================

# Tên environment variable chứa Discord Bot Token.
DISCORD_BOT_TOKEN_ENV = "DISCORD_BOT_TOKEN"

# ID của Discord Forum Channel (phải là Forum Channel, không phải text channel).
DISCORD_CHANNEL_ID = 1563057931333799977

# Chế độ thử (không đăng thật, chỉ in ra log). Đặt "1" vào env DRY_RUN khi debug.
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

# =====================================================================
# 2. NGƯỠNG ĐĂNG BÀI
# =====================================================================

# Điểm tối thiểu để một bài được đăng.
MIN_SCORE_TO_POST = 80

# Bài trong khoảng [SPECIAL_MIN_SCORE, MIN_SCORE_TO_POST) chỉ được đăng
# nếu có "yếu tố đặc biệt" (xem analyzer.py - SPECIAL_SIGNALS).
SPECIAL_MIN_SCORE = 70

# Điểm rule tối thiểu để một bài được GỬI CHO AI (nhánh quota THƯỜNG).
# Bài 45-84 chưa đạt ngưỡng đăng vẫn được AI xem; bài < 45 không gửi AI.
AI_CANDIDATE_MIN_SCORE = 45

# Bài rule < ngưỡng đăng (85 / special) CHỈ được đăng nếu AI thật sự chấm
# điểm >= mức này (và các điều kiện khác vẫn đạt). AI tắt/lỗi -> không được cứu.
AI_RESCUE_MIN_SCORE = 80

# Tối đa số bài TIN THƯỜNG mỗi ngày (theo múi giờ Việt Nam).
# = số khung giờ (sáng / trưa / chiều) x MAX_POSTS_PER_SLOT.
MAX_POSTS_PER_DAY = 3

# Tối đa số bài TIN THƯỜNG trong MỖI khung giờ: mỗi khung chỉ đăng bài hot
# nhất (điểm cao nhất) 1 bài. Khung nào chưa có bài đạt ngưỡng thì bỏ qua,
# KHÔNG dồn sang khung sau (không đăng bù).
MAX_POSTS_PER_SLOT = 1

# Các khung giờ đăng tin THƯỜNG theo giờ Việt Nam: (tên, giờ bắt đầu, giờ kết thúc).
# Khung là [bắt đầu, kết thúc) - ví dụ ("sáng", 6, 11) = từ 06:00 tới 10:59.
# Trong cùng một khung, các lần chạy sau bài đã đăng sẽ không đăng thêm bài
# thường; nếu lần chạy đầu chưa có bài tốt thì lần chạy sau trong khung vẫn
# được thử lại. Ngoài các khung (21:00-05:59) bot chỉ xét TIN NÓNG.
# Workflow chạy MỖI GIỜ (cron "17 * * * *" UTC = phút 17 mỗi giờ -> 07:17,
# 08:17... 23:17, 00:17... giờ VN) -> sáng: 07:17+08:17+09:17+10:17 (bài hot
# nhất của khung), trưa 11-14h, chiều 15-20h tương tự. Các lần chạy 22h-05h
# chỉ xét tin nóng (ngoài khung).
NORMAL_POST_SLOTS = (
    ("sáng", 6, 11),
    ("trưa", 11, 15),
    ("chiều", 15, 21),
)

# =====================================================================
# 2b. TIN NÓNG (HOT NEWS) - quota RIÊNG, không đụng MAX_POSTS_PER_DAY
# =====================================================================

# Tối đa bài TIN NÓNG mỗi ngày (tổng tối đa = 3 thường + 3 nóng).
# (Đã tăng từ 2 -> 3 theo yêu cầu D3 để sự kiện cực lớn không bị kẹt slot.)
MAX_HOT_POSTS_PER_DAY = 3

# Bật/tắt hẳn tính năng tin nóng. "0" / "false" / "off" / "no" = tắt.
# Chuỗi rỗng (secret chưa set trên GitHub Actions) -> vẫn BẬT (mặc định "1").
HOT_ENABLED = os.environ.get("HOT_ENABLED", "1").strip().lower() not in (
    "0", "false", "off", "no")

# Ngưỡng ĐIỂM rule cho tin nóng (cao hơn tin thường: 85 -> 95).
HOT_MIN_SCORE = 95

# Bài cũ hơn số giờ này KHÔNG được coi là tin nóng.
HOT_MAX_AGE_HOURS = 12

# Số NGUỒN RSS KHÁC NHAU cùng đưa một sự kiện (tính cả nguồn của chính bài đó).
HOT_MIN_SOURCES_INTL = 2   # sự kiện quốc tế cần >= 2 nguồn
HOT_MIN_SOURCES_VN = 1     # sự kiện Việt Nam cần >= 1 nguồn

# Khi KHÔNG dùng AI mà vẫn cho đăng tin nóng (HOT_ALLOW_WITHOUT_AI=1):
# bài bắt buộc có tối thiểu số nguồn độc lập này.
# (Trước đây hằng này nằm trong hot.py - chuyển về config để chung một chỗ.)
HOT_MIN_SOURCES_WITHOUT_AI = 3

# Điểm tối thiểu do AI chấm để một bài được coi là tin nóng
# (đã hạ 90 -> 85 theo yêu cầu B3 - 90 quá khắt khe, chưa bài nào đạt).
HOT_AI_MIN_SCORE = 85

# AI tắt/lỗi thì KHÔNG đăng tin nóng (mặc định "0").
# Đặt "1" -> khi không có AI, bài nóng phải có >= 3 nguồn độc lập mới đăng.
HOT_ALLOW_WITHOUT_AI = os.environ.get(
    "HOT_ALLOW_WITHOUT_AI", "0").strip().lower() in ("1", "true", "yes", "on")

# =====================================================================
# 2c. TÍN HIỆU CỨNG (HARD SIGNAL) - "tin quy mô lớn" (A3 / D1)
# =====================================================================

# Số tiền tối thiểu (USD) để một số tiền trong title+summary được coi là
# "tổn thất lớn" - CHỈ tính khi đi cùng từ khóa tấn công (hack/stolen/theft/
# drain/heist/breach/exploit/siphon) trong cùng chuỗi.
HARD_SIGNAL_MIN_USD = 50_000_000

# Quy mô người dùng/bản ghi tối thiểu (đơn vị: người) để được coi là
# "ảnh hưởng hàng triệu" (vd "50 million users", "134 million installs").
HARD_SIGNAL_MIN_USERS = 1_000_000

# TTL (giờ) chặn lại bài hard_signal bị AI LOẠI: 6 giờ (thay vì 36 giờ của
# bài thường) -> tin đang diễn biến (số tiền cập nhật) được xét lại sớm.
# Lý do B4.
AI_REJECT_TTL_HOURS_HARD = 6

# Số bài hard_signal TỐI ĐA gửi AI trong MỘT lần chạy (sắp theo điểm rule
# giảm dần). Bài hard vượt giới hạn KHÔNG bị ghi vào ai_rejected -> lần chạy
# sau vẫn được xét lại (không bao giờ "đánh mất" tin lớn vì hết suất AI).
HARD_MAX_CANDIDATES = 8

# Danh sách TỔ CHỨC TRỌNG YẾU chia 2 TẦNG (nhóm 4 của detect_hard_signals,
# lowercase, khớp theo ranh giới từ, và BẮT BUỘC xuất hiện trong TITLE):
#
# ORGS_TIER1 (TỰ KÍCH HOẠC): org + từ khóa SỰ CỐ NẠN NHÂN là đủ.
#   Sàn crypto, ví/hạ tầng crypto, cloud/CDN lớn, ngân hàng, chính phủ
#   (khi là nạn nhân), bệnh viện, viễn thông.
# ORGS_TIER2 (KHÔNG BAO GIỜ đủ một mình): chỉ tính khi ĐI KÈM
#   tiền >= HARD_SIGNAL_MIN_USD, HOẶC quy mô người >= HARD_SIGNAL_MIN_USERS,
#   HOẶC cụm kỷ lục (largest ... in history/on record/ever).
#   Hãng công nghệ lớn, cơ quan điều tra/phòng thủ, tên chung chung.
#   -> tin thường ngày "Microsoft patch Tuesday", "CISA thêm KEV",
#      "FBI bắt hacker", "Google vá zero-day", "Cisco cảnh báo tấn công"
#      KHÔNG THỂ thành hard_signal chỉ vì có tên org trong title.
ORGS_TIER1 = [
    # Crypto (sàn / ví / hạ tầng crypto lớn)
    "binance", "coinbase", "kraken", "bybit", "okx", "bitfinex",
    "gemini", "crypto.com", "tether", "ripple", "metamask",
    "uniswap", "opensea",
    # Cloud / CDN / hạ tầng lớn (khách hàng của họ bị ảnh hưởng hàng loạt)
    "aws", "amazon web services", "azure", "cloudflare",
    "akamai", "digitalocean",
    # Chính phủ / cơ quan nhà nước (khi là NẠN NHÂN sự cố)
    "white house", "pentagon", "state department", "treasury",
    "u.s. treasury", "us treasury", "homeland security",
    "department of homeland security", "european commission",
    "united nations", "bộ công an", "bộ quốc phòng", "bộ thông tin",
    "bộ tài chính", "chính phủ",
    # Ngân hàng lớn
    "bank of america", "jpmorgan", "jpmorgan chase", "citibank", "hsbc",
    "barclays", "deutsche bank", "goldman sachs", "wells fargo",
    "techcombank", "vietcombank", "vpbank", "mbbank", "tpbank",
    "bidv", "vietinbank", "sacombank", "bac a bank",
    # Bệnh viện (tên cụ thể / tiếng Việt) + viễn thông
    "mayo clinic", "bệnh viện",
    "verizon", "at&t", "t-mobile", "vodafone",
    "viettel", "vnpt", "mobifone", "vinaphone", "fpt",
]

ORGS_TIER2 = [
    # Hãng công nghệ / nền tảng lớn (KHÔNG tự kích hoạt)
    "microsoft", "google", "apple", "cisco", "intel", "meta",
    "facebook", "instagram", "whatsapp", "oracle", "ibm", "salesforce",
    "github", "gitlab", "atlassian", "okta", "crowdstrike", "vmware",
    "nvidia", "snowflake", "hugging face",
    # Cơ quan điều tra / phòng thủ / tổ chức quốc tế
    "fbi", "cia", "nsa", "cisa", "nato",
    # Tên chung chung
    "hospital",
]

# =====================================================================
# 3. TIMEZONE
# =====================================================================

# Quota ngày và các khung giờ sáng/trưa/chiều tính theo múi giờ này, KHÔNG phải UTC.
TIMEZONE = "Asia/Ho_Chi_Minh"

# =====================================================================
# 4. RSS SOURCES
# =====================================================================

# Timeout (giây) cho mỗi lần fetch RSS. Feed chậm/không phản hồi sẽ bị bỏ qua.
RSS_TIMEOUT_SECONDS = 20

# Bài cũ hơn số giờ này sẽ bị loại (không đăng tin cũ).
MAX_ARTICLE_AGE_HOURS = 36

# Số bài tối đa lấy từ mỗi feed (phòng feed "nuốt" toàn bộ nguồn khác).
MAX_ARTICLES_PER_SOURCE = 40

# Tổng số bài tối đa xử lý trong một lần chạy.
MAX_TOTAL_ARTICLES = 400

# User-Agent khi fetch (một số feed chặn request không có UA).
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Các nguồn RSS chính thức đã xác minh.
# KHÔNG tự thêm URL bịa. Muốn thêm nguồn mới phải test URL thật trước.
# (Ngày 31/10/2026: 8 nguồn mới dưới đây đã kiểm chứng bằng feedparser:
#  HTTP 200, parse được entries, entry CÓ ngày đăng. Ứng viên THẤT BẠI
#  kiểm chứng nên KHÔNG thêm: Ars Technica security (404), Reuters tech (404),
#  Google Cloud Threat Intelligence / Mandiant blog (0 entries), ICTnews
#  (0 entries - trả HTML), antoanthongtin.vn (lỗi SSL certificate),
#  VnExpress an-ninh-mang/oi-so (0 entries - sai slug).)
RSS_SOURCES = {
    # --- Bảo mật (an ninh mạng) ---
    "BleepingComputer": "https://www.bleepingcomputer.com/feed/",
    "The Hacker News": "https://feeds.feedburner.com/TheHackersNews",
    "KrebsOnSecurity": "https://krebsonsecurity.com/feed/",
    "The Record": "https://therecord.media/feed",
    "CISA": "https://www.cisa.gov/cybersecurity-advisories/cybersecurity-advisories.xml",
    "SecurityWeek": "https://www.securityweek.com/feed/",
    "Dark Reading": "https://www.darkreading.com/rss.xml",
    "CyberScoop": "https://cyberscoop.com/feed/",
    "Wired (Security)": "https://www.wired.com/feed/category/security/latest/rss",
    "Microsoft Security Blog": "https://www.microsoft.com/en-us/security/blog/feed/",
    # --- Crypto (chỉ đi vào pipeline khi có hard_signal / từ khóa tấn công) ---
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "The Block": "https://www.theblock.co/rss.xml",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    # --- Tổng hợp (chỉ đi vào pipeline khi có hard_signal / từ khóa tấn công) ---
    "BBC Technology": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    # --- Việt Nam ---
    "VnExpress Cong nghe": "https://vnexpress.net/rss/cong-nghe.rss",
    "Tuoi Tre Cong nghe": "https://tuoitre.vn/rss/cong-nghe.rss",
    "Thanh Nien Cong nghe": "https://thanhnien.vn/rss/cong-nghe.rss",
    "Vietnamnet Cong nghe": "https://vietnamnet.vn/rss/cong-nghe.rss",
}

# Mức độ tin cậy của từng nguồn (dùng trong scoring).
# Nguồn không có trong dict này được coi là "Unknown".
SOURCE_TRUST_SCORE = {
    "BleepingComputer": 14,
    "The Hacker News": 13,
    "KrebsOnSecurity": 14,
    "The Record": 13,
    "CISA": 15,
    "SecurityWeek": 12,
    "Dark Reading": 12,
    "CyberScoop": 11,
    "Wired (Security)": 11,
    "Microsoft Security Blog": 12,
    "CoinDesk": 12,
    "The Block": 11,
    "Cointelegraph": 10,
    "Decrypt": 10,
    "BBC Technology": 13,
    "VnExpress Cong nghe": 12,
    "Tuoi Tre Cong nghe": 12,
    "Thanh Nien Cong nghe": 11,
    "Vietnamnet Cong nghe": 11,
    "Unknown": 6,
}

# Loại từng nguồn (D1): "security" (báo an ninh mạng) | "crypto" |
# "general" (báo tổng hợp) | "vn" (báo tiếng Việt).
# Nguồn không có trong map -> mặc định "security" (các nguồn cũ đều là security).
SOURCE_KIND = {
    "BleepingComputer": "security",
    "The Hacker News": "security",
    "KrebsOnSecurity": "security",
    "The Record": "security",
    "CISA": "security",
    "SecurityWeek": "security",
    "Dark Reading": "security",
    "CyberScoop": "security",
    "Wired (Security)": "security",
    "Microsoft Security Blog": "security",
    "CoinDesk": "crypto",
    "The Block": "crypto",
    "Cointelegraph": "crypto",
    "Decrypt": "crypto",
    "BBC Technology": "general",
    "VnExpress Cong nghe": "vn",
    "Tuoi Tre Cong nghe": "vn",
    "Thanh Nien Cong nghe": "vn",
    "Vietnamnet Cong nghe": "vn",
}

# Nguồn crypto / tổng hợp / tiếng Việt CHỈ được vào pipeline khi bài có
# hard_signal HOẶC có từ khóa tấn công/sự cố rõ ràng:
# - crypto/general: tránh bị ngập tin giá coin / tin thị trường.
# - vn: tránh tin đời sống/thương mại từ báo VN (nhóm 5); bài VN về an ninh
#   mạng (tấn công, rò rỉ, lộ lọt, mã độc, lừa đảo, lỗ hổng...) vẫn qua cổng.
SOURCE_REQUIRES_HARD_SIGNAL = {"crypto", "general", "vn"}

# Loại nguồn cần >= 2 nguồn xác nhận để một bài hard_signal được coi là NÓNG
# (đường hard của hot_rule_explain). Báo an ninh (security) và báo VN (vn)
# uy tín chỉ cần >= 1 nguồn - giữ nguyên hành vi B2 (KHÔNG đổi vì thêm "vn"
# vào SOURCE_REQUIRES_HARD_SIGNAL cho cổng pipeline).
HOT_HARD_MIN_SOURCES_KINDS = {"crypto", "general"}

# =====================================================================
# 5. DISCORD FORUM TAGS
# =====================================================================

# Danh sách tag có sẵn trên Forum Channel.
# Bot tự chọn tag phù hợp cho mỗi bài (xem analyzer.py -> build_tags).
# Nếu tag không tồn tại trên Forum, bot sẽ bỏ qua tag đó (không crash).
FORUM_TAG_NAMES = [
    "🇻🇳 Việt Nam",
    "🌎 Quốc tế",
    "🔐 An ninh mạng",
    "🚨 Tội phạm mạng",
    "💻 Công nghệ",
    "🦠 Malware",
    "🔓 Lỗ hổng",
    "💾 Rò rỉ dữ liệu",
]

# Tag mặc định nếu bot không xác định được gì (phòng hờ, ít dùng).
DEFAULT_TAG_NAMES = ["🌎 Quốc tế", "🔐 An ninh mạng"]

# =====================================================================
# 6. STATE & LOGGING
# =====================================================================

# File lưu trạng thái (bài đã đăng, fingerprint, số bài theo ngày).
# Workflow GitHub Actions commit/push file này sau mỗi lần chạy.
STATE_FILE = os.environ.get("STATE_FILE", "state/news_state.json")

# Mức log: DEBUG / INFO / WARNING / ERROR
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# =====================================================================
# 7. RATE LIMIT DISCORD
# =====================================================================

# Nghỉ (giây) giữa hai lần tạo Forum Post, tránh rate limit.
POST_DELAY_SECONDS = 2
