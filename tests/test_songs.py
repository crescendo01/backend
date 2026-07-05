def test_list_songs_excludes_premium_by_default(authed_client, fake_supabase):
    fake_supabase.seed(
        "songs",
        [
            {"id": "s1", "instrument": "piano", "difficulty": 1, "is_premium_only": False},
            {"id": "s2", "instrument": "piano", "difficulty": 2, "is_premium_only": True},
        ],
    )
    resp = authed_client.get("/v1/songs", params={"instrument": "piano"})
    ids = [r["id"] for r in resp.json()]
    assert ids == ["s1"]


def test_list_songs_includes_premium_when_requested(authed_client, fake_supabase):
    fake_supabase.seed(
        "songs",
        [
            {"id": "s1", "instrument": "piano", "difficulty": 1, "is_premium_only": False},
            {"id": "s2", "instrument": "piano", "difficulty": 2, "is_premium_only": True},
        ],
    )
    resp = authed_client.get("/v1/songs", params={"instrument": "piano", "include_premium": True})
    ids = {r["id"] for r in resp.json()}
    assert ids == {"s1", "s2"}


def test_get_song_404_when_missing(authed_client, fake_supabase):
    assert authed_client.get("/v1/songs/nope").status_code == 404


def test_fetch_or_create_progress_creates_when_absent(authed_client, fake_supabase):
    resp = authed_client.post("/v1/songs/s1/progress")
    assert resp.status_code == 200
    row = resp.json()
    assert row["user_id"] == "test-uid-123"
    assert row["song_id"] == "s1"
    assert row["current_step"] == 1
    assert row["completed_steps"] == []
    assert row["mastered"] is False


def test_fetch_or_create_progress_returns_existing_without_duplicating(authed_client, fake_supabase):
    first = authed_client.post("/v1/songs/s1/progress").json()
    second = authed_client.post("/v1/songs/s1/progress").json()
    assert first["id"] == second["id"]
    assert len(fake_supabase.tables["user_songs_progress"]) == 1


def test_step_attempt_requires_existing_progress(authed_client, fake_supabase):
    resp = authed_client.post(
        "/v1/songs/s1/steps/1/attempts",
        json={"score": 90, "total_steps": 5, "mastery_threshold": 80},
    )
    assert resp.status_code == 404


def test_step_attempt_below_threshold_does_not_advance_or_complete_step(authed_client, fake_supabase):
    authed_client.post("/v1/songs/s1/progress")
    resp = authed_client.post(
        "/v1/songs/s1/steps/1/attempts",
        json={"score": 50, "total_steps": 5, "mastery_threshold": 80},
    )
    row = resp.json()
    assert row["completed_steps"] == []
    assert row["current_step"] == 2  # advances regardless of pass/fail, same as the Dart original
    assert row["best_score"] == 50
    assert row["mastered"] is False


def test_step_attempt_above_threshold_completes_step_and_advances(authed_client, fake_supabase):
    authed_client.post("/v1/songs/s1/progress")
    resp = authed_client.post(
        "/v1/songs/s1/steps/1/attempts",
        json={"score": 90, "total_steps": 5, "mastery_threshold": 80},
    )
    row = resp.json()
    assert row["completed_steps"] == [1]
    assert row["current_step"] == 2


def test_step_attempt_does_not_advance_past_final_step(authed_client, fake_supabase):
    authed_client.post("/v1/songs/s1/progress")
    resp = authed_client.post(
        "/v1/songs/s1/steps/5/attempts",
        json={"score": 90, "total_steps": 5, "mastery_threshold": 80},
    )
    row = resp.json()
    assert row["current_step"] == 5  # stays put — there's no step 6


def test_song_mastered_once_all_steps_complete(authed_client, fake_supabase):
    authed_client.post("/v1/songs/s1/progress")
    for step in range(1, 6):
        resp = authed_client.post(
            f"/v1/songs/s1/steps/{step}/attempts",
            json={"score": 90, "total_steps": 5, "mastery_threshold": 80},
        )
    row = resp.json()
    assert sorted(row["completed_steps"]) == [1, 2, 3, 4, 5]
    assert row["mastered"] is True


def test_step_attempt_best_score_never_decreases(authed_client, fake_supabase):
    authed_client.post("/v1/songs/s1/progress")
    authed_client.post("/v1/songs/s1/steps/1/attempts", json={"score": 90, "total_steps": 5, "mastery_threshold": 80})
    resp = authed_client.post(
        "/v1/songs/s1/steps/2/attempts", json={"score": 30, "total_steps": 5, "mastery_threshold": 80}
    )
    assert resp.json()["best_score"] == 90


def test_mastered_count_only_counts_callers_mastered_rows(authed_client, fake_supabase):
    fake_supabase.seed(
        "user_songs_progress",
        [
            {"id": "p1", "user_id": "test-uid-123", "song_id": "s1", "mastered": True},
            {"id": "p2", "user_id": "test-uid-123", "song_id": "s2", "mastered": False},
            {"id": "p3", "user_id": "someone-else", "song_id": "s1", "mastered": True},
        ],
    )
    resp = authed_client.get("/v1/songs/mastered-count")
    assert resp.json()["count"] == 1


def test_songs_endpoints_require_auth(client):
    assert client.get("/v1/songs", params={"instrument": "piano"}).status_code == 401
    assert client.post("/v1/songs/s1/progress").status_code == 401
