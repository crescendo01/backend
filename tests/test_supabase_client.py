import json

import httpx
import pytest

from app.supabase_client import SupabaseRest, SupabaseRestError


def _client_with(handler) -> SupabaseRest:
    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport)
    return SupabaseRest("https://proj.supabase.co", "service-role-key", client=async_client)


@pytest.mark.asyncio
async def test_select_sends_filters_and_parses_rows():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        return httpx.Response(200, json=[{"id": "1", "user_id": "abc"}])

    client = _client_with(handler)
    rows = await client.select(
        "user_progress",
        filters={"user_id": "eq.abc", "completed": "eq.true"},
        order="created_at.desc",
        limit=10,
    )
    assert rows == [{"id": "1", "user_id": "abc"}]
    assert "user_id=eq.abc" in seen["url"]
    assert "completed=eq.true" in seen["url"]
    assert "order=created_at.desc" in seen["url"]
    assert "limit=10" in seen["url"]
    assert seen["headers"]["apikey"] == "service-role-key"
    assert seen["headers"]["authorization"] == "Bearer service-role-key"


@pytest.mark.asyncio
async def test_select_with_join_sends_explicit_select_param():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[{"id": "1", "users": {"display_name": "Ada"}}])

    client = _client_with(handler)
    rows = await client.select(
        "leaderboards",
        filters={"instrument": "eq.piano"},
        select="*,users(display_name,avatar_url)",
    )
    assert rows[0]["users"]["display_name"] == "Ada"
    # PostgREST's embed syntax uses parens/commas — httpx encodes them in
    # the query string, so check the decoded param rather than raw text.
    assert httpx.QueryParams(httpx.URL(seen["url"]).query)["select"] == "*,users(display_name,avatar_url)"



    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "only-one"}])

    client = _client_with(handler)
    row = await client.select("users", filters={"id": "eq.x"}, single=True)
    assert row == {"id": "only-one"}


@pytest.mark.asyncio
async def test_select_single_empty_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = _client_with(handler)
    row = await client.select("users", filters={"id": "eq.missing"}, single=True)
    assert row is None


@pytest.mark.asyncio
async def test_count_parses_content_range_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["prefer"] == "count=exact"
        return httpx.Response(200, json=[], headers={"content-range": "0-0/42"})

    client = _client_with(handler)
    total = await client.count("user_progress", filters={"user_id": "eq.abc"})
    assert total == 42


@pytest.mark.asyncio
async def test_count_handles_missing_or_wildcard_range_gracefully():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], headers={"content-range": "*/0"})

    client = _client_with(handler)
    total = await client.count("user_progress", filters={})
    assert total == 0


@pytest.mark.asyncio
async def test_insert_sends_representation_preference_and_returns_row():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["prefer"] == "return=representation"
        assert json.loads(request.content) == {"user_id": "abc", "score": 90}
        return httpx.Response(201, json=[{"id": "new-id", "user_id": "abc", "score": 90}])

    client = _client_with(handler)
    row = await client.insert("user_progress", {"user_id": "abc", "score": 90})
    assert row["id"] == "new-id"


@pytest.mark.asyncio
async def test_update_sends_filters_as_query_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[{"id": "row-1", "best_score": 95}])

    client = _client_with(handler)
    rows = await client.update("user_progress", {"best_score": 95}, filters={"id": "eq.row-1"})
    assert rows[0]["best_score"] == 95
    assert "id=eq.row-1" in seen["url"]


@pytest.mark.asyncio
async def test_upsert_ignore_duplicates_sets_correct_prefer_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "resolution=ignore-duplicates" in request.headers["prefer"]
        assert "on_conflict=user_id%2Cbadge_type" in str(request.url) or "on_conflict=user_id,badge_type" in str(
            request.url
        )
        return httpx.Response(201, json=[])  # PostgREST returns [] when a duplicate was ignored

    client = _client_with(handler)
    result = await client.upsert(
        "achievements",
        {"id": "x", "user_id": "abc", "badge_type": "lessons_50"},
        on_conflict="user_id,badge_type",
        ignore_duplicates=True,
    )
    assert result is None


@pytest.mark.asyncio
async def test_upsert_merge_duplicates_returns_row():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "resolution=merge-duplicates" in request.headers["prefer"]
        return httpx.Response(201, json=[{"id": "lb-1", "score": 120}])

    client = _client_with(handler)
    result = await client.upsert("leaderboards", {"id": "lb-1", "score": 120})
    assert result == {"id": "lb-1", "score": 120}


@pytest.mark.asyncio
async def test_non_2xx_response_raises_supabase_rest_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "no rows found"})

    client = _client_with(handler)
    with pytest.raises(SupabaseRestError) as exc_info:
        await client.select("users", filters={"id": "eq.nope"}, single=True)
    assert exc_info.value.status_code == 404
    assert "no rows found" in exc_info.value.message


@pytest.mark.asyncio
async def test_upload_object_sends_bytes_with_upsert_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "/object/recordings/abc/file.m4a" in str(request.url)
        assert request.headers["x-upsert"] == "true"
        assert request.content == b"fake-audio-bytes"
        return httpx.Response(200, json={"Key": "recordings/abc/file.m4a"})

    client = _client_with(handler)
    await client.upload_object("recordings", "abc/file.m4a", b"fake-audio-bytes", "audio/m4a")


@pytest.mark.asyncio
async def test_download_object_returns_raw_bytes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"fake-audio-bytes")

    client = _client_with(handler)
    data = await client.download_object("recordings", "abc/file.m4a")
    assert data == b"fake-audio-bytes"
