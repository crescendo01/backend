"""
Generates short, encouraging coaching feedback for a practice attempt.

This is a server-side port of `lib/services/gemini_feedback_service.dart` —
same prompt, same fallback templates, same cache-key shape — so feedback
text is consistent whether or not this endpoint is involved. Moving it
server-side keeps GEMINI_API_KEY out of the compiled Flutter binary
(`--dart-define` values are readable by anyone who pulls apart the app)
and gives you one place to swap models, add logging, or batch calls later.
"""
from __future__ import annotations

import logging

import httpx
from cachetools import TTLCache

from app.config import get_settings
from app.schemas import FeedbackRequest

logger = logging.getLogger("crescendo.gemini")

# Same idea as the Dart side's in-memory `_cache` map: identical (rounded)
# scores for the same note/instrument don't re-trigger a paid call. Bounded
# size + 1 hour TTL so this can't grow without bound on a long-lived
# instance. Per-process only — fine for a single Render instance; if you
# scale to multiple instances later, swap this for Redis.
_feedback_cache: TTLCache[str, str] = TTLCache(maxsize=5000, ttl=3600)

_GEMINI_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# Tried in order on every request. Quota exhaustion (429) or a server-side
# error (5xx) on one model falls through to the next — same pattern as the
# InboxOS classifier fallback chain. settings.gemini_model is ignored now;
# edit this list directly to change the chain.
_FALLBACK_MODELS: list[str] = [
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]


def _cache_key(req: FeedbackRequest) -> str:
    s = req.score
    return (
        f"{req.instrument}|{req.target_note}|{s.overall}|"
        f"{round(s.pitch_accuracy)}|{round(s.timing_accuracy)}"
    )


def _build_prompt(req: FeedbackRequest) -> str:
    s = req.score
    pitch_direction = "sharp/flat (mixed)" if s.average_cents_off > 0 else "on pitch"
    return f"""
You are a warm, encouraging music coach inside a learning app called Crescendo.
A student just played the note {req.target_note} on {req.instrument}.

Results:
- Overall score: {s.overall}/100
- Pitch accuracy: {round(s.pitch_accuracy)}/100 (average deviation {round(s.average_cents_off)} cents, {pitch_direction})
- Timing/coverage accuracy: {round(s.timing_accuracy)}/100

Write 1-2 short sentences of specific, actionable feedback, like a real teacher
standing next to them would. Mention one concrete thing to try next time. Be
encouraging, never harsh. Do not use the word "error". Keep it under 40 words.
""".strip()


def _fallback_feedback(req: FeedbackRequest) -> str:
    overall = req.score.overall
    if overall >= 90:
        return "Excellent! That was right on target. Try the next step to keep the momentum going."
    if overall >= 75:
        return "Nice work — close to spot on. A little more breath/finger support should lock it in."
    if overall >= 50:
        return (
            "Good attempt! Your pitch drifted a bit partway through — try listening "
            "to the reference note again before you play."
        )
    return "Almost there! Take a breath, listen to the target note once more, and give it another go."


async def _call_model(client: httpx.AsyncClient, model: str, prompt: str, api_key: str) -> str | None:
    """Single attempt against one model. Returns text, or None if the
    response came back malformed (caller treats that as a soft failure
    and tries the next model)."""
    url = _GEMINI_ENDPOINT_TEMPLATE.format(model=model)
    response = await client.post(
        url,
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.6, "maxOutputTokens": 150},
        },
    )
    response.raise_for_status()  # 429/5xx -> httpx.HTTPStatusError -> caller falls through
    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        return None
    parts = (candidates[0].get("content") or {}).get("parts") or []
    if not parts:
        return None
    text = parts[0].get("text")
    return text.strip() if isinstance(text, str) and text.strip() else None


async def generate_feedback(req: FeedbackRequest) -> tuple[str, str]:
    """Returns (feedback_text, source) where source is gemini:<model>|fallback|cache."""
    settings = get_settings()
    key = _cache_key(req)

    cached = _feedback_cache.get(key)
    if cached is not None:
        return cached, "cache"

    if not settings.has_gemini_config:
        return _fallback_feedback(req), "fallback"

    prompt = _build_prompt(req)

    async with httpx.AsyncClient(timeout=10.0) as client:
        for model in _FALLBACK_MODELS:
            try:
                text = await _call_model(client, model, prompt, settings.gemini_api_key)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                # 429 = quota/rate-limited on this model, 5xx = transient
                # server error. Either way, try the next model in the chain.
                if status == 429 or status >= 500:
                    logger.warning("Gemini model %s failed (%s), trying next in chain", model, status)
                    continue
                # 4xx other than 429 (bad request, invalid model name, etc.)
                # won't be fixed by retrying the same prompt on another
                # model either, but we try anyway in case it's model-specific
                # (e.g. a model name that's been deprecated/removed).
                logger.warning("Gemini model %s returned %s, trying next in chain", model, status)
                continue
            except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                logger.warning("Gemini model %s call failed (%s), trying next in chain", model, exc)
                continue

            if text is None:
                logger.warning("Gemini model %s returned empty/malformed response, trying next", model)
                continue

            _feedback_cache[key] = text
            return text, f"gemini:{model}"

    # Every model in the chain failed or returned nothing usable.
    logger.error("All Gemini models in fallback chain failed for request key=%s", key)
    return _fallback_feedback(req), "fallback"