# -*- coding: utf-8 -*-
"""
state.py - Lưu/đọc trạng thái bot giữa các lần chạy GitHub Actions.

State được lưu vào JSON (config.STATE_FILE) và được workflow commit/push
lại vào repository sau mỗi lần chạy (đây là điểm BẮT BUỘC của thiết kế).

Schema (state/news_state.json):
{
  "version": 1,
  "last_run": "2026-10-23T21:15:00+07:00",
  "daily_posts": {"2026-10-23": 1},
  "daily_hot_posts": {"2026-10-23": 1},
  "posts": [
    {
      "title": "...",
      "link": "...",
      "source": "BleepingComputer",
      "score": 93,
      "fingerprint": "30ee19e8d8a5618f",
      "day": "2026-10-23",
      "posted_at": "2026-10-23T21:15:03+07:00",
      "kind": "normal"
    }
  ],
  "ai_rejected": [
    {
      "link": "...",
      "fingerprint": "30ee19e8d8a5618f",
      "rejected_at": "2026-10-23T21:15:03+07:00",
      "scope": "all"
    }
  ]
}

TƯƠNG THÍCH NGƯỢC TUYỆT ĐỐI - file state cũ vẫn load được, không mất dữ liệu:
- Thiếu "ai_rejected"      -> [] .
- Thiếu "daily_hot_posts"  -> {} (chưa từng đăng tin nóng).
- Bài trong "posts" thiếu "kind"     -> coi là "normal".
- Mục "ai_rejected" thiếu "scope"    -> coi là "all".

QUOTA HAI LOẠI (C1/C2):
- daily_posts + posts.kind="normal": quota tin THƯỜNG (MAX_POSTS_PER_DAY = 3,
  mỗi khung sáng/trưa/chiều 1 bài - xem posts_in_window).
- daily_hot_posts + posts.kind="hot": quota tin NÓNG (MAX_HOT_POSTS_PER_DAY).
- posts_today(day)  CHỈ đếm bài kind != "hot" -> analyzer.select_articles
  GIỮ NGUYÊN, không phải sửa.
- hot_posts_today(day) đếm bài kind == "hot".

BỘ NHỚ "BÀI BỊ AI LOẠI" (ai_rejected, có scope - C3):
- scope "all"     : AI đánh giá thật rồi LOẠI (publish=false / score thấp)
                    -> chặn ở MỌI chế độ.
- scope "hot_only": AI DUYỆT nhưng bài KHÔNG nóng khi quota thường đã đầy
                    -> CHỈ chặn ở chế độ hot-only; sang tin thường vẫn xét lại.
- TTL = config.MAX_ARTICLE_AGE_HOURS: quá hạn thì mục bị dọn.
- KHÔNG ghi khi AI lỗi/tắt/fallback (không có quyết định thật).

LƯU Ý (C4): save() coi thay đổi CHỈ gồm "last_run" là KHÔNG có thay đổi ->
không ghi file -> workflow không commit/push 8 lần/ngày vô ích.
Hậu quả: last_run trong file là thời điểm của lần lưu CÓ THAY ĐỮNG GẦN NHẤT.

API (được analyzer.py / main.py / hot.py gọi):
- NewsState.load(path)                    -> NewsState (CHỈ khi file chưa tồn tại
                                             mới trả state trống; file CÓ mà hỏng
                                             -> backup .corrupt rồi RAISE
                                             StateCorruptError - FAIL-CLOSED)
- StateCorruptError(RuntimeError)         -> lỗi riêng khi state hỏng
- state.save(path)                        -> bool (True nếu thực sự ghi file;
                                             ghi NGUYÊN TỬ qua file .tmp;
                                             LỖI GHI -> RAISE cho main exit 1)
- state.posts_today(day)                  -> int (bài THƯỜNG)
- state.hot_posts_today(day)              -> int (bài NÓNG)
- state.is_posted_link(link)              -> bool
- state.is_posted_fingerprint(fp)         -> bool
- state.is_posted_title(title)            -> bool (so theo SỰ KIỆN, không chỉ text)
- state.record_post(scored, day, kind)    -> None (kind = "normal" | "hot")
- state.is_ai_rejected(scored, now, hot_only)   -> bool
- state.record_ai_rejection(scored, now, scope) -> None (scope = "all"|"hot_only")
- state.mark_last_run(now)                -> None
"""

import json
import logging
import os
import shutil
from datetime import datetime, timedelta

from config import (
    MAX_ARTICLE_AGE_HOURS,
    MAX_HOT_POSTS_PER_DAY,
    MAX_POSTS_PER_DAY,
)
from textutils import format_vn_time, is_same_event, normalize_title, now_vn

logger = logging.getLogger("state")

STATE_VERSION = 1


class StateCorruptError(RuntimeError):
    """
    File state TỒN TẠI nhưng không đọc/parse được (JSON hỏng, không phải
    object JSON, lỗi parse nội dung). FAIL-CLOSED: bot PHẢI dừng lại thay vì
    dùng state trắng (state trắng = quên bài đã đăng, quota về 0 -> đăng lại
    tin cũ và ghi đè mất file state thật).
    """
    pass

# Giới hạn lịch sử để file state không phình vô hạn.
MAX_HISTORY_POSTS = 400
MAX_HISTORY_DAYS = 30

# Giới hạn bộ nhớ bài bị AI loại (mỗi lần chạy tối đa ~6 mục; 300 mục ~ 2 tháng).
MAX_AI_REJECTED = 300


def _parse_iso(value):
    """Parse chuỗi ISO8601 (có/không timezone) -> datetime, hoặc None."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _backup_path(path):
    """
    Trả về đường dẫn backup CHƯA tồn tại cho file `path` hỏng:
    path + ".corrupt", rồi ".corrupt.1", ".corrupt.2", ...
    -> KHÔNG BAO GIỜ ghi đè backup của lần hỏng trước đó.
    """
    backup = path + ".corrupt"
    idx = 1
    while os.path.exists(backup):
        backup = "%s.corrupt.%d" % (path, idx)
        idx += 1
    return backup


def _remove_quietly(path):
    """Xóa file (dùng để dọn file tạm ghi dở). Lỗi -> chỉ log, không ném."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        logger.warning("[STATE] Không dọn được file tạm %s: %r", path, exc)


class NewsState:
    """Đại diện cho file state/news_state.json."""

    def __init__(self):
        self.version = STATE_VERSION
        self.last_run = None
        # {"2026-10-23": 1, ...} - đếm số bài THƯỜNG theo ngày (giờ VN).
        self.daily_posts = {}
        # Tương tự, đếm số bài NÓNG theo ngày (file cũ không có -> {}).
        self.daily_hot_posts = {}
        # Danh sách bài đã đăng, mới nhất ở cuối.
        self.posts = []
        # Danh sách bài AI đã THỰC SỰ đánh giá rồi loại (xem docstring).
        self.ai_rejected = []

    # ==================================================================
    # LOAD / SAVE
    # ==================================================================

    @classmethod
    def _fail_corrupt(cls, path, reason, cause=None):
        """
        Bước bắt buộc khi file state CÓ nhưng hỏng (FAIL-CLOSED, B1):
        1. Backup sang tên CHƯA trùng (.corrupt / .corrupt.1 / .corrupt.2 ...)
           -> không ghi đè backup của lần hỏng trước.
        2. RAISE StateCorruptError với thông điệp cách khắc phục (tiếng Việt).
        KHÔNG BAO GIỜ trả state trắng khi file có mà hỏng.
        """
        backup = None
        try:
            backup = _backup_path(path)
            shutil.copyfile(path, backup)
        except Exception as exc2:
            logger.error("[STATE] Không backup được file hỏng %s (%r).",
                         path, exc2)
        if backup:
            logger.error("[STATE] File state hỏng (%s). Đã backup sang %s.",
                         reason, backup)
        else:
            logger.error("[STATE] File state hỏng (%s) và không backup được.",
                         reason)
        msg = (
            "File state %s bị hỏng: %s. "
            "Đã backup (nếu được) sang: %s. "
            "CÁCH KHẮC PHỤC (chạy TRÊN MÁY CÁ NHÂN, KHÔNG phải trên GitHub "
            "runner - actions/checkout mặc định chỉ clone 1 commit nên "
            "HEAD~1 không dùng được): "
            "1) chạy 'git log --oneline -- %s' để tìm commit còn file state "
            "tốt; 2) 'git checkout <mã commit> -- %s'; 3) commit và push. "
            "HOẶC xóa file %s để bot bắt đầu mới từ đầu - "
            "CẢNH BÁO: bot sẽ quên toàn bộ bài đã từng đăng."
            % (path, reason, backup or "không tạo được", path, path, path)
        )
        if cause is not None:
            raise StateCorruptError(msg) from cause
        raise StateCorruptError(msg)

    @classmethod
    def load(cls, path):
        """
        Đọc state từ file JSON.
        - File CHƯA tồn tại -> state trống (không crash).
        - File CÓ nhưng hỏng (JSON hỏng, không phải object JSON, lỗi parse
          nội dung) -> backup sang .corrupt rồi RAISE StateCorruptError.
          FAIL-CLOSED: KHÔNG trả state trắng -> không quên bài đã đăng,
          không reset quota, không đăng lại tin cũ, không ghi đè file thật.
        - File CŨ (thiếu trường ai_rejected) -> ai_rejected = [] (tương thích ngược).
        """
        state = cls()
        if not path or not os.path.exists(path):
            logger.info("[STATE] File chưa tồn tại (%s), bắt đầu trống.", path)
            return state

        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (ValueError, OSError) as exc:
            cls._fail_corrupt(path, "không đọc/parse được JSON (%r)" % exc,
                              exc)

        if not isinstance(data, dict):
            cls._fail_corrupt(path,
                              "không phải object JSON (kiểu %s)"
                              % type(data).__name__)

        try:
            state.version = int(data.get("version", STATE_VERSION))
            state.last_run = data.get("last_run")

            raw_daily = data.get("daily_posts") or {}
            if isinstance(raw_daily, dict):
                for key, value in raw_daily.items():
                    try:
                        state.daily_posts[str(key)] = int(value)
                    except (TypeError, ValueError):
                        continue

            # Tương thích ngược: file cũ thiếu "daily_hot_posts" -> {}.
            raw_daily_hot = data.get("daily_hot_posts") or {}
            if isinstance(raw_daily_hot, dict):
                for key, value in raw_daily_hot.items():
                    try:
                        state.daily_hot_posts[str(key)] = int(value)
                    except (TypeError, ValueError):
                        continue

            raw_posts = data.get("posts") or []
            if isinstance(raw_posts, list):
                for item in raw_posts[-MAX_HISTORY_POSTS:]:
                    if isinstance(item, dict):
                        state.posts.append(item)

            # Tương thích ngược: file cũ thiếu "ai_rejected" -> [].
            raw_rejected = data.get("ai_rejected") or []
            if isinstance(raw_rejected, list):
                for item in raw_rejected[-MAX_AI_REJECTED:]:
                    if isinstance(item, dict):
                        state.ai_rejected.append(item)

            logger.info("[STATE] Loaded: %d bài đã đăng, %d bài bị AI loại, "
                        "lịch sử từ %s.",
                        len(state.posts), len(state.ai_rejected),
                        state.last_run or "chưa rõ")
        except Exception as exc:
            # Lỗi parse NỘI DUNG (B1) -> cũng fail-closed, không bắt đầu mới.
            cls._fail_corrupt(path, "lỗi khi parse nội dung state (%r)" % exc,
                              exc)

        # Dọn mục hết hạn ngay khi load (TTL = MAX_ARTICLE_AGE_HOURS).
        state._prune_ai_rejected()
        return state

    def to_dict(self):
        """ serialize ra dict để ghi JSON."""
        return {
            "version": STATE_VERSION,
            "last_run": self.last_run,
            "daily_posts": dict(self.daily_posts),
            "daily_hot_posts": dict(self.daily_hot_posts),
            "posts": list(self.posts[-MAX_HISTORY_POSTS:]),
            "ai_rejected": list(self.ai_rejected[-MAX_AI_REJECTED:]),
        }

    @staticmethod
    def _same_ignoring_last_run(old_text, new_text):
        """
        True nếu 2 bản JSON giống hệt nhau SAU KHI bỏ trường "last_run".
        Dùng để không ghi file (và workflow không commit) chỉ vì last_run đổi
        sau mỗi lần chạy (C4).
        """
        try:
            old_obj = json.loads(old_text)
            new_obj = json.loads(new_text)
        except ValueError:
            return False
        if not isinstance(old_obj, dict) or not isinstance(new_obj, dict):
            return False
        old_obj.pop("last_run", None)
        new_obj.pop("last_run", None)
        return old_obj == new_obj

    def save(self, path):
        """
        Ghi state ra file JSON.
        Trả về True nếu CÓ ghi file, False nếu nội dung không đổi
        (để workflow không commit/push vô ích).
        Ghi file LỖI -> RAISE exception (không nuốt): main.record_and_save
        bắt được và exit 1 - fail-closed, KHÔNG để lỗi lưu state âm thầm
        (âm thầm = lần chạy sau bot quên bài đã đăng -> đăng lại tin cũ).

        C4: nội dung CHỈ khác mỗi "last_run" cũng coi là không đổi -> KHÔNG ghi.
        """
        self._prune()
        self._prune_ai_rejected()

        new_text = json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"

        # Không ghi lại nếu nội dung y hệt (tránh commit không cần thiết).
        if os.path.exists(path):
            old_text = None
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    old_text = fh.read()
            except OSError:
                old_text = None

            if old_text is not None:
                if old_text == new_text:
                    logger.info("[STATE] Không có thay đổi.")
                    return False
                if self._same_ignoring_last_run(old_text, new_text):
                    logger.info(
                        "[STATE] Chỉ trường last_run đổi -> KHÔNG ghi file "
                        "(tránh commit/push vô ích).")
                    return False

        # B3: ghi NGUYÊN TỬ - ghi ra file tạm CÙNG thư mục rồi os.replace
        # -> không bao giờ để lại file state ghi dở; lỗi thì dọn file tạm.
        tmp_path = path + ".tmp"
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, path)
            logger.info("[STATE] Saved: %d bài, %d ngày, %d bài bị AI loại.",
                        len(self.posts), len(self.daily_posts),
                        len(self.ai_rejected))
            return True
        except Exception as exc:
            logger.error("[STATE] Error khi ghi %s: %r", path, exc)
            # Dọn file tạm ghi dở; file state cũ (nếu có) vẫn còn nguyên vẹn.
            _remove_quietly(tmp_path)
            # RAISE LẠI (KHÔNG return False): lỗi lưu state là NGHIÊM TRỌNG -
            # nếu nuốt lỗi thì main sẽ exit 0, workflow không biết state
            # chưa được ghi -> lần chạy sau bot QUÊN bài vừa đăng và
            # CÓ THỂ ĐĂNG LẠI TIN CŨ. main.record_and_save bắt lỗi này.
            raise

    def _prune(self):
        """Bỏ các ngày cũ và cắt lịch sử để file state không phình."""
        # Bỏ daily_posts / daily_hot_posts quá cũ (so với key "YYYY-MM-DD").
        for bucket in (self.daily_posts, self.daily_hot_posts):
            try:
                sorted_days = sorted(bucket.keys())
                if len(sorted_days) > MAX_HISTORY_DAYS:
                    for old_day in sorted_days[:-MAX_HISTORY_DAYS]:
                        bucket.pop(old_day, None)
            except Exception:
                pass

        # Cắt danh sách posts.
        if len(self.posts) > MAX_HISTORY_POSTS:
            self.posts = self.posts[-MAX_HISTORY_POSTS:]

    def _prune_ai_rejected(self):
        """
        Dọn bộ nhớ bài bị AI loại:
        - Bỏ mục quá TTL (MAX_ARTICLE_AGE_HOURS tính từ rejected_at).
        - Bỏ mục thiếu trường dữ liệu.
        - Cắt còn MAX_AI_REJECTED mục mới nhất.
        """
        if not self.ai_rejected:
            return

        try:
            ref = now_vn()
        except Exception:
            ref = None

        cutoff = None
        if ref is not None:
            cutoff = ref - timedelta(hours=MAX_ARTICLE_AGE_HOURS)

        kept = []
        dropped = 0
        for item in self.ai_rejected:
            if not isinstance(item, dict):
                dropped += 1
                continue
            link = item.get("link")
            fingerprint = item.get("fingerprint")
            if not link and not fingerprint:
                dropped += 1
                continue

            rejected_at = _parse_iso(item.get("rejected_at"))
            if rejected_at is None:
                # Không đọc được thời điểm -> giữ lại (an toàn, không mất dữ liệu).
                kept.append(item)
                continue
            if cutoff is not None:
                try:
                    # Ép cùng kiểu so sánh aware/naive nếu file cũ lưu thiếu tz.
                    if rejected_at.tzinfo is None and cutoff.tzinfo is not None:
                        rejected_at = rejected_at.replace(tzinfo=cutoff.tzinfo)
                    if rejected_at < cutoff:
                        dropped += 1
                        continue
                except Exception:
                    dropped += 1
                    continue
            kept.append(item)

        if dropped:
            logger.info("[STATE] Dọn %d mục bài bị AI loại hết hạn/thiếu dữ liệu.",
                        dropped)

        if len(kept) > MAX_AI_REJECTED:
            kept = kept[-MAX_AI_REJECTED:]

        self.ai_rejected = kept

    # ==================================================================
    # TRUY VẤN (được analyzer.py gọi)
    # ==================================================================

    def posts_today(self, day):
        """
        Số bài THƯỜNG đã đăng trong ngày `day` ("YYYY-MM-DD" theo giờ VN).
        Chỉ đếm bài có kind != "hot" -> analyzer.select_articles GIỮ NGUYÊN.
        (Bản ghi cũ không có "kind" -> coi là "normal" nên vẫn đếm đúng.)
        Đếm trực tiếp từ danh sách posts (kind là nguồn sự thật về loại bài).
        """
        return self._count_kind(day, hot=False)

    def posts_in_window(self, day, start_hour, end_hour):
        """
        Số bài THƯỜNG đã đăng trong ngày `day` mà giờ đăng (giờ VN, lấy từ
        trường posted_at) nằm trong khung [start_hour, end_hour).
        Dùng cho quota theo khung sáng/trưa/chiều. Không bao giờ đếm bài hot.
        FAIL-CLOSED: bản ghi không đọc được posted_at -> coi như nằm trong khung
        (tính vào quota khung) để không đăng dư.
        """
        total = 0
        for item in self.posts:
            if not isinstance(item, dict) or item.get("day") != day:
                continue
            if str(item.get("kind") or "normal").lower() == "hot":
                continue
            try:
                hour = datetime.fromisoformat(str(item.get("posted_at"))).hour
            except (TypeError, ValueError):
                total += 1
                continue
            if start_hour <= hour < end_hour:
                total += 1
        return total

    def hot_posts_today(self, day):
        """
        Số bài NÓNG đã đăng trong ngày `day` ("YYYY-MM-DD" theo giờ VN).
        File cũ không có bản ghi kind="hot" -> 0.
        """
        return self._count_kind(day, hot=True)

    def _count_kind(self, day, hot):
        """Đếm số bản ghi trong ngày `day` khớp loại bài (hot/normal)."""
        total = 0
        for item in self.posts:
            if not isinstance(item, dict):
                continue
            if item.get("day") != day:
                continue
            entry_is_hot = str(item.get("kind") or "normal").lower() == "hot"
            if entry_is_hot == bool(hot):
                total += 1
        return total

    def is_posted_link(self, link):
        """True nếu link này đã từng đăng."""
        if not link:
            return False
        for item in self.posts:
            if item.get("link") == link:
                return True
        return False

    def is_posted_fingerprint(self, fingerprint):
        """True nếu fingerprint này đã từng đăng."""
        if not fingerprint:
            return False
        for item in self.posts:
            if item.get("fingerprint") == fingerprint:
                return True
        return False

    def is_posted_title(self, title):
        """
        True nếu title này (hoặc bài CÙNG SỰ KIỆN) đã từng đăng.
        So theo sự kiện (is_same_event) chứ không so text y hệt,
        để bắt các nguồn khác nhau viết title khác nhau cho cùng vụ việc.
        """
        if not title:
            return False
        norm = normalize_title(title)
        for item in self.posts:
            stored = item.get("title") or ""
            if not stored:
                continue
            if normalize_title(stored) == norm:
                return True
            try:
                if is_same_event(title, stored):
                    return True
            except Exception:
                continue
        return False

    # ==================================================================
    # BỘ NHỚ "BÀI BỊ AI LOẠI" (D2)
    # ==================================================================

    def is_ai_rejected(self, scored, now=None, hot_only=False):
        """
        True nếu bài này (theo link HOẶC fingerprint) đã từng bị AI THỰC SỰ
        đánh giá rồi loại và còn trong TTL.
        - scope "all"     : luôn True (chặn ở mọi chế độ).
        - scope "hot_only": CHỈ True khi hot_only=True (đang xét chế độ tin
          nóng khi quota thường đã đầy); xét tin thường thì KHÔNG chặn.
        - Mục cũ không có scope -> coi là "all" (tương thích ngược).
        `scored`: analyzer.ScoredArticle (dùng getattr để an toàn).
        """
        if not self.ai_rejected:
            return False

        link = getattr(scored, "link", "") or ""
        fingerprint = getattr(scored, "fingerprint", "") or ""
        if not link and not fingerprint:
            return False

        for item in self.ai_rejected:
            if not isinstance(item, dict):
                continue
            match = False
            if link and item.get("link") == link:
                match = True
            if fingerprint and item.get("fingerprint") == fingerprint:
                match = True
            if not match:
                continue

            scope = str(item.get("scope") or "all").lower()
            if scope == "all":
                return True
            if scope == "hot_only" and hot_only:
                return True
            # scope "hot_only" mà đang xét tin thường -> không chặn.
        return False

    def record_ai_rejection(self, scored, now=None, scope="all"):
        """
        Ghi nhận một bài vừa bị AI THỰC SỰ đánh giá rồi loại.
        - scope "all"     : AI LOẠI bài (publish=false hoặc score < AI_MIN_SCORE).
        - scope "hot_only": AI DUYỆT nhưng bài KHÔNG nóng khi quota thường đã đầy
                            (ngày mai vẫn xét lại như tin thường).
        Chỉ ghi khi AI đã thật sự đánh giá (không ghi khi AI lỗi/fallback).
        Ghi đè thời điểm nếu bài đã có trong bộ nhớ và KHÔNG hạ cấp scope
        ("all" đã ghi thì "hot_only" không làm mất đi).
        `scored`: analyzer.ScoredArticle, `now`: thời điểm (mặc định now_vn()).
        """
        link = getattr(scored, "link", "") or ""
        fingerprint = getattr(scored, "fingerprint", "") or ""
        if not link and not fingerprint:
            return

        scope = "hot_only" if str(scope or "all").lower() == "hot_only" else "all"

        ref = now or now_vn()
        try:
            rejected_at = ref.isoformat()
        except Exception:
            rejected_at = format_vn_time(ref)

        # Cập nhật nếu đã tồn tại (gia hạn TTL), không thêm trùng.
        for item in self.ai_rejected:
            if not isinstance(item, dict):
                continue
            same_link = link and item.get("link") == link
            same_fp = fingerprint and item.get("fingerprint") == fingerprint
            if same_link or same_fp:
                item["rejected_at"] = rejected_at
                # Giữ scope rộng nhất: "all" không bao giờ bị hạ thành
                # "hot_only"; "hot_only" thì nâng lên "all" được.
                if scope == "all" or str(item.get("scope") or "all").lower() != "all":
                    item["scope"] = scope
                return

        self.ai_rejected.append({
            "link": link,
            "fingerprint": fingerprint,
            "rejected_at": rejected_at,
            "scope": scope,
        })

        # Cắt bớt nếu vượt giới hạn.
        if len(self.ai_rejected) > MAX_AI_REJECTED:
            self.ai_rejected = self.ai_rejected[-MAX_AI_REJECTED:]

        logger.info("[STATE] Ghi nhận bài bị AI loại (scope=%s): %s",
                    scope, (getattr(scored, "title", "") or link)[:60])

    # ==================================================================
    # GHI NHẬN
    # ==================================================================

    def record_post(self, scored, day, kind="normal"):
        """
        Ghi nhận một bài vừa đăng thành công.
        `scored` là analyzer.ScoredArticle, `day` là today_key theo giờ VN,
        `kind` là "normal" (tin thường) hoặc "hot" (tin nóng - C1).
        Bản ghi cũ không có trường "kind" được đọc lại như "normal".
        """
        kind = "hot" if str(kind or "normal").lower() == "hot" else "normal"

        entry = {
            "title": getattr(scored, "title", "") or "",
            "link": getattr(scored, "link", "") or "",
            "source": getattr(scored, "source", "") or "",
            "score": int(getattr(scored, "score", 0) or 0),
            "fingerprint": getattr(scored, "fingerprint", "") or "",
            "day": day,
            "posted_at": now_vn().isoformat(),
            "kind": kind,
        }
        self.posts.append(entry)

        if kind == "hot":
            # Quota THƯỜNG không bị đếm -> MAX_POSTS_PER_DAY giữ nguyên ý nghĩa.
            self.daily_hot_posts[day] = self.hot_posts_today(day)
            logger.info(
                "[STATE] Ghi nhận tin NÓNG: [%d] %s (ngày %s, nóng %d/%d hôm nay)",
                entry["score"], entry["title"][:60], day,
                self.hot_posts_today(day), MAX_HOT_POSTS_PER_DAY)
        else:
            self.daily_posts[day] = self.posts_today(day)
            logger.info(
                "[STATE] Ghi nhận tin thường: [%d] %s (ngày %s, thường %d/%d hôm nay)",
                entry["score"], entry["title"][:60], day,
                self.posts_today(day), MAX_POSTS_PER_DAY)

    def mark_last_run(self, now=None):
        """Đánh dấu thời điểm chạy gần nhất."""
        ref = now or now_vn()
        try:
            self.last_run = ref.isoformat()
        except Exception:
            self.last_run = format_vn_time(ref)
