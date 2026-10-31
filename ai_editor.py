# -*- coding: utf-8 -*-
"""
ai_editor.py - Lớp trung gian giữa analyzer.py và discord_poster.py.

Luồng (KHÔNG bỏ qua bất kỳ bước kiểm tra code nào):
    candidate từ analyzer (đã qua threshold/state/dup/không bị AI loại trước đó)
        -> AI FILTER/SELECTOR  (chọn 0..max_posts bài, max_posts = số quota
                                còn lại hôm nay do main.py truyền vào)
        -> AI NEWS WRITER      (viết lại nội dung tiếng Việt, chỉ viết số bài
                                code cho phép -> không viết thừa rồi bị vứt)
        -> main.py vẫn chạy analyzer.select_articles() lần nữa
           (threshold + duplicate + state + quota MAX_POSTS_PER_DAY)
        -> discord_poster.py đăng bài (dùng nội dung AI nếu có)

THIẾT KẾ AN TOÀN:
- KHÔNG hard-code API key. Đọc env: AI_PROVIDER / AI_API_KEY / AI_MODEL /
  AI_BASE_URL / AI_TIMEOUT_SECONDS / AI_MAX_CANDIDATES.
- AI provider KHÔNG khả dụng (thiếu key, timeout, HTTP 429 quáquota, HTTP lỗi,
  JSON sai format) -> FALLBACK GRACEFUL: giữ nguyên kết quả rule-based,
  KHÔNG crash, KHÔNG mất trạng thái, bot vẫn chạy và vẫn đăng theo rule.
- Mọi lỗi đều được nuốt + log (không bao giờ log API key).
- AI KHÔNG được tự chọn quá max_posts bài (code cắt).
- AI KHÔNG được vượt threshold hay quota: các bước đó do main.py +
  analyzer.select_articles() đảm nhiệm.
- Bài AI THỰC SỰ đã đánh giá rồi loại -> gắn cờ .ai_rejected = True lên
  ScoredArticle để main.py ghi vào bộ nhớ "đã bị AI loại" (tiết kiệm quota
  những lần chạy sau). KHÔNG gắn cờ khi AI lỗi/tắt/fallback.

BỘ NHỚ "ĐÃ BỊ AI LOẠI" (xem state.py):
- main.py đọc/ghi qua state.is_ai_rejected(...) / state.record_ai_rejection(...).
- TTL = MAX_ARTICLE_AGE_HOURS: hết hạn thì bài được xét lại từ đầu.

CÁC BIẾN MÔI TRƯỜNG (xem .env.example):
    AI_PROVIDER      : "openai" | "groq" | "openrouter" | "ollama" | "custom"
                       rỗng / "off" / "none" -> TẮT AI (dùng rule-based thuần)
    AI_API_KEY       : khóa API (để trống với ollama local)
    AI_MODEL         : tên model, ví dụ "openai/gpt-oss-120b" (groq)
    AI_BASE_URL      : override endpoint (OpenAI-compatible /chat/completions)
    AI_TIMEOUT_SECONDS   (mặc định 45)
    AI_MAX_CANDIDATES    (mặc định 6 - số bài gửi cho AI đánh giá)

Provider miễn phí tham khảo (phải tự kiểm tra free tier hiện tại):
- groq       : api.groq.com - có free tier (rate limit ~1000 req/ngày).
- openrouter : openrouter.ai - một số model ":free".
- ollama     : chạy local, miễn phí hoàn toàn (không dùng trên GitHub Actions).
- openai     : THƯỜNG PHẢI TRẢ TIỀN - chỉ dùng nếu bạn có key.
Không có provider nào là bắt buộc. Bot chạy tốt khi AI bị tắt.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass

import requests

from config import (AI_RESCUE_MIN_SCORE, FORUM_TAG_NAMES, MAX_POSTS_PER_DAY,
                    MIN_SCORE_TO_POST, SPECIAL_MIN_SCORE)
# Cấp slot tin nóng (D4) + xét bài nóng (D5). hot.py chỉ import config/textutils
# -> không có import vòng.
from hot import allocate_slots, is_hot

logger = logging.getLogger("ai_editor")

# =====================================================================
# 0. HẰNG SỐ
# =====================================================================

# B1: bài AI trả publish=true nhưng score < mức này VẪN bị loại
# (AI "nể nang" cho điểm cao với tin thường -> chặn lại).
AI_MIN_SCORE = 75

# Số lần thử lại tối đa khi gặp HTTP 429 (xem _call_chat).
MAX_429_RETRIES = 3

# Nếu Retry-After > số giây này thì coi như HẾT QUOTA NGÀY, KHÔNG chờ.
RETRY_AFTER_GIVEUP_SECONDS = 30

# Mỗi lần chờ 429 tối đa bấy nhiêu giây (gói miễn phí hay giới hạn token/phút).
MAX_429_WAIT_SECONDS = 20

# Gửi AI theo lô nhỏ để không vượt giới hạn token/phút của gói miễn phí.
FILTER_BATCH_SIZE = 4
FILTER_BATCH_PAUSE_SECONDS = 2.0

# Model suy luận (gpt-oss...) tính cả token suy luận vào max_tokens -> nếu để
# quá nhỏ thì JSON bị cắt giữa chừng ("JSON sai format"). Cộng thêm khoảng đệm.
REASONING_TOKEN_HEADROOM = 1500


# =====================================================================
# 1. ĐỌC CẤU HÌNH TỪ ENV (không hard-code key)
# =====================================================================

AI_PROVIDER_ENV = "AI_PROVIDER"
AI_API_KEY_ENV = "AI_API_KEY"
AI_MODEL_ENV = "AI_MODEL"
AI_BASE_URL_ENV = "AI_BASE_URL"
AI_TIMEOUT_ENV = "AI_TIMEOUT_SECONDS"
AI_MAX_CANDIDATES_ENV = "AI_MAX_CANDIDATES"
AI_FALLBACK_MODEL_ENV = "AI_FALLBACK_MODEL"

# Endpoint OpenAI-compatible mặc định theo provider.
DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
    "custom": "",
}

# Model gợi ý theo provider (có thể đổi qua env AI_MODEL).
DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "groq": "openai/gpt-oss-120b",
    "openrouter": "meta-llama/llama-3.3-70b-instruct:free",
    "ollama": "llama3.1",
    "custom": "",
}

DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_MAX_CANDIDATES = 10


@dataclass
class AIConfig:
    """Cấu hình AI đọc từ environment (KHÔNG chứa key khi log ra ngoài)."""
    provider: str = ""
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    timeout: int = DEFAULT_TIMEOUT_SECONDS
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    # Model dự phòng (tùy chọn): thử một lần khi model chính bị 429/JSON hỏng.
    fallback_model: str = ""

    @property
    def enabled(self):
        """AI chỉ được coi là bật khi có provider + model + (key hoặc local)."""
        if self.provider in ("", "off", "none", "0", "false"):
            return False
        if not self.model:
            return False
        if not self.base_url:
            return False
        need_key = self.provider != "ollama"
        if need_key and not self.api_key:
            return False
        return True

    def describe(self):
        """Mô tả an toàn (KHÔNG chứa API key) để log."""
        return "provider=%s model=%s base=%s enabled=%s" % (
            self.provider or "-", self.model or "-",
            self.base_url or "-", self.enabled,
        )


def load_config():
    """Đọc cấu hình AI từ environment variables."""
    provider = os.environ.get(AI_PROVIDER_ENV, "").strip().lower()
    api_key = os.environ.get(AI_API_KEY_ENV, "").strip()
    model = os.environ.get(AI_MODEL_ENV, "").strip()
    base_url = os.environ.get(AI_BASE_URL_ENV, "").strip().rstrip("/")

    if provider == "none":
        provider = ""
    if provider and not base_url:
        base_url = DEFAULT_BASE_URLS.get(provider, "")
    if provider and not model:
        model = DEFAULT_MODELS.get(provider, "")

    try:
        timeout = int(os.environ.get(AI_TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    timeout = max(5, min(timeout, 120))  # luôn trong [5, 120] giây

    try:
        max_candidates = int(os.environ.get(
            AI_MAX_CANDIDATES_ENV, DEFAULT_MAX_CANDIDATES))
    except (TypeError, ValueError):
        max_candidates = DEFAULT_MAX_CANDIDATES
    max_candidates = max(1, min(max_candidates, 20))

    return AIConfig(
        provider=provider,
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout=timeout,
        max_candidates=max_candidates,
        fallback_model=os.environ.get(AI_FALLBACK_MODEL_ENV, "").strip(),
    )


def get_max_candidates():
    """Số bài tối đa gửi cho AI đánh giá (dùng để cắt candidate trong main.py)."""
    try:
        return load_config().max_candidates
    except Exception:
        return DEFAULT_MAX_CANDIDATES


# =====================================================================
# 2. GỌI API (OpenAI-compatible chat/completions qua requests)
# =====================================================================

def _parse_retry_after(response):
    """
    Đọc header Retry-After (số giây). Trả về float hoặc None nếu không có/
    không đọc được (header cũng có thể là HTTP-date - trường hợp đó coi như None).
    """
    raw = (response.headers or {}).get("Retry-After")
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _extract_content(response):
    """Lấy nội dung text từ response 200, hoặc None nếu rỗng/sai format."""
    try:
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            logger.warning("[AI] Response rỗng từ provider.")
            return None
        return content
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning("[AI] Không parse được response JSON của provider: %r", exc)
        return None


def _is_reasoning_model(model):
    """gpt-oss / o-series: có token suy luận, tính vào max_tokens."""
    m = (model or "").lower()
    return "gpt-oss" in m or "qwen3" in m


def _call_chat(cfg, system_prompt, user_prompt,
               max_tokens=2200, temperature=0.2, model=None):
    """
    Gọi POST {base_url}/chat/completions.
    Trả về nội dung text (str), hoặc None nếu lỗi.
    KHÔNG BAO GIỜ log API key.

    Xử lý đặc biệt (B2):
    - HTTP 429: chờ theo Retry-After rồi thử lại tối đa MAX_429_RETRIES lần.
      Nếu Retry-After > RETRY_AFTER_GIVEUP_SECONDS (hết quota ngày) -> KHÔNG
      chờ, trả None để fallback rule-based.
    - Provider groq/openai: gửi response_format={"type":"json_object"}.
      Nếu server trả 400 -> thử lại MỘT lần không có response_format.
    """
    url = cfg.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = "Bearer " + cfg.api_key

    use_model = model or cfg.model
    reasoning = _is_reasoning_model(use_model)
    if reasoning:
        max_tokens = max_tokens + REASONING_TOKEN_HEADROOM

    base_payload = {
        "model": use_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    wants_json_format = cfg.provider in ("groq", "openai")
    json_format_dropped = False
    # gpt-oss trên Groq: reasoning_effort=low để suy luận ngắn, đỡ tốn token.
    wants_reasoning_effort = reasoning and cfg.provider == "groq"
    reasoning_effort_dropped = False
    retries_429 = 0

    while True:
        payload = dict(base_payload)
        if wants_json_format and not json_format_dropped:
            payload["response_format"] = {"type": "json_object"}
        if wants_reasoning_effort and not reasoning_effort_dropped:
            payload["reasoning_effort"] = "low"

        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=cfg.timeout,
            )
        except requests.exceptions.Timeout:
            logger.warning("[AI] Timeout %ss khi gọi %s", cfg.timeout, cfg.provider)
            return None
        except requests.exceptions.RequestException as exc:
            logger.warning("[AI] Lỗi kết nối (%s): %s",
                           cfg.provider, str(exc)[:200])
            return None
        except Exception as exc:  # Không để bất kỳ lỗi nào crash bot.
            logger.warning("[AI] Lỗi không xác định khi gọi API: %r", exc)
            return None

        # --- 429: rate limit / hết quota ---
        if response.status_code == 429:
            retry_after = _parse_retry_after(response)

            # Hết quota ngày -> KHÔNG chờ, fallback rule-based ngay.
            if retry_after is not None and retry_after > RETRY_AFTER_GIVEUP_SECONDS:
                logger.warning(
                    "[AI] HTTP 429 với Retry-After=%.0fs (> %ds, hết quota) "
                    "-> không chờ, fallback rule-based.",
                    retry_after, RETRY_AFTER_GIVEUP_SECONDS)
                return None

            if retries_429 >= MAX_429_RETRIES:
                logger.warning("[AI] HTTP 429 sau %d lần thử lại -> fallback.",
                               MAX_429_RETRIES)
                return None

            # Không có Retry-After -> đợi một khoảng ngắn an toàn.
            wait_seconds = retry_after if retry_after is not None else 3.0
            wait_seconds = max(0.5, min(wait_seconds,
                                        float(MAX_429_WAIT_SECONDS)))
            retries_429 += 1
            logger.info("[AI] HTTP 429, chờ %.1fs rồi thử lại (%d/%d)...",
                        wait_seconds, retries_429, MAX_429_RETRIES)
            time.sleep(wait_seconds)
            continue

        # --- 400: có thể do response_format không được hỗ trợ ---
        if (response.status_code == 400 and wants_json_format
                and not json_format_dropped):
            logger.warning(
                "[AI] HTTP 400 từ %s (nghi do response_format) "
                "-> thử lại MỘT lần không có response_format. Chi tiết: %s",
                cfg.provider, response.text[:300])
            json_format_dropped = True
            continue
        if (response.status_code == 400 and wants_reasoning_effort
                and not reasoning_effort_dropped):
            logger.warning(
                "[AI] HTTP 400 từ %s (nghi do reasoning_effort) "
                "-> thử lại MỘT lần không có reasoning_effort. Chi tiết: %s",
                cfg.provider, response.text[:300])
            reasoning_effort_dropped = True
            continue

        if response.status_code != 200:
            # Chỉ log status + đoạn ngắn (body lỗi của provider không chứa key).
            logger.warning("[AI] HTTP %d từ %s: %s",
                           response.status_code, cfg.provider,
                           response.text[:300])
            return None

        return _extract_content(response)


# =====================================================================
# 3. PARSE JSON BỀN VỮNG (model hay bọc ```json ...)
# =====================================================================

def extract_json(text):
    """
    Lấy dict/list từ text của model.
    Chịu được: markdown code fence, chú thích trước/sau JSON, escape sai.
    Trả về object (dict/list) hoặc None.
    """
    if not text or not isinstance(text, str):
        return None
    t = text.strip()

    # Bỏ code fence ```json ... ```
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S | re.I)
    if fence:
        t = fence.group(1).strip()

    # Thử parse nguyên si.
    try:
        return json.loads(t)
    except ValueError:
        pass

    # Quét từng cặp ngoặc, tôn trọng chuỗi ký tự (tránh } trong "lý do").
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = t.find(open_ch)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(t)):
            ch = t[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    chunk = t[start:i + 1]
                    try:
                        return json.loads(chunk)
                    except ValueError:
                        break  # thử cặp ngoặc kế tiếp
    return None


# =====================================================================
# 4. QUYẾT ĐỊNH CỦA AI FILTER
# =====================================================================

# Lý do của placeholder (khi AI KHÔNG trả về quyết định cho một id).
PLACEHOLDER_REASON = "AI không trả về quyết định cho bài này"


@dataclass
class AIDecision:
    """Kết quả AI đánh giá MỘT bài (đúng format yêu cầu)."""
    id: int = -1
    publish: bool = False
    score: int = 0
    reason: str = ""
    category: str = "An ninh mạng"
    region: str = "Quốc tế"
    severity: str = "medium"
    # D3: bài có phải TIN NÓNG không (mặc định False; thiếu trường -> False).
    hot: bool = False
    # False = placeholder do code tạo (AI KHÔNG thật sự đánh giá bài này),
    # dùng để KHÔNG ghi vào bộ nhớ "đã bị AI loại" (yêu cầu D4).
    from_ai: bool = True


def _to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "có", "co", "ok")
    return False


def _to_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_decisions(data):
    """
    Chuyển JSON của AI thành List[AIDecision].
    JSON sai format -> trả [] (fallback sẽ chạy rule-based).
    """
    if isinstance(data, dict):
        items = (data.get("decisions") or data.get("results")
                 or data.get("items") or [])
    elif isinstance(data, list):
        items = data
    else:
        return []

    if not isinstance(items, list):
        return []

    decisions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        decision = AIDecision(
            id=_to_int(item.get("id"), -1),
            publish=_to_bool(item.get("publish")),
            score=max(0, min(100, _to_int(item.get("score"), 0))),
            reason=str(item.get("reason") or "").strip()[:500],
            category=str(item.get("category") or "An ninh mạng").strip()[:80],
            region=str(item.get("region") or "Quốc tế").strip()[:80],
            severity=str(item.get("severity") or "medium").strip().lower()[:20],
            # D3: đọc trường "hot" bằng _to_bool; thiếu -> False.
            hot=_to_bool(item.get("hot")),
            from_ai=True,
        )
        if decision.severity not in ("low", "medium", "high", "critical"):
            decision.severity = "medium"
        decisions.append(decision)
    return decisions


# =====================================================================
# 5. MAP CATEGORY / REGION -> FORUM TAG (chỉ dùng tag có sẵn)
# =====================================================================

# Matching theo TỪ KHÓA (không phân biệt hoa/thường) trong category của AI.
_CATEGORY_KEYWORDS = [
    ("lỗ hổng", "🔓 Lỗ hổng"),
    ("vulnerab", "🔓 Lỗ hổng"),
    ("zero", "🔓 Lỗ hổng"),
    ("cve", "🔓 Lỗ hổng"),
    ("exploit", "🔓 Lỗ hổng"),
    ("rò rỉ", "💾 Rò rỉ dữ liệu"),
    ("data breach", "💾 Rò rỉ dữ liệu"),
    ("data leak", "💾 Rò rỉ dữ liệu"),
    ("leak", "💾 Rò rỉ dữ liệu"),
    ("malware", "🦠 Malware"),
    ("ransomware", "🦠 Malware"),
    ("trojan", "🦠 Malware"),
    ("apt", "🦠 Malware"),
    ("tội phạm", "🚨 Tội phạm mạng"),
    ("cybercrime", "🚨 Tội phạm mạng"),
    ("scam", "🚨 Tội phạm mạng"),
    ("lừa đảo", "🚨 Tội phạm mạng"),
    ("fraud", "🚨 Tội phạm mạng"),
    ("takedown", "🚨 Tội phạm mạng"),
    ("triệt phá", "🚨 Tội phạm mạng"),
    ("công nghệ", "💻 Công nghệ"),
    ("technology", "💻 Công nghệ"),
    ("cloud", "💻 Công nghệ"),
    ("infrastructure", "💻 Công nghệ"),
    ("hạ tầng", "💻 Công nghệ"),
]


def map_tags(region, category, fallback_tags=None):
    """
    Chuyển (region, category) của AI -> list tên tag Forum.
    CHỈ trả về tag có trong config.FORUM_TAG_NAMES (không tạo tag mới).
    Tối đa 3 tag. Nếu không map được -> dùng fallback_tags (tag của rule).
    """
    tags = []

    # (a) Vùng miền luôn đứng đầu.
    region_l = (region or "").lower()
    if "việt" in region_l or "viet" in region_l:
        region_tag = "🇻🇳 Việt Nam"
    else:
        region_tag = "🌎 Quốc tế"
    tags.append(region_tag)

    # (b) Category.
    cat_l = (category or "").lower()
    for keyword, tag in _CATEGORY_KEYWORDS:
        if keyword in cat_l:
            if tag not in tags:
                tags.append(tag)
            break

    # (c) Bổ sung từ tag của rule (vd 🔐 An ninh mạng).
    # Bỏ tag vùng miền của rule để KHÔNG mâu thuẫn với region do AI chọn
    # (vd rule gán 🌎 Quốc tế nhưng AI xác định là tin Việt Nam).
    for tag in (fallback_tags or []):
        if tag in ("🇻🇳 Việt Nam", "🌎 Quốc tế"):
            continue
        if tag not in tags:
            tags.append(tag)
        if len(tags) >= 3:
            break

    valid = [t for t in tags if t in FORUM_TAG_NAMES]
    dedup = []
    for t in valid:
        if t not in dedup:
            dedup.append(t)
    dedup = dedup[:3]

    if not dedup:
        return [t for t in (fallback_tags or []) if t in FORUM_TAG_NAMES][:3] or []
    return dedup


# =====================================================================
# 6. NHIỆM VỤ 1: AI FILTER / SELECTOR
# =====================================================================

def _filter_chunk_once(chunk, cfg, model=None):
    """
    Gửi MỘT lô candidate cho AI (một model). Trả dict {index trong lô ->
    AIDecision} hoặc None nếu lỗi (429 hết lượt, JSON hỏng, id không khớp...).
    """
    import ai_prompts

    system_prompt = ai_prompts.FILTER_SYSTEM_PROMPT
    user_prompt = ai_prompts.build_filter_user_payload(chunk)

    raw = _call_chat(cfg, system_prompt, user_prompt,
                     max_tokens=max(1200, 260 * len(chunk)),
                     temperature=0.1, model=model)
    if raw is None:
        return None

    data = extract_json(raw)
    if data is None:
        logger.warning("[AI] Filter trả JSON sai format (model=%s).",
                       model or cfg.model)
        return None

    decisions = parse_decisions(data)
    if not decisions:
        logger.warning("[AI] Filter không trả decision nào hợp lệ (model=%s).",
                       model or cfg.model)
        return None

    by_id = {}
    for d in decisions:
        if 0 <= d.id < len(chunk):
            by_id[d.id] = d
    if not by_id:
        logger.warning("[AI] Filter trả id không khớp candidate (model=%s).",
                       model or cfg.model)
        return None
    return by_id


def _filter_chunk(chunk, cfg):
    """Thử model chính, lỗi thì thử AI_FALLBACK_MODEL (nếu có) đúng một lần."""
    result = _filter_chunk_once(chunk, cfg)
    if result is not None:
        return result
    fb = (getattr(cfg, "fallback_model", "") or "").strip()
    if fb and fb != cfg.model:
        logger.info("[AI] Model chính lỗi -> thử model dự phòng %s.", fb)
        return _filter_chunk_once(chunk, cfg, model=fb)
    return None


def ai_filter(candidates, cfg):
    """
    Gửi candidate cho AI đánh giá THEO LÔ NHỎ (FILTER_BATCH_SIZE bài/lần) để
    không vượt giới hạn token/phút của gói miễn phí.
    Trả về: dict {index -> AIDecision}, hoặc None nếu AI lỗi/tắt hoàn toàn
    (không lô nào thành công). Lô lỗi một phần -> các bài của lô đó nhận
    placeholder (from_ai=False): KHÔNG đăng và KHÔNG ghi bộ nhớ "AI loại".
    """
    total = len(candidates)
    by_id = {}
    ok_chunks = 0

    for start in range(0, total, FILTER_BATCH_SIZE):
        if start > 0:
            time.sleep(FILTER_BATCH_PAUSE_SECONDS)
        chunk = candidates[start:start + FILTER_BATCH_SIZE]
        result = _filter_chunk(chunk, cfg)
        if result is None:
            logger.warning("[AI] Lô %d-%d/%d lỗi -> các bài trong lô không "
                           "được AI đánh giá.", start + 1,
                           start + len(chunk), total)
            continue
        ok_chunks += 1
        for local_idx, decision in result.items():
            decision.id = start + local_idx
            by_id[start + local_idx] = decision

    if ok_chunks == 0:
        logger.warning("[AI] Không lô nào thành công -> fallback rule-based.")
        return None

    # Thiếu id -> placeholder (an toàn, không đăng nhầm).
    # Placeholder KHÔNG phải là quyết định thật của AI -> không ghi bộ nhớ.
    for idx in range(total):
        if idx not in by_id:
            by_id[idx] = AIDecision(
                id=idx, publish=False, score=0,
                reason=PLACEHOLDER_REASON,
                from_ai=False,
            )
    return by_id


# =====================================================================
# 7. NHIỆM VỤ 2: AI NEWS WRITER
# =====================================================================

# Cụm clickbait bị CẤM trong nội dung AI (nếu trượt vào -> bỏ, dùng nội dung rule).
_FORBIDDEN_PHRASES = [
    "bạn sẽ không tin",
    "điều gì xảy ra tiếp theo",
    "sốc", "ngỡ ngàng", "kinh hoàng",
    "cú sốc",
    "click vào đây",
    "đừng bỏ lỡ",
]

# Số từ tối thiểu của nội dung AI (B3: hạ còn 40 - nguồn RSS ngắn thì bài ngắn).
AI_MIN_WORDS = 40

# Nhóm số >= 3 chữ số, có thể kèm phân cách hàng nghìn (1.000 / 1,000 / 1 000).
_NUMBER_GROUP_RE = re.compile(r"\d+(?:[.,\u00a0\u202f]\d+)*")


def extract_number_groups(text):
    """
    Trích các nhóm số TỪ 3 CHỮ SỐ TRỞ LÊN từ text, sau khi bỏ dấu phân cách
    hàng nghìn. VD: "1,462 người" -> {"1462"}; "1.000 máy" -> {"1000"};
    "2026" -> {"2026"}; "45 giây" -> {} (quá ngắn, bỏ qua).
    """
    groups = set()
    if not text:
        return groups
    for match in _NUMBER_GROUP_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) >= 3:
            groups.add(digits)
    return groups


def source_number_groups(scored):
    """
    Tập số hợp lệ lấy từ NGUỒN (title + summary + link + thời gian đăng).
    Thời gian đăng được tính vì AI nhận thông tin đó trong input -> không
    phải số bịa.
    """
    article = scored.article
    published = getattr(article, "published", None)
    published_str = ""
    if published is not None:
        try:
            from textutils import format_vn_time
            published_str = format_vn_time(published)
        except Exception:
            published_str = ""
    blob = " ".join([
        getattr(scored, "title", "") or "",
        getattr(article, "summary", "") or "",
        getattr(scored, "link", "") or "",
        published_str,
    ])
    return extract_number_groups(blob)


def ai_write(scored, decision, cfg):
    """
    Yêu cầu AI viết lại MỘT bài thành tin tiếng Việt.
    Trả về {"title": str, "content": str} hoặc None (dùng nội dung rule).

    Guard chống bịa số liệu (B3): mọi nhóm số >= 3 chữ số trong title/content
    do AI viết PHẢI xuất hiện trong title+summary+link(+tg đăng) của nguồn;
    nếu không -> bỏ bản viết, dùng nội dung rule.
    """
    import ai_prompts

    raw = _call_chat(
        cfg,
        ai_prompts.WRITER_SYSTEM_PROMPT,
        ai_prompts.build_writer_user_payload(scored, decision),
        max_tokens=1800,
        temperature=0.4,
    )
    if raw is None:
        return None

    data = extract_json(raw)
    if not isinstance(data, dict):
        logger.warning("[AI] Writer trả JSON sai format -> dùng nội dung rule.")
        return None

    title = str(data.get("title") or "").strip()
    content = str(data.get("content") or "").strip()
    content = content.replace("\r\n", "\n").replace("\r", "\n").strip()

    # Bỏ dấu " " bọc ngoài nếu model trả về.
    if len(title) >= 2 and title[0] == title[-1] == '"':
        title = title[1:-1].strip()

    if not title or not content:
        logger.warning("[AI] Writer trả title/content rỗng -> dùng nội dung rule.")
        return None

    # B3: nội dung quá ngắn -> bỏ (nguồn ngắn thì bài ngắn, không độn chữ).
    word_count = len(content.split())
    if word_count < AI_MIN_WORDS:
        logger.warning("[AI] Writer trả nội dung quá ngắn (%d từ, cần >= %d) "
                       "-> dùng rule.", word_count, AI_MIN_WORDS)
        return None

    # B3: guard chống bịa số liệu.
    ai_numbers = extract_number_groups(title + " " + content)
    allowed_numbers = source_number_groups(scored)
    invented = sorted(ai_numbers - allowed_numbers)
    if invented:
        logger.warning(
            "[AI] Writer bịa số liệu không có trong nguồn (%s) "
            "-> dùng nội dung rule. Bài: %s",
            ", ".join(invented[:10]), scored.title[:60])
        return None

    # Chặn văn phong clickbait/cấm.
    lowered = content.lower() + " " + title.lower()
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in lowered:
            logger.warning("[AI] Writer dùng văn phong cấm (%r) -> dùng rule.",
                           phrase)
            return None

    # Tiêu đề quá dài -> cắt về 90 ký tự.
    if len(title) > 90:
        title = title[:89].rstrip() + "…"

    return {"title": title, "content": content}


# =====================================================================
# 8. HÀM CHÍNH: review_candidates (main.py gọi hàm này)
# =====================================================================

def review_candidates(candidates, now=None, max_posts=None, hot_slots=0):
    """
    AI đánh giá + biên tập các candidate từ analyzer.

    Tham số:
    - candidates : List[analyzer.ScoredArticle] (đã qua lọc rule).
    - now        : thời điểm tham chiếu (giờ VN), mặc định now_vn().
    - max_posts  : số slot TIN THƯỜNG còn lại (main.py tính từ quota).
                   Mặc định MAX_POSTS_PER_DAY. AI KHÔNG BAO GIỜ được chọn
                   nhiều hơn mức này.
    - hot_slots  : số slot TIN NÓNG còn lại (0 = không có slot nóng).

    Trả về: List[analyzer.ScoredArticle] ĐÃ CẤP SLOT (bài hot đứng trước),
    mỗi bài có .slot ("hot" | "normal") và .is_hot.
    - Bài được AI chọn: gắn .ai_decision, .ai_title, .ai_content, .tags mới.
    - Bài AI THỰC SỰ đánh giá rồi loại: gắn .ai_rejected = True kèm
      .ai_rejected_scope = "all" | "hot_only" (D5) để main.py ghi bộ nhớ.
    - AI lỗi/tắt: trả về NGUYÊN VẬN danh sách rule, KHÔNG gắn cờ nào;
      main.py sẽ tự cấp slot với ai_used=False (phụ thuộc HOT_ALLOW_WITHOUT_AI).

    Thiết kế D4: allocate_slots(...) THAY cho đoạn cắt cứng MAX_POSTS_PER_DAY,
    và CHỈ gọi writer cho bài ĐÃ CẤP SLOT -> không viết thừa rồi bị vứt.
    Hàm này KHÔNG đăng bài, KHÔNG đụng state, KHÔNG vượt quota -
    main.py vẫn chạy analyzer.select_articles() / hot.select_hot_articles()
    sau đó để kiểm tra lại bằng code.
    """
    if not candidates:
        return []

    if max_posts is None:
        max_posts = MAX_POSTS_PER_DAY
    try:
        max_posts = int(max_posts)
    except (TypeError, ValueError):
        max_posts = MAX_POSTS_PER_DAY
    try:
        hot_slots = max(0, int(hot_slots or 0))
    except (TypeError, ValueError):
        hot_slots = 0

    # Hết cả hai loại quota -> KHÔNG gọi AI (tiết kiệm token).
    if max_posts <= 0 and hot_slots <= 0:
        logger.info("[AI] Không còn quota (thường/nóng) -> bỏ qua AI.")
        return []

    cfg = load_config()

    if not cfg.enabled:
        logger.info("[AI] AI TẮT (%s). Dùng kết quả rule-based thuần.", cfg.describe())
        return list(candidates)

    logger.info("[AI] Bắt đầu AI filter cho %d candidate, quota thường còn %d, "
                "quota nóng còn %d (%s)",
                len(candidates), max_posts, hot_slots, cfg.describe())

    try:
        decisions = ai_filter(candidates, cfg)
    except Exception as exc:
        logger.warning("[AI] Lỗi khi filter: %r -> fallback rule-based.", exc)
        decisions = None

    if decisions is None:
        logger.info("[AI] Fallback: dùng toàn bộ kết quả rule-based.")
        return list(candidates)

    # Gom bài AI chọn (publish=true và score >= AI_MIN_SCORE),
    # sắp theo điểm AI giảm dần, Việt Nam thắng điểm hòa.
    approved = []
    for idx, scored in enumerate(candidates):
        decision = decisions.get(idx)
        if decision is None:
            continue

        # Bài AI THỰC SỰ đánh giá (không phải placeholder):
        # lưu quyết định để main.py in log [HOT] (E6).
        if getattr(decision, "from_ai", False):
            scored.ai_decision = decision

        # Bài AI THỰC SỰ đánh giá (không phải placeholder) nhưng KHÔNG đạt
        # -> đánh dấu để main.py ghi bộ nhớ "đã bị AI loại" scope "all" (D5).
        really_rejected = (
            decision.from_ai
            and (not decision.publish or decision.score < AI_MIN_SCORE)
        )
        if really_rejected:
            scored.ai_rejected = True
            scored.ai_rejected_scope = "all"
            logger.info("[AI] LOẠI [%d] score=%d/%d publish=%s (%s): %s | %s",
                        idx, decision.score, AI_MIN_SCORE, decision.publish,
                        decision.severity, scored.title[:60],
                        decision.reason[:120])
            continue

        if not decision.publish or decision.score < AI_MIN_SCORE:
            # Placeholder hoặc bài không có quyết định thật -> KHÔNG gắn cờ.
            continue

        # Bài rule CHƯA đạt ngưỡng đăng chỉ được cấp slot nếu AI chấm đủ cao
        # (AI_RESCUE_MIN_SCORE) - tránh chiếm slot rồi bị cổng code loại.
        rule_ok = (scored.score >= MIN_SCORE_TO_POST
                   or (scored.score >= SPECIAL_MIN_SCORE
                       and getattr(scored, "is_special", False)))
        if not rule_ok and decision.score < AI_RESCUE_MIN_SCORE:
            scored.ai_decision = decision
            logger.info("[AI] BỎ [%d] rule=%d chưa đạt ngưỡng, AI=%d < %d: %s",
                        idx, scored.score, decision.score,
                        AI_RESCUE_MIN_SCORE, scored.title[:60])
            continue

        scored.ai_rejected = False
        scored.ai_rejected_scope = ""
        scored.ai_decision = decision
        approved.append((scored, decision))

    approved.sort(
        key=lambda pair: (
            -pair[1].score,
            0 if getattr(pair[0], "is_vietnam", False) else 1,
            -int(getattr(pair[0], "score", 0) or 0),
        )
    )

    # D4: CẤP SLOT thay cho đoạn cắt cứng theo max_posts.
    # Bài không được cấp slot KHÔNG được gọi writer -> không viết thừa rồi bị vứt.
    allocated = allocate_slots(approved, remaining_normal=max_posts,
                               remaining_hot=hot_slots, now=now, ai_used=True)
    allocated_ids = {id(s) for s in allocated}
    if len(approved) > len(allocated):
        logger.info("[AI] %d bài đạt -> cấp %d slot (thường còn %d / nóng còn %d), "
                    "%d bài không có slot.",
                    len(approved), len(allocated), max_posts, hot_slots,
                    len(approved) - len(allocated))

    # D5: chế độ HOT-ONLY (quota thường đã đầy): bài AI DUYỆT nhưng KHÔNG nóng
    # -> ghi bộ nhớ scope "hot_only" (ngày mai vẫn xét lại như tin thường).
    # Bài NÓNG bị tràn slot nóng thì KHÔNG ghi (lần sau còn lấy slot trống).
    if max_posts <= 0:
        for scored, decision in approved:
            if id(scored) in allocated_ids:
                continue
            try:
                hot_flag = is_hot(scored, decision, now, ai_used=True)
            except Exception:
                hot_flag = False
            if hot_flag:
                continue
            scored.ai_rejected = True
            scored.ai_rejected_scope = "hot_only"
            logger.info("[AI] HOT-ONLY: duyệt nhưng không nóng -> ghi hot_only: %s",
                        scored.title[:60])

    # AI viết lại MỖI BÀI ĐÃ CẤP SLOT (lỗi viết -> dùng nội dung rule).
    result = []
    for scored in allocated:
        decision = getattr(scored, "ai_decision", None)
        if decision is not None:
            scored.tags = map_tags(decision.region, decision.category,
                                   getattr(scored, "tags", []))

        written = None
        try:
            written = ai_write(scored, decision, cfg)
        except Exception as exc:
            logger.warning("[AI] Lỗi khi viết bài %r: %r -> dùng nội dung rule.",
                           scored.title[:60], exc)

        ai_score = int(getattr(decision, "score", 0) or 0) if decision else 0
        if written:
            scored.ai_title = written["title"]
            scored.ai_content = written["content"]
            logger.info("[AI] CHỌN + VIẾT: [%d/%d] %s (slot=%s)",
                        ai_score, scored.score, scored.title[:60],
                        scored.slot)
        else:
            scored.ai_title = ""
            scored.ai_content = ""
            logger.info("[AI] CHỌN (nội dung rule): [%d/%d] %s (slot=%s)",
                        ai_score, scored.score, scored.title[:60],
                        scored.slot)

        result.append(scored)

    logger.info("[AI] Kết quả: %d/%d bài được cấp slot (nóng %d, thường %d).",
                len(result), len(candidates),
                sum(1 for s in result if getattr(s, "slot", "") == "hot"),
                sum(1 for s in result if getattr(s, "slot", "") == "normal"))
    return result