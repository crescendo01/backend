"""Ear Training module — skill tree (modules → lessons → exercises) plus
per-user progress/session tracking. Mirrors `lessons.py`'s shape: thin
handlers over `SupabaseRest`, server computes anything that affects XP so
a tampered client can't self-award progress.

Table prefix `ear_` (content) / `user_ear_` (per-user) — kept separate
from the existing `lessons` / `user_progress` tables since this is a
structurally different system (modules + exercises, not single lessons).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.dependencies import CurrentUser, get_current_user
from app.schemas import EarSessionCompleteRequest, EarSessionStartRequest
from app.supabase_client import SupabaseRest, SupabaseRestError, get_supabase

router = APIRouter(prefix="/v1/ear", tags=["ear-training"])

EAR_MODULES = "ear_modules"
EAR_LESSONS = "ear_lessons"
EAR_EXERCISES = "ear_exercises"
USER_EAR_PROGRESS = "user_ear_progress"
USER_EAR_SESSIONS = "user_ear_sessions"
EAR_INTERVAL_MNEMONICS = "ear_interval_mnemonics"
EAR_CHORD_QUALITIES = "ear_chord_qualities"
EAR_PROGRESSIONS = "ear_progressions"
EAR_RHYTHM_PATTERNS = "ear_rhythm_patterns"
EAR_AUDIO_BUCKET = "ear-training-audio"

# XP/hint constants — mirrors docs/implementation_guide.md "5. Gamification
# Integration" exactly, kept server-side so it can't be spoofed by the client.
HINT_PENALTIES = {
    "beginner": 2,
    "intermediate": 3,
    "advanced": 5,
    "expert": 10,
}
MASTERY_ACCURACY = 90.0


# ── Modules ──────────────────────────────────────────────────────────────────

@router.get("/modules")
async def list_modules(
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    return await supabase.select(EAR_MODULES, order="sort_order")


@router.get("/modules/{module_id}/lessons")
async def list_lessons_for_module(
    module_id: str,
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    return await supabase.select(
        EAR_LESSONS,
        filters={"module_id": f"eq.{module_id}"},
        order="sort_order",
    )


# ── Lessons ──────────────────────────────────────────────────────────────────

@router.get("/lessons/{lesson_id}")
async def get_lesson(
    lesson_id: str,
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    lesson = await supabase.select(EAR_LESSONS, filters={"id": f"eq.{lesson_id}"}, single=True)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Ear lesson not found.")
    return lesson


@router.get("/lessons/{lesson_id}/exercises")
async def list_exercises(
    lesson_id: str,
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    return await supabase.select(
        EAR_EXERCISES,
        filters={"lesson_id": f"eq.{lesson_id}"},
        order="sort_order",
    )


# ── Progress ─────────────────────────────────────────────────────────────────

@router.get("/progress")
async def get_progress(
    lesson_ids: Optional[str] = Query(default=None, description="Comma-separated ear_lessons ids"),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    filters = {"user_id": f"eq.{current_user.uid}"}
    if lesson_ids:
        ids = [i.strip() for i in lesson_ids.split(",") if i.strip()]
        if ids:
            filters["lesson_id"] = "in.(" + ",".join(ids) + ")"
    return await supabase.select(USER_EAR_PROGRESS, filters=filters)


# ── Sessions ─────────────────────────────────────────────────────────────────

@router.post("/sessions")
async def start_session(
    payload: EarSessionStartRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    row = {
        "user_id": current_user.uid,
        "lesson_id": payload.lesson_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    return await supabase.insert(USER_EAR_SESSIONS, row)


@router.put("/sessions/{session_id}/complete")
async def complete_session(
    session_id: str,
    payload: EarSessionCompleteRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    """Computes accuracy/passed/xp server-side, updates the session row,
    then upserts `user_ear_progress` — same "client reports raw counts,
    server decides what they're worth" split as `lessons.record_attempt`.
    """
    session = await supabase.select(
        USER_EAR_SESSIONS,
        filters={"id": f"eq.{session_id}", "user_id": f"eq.{current_user.uid}"},
        single=True,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Ear session not found.")

    lesson = await supabase.select(EAR_LESSONS, filters={"id": f"eq.{session['lesson_id']}"}, single=True)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Ear lesson not found.")

    seen = payload.exercises_seen
    correct = payload.exercises_correct
    accuracy = round((correct / seen) * 100, 2) if seen > 0 else 0.0
    min_accuracy = float(lesson.get("min_accuracy") or 70.0)
    passed = accuracy >= min_accuracy
    mastered_this_session = accuracy >= MASTERY_ACCURACY

    base_xp = int(lesson.get("xp_reward") or 0)
    hint_penalty = HINT_PENALTIES.get(lesson.get("difficulty", "beginner"), 2) * payload.hints_used
    xp_earned = max(0, round(base_xp * (accuracy / 100)) - hint_penalty) if passed else 0

    now = datetime.now(timezone.utc).isoformat()

    updated_session = await supabase.update(
        USER_EAR_SESSIONS,
        {
            "completed_at": now,
            "exercises_seen": seen,
            "exercises_correct": correct,
            "accuracy": accuracy,
            "xp_earned": xp_earned,
            "hints_used": payload.hints_used,
            "passed": passed,
            "session_data": payload.session_data,
        },
        filters={"id": f"eq.{session_id}"},
    )

    existing_progress = await supabase.select(
        USER_EAR_PROGRESS,
        filters={"user_id": f"eq.{current_user.uid}", "lesson_id": f"eq.{lesson['id']}"},
        single=True,
    )
    previous_best = float(existing_progress.get("best_accuracy") or 0) if existing_progress else 0.0
    previous_xp = int(existing_progress.get("total_xp_earned") or 0) if existing_progress else 0
    previous_attempts = int(existing_progress.get("total_attempts") or 0) if existing_progress else 0
    already_mastered = bool(existing_progress and existing_progress.get("status") == "mastered")

    if mastered_this_session or already_mastered:
        status = "mastered"
    elif passed:
        status = "passed"
    else:
        status = "in_progress"

    progress = await supabase.upsert(
        USER_EAR_PROGRESS,
        {
            "user_id": current_user.uid,
            "lesson_id": lesson["id"],
            "status": status,
            "best_accuracy": max(previous_best, accuracy),
            "total_attempts": previous_attempts + 1,
            "total_xp_earned": previous_xp + xp_earned,
            "last_played_at": now,
            "mastered_at": now if (mastered_this_session and not already_mastered) else (
                existing_progress.get("mastered_at") if existing_progress else None
            ),
        },
        on_conflict="user_id,lesson_id",
    )

    return {
        "session": updated_session[0] if updated_session else None,
        "progress": progress,
        "accuracy": accuracy,
        "passed": passed,
        "xp_earned": xp_earned,
        "is_personal_best": accuracy > previous_best,
    }


# ── Reference tables (mnemonics, chord qualities, progressions, rhythms) ────
# Static content the lesson screens look up for theory-tip/mnemonic cards —
# small enough to fetch in full rather than filtering server-side.

@router.get("/reference/intervals")
async def list_interval_mnemonics(
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    return await supabase.select(EAR_INTERVAL_MNEMONICS, order="sort_order")


@router.get("/reference/chords")
async def list_chord_qualities(
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    return await supabase.select(EAR_CHORD_QUALITIES, order="sort_order")


@router.get("/reference/progressions")
async def list_progressions(
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    return await supabase.select(EAR_PROGRESSIONS, order="sort_order")


@router.get("/reference/rhythms")
async def list_rhythm_patterns(
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    return await supabase.select(EAR_RHYTHM_PATTERNS, order="sort_order")


# ── Progression audio (recorded files, not synthesized) ─────────────────────
# See progression_recording_guide.md — these ~6 files are real piano takes,
# not generated by the audio engine. Until they're recorded and uploaded to
# the `ear-training-audio` Storage bucket, this 404s; the Flutter side
# catches that and falls back to a synthesized block-chord approximation
# via AudioEngine so the lesson still works end to end in the meantime.

@router.get("/progressions/audio/{audio_key}")
async def download_progression_audio(
    audio_key: str,
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    try:
        content = await supabase.download_object(EAR_AUDIO_BUCKET, f"progressions/{audio_key}.mp3")
    except SupabaseRestError as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail="Progression recording not uploaded yet.") from exc
        raise
    return Response(content=content, media_type="audio/mpeg")
