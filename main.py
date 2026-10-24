# -*- coding: utf-8 -*-
"""
main.py - Điều phối toàn bộ bot chạy MỘT LẦN rồi thoát (dùng cho GitHub Actions).

Luồng:
1. Đọc state (bài đã đăng, quota theo ngày, bộ nhớ bài bị AI loại).
2. GATE QUOTA: tính quota CÒN LẠI = tin THƯỜNG (3 bài/ngày: mỗi khung
   sáng/trưa/chiều 1 bài hot nhất) + tin NÓNG (MAX_HOT_POSTS_PER_DAY). Hết cả hai
   -> trả về NGAY, KHÔNG fetch RSS, KHÔNG gọi AI.
3. Fetch toàn bộ RSS (collector.py).
4. Chấm điểm (analyzer.analyze_articles) + tính corroboration
   (hot.annotate_corroboration) TRÊN DANH SÁCH CHƯA BỊ DEDUP.
5. Lọc rule -> candidate. Ở chế độ hot-only (hết quota thường) chỉ giữ bài
   is_hot_rule và bỏ bài is_ai_rejected(hot_only=True).
6. AI EDITOR (ai_editor.review_candidates): AI filter + AI viết tiếng Việt +
   CẤP SLOT hot/normal. AI lỗi/tắt -> fallback: main tự cấp slot (ai_used=False).
7. KIỂM TRA LẠI BẰNG CODE:
   - slot "normal" -> analyzer.select_articles (threshold + duplicate + state +
     quota MAX_POSTS_PER_DAY) - GIỮ NGUYÊN như cũ.
   - slot "hot"    -> hot.select_hot_articles (is_hot_rule + duplicate + state +
     quota MAX_HOT_POSTS_PER_DAY).
8. Đăng lên Discord: bài NÓNG trước, bài THƯỜNG sau.
9. Ghi state: record_post(..., kind="hot"/"normal") + bộ nhớ bài AI loại theo
   scope ("all"/"hot_only"), lưu CẢ KHI không đăng nhưng có bài mới bị loại.
   DRY_RUN KHÔNG ghi state.
10. Thoát (workflow commit/push state).

KHÔNG có while True. KHÔNG cần server 24/7.
AI KHÔNG BAO GIỜ được vượt quota / threshold / duplicate / state.
"""

import logging
import os
import sys
import traceback

from config import (
    MAX_POSTS_PER_SLOT,
    NORMAL_POST_SLOTS,
    AI_CANDIDATE_MIN_SCORE,
    DISCORD_BOT_TOKEN_ENV,
    DISCORD_CHANNEL_ID,
    DRY_RUN,
    HOT_ENABLED,
    LOG_LEVEL,
    MAX_HOT_POSTS_PER_DAY,
    MAX_POSTS_PER_DAY,
    MIN_SCORE_TO_POST,
    RSS_SOURCES,
    STATE_FILE,
)
from collector import collect_articles
from analyzer import (
    analyze_articles,
    is_duplicate_of_any,
    is_already_posted,
    meets_threshold,
    select_articles,
)
from ai_editor import get_max_candidates, review_candidates
from hot import (
    allocate_slots,
    annotate_corroboration,
    hot_rule_explain,
    is_hot_rule,
    select_hot_articles,
)
from state import NewsState
from discord_poster import post_all
from textutils import format_vn_time, now_vn, today_key

# =====================================================================
# LOGGING
# =====================================================================

def setup_logging():
    """Cấu hình logging ra console theo LOG_LEVEL trong config."""
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)-7s] %(name)-10s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,   # Python 3.8+: ghi đè cấu hình cũ nếu có
    )
    # Đảm bảo log không in token ra console.
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)


logger = logging.getLogger("main")


# =====================================================================
# BƯỚC 1: ĐỌC STATE
# =====================================================================

def load_state():
    """
    Đọc state từ file.

    FAIL-CLOSED (B2):
    - File state CÓ nhưng hỏng / không đọc được (StateCorruptError hoặc lỗi
      đọc khác) -> RAISE lại, KHÔNG bao giờ dùng state trắng (state trắng sẽ
      làm bot quên bài đã đăng, quota về 0 -> đăng lại tin cũ và ghi đè file).
    - Chỉ khi file CHƯA tồn tại mới trả NewsState() trống.

    run_once gọi hàm này BÊN TRONG try -> lỗi này thành exit code 1,
    KHÔNG fetch RSS, KHÔNG gọi AI, KHÔNG đăng Discord, KHÔNG ghi đè state.
    """
    logger.info("[STATE] Đọc state từ: %s", STATE_FILE)
    try:
        state = NewsState.load(STATE_FILE)
    except Exception as exc:
        if os.path.exists(STATE_FILE):
            logger.error("[STATE] State hỏng/không đọc được (%r) -> DỪNG CHẠY "
                         "(fail-closed, KHÔNG dùng state trống).", exc)
            raise
        logger.warning("[STATE] Lỗi khi bắt đầu với state trống (%r).", exc)
        return NewsState()
    logger.info("[STATE] Đã tải: %d bài đã đăng trước đó, %d bài bị AI loại.",
                len(state.posts), len(state.ai_rejected))
    return state


# =====================================================================
# QUOTA CÒN LẠI (E1) - cách tính GIỐNG analyzer.select_articles
# =====================================================================

def get_current_slot(now):
    """
    Trả về (tên, giờ bắt đầu, giờ kết thúc) của khung giờ đăng tin THƯỜNG
    chứa thời điểm `now` (giờ VN), hoặc None nếu đang ngoài mọi khung.
    """
    hour = now.hour
    for name, start_hour, end_hour in NORMAL_POST_SLOTS:
        if start_hour <= hour < end_hour:
            return name, start_hour, end_hour
    return None


def compute_quota(state, now):
    """
    Trả về (remaining_normal, remaining_hot):
    - remaining_normal (tin THƯỜNG) = min(còn quota ngày, còn quota KHUNG GIỜ):
        + quota ngày  = MAX_POSTS_PER_DAY - state.posts_today(day)
        + quota khung = MAX_POSTS_PER_SLOT - số bài thường đã đăng trong khung
          sáng/trưa/chiều hiện tại.
      Ngoài mọi khung giờ -> 0 (chỉ xét tin nóng). Mỗi lần chạy vì vậy chỉ
      đăng tối đa 1 bài thường: bài hot nhất của khung.
      (posts_today KHÔNG đếm bài kind="hot".)
    - remaining_hot = max(0, MAX_HOT_POSTS_PER_DAY - state.hot_posts_today(day))
      nếu HOT_ENABLED, ngược lại 0. LOGIC TIN NÓNG GIỮ NGUYÊN.

    FAIL-CLOSED (A3): không đọc được số bài đã đăng -> coi như HẾT quota
    (an toàn) -> KHÔNG đăng thêm. Trường hợp state là None (chưa có file)
    vẫn cho quota đầy đủ như cũ.
    """
    day = today_key(now)

    slot = get_current_slot(now)
    if slot is None:
        remaining_normal = 0
        logger.info("[SLOT] %02d:%02d - ngoài khung giờ sáng/trưa/chiều -> "
                    "không đăng tin thường, chỉ xét tin nóng.",
                    now.hour, now.minute)
    else:
        slot_name, slot_start, slot_end = slot
        try:
            posted_day = state.posts_today(day) if state is not None else 0
            posted_slot = (state.posts_in_window(day, slot_start, slot_end)
                           if state is not None else 0)
        except Exception as exc:
            logger.warning("[STATE] Lỗi đọc quota thường (%r) -> coi như hết "
                           "quota (an toàn).", exc)
            posted_day = MAX_POSTS_PER_DAY
            posted_slot = MAX_POSTS_PER_SLOT
        remaining_normal = max(0, min(MAX_POSTS_PER_DAY - posted_day,
                                      MAX_POSTS_PER_SLOT - posted_slot))
        logger.info("[SLOT] Khung %s (%02d:00-%02d:00): đã đăng %d/%d trong khung, "
                    "%d/%d trong ngày -> còn %d bài thường.",
                    slot_name, slot_start, slot_end,
                    posted_slot, MAX_POSTS_PER_SLOT,
                    posted_day, MAX_POSTS_PER_DAY, remaining_normal)

    if not HOT_ENABLED:
        return remaining_normal, 0

    try:
        posted_hot = state.hot_posts_today(day) if state is not None else 0
    except Exception as exc:
        logger.warning("[STATE] Lỗi đọc hot_posts_today (%r) -> coi như hết "
                       "quota (an toàn).", exc)
        posted_hot = MAX_HOT_POSTS_PER_DAY
    remaining_hot = max(0, MAX_HOT_POSTS_PER_DAY - posted_hot)

    return remaining_normal, remaining_hot


# =====================================================================
# BƯỚC 5: LỌC RULE -> CANDIDATE
# =====================================================================

def build_rule_candidates(scored, state, now=None, hot_only=False):
    """
    Lọc các bài đạt ngưỡng và chưa từng đăng, giữ thứ tự điểm giảm dần.

    hot_only=True (KHÔNG ĐỔI - giữ nguyên hành vi cũ):
    - meets_threshold: điểm >= 85, hoặc 70-84 kèm yếu tố đặc biệt.
    - BẮT BUỘC is_hot_rule (E3) - lọc TRƯỚC khi cắt AI_MAX_CANDIDATES
      nên không bỏ lọt bài nóng vì hết suất gửi AI.

    hot_only=False (MỞ RỘNG cho AI rescue):
    - meets_threshold(s) HOẶC s.score >= AI_CANDIDATE_MIN_SCORE (45):
      bài 45-84 (chưa đạt ngưỡng đăng) vẫn được GỬI AI xem xét.
    - Sắp xếp trước khi cắt theo (meets_threshold giảm dần, score giảm dần)
      -> bài ĐẠT ngưỡng rule luôn chiếm suất gửi AI trước, bài 45-84 chỉ
      lấy suất còn trống.

    Chung (cả hai nhánh):
    - Chưa từng đăng (state: link/fingerprint/title).
    - CHƯA nằm trong bộ nhớ "đã bị AI loại":
        + hot_only=False (còn quota thường) -> bỏ bài scope "all".
        + hot_only=True  (chỉ còn quota nóng) -> bỏ bài scope "all" VÀ "hot_only".
    - Không trùng sự kiện với bài đã chọn trước đó trong lần này.

    Cắt danh sách còn AI_MAX_CANDIDATES để payload gửi AI không quá dài.
    """
    limit = get_max_candidates()
    picked = []
    skipped_rejected = 0
    skipped_not_hot = 0

    if hot_only:
        # Nhánh hot: GIỮ NGUYÊN - vẫn bắt buộc meets_threshold + is_hot_rule,
        # không sort lại (scored đã sort điểm giảm dần từ analyze_articles).
        pool = scored
    else:
        # Nhánh thường: ưu tiên bài ĐẠT ngưỡng rule, rồi tới điểm giảm dần.
        pool = sorted(
            scored,
            key=lambda s: (1 if meets_threshold(s) else 0, s.score),
            reverse=True,
        )

    for s in pool:
        if hot_only:
            if not meets_threshold(s):
                continue
        else:
            if not (meets_threshold(s) or s.score >= AI_CANDIDATE_MIN_SCORE):
                continue
        if is_already_posted(s, state):
            continue
        try:
            if state is not None and state.is_ai_rejected(s, now, hot_only=hot_only):
                skipped_rejected += 1
                continue
        except Exception as exc:
            logger.warning("[STATE] Lỗi kiểm tra bộ nhớ AI loại (%r).", exc)
        if hot_only and not is_hot_rule(s, now):
            skipped_not_hot += 1
            continue
        if is_duplicate_of_any(s, picked):
            continue
        picked.append(s)
        if len(picked) >= limit:
            break

    logger.info("[FILTER] Số bài đạt ngưỡng rule (trước AI, tối đa %d): %d "
                "(bỏ %d bài từng bị AI loại%s)",
                limit, len(picked), skipped_rejected,
                ", %d bài không đạt hot_rule" % skipped_not_hot if hot_only else "")
    return picked


# =====================================================================
# HỖ TRỢ: ĐẢM BÀI ĐÃ CÓ SLOT (nhánh fallback của AI)
# =====================================================================

def ensure_slots(scored_list, remaining_normal, remaining_hot, now):
    """
    Bài đã qua review_candidates:
    - AI thật sự chạy -> đã được cấp slot ("hot"/"normal") -> giữ nguyên.
    - AI lỗi/tắt (fallback trả nguyên candidates, slot còn "none")
      -> main tự cấp slot với ai_used=False (is_hot lúc đó phụ thuộc
      HOT_ALLOW_WITHOUT_AI + corroboration >= 3).

    Trả về list scored đã có slot (bài hot đứng trước).
    """
    items = list(scored_list or [])
    if not items:
        return []
    if all(getattr(s, "slot", "none") != "none" for s in items):
        return items

    pairs = [(s, getattr(s, "ai_decision", None)) for s in items]
    return allocate_slots(pairs, remaining_normal=remaining_normal,
                          remaining_hot=remaining_hot, now=now, ai_used=False)


# =====================================================================
# E6: LOG TỪNG CANDIDATE ĐỂ CHỈNH NGƯỠNG BẰNG DỮ LIỆU THẬT
# =====================================================================

def log_hot_table(candidates, now=None):
    """In MỘT dòng [HOT] cho TỪNG candidate (kể cả bài không được chọn)."""
    for s in candidates:
        try:
            ok, fails = hot_rule_explain(s, now)
        except Exception as exc:
            ok, fails = False, ["lỗi hot_rule_explain: %r" % exc]

        age = getattr(getattr(s, "article", None), "age_hours", None)
        age_s = "?" if age is None else "%.1f" % age

        decision = getattr(s, "ai_decision", None)
        if decision is None:
            ai_part = "AI hot=? severity=? score=?"
        else:
            ai_part = "AI hot=%s severity=%s score=%s" % (
                bool(getattr(decision, "hot", False)),
                getattr(decision, "severity", "?"),
                int(getattr(decision, "score", 0) or 0),
            )

        fail_s = ""
        if fails:
            fail_s = " (%s)" % "; ".join(fails)

        try:
            logger.info(
                "[HOT] %s | score=%d age=%sh special=%s sources=%d vn=%s "
                "-> rule_hot=%s%s | %s -> slot=%s",
                (getattr(s, "title", "") or "")[:60],
                int(getattr(s, "score", 0) or 0),
                age_s,
                bool(getattr(s, "is_special", False)),
                int(getattr(s, "corroboration", 0) or 0),
                bool(getattr(s, "is_vietnam", False)),
                ok, fail_s, ai_part,
                getattr(s, "slot", "none"),
            )
        except Exception as exc:
            logger.warning("[HOT] Lỗi in log candidate: %r", exc)


# =====================================================================
# BƯỚC 2-7: THU THẬP + PHÂN TÍCH + LỌC RULE + AI + CẤP SLOT
# =====================================================================

def gather_and_select(state, now):
    """
    Gate quota -> thu thập RSS -> chấm điểm -> tính corroboration -> lọc rule
    -> AI đánh giá + viết lại + cấp slot -> kiểm tra lại bằng code.

    Trả về (selected, rejected):
    - selected: List[ScoredArticle] cần đăng (bài NÓNG đứng trước),
      tổng không bao giờ vượt remaining_normal + remaining_hot.
    - rejected: List[ScoredArticle] AI THỰC SẼ đánh giá rồi loại
      (main.py ghi vào bộ nhớ "đã bị AI loại" với scope từ .ai_rejected_scope).
    """
    day = today_key(now)
    remaining_normal, remaining_hot = compute_quota(state, now)
    logger.info("[GATE] Ngày %s: quota thường còn %d/%d, quota nóng còn %d/%d "
                "(HOT_ENABLED=%s)",
                day, remaining_normal, MAX_POSTS_PER_DAY,
                remaining_hot, MAX_HOT_POSTS_PER_DAY, HOT_ENABLED)

    # E3 - GATE 1: hết cả hai loại quota -> về NGAY, không fetch, không AI.
    if remaining_normal <= 0 and remaining_hot <= 0:
        logger.info("[GATE] Hết quota thường lẫn quota nóng -> kết thúc, không gọi AI.")
        return [], []

    hot_only = remaining_normal <= 0
    if hot_only:
        logger.info("[GATE] Hết quota THƯỜNG -> chỉ xét bài đạt is_hot_rule.")

    logger.info("[RSS] Bắt đầu thu thập tin từ %d nguồn...", len(RSS_SOURCES))
    articles = collect_articles(now)
    logger.info("[RSS] Thu được %d bài sau khi lọc.", len(articles))

    if not articles:
        logger.warning("[RSS] Không có bài nào từ RSS. Kết thúc sớm.")
        return [], []

    logger.info("[SCORE] Bắt đầu chấm điểm %d bài...", len(articles))
    scored = analyze_articles(articles, now)
    logger.info("[SCORE] Hoàn tất. Số bài đạt điểm: %d",
                sum(1 for s in scored if s.score >= MIN_SCORE_TO_POST))

    # E2/B1: tính corroboration NGAY SAU khi chấm điểm - analyze_articles
    # KHÔNG dedup bên trong nên đây là danh sách CHƯA bị dedup.
    annotate_corroboration(scored)

    # --- Lọc rule -> candidate (hot-only: chỉ bài is_hot_rule) ---
    candidates = build_rule_candidates(scored, state, now, hot_only=hot_only)
    if not candidates:
        logger.info("[FILTER] Không còn candidate -> kết thúc, KHÔNG gọi AI.")
        return [], []

    # --- AI FILTER + AI WRITER + CẤP SLOT (fallback graceful nếu AI lỗi/tắt) ---
    ai_selected = review_candidates(candidates, now,
                                    max_posts=remaining_normal,
                                    hot_slots=remaining_hot)
    to_post = ensure_slots(ai_selected, remaining_normal, remaining_hot, now)

    # Bài AI THỰC SỰ đánh giá rồi loại (gắn cờ .ai_rejected trong ai_editor).
    rejected = [s for s in candidates if getattr(s, "ai_rejected", False)]

    normal_list = [s for s in to_post if getattr(s, "slot", "") == "normal"]
    hot_list = [s for s in to_post if getattr(s, "slot", "") == "hot"]

    # --- KIỂM TRA LẠI BẰNG CODE (AI không được bỏ qua) ---
    logger.info("[FILTER] Kiểm tra lại bằng code (threshold=%d, quota thường %d, "
                "quota nóng %d)...",
                MIN_SCORE_TO_POST, remaining_normal, remaining_hot)
    # select_articles chỉ biết quota NGÀY -> cắt thêm theo quota KHUNG GIỜ.
    normal_selected = select_articles(normal_list, state, now)[:remaining_normal]
    hot_selected = select_hot_articles(hot_list, state, now,
                                       already_selected=normal_selected)

    # E4: đăng bài NÓNG trước, bài THƯỜNG sau.
    selected = list(hot_selected) + list(normal_selected)

    # E6: mỗi candidate một dòng để chỉnh ngưỡng bằng dữ liệu thật.
    log_hot_table(candidates, now)

    logger.info("[FILTER] Đã chọn %d bài (nóng %d, thường %d), AI loại %d bài.",
                len(selected), len(hot_selected), len(normal_selected),
                len(rejected))
    return selected, rejected


# =====================================================================
# BƯỚC 8: ĐĂNG LÊN DISCORD
# =====================================================================

def post_to_discord(scored_list):
    """
    Đăng các bài đã chọn lên Discord Forum Channel.
    Trả về: List[dict] kết quả từ post_all (mỗi phần tử {"ok", "thread_id", "error"}).
    """
    if not scored_list:
        return []

    # Lấy Discord token từ env (KHÔNG hard-code).
    token = os.environ.get(DISCORD_BOT_TOKEN_ENV, "").strip()

    if not DRY_RUN and not token:
        logger.error(
            "[DISCORD] Thiếu environment variable %s. "
            "Không thể đăng bài. Hãy set token trong GitHub Secrets.",
            DISCORD_BOT_TOKEN_ENV
        )
        return [{"ok": False, "thread_id": None,
                 "error": "Thiếu %s" % DISCORD_BOT_TOKEN_ENV,
                 "config_error": True}]

    logger.info("[DISCORD] Bắt đầu đăng %d bài vào channel %s (dry_run=%s)...",
                len(scored_list), DISCORD_CHANNEL_ID, DRY_RUN)

    # Chạy async post_all trong sync context.
    import asyncio
    try:
        results = asyncio.run(
            post_all(token, DISCORD_CHANNEL_ID, scored_list, dry_run=DRY_RUN)
        )
    except RuntimeError as exc:
        # asyncio.run() lỗi nếu đã có running loop (hiếm gặp trong CI).
        logger.error("[DISCORD] Lỗi asyncio: %r", exc)
        results = [{"ok": False, "thread_id": None, "error": repr(exc)}]
    except Exception as exc:
        logger.error("[DISCORD] Lỗi không xác định khi đăng: %r", exc)
        logger.debug(traceback.format_exc())
        results = [{"ok": False, "thread_id": None, "error": repr(exc)}]

    # Log kết quả.
    success = sum(1 for r in results if r.get("ok"))
    failed = len(results) - success
    logger.info("[DISCORD] Kết quả: %d thành công, %d thất bại.", success, failed)

    return results


# =====================================================================
# BƯỚC 9: GHI NHẬN + LƯU STATE
# =====================================================================

def record_and_save(state, scored_list, results, day, rejected=None):
    """
    Ghi nhận các bài ĐĂNG THÀNH CÔNG (kind theo slot: "hot"/"normal") +
    các bài AI THỰC SỰ đã loại (scope "all"/"hot_only") vào state, rồi lưu file.

    - Chỉ tính quota những bài đăng thật sự thành công.
    - rejected: bài AI thật sự đánh giá rồi loại -> ghi bộ nhớ. KHÔNG ghi khi
      AI lỗi/tắt (không có trong danh sách).
    - Lưu state CẢ KHI không đăng bài nào nhưng có bài mới bị AI loại.

    Trả về True khi state được lưu ĐÚNG (hoặc không có gì để đổi),
    False khi LỖI LƯU (state.save ném lỗi) - run_once exit 1 để
    GitHub Actions báo đỏ, vì lần chạy sau bot có thể đăng lại tin cũ.
    """
    # (a) Bài đăng thành công - E5: kind lấy theo slot của bài.
    for scored, result in zip(scored_list, results):
        if result.get("ok"):
            try:
                kind = getattr(scored, "slot", "normal")
                state.record_post(scored, day, kind=kind)
                logger.info("[STATE] Đã ghi nhận bài (%s): %s",
                            kind, scored.title[:60])
            except Exception as exc:
                logger.error("[STATE] Lỗi ghi nhận bài (%r): %r",
                             scored.title[:60], exc)

    # (b) Bài bị AI thật sự đánh giá rồi loại (scope do ai_editor gắn - D5).
    for scored in (rejected or []):
        try:
            scope = getattr(scored, "ai_rejected_scope", "all") or "all"
            state.record_ai_rejection(scored, now_vn(), scope=scope)
        except Exception as exc:
            logger.error("[STATE] Lỗi ghi bài bị AI loại (%r): %r",
                         getattr(scored, "title", "?"), exc)

    # Lưu state ra file (kể cả khi không có bài đăng mới nhưng có bài bị loại).
    logger.info("[STATE] Lưu state vào: %s", STATE_FILE)
    try:
        state.mark_last_run(now_vn())
        saved = state.save(STATE_FILE)
        if saved:
            logger.info("[STATE] Đã lưu state thành công.")
        else:
            logger.info("[STATE] Không có thay đổi, không cần commit.")
        return True
    except Exception as exc:
        posted_titles = [getattr(s, "title", "?")[:80]
                         for s, r in zip(scored_list, results)
                         if r.get("ok")]
        logger.error(
            "[STATE] LỖI LƯU STATE (%r): file %s KHÔNG được ghi -> "
            "các bài vừa đăng CHƯA được ghi nhận, CÓ THỂ BỊ ĐĂNG LẠI Ở LẦN "
            "CHẠY SAU, HÃY KIỂM TRA NGAY. Bài vừa đăng có thể bị lặp: %s",
            exc, STATE_FILE,
            posted_titles if posted_titles else "(không có bài nào đăng thành công)")
        logger.debug(traceback.format_exc())
        return False


def log_quota_summary(state, day, dry_run=False):
    """E6: log quota cuối lần chạy - đã dùng / còn, cả hai loại."""
    try:
        used_normal = state.posts_today(day)
        used_hot = state.hot_posts_today(day)
    except Exception as exc:
        logger.warning("[QUOTA] Lỗi đọc quota: %r", exc)
        return
    suffix = " (DRY_RUN: chưa ghi state)" if dry_run else ""
    logger.info(
        "[QUOTA] Tin THƯỜNG: %d/%d đã dùng, còn %d | Tin NÓNG: %d/%d đã dùng, "
        "còn %d | HOT_ENABLED=%s%s",
        used_normal, MAX_POSTS_PER_DAY, max(0, MAX_POSTS_PER_DAY - used_normal),
        used_hot, MAX_HOT_POSTS_PER_DAY, max(0, MAX_HOT_POSTS_PER_DAY - used_hot),
        HOT_ENABLED, suffix,
    )


# =====================================================================
# ENTRY POINT
# =====================================================================

def run_once():
    """Chạy bot MỘT LẦN hoàn chỉnh. Trả về exit code (0 = OK)."""
    setup_logging()
    now = now_vn()
    day = today_key(now)

    logger.info("=" * 60)
    logger.info("BẮT ĐẦU CHẠY BOT - %s (giờ VN)", format_vn_time(now))
    logger.info("Ngày (VN): %s | Quota: %d thường + %d nóng /ngày | Threshold: %d",
                day, MAX_POSTS_PER_DAY, MAX_HOT_POSTS_PER_DAY, MIN_SCORE_TO_POST)
    logger.info("Dry_run: %s | HOT_ENABLED: %s", DRY_RUN, HOT_ENABLED)
    logger.info("=" * 60)

    exit_code = 0

    try:
        # Bước 1: Đọc state.
        state = load_state()

        # Bước 2-7: Gate quota + thu thập + chấm điểm + lọc rule + AI + slot.
        selected, rejected = gather_and_select(state, now)

        # Bước 8: Đăng Discord (nếu có bài) - bài hot nằm đầu danh sách.
        results = post_to_discord(selected)

        # Bước 9: Ghi nhận + lưu state.
        # DRY_RUN KHÔNG được ghi state: bài chưa đăng thật, mà ghi thì
        # quota/dedup/bộ nhớ AI loại của lần chạy THẬT sẽ bị sai.
        if DRY_RUN:
            logger.info("[STATE] DRY_RUN=1: không ghi state (bài chưa đăng thật).")
        else:
            saved_ok = record_and_save(state, selected, results, day, rejected)
            if not saved_ok:
                # State CHƯA được ghi -> exit 1 để GitHub Actions báo đỏ;
                # lý do + cảnh báo đăng lại đã được record_and_save log.
                exit_code = 1

        # Lỗi cấu hình (thiếu token) -> exit code 1 để GitHub Actions báo lỗi,
        # nhưng state vẫn đã được lưu ở bước trên.
        if any(r.get("config_error") for r in results):
            exit_code = 1

        logger.info("=" * 60)
        logger.info("KẾT THÚC CHẠY BOT - Đăng %d/%d bài (nóng %d), AI loại %d bài",
                    sum(1 for r in results if r.get("ok")), len(selected),
                    sum(1 for s in selected if getattr(s, "slot", "") == "hot"),
                    len(rejected))
        log_quota_summary(state, day, dry_run=DRY_RUN)
        logger.info("=" * 60)

    except KeyboardInterrupt:
        logger.warning("Bị ngắt by người dùng.")
        exit_code = 130
    except Exception as exc:
        logger.error("LỖI NGHIÊM TRỌNG: %r", exc)
        logger.error(traceback.format_exc())
        exit_code = 1

    return exit_code


def main():
    """Entry point chính."""
    return run_once()


if __name__ == "__main__":
    sys.exit(main())
