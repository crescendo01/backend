"""
A small async client for Supabase's REST surface (PostgREST + Storage),
using the **service_role** key. This is the only thing in the whole
system that talks to Supabase now — the Flutter app holds no Supabase
key at all, and every repository method that used to call
`supabase_flutter` directly now calls a route on this backend instead,
which calls here.

Deliberately not using the `supabase`/`postgrest`/`storage3` Python
packages: PostgREST's HTTP query syntax (`column=eq.value`, `Prefer`
headers, `Content-Range` for counts) is small, stable, public API that
hasn't changed in years — wrapping it directly in httpx is fewer
dependencies and fewer SDK-version surprises than pulling in the full
Python SDK stack for what amounts to ~150 lines of code.

Filter values are passed through in PostgREST's own operator format
(e.g. `"eq.abc123"`, `"lte.5"`, `"in.(a,b,c)"`) rather than through a
custom filter DSL — one less abstraction between this code and the
actual HTTP request, which matters when you can't step through it in a
debugger attached to Postgres itself.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger("crescendo.supabase")


class SupabaseRestError(Exception):
    """Raised on any non-2xx response from PostgREST or Storage."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"[{status_code}] {message}")


class SupabaseRest:
    def __init__(self, base_url: str, service_role_key: str, *, client: Optional[httpx.AsyncClient] = None):
        self._rest_url = base_url.rstrip("/") + "/rest/v1"
        self._storage_url = base_url.rstrip("/") + "/storage/v1"
        self._headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
        }
        self._client = client

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        if self._client is not None:
            response = await self._client.request(method, url, headers=headers, timeout=15.0, **kwargs)
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
        if response.status_code >= 400:
            detail = response.text
            try:
                detail = response.json().get("message", detail)
            except Exception:  # noqa: BLE001
                pass
            raise SupabaseRestError(response.status_code, detail)
        return response

    # --- PostgREST (database) ---------------------------------------------

    async def select(
        self,
        table: str,
        *,
        filters: Optional[dict[str, str]] = None,
        select: str = "*",
        order: Optional[str] = None,
        limit: Optional[int] = None,
        single: bool = False,
    ) -> Any:
        """
        `filters` values are pre-formatted PostgREST operators, e.g.
        `{"user_id": "eq.abc123", "completed": "eq.true"}`. `select`
        supports PostgREST's embedded-resource syntax for joins, e.g.
        `"*,users(display_name,avatar_url)"`.
        Returns a list of row dicts, or a single dict / None when
        `single=True` (maps to Dart's `.maybeSingle()`).
        """
        params: dict[str, str] = dict(filters or {})
        params["select"] = select
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = str(limit)

        response = await self._request("GET", f"{self._rest_url}/{table}", params=params)
        rows = response.json()
        if single:
            return rows[0] if rows else None
        return rows

    async def count(self, table: str, *, filters: Optional[dict[str, str]] = None) -> int:
        params: dict[str, str] = dict(filters or {})
        params["select"] = "id"
        params["limit"] = "0"
        response = await self._request(
            "GET",
            f"{self._rest_url}/{table}",
            params=params,
            headers={"Prefer": "count=exact"},
        )
        content_range = response.headers.get("content-range", "*/0")
        total = content_range.split("/")[-1]
        return int(total) if total.isdigit() else 0

    async def insert(self, table: str, row: dict) -> dict:
        response = await self._request(
            "POST",
            f"{self._rest_url}/{table}",
            json=row,
            headers={"Prefer": "return=representation"},
        )
        rows = response.json()
        return rows[0] if isinstance(rows, list) else rows

    async def update(self, table: str, values: dict, *, filters: dict[str, str]) -> list[dict]:
        response = await self._request(
            "PATCH",
            f"{self._rest_url}/{table}",
            params=dict(filters),
            json=values,
            headers={"Prefer": "return=representation"},
        )
        return response.json()

    async def upsert(
        self,
        table: str,
        row: dict,
        *,
        on_conflict: Optional[str] = None,
        ignore_duplicates: bool = False,
    ) -> Optional[dict]:
        params: dict[str, str] = {}
        if on_conflict:
            params["on_conflict"] = on_conflict
        prefer = "return=representation"
        prefer += ",resolution=" + ("ignore-duplicates" if ignore_duplicates else "merge-duplicates")
        response = await self._request(
            "POST",
            f"{self._rest_url}/{table}",
            params=params,
            json=row,
            headers={"Prefer": prefer},
        )
        rows = response.json()
        if not rows:
            return None
        return rows[0] if isinstance(rows, list) else rows

    async def delete(self, table: str, *, filters: dict[str, str]) -> list[dict]:
        """Deletes rows matching `filters`. Returns the deleted rows.
        Any FK from other tables back to this one that's set up with
        `ON DELETE CASCADE` (see the `users` table) is handled by Postgres
        itself — this call doesn't need to know about or touch those
        tables directly.
        """
        response = await self._request(
            "DELETE",
            f"{self._rest_url}/{table}",
            params=dict(filters),
            headers={"Prefer": "return=representation"},
        )
        return response.json()

    # --- Storage -------------------------------------------------------------

    async def upload_object(self, bucket: str, path: str, content: bytes, content_type: str) -> None:
        await self._request(
            "POST",
            f"{self._storage_url}/object/{bucket}/{path}",
            content=content,
            headers={"Content-Type": content_type, "x-upsert": "true"},
        )

    async def download_object(self, bucket: str, path: str) -> bytes:
        response = await self._request("GET", f"{self._storage_url}/object/{bucket}/{path}")
        return response.content

    async def list_objects(self, bucket: str, prefix: str) -> list[dict]:
        """Lists objects under `prefix` (e.g. a per-user folder). Returns
        `[]` rather than raising if the "folder" doesn't exist — Supabase
        Storage has no real directories, just key prefixes, so an empty
        prefix is a normal "nothing uploaded yet" case, not an error."""
        response = await self._request(
            "POST",
            f"{self._storage_url}/object/list/{bucket}",
            json={"prefix": prefix, "limit": 1000},
        )
        return response.json()

    async def remove_objects(self, bucket: str, paths: list[str]) -> None:
        if not paths:
            return
        await self._request(
            "DELETE",
            f"{self._storage_url}/object/{bucket}",
            json={"prefixes": paths},
        )


def get_supabase() -> SupabaseRest:
    settings = get_settings()
    if not settings.has_supabase_config:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not configured. "
            "Set both in the environment — see .env.example."
        )
    return SupabaseRest(settings.supabase_url, settings.supabase_service_role_key)
