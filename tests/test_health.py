def test_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_feedback_requires_auth(client):
    resp = client.post(
        "/v1/feedback/generate",
        json={
            "instrument": "piano",
            "target_note": "C4",
            "score": {
                "overall": 80,
                "pitch_accuracy": 80,
                "timing_accuracy": 80,
                "average_cents_off": 5,
            },
        },
    )
    assert resp.status_code == 401  # HTTPBearer's default for a missing/invalid header
    body = resp.json()
    assert "error" in body
