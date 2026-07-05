"""Gemini-backed coaching feedback, kept server-side (see gemini_service.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import CurrentUser, get_current_user
from app.schemas import FeedbackRequest, FeedbackResponse
from app.services.gemini_service import generate_feedback

router = APIRouter(prefix="/v1/feedback", tags=["feedback"])


@router.post("/generate", response_model=FeedbackResponse)
async def generate(
    payload: FeedbackRequest,
    current_user: CurrentUser = Depends(get_current_user),  # noqa: ARG001 — auth required, uid unused here
):
    feedback, source = await generate_feedback(payload)
    return FeedbackResponse(feedback=feedback, source=source)
