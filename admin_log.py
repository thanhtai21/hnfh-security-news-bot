"""
Gửi nhật ký mỗi lần chạy về kênh admin (mặc định tên: log-botnews).

Cách hoạt động:
  - LogCapture gom mọi dòng log của lần chạy trong bộ nhớ.
  - Cuối lần chạy: dựng bản TÓM TẮT (<= ~1800 ký tự) + đính kèm TOÀN BỘ log (.txt).
  - Tìm kênh: ADMIN_LOG_CHANNEL_ID (nếu đặt) hoặc kênh chữ tên ADMIN_LOG_CHANNEL_NAME
    trong CÙNG server với Forum tin tức.
  - KHÔNG BAO GIỜ làm hỏng lần chạy: mọi lỗi chỉ ghi cảnh báo.
  - DRY_RUN: không gửi (chỉ in tóm tắt ra console).

Biến môi trường (đều tùy chọn):
  ADMIN_LOG_ENABLED       "1" (mặc định) / "0" để tắt
  ADMIN_LOG_CHANNEL_NAME  mặc định "log-botnews"
  ADMIN_LOG_CHANNEL_ID    ID kênh (mặc định 1571795827226185809; ưu tiên hơn tên)
  ADMIN_LOG_ONLY_ISSUES   "1" = chỉ gửi khi có đăng bài/cảnh báo/lỗi (mặc định "0": luôn gửi)
"""

import asyncio
import io
import logging
import os
import re

logger = logging.getLogger("admin_log")

DEFAULT_CHANNEL_NAME = "log-botnews"
# ID kênh log-botnews (không phải bí mật). Biến môi trường ADMIN_LOG_CHANNEL_ID sẽ ghi đè.
DEFAULT_CHANNEL_ID = "1571795827226185809"
SUMMARY_MAX = 1800

# Che các chuỗi giống khóa bí mật (phòng log vô tình chứa).
_SECRET_PATTERNS = (
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"[MNO][A-Za-z0-9_\-]{23,25}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}"),
    re.compile(r"(?i)(token|api[_-]?key|authorization)\s*[=:]\s*\S+"),
)


def redact(text):
    out = text or ""
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[ĐÃ ẨN]", out)
    return out


def _env_flag(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def is_enabled():
    return _env_flag("ADMIN_LOG_ENABLED", True)


class LogCapture(logging.Handler):
    """Gom các dòng log (đã format) của lần chạy hiện tại."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = []
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(name)-10s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"))

    def emit(self, record):
        try:
            self.lines.append(self.format(record))
        except Exception:
            pass

    def text(self):
        return redact("\n".join(self.lines))


def install_capture():
    handler = LogCapture()
    logging.getLogger().addHandler(handler)
    return handler


def remove_capture(handler):
    try:
        logging.getLogger().removeHandler(handler)
    except Exception:
        pass


def _pick(lines, needle, limit):
    found = [ln for ln in lines if needle in ln]
    return found[:limit], len(found)


def _clip(line, width=170):
    """Bỏ phần 'ngày giờ [LEVEL] tên' ở đầu dòng cho gọn rồi cắt độ dài."""
    parts = line.split("] ", 1)
    body = parts[1] if len(parts) == 2 else line
    body = re.sub(r"^\S+\s+", "", body, count=1) if body.startswith("main") else body
    return body if len(body) <= width else body[:width - 1] + "…"


def build_summary(lines, info):
    """
    info: dict(now, exit_code, dry_run, selected, posted, hot, rejected, posted_titles)
    Trả chuỗi tóm tắt <= SUMMARY_MAX ký tự.
    """
    exit_code = info.get("exit_code", 0)
    ok = exit_code == 0
    head = "%s **Bot tin tức** - %s | đăng %d/%d bài (nóng %d), AI loại %d" % (
        "✅" if ok else "❌", info.get("now", "?"), info.get("posted", 0),
        info.get("selected", 0), info.get("hot", 0), info.get("rejected", 0))
    out = [head]
    if not ok:
        out.append("⚠️ Mã thoát: %s" % exit_code)

    for title in (info.get("posted_titles") or [])[:6]:
        out.append("• " + title[:110])

    errs = [ln for ln in lines if "[ERROR" in ln or "[CRITICAL" in ln]
    warns = [ln for ln in lines if "[WARNING" in ln]
    ai_fail = [ln for ln in lines if "[AI]" in ln and
               ("không khả dụng" in ln or "Chưa có bài do AI viết" in ln
                or "429" in ln or "lỗi" in ln.lower())]
    def _rss_is_bad(ln):
        if "ERROR" in ln or "thất bại" in ln.lower() or "FAILED" in ln or "NO ENTRIES" in ln:
            return True
        if "0 bài hợp lệ" in ln:
            # Có thống kê "thô=N": feed có bài nhưng đều cũ là bình thường.
            if "thô=" in ln:
                return bool(re.search(r"thô=0\b", ln)) or "bozo=True" in ln
            return True
        return False
    rss_bad = [ln for ln in lines if "[RSS]" in ln and _rss_is_bad(ln)]
    events, n_events = _pick(lines, "[EVENT] Bỏ", 3)
    missed, n_missed = _pick(lines, "[MISSED] score=", 8)

    out.append("📊 Cảnh báo %d | lỗi %d | AI lỗi %d | nguồn RSS lỗi/0 bài %d | event guard bỏ %d" % (
        len(warns), len(errs), len(ai_fail), len(rss_bad), n_events))

    if errs:
        out.append("**Lỗi:**")
        out += ["- " + _clip(ln) for ln in errs[:3]]
    if ai_fail:
        out.append("**AI:**")
        out += ["- " + _clip(ln) for ln in ai_fail[:3]]
    if rss_bad:
        out.append("**Nguồn:**")
        out += ["- " + _clip(ln, 120) for ln in rss_bad[:4]]
    if missed:
        out.append("**Tin đáng chú ý chưa đăng (%d):**" % n_missed)
        out += ["- " + _clip(ln, 150) for ln in missed]

    text = "\n".join(out)
    if len(text) > SUMMARY_MAX:
        text = text[:SUMMARY_MAX - 20].rstrip() + "\n… (xem file đính kèm)"
    return redact(text)


def has_issues(lines, info):
    if info.get("posted", 0) or info.get("exit_code", 0):
        return True
    return any("[WARNING" in ln or "[ERROR" in ln for ln in lines)


async def _send(token, forum_channel_id, summary, full_text, filename):
    import discord

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    state = {"ok": False, "error": ""}

    @client.event
    async def on_ready():
        try:
            channel = None
            explicit = (os.environ.get("ADMIN_LOG_CHANNEL_ID", "").strip()
                        or DEFAULT_CHANNEL_ID)
            name = (os.environ.get("ADMIN_LOG_CHANNEL_NAME", "").strip()
                    or DEFAULT_CHANNEL_NAME).lower()
            if explicit.isdigit():
                channel = await client.fetch_channel(int(explicit))
            else:
                forum = await client.fetch_channel(forum_channel_id)
                guild = getattr(forum, "guild", None)
                if guild is not None:
                    chans = await guild.fetch_channels()
                    for ch in chans:
                        if (getattr(ch, "name", "") or "").lower() == name and \
                                hasattr(ch, "send"):
                            channel = ch
                            break
            if channel is None:
                state["error"] = ("Không thấy kênh '%s' (hoặc bot không có quyền xem). "
                                  "Hãy tạo kênh chữ này và cấp quyền cho bot." % name)
                return
            data = io.BytesIO(full_text.encode("utf-8", errors="replace"))
            await channel.send(
                content=summary,
                file=discord.File(data, filename=filename),
                allowed_mentions=discord.AllowedMentions.none(),
                suppress_embeds=True,
            )
            state["ok"] = True
        except Exception as exc:  # noqa: BLE001
            state["error"] = repr(exc)
        finally:
            try:
                await client.close()
            except Exception:
                pass

    await client.start(token)
    return state


def send_admin_log(token, forum_channel_id, lines, info, dry_run=False):
    """Gửi (hoặc in, nếu dry_run). Không bao giờ ném lỗi. Trả True nếu đã gửi."""
    try:
        if not is_enabled():
            return False
        summary = build_summary(lines, info)
        if dry_run:
            logger.info("[ADMINLOG] DRY_RUN - không gửi. Tóm tắt sẽ là:\n%s", summary)
            return False
        if not token:
            logger.warning("[ADMINLOG] Thiếu token Discord, không gửi log.")
            return False
        if _env_flag("ADMIN_LOG_ONLY_ISSUES", False) and not has_issues(lines, info):
            logger.info("[ADMINLOG] Không có gì đáng báo (ONLY_ISSUES=1) -> bỏ qua.")
            return False
        full = redact("\n".join(lines))
        stamp = re.sub(r"[^0-9]", "", str(info.get("now", "")))[:12] or "run"
        res = asyncio.run(_send(token, forum_channel_id, summary, full,
                                "botnews-log-%s.txt" % stamp))
        if res and res.get("ok"):
            logger.info("[ADMINLOG] Đã gửi log về kênh admin.")
            return True
        logger.warning("[ADMINLOG] Không gửi được log: %s",
                       (res or {}).get("error", "không rõ"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ADMINLOG] Lỗi khi gửi log: %r", exc)
    return False
