"""
User profile routes — event-driven, no polling.

On WS connect: fetch current profile once, send immediately, then wait.
On any write (onboarding / update): push updated profile to WS subscribers.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.dependencies import CurrentUser, get_current_user
from app.firebase import delete_user as delete_firebase_user
from app.firebase import verify_id_token_string
from app.realtime import EventHub
from app.schemas import CompleteOnboardingRequest, CreateProfileRequest, UpdateProfileRequest
from app.supabase_client import SupabaseRest, SupabaseRestError, get_supabase

logger = logging.getLogger("crescendo.users")

router = APIRouter(prefix="/v1/users", tags=["users"])

TABLE = "users"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_profile(uid: str, supabase: SupabaseRest):
    return await supabase.select(TABLE, filters={"id": f"eq.{uid}"}, single=True)


def _user_key(uid: str) -> str:
    return f"user:{uid}"


@router.get("/me")
async def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    return await _get_profile(current_user.uid, supabase)


@router.post("/me", status_code=201)
async def create_me(
    payload: CreateProfileRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    now = _now_iso()
    row = {
        "id": current_user.uid,
        "email": payload.email,
        "display_name": payload.display_name,
        "avatar_url": payload.avatar_url,
        "instrument_main": "piano",
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = await supabase.insert(TABLE, row)
    except SupabaseRestError as exc:
        if exc.status_code == 409:
            raise HTTPException(status_code=409, detail="Profile already exists.") from exc
        raise
    return result


@router.put("/me")
async def update_me(
    payload: UpdateProfileRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    values = {k: v for k, v in payload.model_dump().items() if v is not None}
    values["updated_at"] = _now_iso()
    rows = await supabase.update(TABLE, values, filters={"id": f"eq.{current_user.uid}"})
    result = rows[0] if rows else None

    hub: EventHub = request.app.state.poller_hub
    await hub.push(_user_key(current_user.uid), result)

    return result


@router.post("/me/onboarding")
async def complete_onboarding(
    payload: CompleteOnboardingRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    values = {
        "instrument_main": payload.instrument_main,
        "skill_level": payload.skill_level,
        "goals": payload.goals,
        "updated_at": _now_iso(),
    }
    rows = await supabase.update(TABLE, values, filters={"id": f"eq.{current_user.uid}"})
    result = rows[0] if rows else None

    hub: EventHub = request.app.state.poller_hub
    await hub.push(_user_key(current_user.uid), result)

    return result


@router.post("/me/delete")
async def delete_me(
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    """
    Permanently deletes the caller's account: their recorded audio, their
    `users` row (which cascades to user_recordings/achievements/leaderboards/
    user_songs_progress/user_progress via FK `ON DELETE CASCADE` — see the
    migration that added those), and finally the Firebase Auth identity
    itself so the same Google account can sign up fresh later if they want.

    Order matters: storage and the DB row are deleted first, while the
    caller's token is still valid to authenticate this request; the
    Firebase identity goes last, since once that's gone this uid can never
    authenticate another request again.

    A POST (not DELETE) on purpose — this backend's CORS only allows
    GET/POST/PUT for the Flutter *web* build (see main.py), the same
    reason `update_me` is a PUT-to-/me rather than PATCH.
    """
    uid = current_user.uid

    # Best-effort: remove this user's recorded audio from Storage. Not
    # fatal if it fails (e.g. bucket briefly unreachable) — the DB rows
    # referencing those recordings are about to be cascade-deleted either
    # way, so a leftover orphaned file is a minor cleanup issue, not a
    # reason to block account deletion.
    try:
        settings = get_settings()
        objects = await supabase.list_objects(settings.supabase_storage_bucket, f"{uid}/")
        paths = [f"{uid}/{obj['name']}" for obj in objects if obj.get("name")]
        await supabase.remove_objects(settings.supabase_storage_bucket, paths)
    except SupabaseRestError as exc:
        logger.warning("delete_me: storage cleanup failed for '%s' (non-fatal): %s", uid, exc)

    await supabase.delete(TABLE, filters={"id": f"eq.{uid}"})

    try:
        delete_firebase_user(uid)
    except Exception as exc:  # noqa: BLE001 — account data is already gone either way
        logger.error("delete_me: Firebase identity deletion failed for '%s': %s", uid, exc)
        raise HTTPException(
            status_code=502,
            detail="Your account data was deleted, but we couldn't remove your sign-in "
            "record. Contact support if you'd like it fully cleared.",
        ) from exc

    return {"deleted": True}


@router.websocket("/me/ws")
async def watch_me(websocket: WebSocket, supabase: SupabaseRest = Depends(get_supabase)):
    """
    Sends current profile immediately on connect, then only pushes when
    a write (onboarding / update) actually happens. Zero polling.
    """
    token = websocket.query_params.get("token")
    decoded = verify_id_token_string(token) if token else None
    if decoded is None:
        await websocket.close(code=4401, reason="Invalid or missing token")
        return

    uid = decoded["uid"]
    await websocket.accept()

    hub: EventHub = websocket.app.state.poller_hub
    key = _user_key(uid)
    queue = await hub.subscribe(key)

    try:
        # Send current snapshot immediately — client never waits.
        current = await _get_profile(uid, supabase)
        await websocket.send_json(current)

        # Block until a write pushes an update.
        while True:
            value = await queue.get()
            await websocket.send_json(value)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        pass
    finally:
        await hub.unsubscribe(key, queue)
