# AUDIT.md — BodyBuilding Coach AI
_Generated: 2026-05-26 | Phase 1 inspection only — no edits made_

---

## 1. File Tree

```
BodyBuilding-Coach/
├── main.py               FastAPI web API (auth, plans, sessions, checkins, meals, reports)
├── models.py             SQLAlchemy ORM models (18 tables)
├── database.py           SQLAlchemy engine + session factory + get_db() dep
├── telegram_bot.py       Telegram bot — 26 commands, 2 callback handlers, JSON state store
├── claude_service.py     All Anthropic API calls (photo analysis, plan gen, coaching, reports)
├── nutrition_service.py  Nutritionix API + Claude fallback for macro lookup
├── garmin_service.py     Garmin Connect login + daily data fetch + local JSON cache
├── mfp_service.py        MyFitnessPal login + today's diary fetch
├── research_service.py   PubMed + Semantic Scholar async fetch + Claude summaries
├── crypto_utils.py       Fernet encrypt/decrypt for stored credentials
├── static/
│   ├── index.html        Single-page web app shell (7 tabs)
│   ├── app.js            Frontend logic (vanilla JS, fetch-based)
│   └── style.css         App styling
├── fitness_coach.db      SQLite database (production uses DATABASE_URL → PostgreSQL)
├── bot_state.json        Bot-side user state (entirely separate from SQLite DB)
├── garmin_cache.json     Garmin data cache keyed by chat_id
├── requirements.txt      Python dependencies
├── Procfile              Deployment process definition
├── runtime.txt           Python runtime pin
├── run.sh                Local dev startup script
├── .env                  Local secrets (NOT committed — has .env.example)
└── .env.example          Env var template
```

---

## 2. Service Module Details

### claude_service.py
| Function | Signature | Model Used | Callers |
|---|---|---|---|
| `analyze_body_photo` | `(image_path, profile=None, previous_analysis=None) -> dict` | claude-opus-4-7 | main.py POST /api/analyze; telegram_bot._analyze_photo |
| `generate_comprehensive_plan` | `(analysis, research_cache, profile=None) -> dict` | claude-opus-4-7 | main.py POST /api/plan/generate; telegram_bot._generate_plan |
| `estimate_meal_macros` | `(description) -> dict` | claude-haiku-4-5 | nutrition_service.lookup_food_macros (fallback) |
| `generate_recovery_insight` | `(sleep, energy, soreness, stress, profile=None) -> tuple[int, str]` | claude-haiku-4-5 | telegram_bot._finish_checkin; main.py POST /api/checkins |
| `generate_progressive_overload_suggestion` | `(exercise, set_history, profile=None) -> str` | claude-haiku-4-5 | main.py POST /api/sessions/{id}/sets (called but result unused in web) |
| `generate_next_session_targets` | `(session_sets, plan_day, profile=None) -> str` | claude-haiku-4-5 | telegram_bot.cmd_workout (end flow) |
| `generate_weekly_report` | `(sessions, checkins, meals, prs, profile=None) -> dict` | claude-opus-4-7 | main.py POST /api/reports/generate; telegram_bot.cmd_report |
| `analyze_weak_points` | `(analyses, set_logs, profile=None) -> dict` | claude-haiku-4-5 | telegram_bot.cmd_weakpoints |
| `build_rich_context` | `(user_data) -> str` | — (prompt builder) | **NEVER CALLED** — dead code |
| `summarize_research` | `(topic, papers) -> str` | claude-haiku-4-5 | research_service._process_topic |
| `get_goal_system_prompt` | `(goal) -> str` | — | telegram_bot._chat_with_coach; main.py POST /api/plan/generate |

**Env vars**: `ANTHROPIC_API_KEY`
**External deps**: `anthropic` SDK
**DB tables**: none directly

---

### nutrition_service.py
| Function | Signature | Callers |
|---|---|---|
| `lookup_food_macros` | `async (description) -> dict` | telegram_bot.cmd_meal; main.py POST /api/meals |

**Env vars**: `NUTRITIONIX_APP_ID`, `NUTRITIONIX_API_KEY`
**External deps**: `httpx`, `claude_service.estimate_meal_macros` (fallback)
**DB tables**: none
**Fallback**: if Nutritionix keys absent or call fails → calls `estimate_meal_macros` via Claude

---

### garmin_service.py
| Function | Signature | Callers |
|---|---|---|
| `test_login` | `(email, password) -> None` | telegram_bot._handle_connect_step |
| `fetch_and_cache` | `(chat_id, email, enc_pass) -> dict` | telegram_bot._handle_connect_step; telegram_bot._daily_garmin_sync |
| `get_cached` | `(chat_id, for_date=None) -> dict\|None` | telegram_bot.cmd_checkin |

**Env vars**: `DATA_DIR` (cache file location)
**External deps**: `garminconnect`, `crypto_utils.decrypt`
**DB tables**: none — writes `garmin_cache.json` only
**Note**: Cache file is per-process; restarts on Railway may lose it

---

### mfp_service.py
| Function | Signature | Callers |
|---|---|---|
| `test_login` | `(username, password) -> None` | telegram_bot._handle_connect_step |
| `fetch_today` | `(username, enc_pass) -> list[dict]` | telegram_bot.cmd_mfp |

**Env vars**: none
**External deps**: `myfitnesspal`, `crypto_utils.decrypt`
**DB tables**: none
**Note**: MFP data only flows to bot JSON, never to SQLite

---

### research_service.py
| Function | Signature | Callers |
|---|---|---|
| `search_pubmed` | `async (query, max_results=5, years_back=3) -> list` | `get_papers_for_topic` |
| `search_semantic_scholar` | `async (query, max_results=4) -> list` | `get_papers_for_topic` |
| `get_papers_for_topic` | `async (topic) -> list` | `refresh_all_research` |
| `refresh_all_research` | `async (profile=None) -> dict` | main.py POST /api/research/refresh; telegram_bot._fetch_research_summaries |

**Env vars**: none (PubMed & S2 are free/no-key)
**External deps**: `httpx`
**DB tables**: `research_cache` (via callers — written by main.py)

---

### crypto_utils.py
| Function | Signature | Callers |
|---|---|---|
| `encrypt` | `(plaintext) -> str` | telegram_bot._handle_connect_step |
| `decrypt` | `(token) -> str` | garmin_service.fetch_and_cache; mfp_service.fetch_today |

**Env vars**: `ENCRYPTION_KEY` (Fernet key — if missing, raises at import time)
**External deps**: `cryptography`

---

### database.py
**Public**: `get_db()` — FastAPI dependency injector yielding a SQLAlchemy session
**Env vars**: `DATABASE_URL` (default: `sqlite:///./fitness_coach.db`)
**Note**: SQLite uses `connect_args={"check_same_thread": False}`; PostgreSQL skips that

---

## 3. FastAPI Routes

| Method | Path | Auth | Request | Response | Notes |
|---|---|---|---|---|---|
| GET | / | None | — | index.html | Static file |
| POST | /api/auth/register | None | email, password | {token, user} | bcrypt hash |
| POST | /api/auth/login | None | email, password | {token, user} | JWT 30-day |
| GET | /api/auth/me | JWT | — | User + profile | |
| POST | /api/internal/link-code/generate | BOT_SECRET header | — | {code, expires} | 8-char code, 10 min TTL |
| POST | /api/auth/link-telegram | JWT | {code} | link confirmation | Consumes link code |
| GET | /api/internal/telegram/{chat_id}/user | BOT_SECRET header | — | User info | Bot→API bridge |
| GET | /api/subscription | JWT | — | tier + features | |
| POST | /api/subscription/upgrade | JWT | {tier} | **501 Not Implemented** | Stub |
| GET | /api/profile | JWT | — | profile fields | |
| POST | /api/profile | JWT | profile fields | {status: "saved"} | |
| POST | /api/analyze | JWT + tier check | multipart photo | analysis dict | Saves photo locally |
| GET | /api/analyses | **NO AUTH** | ?limit | list of analyses | ⚠️ Exposes all analyses |
| POST | /api/plan/generate | **NO AUTH** | — | {workout, diet, supplement} | ⚠️ Free Claude Opus call |
| GET | /api/plan/current | **NO AUTH** | — | latest plans | ⚠️ |
| GET | /api/research | **NO AUTH** | — | research cache | |
| POST | /api/research/refresh | **NO AUTH** | — | refresh result | ⚠️ Expensive Claude calls |
| GET | /api/progress | **NO AUTH** | — | body analyses | ⚠️ |
| GET | /api/sessions/active | JWT | — | session + sets | |
| POST | /api/sessions/start | JWT | {notes} | {id, started_at} | |
| POST | /api/sessions/{id}/end | JWT | — | summary + streak | |
| POST | /api/sessions/{id}/sets | JWT | {exercise_name, weight_kg, reps} | set + PR flag | |
| GET | /api/sessions/history | JWT | ?limit | sessions list | |
| GET | /api/prs | JWT | — | PRs list | |
| GET | /api/checkins | JWT | ?limit | checkins list | |
| POST | /api/checkins | JWT | sleep/energy/soreness/stress | checkin + streak | Calls Claude Haiku |
| GET | /api/checkins/streak | JWT | — | streak metrics | |
| GET | /api/measurements | JWT | ?limit | measurements | |
| POST | /api/measurements | JWT | measurement fields | measurement record | |
| GET | /api/meals | JWT | ?limit | meals | |
| GET | /api/meals/today | JWT | — | meals + macro totals | |
| POST | /api/meals | JWT | {description, calories, macros} | meal log | Calls Nutritionix/Claude |
| GET | /api/reports | JWT | ?limit | reports list | |
| POST | /api/reports/generate | JWT + Pro tier | — | report + insights | Calls Claude Opus |
| GET | /api/streaks | JWT | — | streak dict | |
| GET | /api/badges | JWT | — | badges list | |
| GET | /api/goals | JWT | — | goals list | |
| POST | /api/goals | JWT | goal fields | goal record | |
| GET | /api/dashboard/summary | JWT | — | aggregated metrics | |
| GET | /api/health | None | — | {status, timestamp} | |

---

## 4. Telegram Bot Commands

| Command | Handler | Behavior | Required State |
|---|---|---|---|
| /start | cmd_start | Welcome message | none |
| /help | cmd_help | Command list | none |
| /profile | cmd_profile | View or set profile (age/weight/goal/etc) | none required |
| /plan | cmd_plan | Show day-picker → generate plan (photo or profile based) | needs profile or last_analysis |
| /log | cmd_log | Auto-start session + show exercise tap-keyboard | optional last_plan |
| /workout | cmd_workout | start/end session, show today's plan | optional last_plan |
| /logset | cmd_logset | Log set directly: `/logset bench 100kg 8` | active_session_id optional |
| /checkin | cmd_checkin | Multi-step recovery check-in (sleep/energy/soreness/stress) | none |
| /progress | cmd_progress | Weight trend, avg recovery, top PRs | measurements/checkins/prs |
| /stats | cmd_stats | PRs by exercise + volume per muscle group | prs, set_logs |
| /measurements | cmd_measurements | Log body measurements | none |
| /weight | cmd_weight | Quick body weight log | none |
| /meal | cmd_meal | Log meal, get macros | none |
| /macros | cmd_macros | Today's macros vs. targets | last_plan, meal_logs |
| /goals | cmd_goals | Set or view goals (weight/bf%/date) | profile optional |
| /streak | cmd_streak | Check-in streak + badges | checkins |
| /weakpoints | cmd_weakpoints | AI weak-point analysis | last_analysis, set_logs |
| /report | cmd_report | Weekly AI coaching report | checkins, meal_logs, set_logs, prs |
| /research | cmd_research | Search PubMed + show summaries | none |
| /reminders | cmd_reminders | Set/view/remove cron reminders | none |
| /units | cmd_units | Toggle kg ↔ lbs | none |
| /connect | cmd_connect | Multi-step Garmin/MFP connect flow | none |
| /mfp | cmd_mfp | Sync MFP diary or show setup | mfp_username, mfp_pass_enc |
| /billing | cmd_billing | Show tier + upsell | subscription_tier |
| /link | cmd_link | Generate code to link to web account | BOT_SECRET + API_BASE_URL |
| /link_status | cmd_link_status | Show link status | BOT_SECRET + API_BASE_URL |

**Callback handlers**:
- `^wk:` → handle_workout_callback (exercise picker, weight/reps selection, session start/end, day swap)
- `^plan:days:` → handle_plan_days_callback (day count → generate plan immediately)

**Scheduled jobs**:
- Daily 6am: `_daily_garmin_sync` — refresh Garmin for all connected users
- Monday 9am: `_weekly_stall_check` — detect weight stalls, send nudge
- Daily 9pm: `_missed_workout_check` — detect missed planned sessions, send nudge
- Dynamic per user: `_send_reminder` — user-configured reminders

---

## 5. Database Models

| Model | Table | Key Columns | FKs | Indexes |
|---|---|---|---|---|
| User | users | id, email(unique), hashed_password, telegram_chat_id(unique nullable), subscription_tier, is_active, created_at | — | email, telegram_chat_id |
| UserProfile | user_profiles | id, user_id, age, gender, height_cm, weight_kg, goal, training_experience, training_days_per_week, dietary_restrictions, equipment_available, injuries, show_date, updated_at | user_id→users.id | user_id |
| BodyAnalysis | body_analyses | id, photo_path, body_fat_estimate, overall_physique_score, strengths(JSON), areas_to_improve(JSON), muscle_development(JSON), symmetry_notes, coach_message, raw_analysis(JSON), body_fat_confidence, created_at | — | created_at |
| WorkoutPlan | workout_plans | id, raw_plan(JSON), created_at | — | created_at |
| DietPlan | diet_plans | id, calories, protein_g, carbs_g, fat_g, raw_plan(JSON), created_at | — | created_at |
| SupplementPlan | supplement_plans | id, raw_plan(JSON), created_at | — | created_at |
| ResearchCache | research_cache | id, topic(unique), papers(JSON), summary, last_updated | — | topic |
| WorkoutSession | workout_sessions | id, chat_id, started_at, ended_at, notes | — | chat_id |
| SetLog | set_logs | id, session_id, exercise_name, weight_kg, reps, estimated_1rm, logged_at | session_id→workout_sessions.id | session_id, exercise_name |
| PersonalRecord | personal_records | id, chat_id, exercise_name, weight_kg, reps, estimated_1rm, set_log_id, achieved_at | set_log_id→set_logs.id (nullable) | chat_id, exercise_name |
| DailyCheckIn | daily_checkins | id, chat_id, date, sleep_score, energy_score, soreness_score, stress_score, recovery_score, coaching_tip, hrv_ms, resting_hr_bpm, sleep_duration_hrs, data_source, created_at | — | chat_id, date |
| BodyMeasurement | body_measurements | id, chat_id, date, body_weight_kg, waist_cm, chest_cm, hips_cm, left/right_arm_cm, left/right_thigh_cm, created_at | — | chat_id |
| MealLog | meal_logs | id, chat_id, date, description, calories, protein_g, carbs_g, fat_g, macro_source, logged_at | — | chat_id, date |
| WeeklyReport | weekly_reports | id, chat_id, week_start, sessions_count, avg_recovery, prs_count, avg_protein_g, ai_insights(JSON), created_at | — | chat_id |
| UserStreak | user_streaks | id, chat_id, streak_type, current_streak, longest_streak, last_activity_date, total_days_active | — | chat_id |
| UserGoal | user_goals | id, chat_id, goal_type, target_weight_kg, target_bf_pct, target_date, start_weight_kg, start_bf_pct, is_active, created_at | — | chat_id |
| Badge | badges | id, chat_id, badge_type, badge_metadata(JSON), earned_at | — | chat_id |
| TelegramLinkCode | telegram_link_codes | id, code(unique), telegram_chat_id, user_id, expires_at, used_at, created_at | user_id→users.id (nullable) | code, telegram_chat_id |

**No Alembic**. Schema changes via `_migrate_db()` in main.py using idempotent `ALTER TABLE … ADD COLUMN` in a try/except-pass loop.

---

## 6. Data-Flow Diagrams

### 6a. Onboarding
```
User → /start
         └→ welcome message + instructions

User → /profile age=25 gender=male weight=80kg goal=bulk experience=intermediate days=4
         └→ parses & validates each field
         └→ writes user["profile"] → bot_state.json
         └→ "Profile saved. Type /plan to generate your plan."

Optional: User sends photo
  └→ handle_photo
       └→ download → base64 encode
       └→ claude_service.analyze_body_photo() [Claude Opus]
       └→ writes user["last_analysis"] → bot_state.json
       └→ _format_analysis() → sends results
       └→ sends _plan_days_keyboard() (2-6 day picker)

User taps day count
  └→ handle_plan_days_callback
       └→ user["profile"]["days"] = N → bot_state.json
       └→ _generate_plan() [if last_analysis] OR _generate_plan_from_profile()
            └→ research_service.refresh_all_research() [PubMed + S2 + Claude Haiku]
            └→ claude_service.generate_comprehensive_plan() [Claude Opus]
       └→ writes user["last_plan"] → bot_state.json
       └→ _send_plan() → 4 messages (workout/diet/supps/coaching)
            └→ workout message includes day buttons (wk:day:{name})
```

### 6b. Photo → Plan (web app path)
```
Browser → POST /api/analyze (multipart, JWT)
  └→ tier check (free: checked by count only — not strictly enforced)
  └→ save photo to uploads/ (local disk)
  └→ claude_service.analyze_body_photo() [Claude Opus]
  └→ saves BodyAnalysis to SQLite
  └→ returns {analysis_id, analysis, photo_url, created_at}

Browser → POST /api/plan/generate  ← ⚠️ NO AUTH
  └→ loads last BodyAnalysis from SQLite
  └→ loads ResearchCache from SQLite
  └→ claude_service.generate_comprehensive_plan() [Claude Opus]
  └→ saves WorkoutPlan + DietPlan + SupplementPlan to SQLite
  └→ returns {workout_plan, diet_plan, supplement_plan}
```

### 6c. Daily Check-In (Telegram)
```
User → /checkin [or /checkin sleep=7 energy=6 soreness=5 stress=4]

Inline args path:
  └→ parse sleep/energy/soreness/stress (1-10)
  └→ try garmin_service.get_cached() → inject hrv/resting_hr/sleep_hours if available
  └→ _finish_checkin()
       └→ claude_service.generate_recovery_insight() [Claude Haiku]
       └→ writes user["checkins"] → bot_state.json
       └→ sends recovery score + coaching tip

Multi-step path:
  └→ active_command = "checkin", command_state = {step: "sleep"}
  └→ user answers 4 prompts (sleep → energy → soreness → stress)
  └→ same _finish_checkin() path above
```

### 6d. Workout Logging (Telegram)
```
User → /log  OR  /workout start

  If no active session:
    └→ session_counter += 1, active_session_id = N
    └→ command_state["current_session_sets"] = []
    └→ _get_session_exercises(user) → exercises for active_session_day OR today
    └→ sends _ex_keyboard() [exercise buttons + "📅 Different day…" + "✏️ Other"]

User taps exercise → wk:ex:{name}
  └→ context.user_data["wk_ex"] = name
  └→ sends _weight_keyboard() [recent weights ± increments, in user's unit]

User taps weight → wk:w:{kg_value}
  └→ context.user_data["wk_w"] = float
  └→ sends _rep_keyboard() [3,4,5,6,7,8,9,10,12,15,20]

User taps reps → wk:r:{n}
  └→ _do_log_set(user, ex, weight_kg, reps)
       └→ epley 1RM, PR check, append to set_logs + current_session_sets
       └→ _save_store()
  └→ sends confirmation + _after_set_keyboard() [Same exercise / Other / End]

User taps "End Session" → wk:end
  └→ summarise volume, sets, best per exercise
  └→ active_session_id = None, current_session_sets = [], active_session_day = None
  └→ _save_store()
  └→ optional: generate_next_session_targets() [Claude Haiku via executor]
```

### 6e. Weekly Report
```
Telegram /report:
  └→ collects last 7d checkins, meal_logs, set_logs, prs from bot JSON
  └→ claude_service.generate_weekly_report() [Claude Opus via executor]
  └→ formats and sends to user

Web POST /api/reports/generate (Pro tier required):
  └→ collects last 7d data from SQLite
  └→ claude_service.generate_weekly_report() [Claude Opus]
  └→ saves WeeklyReport to SQLite
  └→ returns report

APScheduler _weekly_stall_check (Monday 9am):
  └→ for each bot user with measurements:
       └→ compare weight last 14d vs goal direction
       └→ if stalled → calls _chat_with_coach() with stall prompt
       └→ sends AI insight to user
  Note: build_rich_context() is available but NOT called here (dead code)
```

---

## 7. Findings

### 7a. Disconnected Systems
- **Bot JSON ↔ SQLite DB**: The Telegram bot stores ALL user data (`set_logs`, `checkins`, `meal_logs`, `measurements`, `prs`, `last_plan`, `last_analysis`) in `bot_state.json`. The web app uses SQLite. They are completely separate stores. `/link` and `/link_status` commands exist to associate a `chat_id` with a web `User`, but no data migration or sync actually happens after linking. A user logging sets via `/log` never appears in the web app's sessions, and vice versa.
- **garmin_cache.json**: Garmin data written to a local file, never to SQLite. Lost on deploy restart. Not visible in web app at all.
- **`_linked_user_ids`** (telegram_bot.py): In-memory set, lost on restart. Link status survives in DB but bot doesn't reload it at startup.

### 7b. Underused Features
- **`build_rich_context()`** (claude_service.py): Fully implemented function that assembles 7-day training, recovery, nutrition, weight trend, PRs, and weak points into a rich prompt string. **Never called anywhere** in the codebase.
- **`generate_progressive_overload_suggestion()`**: Called in web API `POST /api/sessions/{id}/sets` but the returned suggestion string is never stored or returned to the client — computed and discarded.
- **Garmin HRV/RHR data**: Fetched and cached but only injected into check-in; not surfaced in `/progress`, not shown in web app, not used in plan generation.
- **`equipment_available` and `injuries` columns** (UserProfile): Added via migration, never written to by any route or bot command.
- **`show_date` column** (UserProfile): Added via migration, never used.
- **MFP integration**: Only syncs today's diary on demand via `/mfp`. No automatic daily sync scheduled.
- **`/api/subscription/upgrade`**: Returns 501. No payment flow exists.

### 7c. Duplicated Logic
- **Streak calculation**: Implemented in `telegram_bot.cmd_streak` (pure Python over bot JSON) AND in `main.py` via `UserStreak` table updates in `POST /api/checkins` and `POST /api/sessions/{id}/end`. Two completely separate streak counters for the same user.
- **PR detection**: Implemented in `telegram_bot._do_log_set` (writes to `user["prs"]` JSON) AND in `main.py POST /api/sessions/{id}/sets` (writes to `PersonalRecord` table). Same logic, two stores.
- **Epley 1RM formula**: `_epley_1rm()` in telegram_bot.py and inline in main.py's set logging route.
- **Recovery score formula**: `generate_recovery_insight()` in claude_service called by both bot and web; but bot also has a local fallback formula if Claude fails.
- **Day-of-week exercises lookup**: `_get_today_exercises` (legacy, never called post-refactor) and `_get_session_exercises` (active). Both do the same dict traversal.

### 7d. Risky Code

**Missing authentication (HIGH)**:
- `GET /api/analyses` — returns all body analyses with no auth
- `POST /api/plan/generate` — free, unauthenticated Claude Opus call (cost risk)
- `GET /api/plan/current` — unauthenticated
- `GET /api/progress` — unauthenticated
- `GET /api/research` — unauthenticated
- `POST /api/research/refresh` — unauthenticated, triggers expensive Claude calls

**Bare except / silent swallows**:
- `_migrate_db()` (main.py): `except Exception: pass` on each migration — hides real DB errors
- `cmd_checkin` (telegram_bot.py): `except Exception:` swallows Garmin failure silently
- `_fetch_research_summaries` (telegram_bot.py): `except Exception:` per topic — some topics silently skipped

**Data integrity**:
- Most DB tables use `chat_id: Integer` (not a FK to `users.id`). No referential integrity; rows orphan easily. Multi-user expansion requires a migration.
- `WorkoutSession.chat_id = 0` convention for web sessions — fragile.
- No unique constraint on `(chat_id, date)` in `DailyCheckIn` — duplicate check-ins for same day possible.
- No unique constraint on `(chat_id, exercise_name)` in `PersonalRecord` — multiple PR rows per exercise accumulate.

**Encryption**:
- `crypto_utils.py`: If `ENCRYPTION_KEY` is unset, `Fernet("")` raises at import time — startup crash with no graceful error message.

**File storage**:
- Photos saved to `uploads/` on local disk. Lost on Railway/Heroku deploy. No S3/R2.

**State loss**:
- `bot_state.json` is the single source of truth for all bot users. No backup, no atomic write (`write_text` is not atomic). Power loss mid-write can corrupt all user data.

**Cooldown bypass**:
- `_plan_cooldowns` and `_analyze_cooldowns` are in-memory dicts — reset on bot restart, allowing unlimited Claude Opus calls.

**JWT secret**:
- `SECRET_KEY` defaults to `""` if unset — JWTs become trivially forgeable.

### 7e. Missing vs. Vision
| Vision Item | Status |
|---|---|
| Stripe billing / tiered subs | ❌ Stub only (501 on upgrade) |
| S3/R2 photo storage | ❌ Local disk only |
| Apple Health / Google Fit | ❌ Not started |
| Oura Ring integration | ❌ Not started |
| Show prep mode | ❌ Not started |
| Before/after photo comparison | ❌ Not started |
| Shareable progress cards | ❌ Not started |
| Admin dashboard | ❌ Not started |
| Email/SMS notifications | ❌ Not started |
| Multi-user web auth | ✅ Implemented |
| Streak system (web) | ✅ Implemented |
| Badge system (web) | ✅ Implemented — not shown in UI |
| Goal tracking | ✅ Implemented (bot + web) |
| Weak-point analysis | ✅ Bot only |
| Weekly report | ✅ Bot + web (Pro tier) |
| Research integration | ✅ Implemented |
| Garmin sync | ✅ Bot only |
| MFP sync | ✅ Bot only, on-demand |
| Progressive overload suggestions | ✅ Implemented — result discarded in web |

### 7f. TODO / FIXME
None found in source files.

### 7g. Dead Code

**Dead functions**:
- `claude_service.build_rich_context()` — implemented, never called
- `telegram_bot._get_today_exercises()` — superseded by `_get_session_exercises()`, never called after refactor

**Dead routes** (app.js likely never calls):
- `GET /api/badges` — no badge UI in static/app.js
- `GET /api/dashboard/summary` — verify if app.js calls it

**Dead env vars**:
- `SECRET_KEY` defaults to `""` — used for JWT signing; unset = forgeable tokens

**Unused columns**:
- `UserProfile.equipment_available` — column exists, never written
- `UserProfile.injuries` — column exists, never written
- `UserProfile.show_date` — column exists, never written
- `BodyAnalysis.body_fat_confidence` — populated by Claude, never read by any route

---

Phase 1 complete. Ready for Phase 2 approval.
