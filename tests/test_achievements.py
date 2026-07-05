def test_unlock_achievement(authed_client, fake_supabase):
    resp = authed_client.post(
        "/v1/achievements",
        json={"id": "test-uid-123_lessons_50", "badge_type": "lessons_50"},
    )
    assert resp.status_code == 201
    row = resp.json()
    assert row["badge_type"] == "lessons_50"
    assert row["user_id"] == "test-uid-123"
    assert row["earned_at"] is not None


def test_duplicate_unlock_is_ignored_not_errored(authed_client, fake_supabase):
    payload = {"id": "test-uid-123_lessons_50", "badge_type": "lessons_50"}
    first = authed_client.post("/v1/achievements", json=payload)
    second = authed_client.post("/v1/achievements", json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json() == {"already_unlocked": True}
    assert len(fake_supabase.tables["achievements"]) == 1


def test_user_id_always_comes_from_token_not_body(authed_client, fake_supabase):
    resp = authed_client.post(
        "/v1/achievements",
        json={"id": "x", "badge_type": "songs_10"},
    )
    assert resp.json()["user_id"] == "test-uid-123"


def test_list_achievements_scoped_to_caller(authed_client, fake_supabase):
    fake_supabase.seed(
        "achievements",
        [
            {"id": "a1", "user_id": "test-uid-123", "badge_type": "lessons_50", "earned_at": "2026-01-01T00:00:00Z"},
            {"id": "a2", "user_id": "someone-else", "badge_type": "songs_10", "earned_at": "2026-01-01T00:00:00Z"},
        ],
    )
    resp = authed_client.get("/v1/achievements")
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["badge_type"] == "lessons_50"


def test_achievements_require_auth(client):
    assert client.get("/v1/achievements").status_code == 401
    assert client.post("/v1/achievements", json={"id": "x", "badge_type": "y"}).status_code == 401
