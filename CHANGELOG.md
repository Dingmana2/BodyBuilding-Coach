# CHANGELOG

## 2026-05-27

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
