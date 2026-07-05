"""
Central configuration, loaded from environment variables (Render's
"Environment" tab, or a local .env file when developing).

Nothing here has a hard-coded secret. Every credential is read from the
environment so this codebase is safe to commit / share.
"""
from __future__ import annotations

import json
import base64
from functools import lru_cache
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- General -------------------------------------------------------
    environment: str = "production"  # "production" | "development"
    log_level: str = "INFO"

    # --- Firebase Admin --------------------------------------------------
    # Used to verify the Firebase ID tokens the Flutter app sends, and to
    # set the `role: authenticated` custom claim Supabase's Firebase
    # Third-Party Auth integration relies on.
    #
    # Provide ONE of the two. Base64 is recommended on Render because some
    # dashboards mangle raw multi-line/quote-heavy env var values; base64
    # has no special characters so it always survives copy/paste intact.
    firebase_service_account_json: Optional[str] = None
    firebase_service_account_json_base64: Optional[str] = None
    firebase_project_id: Optional[str] = None  # optional extra sanity check

    # --- Gemini ----------------------------------------------------------
    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-1.5-flash"

    # --- Supabase (service_role) ------------------------------------------
    # The backend is now the *only* thing that talks to Supabase — the
    # Flutter app no longer holds a Supabase key at all. This uses the
    # service_role key deliberately (it bypasses RLS); the backend's route
    # handlers are the access control now, the same job RLS used to do.
    # Get both from Supabase Studio -> Project Settings -> API.
    supabase_url: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    supabase_storage_bucket: str = "recordings"

    # (No poll interval needed — the hub is event-driven, not timer-driven.)

    # --- CORS --------------------------------------------------------------
    # Comma-separated list of allowed origins, e.g.
    # "https://crescendo-xd.web.app,https://crescendo.app"
    # Leave unset/"*" during development; lock this down before you ship.
    allowed_origins: str = "*"

    # --- Rate limiting (simple in-memory sliding window, per client IP) ---
    rate_limit_feedback_per_minute: int = 30

    @field_validator("environment")
    @classmethod
    def _normalize_environment(cls, v: str) -> str:
        v = v.lower().strip()
        return v if v in {"production", "development"} else "production"

    @property
    def cors_origins(self) -> List[str]:
        raw = (self.allowed_origins or "*").strip()
        if raw == "*" or raw == "":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    def firebase_credentials_dict(self) -> dict:
        """Resolve the service account JSON, preferring the base64 form."""
        if self.firebase_service_account_json_base64:
            try:
                decoded = base64.b64decode(self.firebase_service_account_json_base64).decode("utf-8")
                return json.loads(decoded)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    "FIREBASE_SERVICE_ACCOUNT_JSON_BASE64 is set but is not valid "
                    "base64-encoded JSON. Re-copy it from your service account key file."
                ) from exc
        if self.firebase_service_account_json:
            try:
                return json.loads(self.firebase_service_account_json)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    "FIREBASE_SERVICE_ACCOUNT_JSON is set but is not valid JSON. "
                    "Paste the *entire* contents of the downloaded service account "
                    "key file, or switch to FIREBASE_SERVICE_ACCOUNT_JSON_BASE64."
                ) from exc
        raise RuntimeError(
            "No Firebase credentials configured. Set either "
            "FIREBASE_SERVICE_ACCOUNT_JSON_BASE64 (recommended) or "
            "FIREBASE_SERVICE_ACCOUNT_JSON in the environment."
        )

    @property
    def has_gemini_config(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def has_supabase_config(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_role_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
