# Crescendo backend (v2 — full data/storage proxy)

This is now the *only* thing that talks to Supabase. The Flutter app
holds no Supabase key at all — every read and write that used to go
straight from a Dart repository to `supabase_flutter` now calls a route
here instead, authenticated with your Firebase ID token. This backend
uses Supabase's **service_role** key to actually do the work; its route
handlers are the access control now, the job Postgres RLS used to do.

If you're looking for the smaller, earlier version of this backend (auth
bridge + Gemini proxy only, Flutter still talking to Supabase directly
with the anon key) — that's a different, simpler architecture. This
version replaces it entirely; there's no in-between.

## What's in here

| Area | Router | Replaces |
|---|---|---|
| Auth | `app/dependencies.py`, `app/firebase.py` | Verifying who's calling — every route needs this |
| Lessons | `app/routers/lessons.py` | `lesson_repository.dart` |
| Songs | `app/routers/songs.py` | `song_repository.dart` |
| Users | `app/routers/users.py` | `user_repository.dart` (REST + a WebSocket profile watch) |
| Achievements | `app/routers/achievements.py` | `achievement_repository.dart` |
| Leaderboard | `app/routers/leaderboard.py` | `leaderboard_repository.dart` (REST + a WebSocket live-board watch) |
| Recordings | `app/routers/recordings.py` | `recording_repository.dart`, including Storage upload/download |
| Gemini feedback | `app/routers/feedback.py` | the direct-from-app Gemini call (unchanged from before) |

Full endpoint list, request/response shapes: run it locally and open
`/docs` (only enabled when `ENVIRONMENT=development`), or just read
`app/routers/*.py` — every route is short and the docstrings explain the
*why*, not just the *what*.

## A real trade-off, not a free upgrade

Removing the Supabase key from the client doesn't reduce risk the way
removing the Gemini key did — the anon key was *designed* to be public;
your security model was always RLS, not key secrecy. What this actually
buys you is a single point of control over every database/storage
operation. What it costs:

- **Every request now has two network hops** (app → this backend →
  Supabase) instead of one. More latency, more places for something to
  time out.
- **This backend is now a single point of failure for the entire app**,
  not just auth and feedback. If it's down, nothing works — lessons,
  songs, leaderboard, recordings, all of it.
- **"Realtime" here means server-side polling + WebSocket push, not
  Supabase's own Realtime protocol** (see `app/realtime.py`'s docstring
  for the full reasoning). Updates land within `REALTIME_POLL_INTERVAL_SECONDS`
  (2s default), not instantly. For a leaderboard/XP use case this is
  imperceptible; if you build something latency-sensitive later, this is
  the file to revisit.
- **Recording uploads/downloads proxy through Render's bandwidth**, not
  a direct, scoped Supabase signed URL. More backend bandwidth/cost than
  the signed-URL alternative would have been. `app/routers/recordings.py`'s
  docstring explains exactly what to change if this becomes a real cost
  or speed problem.

None of this means it was the wrong call for what you asked for — it's
what "wire all of it through the backend" actually means once you see it
built. Flagging it so it's a known trade-off, not a surprise later.

## One-time setup

### 1. Get a Firebase service account key
Firebase Console → ⚙️ Project Settings → **Service Accounts** → Generate
new private key. Treat the downloaded JSON like a password.

### 2. Get your Supabase service_role key
Supabase Studio → Project Settings → API → **service_role** key (NOT the
anon key — this one bypasses RLS, which is exactly why it now lives only
here, never in the app).

### 3. Deploy to Render
Push this folder to a repo, then either:
- **Blueprint** (recommended): Render dashboard → **New +** → **Blueprint**
  → pick the repo. Render reads `render.yaml`; you'll be prompted for the
  secrets marked `sync: false` (`FIREBASE_SERVICE_ACCOUNT_JSON_BASE64`,
  `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GEMINI_API_KEY`, `ALLOWED_ORIGINS`).
- **Manual web service**: Runtime `Python 3`, Build Command
  `pip install -r requirements.txt`, Start Command
  `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, Health Check Path
  `/healthz` — then add every env var from `.env.example` yourself.

A `Dockerfile` is included too if you'd rather deploy as a Docker service
— functionally identical, just a different build path.

### 4. Point the Flutter app at this backend
See `flutter_patch/README.md` in the top-level delivery — set
`BACKEND_BASE_URL` and you're done; no Supabase URL/key go in
`--dart-define` anymore at all.

### 5. You can skip the old Supabase Third-Party Auth dashboard step
If you set that up for the earlier (v1) version of this backend, you can
remove it — it's not used anymore. Once every database call goes through
this backend with the service_role key, Supabase never sees a Firebase
token, so there's nothing for it to need a claim about. RLS in
`supabase_schema.sql` still exists and still works as defense-in-depth
(in case the service_role key were ever exposed), it's just not the
*primary* access control anymore — these route handlers are.

## Local development

```bash
cp .env.example .env
# fill in: FIREBASE_SERVICE_ACCOUNT_JSON_BASE64, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt

uvicorn app.main:app --reload --port 8000
# interactive docs at http://localhost:8000/docs (ENVIRONMENT=development only)
```

Run the test suite (90 tests, no real Firebase/Supabase/Gemini
credentials needed — Firebase token verification, the Supabase REST/Storage
client, and the realtime poller are all tested against fakes/mocks, not
the network; see `tests/fakes.py` for the in-memory Supabase double):
```bash
python -m pytest -v
```

## Project layout

```
app/
  main.py              FastAPI app: CORS, rate limiting, routers, error handling, PollerHub
  config.py             Settings, read from environment variables
  firebase.py             Firebase Admin wrapper: ID token verification only now
  dependencies.py           The `get_current_user` auth dependency every route uses
  supabase_client.py          Async PostgREST + Storage client (service_role), used by every router
  realtime.py                  Generic polling-based "push when changed" hub (see its docstring)
  middleware.py                  Small dependency-free in-memory rate limiter
  schemas.py                       Request models for every router
  routers/
    health.py             GET / and /healthz
    feedback.py             POST /v1/feedback/generate
    users.py                  GET/POST/PUT /v1/users/me, POST /v1/users/me/onboarding, WS /v1/users/me/ws
    lessons.py                  GET /v1/lessons, /v1/lessons/{id}, /v1/lessons/{id}/next,
                                  /v1/lessons/progress, /v1/lessons/completed-count,
                                  POST /v1/lessons/{id}/attempts
    songs.py                       GET /v1/songs, /v1/songs/{id}, /v1/songs/mastered-count,
                                     POST /v1/songs/{id}/progress, /v1/songs/{id}/steps/{n}/attempts
    achievements.py                  GET/POST /v1/achievements
    leaderboard.py                     GET /v1/leaderboard, /v1/leaderboard/me,
                                         POST /v1/leaderboard/score, WS /v1/leaderboard/ws
    recordings.py                        POST /v1/recordings/audio (upload),
                                           GET /v1/recordings/audio/{path} (download),
                                           POST/GET /v1/recordings (metadata)
  services/
    gemini_service.py                       Gemini call + cache + fallback templates
tests/                                        90 tests covering every router, the realtime
                                                hub in isolation, both WebSocket routes
                                                end-to-end, and the Supabase client itself
```

## Bonus fix along the way

`LeaderboardEntryModel`'s own doc comment said it's "joined with display
info for the UI" — but the original `fetchTop()` never actually joined
anything, so every leaderboard entry showed "Musician" with no avatar,
regardless of who actually set the score. `app/routers/leaderboard.py`
does the join PostgREST supports natively (`leaderboards.user_id ->
users.id`) and flattens it to the flat shape the Dart model already
expects — no Dart-side change needed for this one, it just starts
showing real names.

## Calling it directly (curl, Postman, etc.)

```bash
# Get a Firebase ID token however you like (e.g. print
# `await FirebaseAuth.instance.currentUser?.getIdToken()` from the app once).

curl https://<your-render-url>/v1/lessons?instrument=piano \
  -H "Authorization: Bearer <firebase-id-token>"

curl -X POST https://<your-render-url>/v1/leaderboard/score \
  -H "Authorization: Bearer <firebase-id-token>" \
  -H "Content-Type: application/json" \
  -d '{"instrument": "piano", "difficulty_level": "beginner", "score": 88}'

# WebSocket (auth via query param, not a header — browsers can't set
# custom headers on a WS handshake):
# wss://<your-render-url>/v1/leaderboard/ws?token=<id-token>&instrument=piano&difficulty_level=beginner
```
