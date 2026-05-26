# ARCHITECTURE.md — BodyBuilding Coach AI
_Generated: 2026-05-26 | Phase 2 design only — no code edits made_

---

## 1. COACH BRAIN

### 1a. Problem Statement

The application currently has no single orchestration layer. Business logic is scattered across:
- `telegram_bot.py` (~3 000 lines of mixed transport, state, and domain logic)
- `main.py` (FastAPI routes each embed their own domain rules)
- `claude_service.py` (prompt construction mixed with API calls)

The result is the disconnected-systems problem documented in **AUDIT §7a**: two separate streak counters, two PR detectors, two Epley implementations, and bot JSON that never syncs to SQLite.

### 1b. coach_brain.py — Proposed Orchestrator

`coach_brain.py` is a pure-Python module with zero transport dependencies. It imports from `claude_service`, `nutrition_service`, `research_service`, and `database`. Both `telegram_bot.py` and `main.py` import from it — neither bot nor web implements domain logic directly.

```
┌──────────────────────────────────────────────────────────┐
│                       coach_brain.py                     │
│                                                          │
│  CoachContext (dataclass)                                │
│  ┌────────────────────────────────────────────────────┐  │
│  │ user_id: int                                       │  │
│  │ chat_id: int | None                                │  │
│  │ profile: UserProfileSnapshot                       │  │
│  │ last_analysis: BodyAnalysisSnapshot | None         │  │
│  │ recent_sessions: list[SessionSnapshot]  (7d)       │  │
│  │ recent_checkins: list[CheckInSnapshot]  (7d)       │  │
│  │ recent_meals: list[MealSnapshot]        (7d)       │  │
│  │ prs: dict[str, PRSnapshot]                         │  │
│  │ goals: list[GoalSnapshot]                          │  │
│  │ research_cache: dict[str, str]          (summaries)│  │
│  │ garmin_today: GarminSnapshot | None                │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  Domain methods (all async, all take CoachContext):      │
│    build_context(db, user_id, chat_id) → CoachContext    │
│    generate_plan(ctx, days) → PlanBundle                 │
│    run_checkin(ctx, scores) → CheckInResult              │
│    log_set(ctx, exercise, kg, reps) → SetResult          │
│    end_session(ctx, session_id) → SessionSummary         │
│    generate_report(ctx) → ReportBundle                   │
│    analyze_weak_points(ctx) → WeakPointResult            │
│    detect_plateau(ctx) → PlateauSignal | None            │
│    suggest_overload(ctx, exercise) → str                 │
│    chat(ctx, message, history) → str                     │
└──────────────────────────────────────────────────────────┘
          ↑                              ↑
   telegram_bot.py                  main.py FastAPI
   (transport only)               (transport only)
```

### 1c. CoachContext — Typed Snapshots

All snapshots are frozen dataclasses (no ORM objects escape `coach_brain`). This makes unit-testing trivial and prevents accidental lazy-load N+1 queries in route handlers.

```python
@dataclass(frozen=True)
class UserProfileSnapshot:
    age: int | None
    gender: str | None
    height_cm: float | None
    weight_kg: float | None
    goal: str        # "bulk" | "cut" | "recomp" | "strength" | "prep"
    experience: str  # "beginner" | "intermediate" | "advanced"
    days_per_week: int
    dietary_restrictions: str | None
    equipment: str | None   # currently dead — AUDIT §7b
    injuries: str | None    # currently dead — AUDIT §7b

@dataclass(frozen=True)
class SessionSnapshot:
    session_id: int
    date: date
    sets: list[SetSnapshot]
    total_volume_kg: float

@dataclass(frozen=True)
class SetSnapshot:
    exercise: str
    weight_kg: float
    reps: int
    estimated_1rm: float   # Epley — computed once here, not twice

@dataclass(frozen=True)
class PRSnapshot:
    exercise: str
    weight_kg: float
    reps: int
    estimated_1rm: float
    achieved_at: datetime
```

### 1d. build_rich_context() — Salvaging Dead Code

`claude_service.build_rich_context()` is fully implemented but **never called** (AUDIT §7b). Under the new design:

1. `coach_brain.build_context()` loads all DB data into `CoachContext`
2. `prompt_builder.context_block(ctx)` serialises `CoachContext` to a rich markdown string (this is what `build_rich_context()` currently does — reuse its logic)
3. Every Claude call receives the context block — no more "generate plan without knowing the user's injury history"

### 1e. Single Source of Truth — Migration Path

The bot JSON / SQLite split (AUDIT §7a) cannot be collapsed in one step without breaking production. The migration path:

**Step 1 (Phase 3a)**: `coach_brain.build_context()` reads from SQLite exclusively. Bot commands that need user data call `build_context(db, user_id=0, chat_id=chat_id)` using the existing `chat_id`-keyed tables. Bot JSON becomes read-only legacy.

**Step 2 (Phase 3b)**: All bot write operations (`log_set`, `run_checkin`, etc.) write to SQLite through `coach_brain` domain methods. Bot JSON writes are removed one command at a time.

**Step 3 (Phase 3c)**: `bot_state.json` retired. Garmin cache migrated to `GarminReading` table (see §4).

---

## 2. DATA FLOW MAPS

### 2a. Photo → Plan (Unified Path)

Current state: bot and web are entirely separate paths that never share data (AUDIT §7a). Target:

```
[Telegram photo] ──────┐
                        ├──→ coach_brain.analyze_photo(ctx, image_bytes)
[Web POST /api/analyze] ┘         │
                                  ├─→ claude_service.analyze_body_photo() [Opus]
                                  ├─→ INSERT body_analyses (user_id FK, not chat_id=0)
                                  └─→ BodyAnalysisSnapshot

User picks day count ──→ coach_brain.generate_plan(ctx, days)
                              │
                              ├─→ research_service.refresh_all_research(profile)
                              │       ├─→ PubMed async fetch
                              │       ├─→ Semantic Scholar async fetch
                              │       └─→ claude_service.summarize_research() [Haiku] × N topics
                              │
                              ├─→ prompt_builder.plan_prompt(ctx, research_summaries)
                              │       └─→ context_block(ctx) + goal_block(ctx) + research_block()
                              │
                              ├─→ claude_service.generate_comprehensive_plan() [Opus]
                              │
                              ├─→ INSERT workout_plans, diet_plans, supplement_plans
                              └─→ PlanBundle

[Bot] _send_plan(update, PlanBundle)      ← transport only, no domain logic
[Web] return PlanBundle as JSON           ← transport only
```

### 2b. Daily Check-In (Unified Path)

```
[/checkin or POST /api/checkins]
    │
    ├─→ coach_brain.run_checkin(ctx, scores={sleep,energy,soreness,stress})
    │       │
    │       ├─→ garmin_service.get_cached(chat_id)  → GarminSnapshot | None
    │       │       └─→ inject hrv_ms, resting_hr_bpm, sleep_duration_hrs if available
    │       │
    │       ├─→ _compute_recovery_score(scores, garmin)  ← single formula (§7c)
    │       │
    │       ├─→ claude_service.generate_recovery_insight() [Haiku]
    │       │
    │       ├─→ INSERT daily_checkins (user_id FK)
    │       ├─→ UPSERT user_streaks (checkin type)
    │       └─→ CheckInResult{recovery_score, tip, streak}
    │
[Bot] format + send message
[Web] return CheckInResult as JSON
```

### 2c. Workout Set Logging (Unified Path)

```
[wk:r:{reps} callback or POST /api/sessions/{id}/sets]
    │
    ├─→ coach_brain.log_set(ctx, exercise, weight_kg, reps)
    │       │
    │       ├─→ _epley_1rm(weight_kg, reps)  ← single implementation (AUDIT §7c)
    │       │
    │       ├─→ INSERT set_logs
    │       │
    │       ├─→ PR check: SELECT max(estimated_1rm) WHERE exercise AND user_id
    │       │       └─→ if new PR: INSERT personal_records + badge
    │       │
    │       ├─→ coach_brain.suggest_overload(ctx, exercise) [Haiku]  ← result no longer discarded (AUDIT §7b)
    │       │
    │       └─→ SetResult{set_id, is_pr, overload_suggestion}
    │
[Bot] show PR badge + suggestion in reply
[Web] return SetResult as JSON (overload_suggestion now surfaced)
```

### 2d. Weekly Report (Unified Path)

```
[/report or POST /api/reports/generate]
    │
    ├─→ coach_brain.build_context(db, user_id, chat_id)  ← 7d window
    │
    ├─→ prompt_builder.report_prompt(ctx)
    │       └─→ context_block + sessions_block + nutrition_block + recovery_block
    │
    ├─→ claude_service.generate_weekly_report() [Opus]
    │
    ├─→ INSERT weekly_reports (user_id FK)
    └─→ ReportBundle

[Bot] format + send (no Pro tier gate — bot users get the report)
[Web] Pro tier required (existing gate preserved)
```

### 2e. Research → Plan Integration

Current state: `refresh_all_research()` writes to `research_cache` table, but `POST /api/plan/generate` only passes one topic (AUDIT §7b). Target:

```
research_service.refresh_all_research(profile)
    │
    └─→ {topic: summary, ...}  ← all topics, not just first

prompt_builder.research_block(research_dict, profile)
    │
    ├─→ filter to goal-relevant topics
    │       bulk → hypertrophy, progressive overload, protein synthesis
    │       cut → fat oxidation, muscle retention, HIIT
    │       prep → peak week, water manipulation, carb cycling
    │
    └─→ formatted research section  ← injected into plan prompt
```

---

## 3. SERVICE INTERACTION PLAN

### 3a. Domain Ownership Map

| Domain | Owner | Current Problem |
|--------|-------|-----------------|
| Body composition analysis | `claude_service` | Correct — keep |
| Plan generation | `coach_brain` → `claude_service` | Currently split across bot + main.py |
| Workout session lifecycle | `coach_brain` | Currently in bot callbacks + FastAPI routes |
| Set logging + PR detection | `coach_brain` | Duplicated (AUDIT §7c) |
| Streak tracking | `coach_brain` | Duplicated (AUDIT §7c) |
| Recovery scoring | `coach_brain` | Formula duplicated; Claude call shared |
| Macro lookup | `nutrition_service` | Correct — keep |
| Research fetch + summarise | `research_service` | Correct — keep |
| Garmin data | `garmin_service` | Correct — keep; migrate cache to DB |
| MFP data | `mfp_service` | Correct — keep |
| Prompt construction | `prompt_builder` (new) | Currently inline in claude_service + bot |
| Credential storage | `crypto_utils` | Correct — keep; add startup validation |
| DB session | `database` | Correct — keep |

### 3b. Orphaned Logic Placement

| Orphan | Current Location | Target |
|--------|-----------------|--------|
| `_epley_1rm()` | telegram_bot.py (private) + inline in main.py | `coach_brain._epley_1rm()` |
| Recovery score formula | `generate_recovery_insight()` fallback in bot + Claude path | `coach_brain._compute_recovery_score()` |
| Streak increment logic | `cmd_streak` in bot + `UserStreak` updates in main.py | `coach_brain._update_streak()` |
| `build_rich_context()` | `claude_service.py` (dead) | Migrate logic to `prompt_builder.context_block()` |
| `_get_today_exercises()` | telegram_bot.py (dead, superseded) | Delete |
| Plan day-of-week resolver | Duplicated in `_get_session_exercises()` + `_get_today_exercises()` | `coach_brain._resolve_plan_day()` |
| `get_goal_system_prompt()` | `claude_service.py` | Move to `prompt_builder.goal_system_prompt()` |

### 3c. Service Dependency Rules

```
telegram_bot.py  ──→  coach_brain  ──→  claude_service
main.py          ──→  coach_brain  ──→  nutrition_service
                       │           ──→  research_service
                       │           ──→  garmin_service
                       │           ──→  mfp_service
                       └───────────→  database (read)
                                   ──→  prompt_builder (new)

prompt_builder  ──→  (no external deps — pure string construction)
crypto_utils    ──→  (no external deps — keep isolated)
```

**Rule**: `telegram_bot.py` and `main.py` must never import `claude_service`, `nutrition_service`, `research_service`, `garmin_service`, or `mfp_service` directly. All calls go through `coach_brain`. This is the hard boundary.

---

## 4. DATABASE IMPROVEMENTS

### 4a. New Tables

#### garmin_readings
Replace `garmin_cache.json` (AUDIT §7d — lost on restart) with a proper table.

```sql
CREATE TABLE garmin_readings (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    reading_date DATE   NOT NULL,
    hrv_ms      REAL,
    resting_hr  INTEGER,
    sleep_hrs   REAL,
    steps       INTEGER,
    calories_active INTEGER,
    raw_json    TEXT,    -- full Garmin payload for forward compat
    fetched_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, reading_date)
);
CREATE INDEX ix_garmin_readings_user_date ON garmin_readings(user_id, reading_date);
```

#### coach_conversations
Store chat history so `_chat_with_coach()` can have true multi-turn memory.

```sql
CREATE TABLE coach_conversations (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    role        TEXT    NOT NULL CHECK(role IN ('user','assistant')),
    content     TEXT    NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX ix_coach_conversations_user ON coach_conversations(user_id, created_at DESC);
```

#### plateau_signals
Persist plateau detection outputs so the weekly report can reference history.

```sql
CREATE TABLE plateau_signals (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    signal_type     TEXT    NOT NULL,  -- 'weight_stall' | 'strength_stall' | 'recovery_decline'
    exercise        TEXT,              -- NULL for weight/recovery signals
    detected_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at     TIMESTAMP,
    details_json    TEXT
);
```

### 4b. Schema Fixes on Existing Tables

| Table | Problem (AUDIT §) | Fix |
|-------|-------------------|-----|
| `daily_checkins` | No unique constraint on (chat_id, date) → duplicate check-ins possible (§7d) | `ALTER TABLE daily_checkins ADD CONSTRAINT uq_checkin_user_date UNIQUE(chat_id, date)` — or handle in app via INSERT OR REPLACE |
| `personal_records` | Multiple rows per exercise accumulate; no constraint (§7d) | Add `UNIQUE(chat_id, exercise_name)` with `ON CONFLICT REPLACE` semantics |
| `body_analyses` | No `user_id` FK — shared across all users (§7d) | Add `user_id INTEGER REFERENCES users(id)` nullable for migration safety |
| `workout_sessions` | `chat_id=0` for web sessions — fragile (§7d) | Add `user_id INTEGER REFERENCES users(id)` column; web uses it, bot uses chat_id |
| All chat_id tables | No FK to users → orphan rows (§7d) | Add `user_id` columns progressively; keep `chat_id` for backward compat |

### 4c. Missing Indexes

```sql
-- Checkin range queries (weekly report, streak calc)
CREATE INDEX IF NOT EXISTS ix_daily_checkins_user_date
    ON daily_checkins(chat_id, date DESC);

-- Set log exercise history (overload suggestion, PR check)
CREATE INDEX IF NOT EXISTS ix_set_logs_exercise
    ON set_logs(exercise_name, logged_at DESC);

-- Meal log date range (macro totals, weekly report)
CREATE INDEX IF NOT EXISTS ix_meal_logs_user_date
    ON meal_logs(chat_id, date DESC);

-- Body measurements trend queries
CREATE INDEX IF NOT EXISTS ix_body_measurements_user_date
    ON body_measurements(chat_id, date DESC);

-- PR lookup by user + exercise (replace full scan + ORDER BY)
CREATE INDEX IF NOT EXISTS ix_prs_user_exercise_1rm
    ON personal_records(chat_id, exercise_name, estimated_1rm DESC);
```

### 4d. Migration Order & Rollback

All migrations run through `_migrate_db()` in `main.py` using idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` wrapped in `try/except`. **No Alembic** (existing convention preserved per AUDIT §5).

Migration order for Phase 3:
1. Add indexes (safe, no data change, instant rollback = `DROP INDEX`)
2. Add `user_id` nullable columns to existing tables (backward compat — rows with NULL still work)
3. Create `garmin_readings` table (additive)
4. Create `coach_conversations` table (additive)
5. Create `plateau_signals` table (additive)
6. Backfill `user_id` from `chat_id` join via `telegram_link_codes` where possible
7. Unique constraint on `daily_checkins(chat_id, date)` — **do this last**, after deduplicating existing rows

Rollback for any step: because all changes are additive (new columns nullable, new tables, new indexes), rollback = ignore the new columns/tables in application code. No destructive migration until Phase 4+.

---

## 5. TELEGRAM COMMAND ARCHITECTURE

### 5a. Domain Groupings

Reorganise the 26 commands (AUDIT §4) into 5 domain groups. Each group owns a dedicated handler module imported by `telegram_bot.py`.

```
handlers/
├── onboarding.py     /start, /help, /profile, /units, /connect, /link, /link_status, /billing
├── training.py       /log, /workout, /logset, /plan, /weakpoints, /stats, /progress
├── nutrition.py      /meal, /macros, /mfp
├── recovery.py       /checkin, /measurements, /weight, /streak, /goals
└── reporting.py      /report, /research, /reminders
```

Each module exports:
- `register(app: Application) -> None` — adds its handlers to the app
- A set of `async def cmd_*(update, context)` functions
- No direct imports of `claude_service`, `garmin_service`, etc. — calls go through `coach_brain`

`telegram_bot.py` becomes a thin bootstrap:

```python
from handlers import onboarding, training, nutrition, recovery, reporting

async def _post_init(app):
    onboarding.register(app)
    training.register(app)
    nutrition.register(app)
    recovery.register(app)
    reporting.register(app)
    await _set_bot_commands(app)
    _start_scheduler(app)
```

### 5b. Conversation-State Pattern

The current `active_command` + `command_state` dict (ad-hoc FSM in bot JSON) is replaced with python-telegram-bot's `ConversationHandler`. This eliminates the multi-step parsing scattered across `handle_message()`.

**Example — /checkin as ConversationHandler**:

```
SLEEP, ENERGY, SORENESS, STRESS = range(4)

checkin_conv = ConversationHandler(
    entry_points=[CommandHandler("checkin", checkin_start)],
    states={
        SLEEP:    [MessageHandler(filters.TEXT & ~filters.COMMAND, got_sleep)],
        ENERGY:   [MessageHandler(filters.TEXT & ~filters.COMMAND, got_energy)],
        SORENESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_soreness)],
        STRESS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, got_stress)],
    },
    fallbacks=[CommandHandler("cancel", cancel_conv)],
    per_user=True,
    per_chat=True,
)
```

Each state handler validates input, prompts for the next value, and on the final state calls `coach_brain.run_checkin()`. No `active_command` string needed.

**Flows that need ConversationHandler**:
- `/checkin` (4 steps)
- `/connect` (Garmin/MFP multi-step — currently 8+ state transitions)
- `/profile` (optional multi-field entry)
- `/measurements` (6 fields)
- `/goals` (goal type + target + date)

### 5c. Inline Keyboard Architecture

All callback_data follows a strict schema to avoid unbounded string matching:

```
Format:  <domain>:<action>:<payload>
Examples:
  wk:ex:bench_press          → workout: exercise selected
  wk:w:100.0                 → workout: weight selected (always kg)
  wk:r:8                     → workout: reps selected
  wk:end:0                   → workout: end session
  wk:day:Monday              → workout: day selected for swap
  wk:pick:0                  → workout: show exercise picker
  plan:days:4                → plan: 4-day split requested
  checkin:score:7            → checkin: inline score selection
  goal:type:weight_loss      → goal: type selected
  report:share:0             → report: share to channel
```

**Callback router pattern**:

```python
CALLBACK_ROUTES = {
    "wk":      handle_workout_callback,
    "plan":    handle_plan_callback,
    "checkin": handle_checkin_callback,
    "goal":    handle_goal_callback,
    "report":  handle_report_callback,
}

async def route_callback(update, context):
    domain = update.callback_query.data.split(":")[0]
    handler = CALLBACK_ROUTES.get(domain)
    if handler:
        await handler(update, context)
    else:
        await update.callback_query.answer("Unknown action")
```

This replaces the current monolithic `handle_workout_callback` and separate `handle_plan_days_callback`.

### 5d. State Persistence

Bot conversation state (active sessions, current command step) moves from `bot_state.json` into:
- `context.user_data` — ephemeral per-session (already used for `wk_ex`, `wk_w`)
- SQLite `workout_sessions` — durable session state (already exists; just start using it from bot)
- `ConversationHandler` internal state — replaces `active_command`/`command_state`

`bot_state.json` is reduced to: `{chat_id: {units, subscription_tier, garmin_email, garmin_pass_enc, mfp_username, mfp_pass_enc}}` — credential storage only, not workout data.

### 5e. Error Handling

Replace bare `except Exception: pass` (AUDIT §7d) with:

```python
async def safe_reply(update, text: str) -> None:
    try:
        await update.effective_message.reply_text(text)
    except TelegramError:
        pass  # user blocked bot — acceptable

# In every command handler:
try:
    result = await coach_brain.some_operation(ctx, ...)
except CoachBrainError as e:
    await safe_reply(update, f"⚠️ {e.user_message}")
    logger.error("coach_brain error: %s", e, exc_info=True)
```

`CoachBrainError` is a base exception class with a `user_message: str` field — safe to show to end users.

---

## 6. AI PROMPT ARCHITECTURE

### 6a. prompt_builder.py — Module Design

All prompt construction lives in `prompt_builder.py`. `claude_service.py` becomes purely responsible for API calls — it receives fully-formed `system` and `messages` parameters.

```python
# prompt_builder.py public API

def goal_system_prompt(goal: str) -> str:
    """Return the coaching persona system prompt for the given goal."""

def context_block(ctx: CoachContext) -> str:
    """Serialise CoachContext to a structured markdown block for injection."""

def plan_prompt(ctx: CoachContext, days: int, research: dict[str, str]) -> str:
    """Full user-turn prompt for comprehensive plan generation."""

def checkin_prompt(scores: dict, garmin: GarminSnapshot | None) -> str:
    """Prompt for recovery insight generation."""

def overload_prompt(ctx: CoachContext, exercise: str) -> str:
    """Prompt for progressive overload suggestion."""

def report_prompt(ctx: CoachContext) -> str:
    """Full user-turn prompt for weekly report generation."""

def weak_points_prompt(ctx: CoachContext) -> str:
    """Prompt for weak-point analysis."""

def research_filter(research: dict[str, str], goal: str) -> dict[str, str]:
    """Return the subset of research topics relevant to the given goal."""
```

### 6b. Reusable Prompt Blocks

Each block is a function returning a string section. Blocks compose into full prompts.

```
context_block(ctx)
├── athlete_block(ctx.profile)          → age, weight, goal, experience, equipment, injuries
├── training_block(ctx.recent_sessions) → last 7d sets, volume per muscle, top lifts
├── recovery_block(ctx.recent_checkins) → avg sleep, energy, HRV trend if available
├── nutrition_block(ctx.recent_meals)   → avg calories, protein, carb, fat vs targets
├── progress_block(ctx.prs)             → top PRs per muscle group
├── goal_block(ctx.goals)               → active goals + days remaining + current deficit/surplus
└── analysis_block(ctx.last_analysis)   → body fat, weak points, physique score (if available)

research_block(filtered_research)
└── top 3 relevant topic summaries, truncated to ~300 tokens each
```

**Current problem**: `generate_comprehensive_plan()` in `claude_service.py` builds its own internal prompt and also receives a `goal_system_prompt` injected from outside. The `_build_plan_prompt()` in `telegram_bot.py` builds a separate prompt. These need to collapse into a single `prompt_builder.plan_prompt()`.

### 6c. Versioned Prompts

Prompt text is versioned so A/B testing and rollback are possible without code changes.

```
prompts/
├── v1/
│   ├── system_bulk.txt
│   ├── system_cut.txt
│   ├── system_recomp.txt
│   ├── system_strength.txt
│   ├── system_prep.txt
│   └── system_beginner.txt
└── v2/   (future)
```

`prompt_builder.goal_system_prompt(goal, version="v1")` reads from the appropriate file. The version is stored in `weekly_reports.prompt_version` and `workout_plans.prompt_version` columns so report quality can be traced to the prompt that generated it.

### 6d. Model Routing

Codify the model selection rules that are currently implicit (some hardcoded, some via constants — AUDIT §2):

```python
# claude_service.py constants — single source of truth
ANALYSIS_MODEL  = "claude-opus-4-7"    # photo analysis
PLAN_MODEL      = "claude-opus-4-7"    # comprehensive plan
REPORT_MODEL    = "claude-opus-4-7"    # weekly report (currently hardcoded claude-sonnet-4-6 — BUG)
WEAK_POINT_MODEL = "claude-haiku-4-5-20251001"  # currently hardcoded claude-sonnet-4-6 — BUG
INSIGHT_MODEL   = "claude-haiku-4-5-20251001"   # checkin, overload, next-session
SUMMARY_MODEL   = "claude-haiku-4-5-20251001"   # research summaries
CHAT_MODEL      = "claude-haiku-4-5-20251001"   # conversational coach
```

The two hardcoded `"claude-sonnet-4-6"` strings in `generate_weekly_report()` and `analyze_weak_points()` are bugs that bypass cost control — they must be replaced with the constants.

### 6e. Token Budgets

Estimated token usage per call type (for cost monitoring):

| Call | Model | Input tokens (est.) | Output tokens (est.) | Cost tier |
|------|-------|--------------------|--------------------|-----------|
| `analyze_body_photo` | Opus | 4 000 + image | 800 | High |
| `generate_comprehensive_plan` | Opus | 3 000 | 2 000 | High |
| `generate_weekly_report` | Opus | 4 000 | 1 500 | High |
| `analyze_weak_points` | Haiku | 2 000 | 600 | Low |
| `generate_recovery_insight` | Haiku | 500 | 200 | Low |
| `suggest_overload` | Haiku | 800 | 150 | Low |
| `summarize_research` × N | Haiku | 1 500 | 400 | Low |
| `_chat_with_coach` | Haiku | 1 000 + history | 300 | Low |

Context window guard: `context_block()` must enforce a character budget (~6 000 chars ≈ 1 500 tokens) by truncating `recent_sessions` and `recent_meals` to the most recent 7 days, and research summaries to 300 tokens each.

---

## 7. ADAPTIVE FEATURE SPECS

### 7a. Progression Engine

**Current state**: `generate_progressive_overload_suggestion()` is called in the web API but the result is silently discarded (AUDIT §7b). The bot generates next-session targets after session end but doesn't persist them.

**Target behavior**:

Input: `CoachContext` + exercise name
Output: `OverloadSuggestion` — weight and rep targets for next session

**Algorithm** (no Claude needed for standard cases — Claude for complex cases):

```
1. Fetch last 3 sessions of this exercise from set_logs
2. For each session: compute best set (highest estimated_1rm)
3. Apply progression rule based on exercise type:
   - Compound (bench, squat, deadlift, OHP, row):
       if completed all planned reps in last session → +2.5kg (or +5kg lower body)
       if failed last rep → hold weight, add 1 rep target
       if stalled 2+ sessions → deload: -10%, then resume linear
   - Isolation (curl, extension, lateral raise):
       if completed all planned reps → +1-2kg or +2 reps (double progression)
4. If rule produces a suggestion → return it (no Claude call)
5. If coach detects complex situation (long stall, deload cycle, injury flag) → Claude Haiku
```

This avoids Claude for 80% of suggestions while still using AI for edge cases.

**Persistence**: Store suggestions in a new `progression_suggestions` table. Surface in bot after session end and in web set-logging response.

### 7b. Recovery Formula

**Current state**: Recovery score computed by Claude Haiku with a fallback Python formula if Claude fails. Two separate implementations (AUDIT §7c).

**Target**: A single deterministic formula in `coach_brain` that runs first. Claude provides the coaching tip/narrative, not the score.

```
recovery_score = (
    sleep_score  * 0.30 +
    energy_score * 0.25 +
    (10 - soreness_score) * 0.20 +
    (10 - stress_score)  * 0.15 +
    hrv_modifier         * 0.10  ← 0 if no Garmin, else (hrv_ms - baseline) / baseline scaled to 0-10
)

hrv_modifier: user's 30-day rolling average HRV as baseline;
              today's HRV vs. baseline → +/- adjustment capped at ±2 pts

recovery_label:
    ≥ 8.0 → "Excellent — train hard"
    6.0–7.9 → "Good — normal training"
    4.0–5.9 → "Moderate — consider reducing volume"
    < 4.0  → "Poor — active recovery or rest"
```

Claude Haiku receives the score, label, and raw inputs and generates a 2-sentence personalised coaching tip. Score itself is deterministic.

### 7c. Plateau Detection

**Current state**: `_weekly_stall_check` in APScheduler detects weight stalls but calls `build_rich_context()` which is dead code (AUDIT §7b + §7e). Result: stall check silently does nothing useful.

**Target**: `coach_brain.detect_plateau(ctx) -> PlateauSignal | None`

Three signal types:

**Weight plateau**: 14-day window
```
slope = linear_regression(dates, body_weight_kg values)
if goal == "bulk" and slope < +0.1 kg/week → WEIGHT_STALL
if goal == "cut"  and slope > -0.2 kg/week → WEIGHT_STALL
```

**Strength plateau**: Per-exercise
```
last_4_sessions = set_logs for exercise, last 4 occurrences
if max(estimated_1rm) in last_4_sessions has not increased → STRENGTH_STALL
trigger after 3 consecutive stalled sessions
```

**Recovery decline**: 7-day trend
```
avg_recovery_last_7d vs avg_recovery_prev_7d
if delta < -1.5 points → RECOVERY_DECLINE (possible overtraining)
```

When a signal is detected:
1. Insert into `plateau_signals` table
2. Claude Haiku generates a brief intervention message
3. Send proactively to user (bot push or web notification placeholder)
4. Weekly report section references open `plateau_signals`

### 7d. Weak-Point Analysis

**Current state**: `analyze_weak_points()` calls Claude Sonnet (hardcoded — AUDIT §2) with last analysis + set logs. Works in bot only; no web equivalent.

**Target**: Structured output with actionable prescription.

```python
@dataclass
class WeakPointAnalysis:
    weak_muscles: list[str]          # e.g. ["rear delts", "hamstrings"]
    volume_gaps: dict[str, float]    # muscle_group → sets_per_week deficit vs ideal
    exercise_prescriptions: list[str] # specific exercises to add
    frequency_adjustment: str         # "Add 1 hamstring day" etc.
    priority_rank: list[str]          # ordered by impact on goal
```

Input to Claude: `context_block(ctx)` + physique analysis highlights + current volume per muscle group (computed from `set_logs`).

Output: JSON matching `WeakPointAnalysis` schema. Claude Haiku is sufficient (AUDIT §6d — fix the Sonnet hardcode).

Volume-per-muscle computation is deterministic (no Claude):
```
muscle_map = {
    "bench press": ["chest", "front_delts", "triceps"],
    "squat": ["quads", "glutes"],
    ...
}
weekly_volume = {muscle: sum(sets for all exercises targeting muscle, last 7d)}
ideal_volume = {muscle: 12-20 sets/week for hypertrophy, 8-12 for strength, ...}
```

### 7e. Weekly Report — Enhanced Structure

**Current state**: `generate_weekly_report()` produces a free-form AI report. The JSON shape has `ai_insights` but no structured sections (AUDIT §3).

**Target**: Structured report with deterministic data + AI narrative per section.

```python
@dataclass
class ReportBundle:
    # Deterministic data (no Claude)
    week_start: date
    sessions_completed: int
    sessions_planned: int
    total_volume_kg: float
    new_prs: list[PRSnapshot]
    avg_recovery_score: float
    avg_daily_protein_g: float
    protein_target_g: float
    weight_change_kg: float    # vs prior week
    plateau_signals: list[PlateauSignal]

    # AI narrative (Claude Opus)
    performance_narrative: str    # what went well
    recovery_narrative: str       # sleep/HRV/stress patterns
    nutrition_narrative: str      # protein adherence, caloric context
    recommendations: list[str]    # 3 specific next-week adjustments
    motivational_close: str       # 1 sentence coach voice
```

Claude receives the deterministic data block + `context_block(ctx)` and fills in the narrative sections only. This reduces hallucination risk and allows re-generating narratives without re-running the data queries.

### 7f. Retention Loops

**Current state**: Three APScheduler jobs exist (daily Garmin sync, weekly stall check, daily missed workout check). Stall check is broken (AUDIT §7e). No badge triggers are hooked to events. No onboarding nudge after inactivity.

**Target retention events**:

| Trigger | Timing | Message |
|---------|---------|---------|
| 3-day check-in streak | On achievement | Badge + "You're building a habit" |
| New PR | Immediately after set | "New PR! [exercise] [weight]×[reps]" |
| Missed planned session | Same day 8pm | "Did you train [Day Name] today? Log it or swap." |
| 7-day inactivity | 7 days after last command | "Your coach misses you. /checkin to reconnect." |
| Weight stall (cut) | On plateau detection | "Plateau detected — here's what to adjust." |
| Weight stall (bulk) | On plateau detection | "Gaining slower than expected — let's diagnose." |
| Upcoming show date | 4 weeks, 2 weeks, 1 week | "X weeks to your show. Plan review?" |
| Weekly report ready | Monday morning | "Your weekly report is ready. /report" |

Retention messages are suppressed if user has interacted in the last 24h (anti-spam guard). Each event type has a `last_sent_at` column in `user_streaks` or a new `retention_events` table to prevent duplicate sends.

**Badge system** (currently implemented in web DB but not triggered — AUDIT §7b):

| Badge | Trigger |
|-------|---------|
| `first_session` | First workout logged |
| `first_pr` | First personal record |
| `week_streak_3` | 3-day check-in streak |
| `week_streak_7` | 7-day check-in streak |
| `iron_will` | 30-day check-in streak |
| `protein_target` | 7 consecutive days hitting protein goal |
| `plateau_buster` | First session after plateau resolved |
| `show_prep` | Show date set + 12+ weeks out |

Badge grant events fire from `coach_brain` domain methods and are delivered by both Telegram push and web notification.

---

## Summary of Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| `coach_brain.py` as orchestrator | Eliminates the bot/web logic duplication and the disconnected-systems problem (AUDIT §7a); enables single set of unit tests for domain logic |
| No Alembic | Existing `_migrate_db()` convention; additive-only migrations reduce rollback risk; Alembic adds operational complexity for a single-developer project |
| `ConversationHandler` for multi-step flows | Eliminates the `active_command` FSM from bot JSON; python-telegram-bot already provides this abstraction |
| Haiku for most AI calls | Cost control; Haiku is sufficient for structured JSON generation with clear schema prompts |
| Fix Sonnet hardcodes → Haiku | Both `generate_weekly_report` and `analyze_weak_points` use hardcoded Sonnet, bypassing cost constants — replace with REPORT_MODEL and WEAK_POINT_MODEL |
| Deterministic formulas first, Claude for narrative | Recovery score, 1RM, progression rules, plateau detection are mathematical — Claude adds narrative but shouldn't own the numbers |
| `bot_state.json` reduced to credentials only | Eliminates data-loss risk on restart; all workout/nutrition/checkin data in SQLite |
| `garmin_readings` table | Fixes restart cache loss (AUDIT §7d); enables HRV trend analysis and web visibility |
| Nullable `user_id` migration | Safe additive migration; existing `chat_id` rows keep working; link happens gradually |

---

Phase 2 complete. Flag any architecture decisions you disagree with before I begin Phase 3.
