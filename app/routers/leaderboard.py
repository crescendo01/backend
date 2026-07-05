"""
Leaderboard routes — event-driven, no polling.

On WS connect: fetch current board once, send immediately.
On score submit: push updated board to all subscribers for that
instrument+difficulty_level combo. Zero background polling.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect

from app.dependencies import CurrentUser, get_current_user
from app.firebase import verify_id_token_string
from app.realtime import EventHub
from app.schemas import SubmitScoreRequest
from app.supabase_client import SupabaseRest, get_supabase

logger = logging.getLogger("crescendo.leaderboard")

router = APIRouter(prefix="/v1/leaderboard", tags=["leaderboard"])

TABLE = "leaderboards"


def _flatten_user_join(row: dict) -> dict:
    flat = dict(row)
    joined = flat.pop("users", None) or {}
    flat.setdefault("display_name", joined.get("display_name"))
    flat.setdefault("avatar_url", joined.get("avatar_url"))
    return flat


async def _fetch_top(
    supabase: SupabaseRest, instrument: str, difficulty_level: str, limit: int = 100
) -> list[dict]:
    rows = await supabase.select(
        TABLE,
        filters={
            "instrument": f"eq.{instrument}",
            "difficulty_level": f"eq.{difficulty_level}",
        },
        select="*,users(display_name,avatar_url)",
        order="score.desc",
        limit=limit,
    )
    return [_flatten_user_join(r) for r in rows]


def _board_key(instrument: str, difficulty_level: str) -> str:
    return f"leaderboard:{instrument}:{difficulty_level}"


@router.get("")
async def get_leaderboard(
    instrument: str,
    difficulty_level: str,
    limit: int = 100,
    _current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    return await _fetch_top(supabase, instrument, difficulty_level, limit)


@router.get("/me")
async def get_my_entry(
    instrument: str,
    difficulty_level: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    row = await supabase.select(
        TABLE,
        filters={
            "user_id": f"eq.{current_user.uid}",
            "instrument": f"eq.{instrument}",
            "difficulty_level": f"eq.{difficulty_level}",
        },
        select="*,users(display_name,avatar_url)",
        single=True,
    )
    return _flatten_user_join(row) if row else None


@router.post("/score")
async def submit_score(
    payload: SubmitScoreRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    """Only improves, never lowers. After upsert, pushes the fresh
    top-100 to any open WS subscribers for that board."""
    existing = await supabase.select(
        TABLE,
        filters={
            "user_id": f"eq.{current_user.uid}",
            "instrument": f"eq.{payload.instrument}",
            "difficulty_level": f"eq.{payload.difficulty_level}",
        },
        single=True,
    )
    if existing is not None and int(existing.get("score") or 0) >= payload.score:
        return existing

    row: dict[str, Any] = {
        "user_id": current_user.uid,
        "instrument": payload.instrument,
        "difficulty_level": payload.difficulty_level,
        "score": payload.score,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if existing is not None:
        row["id"] = existing["id"]

    result = await supabase.upsert(TABLE, row, on_conflict="user_id,instrument,difficulty_level")

    # Push updated board to any open WS subscribers — no polling needed.
    hub: EventHub = request.app.state.poller_hub
    key = _board_key(payload.instrument, payload.difficulty_level)
    if hub.active_keys and key in hub.active_keys:
        updated_board = await _fetch_top(supabase, payload.instrument, payload.difficulty_level)
        await hub.push(key, updated_board)

    return result or row


@router.websocket("/ws")
async def watch_leaderboard(
    websocket: WebSocket,
    supabase: SupabaseRest = Depends(get_supabase),
):
    """
    Sends current leaderboard immediately on connect, then only pushes
    when a score submission actually changes it. No polling.
    """
    instrument = websocket.query_params.get("instrument")
    difficulty_level = websocket.query_params.get("difficulty_level")
    token = websocket.query_params.get("token")

    if not instrument or not difficulty_level:
        await websocket.close(code=4400, reason="instrument and difficulty_level are required")
        return

    decoded = verify_id_token_string(token) if token else None
    if decoded is None:
        await websocket.close(code=4401, reason="Invalid or missing token")
        return

    await websocket.accept()

    hub: EventHub = websocket.app.state.poller_hub
    key = _board_key(instrument, difficulty_level)
    queue = await hub.subscribe(key)

    try:
        # Send current snapshot immediately on connect.
        current = await _fetch_top(supabase, instrument, difficulty_level)
        await websocket.send_json(current)

        # Wait silently — only wakes up when a score is submitted.
        while True:
            value = await queue.get()
            await websocket.send_json(value)
    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        pass
    finally:
        await hub.unsubscribe(key, queue)
