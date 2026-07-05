from unittest.mock import patch


def test_bootstrap_sets_claim_when_missing(authed_client):
    with patch("app.routers.auth.ensure_authenticated_claim", return_value=True) as mocked:
        resp = authed_client.post("/v1/auth/bootstrap")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "ok": True,
        "uid": "test-uid-123",
        "claim_set": True,
        "needs_token_refresh": True,
    }
    mocked.assert_called_once_with("test-uid-123")


def test_bootstrap_is_noop_when_claim_already_present(authed_client):
    with patch("app.routers.auth.ensure_authenticated_claim", return_value=False):
        resp = authed_client.post("/v1/auth/bootstrap")
    assert resp.status_code == 200
    body = resp.json()
    assert body["claim_set"] is False
    assert body["needs_token_refresh"] is False


def test_bootstrap_failure_returns_clean_502(authed_client):
    with patch("app.routers.auth.ensure_authenticated_claim", side_effect=RuntimeError("boom")):
        resp = authed_client.post("/v1/auth/bootstrap")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "http_error"
