"""Replaces every direct Supabase call in `lesson_repository.dart`."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import CurrentUser, get_current_user
from app.schemas import LessonAttemptRequest
from app.supabase_client import SupabaseRest, get_supabase

router = APIRouter(prefix="/v1/lessons", tags=["lessons"])

LESSONS = "lessons"
USER_PROGRESS = "user_progress"


@router.get("")
async def list_lessons(
    instrument: str,
    type: Optional[str] = None,  # noqa: A002 — matches the Dart param name `type`
    max_difficulty: Optional[int] = None,
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    filters = {"instrument": f"eq.{instrument}"}
    if type is not None:
        filters["type"] = f"eq.{type}"
    if max_difficulty is not None:
        filters["difficulty"] = f"lte.{max_difficulty}"
    return await supabase.select(LESSONS, filters=filters, order="order_in_series")


@router.get("/progress")
async def get_progress(
    lesson_ids: Optional[str] = Query(default=None, description="Comma-separated lesson ids"),
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    """Returns the raw row list; the Dart side builds the lessonId->progress
    map itself, same as it always has — only the data source changed."""
    filters = {"user_id": f"eq.{current_user.uid}"}
    if lesson_ids:
        ids = [i.strip() for i in lesson_ids.split(",") if i.strip()]
        if ids:
            filters["lesson_id"] = "in.(" + ",".join(ids) + ")"
    return await supabase.select(USER_PROGRESS, filters=filters)


@router.get("/completed-count")
async def completed_count(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    total = await supabase.count(
        USER_PROGRESS,
        filters={"user_id": f"eq.{current_user.uid}", "completed": "eq.true"},
    )
    return {"count": total}


@router.get("/{lesson_id}")
async def get_lesson(
    lesson_id: str,
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    lesson = await supabase.select(LESSONS, filters={"id": f"eq.{lesson_id}"}, single=True)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found.")
    return lesson


@router.get("/{lesson_id}/next")
async def get_next_lesson(
    lesson_id: str,
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    current = await supabase.select(LESSONS, filters={"id": f"eq.{lesson_id}"}, single=True)
    if current is None:
        raise HTTPException(status_code=404, detail="Lesson not found.")
    return await supabase.select(
        LESSONS,
        filters={
            "instrument": f"eq.{current['instrument']}",
            "type": f"eq.{current['type']}",
            "order_in_series": f"gt.{current['order_in_series']}",
        },
        order="order_in_series",
        limit=1,
        single=True,
    )


@router.post("/{lesson_id}/attempts")
async def record_attempt(
    lesson_id: str,
    payload: LessonAttemptRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    """Upserts progress after an attempt. `best_score` only increases —
    identical logic to `LessonRepository.recordAttempt`, just evaluated
    here instead of on the client."""
    existing = await supabase.select(
        USER_PROGRESS,
        filters={"user_id": f"eq.{current_user.uid}", "lesson_id": f"eq.{lesson_id}"},
        single=True,
    )

    now = datetime.now(timezone.utc).isoformat()
    best_score = payload.score if existing is None else max(payload.score, int(existing.get("best_score") or 0))
    attempts = 1 if existing is None else int(existing.get("attempts") or 0) + 1
    completed = best_score >= payload.unlock_threshold

    row = {
        "user_id": current_user.uid,
        "lesson_id": lesson_id,
        "completed": completed,
        "attempts": attempts,
        "best_score": best_score,
        "completed_date": now if completed else (existing.get("completed_date") if existing else None),
        "created_at": (existing.get("created_at") if existing else now),
    }

    if existing is None:
        return await supabase.insert(USER_PROGRESS, row)
    rows = await supabase.update(USER_PROGRESS, row, filters={"id": f"eq.{existing['id']}"})
    return rows[0] if rows else row
