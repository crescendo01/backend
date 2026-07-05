"""
Replaces every direct Supabase call in `recording_repository.dart`,
including Storage — per your choice to proxy uploads/downloads through
the backend rather than hand the client a signed URL straight to
Supabase. The trade-off is real and worth knowing: every recording's
bytes now flow through Render too, not just Supabase, which costs more
bandwidth/latency than a signed URL would (the client would otherwise
talk to Supabase directly for the file transfer, just with a short-lived
scoped URL instead of a permanent key). If that becomes a real cost or
speed problem at scale, swapping `upload_audio`/`download_audio` below
for signed-URL minting is a small, contained change — nothing else in
this router or the Dart side needs to know which approach is in use.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response

from app.config import get_settings
from app.dependencies import CurrentUser, get_current_user
from app.schemas import SaveRecordingRequest
from app.supabase_client import SupabaseRest, SupabaseRestError, get_supabase

router = APIRouter(prefix="/v1/recordings", tags=["recordings"])

TABLE = "user_recordings"

# A conservative cap matched to what a single lesson/song-step take could
# plausibly be — protects the backend's memory/bandwidth from an
# oversized or malformed upload. Practice takes are seconds long; this
# is generous, not tight.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post("/audio")
async def upload_audio(
    file: UploadFile,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    """Replaces `RecordingRepository.uploadAndGetSignedUrl`'s upload half.
    Returns a storage *path*, not a signed URL — URLs expire and
    shouldn't be persisted; fetch a fresh one (or proxy bytes, as here)
    whenever playback is actually needed via GET /v1/recordings/audio/{path}."""
    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Audio file is too large.")

    extension = (file.filename or "").rsplit(".", 1)[-1] if "." in (file.filename or "") else "m4a"
    storage_path = f"{current_user.uid}/{int(datetime.now(timezone.utc).timestamp() * 1000)}.{extension}"

    settings = get_settings()
    content_type = file.content_type or "application/octet-stream"
    await supabase.upload_object(settings.supabase_storage_bucket, storage_path, content, content_type)

    return {"storage_path": storage_path}


@router.get("/audio/{storage_path:path}")
async def download_audio(
    storage_path: str,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    """Replaces the playback half. Enforces the same per-user folder
    isolation the original Storage RLS policy did
    (`(storage.foldername(name))[1] = current_firebase_uid()`), just in
    route logic now instead of Postgres."""
    owner_folder = storage_path.split("/", 1)[0]
    if owner_folder != current_user.uid:
        raise HTTPException(status_code=403, detail="Not your recording.")

    settings = get_settings()
    try:
        content = await supabase.download_object(settings.supabase_storage_bucket, storage_path)
    except SupabaseRestError as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail="Recording not found.") from exc
        raise

    return Response(content=content, media_type="application/octet-stream")


@router.post("", status_code=201)
async def save_recording(
    payload: SaveRecordingRequest,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    row = {
        **payload.model_dump(),
        "user_id": current_user.uid,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return await supabase.insert(TABLE, row)


@router.get("")
async def list_recordings(
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user),
    supabase: SupabaseRest = Depends(get_supabase),
):
    return await supabase.select(
        TABLE,
        filters={"user_id": f"eq.{current_user.uid}"},
        order="created_at.desc",
        limit=limit,
    )
