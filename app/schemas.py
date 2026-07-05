"""
Pydantic models for request/response bodies.

`ScoreBreakdownIn` intentionally mirrors `ScoreBreakdown` from
`lib/services/scoring_service.dart` field-for-field (overall, pitchAccuracy,
timingAccuracy, averageCentsOff -> overall, pitch_accuracy, timing_accuracy,
average_cents_off) so the Flutter client can send it with a straight
key-for-key JSON map, no DTO-mapping layer needed on either side.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ScoreBreakdownIn(BaseModel):
    overall: int = Field(ge=0, le=100, description="0-100 overall score, what the UI shows")
    pitch_accuracy: float = Field(ge=0, le=100)
    timing_accuracy: float = Field(ge=0, le=100)
    average_cents_off: float = Field(ge=-1200, le=1200)


class FeedbackRequest(BaseModel):
    instrument: str = Field(min_length=1, max_length=40)
    target_note: str = Field(min_length=1, max_length=20)
    score: ScoreBreakdownIn


class FeedbackResponse(BaseModel):
    feedback: str
    source: str = Field(
        description="'cache', 'fallback', or 'gemini:<model>' naming whichever "
        "model in the fallback chain actually served this response."
    )


# --- Lessons / songs ---------------------------------------------------------

class LessonAttemptRequest(BaseModel):
    score: int = Field(ge=0, le=100)
    unlock_threshold: int = Field(ge=0, le=100)


class SongStepAttemptRequest(BaseModel):
    score: int = Field(ge=0, le=100)
    total_steps: int = Field(ge=1, le=20)
    mastery_threshold: int = Field(ge=0, le=100)


# --- Users -------------------------------------------------------------------

class CreateProfileRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    display_name: str = Field(default="Musician", max_length=80)
    avatar_url: Optional[str] = None


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=80)
    avatar_url: Optional[str] = None
    instrument_main: Optional[str] = None
    level: Optional[int] = Field(default=None, ge=0)
    xp_total: Optional[int] = Field(default=None, ge=0)
    streak_days: Optional[int] = Field(default=None, ge=0)
    longest_streak_days: Optional[int] = Field(default=None, ge=0)
    last_practice_date: Optional[str] = None
    goals: Optional[list[str]] = None
    skill_level: Optional[str] = None
    subscription_tier: Optional[str] = None


class CompleteOnboardingRequest(BaseModel):
    instrument_main: str
    skill_level: str
    goals: list[str] = Field(default_factory=list)


# --- Achievements --------------------------------------------------------------

class UnlockAchievementRequest(BaseModel):
    id: str
    badge_type: str
    earned_at: Optional[str] = None


# --- Leaderboard -----------------------------------------------------------------

class SubmitScoreRequest(BaseModel):
    instrument: str
    difficulty_level: Literal["beginner", "intermediate", "advanced"]
    score: int = Field(ge=0)


# --- Recordings --------------------------------------------------------------------

class SaveRecordingRequest(BaseModel):
    audio_url: Optional[str] = None
    lesson_or_song_id: str
    score: int = Field(ge=0, le=100)
    pitch_accuracy: float = Field(ge=0, le=100)
    timing_accuracy: float = Field(ge=0, le=100)
    feedback_from_gemini: Optional[str] = None
    duration_seconds: int = Field(ge=0)


# --- Ear Training --------------------------------------------------------------------

class EarSessionStartRequest(BaseModel):
    lesson_id: str


class EarSessionCompleteRequest(BaseModel):
    """Client reports raw counts; server computes accuracy/passed/xp —
    same split of responsibility as `LessonAttemptRequest` above, so a
    tampered client can't hand itself XP it didn't earn."""
    exercises_seen: int = Field(ge=0, le=200)
    exercises_correct: int = Field(ge=0, le=200)
    hints_used: int = Field(default=0, ge=0, le=200)
    session_data: Optional[list[dict]] = None


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


# --- Auth bootstrap ----------------------------------------------------------

class BootstrapResponse(BaseModel):
    """Returned by POST /v1/auth/bootstrap.

    ``needs_token_refresh`` tells the Flutter client to force-refresh its
    Firebase ID token once so the new claim is included in subsequent
    requests.  In the current architecture (backend proxies Supabase with
    service_role, no Supabase custom claim needed) this is always False.
    """
    ok: bool = True
    uid: str
    claim_set: bool = False
    needs_token_refresh: bool = False
