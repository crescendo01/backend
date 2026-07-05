def test_list_lessons_filters_by_instrument_type_and_difficulty(authed_client, fake_supabase):
    fake_supabase.seed(
        "lessons",
        [
            {"id": "l1", "instrument": "piano", "type": "note", "difficulty": 1, "order_in_series": 1},
            {"id": "l2", "instrument": "piano", "type": "note", "difficulty": 5, "order_in_series": 2},
            {"id": "l3", "instrument": "guitar", "type": "note", "difficulty": 1, "order_in_series": 1},
        ],
    )
    resp = authed_client.get("/v1/lessons", params={"instrument": "piano", "type": "note", "max_difficulty": 3})
    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert ids == ["l1"]


def test_get_lesson_by_id(authed_client, fake_supabase):
    fake_supabase.seed("lessons", [{"id": "l1", "instrument": "piano", "title": "Middle C"}])
    resp = authed_client.get("/v1/lessons/l1")
    assert resp.status_code == 200
    assert resp.json()["title"] == "Middle C"


def test_get_lesson_404_when_missing(authed_client, fake_supabase):
    resp = authed_client.get("/v1/lessons/nope")
    assert resp.status_code == 404


def test_get_next_lesson_in_series(authed_client, fake_supabase):
    fake_supabase.seed(
        "lessons",
        [
            {"id": "l1", "instrument": "piano", "type": "note", "order_in_series": 1},
            {"id": "l2", "instrument": "piano", "type": "note", "order_in_series": 2},
            {"id": "l3", "instrument": "piano", "type": "chord", "order_in_series": 2},  # different type, shouldn't match
        ],
    )
    resp = authed_client.get("/v1/lessons/l1/next")
    assert resp.status_code == 200
    assert resp.json()["id"] == "l2"


def test_get_next_lesson_none_left_returns_null(authed_client, fake_supabase):
    fake_supabase.seed("lessons", [{"id": "l1", "instrument": "piano", "type": "note", "order_in_series": 1}])
    resp = authed_client.get("/v1/lessons/l1/next")
    assert resp.status_code == 200
    assert resp.json() is None


def test_progress_returns_rows_scoped_to_caller_and_lesson_ids(authed_client, fake_supabase):
    fake_supabase.seed(
        "user_progress",
        [
            {"id": "p1", "user_id": "test-uid-123", "lesson_id": "l1", "completed": True},
            {"id": "p2", "user_id": "test-uid-123", "lesson_id": "l2", "completed": False},
            {"id": "p3", "user_id": "someone-else", "lesson_id": "l1", "completed": True},
        ],
    )
    resp = authed_client.get("/v1/lessons/progress", params={"lesson_ids": "l1,l2"})
    assert resp.status_code == 200
    rows = resp.json()
    assert {r["lesson_id"] for r in rows} == {"l1", "l2"}
    assert all(r["user_id"] == "test-uid-123" for r in rows)


def test_record_attempt_creates_progress_on_first_try(authed_client, fake_supabase):
    resp = authed_client.post("/v1/lessons/l1/attempts", json={"score": 60, "unlock_threshold": 80})
    assert resp.status_code == 200
    row = resp.json()
    assert row["attempts"] == 1
    assert row["best_score"] == 60
    assert row["completed"] is False
    assert row["completed_date"] is None


def test_record_attempt_marks_completed_once_threshold_met(authed_client, fake_supabase):
    authed_client.post("/v1/lessons/l1/attempts", json={"score": 60, "unlock_threshold": 80})
    resp = authed_client.post("/v1/lessons/l1/attempts", json={"score": 85, "unlock_threshold": 80})
    row = resp.json()
    assert row["attempts"] == 2
    assert row["best_score"] == 85
    assert row["completed"] is True
    assert row["completed_date"] is not None


def test_record_attempt_best_score_never_decreases(authed_client, fake_supabase):
    authed_client.post("/v1/lessons/l1/attempts", json={"score": 90, "unlock_threshold": 80})
    resp = authed_client.post("/v1/lessons/l1/attempts", json={"score": 40, "unlock_threshold": 80})
    row = resp.json()
    assert row["best_score"] == 90
    assert row["attempts"] == 2
    # Once completed, stays completed even if a later attempt scores lower.
    assert row["completed"] is True


def test_record_attempt_completed_date_updates_on_every_completed_attempt(authed_client, fake_supabase):
    """Matches the original Dart semantics exactly (`lesson_repository.dart`
    line ~88): `completed_date` is recomputed on every attempt that still
    qualifies as completed, not frozen at first completion — more "last
    completed at" than "first completed at" despite the column name. Not
    something introduced here; preserved as-is rather than "fixed" since
    nothing about this rebuild was supposed to change lesson semantics."""
    first = authed_client.post("/v1/lessons/l1/attempts", json={"score": 90, "unlock_threshold": 80}).json()
    second = authed_client.post("/v1/lessons/l1/attempts", json={"score": 95, "unlock_threshold": 80}).json()
    assert first["completed_date"] is not None
    assert second["completed_date"] is not None
    assert second["completed_date"] >= first["completed_date"]


def test_record_attempt_is_scoped_per_user(authed_client, fake_supabase):
    fake_supabase.seed(
        "user_progress",
        [{"id": "other", "user_id": "someone-else", "lesson_id": "l1", "completed": True, "attempts": 5, "best_score": 100}],
    )
    resp = authed_client.post("/v1/lessons/l1/attempts", json={"score": 50, "unlock_threshold": 80})
    row = resp.json()
    # A different user's existing row for the same lesson must not be reused.
    assert row["attempts"] == 1
    assert row["best_score"] == 50


def test_completed_count_only_counts_callers_completed_rows(authed_client, fake_supabase):
    fake_supabase.seed(
        "user_progress",
        [
            {"id": "p1", "user_id": "test-uid-123", "lesson_id": "l1", "completed": True},
            {"id": "p2", "user_id": "test-uid-123", "lesson_id": "l2", "completed": True},
            {"id": "p3", "user_id": "test-uid-123", "lesson_id": "l3", "completed": False},
            {"id": "p4", "user_id": "someone-else", "lesson_id": "l1", "completed": True},
        ],
    )
    resp = authed_client.get("/v1/lessons/completed-count")
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


def test_lessons_endpoints_require_auth(client):
    assert client.get("/v1/lessons", params={"instrument": "piano"}).status_code == 401
    assert client.get("/v1/lessons/l1").status_code == 401
    assert client.post("/v1/lessons/l1/attempts", json={"score": 1, "unlock_threshold": 1}).status_code == 401
