# Project Map — BodyBuilding Coach AI

## Architecture: Mixed App

Three subsystems, one repo.

```
telegram_bot.py  ──► users on Telegram (bot + Mini App button)
       │
       └──► HTTP calls ──► main.py (FastAPI)
                                │
                         static/index.html  ←── Telegram Mini App WebView
                         static/app.js          (same as browser web app)
                         static/style.css
```

---

## Main App Areas

### Telegram Bot
- **Location**: `telegram_bot.py` (~6 400 lines)
- **Framework**: python-telegram-bot v21
- **Start command**: `python telegram_bot.py`
- **Active commands** (19 in menu): `/start`, `/help`, `/profile`, `/plan`, `/checkin`, `/workout`, `/log`, `/logset`, `/weight`, `/meal`, `/macros`, `/progress`, `/stats`, `/measurements`, `/report`, `/streak`, `/goals`, `/reminders`, `/units`
- **Hidden commands** (functional, no menu entry): `/privacy`, `/delete_my_data`, `/export`, `/link`, `/link_status`
- **Scheduled jobs**: daily reminder (user-set time), weekly stall check (Mon 9 AM), missed workout check (9 PM daily)

### Web App (SPA)
- **Location**: `static/index.html`, `static/app.js`, `static/style.css`
- **Framework**: Vanilla JS, no build step
- **Tabs**: Dashboard, Analysis, Plans, Workout, Nutrition, Progress, Reports, Profile
- **Telegram Mini App**: loads from same URL inside Telegram WebView; auto-authenticates via `initData`

### Backend / API
- **Location**: `main.py` (~2 100 lines)
- **Framework**: FastAPI + Uvicorn
- **Start command**: `uvicorn main:app --reload --port 8000`
- **Key endpoints**:
  - `POST /api/auth/register` — email/password signup
  - `POST /api/auth/login` — email/password login
  - `POST /api/auth/telegram-webapp` — Mini App auto-auth via initData
  - `GET  /api/dashboard/summary` — all dashboard data in one call
  - `POST /api/checkins` — daily check-in
  - `POST /api/sessions/{id}/sets` — log a workout set
  - `POST /api/analyze` — photo physique analysis (Claude Opus)
  - `POST /api/plan/generate` — generate workout + diet plan
  - `POST /api/reports/generate` — weekly AI report
  - `POST /api/meals` — log a meal
  - `GET  /api/health` — health check

### Database
- **Provider**: SQLite locally (`fitness_coach.db`), PostgreSQL on Railway
- **ORM**: SQLAlchemy 2.x
- **Tables** (17): `users`, `user_profiles`, `body_analyses`, `workout_plans`, `diet_plans`, `supplement_plans`, `research_cache`, `workout_sessions`, `set_logs`, `personal_records`, `daily_checkins`, `body_measurements`, `meal_logs`, `weekly_reports`, `user_streaks`, `user_goals`, `badges`, `telegram_link_codes`, `coach_memories`
- **Migration files**: none — `Base.metadata.create_all()` on startup

### AI / Prompt Logic
- **Location**: `claude_service.py` (690 lines), `prompt_builder.py` (413 lines)
- **Models used**:
  - Photo analysis: `claude-opus-4-7`
  - Chat / coaching: `claude-sonnet-4-6`
  - Reports / summaries: `claude-haiku-4-5-20251001`
- **Note**: `prompt_builder.py` has 6 stub functions that raise `NotImplementedError` — prompts are still built inline in `claude_service.py`

### Domain Orchestrator
- **Location**: `coach_brain.py` (608 lines)
- **Status**: Dataclasses and structure exist; most domain methods raise `NotImplementedError`. The bot and web API still call `claude_service.py` directly.

### Integrations (optional)
| Module | Service | Status |
|---|---|---|
| `garmin_service.py` | Garmin Connect (HRV/sleep) | Optional — disabled if `ENCRYPTION_KEY` missing |
| `mfp_service.py` | MyFitnessPal scraper | Optional — requires `lxml` (Python 3.11 only) |
| `crypto_utils.py` | Fernet AES-256 | Required by garmin + mfp |
| `nutrition_service.py` | Nutritionix API + Claude fallback | Active, no extra setup |
| `research_service.py` | PubMed + Semantic Scholar | Active, no extra setup |

### Railway Deployment
- **Procfile**: `worker: python telegram_bot.py`
- **Web server**: must be added as a second service or second Procfile line:
  `web: uvicorn main:app --host 0.0.0.0 --port $PORT`
- **Required env vars on Railway**: `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `SECRET_KEY`, `BOT_SECRET`, `ENCRYPTION_KEY`, `DATABASE_URL`, `API_BASE_URL`

---

## Files to Understand First

1. `telegram_bot.py` — bot entry point, all command handlers, scheduler
2. `main.py` — API routes, auth (JWT + Telegram Mini App), file serving
3. `claude_service.py` — all Claude API calls, model constants
4. `models.py` — all database tables
5. `static/app.js` — full SPA logic (auth, API calls, UI state)

---

## Files That Are Design Docs (not code)

- `ARCHITECTURE.md` — Phase 2 design doc (useful context)
- `AUDIT.md` — audit findings
- `NEXT_STEPS.md` — known gaps and P0/P1/P2 backlog
- `RELEASE_NOTES.md` — release history
- `peer_review_expert_panel.md` — beta test notes

---

## Known Issues

- `coach_brain.py` domain methods are stubs → bot calls `claude_service.py` directly
- `prompt_builder.py` has 6 unimplemented stubs → inline prompts still used
- `get_current_user_id()` returns `0` for unauthenticated requests (NEXT_STEPS P0 §1)
- `myfitnesspal` requires `lxml` → fails on Python 3.14; use Python 3.11
- Procfile missing `web:` line → FastAPI not deployed on Railway automatically
