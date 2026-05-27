# BodyBuilding Coach AI

An AI-powered physique coaching platform — web app + Telegram bot — built on FastAPI, SQLite, and Claude.

---

## Features

- **Daily check-ins** with recovery scoring (web sliders or Telegram inline buttons)
- **Workout logger** with PR detection, progressive overload suggestions, and next-session targets
- **Nutrition tracking** — describe a meal and Claude estimates the macros
- **Body analysis** — upload a photo; Claude estimates body fat and physique score
- **Weekly AI reports** auto-generated every Sunday for Pro/Elite users
- **Progress tracker** — before/after photo comparison, plateau detection, strength trends
- **Goals & measurements** — set targets, log measurements, track 30-day weight change
- **Coach memory** — the AI remembers your PRs and notes between sessions
- **Research library** — latest PubMed/Semantic Scholar findings injected into advice
- **Badges & streaks** — 7/14/30/60/90-day milestones for workouts and check-ins
- **Telegram bot** with full feature parity (rate-limited on Free tier)
- **Subscription tiers**: Free / Pro / Elite (Stripe-ready)

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/dingmana2/bodybuilding-coach.git
cd bodybuilding-coach
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — required fields: ANTHROPIC_API_KEY, SECRET_KEY, BOT_SECRET
```

Generate secrets:

```bash
python3 -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

### 3. Apply migrations & start

```bash
# Migrations run automatically on first startup via Base.metadata.create_all()
# and the _migrate_db() idempotent ALTER TABLE calls.

python main.py
# OR with uvicorn for production:
uvicorn main:app --host 0.0.0.0 --port 8000
```

Web app is available at `http://localhost:8000`.

### 4. Start the Telegram bot (optional)

```bash
python telegram_bot.py
```

Requires `TELEGRAM_BOT_TOKEN` and `API_BASE_URL` set in `.env`.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Claude API key from console.anthropic.com |
| `SECRET_KEY` | ✅ | JWT signing secret (32+ random bytes, base64) |
| `BOT_SECRET` | ✅ | Shared secret between web API and Telegram bot |
| `TELEGRAM_BOT_TOKEN` | ✅ (bot only) | Token from @BotFather |
| `API_BASE_URL` | bot only | URL of the FastAPI server reachable by the bot |
| `DATA_DIR` | optional | Directory for SQLite DB and uploads (default: repo root) |
| `STRIPE_SECRET_KEY` | optional | Stripe key for subscription billing |
| `APP_ENV` | optional | `production` or `development` |

---

## Running Tests

```bash
pip install pytest httpx
python -m pytest tests/ -v
```

38 tests covering all critical routes (auth, CRUD, upsert logic, PR detection, streak awards). AI calls are mocked — no real API key required.

---

## Architecture

```
main.py           FastAPI app — 45 routes, JWT auth, APScheduler weekly reports
telegram_bot.py   python-telegram-bot v21 — full bot feature set
claude_service.py Anthropic API calls — all AI features
coach_brain.py    CoachContext dataclass + build_context() — data aggregation
prompt_builder.py Centralised prompt construction (Phase 5 stubs for future migration)
models.py         SQLAlchemy ORM models
database.py       SQLite engine + session factory
research_service.py  PubMed + Semantic Scholar research fetching
```

See `ARCHITECTURE.md` for full design decisions.

---

## Database Schema

Tables: `users`, `user_profiles`, `daily_checkins`, `workout_sessions`, `set_logs`,
`personal_records`, `meal_logs`, `body_measurements`, `user_goals`, `body_analyses`,
`coaching_plans`, `weekly_reports`, `user_streaks`, `badges`, `coach_memories`,
`research_cache`, `telegram_link_codes`

Schema is managed by `Base.metadata.create_all` (new tables) and `_migrate_db()` (idempotent column additions).

---

## Deployment (Railway)

1. Create a Railway project with a Web Service pointed at this repo
2. Set all required env vars in Railway dashboard
3. `API_BASE_URL` = your Railway web service public URL
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Bot runs as a separate Worker service: `python telegram_bot.py`

---

## Telegram Bot Commands

| Command | Description | Tier |
|---|---|---|
| `/start` | Onboarding + profile setup | Free |
| `/checkin` | Daily recovery check-in (inline buttons) | Free (3/week) |
| `/workout` | Log a workout set | Free |
| `/plan` | Generate a training + nutrition plan | Free (1/month) |
| `/analyze` | Photo physique analysis | Free (1/month) |
| `/report` | Weekly AI coaching report | Pro+ |
| `/meal` | Log a meal with AI macro estimation | Pro+ |
| `/prs` | View personal records | Free |
| `/link` | Link Telegram to web account | Free |
