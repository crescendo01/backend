from starlette.testclient import TestClient

from app.main import app
from app.supabase_client import get_supabase


def _fake_decode(uid="test-uid-123"):
    def _decode(token):
        if token == "good-token":
            return {"uid": uid, "sub": uid, "email": "test@example.com"}
        return None

    return _decode


def test_user_ws_rejects_missing_token(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.routers.users.verify_id_token_string", _fake_decode())
    app.dependency_overrides[get_supabase] = lambda: fake_supabase
    client = TestClient(app)
    try:
        with client.websocket_connect("/v1/users/me/ws") as ws:
            ws.receive_text()
        assert False, "expected the connection to be rejected"
    except Exception:
        pass
    app.dependency_overrides.pop(get_supabase, None)


def test_user_ws_pushes_initial_profile_then_updates(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.routers.users.verify_id_token_string", _fake_decode())
    app.dependency_overrides[get_supabase] = lambda: fake_supabase
    fake_supabase.seed("users", [{"id": "test-uid-123", "display_name": "Ada", "xp_total": 10}])

    client = TestClient(app)
    try:
        with client.websocket_connect("/v1/users/me/ws?token=good-token") as ws:
            first = ws.receive_json()
            assert first["display_name"] == "Ada"
            assert first["xp_total"] == 10

            # Simulate the gamification pipeline updating XP elsewhere.
            fake_supabase.tables["users"][0]["xp_total"] = 50
            second = ws.receive_json()
            assert second["xp_total"] == 50
    finally:
        app.dependency_overrides.pop(get_supabase, None)


def test_leaderboard_ws_requires_instrument_and_difficulty(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.routers.leaderboard.verify_id_token_string", _fake_decode())
    app.dependency_overrides[get_supabase] = lambda: fake_supabase
    client = TestClient(app)
    try:
        with client.websocket_connect("/v1/leaderboard/ws?token=good-token") as ws:
            ws.receive_text()
        assert False, "expected the connection to be rejected"
    except Exception:
        pass
    app.dependency_overrides.pop(get_supabase, None)


def test_leaderboard_ws_pushes_board_and_updates_on_change(monkeypatch, fake_supabase):
    monkeypatch.setattr("app.routers.leaderboard.verify_id_token_string", _fake_decode())
    app.dependency_overrides[get_supabase] = lambda: fake_supabase
    fake_supabase.seed(
        "leaderboards",
        [{"id": "lb1", "user_id": "u1", "instrument": "piano", "difficulty_level": "beginner", "score": 50}],
    )
    fake_supabase.seed("users", [{"id": "u1", "display_name": "Ada"}])

    client = TestClient(app)
    try:
        url = "/v1/leaderboard/ws?token=good-token&instrument=piano&difficulty_level=beginner"
        with client.websocket_connect(url) as ws:
            first = ws.receive_json()
            assert first[0]["score"] == 50
            assert first[0]["display_name"] == "Ada"

            fake_supabase.tables["leaderboards"][0]["score"] = 90
            second = ws.receive_json()
            assert second[0]["score"] == 90
    finally:
        app.dependency_overrides.pop(get_supabase, None)
