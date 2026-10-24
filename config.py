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
MIN_SCORE_TO_POST = 85

# Bài trong khoảng [SPECIAL_MIN_SCORE, MIN_SCORE_TO_POST) chỉ được đăng
# nếu có "yếu tố đặc biệt" (xem analyzer.py - SPECIAL_SIGNALS).
SPECIAL_MIN_SCORE = 70

# Điểm rule tối thiểu để một bài được GỬI CHO AI (nhánh quota THƯỜNG).
# Bài 45-84 chưa đạt ngưỡng đăng vẫn được AI xem; bài < 45 không gửi AI.
AI_CANDIDATE_MIN_SCORE = 45

# Bài rule < ngưỡng đăng (85 / special) CHỈ được đăng nếu AI thật sự chấm
# điểm >= mức này (và các điều kiện khác vẫn đạt). AI tắt/lỗi -> không được cứu.
AI_RESCUE_MIN_SCORE = 85

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
# Workflow chạy 07h, 10h, 13h, 16h, 19h, 22h, 01h, 04h (giờ VN) -> sáng: 07h+10h,
# trưa: 13h, chiều: 16h+19h.
NORMAL_POST_SLOTS = (
    ("sáng", 6, 11),
    ("trưa", 11, 15),
    ("chiều", 15, 21),
)

# =====================================================================
# 2b. TIN NÓNG (HOT NEWS) - quota RIÊNG, không đụng MAX_POSTS_PER_DAY
# =====================================================================

# Tối đa bài TIN NÓNG mỗi ngày (tổng tối đa = 3 thường + 2 nóng).
MAX_HOT_POSTS_PER_DAY = 2

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

# Điểm tối thiểu do AI chấm để một bài được coi là tin nóng.
HOT_AI_MIN_SCORE = 90

# AI tắt/lỗi thì KHÔNG đăng tin nóng (mặc định "0").
# Đặt "1" -> khi không có AI, bài nóng phải có >= 3 nguồn độc lập mới đăng.
HOT_ALLOW_WITHOUT_AI = os.environ.get(
    "HOT_ALLOW_WITHOUT_AI", "0").strip().lower() in ("1", "true", "yes", "on")

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
RSS_SOURCES = {
    "BleepingComputer": "https://www.bleepingcomputer.com/feed/",
    "The Hacker News": "https://feeds.feedburner.com/TheHackersNews",
    "KrebsOnSecurity": "https://krebsonsecurity.com/feed/",
    "The Record": "https://therecord.media/feed",
    "CISA": "https://www.cisa.gov/cybersecurity-advisories/cybersecurity-advisories.xml",
    "SecurityWeek": "https://www.securityweek.com/feed/",
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
    "Unknown": 6,
}

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
