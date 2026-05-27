# CHANGELOG

## 2026-05-27

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
