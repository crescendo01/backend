"""Replaces every direct Supabase call in `achievement_repository.dart`."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.dependencies import CurrentUser, get_current_user
from app.schemas import UnlockAchievementRequest
from app.supabase_client import SupabaseRest, get_supabase

router = APIRouter(prefix="/v1/achievements", tags=["achievements"])

TABLE = "achievements"


@router.get("")
async def list_achievements(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    return await supabase.select(
        TABLE,
        filters={"user_id": f"eq.{current_user.uid}"},
        order="earned_at.desc",
    )


@router.post("", status_code=201)
async def unlock_achievement(
    payload: UnlockAchievementRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    """Same `(user_id, badge_type)` unique-constraint + ignore-duplicates
    upsert as `AchievementRepository.unlock` — safe if two devices race to
    unlock the same badge. `user_id` always comes from the verified token,
    never the request body, even though the client already sends a
    matching one today."""
    row = {
        "id": payload.id,
        "user_id": current_user.uid,
        "badge_type": payload.badge_type,
        "earned_at": payload.earned_at or None,
    }
    if row["earned_at"] is None:
        row["earned_at"] = datetime.now(timezone.utc).isoformat()

    result = await supabase.upsert(
        TABLE,
        row,
        on_conflict="user_id,badge_type",
        ignore_duplicates=True,
    )
    return result or {"already_unlocked": True}
