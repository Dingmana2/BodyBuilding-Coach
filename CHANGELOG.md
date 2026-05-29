# CHANGELOG

## 2026-05-29 — Sprint 11: Unit fix, workout button UX, analysis readability, open-beta QA

### Fixed
- `telegram_bot.py` — `_build_plan_prompt`: added `user_units` param; converts stored kg/cm to lbs/ft-in for lbs users before injecting into the AI prompt; adds explicit UNITS instruction so Claude uses the correct unit throughout the plan (was always defaulting to metric regardless of user setting)
- `telegram_bot.py` — `_send_plan`: removed `reply_markup=day_keyboard` from the Workout message; day-picker buttons now appear as a single trailing "🏋️ Ready to train?" message after all four plan sections are sent
- `telegram_bot.py` — `_format_analysis`: replaced flat indented muscle list with section-separated layout — `——` dividers, bold per-muscle headers, `→ _action_` style, `•` bullet lists for strengths/priorities, `📐`/`💬` emoji labels for footer fields
- `telegram_bot.py` — all 9 call sites of `_generate_plan` / `_generate_plan_from_profile` updated to pass `user.get("units", "kg")`; one site also fixed `asyncio.get_event_loop()` → `asyncio.get_running_loop()`

### Added
- `tests/beta_simulation.py` — self-contained 60+ archetype open-beta simulation; validates unit display, analysis formatting, Markdown escaping, plan JSON shape, and source-level structural checks; 69/69 pass

### Rollback
- `git revert HEAD` — no schema changes, no migrations

## 2026-05-29 — Replace subjective body-part scores with objective per-muscle tracking

### Changed
- `claude_service.py` — removed `score: 1-10` and `overall_physique_score` from physique analysis JSON schema; prompt now requests only qualitative `notes` + `action` per muscle
- `telegram_bot.py` — `_format_analysis`: removed `X/10` display; muscle lines now show coaching notes + exercise prescription only; photo check-in history in `/progress` shows body fat trend, not scores; `/progress` adds "💪 Muscle Progress" inline button
- `telegram_bot.py` — new module-level `_MUSCLE_MAP` (shared) and `_MUSCLE_MEASURE` constants; new helpers: `_volume_by_muscle()`, `_muscle_strength_trend()`, `_muscle_measurement_trend()`, `_build_muscle_progress_text()`; new `handle_progress_callback()` for `progress:muscles` button
- `telegram_bot.py` — `cmd_stats`: now uses shared `_volume_by_muscle()` instead of duplicated inline logic
- `telegram_bot.py` — `_build_plan_prompt`: removed `Physique score: X/10` injection; kept qualitative coaching notes
- `prompt_builder.py` — `bot_json_context_block()`: added 30-day volume-by-muscle distribution to AI context (e.g. "Chest: 14s | Back: 22s | Legs: 8s") so plan generation can detect imbalances without photo scores

### Added
- `/progress` → "💪 Muscle Progress" button: shows tape measurement deltas (arm/chest/thigh cm over 8 weeks), strength trend per muscle group (best est. 1RM early vs late half of window), and volume distribution bar chart — all from logged set data and measurements with no AI subjectivity

### Rollback
- `git revert HEAD` — no schema changes, no migrations

## 2026-05-29 (Sprint 10 — Open Beta Bug-Fix Sprint: 21 bugs fixed)

### Pre-existing SyntaxError + 20 logic/UX bugs from open beta simulation fixed

**Files:** `telegram_bot.py`, `crypto_utils.py`, `.env.example`

**Critical fixes:**
- **Pre-existing SyntaxError**: `_parse_height` (line 201) contained Unicode curly apostrophes as Python string delimiters — bot would crash on Python 3.11+ at import time. Replaced with proper ASCII-only parsing loop using `for _ch in (…)`.
- **BUG-01**: `handle_checkin_callback` — KeyError crash when user taps old checkin button after session expires. Added `_REQUIRED_CHECKIN_KEYS` guard before `_finish_checkin`; switched `_finish_checkin` to `.get()` with defaults.
- **BUG-02**: `goals_input` branch — custom goal text silently discarded (success message sent without saving). Fixed: custom text now saved as `active_goal["custom_description"]`.
- **BUG-03**: `/delete_my_data` — only wiped `bot_state.json`; Garmin cache entry and user photo files persisted. Added: Garmin cache pop + `DATA_DIR/{chat_id}_*` file deletion.

**High-priority fixes:**
- **BUG-04**: `_parse_height` regex `[''ft]` only matched a single character from that set, so `"5ft10"` never parsed. Replaced with `(?:'|ft)` alternation group (now working for all formats).
- **BUG-05**: Plan regen via AI coach chat didn't pass `ctx_str` — regenerated plans were context-blind (no check-in history, goals, or streak). Fixed with `regen_ctx = _get_bot_context_str(user)`.
- **BUG-06**: `/privacy` falsely claimed "does not retain message text beyond the active session." Corrected to accurately describe conversation history persistence.
- **BUG-07**: `_build_plan_prompt` read `profile.get("show_date")` — a field never written. Now mirrored: when a target_date is saved in goals, `profile["show_date"]` is set too.
- **BUG-08**: `_weekly_stall_check` hardcoded "kg" unit string. Now uses `_wfmt(value, user)` for both displayed weights.

**Medium fixes:**
- **BUG-09**: Profile menu keyboard showed "cm" and "kg" for height/weight buttons regardless of user unit preference. Now uses `_units` conditional labels (`in`/`lbs` for imperial users).
- **BUG-10**: Measurements menu keyboard hardcoded "kg"/"cm". Now unit-aware with display conversion for imperial users.
- **BUG-11**: `/goals` display hardcoded "kg" for target weight. Now uses `_wfmt()`.
- **BUG-13**: AI-generated text in `_format_analysis` and `_send_plan` not escaped with `esc()`. Underscores in coaching notes, actions, symmetry_notes, rationale, coach_message now properly escaped.
- **BUG-14**: `/weakpoints` with sets but no photo gave raw internal error string. Added friendly "upload a photo first" guard.
- **BUG-15**: `crypto_utils.py` silently generated throwaway Fernet key if `ENCRYPTION_KEY` env var unset — all stored Garmin credentials unreadable after restart. Now raises `RuntimeError`. Added `ENCRYPTION_KEY` to `.env.example`.
- **BUG-16**: `_analyze_photo` JSON decode not wrapped in try/except — non-JSON AI responses silently crashed with no user feedback. Now raises descriptive `ValueError`.

**Low-priority fixes:**
- **BUG-17**: All `asyncio.get_event_loop().run_in_executor()` and `loop = asyncio.get_event_loop()` replaced with `get_running_loop()` (deprecated in Python 3.10+ inside async context).
- **BUG-18/19**: AI coach reply and `/weakpoints` output had no Telegram 4096-char limit guard. Added `_send_long()` helper that chunks at newline boundaries.
- **BUG-20**: Text sent during button-based onboarding steps was silently routed to AI coach. Now shows friendly "please tap a button" message.
- **BUG-21**: `_parse_weight` returned `str`, causing `TypeError` in arithmetic (e.g. Epley 1RM). Now returns `float`. Added `try/except` at all call sites.

**Rollback:** Revert all changes to `telegram_bot.py` (this commit), and revert `crypto_utils.py` to re-add the silent-key-generation fallback.

---

## 2026-05-29 (Sprint 9 — US vs Imperial Unit Selection in Onboarding)

### Unit system choice added to `/start` onboarding flow

**Files:** `telegram_bot.py`

**What changed:**
- Added a new `step == "units"` branch in `handle_onboard_callback` between the "days" step and the "gender" step
- After selecting training days, users now see a two-button picker: 🇺🇸 Imperial (lbs / ft-in) or 🌍 Metric (kg / cm)
- Selection saves `user["units"] = "lbs"` or `"kg"` immediately via `_save_store()`
- `step == "gender"` now shows unit-appropriate example: `23 / 5'7" / 155lbs` for imperial, `23 / 178cm / 82kg` for metric
- Existing `_wu(user)` and `_wfmt(value, user)` helpers already propagate this setting through plan generation, stats display, and all coaching output

**Rollback:** Revert the `step == "days"` branch to go directly to gender selection; remove the `step == "units"` branch; revert the example logic in `step == "gender"`.

---

## 2026-05-29 (Sprint 8 — Telegram Bot 50-User Beta Fix Sprint)

### 27-bug audit: 4 critical crashes fixed, 7 high-severity flows fixed, 10 medium UX issues fixed

**Files:** `telegram_bot.py` only

**What changed:**

**CRITICAL fixes:**
- `/weakpoints` crashed 100% of the time: `asyncio.run_in_executor` doesn't exist at module level — changed to `asyncio.get_event_loop().run_in_executor` (one-line fix restoring the entire command)
- Duplicate check-in corrupted streaks and recovery data: replaced `append` with a same-day overwrite — old entry is removed before the new one is stored
- Chat-triggered plan regeneration froze the bot for 30-60 seconds for ALL users: `_generate_plan` / `_generate_plan_from_profile` now run in `run_in_executor` in the `handle_message` regen branch
- Profile `days` field set to emoji/free-text (e.g. "🏋️ 5 days") crashed plan generation with `ValueError: invalid literal for int()`: both `_generate_plan` and `_generate_plan_from_profile` now strip non-numeric chars before `int()` with a safe fallback to 4; default days unified to 4 across both functions

**HIGH fixes:**
- `_parse_height`: added `5ft8`, `5ft10in`, `5feet8` regex branches — previously stored garbage heights for the most natural US format
- `/fridge` dead-end: typing text while `awaiting_fridge_photo` silently fell to the AI coach and stuck the user forever — now returns a clear photo-prompt with cancel option
- `/stats` 30-day volume histogram was wrong for all days 1-29 of the month: `replace(day=max(1,day-30))` replaced with `timedelta(days=30)`
- `/research creatine timing` ignored "creatine timing" entirely — user query now passed as `topic` to `_fetch_research_summaries`; function signature updated
- `handle_plan_days_callback` had no cooldown — could bypass the 5-minute plan spam limit by re-tapping the day picker; cooldown check added before `query.answer()`
- `/logset` accepted weight=0 or negative silently corrupting PRs; validation added with clear error messages; improved error hint includes an example
- `/workout` cleared `active_command` unconditionally — silently killed in-progress checkin/profile flows; now preserves `prev_active` context

**MEDIUM fixes:**
- `cmd_checkin`: added same-day duplicate warning at top with inline-format hint for updating
- `dietary_restrictions` profile field had no input prompt — showed raw field name with underscores; added friendly prompt with examples
- `/link` told users to type `/link-status` (hyphen) — actual command is `/link_status` (underscore); fixed
- `_chat_with_coach` return type annotation was wrong (2-tuple vs actual 3-tuple)
- Fallback recovery score ignored `joint_pain` and `motivation` — formula updated to include all 6 dimensions
- `/peakweek` error only mentioned `prep` goal; updated to mention both `prep` and `cut`
- Goal target date in the past was silently accepted — now validates and rejects with clear message
- Inline `/checkin` only accepted 4 fields, silently defaulting `joint_pain=10` and `motivation=7`; optional fields now forwarded if provided
- `/help` was missing `/link`, `/link_status`, `/billing`, `/freeze`, `/peakweek` — all added

**Rollback:** Revert `telegram_bot.py` to `0ff9ef0`. No schema or external-service changes.

---

## 2026-05-29 (Sprint 7 — Remaining Backlog Features)

### Remove subscription tiers, billing UI, and roll out common-theme beta fixes

**Files**: `main.py`, `static/index.html`, `static/app.js`, `static/style.css`, `telegram_bot.py`

**What changed:**

**Tier / billing removal:**
- `main.py`: Deleted `_get_user_tier()`, removed free-tier gates from `/api/analyze`, `/api/analysis/weak-points`, `/api/reports/generate`. Deleted `/api/subscription` GET and `/api/subscription/upgrade` POST endpoints entirely. Removed `subscription_tier` from `/api/auth/me` and the internal Telegram-user lookup response.
- `static/index.html`: Removed "Billing" nav tab and entire billing section (pricing cards, tier badges). Updated Reports empty-state text. Updated Reports subtitle (removed "Pro+").
- `static/app.js`: Removed `loadBilling()` and `upgradeTier()` functions. Removed `billing` from tab loader map.
- `static/style.css`: Removed all `.billing-tier-badge`, `.pricing-grid`, `.pricing-card`, `.pricing-name`, `.pricing-price`, `.pricing-features` rules.
- `telegram_bot.py`: Replaced `cmd_billing()` with brief "coming soon as partnership" message. Removed `subscription_tier` display from `/link` and `/link-status` outputs. Updated billing command description in bot command list.

**Beta-fix rollout (common themes):**
- **Button double-tap prevention**: `submitAnalysis()` and `logSet()` both disable their button (with interim label) on click and re-enable in `finally` block — prevents double submissions.
- **Profile form improvements**: Added `strength`, `health`, `contest_prep` goal options; added `non-binary` gender; added `Injuries or Physical Limitations` field; removed "(Elite — comp prep only)" label from show date.
- **Accessibility**: `#drop-zone` now has `role="button"`, `tabindex="0"`, `aria-label`, and Enter/Space key handler. `#toast` has `role="alert"` and `aria-live="assertive"`. All check-in range sliders have `aria-label`. Photo input has `capture="environment"` for direct camera on mobile.
- **Text contrast**: `.text-muted` changed from `#888` to `#999` (passes WCAG AA 4.5:1 on `#0d0d0d` background).
- **Mobile touch targets**: `.stepper-btn` now has `min-width: 48px; min-height: 48px` per WCAG 2.5.5.
- **iOS overscroll**: Added `html, body { overscroll-behavior: none; }` to prevent white flash on pull-to-refresh.

**Rationale:** User explicitly requested tier removal toward a partner product model. Beta testing identified button double-tap bugs, accessibility gaps, and profile form limitations as common failure patterns across all 50 simulated user archetypes.

**Rollback:** Revert all six files. No DB schema changes — `subscription_tier` column remains in the `users` table and can be re-read without migration.

---

## 2026-05-29 (Sprint 5 — One Brain: Photo + Profile + Plan Integration)

### Generate button fix, guided onboarding, and unified plan generation

**Files**: `telegram_bot.py`

**What changed:**
- **Generate button fix**: Both `prof:generate` and `handle_plan_days_callback` now run the Claude API call in a thread executor (`run_in_executor`) — previously the synchronous API call blocked the entire async event loop for 30-60 seconds, making the button appear frozen. Also adds plan cooldown check to `prof:generate`.
- **Photo flow — one brain**: After a physique analysis, if the user already has `days` set in their profile, the bot immediately generates the plan automatically (using both the photo analysis AND profile data together). No more re-asking for days they already provided. If a cooldown is active, shows a single generate button instead.
- **Guided onboarding**: After selecting training days, the bot no longer dumps a full 8-button profile keyboard. Instead it walks through two more guided steps: gender (tap buttons) → age/height/weight (one text prompt with skip option). Then shows the generate button. The full profile keyboard remains accessible via /profile for edits.
- **`_auto_plan_after_analysis` helper**: New shared function called by both single-photo and album-photo handlers so the logic is DRY.
- **Plan confirmation message**: Updated to explicitly say "built from your profile + photo analysis" so users understand the systems are connected.
- **`onboard_text` active_command**: New handler in `handle_message` that parses a loose stats string (e.g. `28 / 178cm / 82kg`) and saves age, height, weight to profile before showing the generate button.

**Rationale:** User reported: photo upload re-asked for days already provided; generate button appeared unresponsive; onboarding dumped a form instead of guiding; photo analysis and plan felt like separate features.

**Rollback:** Revert `_auto_plan_after_analysis`, the `onboard:gender/skip` branches in `handle_onboard_callback`, the `onboard_text` branch in `handle_message`, and restore the synchronous `_generate_plan` calls.

---

## 2026-05-28 (Sprint 4 — Full UX Bug-Fix Sprint)

### 8-bug audit fix: silent failures, dead ends, and stale state

**Files**: `telegram_bot.py`

**What changed:**
- **Bug 1 (critical)**: All profile summary strings now escape values with `esc()` — users with underscores in injury fields (e.g. `bad_left_knee`) or apostrophes in height (e.g. `5'10"`) no longer silently break `edit_message_text`.
- **Bug 2 (high)**: "🚀 Generate my plan" button is now permanently the last row of `_profile_menu_keyboard` — it never disappears after a profile edit. The onboarding `days` branch now uses `_profile_menu_keyboard` directly (no more tuple-concatenation fragility).
- **Bug 3 (high)**: `cmd_plan`, `cmd_checkin`, `cmd_workout`, `cmd_meal`, `cmd_fridge` each clear `active_command` and `command_state` on entry — typing a new command while mid-flow in `profile_input` / `goals_input` / etc. no longer leaves state behind to intercept the next free-text message.
- **Bug 4**: Already resolved in previous sprint (`query.answer()` was already in `handle_plan_days_callback`).
- **Bug 5 (medium)**: `query.delete_message()` in `prof:generate`, `handle_plan_days_callback`, and `handle_checkin_callback` is now wrapped in `try/except Exception: pass` — double-tapping a generate or submit button no longer silently kills the flow.
- **Bug 6 (medium)**: `goals_input` handler now tracks `parsed_ok`; if the user's input can't be parsed (wrong date format, non-numeric weight, etc.) it replies with an error + keyboard and returns without clearing state.
- **Bug 7 (medium)**: `goals:custom` callback now includes `reply_markup=_goals_menu_keyboard(user)` so users aren't left with a dead-end text message and no buttons.
- **Bug 8 (low)**: `_HIDDEN_PROFILE_KEYS = frozenset({"goal_set_date"})` filters internal keys from all profile summary displays.

**Rationale:** End-to-end UX audit surfaced 8 bugs causing silent API failures, dead-end states, and stale active_command routing.

**Rollback:** Revert the above edits. Key identifiers: `_HIDDEN_PROFILE_KEYS`, `parsed_ok`, `try: await query.delete_message()`.

---

## 2026-05-28 (Sprint 3 — Inline Keyboard Button UIs)

### Button-based entry for /profile, /goals, /measurements

**Files**: `telegram_bot.py`

**What changed:**
- `/profile` (no args) now shows an inline keyboard with every field as a button displaying its current value. Fields with a fixed option set (goal, experience, days, gender, chronotype, physique_analysis) open a sub-menu of tap-able values. Free-text fields (age, height, weight, injuries, email) prompt the user to type. A "Type custom (field=value)" button preserves the old power-user syntax.
- `/goals` (no args) now shows an inline keyboard with goal-type buttons (Bulk / Cut / Recomp / Strength / Prep / Health) and separate buttons to enter a target weight, body fat %, or target date. Tapping a goal type immediately activates that goal and syncs `profile.goal`. Each target button prompts for text input.
- `/measurements` (no args) now shows an inline keyboard with each body-part measurement as a button showing the last logged value (or "—"). Tapping a button prompts for that specific measurement. A "Type multiple fields at once" button preserves batch syntax.
- Three new `handle_*_callback` functions route `prof:`, `goals:`, `meas:` callback prefixes.
- Three new helper functions: `_profile_menu_keyboard`, `_goals_menu_keyboard`, `_measurements_menu_keyboard`.
- `handle_message` gains three new `active_command` branches: `profile_input`, `goals_input`, `measurements_input`.
- Three new `CallbackQueryHandler` registrations in `main()`.

**Rationale:** Users should not need to memorise `field=value` syntax for routine updates. Buttons lower friction for all skill levels while the "Type custom" fallback preserves power-user flexibility.

**Rollback:** Revert the three `cmd_*` no-args blocks to their previous text-only replies and remove the three new handler functions and their `CallbackQueryHandler` registrations.

---

## 2026-05-28 (Sprint 2 — Tiers 2–5 Full Rollout)

### Items 11–36 from expert panel backlog

**Files**: `telegram_bot.py`, `claude_service.py`

**Tier 2 — Coaching quality:**
- **Warm-up protocols** — every `/plan` day now includes a `warmup` field; displayed in plan output
- **Zone 2 cardio** — `zone2_cardio` added to all generated plans and displayed in workout section
- **Training-day vs rest-day macros** — `training_day_macros` / `rest_day_macros` fields in plan diet; shown in `/plan` output
- **Beta-alanine** — added as priority 4 supplement (Grade B) in all generated plans
- **AI confidence note** — coaching section of every plan ends with "AI-generated — adjust based on how your body responds"
- **Motivation score** — 6th step added to `/checkin` ("Motivation to train 1-10"); stored as `motivation_score`; low-motivation alert when ≤ 3
- **Body image sensitivity toggle** — `/profile physique_analysis=off` to opt out of photo analysis; angle suggestion shown on first single-photo submission

**Tier 3 — Coaching depth:**
- **RPE/RIR in set logs** — `/logset bench 100kg 8 rpe=8` now accepted; stored in set log entry
- **Weight-drop alert** — when logged weight ≥10% below previous for same exercise, bot shows a warning note
- **Phase transition warning** — weekly `/report` flags if athlete has been on same goal ≥10 weeks
- **`goal_set_date`** — stored in profile whenever goal changes; used for phase transition check
- **Micronutrient reminder** — `/report` adds refeed reminder when cut/recomp user has been losing weight for 3+ weeks
- **Sleep chronotype field** — `/profile chronotype=morning` accepted; documented in profile help
- **Food logging confidence** — `/meal` now shows 🟢/🟡/🔴 AI confidence level on estimated macros
- **HRV methodology note** — check-in output clarifies "7-day rolling average (RMSSD)" when HRV present
- **Wearable accuracy disclaimer** — appended to check-in when Garmin data used

**Tier 4 — UX:**
- **`/start` onboarding quiz** — new users get a 3-step inline keyboard: goal → experience → training days; returning users see quick-stats summary
- **`handle_onboard_callback`** — handles `onboard:goal:X`, `onboard:exp:X`, `onboard:days:X`
- **Contextual hints** — first set log shows /stats tip; first check-in shows /report tip
- **Physique scores with action** — `_analyze_photo` prompt now requests an `action` field per muscle; displayed in analysis output
- **Explicit angle prompt** — on first single-photo physique submission, bot suggests "send front + back + side as album"
- **`/help` redesign** — `cmd_help` uses new `_HELP_TEXT` with 5 categorised sections (Setup / Logging / Progress / Settings / Privacy)

**Tier 5 — Architecture / features:**
- **Write lock on `_save_store()`** — `threading.Lock` prevents concurrent write corruption
- **Longevity Score** — `/progress` now shows a 0-100 composite metric (HRV, RHR, sleep, steps, VO2max) when Garmin data is available
- **Contest Prep goal** — `prep` added as a valid goal in profile help text; gates `/peakweek`
- **`/peakweek` command** — generates a 7-day contest peak week protocol (water taper, sodium, carb load, posing schedule); only available for `prep`/`cut` goal users
- **Email + chronotype fields** — documented in `/profile` help; stored as arbitrary profile keys
- **`crypto_utils.py`** — already uses separate `ENCRYPTION_KEY` env var (not derived from bot token) — no change needed

**Rollback**: Revert `_STEP_LABELS` (remove motivation); remove `handle_onboard_callback` and new command handlers; revert `_build_plan_prompt`, `_send_plan`, `_analyze_photo`, `cmd_report`, `cmd_meal`; remove `/peakweek`, `/help` redesign, longevity score from `/progress`; revert `_save_store` lock.

## 2026-05-28 (Sprint 1 — Safety & Quick Wins)

### Items 1–10 from expert panel backlog

**Files**: `telegram_bot.py`, `garmin_service.py`

**What**:
- **`/privacy`** — new command explaining what data is stored, Telegram retention, Garmin/MFP/Anthropic usage, and user rights (export / delete).
- **`/delete_my_data`** — permanently wipes user's record from bot_state.json after inline confirmation button; `handle_delete_callback` processes `del:confirm` / `del:cancel`.
- **`/export`** — sends full user history (profile, set_logs, checkins, meal_logs, PRs, measurements) as a JSON file attachment; encrypted credential fields are stripped before export.
- **`/freeze`** — streak freeze command; 1 per 30 days; adds today's date to `user["streak_freezes"]`; streak calculation in both `_finish_checkin` and `cmd_streak` now unions checkin dates + freeze dates.
- **Physique analysis disclaimer** — `_format_analysis()` appends "_⚠️ AI estimate only — body fat ±5%, scores are relative. Not a medical assessment._"
- **Eating disorder calorie warning** — `_send_plan()` checks generated calorie target against gender-aware threshold (1200 kcal women / 1500 kcal men); adds visible safety note if below threshold.
- **Injury/limitations field** — `/profile injuries=...` injected into `_build_plan_prompt()` as a CRITICAL contraindication clause; `/profile` help text updated with example.
- **Soreness vs joint pain split** — `_STEP_LABELS` now has 5 steps: sleep, energy, soreness (relabelled "Muscle soreness (DOMS)"), joint_pain (🦴, "1=painful, 10=pain-free"), stress. `joint_pain_score` stored in checkin entry. `_finish_checkin` shows red-flag alert if joint_pain ≤ 3. Garmin auto-fill defaults `joint_pain=10`.
- **Sleep hygiene tip** — `_finish_checkin` appends a sleep hygiene tip when `sleep_score ≤ 5`.
- **Garmin VO2 max** — `garmin_service.py` adds `get_max_metrics()` call; result stored as `vo2_max` in cache; surfaced in `_garmin_review_lines()` as "VO2max XX".

**Rollback**: Revert `_STEP_LABELS` to 4 keys; remove new commands from `main()` handler registrations and `set_my_commands`; revert `_format_analysis`, `_send_plan`, `_build_plan_prompt`, `_finish_checkin`, `cmd_streak`; remove `vo2_max` block from `garmin_service.py`.

## 2026-05-28

### Feature: /research now pulls community insights from fitness subreddits
**Files**: `telegram_bot.py`, `community_posts.md`

**What**: `/research` now shows two sections — "📚 Latest Research" (PubMed, existing) and "💬 Community Insights" (new). The Reddit section pulls hot posts from 8 fitness subreddits (r/bodybuilding, r/naturalbodybuilding, r/nutrition, r/fitness, r/longevity, r/Supplements, r/powerlifting, r/weightlifting) via Reddit's public JSON API (no auth needed). Posts filtered by score (≥30) and sticky status, then summarised into coach-relevant insights using Haiku. Both fetches run concurrently via `asyncio.gather`. New helpers: `REDDIT_SUBREDDITS`, `_fetch_reddit_posts`, `_summarize_reddit`, `_fetch_reddit_summaries`. Rate-limit: 2s sleep between subreddit calls. `community_posts.md` updated to remove Reddit post templates (Reddit used as knowledge source, not posting channel) — trimmed to 4 platforms.

**Rollback**: Revert `cmd_research` to the PubMed-only version; remove new Reddit helpers.

### Improvement: /fridge scan shows ingredients grouped by category
**Files**: `telegram_bot.py`

**What**: The ingredient review message now groups detected items by food category instead of a flat comma-separated blob. `_fridge_scan_call` prompt changed to ask Claude to return categorized JSON (`proteins`, `grains`, `vegetables`, `dairy`, `fats`, `fruits`, `other`) instead of a flat array; return type changed from `list[str]` to `dict[str, list[str]]` with max_tokens bumped from 400 to 500. `_handle_fridge_photo` stores both the categorized dict (`ingredients_by_cat`) and a flat list (`ingredients`) in `command_state` — the flat list is used for recipe generation, the dict for display. `_fridge_review_text` updated to show items under their category emoji labels (e.g. "🥩 Proteins: eggs, chicken breast") with empty categories skipped and unrecognised items under "🗂 Other". All call sites updated.

**Rollback**: Revert `telegram_bot.py` to previous commit.

### Feature: /fridge ingredient review UI — add missed items before recipes generate
**Files**: `telegram_bot.py`

**What**: Fridge photo scan now uses a two-step flow. Step 1: photo is scanned for ingredients only (new `_fridge_scan_call`, vision-only, max_tokens=400, timeout=60s). Step 2: an inline keyboard "review" message shows the detected ingredient list, six food category buttons (Proteins, Grains & Carbs, Vegetables, Dairy, Fats, Fruits), and a "Generate Recipes →" button. Each category expands to show common items; tapping an item toggles it (✓ marks already-added items). The user can also type missed items as comma-separated text — they are parsed, deduplicated, and added to the list. Tapping "Generate Recipes" calls `_fridge_recipes_call` (text-only, no image, timeout=90s) with the combined ingredient list, then renders recipes via `_fridge_send_recipes`. State is tracked in `user["command_state"]` (`ingredients`, `added`, `review_msg_id`) with `active_command = "fridge_reviewing"`. New helpers: `_FRIDGE_CATEGORIES`, `_fridge_review_text`, `_fridge_category_keyboard`, `_fridge_items_keyboard`, `handle_fridge_callback`. Text routing added to `handle_message` for the `fridge_reviewing` state.

**Rollback**: Revert `telegram_bot.py` — remove the new helpers and callback, and restore the old single-call `_fridge_api_call` + `_handle_fridge_photo`.

### Feature: Gut health awareness in nutrition coaching
**Files**: `prompt_builder.py`, `telegram_bot.py`, `claude_service.py`

**What**: The nutrition coaching persona, plan generation, and plan template now include gut health awareness. `_DEFAULT_PROMPT` in `prompt_builder.py` (used for all coaching conversations) now instructs the model to recommend 30+ diverse plant varieties for microbiome diversity, fermented probiotic foods (Greek yogurt, kefir, kimchi, sauerkraut), complex carbohydrates and resistant starch over refined carbs, and omega-3 sources for anti-inflammatory recovery. It also notes the gut-brain axis (stress impairs digestion → affects training). In `telegram_bot.py`, `_build_plan_prompt` adds gut health instructions: fermented foods in `foods_to_prioritize`, prebiotic/fiber foods (garlic, onion, oats, legumes), and a `gut_health_note` field. In `claude_service.py`, the comprehensive plan JSON template adds fermented foods and legumes to `foods_to_prioritize` and adds the `gut_health_note` field to the diet object.

**Rollback**: Revert `_DEFAULT_PROMPT` in `prompt_builder.py`, revert gut health instruction block in `_build_plan_prompt`, revert diet object template in `claude_service.py`.

## 2026-05-27

### Bug fix: /fridge showed "Scanning…" forever — NameError on undefined `esc()` silently swallowed
**Files**: `telegram_bot.py`

**What**: After the API call returned successfully, the formatting code called `esc()` (a Markdown-escape helper) which was never defined or imported anywhere in the file. This raised a `NameError` that was not caught by any try/except, leaving the "Scanning your fridge…" message frozen forever with no error shown. Fix: (1) Added `esc(text: str) -> str` at module level (escapes `_`, `*`, `` ` ``, `[` for Telegram legacy Markdown). (2) Applied `esc()` to all dynamic Claude-generated fields in the recipe output (name, ingredients, prep, timing, why_it_fits). (3) Wrapped the entire formatting + `edit_text` call in a try/except so any future message-send failure degrades to a clean error instead of a frozen message.

**Rollback**: Remove the `esc` function definition and revert the formatting block in `_handle_fridge_photo`.

### Bug fix: /fridge blocked event loop for 10 min — wrapped Claude API call in run_in_executor with 90s timeout
**Files**: `telegram_bot.py`

**What**: `_handle_fridge_photo()` was calling `get_anthropic_client().messages.create()` (a blocking sync call) directly inside an `async` function, freezing the entire asyncio event loop for up to 600 seconds (the Anthropic SDK's default timeout). Extracted the API call into a new sync helper `_fridge_api_call(img_b64, prompt) -> dict` with an explicit `timeout=90.0`, and replaced the direct call with `await asyncio.get_event_loop().run_in_executor(None, _fridge_api_call, img_b64, prompt)`. The event loop is now free to handle other messages while the API call runs in a thread pool.

**Rollback**: Replace the `run_in_executor` call in `_handle_fridge_photo` with the original direct `messages.create()` call and remove `_fridge_api_call`.

### Feature: /fridge — scan fridge photo for macro-aligned recipe suggestions
**Files**: `telegram_bot.py`

**What**: New `/fridge` command puts the bot in `awaiting_fridge_photo` state and prompts the user to send a fridge/pantry photo. `handle_photo()` intercepts the next photo before the physique analysis path and routes it to `_handle_fridge_photo()`, which sends the image to Claude (vision) with the user's current diet plan (calories, protein, carbs, fat, foods_to_prioritize, foods_to_limit, meal_timing, goal) and asks for 3 recipes using only the visible ingredients. Each recipe includes: name, ingredient list, macros (calories/protein/carbs/fat), 2-sentence prep method, meal timing, and one sentence explaining why it fits the plan. Existing photo analysis flow is completely unaffected — only routes to fridge handler when the active command is set.

**Rollback**: Remove `cmd_fridge`, `_handle_fridge_photo`, the routing block in `handle_photo`, and the two handler registration lines.

### Feature: Fully automatic /checkin from Garmin — max data, zero manual questions
**Files**: `garmin_service.py`, `telegram_bot.py`

**What**: When Garmin is connected, `/checkin` now requires zero manual input. All 4 recovery scores are auto-derived from watch data: sleep from Garmin sleep score/duration (+ deep/REM stage bonus), energy from Body Battery (new) or HRV fallback, soreness from last-48h training load, stress from Garmin stress score. `garmin_service.fetch_and_cache()` expanded with 5 new API calls: Body Battery (`get_body_battery`), sleep stages extracted from existing sleep call (deep/REM/light mins), respiratory rate (`get_respiration_data`), SpO2 (`get_pulse_ox_data`), daily steps (`get_stats`). All new fields surfaced in `_garmin_review_lines()` output and injected into the AI recovery scoring context. Final check-in message shows "📲 Auto-filled from Garmin" instead of manual score line. If Garmin is connected but watch hasn't synced yet (all fields null), shows a helpful "sync your watch" message and falls back to manual questions.

**Rollback**: Revert `garmin_service.py` and `telegram_bot.py` to previous commits.

### Bug fix: /checkin dropped Garmin data when sleep score was missing; now pulls live
**Files**: `telegram_bot.py`

**What**: Two bugs caused the 📡 Garmin section to never appear. (1) `cmd_checkin` only entered the Garmin-aware path when `sleep_score_1_10` was present — if the cache had HRV/RHR/stress but no sleep score (common right after connecting, before the watch syncs), the code fell through to the normal 4-question flow and `garmin_data` was never stored in `command_state`, so `_finish_checkin` received `None`. Fix: relaxed the gate to any useful Garmin metric (`sleep_score_1_10 OR hrv_ms OR resting_hr_bpm OR stress_score_1_10`), and added `garmin_data` to the fallback command_state dict too. (2) `/checkin` only read from the stale 6am cache — fix: now calls `fetch_and_cache()` live (with typing indicator) at check-in time so the user always gets current watch data; falls back to the cache on network error.

**Rollback**: Revert `telegram_bot.py` to the previous commit. No DB or API changes.

### Feature: /checkin shows Garmin review and scores recovery from watch + workouts + nutrition
**Files**: `telegram_bot.py`

**What**: Enhanced `/checkin` to surface all available objective data and use it to drive the recovery score. Three new private helpers (`_workout_load_summary`, `_nutrition_today_summary`, `_garmin_review_lines`) aggregate 7-day training load from `set_logs`, today's macros from `meal_logs`, and Garmin watch metrics (sleep, HRV, RHR, stress). When Garmin is connected the opening message now shows a full "📡 Garmin review:" block with all metrics and the 7-day load line, rather than just "Sleep X/10". The closing output is restructured into named sections (📡 Garmin, 🏋️ Load, 🍽️ Nutrition) that appear only when data is present. The AI recovery scoring call receives the enriched context string (Garmin HRV/RHR/sleep + workout load + today's nutrition) so the score reflects objective wearable data, not just four subjective sliders. Non-Garmin users see no change — all new params default to empty. `_handle_checkin_step` and `handle_checkin_callback` updated to thread `workout_summary` and `nutrition_summary` through from `command_state`.

**Rollback**: Revert `telegram_bot.py` to the previous commit. No DB schema or external API changes.

### Bug fix: Album cooldown fired on every photo, blocking photos 2 and 3
**Files**: `telegram_bot.py`

**What**: In `handle_photo`, the `_check_cooldown` call was at the top of the function — before the `media_group_id` check. Photo 1 of a 3-photo album would pass and record the cooldown timestamp; photos 2 and 3 arrived milliseconds later, hit the now-active cooldown, sent "⏳ Please wait 59s…" error messages, and returned early without being buffered. The deferred task ran with only 1 photo. Fix: moved cooldown check inside the media group branch (runs only for the first photo of each album) and inside the single-photo path. Added `_blocked` key to silently drop subsequent photos when an album is rejected, preventing duplicate error messages. Added cleanup of `_blocked` key in `_process_media_group`.

**Rationale**: Confirmed bug from production screenshot — 3-photo album produced 2 cooldown errors and a "Front view only" analysis.

**Rollback**: Revert `telegram_bot.py` to prior commit.

### Multi-Photo Progress Check-In (Telegram + Web)
**Files**: `telegram_bot.py`, `claude_service.py`, `main.py`, `static/index.html`, `static/app.js`

**What**:
- **Telegram**: Sending an album (2-3 photos at once) is now handled as a single analysis. `handle_photo` detects `media_group_id`, buffers all photos in `context.bot_data` for 2 seconds, then `_process_media_group` downloads all images and calls `_analyze_photo` with the full list. Single-photo uploads are unchanged.
- **`_analyze_photo(images_b64: list[str], ...)`**: Signature changed from `str` to `list[str]`. Builds one Claude image content block per photo, passes all before the text prompt. Multi-photo prompt includes a note telling Claude to analyze them together as one check-in and note each angle. `max_tokens` raised 1500 → 2000 for multi-image depth.
- **`analyze_body_photo(image_paths, ...)`** (`claude_service.py`): Accepts `str` or `list[str]` (backward-compatible). Builds multiple image blocks for the Claude API call. Same multi-note injected into prompt when >1 photo.
- **`POST /api/analyze`** (`main.py`): Parameter changed from `file: UploadFile` to `files: list[UploadFile]`. Saves each file, calls `analyze_body_photo` with all paths. Returns `photo_url` (first, backward-compat) and `photo_urls` (all). Max 5 photos per request.
- **Web UI** (`index.html`): File input gains `multiple` attribute; drop zone text updated to mention "up to 3 angles".
- **Web UI** (`app.js`): `state.pendingFile` → `state.pendingFiles[]`. `previewFiles()` renders a thumbnail strip for all selected images. Submit button label is "Analyze 3 Photos" when multiple are selected. `submitAnalysis` appends each file under the `files` key.

**Rationale**: Users take 3 progress photos (front, back, side) per check-in. Previously each required a separate upload and a separate Claude call with no cross-angle awareness. Now all 3 are analyzed together in one call.

**Rollback**: Revert all 5 files to prior commit. No DB schema changes.

### Angle/Pose Detection for Photo Analysis
**Files**: `telegram_bot.py`, `claude_service.py`

**What**:
- `_analyze_photo()`: Claude now auto-detects the photo angle (`front`, `back`, `side_left`, `side_right`, `three_quarter`, `unknown`) and only scores muscles visible from that angle. The JSON schema gains `photo_angle` and `angle_notes` fields. Muscles not assessable from the current view are omitted from `muscle_development` entirely. Comparison context (when a prior analysis exists) references the previous angle and tells Claude whether to compare directly (same angle) or note new information (different angle).
- `_format_analysis()`: Shows an angle-specific emoji + label at the top of the response (🔵 Front, 🔴 Back, 🟡 Side, 🟢 Three-quarter). Skips any muscle group where `score` is `null` or missing.
- `cmd_progress()` photo history: Each entry now shows the angle in brackets, e.g. `2026-05-27 [back]: 16% BF | 7.1/10 ↗`.
- `analyze_weak_points()` (`claude_service.py`): Now aggregates muscle scores across ALL stored analyses (not just the latest). Each muscle gets its score from the most recent analysis where it was actually visible. `areas_to_improve` lists are also merged and deduplicated. This means a front photo + back photo together produce a complete physique picture.

**Rationale**: A back-view photo cannot assess chest development. This feature makes angle-awareness explicit so scores are accurate, comparisons are meaningful, and the coach can synthesize information from multiple viewing angles.

**Rollback**: Revert `telegram_bot.py` and `claude_service.py` to prior commit. Existing `photo_angle`-less entries in `user["analyses"]` still work — the display code treats missing `photo_angle` as empty string, which shows no label.

### Photo Progress History — accumulate analyses over time
**Files**: `telegram_bot.py`

**What**:
- `get_user()`: Added `"analyses": []` to the back-fill defaults so existing users get the new field on next startup.
- `handle_photo()`: Appends each new analysis (plus `"date"`) to `user["analyses"]` (capped at 20). `user["last_analysis"]` is still updated for backward compatibility with plan generation.
- `_analyze_photo()`: New optional `prev: dict | None` parameter. When a previous analysis is provided, the Claude prompt includes a comparison note so the coach message can describe visible progress or regression.
- `cmd_progress()`: Added a **Photo Analyses** section showing all stored entries with date, BF%, physique score, and ↗/↘ direction arrows.
- `cmd_weakpoints()`: Now passes `user["analyses"]` (all snapshots) to `analyze_weak_points()` instead of only wrapping the single `last_analysis`.

**Rationale**: Each new photo previously overwrote the previous analysis. Users could not track their physique progress over time via the bot. The fix accumulates all analyses in bot JSON state and threads them through every feature that benefits from longitudinal data.

**Rollback**: Revert `telegram_bot.py` to the prior commit. Existing `user["analyses"]` keys in `bot_state.json` are additive and harmless.

### Bot UX Fixes — Mobile-only user hardening
**Files**: `telegram_bot.py`

**What**:
- `_get_bot_context_str()`: log exception with `print(f"Warning: bot context build failed: {e}")` instead of silently swallowing it — coach no longer loses athlete context without any trace.
- `cmd_meal`: send `typing` chat action before the placeholder message so Telegram shows "typing…" during the 5-10 s AI/Nutritionix lookup.
- `cmd_weakpoints`: removed hard requirement for a prior physique photo. Now falls back to set-log + PRs data for training-imbalance analysis; only blocks if there is genuinely no data at all (no sets, no PRs, no analysis).
- `_chat_with_coach`: when `plan_update` or `plan_regen` JSON blocks are malformed, append an explicit user-facing warning instead of silently dropping the update. User now sees "_Plan change detected but couldn't be applied — type /plan to regenerate._"

**Rationale**: User is mobile/Telegram-only. Silent failures degraded every coaching interaction; these four fixes restore observable feedback for all failure modes.

**Rollback**: Revert `telegram_bot.py` to previous commit.



### Phase 5 — Final Test + Polish Pass
**Files**: `main.py`, `tests/conftest.py` (new), `tests/test_api.py` (new), `.env.example`, `README.md` (new), `RELEASE_NOTES.md` (new), `NEXT_STEPS.md` (new)

**What**:
- Removed unused `JSONResponse` import from `main.py` (identified via orphan code audit).
- Updated `.env.example` to cover all 7 env vars the code actually reads: `ANTHROPIC_API_KEY`, `SECRET_KEY`, `BOT_SECRET`, `TELEGRAM_BOT_TOKEN`, `API_BASE_URL`, `DATA_DIR`, `STRIPE_SECRET_KEY`.
- Created `tests/conftest.py` + `tests/test_api.py`: 38 automated tests covering auth flow, profile, check-in upsert, PUT edit, meals (manual + AI-estimated), measurements, goals (deactivation logic), coach memory, session lifecycle (start → log sets → PR detection → end → history), streaks, badges, dashboard summary, and subscription. All AI calls mocked — tests run offline.
- `python -m pytest tests/ -v` → **38/38 passed**.
- Created `README.md` — setup, env vars, test instructions, deployment guide, Telegram command reference.
- Created `RELEASE_NOTES.md` — user-facing summary of all v1.0.0 features, breaking changes (none), and known limitations.
- Created `NEXT_STEPS.md` — prioritised backlog (P0 security, P1 product gaps, P2 QoL, P3 architecture).

**Rationale**: Phase 5 final-polish pass per spec — test coverage, dead-code cleanup, env var audit, and ship-ready documentation.

**Rollback**: Delete `tests/` directory, revert `main.py` JSONResponse removal, revert `.env.example` to prior version, delete `README.md`, `RELEASE_NOTES.md`, `NEXT_STEPS.md`.

### Feature #17 — Badges & Streaks Detail in Profile
**Files**: `static/index.html`, `static/app.js`
**What**:
- Added Badges & Streaks card to the Profile tab (above Goals card).
- `renderBadges(badges)` renders earned badges as pill tiles (🏅 icon, badge type label, earned date) from `GET /api/badges`.
- `renderStreakDetail(streaks)` renders a table of all streak types (current, best, total days) from `GET /api/streaks`.
- `loadBadgesAndStreaks()` fetches both in parallel; called from `loadProfile()`.

**Rationale**: Badge and streak data existed in SQLite and were partially surfaced in the retention widget (count only). This exposes the full collection so users can see what they've earned and motivates streak maintenance.

**Rollback**: Remove Badges & Streaks card from Profile HTML; remove `renderBadges`, `renderStreakDetail`, `loadBadgesAndStreaks` from `app.js`; remove `loadBadgesAndStreaks()` call from `loadProfile`.

### Feature #16 — Coach Memory Web UI in Dashboard
**Files**: `static/index.html`, `static/app.js`
**What**:
- Added Coach Memory card to the Dashboard tab (below Latest Analysis card). Hidden when no memories exist.
- Shows up to 20 recent memories with type badge (PR/recovery/note/observation, color-coded), content, and timestamp.
- "Add Note" inline form lets users add custom notes (memory_type="note") via `POST /api/memory`.
- `loadMemory()` fetches `GET /api/memory`; called as a fire-and-forget side effect from `loadDashboard()`.

**Rationale**: The `coach_memories` table was written automatically on PRs but never displayed. Showing the memory feed closes the feedback loop and lets users understand what context the AI has about them.

**Rollback**: Remove `#memory-card` from Dashboard HTML; remove `loadMemory` and `addMemory` from `app.js`; remove `loadMemory()` call from `loadDashboard`.

### Feature #15 — PRs Board (already existed, confirmed surfaced)
**Files**: none (pre-existing)
**What**: `GET /api/prs` was already fetched by `loadWorkout()` and rendered by `renderPRs()` into `#prs-list` in the Workout tab. No additional changes needed — confirmed surfaced.

### Feature #14 — Body Measurements Web UI
**Files**: `static/index.html`, `static/app.js`
**What**:
- Added Body Measurements log form to the Progress tab (below Strength Trends card) with fields: Weight, Waist, Chest, Arm (cm). Arm value is stored as both left and right.
- `logMeasurement(event)` calls `POST /api/measurements`; on success clears fields, shows toast, re-renders measurement history table.
- `renderMeasurements(data)` renders a date-sorted table with Weight / Waist / Chest / Arm columns.
- `loadProgress()` now fetches `GET /api/measurements?limit=30` in parallel alongside the existing progress/plateaus calls.

**Rationale**: `POST /api/measurements` and `GET /api/measurements` existed but were unreachable from the web app. Measurements power the 30-day weight-change calculation in `bot_json_context_block()` and are visible to the AI context.

**Rollback**: Remove measurement form and `#measurements-history` div from Progress tab; remove `renderMeasurements` and `logMeasurement` from `app.js`; remove measurements from `loadProgress()` Promise.all.

### Feature #13 — Goals Web UI
**Files**: `static/index.html`, `static/app.js`
**What**:
- Added Goals card to the bottom of the Profile tab with a list of current goals (active/inactive badge) and a "Set Goal" form (goal type, target weight, target BF%, target date).
- `renderGoals(goals)` renders each goal with type, target details, and ACTIVE/inactive badge.
- `saveGoal(event)` calls `POST /api/goals`; deactivates prior goals of the same type (backend behavior), then re-renders the list.
- `loadProfile()` now fetches `GET /api/goals` in parallel with profile and auth/me.

**Rationale**: `GET /api/goals` and `POST /api/goals` existed but were web-invisible. Goals feed the `Active goal:` line in `context_block()` which shapes every AI recommendation.

**Rollback**: Remove Goals card from Profile tab HTML; remove `renderGoals` and `saveGoal` from `app.js`; remove goals from `loadProfile()` Promise.all.

### Feature #12 — Nutrition Tab (Meal Logging Web UI)
**Files**: `static/index.html`, `static/app.js`
**What**:
- Added "Nutrition" nav tab with today's macro totals (kcal / Protein / Carbs / Fat), a meal log form, today's meal list, and a 30-entry recent history.
- `logMeal(event)` calls `POST /api/meals`; if description is provided without manual macros, the backend calls `estimate_meal_macros()` and returns AI-estimated macros. Toast shows protein and source.
- `loadNutrition()` calls `GET /api/meals/today` and `GET /api/meals?limit=30` in parallel; renders totals and both lists.
- Tab registered in `showTab()` loaders as `nutrition: loadNutrition`.

**Rationale**: Meal logging was Telegram-only. Non-Telegram users had no nutrition tracking. Protein compliance is the second most important AI context signal after recovery.

**Rollback**: Remove Nutrition nav tab button and `#tab-nutrition` section from HTML; remove `loadNutrition` and `logMeal` from `app.js`; remove `nutrition` from tab loaders.

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
