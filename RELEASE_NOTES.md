# Release Notes — v1.0.0

**Release date**: 2026-05-27  
**Phases shipped**: Phase 1–5 (complete)

---

## What's New

### Web App

A full-featured single-page web application at `/` (FastAPI static files):

- **Dashboard** — recovery trend sparkline, retention widget (streaks, badges, lapse nudges, comp prep countdown), coach memory feed, and quick-action cards
- **Check-In Form** — log sleep / energy / soreness / stress with live sliders directly on the Dashboard; pre-fills if already checked in today; AI generates a recovery score and coaching tip on every submission
- **Nutrition Tab** — log meals by description (Claude estimates macros) or by entering them manually; today's macro totals (kcal / protein / carbs / fat) update live
- **Workout Tab** — start a session, log sets, see PRs detected in real time, end session with AI-generated next-session targets; full session history; Personal Records board
- **Progress Tab** — before/after photo comparison, plateau detection with 4-week 1RM trend, body measurements log and history table
- **Analysis Tab** — upload a photo for Claude physique assessment (body fat estimate, physique score, recommendations)
- **Plans Tab** — generate a personalised training + nutrition plan; view current plan's workout schedule and diet macros
- **Reports Tab** — weekly AI coaching reports with adherence rating and next-week focus; print-to-PDF
- **Research Tab** — latest peer-reviewed findings from PubMed & Semantic Scholar injected into every AI response
- **Profile Tab** — save athlete profile, set goals (bulk/cut/recomp/strength/maintain) with targets, log body measurements, view badges earned and full streak history, link Telegram account
- **Billing Tab** — subscription tier display; upgrade flow (Stripe-ready)

### Telegram Bot

- `/checkin` now uses **inline keyboard buttons** (1–10 per step) — no more typing; auto-advances through steps; Garmin pre-fill skips already-filled steps
- Free tier: **3 check-ins per rolling 7-day window** (rate-limited at bot level)
- Check-in data now **writes to SQLite** (was bot_state.json only) — recovery data is now visible to the web app and included in AI context
- Full feature parity with the web app for: workouts, PRs, meals, plans, weak-point analysis, weekly reports

### AI Coaching

- **Coach Memory** — PRs are auto-written to a `coach_memories` table; the 5 most recent memories are injected into every AI system prompt so the coach "remembers" past achievements
- **Goal-specific system prompts** — bulk / cut / recomp / strength / prep / beginner personas with distinct coaching priorities
- **Research injection** — PubMed/Semantic Scholar summaries are appended to plan generation prompts
- **Plateau detection** — exercises where estimated 1RM improves <2% over the last 3 weeks are flagged
- **Weak-point analysis** — Pro+ users can request a muscle balance assessment

### Infrastructure

- **Sunday auto-reports** — APScheduler cron job generates weekly reports for all active Pro/Elite users at 08:00 UTC
- **38 automated tests** covering all critical routes (auth, CRUD, upsert, PR detection, streak awards)
- **45 FastAPI routes** — all user-facing endpoints surfaced in the web UI

---

## Breaking Changes

None. This is the initial release.

---

## Known Limitations

- SQLite only — no PostgreSQL support in this release (schema-compatible, env var `DATABASE_URL` accepts a Postgres URL for easy upgrade)
- No Stripe webhook handler — subscription tier upgrades are manual (test-mode UI exists)
- Garmin / MyFitnessPal sync defined in bot but not surfaced in web app
- `prompt_builder.py` stubs (`plan_prompt`, `checkin_prompt`, etc.) raise `NotImplementedError` — not yet called from production paths; current callers build prompts inline

---

## Upgrade / Migration

Fresh install: no migrations needed — `Base.metadata.create_all()` creates all tables on startup.

Existing database: `_migrate_db()` runs idempotent `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for all new columns on every startup. No manual SQL needed.
