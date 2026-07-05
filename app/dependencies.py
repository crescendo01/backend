"""FastAPI dependencies shared across routers."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.firebase import FirebaseTokenError, verify_id_token

_bearer_scheme = HTTPBearer(
    bearerFormat="JWT",
    description="Firebase ID token, e.g. from `FirebaseAuth.instance.currentUser.getIdToken()`.",
    auto_error=True,
)


@dataclass(frozen=True)
class CurrentUser:
    uid: str
    email: str | None
    claims: dict


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> CurrentUser:
    """
    Verify the `Authorization: Bearer <firebase-id-token>` header and
    return the calling user. Raises 401 on anything invalid/expired.
    """
    try:
        decoded = verify_id_token(credentials.credentials)
    except FirebaseTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return CurrentUser(
        uid=decoded["uid"],
        email=decoded.get("email"),
        claims=decoded,
    )
