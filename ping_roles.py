# -*- coding: utf-8 -*-
"""
ping_roles.py - Cấu hình + logic PING ROLE khi bot đăng bài.

Cách dùng:
  1. Tạo role trong Discord (ví dụ "ping news", "hot news", "malware"...).
  2. Cho phép role được nhắc tên: Server Settings -> Roles -> chọn role ->
     bật "Allow anyone to @mention this role" (hoặc cấp cho bot quyền
     "Mention @everyone, @here and All Roles").
  3. Bật Developer Mode, chuột phải vào role -> Copy Role ID, dán vào bên dưới.
  4. Role nào để 0 = TẮT (không ping). Tất cả = 0 thì bot hoạt động y như cũ.

Quy tắc ping:
  - ROLE_PING_NEWS_ID : ping ở MỌI bài mới (thường và nóng).
  - ROLE_HOT_NEWS_ID  : chỉ ping ở bài NÓNG (scored.is_hot).
  - TOPIC_ROLE_IDS    : ping theo TAG/NGÀNH của bài (tag do bot tự gán).
    Tối đa MAX_TOPIC_ROLE_PINGS role ngành mỗi bài để tránh ping quá nhiều.
An toàn: chỉ những role có ID trong file này mới được ping; nội dung từ RSS/AI
không thể ping role/người khác.
"""
import re

ROLE_PING_NEWS_ID = 0
ROLE_HOT_NEWS_ID = 0

# Khóa = đúng tên tag trong config.FORUM_TAG_NAMES (emoji/viết hoa không quan trọng).
TOPIC_ROLE_IDS = {
    "🇻🇳 Việt Nam": 0,
    "🌎 Quốc tế": 0,
    "🔐 An ninh mạng": 0,
    "🚨 Tội phạm mạng": 0,
    "💻 Công nghệ": 0,
    "🦠 Malware": 0,
    "🔓 Lỗ hổng": 0,
    "💾 Rò rỉ dữ liệu": 0,
}
MAX_TOPIC_ROLE_PINGS = 2

DISCORD_MESSAGE_MAX = 2000


def _tag_key(text):
    """Chuẩn hóa tên tag: bỏ emoji/ký tự đặc biệt, hạ chữ thường."""
    return re.sub(r"[^\w]", "", str(text or "").casefold())


def _valid_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def build_ping_roles(scored):
    """Trả về list role ID cần ping cho bài `scored` (không trùng, giữ thứ tự)."""
    ids = []

    def add(role_id):
        if _valid_id(role_id) and role_id not in ids:
            ids.append(role_id)

    add(ROLE_PING_NEWS_ID)
    if getattr(scored, "is_hot", False):
        add(ROLE_HOT_NEWS_ID)

    topic_map = {_tag_key(name): rid for name, rid in TOPIC_ROLE_IDS.items()}
    topic_count = 0
    for tag in (getattr(scored, "tags", None) or []):
        rid = topic_map.get(_tag_key(tag), 0)
        if _valid_id(rid) and rid not in ids and topic_count < MAX_TOPIC_ROLE_PINGS:
            add(rid)
            topic_count += 1
    return ids


def prepend_pings(content, role_ids):
    """
    Thêm dòng nhắc role lên ĐẦU nội dung. Nếu vượt 2000 ký tự thì bỏ bớt role
    ở cuối cho tới khi vừa. Trả về (content_mới, role_ids_thực_sự_ping).
    """
    ids = list(role_ids or [])
    while ids:
        line = " ".join("<@&%d>" % rid for rid in ids) + "\n"
        if len(line) + len(content) <= DISCORD_MESSAGE_MAX:
            return line + content, ids
        ids.pop()
    return content, []
