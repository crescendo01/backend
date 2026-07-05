def test_upload_then_download_round_trip(authed_client, fake_supabase):
    upload_resp = authed_client.post(
        "/v1/recordings/audio",
        files={"file": ("take1.m4a", b"fake-audio-bytes", "audio/m4a")},
    )
    assert upload_resp.status_code == 200
    storage_path = upload_resp.json()["storage_path"]
    assert storage_path.startswith("test-uid-123/")
    assert storage_path.endswith(".m4a")

    download_resp = authed_client.get(f"/v1/recordings/audio/{storage_path}")
    assert download_resp.status_code == 200
    assert download_resp.content == b"fake-audio-bytes"


def test_download_rejects_another_users_recording(authed_client, fake_supabase):
    fake_supabase.storage["recordings/someone-else/take1.m4a"] = b"not yours"
    resp = authed_client.get("/v1/recordings/audio/someone-else/take1.m4a")
    assert resp.status_code == 403


def test_download_404_when_object_missing(authed_client, fake_supabase):
    resp = authed_client.get("/v1/recordings/audio/test-uid-123/missing.m4a")
    assert resp.status_code == 404


def test_upload_rejects_oversized_file(authed_client, fake_supabase):
    too_big = b"x" * (25 * 1024 * 1024 + 1)
    resp = authed_client.post(
        "/v1/recordings/audio",
        files={"file": ("big.m4a", too_big, "audio/m4a")},
    )
    assert resp.status_code == 413


def test_save_and_list_recording_metadata(authed_client, fake_supabase):
    save_resp = authed_client.post(
        "/v1/recordings",
        json={
            "audio_url": "test-uid-123/take1.m4a",
            "lesson_or_song_id": "lesson-1",
            "score": 88,
            "pitch_accuracy": 90.5,
            "timing_accuracy": 85.0,
            "feedback_from_gemini": "Nice work!",
            "duration_seconds": 12,
        },
    )
    assert save_resp.status_code == 201
    saved = save_resp.json()
    assert saved["user_id"] == "test-uid-123"
    assert saved["score"] == 88

    list_resp = authed_client.get("/v1/recordings")
    assert len(list_resp.json()) == 1
    assert list_resp.json()[0]["lesson_or_song_id"] == "lesson-1"


def test_list_recordings_scoped_to_caller(authed_client, fake_supabase):
    fake_supabase.seed(
        "user_recordings",
        [
            {"id": "r1", "user_id": "test-uid-123", "lesson_or_song_id": "l1", "created_at": "2026-01-01T00:00:00Z"},
            {"id": "r2", "user_id": "someone-else", "lesson_or_song_id": "l2", "created_at": "2026-01-01T00:00:00Z"},
        ],
    )
    resp = authed_client.get("/v1/recordings")
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["id"] == "r1"


def test_recordings_require_auth(client):
    assert client.get("/v1/recordings").status_code == 401
    assert client.post("/v1/recordings/audio", files={"file": ("x.m4a", b"y", "audio/m4a")}).status_code == 401
