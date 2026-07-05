def test_get_me_returns_null_when_no_profile_yet(authed_client, fake_supabase):
    resp = authed_client.get("/v1/users/me")
    assert resp.status_code == 200
    assert resp.json() is None


def test_create_then_get_me(authed_client, fake_supabase):
    create_resp = authed_client.post(
        "/v1/users/me",
        json={"email": "ada@example.com", "display_name": "Ada", "avatar_url": None},
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    assert created["id"] == "test-uid-123"
    assert created["email"] == "ada@example.com"
    assert created["instrument_main"] == "piano"  # default, matches the schema default

    get_resp = authed_client.get("/v1/users/me")
    assert get_resp.json()["email"] == "ada@example.com"


def test_create_defaults_display_name_to_musician(authed_client, fake_supabase):
    resp = authed_client.post("/v1/users/me", json={"email": "x@example.com"})
    assert resp.json()["display_name"] == "Musician"


def test_update_me_only_changes_provided_fields(authed_client, fake_supabase):
    authed_client.post("/v1/users/me", json={"email": "ada@example.com", "display_name": "Ada"})
    resp = authed_client.put("/v1/users/me", json={"streak_days": 5})
    row = resp.json()
    assert row["streak_days"] == 5
    assert row["email"] == "ada@example.com"  # untouched
    assert row["display_name"] == "Ada"  # untouched


def test_complete_onboarding_sets_instrument_skill_and_goals(authed_client, fake_supabase):
    authed_client.post("/v1/users/me", json={"email": "ada@example.com"})
    resp = authed_client.post(
        "/v1/users/me/onboarding",
        json={"instrument_main": "guitar", "skill_level": "intermediate", "goals": ["learn_song", "daily_practice"]},
    )
    row = resp.json()
    assert row["instrument_main"] == "guitar"
    assert row["skill_level"] == "intermediate"
    assert row["goals"] == ["learn_song", "daily_practice"]


def test_users_endpoints_require_auth(client):
    assert client.get("/v1/users/me").status_code == 401
    assert client.post("/v1/users/me", json={"email": "x@example.com"}).status_code == 401
    assert client.put("/v1/users/me", json={}).status_code == 401
