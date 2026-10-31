# -*- coding: utf-8 -*-
"""
discord_poster.py - Kết nối Discord và tạo Forum Post (KHÔNG phải tin nhắn text).

Sử dụng discord.py 2.x:
- Fetch Forum Channel theo ID.
- Đối chiếu tên tag với available_tags của channel.
- Tạo Forum Post bằng channel.create_thread(...).

QUYỀN CẦN THIẾT (KHÔNG cần Administrator):
- View Channel
- Send Messages
- Embed Links
- Read Message History
- Create Public Threads
- Send Messages in Threads   <-- BẮT BUỘC với Forum Channel

Lưu ý về Forum API:
- Tạo post trong Forum về bản chất là tạo một thread công khai.
- "Send Messages in Threads" là quyền quyết định; thiếu nó sẽ ra lỗi 403.
- applied_tags chỉ nhận các ForumTag có SẴN trên channel; tag không tồn tại
  sẽ bị bỏ qua (không crash) và được ghi warning.
- Tag trên Forum có thể lưu emoji ở trường RIÊNG, nên tag.name có thể là
  "Việt Nam" chứ không phải "🇻🇳 Việt Nam" -> so khớp theo khóa chuẩn hóa (A1).
"""

import asyncio
import logging
import re
import unicodedata

import discord

from config import (
    DISCORD_CHANNEL_ID,
    MIN_SCORE_TO_POST,
    POST_DELAY_SECONDS,
)
from textutils import excerpt, format_vn_time, normalize_ws
from ping_roles import build_ping_roles, prepend_pings

logger = logging.getLogger("poster")

# Giới hạn của Discord.
THREAD_NAME_MAX = 100      # tên thread/post tối đa 100 ký tự
CONTENT_MAX = 1900         # nội dung tin nhắn tối đa 2000 ký tự


# =====================================================================
# CHUẨN HÓA TÊN TAG (A1)
# =====================================================================

def normalize_tag_key(name):
    """
    Chuẩn hóa tên tag thành khóa so khớp bền vững:
    - Unicode NFC (gộp dấu).
    - Bỏ MỌI ký tự không phải chữ/số (emoji, khoảng trắng, dấu câu...).
    - lowercase.

    Ví dụ:
        "🇻🇳 Việt Nam" -> "việt nam" -> "việtnam"
        "Việt Nam"     -> "việt nam" -> "việtnam"   (khớp nhau)
        "🔓 Lỗ hổng"   -> "lỗ hổng"  -> "lỗhổng"
    """
    text = unicodedata.normalize("NFC", str(name or ""))
    text = re.sub(r"[\W_]+", "", text, flags=re.UNICODE)
    return text.lower()


# =====================================================================
# FORMAT NỘI DUNG FORUM POST
# =====================================================================

def severity_label(score):
    """Chuyển điểm sang nhãn mức độ hiển thị cho người đọc."""
    if score >= 95:
        return "🔴 Nghiêm trọng"
    if score >= MIN_SCORE_TO_POST:
        return "🟠 Cao"
    return "🟡 Trung bình-Cao"   # chỉ dùng cho bài 70-84 có yếu tố đặc biệt


def build_post_title(scored):
    """
    Tên Forum Post: ưu tiên tiêu đề do AI VIẾT (ai_title) nếu có,
    không thì lấy title gốc.
    F1: bài TIN NÓNG được thêm tiền tố "🔥 " (nếu chưa có).
    Cắt còn THREAD_NAME_MAX ký tự (sau khi thêm tiền tố -> luôn <= 100).
    KHÔNG tạo tag Forum mới.
    """
    title = normalize_ws(getattr(scored, "ai_title", "") or "")
    if not title:
        title = normalize_ws(getattr(scored, "title", "") or "")
    if not title:
        title = "Cập nhật an ninh mạng"

    # F1: tiền tố tin nóng (không thêm lần hai nếu tiêu đề đã có sẵn).
    if getattr(scored, "is_hot", False) and not title.startswith("🔥"):
        title = "🔥 " + title

    if len(title) > THREAD_NAME_MAX:
        title = title[:THREAD_NAME_MAX - 1].rstrip() + "…"
    return title


def _truncate_keep_footer(content, limit=CONTENT_MAX):
    """
    Cắt nội dung về <= limit ký tự NHƯNG GIỮ NGUYÊN footer (phần bắt đầu
    bằng "🔗": nguồn + URL). Chỉ cắt phần thân phía trước và thêm "…".

    Nếu footer không nằm trong limit thì cắt footer cho vừa (hiếm; link có thể
    bị mất trong trường hợp này - nhưng footer luôn được ưu tiên tối đa).
    """
    if len(content) <= limit:
        return content

    footer_idx = content.rfind("🔗")
    if footer_idx <= 0:
        # Không có footer -> cắt thẳng (giữ nguyên hành vi cũ).
        return content[:limit - 1].rstrip() + "…"

    body = content[:footer_idx].rstrip()
    footer = content[footer_idx:].strip()

    # Thử giữ footer nguyên vẹn trước.
    # body + "…" + "\n" + footer <= limit  ->  max_body = limit - len(footer) - 2
    max_body = limit - len(footer) - 2
    if max_body < 0:
        # Footer dài hơn limit (hiếm) -> buộc cắt footer, giữ phần đầu.
        return footer[:limit - 1].rstrip() + "…"

    if len(body) > max_body:
        body = body[:max_body].rstrip() + "…"

    return (body + "\n" + footer)[:limit]


def build_post_content(scored):
    """
    Dựng nội dung Forum Post.

    Ưu tiên 1: nội dung do AI biên tập (scored.ai_content) theo format
              🚨 / 🔎 Đáng chú ý / 🛡️ Tác động / 🔗 Nguồn.
              Khi cắt theo CONTENT_MAX phải GIỮ footer 🔗 (A4).
    Ưu tiên 2: nếu AI lỗi/tắt (không có ai_content) -> dựng nội dung rule-based
              từ summary + tín hiệu có thật trong RSS.

    QUAN TRỌNG: toàn bộ thông tin đều lấy từ title/summary/signals có thật
    trong RSS (hoặc do AI viết đúng theo input đó). KHÔNG bịa thêm chi tiết.
    """
    article = scored.article
    link = getattr(scored, "link", "") or ""

    # ---- ƯU TIÊN 1: NỘI DUNG AI ----
    ai_content = (getattr(scored, "ai_content", "") or "").strip()
    if ai_content:
        content = ai_content.replace("\r\n", "\n").replace("\r", "\n").strip()
        # AI quên chèn link gốc -> tự bổ sung để người đọc truy cập được nguồn.
        if link and link not in content:
            content += "\n\n🔗 [Đọc bài gốc](%s)" % link
        # Cắt nhưng PHẢI giữ footer 🔗 (nguồn + URL không bao giờ được mất).
        return _truncate_keep_footer(content, CONTENT_MAX)

    # ---- ƯU TIÊN 2: NỘI DUNG RULE-BASED (fallback) ----
    parts = []

    # F2: dòng đánh dấu TIN NÓNG (chỉ ở nhánh rule-based; nội dung AI giữ nguyên).
    if getattr(scored, "is_hot", False):
        parts.append("🔥 **TIN NÓNG**")

    # (1) Tóm tắt sự kiện - lấy nguyên văn summary từ RSS (đã strip HTML).
    summary = normalize_ws(getattr(article, "summary", "") or "")
    if not summary:
        summary = normalize_ws(getattr(article, "title", "") or "")
    parts.append("🚨 **Tóm tắt sự kiện**")
    parts.append(excerpt(summary, 700) or "(nguồn không cung cấp tóm tắt)")
    parts.append("")

    # (2) Điểm đáng chú ý - liệt kê các tín hiệu THỰC SỰ match trong bài.
    highlights = []
    for reason in getattr(scored, "reasons", [])[:6]:
        # Bỏ đoạn "(+14)" cho gọn, giữ phần mô tả.
        clean = reason.split(" (+")[0].strip()
        if clean and not clean.startswith("Chủ đề"):
            highlights.append(clean)

    if highlights:
        parts.append("**Điểm đáng chú ý**")
        for item in highlights[:5]:
            parts.append("• %s" % item)
        parts.append("")

    # (3) Mức độ.
    parts.append("**Mức độ:** %s" % severity_label(scored.score))
    parts.append("")

    # (4) Nguồn + thời gian.
    parts.append("**Nguồn:** %s" % (scored.source or "không rõ"))
    published = getattr(article, "published", None)
    parts.append("🕐 Đăng lúc: %s" % format_vn_time(published))
    parts.append("")

    # (5) Link bài gốc.
    if link:
        parts.append("🔗 [Đọc bài gốc](%s)" % link)

    content = "\n".join(parts)

    # Cắt nội dung nếu vượt giới hạn Discord (giữ footer 🔗 - cùng logic A4).
    return _truncate_keep_footer(content, CONTENT_MAX)


# =====================================================================
# ĐỐI CHIẾU TAG VỚI FORUM CHANNEL
# =====================================================================

def resolve_tags(forum_channel, tag_names):
    """
    Đối chiếu danh sách tên tag -> list ForumTag có sẵn trên channel.

    So khớp theo khóa chuẩn hóa (A1) vì tag trên Forum có thể lưu emoji ở
    trường riêng -> tag.name chỉ còn "Việt Nam".

    - Tag không tồn tại -> bỏ qua + warning kèm danh sách khóa tag có sẵn.
    - Trả về list[ForumTag] (có thể rỗng; Discord chấp nhận 0 tag).
    - Tối đa 5 tag/post theo giới hạn Discord.
    """
    available = {}        # key chuẩn hóa -> ForumTag
    available_keys = []   # để log khi không khớp tag nào
    try:
        for tag in forum_channel.available_tags:
            key = normalize_tag_key(tag.name)
            available[key] = tag
            available_keys.append(key)
    except Exception as exc:
        logger.warning("[DISCORD] Không đọc được available_tags: %r", exc)
        return []

    resolved = []
    for name in (tag_names or []):
        key = normalize_tag_key(name)
        tag = available.get(key)
        if tag is not None:
            resolved.append(tag)
        else:
            logger.warning(
                "[DISCORD] Tag không tồn tại trên Forum, bỏ qua: %r (key=%r). "
                "Khóa tag có sẵn trên Forum: %s",
                name, key, available_keys or "(trống)",
            )

    # Discord giới hạn tối đa 5 tag cho một post.
    return resolved[:5]


# =====================================================================
# TẠO FORUM POST
# =====================================================================

def _build_allowed_mentions(role_ids):
    """Chỉ cho phép ping đúng các role trong role_ids (rỗng = không ping ai)."""
    if role_ids:
        return discord.AllowedMentions(
            everyone=False, users=False, replied_user=False,
            roles=[discord.Object(id=rid) for rid in role_ids])
    return discord.AllowedMentions.none()


async def post_to_forum(token, channel_id, title, content, tag_names,
                        dry_run=False, role_ids=None):
    """
    Tạo MỘT Forum Post trên channel với token cho sẵn.

    Trả về dict:
      {"ok": bool, "thread_id": int|None, "error": str|None}

    Hàm này KHÔNG bao giờ raise ra ngoài; mọi lỗi đều trả về trong dict.
    """
    result = {"ok": False, "thread_id": None, "error": None}

    # Giới hạn thời gian kết nối tổng thể để không treo GitHub Actions.
    try:
        return await asyncio.wait_for(
            _post_to_forum_inner(token, channel_id, title, content,
                                 tag_names, result, dry_run, role_ids),
            timeout=90,
        )
    except asyncio.TimeoutError:
        result["error"] = "timeout sau 90s khi kết nối Discord"
        logger.error("[DISCORD] Timeout khi tạo post: %s", title[:60])
        return result
    except Exception as exc:
        result["error"] = repr(exc)
        logger.error("[DISCORD] Lỗi ngoài không xác định: %r", exc)
        return result


def _log_forbidden_diagnostics(client, channel_id):
    """
    Chạy khi gặp 403 Forbidden: ghi ra bot đang ở những server nào và có
    thấy kênh mục tiêu không, để biết lỗi do quyền kênh, sai server, hay sai
    bot/token. Chỉ ghi tên + ID (KHÔNG ghi token).
    """
    try:
        guilds = list(client.guilds)
        logger.error("[DIAG] Bot id=%s đang ở %d server.",
                     getattr(client.user, "id", "?"), len(guilds))
        for guild in guilds:
            channels = list(guild.channels)
            found = any(getattr(c, "id", None) == channel_id for c in channels)
            forums = ["%s(%s)" % (c.name, c.id) for c in channels
                      if isinstance(c, discord.ForumChannel)]
            logger.error("[DIAG] Server '%s' (%s): bot thấy %d kênh | "
                         "kênh mục tiêu %s: %s | forum nhìn thấy: %s",
                         guild.name, guild.id, len(channels), channel_id,
                         "CÓ THẤY" if found else "KHÔNG THẤY",
                         ", ".join(forums) or "(không có)")
            if found:
                target = next(c for c in channels
                              if getattr(c, "id", None) == channel_id)
                me = guild.me
                perms = target.permissions_for(me)
                names = [
                    ("view_channel", "Xem kênh"),
                    ("send_messages", "Tạo bài đăng (Create Posts)"),
                    ("send_messages_in_threads", "Gửi tin trong bài đăng"),
                    ("create_public_threads", "Tạo thread công khai"),
                    ("embed_links", "Nhúng liên kết"),
                    ("read_message_history", "Đọc lịch sử"),
                    ("administrator", "Administrator"),
                ]
                summary = ", ".join(
                    "%s=%s" % (label, "CÓ" if getattr(perms, attr, False)
                               else "THIẾU")
                    for attr, label in names)
                logger.error("[DIAG] Quyền của bot trên kênh mục tiêu: %s",
                             summary)
                logger.error("[DIAG] Bot bị timeout đến: %s | role cao nhất: %s",
                             getattr(me, "timed_out_until", None),
                             getattr(getattr(me, "top_role", None), "name", "?"))
    except Exception as exc:  # chẩn đoán không được làm hỏng luồng chính
        logger.error("[DIAG] Không chẩn đoán được: %r", exc)


async def _post_to_forum_inner(token, channel_id, title, content, tag_names,
                               result, dry_run, role_ids=None):
    """Phần lõi (có client) của post_to_forum."""

    # Chế độ thử: không kết nối Discord, chỉ in nội dung ra log.
    if dry_run:
        logger.info("[DISCORD] DRY RUN - sẽ tạo post: %s", title)
        logger.info("[DISCORD] Tags: %s", tag_names)
        logger.info("[DISCORD] Nội dung:\n%s", content)
        result["ok"] = True
        result["thread_id"] = 0
        return result

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            logger.info("[DISCORD] Đã kết nối với tư cách %s", client.user)

            # Lấy Forum Channel.
            channel = await client.fetch_channel(channel_id)
            if not isinstance(channel, discord.ForumChannel):
                result["error"] = (
                    "Channel %s không phải Forum Channel (nhận được %s). "
                    "Hãy kiểm tra DISCORD_CHANNEL_ID." %
                    (channel_id, type(channel).__name__)
                )
                logger.error("[DISCORD] %s", result["error"])
                return

            # Đối chiếu tag (so khớp theo khóa chuẩn hóa).
            applied = resolve_tags(channel, tag_names)

            # Tạo Forum Post.
            # A2: chỉ role trong ping_roles.py được ping; nội dung RSS/AI KHÔNG ping ai.
            logger.info("[DISCORD] Posting... %s", title)
            created = await channel.create_thread(
                name=title,
                content=content,
                applied_tags=applied,
                allowed_mentions=_build_allowed_mentions(role_ids),
            )

            # A3: create_thread với Forum Channel trả về cặp (thread, message).
            # Lấy thread_id từ created.thread.id (an toàn với mọi kiểu trả về).
            thread_obj = getattr(created, "thread", None)
            if thread_obj is None:
                thread_obj = created   # fallback nếu discord.py trả thẳng Thread

            result["ok"] = True
            result["thread_id"] = getattr(thread_obj, "id", None)
            logger.info("[DISCORD] Success: post '%s' (thread_id=%s, tags=%d)",
                        title[:60], result["thread_id"], len(applied))
        except discord.Forbidden as exc:
            _log_forbidden_diagnostics(client, channel_id)
            result["error"] = (
                "403 Forbidden - thiếu quyền. Cần: View Channel, Send Messages, "
                "Embed Links, Read Message History, Create Public Threads, "
                "Send Messages in Threads. Chi tiết: %r" % exc
            )
            logger.error("[DISCORD] %s", result["error"])
        except discord.NotFound as exc:
            result["error"] = "404 NotFound - sai channel ID hoặc token không ở server đó: %r" % exc
            logger.error("[DISCORD] %s", result["error"])
        except discord.HTTPException as exc:
            result["error"] = "Lỗi HTTP Discord (%s): %s" % (exc.status, exc.text)
            logger.error("[DISCORD] %s", result["error"])
        except Exception as exc:
            result["error"] = repr(exc)
            logger.error("[DISCORD] Lỗi không xác định: %r", exc)
        finally:
            # Đóng client để hàm async kết thúc và GitHub Actions thoát sạch.
            try:
                await client.close()
            except Exception:
                pass

    # start() sẽ chạy cho tới khi client.close() được gọi trong on_ready.
    await client.start(token)
    return result


# =====================================================================
# ĐĂNG NHIỀU BÀI (giữ thứ tự, có nghỉ giữa các post)
# =====================================================================

async def post_all(token, channel_id, scored_list, dry_run=False):
    """
    Đăng lần lượt các bài trong scored_list.
    Trả về list kết quả (mỗi phần tử là dict của post_to_forum).
    """
    results = []
    total = len(scored_list)
    for index, scored in enumerate(scored_list, 1):
        title = build_post_title(scored)
        content = build_post_content(scored)
        tags = list(getattr(scored, "tags", []) or [])
        content, ping_ids = prepend_pings(content, build_ping_roles(scored))
        if ping_ids:
            logger.info("[DISCORD] Ping %d role: %s", len(ping_ids), ping_ids)

        logger.info("[DISCORD] Đăng bài %d/%d: %s", index, total, title)
        outcome = await post_to_forum(
            token=token,
            channel_id=channel_id,
            title=title,
            content=content,
            tag_names=tags,
            dry_run=dry_run,
            role_ids=ping_ids,
        )
        results.append(outcome)

        # Nghỉ giữa hai post để tránh rate limit.
        if index < total:
            await asyncio.sleep(POST_DELAY_SECONDS)

    return results
