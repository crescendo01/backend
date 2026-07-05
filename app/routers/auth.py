"""
Auth bootstrap endpoint.

POST /v1/auth/bootstrap should be called once after every sign-in (it is
idempotent).  The Flutter client sends its Firebase ID token; this endpoint
verifies it and returns a confirmation so the app knows the backend is
reachable and the token is valid.

In the current architecture the backend uses Supabase's service_role key
for all database access, so no Supabase custom claim is needed and
``needs_token_refresh`` is always False.  The ``ensure_authenticated_claim``
helper in firebase.py is a no-op stub kept for forward-compatibility.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import CurrentUser, get_current_user
from app.firebase import ensure_authenticated_claim
from app.schemas import BootstrapResponse

logger = logging.getLogger("crescendo.auth")

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/bootstrap", response_model=BootstrapResponse)
def bootstrap(current_user: CurrentUser = Depends(get_current_user)):
    try:
        claim_was_just_set = ensure_authenticated_claim(current_user.uid)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to ensure custom claim for uid=%s", current_user.uid)
        raise HTTPException(
            status_code=502,
            detail="Could not finish setting up your account right now. Please try again.",
        ) from exc

    return BootstrapResponse(
        ok=True,
        uid=current_user.uid,
        claim_set=claim_was_just_set,
        needs_token_refresh=claim_was_just_set,
    )
