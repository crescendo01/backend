import pytest
from fastapi.testclient import TestClient

# These env vars must exist before `app.config.get_settings()` is first
# called (it's read at import time in app.main). A clearly-fake but
# well-formed service account keeps firebase_admin's Certificate() happy
# without ever making a real network call in tests (we override/monkeypatch
# anything that would).
import os
import base64
import json

_FAKE_SERVICE_ACCOUNT = {
    "type": "service_account",
    "project_id": "crescendo-test",
    "private_key_id": "test",
    "private_key": (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIBVAIBADANBgkqhkiG9w0BAQEFAASCAT4wggE6AgEAAkEAyc1qf5K2u5Q1z9C7\n"
        "-----END PRIVATE KEY-----\n"
    ),
    "client_email": "test@crescendo-test.iam.gserviceaccount.com",
    "client_id": "123456789",
    "token_uri": "https://oauth2.googleapis.com/token",
}
os.environ.setdefault(
    "FIREBASE_SERVICE_ACCOUNT_JSON_BASE64",
    base64.b64encode(json.dumps(_FAKE_SERVICE_ACCOUNT).encode()).decode(),
)
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("GEMINI_API_KEY", "")
os.environ.setdefault("SUPABASE_URL", "https://test-project.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("REALTIME_POLL_INTERVAL_SECONDS", "0.05")

from app.config import get_settings  # noqa: E402
from app.dependencies import CurrentUser, get_current_user  # noqa: E402
from app.main import app  # noqa: E402
from app.supabase_client import get_supabase  # noqa: E402
from tests.fakes import FakeSupabase  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_supabase():
    return FakeSupabase()


@pytest.fixture
def authed_client(fake_supabase):
    """A TestClient where auth is pre-satisfied as a fixed fake user, and
    every Supabase call hits an in-memory fake instead of the network."""

    def _fake_user() -> CurrentUser:
        return CurrentUser(uid="test-uid-123", email="test@example.com", claims={"uid": "test-uid-123"})

    app.dependency_overrides[get_current_user] = _fake_user
    app.dependency_overrides[get_supabase] = lambda: fake_supabase
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_supabase, None)


@pytest.fixture
def client():
    """A plain TestClient with no auth override — for testing 401s, health, etc."""
    with TestClient(app) as c:
        yield c
