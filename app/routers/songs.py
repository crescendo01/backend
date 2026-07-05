"""Replaces every direct Supabase call in `song_repository.dart`."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import CurrentUser, get_current_user
from app.schemas import SongStepAttemptRequest
from app.supabase_client import SupabaseRest, get_supabase

router = APIRouter(prefix="/v1/songs", tags=["songs"])

SONGS = "songs"
USER_SONGS_PROGRESS = "user_songs_progress"


@router.get("")
async def list_songs(
    instrument: str,
    include_premium: bool = False,
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    filters = {"instrument": f"eq.{instrument}"}
    if not include_premium:
        filters["is_premium_only"] = "eq.false"
    return await supabase.select(SONGS, filters=filters, order="difficulty")


@router.get("/mastered-count")
async def mastered_count(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    total = await supabase.count(
        USER_SONGS_PROGRESS,
        filters={"user_id": f"eq.{current_user.uid}", "mastered": "eq.true"},
    )
    return {"count": total}


@router.get("/{song_id}")
async def get_song(
    song_id: str,
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    song = await supabase.select(SONGS, filters={"id": f"eq.{song_id}"}, single=True)
    if song is None:
        raise HTTPException(status_code=404, detail="Song not found.")
    return song


@router.post("/{song_id}/progress")
async def get_or_create_progress(
    song_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    """POST because it can create a row — same fetch-or-create semantics
    as `SongRepository.fetchOrCreateProgress`."""
    existing = await supabase.select(
        USER_SONGS_PROGRESS,
        filters={"user_id": f"eq.{current_user.uid}", "song_id": f"eq.{song_id}"},
        single=True,
    )
    if existing is not None:
        return existing

    fresh = {
        "id": str(uuid.uuid4()),
        "user_id": current_user.uid,
        "song_id": song_id,
        "current_step": 1,
        "best_score": 0,
        "completed_steps": [],
        "mastered": False,
    }
    return await supabase.insert(USER_SONGS_PROGRESS, fresh)


@router.post("/{song_id}/steps/{step_number}/attempts")
async def record_step_attempt(
    song_id: str,
    step_number: int,
    payload: SongStepAttemptRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    """Identical merge logic to `SongRepository.recordStepResult`, just
    looked up server-side by (user, song) instead of trusting a
    client-supplied progress snapshot — the rest of the math is the same:
    advance `current_step`, raise `best_score`, mark steps complete past
    the mastery threshold, flip `mastered` once every step clears it."""
    progress = await supabase.select(
        USER_SONGS_PROGRESS,
        filters={"user_id": f"eq.{current_user.uid}", "song_id": f"eq.{song_id}"},
        single=True,
    )
    if progress is None:
        raise HTTPException(
            status_code=404,
            detail="No progress row for this song yet — call POST /v1/songs/{song_id}/progress first.",
        )

    completed_steps = set(progress.get("completed_steps") or [])
    if payload.score >= payload.mastery_threshold:
        completed_steps.add(step_number)

    next_step = step_number + 1 if step_number < payload.total_steps else step_number
    best_score = max(payload.score, int(progress.get("best_score") or 0))
    mastered = len(completed_steps) >= payload.total_steps

    values = {
        "current_step": next_step,
        "best_score": best_score,
        "completed_steps": sorted(completed_steps),
        "mastered": mastered,
    }
    rows = await supabase.update(USER_SONGS_PROGRESS, values, filters={"id": f"eq.{progress['id']}"})
    return rows[0] if rows else {**progress, **values}
