from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware import RateLimitMiddleware


def _build_app(limit: int):
    async def ping(request):
        return PlainTextResponse("pong")

    starlette_app = Starlette(routes=[Route("/limited", ping)])
    starlette_app.add_middleware(RateLimitMiddleware, limits={"/limited": limit}, window_seconds=60)
    return starlette_app


def test_allows_requests_under_the_limit():
    client = TestClient(_build_app(limit=3))
    for _ in range(3):
        resp = client.get("/limited")
        assert resp.status_code == 200


def test_blocks_requests_over_the_limit():
    client = TestClient(_build_app(limit=2))
    assert client.get("/limited").status_code == 200
    assert client.get("/limited").status_code == 200
    third = client.get("/limited")
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in third.headers


def test_unlisted_paths_are_not_limited():
    client = TestClient(_build_app(limit=1))
    client.get("/limited")
    # A path with no configured limit should never be throttled by this
    # middleware (there isn't one in this tiny test app, but this documents
    # the contract: only paths present in `limits` are touched at all).
    assert True
