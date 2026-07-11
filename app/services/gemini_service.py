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
from app.schemas import FeedbackRequest, LessonQuestionTurn

logger = logging.getLogger("crescendo.gemini")

# Same idea as the Dart side's in-memory `_cache` map: identical (rounded)
# scores for the same note/instrument don't re-trigger a paid call. Bounded
# size + 1 hour TTL so this can't grow without bound on a long-lived
# instance. Per-process only — fine for a single Render instance; if you
# scale to multiple instances later, swap this for Redis.
_feedback_cache: TTLCache[str, str] = TTLCache(maxsize=5000, ttl=3600)

# Separate cache for lesson Q&A — keyed by lesson+question+history so a
# repeated exact question (common: "what's a triad?" asked in several
# lessons) doesn't re-trigger a paid call, but a genuine follow-up
# ("what about minor ones?") still gets a fresh, context-aware answer.
_lesson_qa_cache: TTLCache[str, str] = TTLCache(maxsize=2000, ttl=3600)

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


def _extract_lesson_text(title: str, content_json: dict) -> str:
    """Flattens this app's lesson `content_json` `sections` structure into
    plain text for a prompt. Deliberately tolerant of missing/extra fields
    since section shapes vary by type (hero/concept/summary/staff_visual/
    duration_viz/score_reading/etc.) — grabs whatever textual fields exist
    and skips the rest rather than special-casing every section type here.
    """
    lines = [f"Lesson: {title}"]
    for section in content_json.get("sections") or []:
        if not isinstance(section, dict):
            continue
        heading = section.get("title") or section.get("subtitle")
        if heading:
            lines.append(f"\n## {heading}")
        for key in ("body", "subtitle"):
            val = section.get(key)
            if isinstance(val, str) and val:
                lines.append(val)
        for key in ("bullets", "points", "extra_notes"):
            items = section.get(key)
            if isinstance(items, list):
                lines.extend(f"- {item}" for item in items if isinstance(item, str))
        steps = section.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    lines.append(f"{step.get('number', '')}. {step.get('title', '')}: {step.get('detail', '')}")
    return "\n".join(lines)[:6000]  # keep the prompt bounded regardless of lesson length


def _build_qa_prompt(lesson_text: str, question: str, history: list[LessonQuestionTurn]) -> str:
    convo = ""
    if history:
        convo = "\n".join(f"{'Student' if t.role == 'user' else 'You'}: {t.text}" for t in history) + "\n"
    return f"""
You are a warm, patient music theory tutor inside a learning app called
Crescendo. A student is reading the lesson below and has a question about it.

--- LESSON CONTENT ---
{lesson_text}
--- END LESSON CONTENT ---

{convo}Student: {question}

Answer their question clearly and encouragingly, grounded in the lesson
content above. If the question goes beyond what's in this lesson, you can
still help, but say so briefly. Keep it conversational and under 120 words —
this is a chat bubble, not an essay. No markdown headers.
""".strip()


def _fallback_qa_answer() -> str:
    return (
        "I'm having trouble reaching my brain right now — try asking again in "
        "a moment. In the meantime, re-reading the section just above your "
        "question often clears things up!"
    )


async def answer_lesson_question(
    lesson_id: str,
    lesson_title: str,
    content_json: dict,
    question: str,
    history: list[LessonQuestionTurn],
) -> tuple[str, str]:
    """Returns (answer_text, source). Mirrors generate_feedback's cache +
    fallback-chain shape, applied to open-ended lesson Q&A instead of
    templated practice feedback."""
    settings = get_settings()
    cache_key = f"{lesson_id}|{question.strip().lower()}|{len(history)}"

    cached = _lesson_qa_cache.get(cache_key)
    if cached is not None:
        return cached, "cache"

    if not settings.has_gemini_config:
        return _fallback_qa_answer(), "fallback"

    lesson_text = _extract_lesson_text(lesson_title, content_json)
    prompt = _build_qa_prompt(lesson_text, question, history)

    async with httpx.AsyncClient(timeout=15.0) as client:
        for model in _FALLBACK_MODELS:
            try:
                text = await _call_model(client, model, prompt, settings.gemini_api_key)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429 or status >= 500:
                    logger.warning("Gemini model %s failed (%s), trying next in chain", model, status)
                    continue
                logger.warning("Gemini model %s returned %s, trying next in chain", model, status)
                continue
            except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                logger.warning("Gemini model %s call failed (%s), trying next in chain", model, exc)
                continue

            if text is None:
                logger.warning("Gemini model %s returned empty/malformed response, trying next", model)
                continue

            _lesson_qa_cache[cache_key] = text
            return text, f"gemini:{model}"

    logger.error("All Gemini models in fallback chain failed for lesson Q&A key=%s", cache_key)
    return _fallback_qa_answer(), "fallback"


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