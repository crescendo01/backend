"""
Thin wrapper around firebase-admin. One job now: verify the Firebase ID
token the Flutter app sends with every request, so every route below
knows *who* is calling and can scope its query to that user — the same
job Postgres RLS used to do, just enforced here instead, since the
backend (not the client) is now the only thing with database access.

(An earlier version of this file also set a `role: authenticated` custom
claim for Supabase's Firebase Third-Party Auth integration. That's gone
now — once every database call is proxied through this backend with the
service_role key, Supabase never sees a Firebase token at all, so there's
nothing for it to need a claim about.)
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from firebase_admin import exceptions as firebase_exceptions

from app.config import get_settings

logger = logging.getLogger("crescendo.firebase")


class FirebaseTokenError(Exception):
    """Raised whenever a bearer token fails verification, for any reason."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@lru_cache
def _app() -> firebase_admin.App:
    """Initialize the Firebase Admin app exactly once per process."""
    settings = get_settings()
    cred_dict = settings.firebase_credentials_dict()
    cred = credentials.Certificate(cred_dict)
    options = {}
    if settings.firebase_project_id:
        options["projectId"] = settings.firebase_project_id
    elif cred_dict.get("project_id"):
        options["projectId"] = cred_dict["project_id"]
    logger.info("Initializing Firebase Admin app for project '%s'", options.get("projectId", "<unknown>"))
    return firebase_admin.initialize_app(cred, options)


def verify_id_token(id_token: str) -> dict:
    """
    Verify a Firebase ID token sent by the Flutter app.

    Returns the decoded token claims dict (contains at least `uid`, `sub`,
    `email`, `aud`, `exp`). Raises FirebaseTokenError on anything that
    isn't a valid, current, correctly-issued token.
    """
    app = _app()  # config/credential errors here are OUR bug, not the caller's — let them 500, not 401
    try:
        return firebase_auth.verify_id_token(id_token, app=app, check_revoked=False)
    except firebase_auth.ExpiredIdTokenError as exc:
        raise FirebaseTokenError("Your session has expired. Please sign in again.") from exc
    except firebase_auth.RevokedIdTokenError as exc:
        raise FirebaseTokenError("This session has been revoked. Please sign in again.") from exc
    except firebase_auth.InvalidIdTokenError as exc:
        raise FirebaseTokenError("Invalid authentication token.") from exc
    except firebase_auth.CertificateFetchError as exc:
        # Transient: we couldn't reach Google's cert endpoint. Treat as a
        # server-side problem, not a client auth failure.
        logger.error("Could not fetch Firebase certificates: %s", exc)
        raise FirebaseTokenError(
            "Could not verify your session right now. Please try again in a moment."
        ) from exc
    except firebase_exceptions.FirebaseError as exc:
        # Any other firebase-admin failure while verifying: still about the
        # caller's token, not our config — surface as 401, not 500.
        raise FirebaseTokenError("Invalid authentication token.") from exc


def verify_id_token_string(id_token: str) -> Optional[dict]:
    """Same as `verify_id_token`, but returns None instead of raising —
    convenient for the WebSocket routes, which need to reject a bad token
    by closing the socket rather than raising an HTTPException."""
    try:
        return verify_id_token(id_token)
    except FirebaseTokenError:
        return None


def delete_user(uid: str) -> None:
    """Permanently deletes the Firebase Auth account for `uid` — the last
    step of account deletion, after the Supabase profile row (and
    everything that cascades from it) is gone. Not finding the user is
    treated as success: whatever the caller wanted (no more account with
    this uid) is already true.
    """
    app = _app()
    try:
        firebase_auth.delete_user(uid, app=app)
    except firebase_auth.UserNotFoundError:
        logger.info("delete_user: '%s' already absent from Firebase, nothing to do", uid)


def reset_for_tests() -> None:
    """Test-only helper: drop the cached app so a fresh one is built."""
    _app.cache_clear()
    for app in list(firebase_admin._apps.values()):  # type: ignore[attr-defined]
        firebase_admin.delete_app(app)


def ensure_authenticated_claim(uid: str) -> bool:
    """
    In the current architecture the backend proxies every Supabase call with
    the service_role key, so Supabase never sees the Firebase token and no
    custom claim is needed.  This stub exists so `routers/auth.py` can call
    it without error; it always returns False (= no claim set, no token
    refresh required on the client).
    """
    return False
