# CHANGELOG

## 2026-05-27

### Feature #11 — Web Check-In Form
**Files**: `main.py`, `static/app.js`, `static/index.html`
**What**:
- Added "Log Check-In" button directly inside the Recovery Trend card on the Dashboard tab — always visible regardless of prior check-in history.
- Clicking the button reveals an inline slider form (Sleep, Energy, Soreness, Stress — each 1–10) with live numeric readout; no modal or page change.
- If user has already checked in today: button reads "Edit Today's Check-In", sliders pre-fill with current values, and the badge "✓ Logged today" is shown. Submitting calls `PUT /api/checkins/{id}` to update.
- First-time daily check-in submits via `POST /api/checkins`; recovery score and coaching tip regenerated on both paths.
- `POST /api/checkins` now upserts (no more 409 on same-day re-submit): moves scores and AI call before the duplicate check, updates the existing row if found.
- New `PUT /api/checkins/{checkin_id}` endpoint: validates ownership + today-only constraint, regenerates recovery score and coaching tip.
- On success: toast shows "Recovery logged — score: X/100", cache invalidated, dashboard refreshes automatically.
- `renderRecoveryWidget()` updated to always show the recovery card (previously hidden when no check-ins existed), making the button always discoverable.
- `state.latestCheckins` and `state.todayCheckinId` added to application state.

**Rationale**: Non-Telegram users had no way to submit daily check-ins via the web app. This closes the feature parity gap and makes the recovery pipeline (scoring, streaks, lapse nudges) available to all users regardless of whether they use Telegram.

**Rollback**: Revert `POST /api/checkins` upsert logic (restore 409 path), remove `PUT /api/checkins/{checkin_id}` endpoint, remove button/form HTML from `#recovery-card`, remove `openCheckinForm`, `closeCheckinForm`, `submitCheckin` from `app.js`, restore `renderRecoveryWidget` hide-when-empty logic.

### Feature #10 — Premium Polish: Comp Prep Mode, PDF Reports, Before/After View
**Files**: `main.py`, `static/app.js`, `static/index.html`
**What**:

**Comp prep countdown**:
- `GET /api/dashboard/summary` now returns `days_to_show: int | None` — computed from `UserProfile.show_date` when `goal == "prep"`.
- `renderRetentionWidget` shows a countdown banner: ≤7 days → red urgency; ≤30d → gold "stay sharp"; >30d → gold info. "Show day!" message on day-of.
- Profile form now includes a `show_date` (date input) field for competition show date.
- `POST /api/profile` saves `show_date`; `GET /api/profile` returns it.

**PDF reports (print-to-PDF)**:
- `printReport(id)` opens a print-formatted HTML window (portrait, clean typography) and calls `window.print()`. User selects "Save as PDF" from the browser print dialog. No external library.
- Reports are stored in `_reportsById` map on load; print button calls `printReport(r.id)` by integer key (no JSON-in-HTML).
- Each report card in the Reports tab now has a "PDF" button.

**Before/After photo comparison**:
- Progress tab now shows a "Before vs Now" card (hidden when <2 analyses) with the oldest and newest photo side by side (3:4 aspect ratio, arrow separator), plus body fat and physique score change columns.
- `loadProgress()` populates the comparison card from `data[0]` (oldest) and `data[data.length-1]` (newest).

**No new dependencies** — PDF via browser print, photo comparison via CSS flexbox.
**Rollback**: Remove `days_to_show` from `dashboard_summary` + revert show_date profile changes; delete `printReport`/`_reportsById`/PDF button; remove comparison-card from `index.html` + revert `loadProgress()`.

### Feature #9 — Retention Systems: Streaks, Lapse Nudges, Milestone Celebrations
**Files**: `main.py`, `static/app.js`, `static/index.html`
**What**:
- `GET /api/dashboard/summary` now returns `last_workout_date` and `last_checkin_date` (from latest `workout_sessions.ended_at` and `daily_checkins.date`).
- Added `retention-card` to dashboard HTML: shows workout streak (gold), check-in streak (green), badges count, sessions this week, and a lapse nudge banner.
- Lapse nudge logic: ≥4 days since last workout → red "streak at risk" banner; 2-3 days → gold "keep the momentum" banner; ≥2 days since last check-in (and no workout nudge) → blue "check in today" banner.
- `renderRetentionWidget(summary)` function reads `summary.streaks`, `summary.badges_count`, `summary.sessions_this_week`, `summary.last_workout_date`, `summary.last_checkin_date`.
- `endSession()` now shows a celebration toast for milestone streak values (7, 14, 30, 60, 90 days) 1.5s after the session-done toast.
**Rationale**: Streak data was computed and stored but never surfaced in the web UI. Users had no visibility into their consistency stats or any nudge to return after a lapse.
**Rollback**: Remove `retention-card` from `index.html`; remove `renderRetentionWidget` and milestone toast from `app.js`; revert `dashboard_summary` to remove `last_workout_date`/`last_checkin_date` fields and last_session/last_checkin_row queries.

### Feature #8 — Athlete Memory: coach_memory Table + Context Injection
**Files**: `models.py`, `main.py`, `coach_brain.py`, `prompt_builder.py`
**What**:
- Added `CoachMemory` model (`coach_memories` table): `(id, chat_id, content, memory_type, created_at)`. Created by `Base.metadata.create_all` (new table, no migration entry needed).
- `GET /api/memory?limit=20` — list memories for current user.
- `POST /api/memory` — store a custom memory (content, memory_type). Content capped at 500 chars.
- Auto-write PR memories: in `POST /api/sessions/{id}/sets`, whenever a new PR is detected, a memory is written (`memory_type="pr"`) with the exercise, weight, reps, and estimated 1RM. Previous best included if it was an improvement.
- Added `MemorySnapshot(memory_id, content, memory_type, created_at)` frozen dataclass to `coach_brain.py`.
- Updated `CoachContext` to include `recent_memories: tuple[MemorySnapshot, ...]`.
- Added `_load_memories(db, chat_id, k=5)` helper — returns the most recent K memories by `created_at DESC`.
- `build_context()` now calls `_load_memories` and populates `recent_memories`.
- `context_block()` in `prompt_builder.py` appends `Coach memories: <semi-colon separated>` when `ctx.recent_memories` is non-empty — injected into every AI call.
**Rationale**: The coach had no memory between sessions. PR history lived only in `personal_records` (current best, not history). Memories give the AI conversational continuity — it can reference past PRs, patterns, and coaching notes.
**Rollback**: Remove `CoachMemory` model; remove `GET/POST /api/memory` endpoints; remove PR memory write from `log_set`; remove `MemorySnapshot` + `recent_memories` from coach_brain; revert `context_block` to remove memory line.

### Feature #7 — Weekly Athlete Report: Sunday Auto-Job + Reports Tab
**Files**: `models.py`, `main.py`, `static/app.js`, `static/index.html`
**What**:
- Added `next_week_focus = Column(Text, nullable=True)` and `adherence_rating = Column(String, nullable=True)` to `WeeklyReport` model; idempotent migrations added.
- Updated `POST /api/reports/generate` to persist both new fields.
- Updated `GET /api/reports` to return `next_week_focus` and `adherence_rating` in each report dict.
- Added `_auto_weekly_reports()` async function: Sunday 8:00 UTC, iterates all active Pro/Elite users, skips users who already have a report for the current ISO week, generates + stores a report for each. Errors per-user are non-fatal (logged as warnings).
- Added `_lifespan(app)` FastAPI asynccontextmanager that starts/stops an `AsyncIOScheduler` with the Sunday cron job. `app = FastAPI(..., lifespan=_lifespan)`.
- Added "Reports" nav tab and `<section id="tab-reports">` with Generate button.
- Added `generateReport()` (calls `POST /api/reports/generate`, refreshes list) and `loadReports()` (renders report cards with adherence badge, stats row, coaching insights list, next-week focus banner).
**Rationale**: Reports were generated on demand but never auto-triggered and never displayed in the web UI. The Sunday job makes Pro+ feel like a real coaching product. The UI completes the web-side feature parity.
**Rollback**: Remove `_auto_weekly_reports`, `_lifespan`; revert `app = FastAPI(...)` to remove lifespan; remove `asynccontextmanager` and `AsyncIOScheduler` imports; remove new columns from model and migration list; revert `GET /api/reports` and `POST /api/reports/generate`; remove Reports tab from HTML + `loadReports`/`generateReport` from JS.

### Feature #6 — Weak-Point Analysis Web Surface
**Files**: `main.py`, `static/app.js`, `static/index.html`
**What**:
- Added `POST /api/analysis/weak-points` endpoint. Gate: free users get HTTP 402 with upgrade message. Loads the 5 most recent body analyses + last 30 days of set_logs, then calls `analyze_weak_points()` with CoachContext prepended. Returns `{weak_points, volume_recommendations, priority_fix}`.
- Added "Weak-Point Analysis" card at the bottom of the Analysis tab with an "Analyze Weak Points" button.
- `generateWeakPoints()` calls the endpoint, renders a priority fix banner (gold border), bulleted weak points list, and per-muscle volume recommendation table.
- No new DB schema changes.
**Rationale**: The bot already had `/weakpoints` but the web app had no equivalent. The API function and data were already wired; only the endpoint and UI were missing.
**Rollback**: Remove `POST /api/analysis/weak-points` endpoint; remove weak-points card from `index.html`; delete `generateWeakPoints()` from `app.js`.

### Feature #5 — Plateau Detection: Rolling 4-Week 1RM Trend Analysis
**Files**: `main.py`, `static/app.js`, `static/index.html`
**What**:
- Added `GET /api/progress/plateaus?weeks=4` endpoint. Loads `set_logs` via sessions owned by the user for the past N weeks, groups weekly max estimated 1RM per exercise, flags exercises where the last 3 weeks show <2% 1RM change as stalled.
- Returns `[{exercise, stalled, weeks_stalled, weekly_trend, current_1rm, peak_1rm}]` sorted: stalled exercises first, then by peak 1RM.
- Added "Strength Trends (4 weeks)" card to the Progress tab. Each exercise row shows the weekly 1RM trend (`118 → 120 → 120 → 120`), current est. 1RM in gold, and a PLATEAU / PROGRESSING badge.
- `loadProgress()` now fetches plateaus in the same `Promise.all` as photos.
- No new DB tables or columns — computed entirely from existing `set_logs` and `workout_sessions`.
**Rationale**: Plateau detection was listed in the feature queue. `set_logs` already stores per-set `estimated_1rm` + `logged_at`, giving enough signal to detect multi-week 1RM stalls without any schema changes.
**Rollback**: Remove `GET /api/progress/plateaus` endpoint; remove "Strength Trends" card from `index.html`; revert `loadProgress()` to single fetch; delete `renderPlateaus()`.

### Feature #4 — Progression Engine: Persistent Next-Session Targets
**Files**: `models.py`, `main.py`, `static/app.js`, `static/index.html`
**What**:
- Added `next_session_targets = Column(Text, nullable=True)` to `WorkoutSession` model with idempotent `ALTER TABLE` migration in `_migrate_db()`.
- `POST /api/sessions/{id}/end` now persists the `generate_next_session_targets()` result to `workout_sessions.next_session_targets` after generating it (previously computed but discarded).
- `GET /api/sessions/history` now includes `next_session_targets` in each session dict.
- Web workout tab shows a gold-bordered "Next Session Targets" card (`next-session-targets-card`) above the start-session card. Card is populated with the most recent session's targets on tab load, and refreshed immediately after a session ends (replacing the truncated 100-char toast).
- Session history items now render `next_session_targets` inline in gold text below each session's metadata.
- Removed the truncated `next_session_targets.slice(0,100)…` toast; added `_showNextSessionTargets(targets)` helper for idempotent show/hide.
**Rationale**: The AI-generated targets were computed on every session end but immediately discarded — the toast truncated them to 100 chars and they vanished. This makes them persistent and visible at the start of the next workout.
**Rollback**: Remove `next_session_targets` column from `WorkoutSession` model; remove migration entry; remove `if next_session_tip: session.next_session_targets` block; remove field from history response; remove `next-session-targets-card` div; revert `loadWorkout` and `renderSessionHistory`; restore the `slice(0,100)` toast; delete `_showNextSessionTargets`.

### Feature #3 — Recovery Score Web Dashboard Surface
**Files**: `static/app.js`, `static/index.html`
**What**:
- Added a 5th stat card "Recovery Score" to the dashboard stats grid, populated from `GET /api/dashboard/summary → avg_recovery_7d`; color-coded green ≥70, gold 50–69, red <50.
- Added a "Recovery Trend" card on the dashboard showing the 7-day average score and a pure-SVG sparkline of the last 7 check-in recovery scores (oldest→newest, color matches threshold bands).
- Added the latest coaching tip below the sparkline when present.
- `loadDashboard()` now fetches `/dashboard/summary` and `/checkins?limit=7` in the existing `Promise.all` (both cached at 30s, no new network round-trips on re-render).
- Added `_sparklineSvg(scores, w, h)` helper (zero dependencies — raw SVG path + circles) and `renderRecoveryWidget(summary, checkins)` renderer. Both skip gracefully when no check-in data exists.
**Rationale**: `recovery_score` was computed and stored in `daily_checkins` but invisible in the web UI. `/api/dashboard/summary` already returned `avg_recovery_7d`; the frontend just never used it.
**Rollback**: Remove `stat-recovery` card from `index.html` stats-grid; remove `recovery-card` div from `index.html`; revert `loadDashboard()` to two-item `Promise.all`; delete `_sparklineSvg`, `_scoreColor`, `renderRecoveryWidget` from `app.js`.

### Feature #2 — Daily Check-ins: Inline Buttons + SQLite Persistence + Rate Limiting
**Files**: `telegram_bot.py`
**What**:
- Added `_STEP_LABELS` dict, `_score_keyboard(step)`, `_checkins_this_week(user)` helpers.
- `cmd_checkin` now shows a 1-10 inline keyboard (2 rows of 5) for the first step instead of a text prompt. Garmin pre-fill still works with remaining steps shown as inline keyboards. Text fallback (`/checkin sleep=7 energy=6 soreness=5 stress=4`) preserved.
- Added `handle_checkin_callback` to process `ci:{step}:{value}` button taps, auto-advancing through all 4 steps then calling `_finish_checkin`.
- Rate limit gate at the top of `cmd_checkin`: Free users are blocked after 3 check-ins in the last 7 days; Pro/Elite users proceed without restriction.
- `_finish_checkin` now writes each bot check-in to the `daily_checkins` SQLite table (with duplicate guard on `chat_id + date`), bridging the bot→DB gap so `build_context()` and the web app can see bot check-ins. SQLite write failure is warned and non-fatal.
- All `update.message.reply_text()` calls in `_finish_checkin` replaced with `update.effective_chat.send_message()` to make the function safe when called from both message and callback query contexts.
- Registered `CallbackQueryHandler(handle_checkin_callback, pattern=r"^ci:")`.
**Rationale**: Check-in UX was text-only (clunky for 1-10 scores). Bot check-ins never reached SQLite, breaking `build_context()` recovery data for bot users. Free-tier rate limit was missing.
**Rollback**: Remove `handle_checkin_callback`, `_score_keyboard`, `_checkins_this_week`, `_STEP_LABELS`; revert `cmd_checkin` to text prompts; revert `_finish_checkin` to bot-JSON-only write and `update.message.reply_text`; remove `ci:` handler registration.

### Feature #1 — Coach Brain MVP
**Files**: `coach_brain.py`, `prompt_builder.py`, `claude_service.py`, `main.py`, `telegram_bot.py`, `models.py`
**What**: Every AI call now receives a full structured context snapshot of the athlete's last 7 days.
- `coach_brain.py`: Implemented `CoachBrainError` + `build_context(db, user_id, chat_id)` + 10 private DB helper functions. Loads profile, sessions+sets, PRs, check-ins, meals, goals, body analysis, research cache, and Garmin snapshot into a typed frozen `CoachContext`.
- `prompt_builder.py`: Implemented `context_block(ctx)` (SQLite path), `bot_json_context_block(user_data)` (bot JSON path, migrated from dead `claude_service.build_rich_context()`), and `goal_system_prompt(goal)`. Dead stubs remain for Phase 5.
- `claude_service.py`: Added `context_str: str = ""` to 5 functions (`generate_comprehensive_plan`, `generate_recovery_insight`, `generate_weekly_report`, `analyze_weak_points`, `generate_next_session_targets`); when non-empty, prepended to system prompt or user-turn content. Deleted `build_rich_context()` (replaced by `prompt_builder.bot_json_context_block()`).
- `models.py`: Added nullable `user_id` FK column to `WorkoutSession` (done in prior session).
- `main.py`: Migration for `workout_sessions.user_id` column + index; `POST /api/sessions/start` now stores `user_id`; `POST /api/plan/generate`, `POST /api/checkins`, `POST /api/reports/generate` all call `build_context()` + `context_block()` before their Claude call (CoachBrainError → graceful fallback to `context_str=""`).
- `telegram_bot.py`: Added `_get_bot_context_str(user)` helper (calls `bot_json_context_block`); wired into `_finish_checkin`, `handle_plan_days_callback` (via `_generate_plan`/`_generate_plan_from_profile`), `_chat_with_coach` (via `handle_message`), `cmd_weakpoints`, `cmd_report`.
**Rationale**: All AI calls were receiving minimal or no context. The dead `build_rich_context()` proved the data existed but was never wired up. This activates it across every AI-calling code path.
**Rollback**: Revert `context_str` params from the 5 `claude_service` functions; revert imports + context wiring in `main.py` and `telegram_bot.py`; restore `build_rich_context()` in `claude_service.py`; revert `prompt_builder.py` and `coach_brain.py` to Phase 3 stubs.

## 2026-05-26

### R2 — Delete dead `_get_today_exercises` function
**Files**: `telegram_bot.py`
**Rationale**: Function was superseded by `_get_session_exercises` during Phase 1 refactor (AUDIT §7g).
Grep confirmed zero call sites. Pure deletion, no behavior change.
**Rollback**: Restore `_get_today_exercises` definition at approximately line 285.

### R3 — Atomic `_save_store()` write
**Files**: `telegram_bot.py`
**Rationale**: `Path.write_text()` is not crash-safe; a mid-write failure corrupts `bot_state.json`
and destroys all user data (AUDIT §7d). Now writes to `.tmp` file then `os.replace()` (POSIX atomic rename).
**Rollback**: Revert `_save_store` body to the single `write_text` call.

### R4 — Add missing compound DB indexes
**Files**: `main.py`
**Rationale**: All existing indexes were single-column. Five common multi-column query patterns
(checkin by user+date, set logs by exercise+time, meals by user+date, measurements by user+date,
PRs by user+exercise+1RM) had no compound index, causing full-table scans (ARCHITECTURE §4c).
Added via idempotent `CREATE INDEX IF NOT EXISTS` in `_migrate_db()`.
**Rollback**: Remove the five `CREATE INDEX` statements from `_migrate_db()`; drop indexes manually
if needed (`DROP INDEX IF EXISTS ix_...`).

### R5 — Add error logging to silent `except Exception:` blocks
**Files**: `telegram_bot.py`
**Rationale**: Four bare `except Exception: pass/continue` blocks made production failures invisible
(AUDIT §7d). Added `print(f"Warning: ...")` before each pass/continue to match existing convention.
**Rollback**: Revert the four `except Exception as e:` lines back to `except Exception:` and remove the print calls.

### R6 — Create `prompt_builder.py` skeleton
**Files**: `prompt_builder.py` (new)
**Rationale**: Establishes module boundary per ARCHITECTURE §6a. All stubs raise `NotImplementedError`.
Zero impact on existing code. Phase 4 will migrate prompt construction here.
**Rollback**: Delete `prompt_builder.py`.

### R7 — Create `coach_brain.py` skeleton
**Files**: `coach_brain.py` (new)
**Rationale**: Establishes orchestration layer per ARCHITECTURE §1. Typed frozen dataclasses for all
snapshots; stub async domain methods raise `NotImplementedError`. Zero impact on existing code.
**Rollback**: Delete `coach_brain.py`.

### R8 — Consolidate duplicate model constants
**Files**: `telegram_bot.py`, `claude_service.py`
**Rationale**: `telegram_bot.py` had local copies of `ANALYSIS_MODEL` and `SUMMARY_MODEL` that
duplicated `claude_service.py`'s definitions (AUDIT §7c). Removed the local copies and added
`from claude_service import ANALYSIS_MODEL, SUMMARY_MODEL` at the top level. `CHAT_MODEL` stays
local as it is bot-specific.
**Rollback**: Remove the import and restore the two constant definitions in `telegram_bot.py`.

### R9 — Centralize Epley 1RM formula
**Files**: `claude_service.py`, `telegram_bot.py`, `main.py`
**Rationale**: `_epley_1rm()` was defined privately in `telegram_bot.py` and duplicated inline
in `main.py` (AUDIT §7c). Moved to `claude_service.epley_1rm()` as a public function; both callers
now import it. Single implementation, single test point.
**Rollback**: Restore `_epley_1rm` private def in `telegram_bot.py`; restore inline formula in `main.py`;
remove `epley_1rm` from `claude_service.py` and its import in `main.py`.

### R10 — Consolidate duplicate Anthropic client singletons
**Files**: `claude_service.py`, `telegram_bot.py`
**Rationale**: Both modules maintained their own lazy `anthropic.Anthropic()` singleton, creating
two client objects per process (AUDIT §7c). Exposed `get_anthropic_client()` as a public function
from `claude_service.py` (aliasing the existing `_client`). `telegram_bot.py` now imports and uses
the shared client; its local `_anthropic_client` singleton and `claude()` function are removed.
Removed the now-unused `import anthropic` from `telegram_bot.py`.
**Rollback**: Restore `_anthropic_client` singleton and `claude()` function in `telegram_bot.py`;
restore `import anthropic`; rename `get_anthropic_client` back to `_client` in `claude_service.py`.

### R1 — Fix hardcoded model strings in claude_service.py
**Files**: `claude_service.py`
**Rationale**: `generate_weekly_report()` (line 565) and `analyze_weak_points()` (line 603) hardcoded
`"claude-sonnet-4-6"` directly, bypassing module constants and making cost-control changes ineffective
for those functions (AUDIT §2, ARCHITECTURE §6d).
**Change**: Added `REPORT_MODEL = "claude-opus-4-7"` and `WEAK_POINT_MODEL = "claude-haiku-4-5-20251001"`
constants; replaced the two inline strings with the constants. Effective model values unchanged.
**Rollback**: Revert the two `model=` lines in `generate_weekly_report` and `analyze_weak_points`
back to `"claude-sonnet-4-6"`.
