"""
One-off script: set `role: authenticated` on every EXISTING Firebase user.

You only need this once. New users get the claim automatically the first
time the app calls POST /v1/auth/bootstrap (see app/routers/auth.py) — this
script is just for any accounts (e.g. your own test account) created
*before* that endpoint existed.

Run locally (not on Render):
    cd crescendo-backend
    python -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    export FIREBASE_SERVICE_ACCOUNT_JSON_BASE64=...   # same value as on Render
    python scripts/backfill_existing_users_claim.py

Safe to re-run — users who already have the claim are skipped and counted
separately, not re-written.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python scripts/backfill_existing_users_claim.py` from
# the project root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firebase_admin import auth as firebase_auth  # noqa: E402

from app.firebase import _app  # noqa: E402  (reuses the same lazy singleton init)


def main() -> None:
    app = _app()
    updated = 0
    skipped = 0
    failed = 0

    page = firebase_auth.list_users(app=app)
    while page:
        for user in page.users:
            claims = dict(user.custom_claims or {})
            if claims.get("role") == "authenticated":
                skipped += 1
                continue
            try:
                claims["role"] = "authenticated"
                firebase_auth.set_custom_user_claims(user.uid, claims, app=app)
                updated += 1
                print(f"  set role=authenticated  uid={user.uid}  email={user.email}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"  FAILED  uid={user.uid}: {exc}", file=sys.stderr)
        page = page.get_next_page()

    print(f"\nDone. updated={updated} already_set={skipped} failed={failed}")
    if updated:
        print(
            "Anyone already signed into the app needs to sign out and back in "
            "(or the app needs to force-refresh their ID token) before the new "
            "claim takes effect."
        )


if __name__ == "__main__":
    main()
