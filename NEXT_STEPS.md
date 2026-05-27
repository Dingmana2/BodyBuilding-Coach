# Next Steps — Prioritised Backlog

Items deferred from Phases 1–5, ordered by impact / effort.

---

## P0 — Security (must fix before production traffic)

### 1. Strict authentication on all endpoints
**Problem**: `get_current_user_id()` returns `0` for unauthenticated requests instead of raising 401. Most endpoints silently operate on `user_id=0` (returning empty data) rather than rejecting the request. Only `/api/auth/me` currently raises 401.
**Fix**: Add a `require_auth` dependency that raises `HTTPException(401)` when `user_id == 0`. Apply to all write endpoints and user-specific read endpoints.
**Files**: `main.py`

### 2. Rate-limiting on expensive AI routes
**Problem**: `POST /api/analyze`, `POST /api/plan/generate`, `POST /api/reports/generate` call Claude and are unguarded — a single user can trigger unlimited API spend.
**Fix**: Add per-user rate limits (e.g. 10 analyses/day for Pro, 3 for Free) via a simple counter in Redis or SQLite. Tier limits already enforced by the feature-gate middleware — harden them.
**Files**: `main.py`

### 3. Input validation with Pydantic request models
**Problem**: All `POST` endpoints read raw `await request.json()` dicts. Malformed payloads can cause `KeyError`, `ValueError`, or unhandled `None` coercions.
**Fix**: Replace `request: Request` + dict access with typed Pydantic `BaseModel` schemas. This also generates correct OpenAPI docs.
**Files**: `main.py` (all POST/PUT handlers)

---

## P1 — Core Product Gaps

### 4. Migrate `prompt_builder.py` stubs (Phase 5 design goal)
**Problem**: `plan_prompt()`, `checkin_prompt()`, `overload_prompt()`, `report_prompt()`, `weak_points_prompt()`, `research_filter()` all raise `NotImplementedError`. Current callers build prompts inline in `claude_service.py` and `telegram_bot.py`.
**Fix**: Migrate inline prompt construction to these stubs as planned in `ARCHITECTURE.md §6a`.
**Files**: `prompt_builder.py`, `claude_service.py`, `telegram_bot.py`

### 5. Migrate `coach_brain.py` domain method stubs
**Problem**: `generate_plan()`, `run_checkin()`, `log_set()`, `end_session()`, `generate_report()`, `analyze_weak_points()`, `detect_plateau()`, `suggest_overload()`, `chat()` all raise `NotImplementedError`.
**Fix**: Wire these to the existing `claude_service` + `main.py` implementations as the orchestration layer.
**Files**: `coach_brain.py`

### 6. DELETE endpoints for user data
**Problem**: Users can add meals, goals, and check-ins but cannot delete them via the web app or API.
**Fix**: Add `DELETE /api/meals/{id}`, `DELETE /api/goals/{id}`, `DELETE /api/checkins/{id}` with ownership validation.
**Files**: `main.py`, `static/app.js`, `static/index.html`

### 7. Garmin / MyFitnessPal sync in web app
**Problem**: Garmin Connect and MFP integrations exist in `telegram_bot.py` but are not accessible from the web app.
**Fix**: Add `POST /api/integrations/garmin` and `POST /api/integrations/mfp` endpoints; surface credentials form in Profile tab.
**Files**: `main.py`, `static/index.html`, `static/app.js`

---

## P2 — Quality of Life

### 8. PostgreSQL support
**Problem**: `database.py` supports PostgreSQL via `DATABASE_URL` but has never been tested. SQLite's WAL mode is sufficient for low traffic but won't scale horizontally.
**Fix**: Add a CI step that runs `pytest` against a PostgreSQL Docker container. Fix any SQLite-specific SQL (e.g. `pragma` calls).
**Files**: `database.py`, CI config

### 9. Stripe webhook handler
**Problem**: The billing UI exists but upgrades are fire-and-forget (no webhook confirms payment). Subscription tier is updated client-side via `POST /api/subscription/upgrade` without real payment verification.
**Fix**: Add `POST /api/webhooks/stripe` handler for `checkout.session.completed` and `customer.subscription.deleted` events.
**Files**: `main.py`

### 10. Photo upload progress + compression
**Problem**: Large photos (up to 20MB) are uploaded synchronously; no progress indicator; no client-side compression before upload.
**Fix**: Add client-side `canvas.toBlob()` compression to ~800px max dimension before upload. Add XHR progress event to the upload UI.
**Files**: `static/app.js`

### 11. Session pagination in history
**Problem**: `GET /api/sessions/history` returns the last 10 sessions only. Users with long training histories can't access older data.
**Fix**: Add `?offset=N` pagination parameter; add "Load More" button to session history UI.
**Files**: `main.py`, `static/app.js`

### 12. Export / data portability
**Problem**: Users have no way to export their data (workouts, meals, check-ins, PRs).
**Fix**: Add `GET /api/export` endpoint returning a JSON bundle of all user data. Add "Export Data" button in Profile tab.
**Files**: `main.py`, `static/index.html`, `static/app.js`

---

## P3 — Architectural Hardening

### 13. Replace homebrew JWT with PyJWT or python-jose
**Problem**: `_create_token()` and `_verify_password()` are custom implementations in `main.py`. They work correctly but add maintenance burden and are not audited by the security community.
**Fix**: Swap in `python-jose[cryptography]` for JWT and keep `passlib[bcrypt]` for password hashing.
**Files**: `main.py`, `requirements.txt`

### 14. Async SQLAlchemy
**Problem**: All DB queries run synchronously inside `async def` route handlers via `Depends(get_db)`. This blocks the event loop under load.
**Fix**: Migrate to `sqlalchemy.ext.asyncio` with `AsyncSession`. This is a larger refactor; do it before scaling past single-node.
**Files**: `database.py`, `main.py`, `coach_brain.py`

### 15. Structured logging
**Problem**: The codebase uses bare `print()` for errors (e.g. `print(f"Warning: ...")`). No log levels, no correlation IDs, no sentry integration.
**Fix**: Replace all `print()` with `logging.getLogger(__name__)` calls. Add Sentry DSN support via env var.
**Files**: All `.py` files

---

## Notes on Intentional Stubs

The following `NotImplementedError` stubs in `prompt_builder.py` and `coach_brain.py` are **by design** — they represent planned Phase 6 architectural migration, not incomplete features. The underlying functionality exists and works; the stubs are the migration target. Do not delete them.

```
prompt_builder: plan_prompt, checkin_prompt, overload_prompt, report_prompt, weak_points_prompt, research_filter
coach_brain:    generate_plan, run_checkin, log_set, end_session, generate_report, analyze_weak_points, detect_plateau, suggest_overload, chat
```
