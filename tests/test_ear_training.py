def test_list_modules_ordered(authed_client, fake_supabase):
    fake_supabase.seed(
        "ear_modules",
        [
            {"id": "m2", "slug": "intervals", "sort_order": 2},
            {"id": "m1", "slug": "foundation", "sort_order": 1},
        ],
    )
    resp = authed_client.get("/v1/ear/modules")
    assert resp.status_code == 200
    slugs = [row["slug"] for row in resp.json()]
    assert slugs == ["foundation", "intervals"]


def test_list_lessons_for_module(authed_client, fake_supabase):
    fake_supabase.seed(
        "ear_lessons",
        [
            {"id": "l1", "module_id": "m1", "slug": "minor-2nd", "sort_order": 1},
            {"id": "l2", "module_id": "m1", "slug": "major-2nd", "sort_order": 2},
            {"id": "l3", "module_id": "m2", "slug": "major-vs-minor", "sort_order": 1},
        ],
    )
    resp = authed_client.get("/v1/ear/modules/m1/lessons")
    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert ids == ["l1", "l2"]


def test_get_lesson_404_when_missing(authed_client, fake_supabase):
    resp = authed_client.get("/v1/ear/lessons/nope")
    assert resp.status_code == 404


def test_list_exercises_for_lesson(authed_client, fake_supabase):
    fake_supabase.seed(
        "ear_exercises",
        [
            {"id": "e2", "lesson_id": "l1", "sort_order": 2},
            {"id": "e1", "lesson_id": "l1", "sort_order": 1},
            {"id": "e3", "lesson_id": "other", "sort_order": 1},
        ],
    )
    resp = authed_client.get("/v1/ear/lessons/l1/exercises")
    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert ids == ["e1", "e2"]


def test_start_session_creates_row_for_caller(authed_client, fake_supabase):
    resp = authed_client.post("/v1/ear/sessions", json={"lesson_id": "l1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "test-uid-123"
    assert body["lesson_id"] == "l1"
    assert body["started_at"]


def test_complete_session_computes_xp_and_passes(authed_client, fake_supabase):
    fake_supabase.seed(
        "ear_lessons",
        [{"id": "l1", "xp_reward": 40, "min_accuracy": 70.0, "difficulty": "beginner"}],
    )
    session = authed_client.post("/v1/ear/sessions", json={"lesson_id": "l1"}).json()

    resp = authed_client.put(
        f"/v1/ear/sessions/{session['id']}/complete",
        json={"exercises_seen": 10, "exercises_correct": 9, "hints_used": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accuracy"] == 90.0
    assert body["passed"] is True
    # base 40 * 0.9 = 36, minus 1 hint * beginner penalty (2) = 34
    assert body["xp_earned"] == 34
    assert body["progress"]["status"] == "mastered"  # 90% hits MASTERY_ACCURACY


def test_complete_session_fails_below_min_accuracy(authed_client, fake_supabase):
    fake_supabase.seed(
        "ear_lessons",
        [{"id": "l1", "xp_reward": 40, "min_accuracy": 70.0, "difficulty": "beginner"}],
    )
    session = authed_client.post("/v1/ear/sessions", json={"lesson_id": "l1"}).json()

    resp = authed_client.put(
        f"/v1/ear/sessions/{session['id']}/complete",
        json={"exercises_seen": 10, "exercises_correct": 3, "hints_used": 0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accuracy"] == 30.0
    assert body["passed"] is False
    assert body["xp_earned"] == 0
    assert body["progress"]["status"] == "in_progress"


def test_complete_session_best_accuracy_only_increases(authed_client, fake_supabase):
    fake_supabase.seed(
        "ear_lessons",
        [{"id": "l1", "xp_reward": 40, "min_accuracy": 70.0, "difficulty": "beginner"}],
    )
    fake_supabase.seed(
        "user_ear_progress",
        [
            {
                "id": "p1",
                "user_id": "test-uid-123",
                "lesson_id": "l1",
                "status": "passed",
                "best_accuracy": 95.0,
                "total_attempts": 3,
                "total_xp_earned": 100,
            }
        ],
    )
    session = authed_client.post("/v1/ear/sessions", json={"lesson_id": "l1"}).json()

    resp = authed_client.put(
        f"/v1/ear/sessions/{session['id']}/complete",
        json={"exercises_seen": 10, "exercises_correct": 8, "hints_used": 0},
    )
    body = resp.json()
    assert body["progress"]["best_accuracy"] == 95.0  # unchanged — 80% is worse
    assert body["progress"]["total_attempts"] == 4
    assert body["is_personal_best"] is False


def test_complete_session_404_for_someone_elses_session(authed_client, fake_supabase):
    fake_supabase.seed(
        "user_ear_sessions",
        [{"id": "s1", "user_id": "someone-else", "lesson_id": "l1"}],
    )
    resp = authed_client.put(
        "/v1/ear/sessions/s1/complete",
        json={"exercises_seen": 5, "exercises_correct": 5, "hints_used": 0},
    )
    assert resp.status_code == 404


def test_reference_tables_return_seeded_rows(authed_client, fake_supabase):
    fake_supabase.seed("ear_interval_mnemonics", [{"id": "i1", "abbreviation": "m2", "sort_order": 1}])
    fake_supabase.seed("ear_chord_qualities", [{"id": "c1", "slug": "major", "sort_order": 1}])
    fake_supabase.seed("ear_progressions", [{"id": "pr1", "slug": "I-IV-V", "sort_order": 1}])
    fake_supabase.seed("ear_rhythm_patterns", [{"id": "r1", "slug": "quarter-note-walk", "sort_order": 1}])

    assert authed_client.get("/v1/ear/reference/intervals").json()[0]["abbreviation"] == "m2"
    assert authed_client.get("/v1/ear/reference/chords").json()[0]["slug"] == "major"
    assert authed_client.get("/v1/ear/reference/progressions").json()[0]["slug"] == "I-IV-V"
    assert authed_client.get("/v1/ear/reference/rhythms").json()[0]["slug"] == "quarter-note-walk"


def test_ear_routes_require_auth(client):
    resp = client.get("/v1/ear/modules")
    assert resp.status_code == 403 or resp.status_code == 401
