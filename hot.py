# -*- coding: utf-8 -*-
"""
hot.py - Logic TIN NÓNG (hot news): tính corroboration, xét ngưỡng nóng,
cấp slot và chọn bài nóng để đăng.

NGUYÊN TẮC IMPORT (bắt buộc):
- CHỈ import config + textutils (và thư viện chuẩn) - KHÔNG import
  ai_editor / main / discord_poster -> không thể có import vòng.
- KHÔNG import analyzer: các kiểm tra trùng sự kiện được tái hiện NGUYÊN LOGIC
  bằng primitive trong textutils (is_same_event / entity_overlap /
  title_similarity) như analyzer.is_duplicate_of_any đang dùng.

CÁC HÀM CHÍNH:
- annotate_corroboration(scored_list) : gắn scored.corroboration (B1)
- is_hot_rule / hot_rule_explain      : xét nóng theo RULE (B2) - HAI ĐƯỜNG:
                                        đường classic (score>=95+special+nguồn)
                                        HOẶC đường hard_signal (tuổi<=12h +
                                        nguồn theo loại nguồn: security/vn >=1,
                                        crypto/general >=2)
- is_hot_ai                           : xét nóng theo quyết định AI (B3)
- is_hot                              : kết hợp rule + AI (B4) + đường dự
                                        phòng B5 (AI tắt, hard_signal >=2 nguồn)
- allocate_slots                      : cấp slot hot/normal (B5)
- select_hot_articles                 : kiểm tra lại bằng code (B6)

NGUYÊN TẮC AN TOÀN:
- Hàm thuần (không I/O, không network, không token).
- Không bao giờ vượt quota: allocate_slots tự cắt theo remaining_*;
  select_hot_articles tự cắt theo MAX_HOT_POSTS_PER_DAY - đã đăng.
- Không có tin nóng -> không cấp slot hot, KHÔNG đăng cho đủ quota.
- FAIL-CLOSED: lỗi đọc state (đã đăng? còn quota?) -> coi như ĐÃ ĐĂNG /
  HẾT quota -> KHÔNG đăng, KHÔNG bao giờ coi lỗi là "chưa đăng".
"""

import logging

from config import (
    HOT_AI_MIN_SCORE,
    HOT_ALLOW_WITHOUT_AI,
    HOT_ENABLED,
    HOT_HARD_MIN_SOURCES_KINDS,
    HOT_MAX_AGE_HOURS,
    HOT_MIN_SCORE,
    HOT_MIN_SOURCES_INTL,
    HOT_MIN_SOURCES_VN,
    HOT_MIN_SOURCES_WITHOUT_AI,
    MAX_HOT_POSTS_PER_DAY,
    MIN_SCORE_TO_POST,
    SOURCE_KIND,
    SPECIAL_MIN_SCORE,
)
from textutils import (
    entity_overlap,
    is_same_event,
    now_vn,
    title_similarity,
    today_key,
)

logger = logging.getLogger("hot")

# Giá trị của scored.slot.
SLOT_HOT = "hot"
SLOT_NORMAL = "normal"
SLOT_NONE = "none"

# Hằng HOT_MIN_SOURCES_WITHOUT_AI (= 3) đã chuyển sang config.py (C2),
# hot.py chỉ import lại - không còn khai báo trùng ở đây.


# =====================================================================
# B1. CORROBORATION (số nguồn RSS khác nhau cùng đưa một sự kiện)
# =====================================================================

def _meets_threshold(scored):
    """
    Đúng y hệt analyzer.meets_threshold (hot.py không import analyzer):
    - score >= MIN_SCORE_TO_POST -> True
    - SPECIAL_MIN_SCORE <= score < MIN_SCORE_TO_POST -> True nếu is_special
    """
    try:
        score = int(getattr(scored, "score", 0) or 0)
    except (TypeError, ValueError):
        score = 0
    if score >= MIN_SCORE_TO_POST:
        return True
    if score >= SPECIAL_MIN_SCORE and getattr(scored, "is_special", False):
        return True
    return False


def annotate_corroboration(scored_list):
    """
    Với MỖI bài đạt meets_threshold HOẶC CÓ hard_signal (B2 - bài hard_signal
    cần corroboration để xét nóng dù điểm rule chưa đạt), đếm số TÊN NGUỒN
    khác nhau (kể cả nguồn của chính bài đó) trong TOÀN BỘ scored_list mà
    is_same_event(...) trả True (title + summary, đúng chữ ký
    analyzer.is_duplicate_of_any đang dùng). Gắn scored.corroboration (int).
    Bài không đạt threshold và không có hard_signal -> corroboration = 0.

    BẮT BUỘC chạy trên danh sách CHƯA bị dedup:
    - analyzer.analyze_articles KHÔNG dedup bên trong (nó chỉ chấm điểm + sort);
      dedup xảy ra ở build_rule_candidates / select_articles (các bước SAU).
    -> main.py gọi hàm này NGAY SAU analyze_articles, TRƯỚC mọi bước dedup.

    Trả về danh sách đã gắn (list[int] không đổi, không sort lại).
    """
    items = list(scored_list or [])
    if not items:
        return items

    # Summary của từng bài (dùng để so mức rộng hơn, như is_duplicate_of_any).
    summaries = []
    for item in items:
        try:
            summaries.append(
                getattr(getattr(item, "article", None), "summary", "") or "")
        except Exception:
            summaries.append("")

    for i, scored in enumerate(items):
        try:
            if (not _meets_threshold(scored)
                    and not getattr(scored, "hard_signal", False)):
                scored.corroboration = 0
                continue

            own_source = str(getattr(scored, "source", "") or "").strip()
            sources = {own_source} if own_source else set()
            title = getattr(scored, "title", "") or ""

            for j, other in enumerate(items):
                if other is scored:
                    continue
                try:
                    same = is_same_event(
                        title,
                        getattr(other, "title", "") or "",
                        summaries[i],
                        summaries[j],
                    )
                except Exception:
                    continue
                if same:
                    other_source = str(
                        getattr(other, "source", "") or "").strip()
                    if other_source:
                        sources.add(other_source)

            scored.corroboration = len(sources)
        except Exception as exc:
            logger.warning("[HOT] Lỗi tính corroboration %r: %r",
                           getattr(scored, "title", "?"), exc)
            # An toàn: coi như chỉ có nguồn của chính nó.
            scored.corroboration = 1

    return items


# =====================================================================
# B2. XÉT NÓNG THEO RULE
# =====================================================================

def hot_rule_explain(scored, now=None):
    """
    Trả về (is_hot: bool, fails: list[str]) - điều kiện "nóng theo RULE" (B2):
    là HAI ĐƯỜNG, đạt ĐƯỜNG NÀO cũng được:

    - Đường CLASSIC: score >= HOT_MIN_SCORE VÀ is_special VÀ đủ nguồn
      (>= HOT_MIN_SOURCES_INTL, riêng tin Việt Nam >= HOT_MIN_SOURCES_VN).
    - Đường HARD SIGNAL: có hard_signal VÀ tuổi <= HOT_MAX_AGE_HOURS VÀ
      corroboration đủ theo LOẠI NGUỒN:
        + nguồn báo an ninh / báo VN uy tín (SOURCE_KIND security/vn)
          -> cần >= 1 nguồn;
        + nguồn crypto / tổng hợp (SOURCE_KIND crypto/general)
          -> cần >= 2 nguồn.

    Cả hai đường đều đòi hỏi tuổi <= HOT_MAX_AGE_HOURS (bài cũ không nóng).
    - is_hot = True khi đạt ít nhất 1 đường.
    - fails  = danh sách lý do KHÔNG đạt (rỗng khi đạt) -> dùng cho log;
      select_hot_articles dùng CÙNG hàm này -> không mâu thuẫn code.

    Hàm thuần, không I/O.
    """
    fails = []

    if not HOT_ENABLED:
        fails.append("HOT_ENABLED=0")
        return False, fails

    # --- Tuổi bài (cả hai đường đều cần) ---
    article = getattr(scored, "article", None)
    age = getattr(article, "age_hours", None)
    age_ok = True
    if age is None:
        age_ok = False
        fails.append("age=? (không rõ thời gian)")
    else:
        try:
            age_val = float(age)
        except (TypeError, ValueError):
            age_val = None
        if age_val is None:
            age_ok = False
            fails.append("age=? (không đọc được)")
        elif age_val > HOT_MAX_AGE_HOURS:
            age_ok = False
            fails.append("age=%.1fh > %dh" % (age_val, HOT_MAX_AGE_HOURS))

    # --- Điểm rule / special / số nguồn ---
    try:
        score = int(getattr(scored, "score", 0) or 0)
    except (TypeError, ValueError):
        score = 0
    try:
        sources = int(getattr(scored, "corroboration", 0) or 0)
    except (TypeError, ValueError):
        sources = 0
    special = bool(getattr(scored, "is_special", False))
    hard = bool(getattr(scored, "hard_signal", False))

    # Ngưỡng nguồn cho đường CLASSIC (nguồn riêng cho tin Việt Nam).
    need_classic = HOT_MIN_SOURCES_VN if getattr(scored, "is_vietnam", False) \
        else HOT_MIN_SOURCES_INTL
    # Ngưỡng nguồn cho đường HARD SIGNAL - theo LOẠI NGUỒN (B2):
    # báo an ninh uy tín cần >= 1; crypto/tổng hợp cần >= 2
    # (HOT_HARD_MIN_SOURCES_KINDS trong config - tách riêng với
    #  SOURCE_REQUIRES_HARD_SIGNAL vốn đã thêm "vn" cho cổng pipeline).
    src_kind = SOURCE_KIND.get(getattr(scored, "source", "") or "", "security")
    need_hard = 2 if src_kind in HOT_HARD_MIN_SOURCES_KINDS else 1

    classic_fails = []
    if score < HOT_MIN_SCORE:
        classic_fails.append("score=%d < %d" % (score, HOT_MIN_SCORE))
    if not special:
        classic_fails.append("special=False")
    if sources < need_classic:
        classic_fails.append("sources=%d < %d" % (sources, need_classic))

    hard_fails = []
    if not hard:
        hard_fails.append("hard_signal=False")
    elif sources < need_hard:
        hard_fails.append("hard: sources=%d < %d (nguồn %s)"
                          % (sources, need_hard, src_kind))

    classic_ok = age_ok and not classic_fails
    hard_ok = age_ok and not hard_fails

    if classic_ok or hard_ok:
        return True, []

    fails.extend(classic_fails)
    fails.extend(hard_fails)
    return False, fails


def is_hot_rule(scored, now=None):
    """True khi bài ĐẠT toàn bộ điều kiện nóng theo RULE (B2)."""
    ok, _fails = hot_rule_explain(scored, now)
    return ok


# =====================================================================
# B3/B4. KẾT HỢP RULE + AI
# =====================================================================

def is_hot_ai(decision):
    """
    True khi quyết định của AI xác nhận bài là tin nóng:
    hot == True AND severity == "critical" AND score >= HOT_AI_MIN_SCORE.
    """
    if decision is None:
        return False
    if not getattr(decision, "hot", False):
        return False
    severity = str(getattr(decision, "severity", "") or "").strip().lower()
    if severity != "critical":
        return False
    try:
        ai_score = int(getattr(decision, "score", 0) or 0)
    except (TypeError, ValueError):
        ai_score = 0
    return ai_score >= HOT_AI_MIN_SCORE


def is_hot(scored, decision=None, now=None, ai_used=False):
    """
    Bài có phải TIN NÓNG không:
    - Đã qua RULE (is_hot_rule) trong MỌI trường hợp.
    - Có AI (ai_used=True)  -> phải thêm is_hot_ai(decision).
    - Không có AI (AI tắt/lỗi):
        + B5 (đường dự phòng sự kiện cực lớn): bài hard_signal có
          >= 2 nguồn độc lập -> VẪN là tin nóng, ĐẶC BIỆT kể cả khi
          HOT_ALLOW_WITHOUT_AI=0. Có log rõ khi dùng đường này.
        + Còn lại -> chỉ được nóng khi HOT_ALLOW_WITHOUT_AI bật
          VÀ corroboration >= HOT_MIN_SOURCES_WITHOUT_AI.
    """
    if not is_hot_rule(scored, now):
        return False
    if ai_used:
        return is_hot_ai(decision)

    try:
        sources = int(getattr(scored, "corroboration", 0) or 0)
    except (TypeError, ValueError):
        sources = 0

    # B5: đường dự phòng cho sự kiện cực lớn khi AI tắt/lỗi.
    if getattr(scored, "hard_signal", False) and sources >= 2:
        logger.info(
            "[HOT] AI TẮT/LỖI: bài hard_signal có %d nguồn độc lập -> "
            "xét như tin nóng (đường dự phòng B5, bất kể "
            "HOT_ALLOW_WITHOUT_AI=%s): %s",
            sources, HOT_ALLOW_WITHOUT_AI,
            (getattr(scored, "title", "") or "")[:80])
        return True

    if not HOT_ALLOW_WITHOUT_AI:
        return False
    return sources >= HOT_MIN_SOURCES_WITHOUT_AI


# =====================================================================
# B5. CẤP SLOT (hàm thuần, không I/O)
# =====================================================================

def allocate_slots(approved, remaining_normal, remaining_hot,
                   now=None, ai_used=True):
    """
    Cấp slot đăng bài cho danh sách đã SẮP THEO THỨ TỰ ƯU TIÊN.

    Tham số:
    - approved         : list[(scored, decision)] (AI path) hoặc
                         [(scored, None)] (fallback path).
    - remaining_normal : số slot TIN THƯỜNG còn lại.
    - remaining_hot    : số slot TIN NÓNG còn lại.
    - ai_used          : True nếu có AI thật sự đánh giá (ảnh hưởng is_hot).

    Thuật toán:
      a. tách hot_items (is_hot) / others.
      b. cấp slot "hot" cho tối đa remaining_hot bài đầu của hot_items.
      c. hot_items còn dư + others lần lượt cấp slot "normal" tới khi hết quota.
      d. gắn scored.slot ("hot"/"normal"/"none") và scored.is_hot
         (True kể cả khi bài nóng nằm ở slot normal).

    Trả về: list scored ĐÃ CẤP SLOT, bài hot đứng trước.
    KHÔNG BAO GIỜ vượt remaining_normal + remaining_hot.
    """
    pairs = list(approved or [])

    try:
        normal_left = max(0, int(remaining_normal or 0))
    except (TypeError, ValueError):
        normal_left = 0
    try:
        hot_left = max(0, int(remaining_hot or 0))
    except (TypeError, ValueError):
        hot_left = 0

    # C1: giữ TỔNG slot thường nhận được TRƯỚC khi trừ dần trong vòng lặp,
    # để log in đúng mẫu số (giá trị gốc) thay vì số còn lại.
    normal_total = normal_left

    hot_items = []
    others = []

    for pair in pairs:
        scored = pair[0]
        decision = pair[1] if len(pair) > 1 else None
        try:
            hot_flag = is_hot(scored, decision, now, ai_used)
        except Exception as exc:
            logger.warning("[HOT] Lỗi is_hot %r: %r -> coi như không nóng",
                           getattr(scored, "title", "?"), exc)
            hot_flag = False
        scored.is_hot = hot_flag
        scored.slot = SLOT_NONE          # reset trước khi cấp
        (hot_items if hot_flag else others).append(pair)

    result = []

    # b. slot "hot" cho bài nóng (tối đa remaining_hot).
    for pair in hot_items[:hot_left]:
        scored = pair[0]
        scored.slot = SLOT_HOT
        result.append(scored)

    # c. slot "normal" cho bài nóng dư + bài thường (giữ thứ tự ưu tiên).
    for pair in (hot_items[hot_left:] + others):
        if normal_left <= 0:
            break
        scored = pair[0]
        scored.slot = SLOT_NORMAL
        result.append(scored)
        normal_left -= 1

    # Các bài còn lại KHÔNG được cấp slot (slot giữ "none") -> không đăng.
    logger.info(
        "[HOT] Cấp slot: %d bài (nóng %d/%d, thường %d/%d) từ %d bài đạt "
        "| nóng chờ slot: %d",
        len(result),
        sum(1 for s in result if s.slot == SLOT_HOT), hot_left,
        normal_total - normal_left, normal_total,
        len(pairs),
        max(0, len(hot_items) - hot_left),
    )
    return result


# =====================================================================
# B6. KIỂM TRA LẠI BẰNG CODE CHO BÀI SLOT HOT
# =====================================================================

def _is_already_posted(scored, state):
    """Đúng như analyzer.is_already_posted: link / fingerprint / title (sự kiện)."""
    if state is None:
        return False
    link = getattr(scored, "link", "") or ""
    fingerprint = getattr(scored, "fingerprint", "") or ""
    title = getattr(scored, "title", "") or ""
    try:
        if link and state.is_posted_link(link):
            logger.info("[HOT] Đã đăng rồi (link): %s", title[:80])
            return True
        if fingerprint and state.is_posted_fingerprint(fingerprint):
            logger.info("[HOT] Đã đăng rồi (fingerprint): %s", title[:80])
            return True
        if title and state.is_posted_title(title):
            logger.info("[HOT] Đã đăng rồi (title): %s", title[:80])
            return True
    except Exception as exc:
        # FAIL-CLOSED (A1): không kiểm tra được state -> coi như ĐÃ ĐĂNG.
        logger.warning("[HOT] Lỗi kiểm tra state (%r) -> coi như ĐÃ ĐĂNG, "
                       "không đăng lại.", exc)
        return True
    return False


def _is_duplicate_of_any(scored, accepted):
    """Đúng như analyzer.is_duplicate_of_any (dùng primitive của textutils)."""
    cur_summary = getattr(getattr(scored, "article", None), "summary", "") or ""
    for prev in accepted:
        if getattr(scored, "link", "") and scored.link == getattr(prev, "link", ""):
            logger.info("[HOT] Trùng URL: %s", getattr(scored, "title", "")[:80])
            return True
        if (getattr(scored, "fingerprint", "")
                and scored.fingerprint == getattr(prev, "fingerprint", "")):
            logger.info("[HOT] Trùng fingerprint: %s",
                        getattr(scored, "title", "")[:80])
            return True
        prev_summary = getattr(getattr(prev, "article", None), "summary", "") or ""
        try:
            if is_same_event(getattr(scored, "title", ""),
                             getattr(prev, "title", ""),
                             cur_summary, prev_summary):
                logger.info("[HOT] Cùng sự kiện: %s ~ %s",
                            getattr(scored, "title", "")[:60],
                            getattr(prev, "title", "")[:60])
                return True
            if (entity_overlap(getattr(scored, "title", ""),
                               getattr(prev, "title", "")) >= 2
                    and title_similarity(getattr(scored, "title", ""),
                                         getattr(prev, "title", "")) >= 0.45):
                logger.info("[HOT] Cùng sự kiện (entity): %s ~ %s",
                            getattr(scored, "title", "")[:60],
                            getattr(prev, "title", "")[:60])
                return True
        except Exception:
            continue
    return False


def select_hot_articles(hot_list, state, now=None, already_selected=None):
    """
    Chọn tối đa MAX_HOT_POSTS_PER_DAY bài slot "hot" để đăng.
    KIỂM TRA LẠI BẰNG CODE (AI không được bỏ qua):
    1. slot phải là "hot".
    2. is_hot_rule còn đúng tại thời điểm đăng.
    3. Chưa từng đăng (state: link / fingerprint / title).
    4. Không trùng sự kiện với already_selected và với các bài hot vừa chọn.
    5. Còn quota nóng: max(0, MAX_HOT_POSTS_PER_DAY - hot_posts_today(day)).

    Trả về List[ScoredArticle] (tối đa quota nóng còn lại).
    """
    ref = now or now_vn()
    day = today_key(ref)

    try:
        used = int(state.hot_posts_today(day)) if state is not None else 0
    except Exception as exc:
        # FAIL-CLOSED (A2): không đọc được quota nóng -> coi như ĐÃ HẾT quota.
        logger.warning("[HOT] Lỗi đọc hot_posts_today (%r) -> coi như hết "
                       "quota nóng (an toàn).", exc)
        used = MAX_HOT_POSTS_PER_DAY

    remaining = max(0, MAX_HOT_POSTS_PER_DAY - used)
    logger.info("[HOT] Ngày %s: đã đăng %d/%d tin nóng, còn quota %d",
                day, used, MAX_HOT_POSTS_PER_DAY, remaining)

    pool = list(already_selected or [])
    accepted = []

    for scored in (hot_list or []):
        if remaining <= 0:
            break

        if getattr(scored, "slot", SLOT_NONE) != SLOT_HOT:
            continue

        ok, fails = hot_rule_explain(scored, ref)
        if not ok:
            logger.info("[HOT] Bỏ (hết đạt hot_rule): %s | %s",
                        (getattr(scored, "title", "") or "")[:60],
                        "; ".join(fails))
            continue

        if _is_already_posted(scored, state):
            continue

        if _is_duplicate_of_any(scored, pool):
            continue

        accepted.append(scored)
        pool.append(scored)
        remaining -= 1
        logger.info("[HOT] CHỌN tin nóng (%d slot còn): [%d] %s",
                    remaining + 1, getattr(scored, "score", 0),
                    (getattr(scored, "title", "") or "")[:80])

    if not accepted:
        logger.info("[HOT] Không có tin nóng nào qua được kiểm tra lại.")

    return accepted
