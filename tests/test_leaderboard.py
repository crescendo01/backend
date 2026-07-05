def test_leaderboard_joins_and_flattens_display_name(authed_client, fake_supabase):
    fake_supabase.seed(
        "users",
        [{"id": "u1", "display_name": "Ada Lovelace", "avatar_url": "https://example.com/ada.png"}],
    )
    fake_supabase.seed(
        "leaderboards",
        [{"id": "lb1", "user_id": "u1", "instrument": "piano", "difficulty_level": "beginner", "score": 100}],
    )

    resp = authed_client.get("/v1/leaderboard", params={"instrument": "piano", "difficulty_level": "beginner"})
    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["display_name"] == "Ada Lovelace"
    assert row["avatar_url"] == "https://example.com/ada.png"
    assert "users" not in row


def test_my_entry_also_joins_display_name(authed_client, fake_supabase):
    fake_supabase.seed("users", [{"id": "test-uid-123", "display_name": "Me", "avatar_url": None}])
    fake_supabase.seed(
        "leaderboards",
        [{"id": "lb1", "user_id": "test-uid-123", "instrument": "piano", "difficulty_level": "beginner", "score": 50}],
    )
    resp = authed_client.get("/v1/leaderboard/me", params={"instrument": "piano", "difficulty_level": "beginner"})
    assert resp.json()["display_name"] == "Me"


def test_flatten_user_join_handles_missing_join_gracefully():
    from app.routers.leaderboard import _flatten_user_join

    flat = _flatten_user_join({"id": "lb1", "score": 10, "users": None})
    assert flat["display_name"] is None
    assert flat["avatar_url"] is None
    assert "users" not in flat


def test_submit_score_creates_new_entry(authed_client, fake_supabase):
    resp = authed_client.post(
        "/v1/leaderboard/score",
        json={"instrument": "piano", "difficulty_level": "beginner", "score": 80},
    )
    assert resp.status_code == 200
    assert resp.json()["score"] == 80


def test_submit_score_only_improves_never_lowers(authed_client, fake_supabase):
    authed_client.post("/v1/leaderboard/score", json={"instrument": "piano", "difficulty_level": "beginner", "score": 80})
    resp = authed_client.post(
        "/v1/leaderboard/score", json={"instrument": "piano", "difficulty_level": "beginner", "score": 50}
    )
    assert resp.json()["score"] == 80  # unchanged — lower score ignored
    assert len(fake_supabase.tables["leaderboards"]) == 1


def test_submit_score_updates_when_higher(authed_client, fake_supabase):
    authed_client.post("/v1/leaderboard/score", json={"instrument": "piano", "difficulty_level": "beginner", "score": 80})
    resp = authed_client.post(
        "/v1/leaderboard/score", json={"instrument": "piano", "difficulty_level": "beginner", "score": 95}
    )
    assert resp.json()["score"] == 95
    assert len(fake_supabase.tables["leaderboards"]) == 1  # updated in place, not duplicated


def test_submit_score_is_scoped_per_board(authed_client, fake_supabase):
    authed_client.post("/v1/leaderboard/score", json={"instrument": "piano", "difficulty_level": "beginner", "score": 80})
    authed_client.post(
        "/v1/leaderboard/score", json={"instrument": "guitar", "difficulty_level": "beginner", "score": 60}
    )
    assert len(fake_supabase.tables["leaderboards"]) == 2


def test_my_entry_returns_null_when_not_on_board(authed_client, fake_supabase):
    resp = authed_client.get("/v1/leaderboard/me", params={"instrument": "piano", "difficulty_level": "beginner"})
    assert resp.status_code == 200
    assert resp.json() is None


def test_leaderboard_requires_auth(client):
    assert client.get("/v1/leaderboard", params={"instrument": "piano", "difficulty_level": "beginner"}).status_code == 401
    assert client.post(
        "/v1/leaderboard/score", json={"instrument": "piano", "difficulty_level": "beginner", "score": 1}
    ).status_code == 401
