# Claude Local Instructions — BodyBuilding Coach AI

## Project

BodyBuilding Coach AI — Telegram bot + FastAPI web backend + Telegram Mini App.

## Goal

Build a clean, well-integrated fitness coaching app. Primary interface is the Telegram Mini App (same web SPA as the browser app, loaded inside Telegram). Bot handles daily habit loop and notifications. Web app handles the full dashboard.

## Current Situation (as of 2026-06-02)

- Inspection complete. Project map is at `docs/PROJECT_MAP.md`.
- Bot commands trimmed from 33 → 19 active + 5 hidden.
- Telegram Mini App plumbing added:
  - `static/index.html`: Telegram Web App SDK loaded in `<head>`, viewport-fit=cover
  - `static/app.js`: `_tryTelegramAuth()` auto-authenticates via initData on open
  - `main.py`: `POST /api/auth/telegram-webapp` validates initData HMAC and returns JWT
  - `telegram_bot.py`: `/start` and `/help` include "Open Dashboard" Mini App button

## Rules

- Do not rewrite the whole app.
- Do not delete working code — disable or hide instead.
- Do not expose secrets.
- Do not commit .env files.
- Work in small steps.
- Explain what changed.
- Prefer simple, maintainable code.

## Python Issue

- System Python is 3.14. `myfitnesspal` transitively requires `lxml` which has no 3.14 wheels.
- Always use Python 3.11 venv locally: `py -3.11 -m venv .venv`
- `runtime.txt` already says `python-3.11` for Railway — no change needed there.

## Next Priority Tasks

### P0 — Required before real users
1. Fix `Procfile` — add `web: uvicorn main:app --host 0.0.0.0 --port $PORT`
2. Fix unauthenticated access — `get_current_user_id()` returns 0 instead of raising 401
3. Set all Railway env vars (see `docs/LOCAL_SETUP.md`)

### P1 — Mini App polish
4. Test Mini App auth flow end-to-end with ngrok
5. Hide login/register UI elements when running as Mini App (user is always auto-logged in)
6. Handle Telegram theme variables (bg color, text color) via CSS variables

### P2 — Feature cleanup
7. Remove Research tab from web SPA drawer (cut feature)
8. Wire `coach_brain.py` domain methods (currently raise NotImplementedError)
9. Migrate inline prompts to `prompt_builder.py` stubs

## Architecture Reference

See `docs/PROJECT_MAP.md` for full file map and known issues.
See `docs/LOCAL_SETUP.md` for how to run locally and deploy.
