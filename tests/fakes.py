"""
An in-memory stand-in for `SupabaseRest`, used only in tests. Implements
just enough of PostgREST's filter semantics (`eq`, `lte`, `gt`, `in`,
`true`/`false`) to exercise the actual merge/business logic in each
router — best_score-only-increases, mastery thresholds, score-only-improves,
etc. — against realistic data, without a real Supabase project.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional


def _matches(row: dict, filters: dict[str, str]) -> bool:
    for column, expr in filters.items():
        if column == "select":
            continue
        op, _, value = expr.partition(".")
        actual = row.get(column)
        if op == "eq":
            if str(actual).lower() != value.lower() and actual != value:
                # handles bool columns where actual=True but value="true"
                if not (isinstance(actual, bool) and str(actual).lower() == value.lower()):
                    return False
        elif op == "lte":
            if actual is None or float(actual) > float(value):
                return False
        elif op == "gt":
            if actual is None or float(actual) <= float(value):
                return False
        elif op == "in":
            options = value.strip("()").split(",")
            if str(actual) not in options:
                return False
        else:
            raise NotImplementedError(f"FakeSupabase doesn't support operator '{op}' yet")
    return True


class FakeSupabase:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.storage: dict[str, bytes] = {}

    def seed(self, table: str, rows: list[dict]) -> None:
        self.tables.setdefault(table, []).extend(rows)

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
        rows = [r for r in self.tables.get(table, []) if _matches(r, filters or {})]
        if order:
            col, _, direction = order.partition(".")
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=(direction == "desc"))
        if limit is not None:
            rows = rows[:limit]
        rows = [dict(r) for r in rows]  # never hand back internal refs

        # Minimal simulation of PostgREST's embedded-resource syntax
        # (`select=*,users(display_name,avatar_url)`) — enough to test the
        # *router's* join-flattening logic end to end, not a real FK join.
        if "users(" in select and table != "users":
            for row in rows:
                owner = next((u for u in self.tables.get("users", []) if u.get("id") == row.get("user_id")), None)
                row["users"] = dict(owner) if owner else None

        if single:
            return rows[0] if rows else None
        return rows

    async def count(self, table: str, *, filters: Optional[dict[str, str]] = None) -> int:
        return len([r for r in self.tables.get(table, []) if _matches(r, filters or {})])

    async def insert(self, table: str, row: dict) -> dict:
        stored = dict(row)
        stored.setdefault("id", str(uuid.uuid4()))
        self.tables.setdefault(table, []).append(stored)
        return dict(stored)

    async def update(self, table: str, values: dict, *, filters: dict[str, str]) -> list[dict]:
        updated = []
        for row in self.tables.get(table, []):
            if _matches(row, filters):
                row.update(values)
                updated.append(dict(row))
        return updated

    async def upsert(
        self,
        table: str,
        row: dict,
        *,
        on_conflict: Optional[str] = None,
        ignore_duplicates: bool = False,
    ) -> Optional[dict]:
        conflict_cols = (on_conflict or "id").split(",")
        existing = next(
            (r for r in self.tables.get(table, []) if all(r.get(c) == row.get(c) for c in conflict_cols)),
            None,
        )
        if existing is not None:
            if ignore_duplicates:
                return None
            existing.update(row)
            return dict(existing)
        stored = dict(row)
        stored.setdefault("id", str(uuid.uuid4()))
        self.tables.setdefault(table, []).append(stored)
        return dict(stored)

    async def upload_object(self, bucket: str, path: str, content: bytes, content_type: str) -> None:
        self.storage[f"{bucket}/{path}"] = content

    async def download_object(self, bucket: str, path: str) -> bytes:
        key = f"{bucket}/{path}"
        if key not in self.storage:
            from app.supabase_client import SupabaseRestError

            raise SupabaseRestError(404, "Object not found")
        return self.storage[key]
