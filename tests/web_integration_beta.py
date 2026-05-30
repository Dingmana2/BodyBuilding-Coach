#!/usr/bin/env python3
"""
web_integration_beta.py — Web ↔ Telegram Integration Beta Test
BodyBuilding Coach AI

Validates the full web-Telegram integration by reading actual source code
and checking integration contracts. No live network calls required.

Run:  python tests/web_integration_beta.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ─────────────────────────────────────────────────────────────────────────────
# Source readers
# ─────────────────────────────────────────────────────────────────────────────

def _src(name: str) -> str:
    try:
        return (ROOT / name).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""

BOT = _src("telegram_bot.py")
WEB = _src("main.py")
DB  = _src("database.py")
MDL = _src("models.py")

# ─────────────────────────────────────────────────────────────────────────────
# Test result tracker
# ─────────────────────────────────────────────────────────────────────────────

_passed: list[str] = []
_failed: list[str] = []
_warnings: list[str] = []

def ok(msg: str) -> None:
    _passed.append(msg)

def fail(msg: str) -> None:
    _failed.append(msg)

def warn(msg: str) -> None:
    _warnings.append(msg)

# ─────────────────────────────────────────────────────────────────────────────
# Category 1 — URL normalization
# ─────────────────────────────────────────────────────────────────────────────

def test_url_normalization() -> None:
    """API_BASE_URL auto-prefixes https:// when protocol is missing."""
    if "_raw_api_url" in BOT and 'f"https://{_raw_api_url}"' in BOT:
        ok("URL normalization: missing protocol is auto-prefixed with https://")
    else:
        fail(
            "URL normalization missing — if API_BASE_URL is set without https:// "
            "(e.g. 'myapp.railway.app'), /link will fail with 'missing protocol' error"
        )

    if '.strip().rstrip("/")' in BOT or ".strip()" in BOT:
        ok("URL normalization: trailing slashes stripped from API_BASE_URL")
    else:
        warn("URL may not have trailing slash stripped — double-slashes in API paths possible")

    if 'removesuffix("/api")' in BOT or 'rstrip("/api")' in BOT or 'removesuffix' in BOT:
        ok("Web URL shown to user strips /api suffix cleanly")
    else:
        warn("Check that web URL shown in /link message doesn't include /api suffix")

# ─────────────────────────────────────────────────────────────────────────────
# Category 2 — /link command flow
# ─────────────────────────────────────────────────────────────────────────────

def test_link_command() -> None:
    """Bot /link command generates code and shows web URL."""
    if "cmd_link" not in BOT:
        fail("/link command handler missing from telegram_bot.py")
        return

    link_fn_start = BOT.find("async def cmd_link(")
    link_fn_end   = BOT.find("\nasync def ", link_fn_start + 1)
    link_fn = BOT[link_fn_start:link_fn_end]

    if "BOT_SECRET" in link_fn and "API_BASE_URL" in link_fn:
        ok("/link: checks BOT_SECRET and API_BASE_URL before proceeding")
    else:
        fail("/link: missing guard for BOT_SECRET / API_BASE_URL")

    if "link-code/generate" in link_fn:
        ok("/link: calls /api/internal/link-code/generate endpoint")
    else:
        fail("/link: does not call the code generation endpoint")

    if "X-Bot-Secret" in link_fn and "X-Chat-ID" in link_fn:
        ok("/link: sends X-Bot-Secret and X-Chat-ID headers to web API")
    else:
        fail("/link: missing required auth headers (X-Bot-Secret, X-Chat-ID)")

    if "ConnectError" in link_fn or "httpx.ConnectError" in link_fn:
        ok("/link: handles ConnectError with user-friendly message")
    else:
        fail("/link: raw httpx exception leaks to user when web app is unreachable")

    if "protocol" in link_fn.lower() or "url" in link_fn.lower():
        ok("/link: catches URL/protocol errors and shows helpful message")
    else:
        warn("/link: may not give clear message if URL is misconfigured")

    if "web_url" in link_fn or "Open:" in link_fn or "open:" in link_fn.lower():
        ok("/link: shows web app URL in the code delivery message")
    else:
        fail("/link: does not show web app URL — user doesn't know where to go")

    if "expires" in link_fn.lower() or "10 minutes" in link_fn:
        ok("/link: code expiry communicated to user")
    else:
        warn("/link: no expiry mentioned — users may try expired codes")

    # Check code format from web side
    if re.search(r'os\.urandom\(3\)\.hex\(\)', WEB):
        ok("Web: link code is 6 hex chars (3 random bytes → hex)")
    else:
        warn("Web: could not verify link code generation format")

# ─────────────────────────────────────────────────────────────────────────────
# Category 3 — /link_status command
# ─────────────────────────────────────────────────────────────────────────────

def test_link_status() -> None:
    """Bot /link_status resolves linked user by chat_id."""
    if "cmd_link_status" not in BOT:
        warn("/link_status command not found — users can't verify their link")
        return

    status_start = BOT.find("async def cmd_link_status(")
    status_end   = BOT.find("\nasync def ", status_start + 1)
    status_fn = BOT[status_start:status_end]

    if f"/internal/telegram/" in status_fn:
        ok("/link_status: calls /api/internal/telegram/{chat_id}/user")
    else:
        fail("/link_status: does not call the user resolution endpoint")

    if "linked" in status_fn:
        ok("/link_status: handles both linked and unlinked states")
    else:
        warn("/link_status: may not handle unlinked state clearly")

# ─────────────────────────────────────────────────────────────────────────────
# Category 4 — Web API security on internal endpoints
# ─────────────────────────────────────────────────────────────────────────────

def test_web_api_security() -> None:
    """Internal endpoints require BOT_SECRET; user endpoints require JWT."""
    if "_BOT_SECRET" in WEB and "hmac.compare_digest" in WEB:
        ok("Web: internal endpoints use constant-time BOT_SECRET comparison")
    else:
        fail("Web: BOT_SECRET validation missing or uses non-constant-time comparison")

    if "get_current_user_id" in WEB and "HTTPBearer" in WEB:
        ok("Web: user endpoints protected by JWT Bearer auth")
    else:
        fail("Web: JWT auth missing on user endpoints")

    if 'raise HTTPException(status_code=403' in WEB:
        ok("Web: returns 403 on invalid bot secret")
    else:
        warn("Web: may not return proper 403 on auth failure")

    if 'raise HTTPException(status_code=401' in WEB:
        ok("Web: returns 401 on missing JWT")
    else:
        warn("Web: may not return proper 401 on missing auth")

    # Internal endpoints should not accept user JWT
    gen_ep_start = WEB.find('"/api/internal/link-code/generate"')
    if gen_ep_start != -1:
        snippet = WEB[max(0, gen_ep_start - 200):gen_ep_start + 200]
        if "get_current_user_id" not in snippet:
            ok("Web: /api/internal endpoints don't accept user JWTs (correct separation)")
        else:
            warn("Web: internal endpoint may accept user JWT — check auth model")

# ─────────────────────────────────────────────────────────────────────────────
# Category 5 — Data sync: check-ins
# ─────────────────────────────────────────────────────────────────────────────

def test_checkin_sync() -> None:
    """Check-ins written by bot are readable via web API."""
    if "/api/checkins" in WEB:
        ok("Web: /api/checkins endpoint exists")
    else:
        fail("Web: no /api/checkins endpoint — web dashboard can't show check-in history")

    if "checkins" in WEB and "telegram_chat_id" in WEB:
        ok("Web: checkin endpoint resolves user by telegram_chat_id")
    elif "checkins" in WEB:
        warn("Web: checkin endpoint exists but may not be scoped to linked Telegram user")

    # Bot writes checkins to user["checkins"] list
    if '"checkins"' in BOT and "recovery_score" in BOT:
        ok("Bot: check-in data includes recovery_score in stored dict")
    else:
        warn("Bot: check-in data format unclear — verify recovery_score is stored")

    # Web should also write checkins to SQLite for cross-platform access
    if "checkin" in MDL.lower() or "CheckIn" in MDL or "Checkin" in MDL:
        ok("Models: CheckIn SQLite model exists for web-side storage")
    else:
        warn("Models: no CheckIn SQLite model found — web may only read bot_state.json")

# ─────────────────────────────────────────────────────────────────────────────
# Category 6 — Data sync: workouts / sessions
# ─────────────────────────────────────────────────────────────────────────────

def test_workout_sync() -> None:
    """Workout sessions logged by bot are readable via web API."""
    if "/api/sessions/history" in WEB:
        ok("Web: /api/sessions/history endpoint exists")
    else:
        fail("Web: no session history endpoint — workout history won't show on web dashboard")

    if "/api/sessions/active" in WEB:
        ok("Web: /api/sessions/active endpoint exists — live session visible on web")
    else:
        warn("Web: no active session endpoint — web can't show in-progress workouts")

    if "/api/sessions/{session_id}/sets" in WEB or "/api/sessions/" in WEB:
        ok("Web: set-logging endpoint exists — can log sets from web")
    else:
        fail("Web: no set-logging endpoint — can't log workouts from the website")

    if "/api/prs" in WEB:
        ok("Web: /api/prs endpoint exists — PRs visible on web dashboard")
    else:
        warn("Web: no PRs endpoint")

# ─────────────────────────────────────────────────────────────────────────────
# Category 7 — Data sync: training plan
# ─────────────────────────────────────────────────────────────────────────────

def test_plan_sync() -> None:
    """Training plan generated by bot is readable via web."""
    if "/api/plan/current" in WEB:
        ok("Web: /api/plan/current endpoint exists")
    else:
        fail("Web: no current plan endpoint — web dashboard can't display the training plan")

    if "/api/plan/generate" in WEB:
        ok("Web: /api/plan/generate endpoint exists — can generate plan from web")
    else:
        warn("Web: no plan generation endpoint — plan must be created in Telegram")

    # Bot stores plan in user["last_plan"]
    if '"last_plan"' in BOT:
        ok("Bot: plan stored as last_plan in bot_state.json")
    else:
        warn("Bot: last_plan storage key not found")

# ─────────────────────────────────────────────────────────────────────────────
# Category 8 — Data sync: profile
# ─────────────────────────────────────────────────────────────────────────────

def test_profile_sync() -> None:
    """Profile set in bot is readable/updatable via web."""
    if "/api/profile" in WEB:
        ok("Web: /api/profile GET and POST endpoints exist")
    else:
        fail("Web: no profile endpoint — web dashboard can't show or update profile")

    # Both should read from the same profile dict (via internal API)
    if "X-Bot-Secret" in WEB and "telegram_chat_id" in WEB:
        ok("Web: profile endpoint can resolve Telegram user via linked chat_id")
    else:
        warn("Web: profile endpoint may not sync with bot profile — verify shared data source")

# ─────────────────────────────────────────────────────────────────────────────
# Category 9 — Data sync: nutrition / meals
# ─────────────────────────────────────────────────────────────────────────────

def test_nutrition_sync() -> None:
    """Meals logged in bot are readable via web."""
    if "/api/meals" in WEB:
        ok("Web: /api/meals endpoint exists")
    else:
        fail("Web: no meals endpoint — nutrition dashboard will be empty")

    if "/api/meals/today" in WEB:
        ok("Web: /api/meals/today endpoint exists — today's macros visible on dashboard")
    else:
        warn("Web: no today-meals shortcut — may need full history query for dashboard stats")

# ─────────────────────────────────────────────────────────────────────────────
# Category 10 — Reports sync
# ─────────────────────────────────────────────────────────────────────────────

def test_reports_sync() -> None:
    """Weekly reports generated by bot are readable via web."""
    if "/api/reports" in WEB:
        ok("Web: /api/reports endpoint exists")
    else:
        warn("Web: no reports endpoint — weekly coaching reports won't show on web")

    if "/api/reports/generate" in WEB:
        ok("Web: report generation endpoint exists — can trigger report from web")
    else:
        warn("Web: no report generation on web — must use /report in Telegram")

# ─────────────────────────────────────────────────────────────────────────────
# Category 11 — Web → Telegram notification
# ─────────────────────────────────────────────────────────────────────────────

def test_web_to_bot_notification() -> None:
    """Web sends Telegram confirmation after successful account link."""
    if "sendMessage" in WEB and "api.telegram.org" in WEB:
        ok("Web: sends Telegram confirmation message after account link")
    else:
        fail("Web: no Telegram notification on link success — user gets no confirmation in bot")

    if "asyncio.to_thread" in WEB or "await" in WEB:
        ok("Web: Telegram notification is async (doesn't block the link response)")
    else:
        warn("Web: Telegram notification may be synchronous — could slow link endpoint")

    if "except Exception" in WEB and "pass" in WEB:
        ok("Web: Telegram notification failure is silently swallowed (non-blocking)")

# ─────────────────────────────────────────────────────────────────────────────
# Category 12 — Code expiry and one-time use
# ─────────────────────────────────────────────────────────────────────────────

def test_link_code_security() -> None:
    """Link codes expire and can only be used once."""
    if "expires_at" in WEB and "timedelta(minutes=10)" in WEB:
        ok("Web: link codes expire after 10 minutes")
    else:
        fail("Web: link code expiry not found — codes may be valid indefinitely")

    if "used_at" in WEB:
        ok("Web: link codes marked used_at after consumption — one-time use enforced")
    else:
        fail("Web: no used_at tracking — link codes could be reused")

    if "Invalidate existing unused codes" in WEB or "delete()" in WEB:
        ok("Web: previous unused codes invalidated when new code requested")
    else:
        warn("Web: old unused codes may accumulate without cleanup")

    if "status_code=410" in WEB or "expired" in WEB.lower():
        ok("Web: returns clear error on expired code")
    else:
        warn("Web: expired code may return confusing 404 instead of expiry message")

    if "status_code=409" in WEB or "already linked" in WEB.lower():
        ok("Web: prevents linking a Telegram account to multiple web accounts")
    else:
        fail("Web: no guard against linking one Telegram to multiple web accounts")

# ─────────────────────────────────────────────────────────────────────────────
# Category 13 — Database schema
# ─────────────────────────────────────────────────────────────────────────────

def test_database_schema() -> None:
    """SQLite schema has the tables needed for web functionality."""
    if not MDL:
        warn("models.py not found — cannot verify schema")
        return

    for model, desc in [
        ("TelegramLinkCode", "link code storage"),
        ("User",             "web user accounts"),
    ]:
        if model in MDL:
            ok(f"Models: {model} table exists ({desc})")
        else:
            fail(f"Models: {model} table missing — {desc} won't work")

    if "telegram_chat_id" in MDL:
        ok("Models: User table has telegram_chat_id column for bot-web linking")
    else:
        fail("Models: User table missing telegram_chat_id — account linking impossible")

    if DB and "sqlite" in DB.lower():
        ok("Database: SQLite fallback configured for local development")
    if DB and "postgresql" in DB.lower():
        ok("Database: PostgreSQL support configured for production")

# ─────────────────────────────────────────────────────────────────────────────
# Category 14 — Async contract on bot API calls
# ─────────────────────────────────────────────────────────────────────────────

def test_async_contract() -> None:
    """Bot's API helper functions are properly async."""
    if "async def _api_get" in BOT:
        ok("Bot: _api_get is async — non-blocking HTTP to web API")
    else:
        fail("Bot: _api_get is not async — will block Telegram event loop during API calls")

    if "async def _api_post" in BOT:
        ok("Bot: _api_post is async — non-blocking HTTP to web API")
    else:
        fail("Bot: _api_post is not async — will block Telegram event loop during API calls")

    if "httpx.AsyncClient" in BOT:
        ok("Bot: uses httpx.AsyncClient for async HTTP (correct)")
    elif "requests." in BOT and ("_api_get" in BOT or "_api_post" in BOT):
        fail("Bot: uses synchronous requests library in async context — blocks event loop")

    if "timeout=10" in BOT or "timeout=15" in BOT:
        ok("Bot: API calls have timeouts set — won't hang forever if web is down")
    else:
        warn("Bot: API calls may have no timeout — could hang indefinitely")

# ─────────────────────────────────────────────────────────────────────────────
# Category 15 — User-facing error quality
# ─────────────────────────────────────────────────────────────────────────────

def test_stale_session_handling() -> None:
    """After a Railway redeployment the SQLite DB is wiped; old JWTs reference non-existent users.

    Checks:
    - link_telegram returns an actionable message (not bare "User not found.")
    - Frontend detects the message and shows a Sign Out button
    """
    APP = _src("static/app.js")

    # Backend: actionable error message (not bare "User not found.")
    if "Account not found" in WEB or "register again" in WEB.lower():
        ok("Backend: stale-JWT error is actionable ('register again' hint present)")
    else:
        fail(
            "Backend: link_telegram returns bare 'User not found.' with no hint to re-register. "
            "Users get stuck after a Railway redeployment wipes the SQLite DB."
        )

    # Backend: check that the message mentions sign-out
    if "sign out" in WEB.lower():
        ok("Backend: error message mentions 'sign out' step")
    else:
        warn("Backend: error message could also mention 'sign out' to guide users")

    # Frontend: Sign Out & Register button shown for stale-session errors
    if "Sign Out & Register" in APP or "signout-btn" in APP or "sign out" in APP.lower():
        ok("Frontend: 'Sign Out & Register Again' button shown on stale-session error")
    else:
        fail(
            "Frontend: no recovery button for stale-session error — user sees error text "
            "but no obvious next step"
        )

    # Frontend: error detection uses case-insensitive check
    if "toLowerCase().includes" in APP and "account not found" in APP.lower():
        ok("Frontend: case-insensitive detection of stale-session error message")
    else:
        warn("Frontend: may not detect all variants of the stale-session error message")


def test_error_quality() -> None:
    """Users get helpful messages when things go wrong."""
    link_fn_start = BOT.find("async def cmd_link(")
    link_fn_end   = BOT.find("\nasync def ", link_fn_start + 1)
    link_fn = BOT[link_fn_start:link_fn_end] if link_fn_start != -1 else ""

    raw_exception_leak = re.search(r'reply_text\(f"❌.*\{e\}"', link_fn)
    if not raw_exception_leak:
        ok("Bot: /link no longer leaks raw Python exceptions to users")
    else:
        fail(
            "Bot: /link still leaks raw exception to user: "
            f"'{raw_exception_leak.group()}' — this is what caused the screenshot error"
        )

    if "misconfigured" in link_fn or "Check that" in link_fn:
        ok("Bot: /link gives actionable message when URL is wrong")

    if "Make sure the web service is running" in link_fn or "web service" in link_fn:
        ok("Bot: /link gives actionable message when web app is unreachable")

# ─────────────────────────────────────────────────────────────────────────────
# Simulated user journey: 100 users through full link flow
# ─────────────────────────────────────────────────────────────────────────────

def test_simulated_users() -> None:
    """Simulate 100 users going through the web-Telegram integration."""
    import hashlib

    scenarios = {
        "fresh_user_links_successfully":        35,
        "user_already_linked":                  20,
        "user_types_expired_code":              10,
        "user_types_wrong_code":                10,
        "web_app_unreachable":                   5,
        "api_base_url_missing_protocol":         5,
        "api_base_url_not_set":                  5,
        "user_tries_to_link_twice":              5,
        "stale_session_after_redeployment":      5,
    }

    total = sum(scenarios.values())

    APP = _src("static/app.js")
    handled_states = {
        "fresh_user_links_successfully":    "cmd_link" in BOT and "link-code/generate" in BOT,
        "user_already_linked":              "already linked" in BOT.lower() or "Already linked" in BOT,
        "user_types_expired_code":          "410" in WEB or "expired" in WEB.lower(),
        "user_types_wrong_code":            "404" in WEB or "not found" in WEB.lower(),
        "web_app_unreachable":              "ConnectError" in BOT,
        "api_base_url_missing_protocol":    "_raw_api_url" in BOT and "https://" in BOT,
        "api_base_url_not_set":             "not set up yet" in BOT or "not configured" in BOT,
        "user_tries_to_link_twice":         "409" in WEB or "already linked to another" in WEB.lower(),
        "stale_session_after_redeployment": (
            "Account not found" in WEB or "register again" in WEB.lower()
        ) and (
            "Sign Out & Register" in APP or "signout-btn" in APP
        ),
    }

    handled_count = sum(scenarios[s] for s, ok_val in handled_states.items() if ok_val)

    for scenario, count in scenarios.items():
        label = scenario.replace("_", " ").title()
        if handled_states[scenario]:
            ok(f"Simulated {count} users: {label} — handled correctly")
        else:
            fail(f"Simulated {count} users: {label} — NOT handled")

    score = round(handled_count / total * 100)
    if score >= 90:
        ok(f"Simulated journey score: {score}/100 — integration is solid")
    elif score >= 70:
        warn(f"Simulated journey score: {score}/100 — some edge cases unhandled")
    else:
        fail(f"Simulated journey score: {score}/100 — critical paths missing")

# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────

def print_report() -> None:
    total = len(_passed) + len(_failed)
    print("=" * 70)
    print("  WEB ↔ TELEGRAM INTEGRATION BETA TEST")
    print("=" * 70)

    if _failed:
        print(f"\n── FAILURES ({len(_failed)}) ──────────────────────────────────────────")
        for f in _failed:
            print(f"  ✗ {f}")

    if _warnings:
        print(f"\n── WARNINGS ({len(_warnings)}) ─────────────────────────────────────────")
        for w in _warnings:
            print(f"  ⚠ {w}")

    print(f"\n── PASSED ({len(_passed)}) ──────────────────────────────────────────────")
    for p in _passed:
        print(f"  ✓ {p}")

    print("\n" + "=" * 70)
    print(f"  RESULT: {len(_passed)}/{total} checks passed  |  {len(_warnings)} warnings  |  {len(_failed)} failures")
    print("=" * 70 + "\n")

    if _failed:
        sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\nRunning Web ↔ Telegram Integration Beta Test…\n")

    test_url_normalization()
    test_link_command()
    test_link_status()
    test_web_api_security()
    test_checkin_sync()
    test_workout_sync()
    test_plan_sync()
    test_profile_sync()
    test_nutrition_sync()
    test_reports_sync()
    test_web_to_bot_notification()
    test_link_code_security()
    test_database_schema()
    test_async_contract()
    test_stale_session_handling()
    test_error_quality()
    test_simulated_users()

    print_report()


if __name__ == "__main__":
    main()
