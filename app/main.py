"""
Crescendo backend — FastAPI entrypoint.

Responsibilities — this got a lot bigger than "auth bridge + Gemini
proxy" once the decision was made to proxy every database/storage call
through the backend instead of giving the Flutter client any Supabase
key at all. Current scope:

  1. Auth        — verify Firebase ID tokens on every request
                    (app/dependencies.py, app/firebase.py).
  2. Lessons/songs/users/achievements/leaderboard/recordings — every
     route that used to be a direct `supabase_flutter` call from the
     Dart repositories now lives here, using the **service_role** key.
     RLS in `supabase_schema.sql` is no longer load-bearing for normal
     app traffic — these route handlers are the access control now.
  3. Storage proxy — recording upload/download goes through this
     backend's bytes, not a Supabase signed URL (app/routers/recordings.py).
  4. Realtime     — leaderboard + user-profile live updates, via
     server-side polling + WebSocket push, not Supabase's own Realtime
     protocol (see app/realtime.py for why).
  5. Gemini proxy — coaching feedback, server-side so the API key never
     ships inside the app (unchanged from the original, smaller version
     of this backend).
  6. GET /healthz — for Render's health check.

Run locally:
    uvicorn app.main:app --reload --port 8000

Run in production (this is exactly what render.yaml's startCommand does):
    uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import httpx
from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.middleware import RateLimitMiddleware
from app.realtime import PollerHub
from app.routers import achievements, auth, ear_training, feedback, health, leaderboard, lessons, recordings, songs, users


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    # Render's load balancer hits /healthz roughly every 5 seconds, by
    # design, on every web service — this is not configurable on Render's
    # side. It's harmless (doesn't affect billing/spin-down on paid plans)
    # but it drowns out real log lines, so just drop those access-log
    # entries here instead.
    class _DropHealthzAccessLogs(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return "/healthz" not in record.getMessage()

    logging.getLogger("uvicorn.access").addFilter(_DropHealthzAccessLogs())

    # httpx logs every outbound HTTP request at INFO — one line per Supabase
    # poll, which completely buries real log lines. Raise to WARNING so
    # connection errors / timeouts still surface, but routine 200s don't.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


settings = get_settings()
_configure_logging(settings.log_level)
logger = logging.getLogger("crescendo.main")

app = FastAPI(
    title="Crescendo Backend",
    description="The only thing that talks to Supabase now — full data/storage proxy, plus auth verification and a secure Gemini feedback proxy.",
    version="2.0.0",
    docs_url="/docs" if settings.is_development else None,
    redoc_url="/redoc" if settings.is_development else None,
)

app.state.poller_hub = PollerHub()

# --- CORS --------------------------------------------------------------
# Flutter mobile/desktop builds don't send a browser Origin header at all,
# so this only matters for the Flutter *web* build. Lock `ALLOWED_ORIGINS`
# down to your real web origin(s) before you ship; "*" is a fine default
# while you're still wiring things up locally.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,  # we use Bearer tokens, not cookies
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type"],
)

# --- Rate limiting -------------------------------------------------------
# Generous defaults for the data-proxy routes — they stand in for what
# used to be unthrottled direct-to-Postgres calls, so they shouldn't be
# meaningfully tighter than "fast enough that nobody notices."
app.add_middleware(
    RateLimitMiddleware,
    limits={
        "/v1/feedback/generate": settings.rate_limit_feedback_per_minute,
        "/v1/recordings/audio": 20,
    },
    window_seconds=60,
)

# --- Routers -------------------------------------------------------------
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(feedback.router)
app.include_router(users.router)
app.include_router(lessons.router)
app.include_router(ear_training.router)
app.include_router(songs.router)
app.include_router(achievements.router)
app.include_router(leaderboard.router)
app.include_router(recordings.router)


# --- Error shape -----------------------------------------------------------
# Every error response (auth failures, validation errors, unhandled
# exceptions) comes back as `{"error": {"code": ..., "message": ...}}` so
# the Dart side has exactly one shape to parse, instead of three. (Does
# not apply to WebSocket routes — those signal failure by closing the
# socket with a 4xxx code instead; see app/routers/users.py and
# app/routers/leaderboard.py.)

@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return await http_exception_handler(request, exc)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "http_error", "message": str(exc.detail)}},
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    message = f"{field}: {first.get('msg')}" if field else "Invalid request body."
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"error": {"code": "validation_error", "message": message}},
    )


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Something went wrong on our end. Please try again.",
            }
        },
    )


@app.on_event("startup")
async def _on_startup() -> None:
    logger.info("Crescendo backend starting up (environment=%s)", settings.environment)
    if settings.cors_origins == ["*"]:
        logger.warning(
            "ALLOWED_ORIGINS is unset (defaulting to '*'). Fine for development; "
            "set it to your real web origin(s) before shipping to production."
        )
    if not settings.has_gemini_config:
        logger.warning(
            "GEMINI_API_KEY is not set — /v1/feedback/generate will always return "
            "fallback template feedback, same as the Flutter app does with no key."
        )
    if not settings.has_supabase_config:
        logger.warning(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set — every lesson/"
            "song/user/achievement/leaderboard/recording route will fail until "
            "both are configured. The app has no other way to reach the database "
            "now, so this isn't optional the way the Gemini key is."
        )

    # Keep-alive: Render free tier spins down after 15 min of inactivity.
    # Ping our own /healthz every 10 min so the service stays warm.
    # RENDER_EXTERNAL_URL is set automatically by Render for all web services.
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if render_url:
        async def _keep_alive() -> None:
            async with httpx.AsyncClient() as client:
                while True:
                    await asyncio.sleep(10 * 60)  # 10 minutes
                    try:
                        await client.get(f"{render_url}/healthz", timeout=10)
                        logger.debug("Keep-alive ping sent to %s/healthz", render_url)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Keep-alive ping failed: %s", exc)

        app.state.keep_alive_task = asyncio.create_task(_keep_alive())
        logger.info("Keep-alive task started (pinging %s/healthz every 10 min)", render_url)


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    task = getattr(app.state, "keep_alive_task", None)
    if task is not None:
        task.cancel()