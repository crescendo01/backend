from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.schemas import FeedbackRequest, ScoreBreakdownIn
from app.services import gemini_service

VALID_BODY = {
    "instrument": "piano",
    "target_note": "C4",
    "score": {
        "overall": 92,
        "pitch_accuracy": 95.0,
        "timing_accuracy": 90.0,
        "average_cents_off": 4.0,
    },
}


def test_feedback_validation_error_shape(authed_client):
    resp = authed_client.post(
        "/v1/feedback/generate",
        json={"instrument": "piano", "target_note": "C4", "score": {"overall": 150}},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"


def test_feedback_falls_back_without_gemini_key(authed_client):
    # conftest sets GEMINI_API_KEY="" so this exercises the no-key fallback path.
    gemini_service._feedback_cache.clear()
    resp = authed_client.post("/v1/feedback/generate", json=VALID_BODY)
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "fallback"
    assert "target" in body["feedback"].lower() or "on target" in body["feedback"].lower()


@pytest.mark.parametrize(
    "overall,expected_snippet",
    [(95, "Excellent"), (80, "Nice work"), (60, "Good attempt"), (20, "Almost there")],
)
def test_fallback_feedback_thresholds(overall, expected_snippet):
    req = FeedbackRequest(
        instrument="guitar",
        target_note="E2",
        score=ScoreBreakdownIn(overall=overall, pitch_accuracy=50, timing_accuracy=50, average_cents_off=0),
    )
    assert expected_snippet in gemini_service._fallback_feedback(req)


def test_cache_key_is_stable_for_rounded_scores():
    req_a = FeedbackRequest(
        instrument="piano",
        target_note="C4",
        score=ScoreBreakdownIn(overall=88, pitch_accuracy=90.4, timing_accuracy=85.6, average_cents_off=3),
    )
    req_b = FeedbackRequest(
        instrument="piano",
        target_note="C4",
        score=ScoreBreakdownIn(overall=88, pitch_accuracy=90.2, timing_accuracy=85.9, average_cents_off=3),
    )
    assert gemini_service._cache_key(req_a) == gemini_service._cache_key(req_b)


@pytest.mark.asyncio
async def test_generate_feedback_uses_gemini_when_configured():
    req = FeedbackRequest(
        instrument="violin",
        target_note="A4",
        score=ScoreBreakdownIn(overall=70, pitch_accuracy=70, timing_accuracy=70, average_cents_off=10),
    )
    gemini_service._feedback_cache.clear()

    fake_response = AsyncMock()
    fake_response.raise_for_status = lambda: None
    fake_response.json = lambda: {
        "candidates": [{"content": {"parts": [{"text": "Great bow control — keep your wrist relaxed."}]}}]
    }

    with patch("app.services.gemini_service.get_settings") as mock_settings, patch(
        "httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)
    ):
        mock_settings.return_value.has_gemini_config = True
        mock_settings.return_value.gemini_api_key = "fake-key"
        mock_settings.return_value.gemini_model = "gemini-3.5-flash"

        feedback, source = await gemini_service.generate_feedback(req)

    assert source == "gemini"
    assert "bow control" in feedback.lower()


@pytest.mark.asyncio
async def test_generate_feedback_falls_back_on_gemini_error():
    req = FeedbackRequest(
        instrument="flute",
        target_note="G4",
        score=ScoreBreakdownIn(overall=40, pitch_accuracy=40, timing_accuracy=40, average_cents_off=30),
    )
    gemini_service._feedback_cache.clear()

    with patch("app.services.gemini_service.get_settings") as mock_settings, patch(
        "httpx.AsyncClient.post", new=AsyncMock(side_effect=httpx.TimeoutException("slow"))
    ):
        mock_settings.return_value.has_gemini_config = True
        mock_settings.return_value.gemini_api_key = "fake-key"
        mock_settings.return_value.gemini_model = "gemini-3.5-flash"

        feedback, source = await gemini_service.generate_feedback(req)

    assert source == "fallback"
    assert "Almost there" in feedback
