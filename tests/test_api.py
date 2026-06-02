"""Smoke tests for all FastAPI routes — auth flow, CRUD, and upsert logic.

Uses an isolated SQLite test DB (configured in conftest.py before any imports).
AI calls are mocked so tests run offline without an ANTHROPIC_API_KEY.

NOTE: Most read endpoints use "soft auth" — unauthenticated requests succeed
but operate on user_id=0 (returning empty data). Only a few endpoints
explicitly raise 401; those are noted below. Adding strict auth guards is
tracked in NEXT_STEPS.md.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

# Patch via main.X (functions are imported into main's namespace at import time).
# The module-level with-block ensures the mocks are the objects that get bound
# into main during import; patches inside individual tests then target main.X.

_MOCK_RECOVERY = (72, "Good recovery — train hard today.")
_MOCK_REPORT = {
    "summary": "Solid week.",
    "highlights": ["PR on bench"],
    "recommendations": ["Add volume"],
    "adherence_rating": "high",
    "next_week_focus": "Upper body strength",
}
_MOCK_OVERLOAD = "Add 2.5kg to your next bench session."
_MOCK_MACROS = {"calories": 450, "protein_g": 40, "carbs_g": 50, "fat_g": 10}

with (
    patch("claude_service.generate_recovery_insight", return_value=_MOCK_RECOVERY),
    patch("claude_service.generate_weekly_report", return_value=_MOCK_REPORT),
    patch("claude_service.estimate_meal_macros", return_value=_MOCK_MACROS),
    patch("claude_service.generate_progressive_overload_suggestion", return_value=_MOCK_OVERLOAD),
):
    from main import app
    from database import Base, engine
    Base.metadata.create_all(bind=engine)

client = TestClient(app)

# ── Shared state across tests ──────────────────────────────────────────────────
_state: dict = {}


def _auth():
    return {"Authorization": f"Bearer {_state['token']}"}


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert "api_key_configured" in r.json()


def test_register():
    r = client.post("/api/auth/register", json={"email": "test@example.com", "password": "password123"})
    assert r.status_code == 200
    data = r.json()
    assert "token" in data
    assert data["user"]["email"] == "test@example.com"
    _state["token"] = data["token"]
    _state["user_id"] = data["user"]["id"]


def test_register_duplicate():
    r = client.post("/api/auth/register", json={"email": "test@example.com", "password": "password123"})
    assert r.status_code == 409


def test_login():
    r = client.post("/api/auth/login", json={"email": "test@example.com", "password": "password123"})
    assert r.status_code == 200
    assert "token" in r.json()


def test_login_wrong_password():
    r = client.post("/api/auth/login", json={"email": "test@example.com", "password": "wrong"})
    assert r.status_code == 401


def test_me_authenticated():
    r = client.get("/api/auth/me", headers=_auth())
    assert r.status_code == 200
    assert r.json()["email"] == "test@example.com"


def test_me_unauthenticated():
    """/api/auth/me is the one endpoint that explicitly raises 401."""
    r = client.get("/api/auth/me")
    assert r.status_code == 401


# ── Profile ───────────────────────────────────────────────────────────────────

def test_save_profile():
    r = client.post("/api/profile", json={"age": 28, "goal": "bulk", "weight_kg": 85.0}, headers=_auth())
    assert r.status_code == 200


def test_get_profile():
    r = client.get("/api/profile", headers=_auth())
    assert r.status_code == 200
    assert r.json()["age"] == 28


# ── Check-Ins (upsert + PUT) ───────────────────────────────────────────────────

def test_checkin_create():
    with patch("main.generate_recovery_insight", return_value=_MOCK_RECOVERY):
        r = client.post("/api/checkins", json={
            "sleep_score": 7, "energy_score": 8, "soreness_score": 5, "stress_score": 4
        }, headers=_auth())
    assert r.status_code == 200
    data = r.json()
    assert data["recovery_score"] == 72
    assert "streak" in data
    _state["checkin_id"] = data["id"]


def test_checkin_upsert_same_day():
    """Second POST on the same day must update (upsert), not return 409."""
    with patch("main.generate_recovery_insight", return_value=(80, "Great!")):
        r = client.post("/api/checkins", json={
            "sleep_score": 9, "energy_score": 9, "soreness_score": 4, "stress_score": 3
        }, headers=_auth())
    assert r.status_code == 200
    data = r.json()
    assert data["sleep_score"] == 9
    assert data["recovery_score"] == 80


def test_checkin_put():
    with patch("main.generate_recovery_insight", return_value=(65, "Easy day.")):
        r = client.put(f"/api/checkins/{_state['checkin_id']}", json={
            "sleep_score": 6, "energy_score": 6, "soreness_score": 7, "stress_score": 6
        }, headers=_auth())
    assert r.status_code == 200
    assert r.json()["sleep_score"] == 6


def test_list_checkins():
    r = client.get("/api/checkins", headers=_auth())
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


# ── Meals ─────────────────────────────────────────────────────────────────────

def test_log_meal_with_estimation():
    with patch("main.estimate_meal_macros", return_value=_MOCK_MACROS):
        r = client.post("/api/meals", json={"description": "200g chicken + rice"}, headers=_auth())
    assert r.status_code == 200
    data = r.json()
    assert data["protein_g"] == 40
    assert data["macro_source"] == "estimated"


def test_log_meal_manual():
    r = client.post("/api/meals", json={
        "description": "Protein shake", "calories": 200, "protein_g": 30, "carbs_g": 10, "fat_g": 5
    }, headers=_auth())
    assert r.status_code == 200
    assert r.json()["macro_source"] == "manual"


def test_meals_today():
    r = client.get("/api/meals/today", headers=_auth())
    assert r.status_code == 200
    data = r.json()
    assert "meals" in data and "totals" in data
    assert data["totals"]["protein_g"] >= 30


def test_list_meals():
    r = client.get("/api/meals", headers=_auth())
    assert r.status_code == 200
    assert len(r.json()) >= 2


# ── Measurements ──────────────────────────────────────────────────────────────

def test_log_measurement():
    r = client.post("/api/measurements", json={"body_weight_kg": 85.5, "waist_cm": 81.0}, headers=_auth())
    assert r.status_code == 200
    assert r.json()["body_weight_kg"] == 85.5


def test_list_measurements():
    r = client.get("/api/measurements", headers=_auth())
    assert r.status_code == 200
    assert len(r.json()) >= 1


# ── Goals ─────────────────────────────────────────────────────────────────────

def test_set_goal():
    r = client.post("/api/goals", json={"goal_type": "bulk", "target_weight_kg": 90.0}, headers=_auth())
    assert r.status_code == 200
    assert r.json()["is_active"] is True


def test_set_goal_deactivates_previous():
    """Second goal of same type must deactivate the first."""
    r = client.post("/api/goals", json={"goal_type": "bulk", "target_weight_kg": 92.0}, headers=_auth())
    assert r.status_code == 200
    goals = client.get("/api/goals", headers=_auth()).json()
    active_bulks = [g for g in goals if g["goal_type"] == "bulk" and g["is_active"]]
    assert len(active_bulks) == 1
    assert active_bulks[0]["target_weight_kg"] == 92.0


def test_list_goals():
    r = client.get("/api/goals", headers=_auth())
    assert r.status_code == 200
    assert len(r.json()) >= 1


# ── Coach Memory ──────────────────────────────────────────────────────────────

def test_add_memory():
    r = client.post("/api/memory", json={"content": "Athlete prefers morning training.", "memory_type": "note"}, headers=_auth())
    assert r.status_code == 200
    assert r.json()["content"] == "Athlete prefers morning training."


def test_add_memory_empty_content():
    r = client.post("/api/memory", json={"content": "   "}, headers=_auth())
    assert r.status_code == 400


def test_list_memory():
    r = client.get("/api/memory", headers=_auth())
    assert r.status_code == 200
    assert len(r.json()) >= 1


# ── Workout Sessions ──────────────────────────────────────────────────────────

def test_start_session():
    r = client.post("/api/sessions/start", json={"notes": "Push day"}, headers=_auth())
    assert r.status_code == 200
    data = r.json()
    assert "id" in data
    assert "started_at" in data
    _state["session_id"] = data["id"]


def test_active_session():
    r = client.get("/api/sessions/active", headers=_auth())
    assert r.status_code == 200
    assert r.json()["session"] is not None


def test_log_set():
    r = client.post(f"/api/sessions/{_state['session_id']}/sets", json={
        "exercise_name": "Bench Press", "weight_kg": 100.0, "reps": 5, "set_number": 1
    }, headers=_auth())
    assert r.status_code == 200
    data = r.json()
    assert data["exercise_name"] == "Bench Press"
    assert data["weight_kg"] == 100.0


def test_log_set_pr_detection():
    """A heavier set triggers PR detection and auto-memory."""
    r = client.post(f"/api/sessions/{_state['session_id']}/sets", json={
        "exercise_name": "Bench Press", "weight_kg": 110.0, "reps": 3, "set_number": 2
    }, headers=_auth())
    assert r.status_code == 200
    assert r.json()["is_pr"] is True
    prs = client.get("/api/prs", headers=_auth()).json()
    bench_pr = next((p for p in prs if p["exercise_name"] == "Bench Press"), None)
    assert bench_pr is not None


def test_end_session():
    with patch("main.generate_next_session_targets", return_value=_MOCK_OVERLOAD):
        r = client.post(f"/api/sessions/{_state['session_id']}/end", headers=_auth())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ended"
    assert data["set_count"] >= 2


def test_session_history():
    r = client.get("/api/sessions/history", headers=_auth())
    assert r.status_code == 200
    assert len(r.json()) >= 1


# ── Streaks, Badges, Dashboard ────────────────────────────────────────────────

def test_streaks():
    r = client.get("/api/streaks", headers=_auth())
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_badges():
    r = client.get("/api/badges", headers=_auth())
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_dashboard_summary():
    r = client.get("/api/dashboard/summary", headers=_auth())
    assert r.status_code == 200
    data = r.json()
    assert "streaks" in data
    assert "sessions_this_week" in data


def test_subscription():
    r = client.get("/api/subscription", headers=_auth())
    assert r.status_code == 200
    assert r.json()["tier"] == "free"


def test_prs():
    r = client.get("/api/prs", headers=_auth())
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_progress_plateaus():
    r = client.get("/api/progress/plateaus", headers=_auth())
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_checkin_streak():
    r = client.get("/api/checkins/streak", headers=_auth())
    assert r.status_code == 200
    assert "current_streak" in r.json()


# ── Progressive overload — last-sets endpoint ─────────────────────────────────

def test_last_sets_empty():
    """New user with no sessions returns an empty dict."""
    r = client.post("/api/auth/register", json={"email": "fresh@example.com", "password": "password123"})
    token = r.json()["token"]
    r2 = client.get("/api/sessions/last-sets", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    assert r2.json() == {}


def test_last_sets_populated():
    """After logging a set, last-sets returns that exercise with correct weight/reps."""
    # Start a session
    r = client.post("/api/sessions/start", json={}, headers=_auth())
    assert r.status_code == 200
    session_id = r.json()["id"]

    # Log a set
    r2 = client.post(
        f"/api/sessions/{session_id}/sets",
        json={"exercise_name": "Bench Press", "weight_kg": 100.0, "reps": 8},
        headers=_auth(),
    )
    assert r2.status_code == 200

    # End session so it's visible in history
    client.post(f"/api/sessions/{session_id}/end", json={}, headers=_auth())

    # last-sets should now return the logged set
    r3 = client.get("/api/sessions/last-sets", headers=_auth())
    assert r3.status_code == 200
    data = r3.json()
    assert "Bench Press" in data
    assert data["Bench Press"]["weight_kg"] == 100.0
    assert data["Bench Press"]["reps"] == 8


def test_log_set_missing_fields():
    """log_set returns 400 when required fields are absent."""
    r = client.post("/api/sessions/start", json={}, headers=_auth())
    session_id = r.json()["id"]

    # Missing reps
    r2 = client.post(
        f"/api/sessions/{session_id}/sets",
        json={"exercise_name": "Squat", "weight_kg": 80.0},
        headers=_auth(),
    )
    assert r2.status_code == 400
    assert "reps" in r2.json()["detail"]
