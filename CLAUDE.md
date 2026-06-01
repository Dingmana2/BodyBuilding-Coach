# BodyBuilding Coach AI — Claude Code Conventions

## gstack Skills

gstack is installed at `~/.claude/skills/gstack`. Use `/browse` from gstack for all web browsing tasks — never use `mcp__claude-in-chrome__*` tools.

Available skills (invoke with `/skill-name`):
`/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`, `/design-consultation`, `/design-shotgun`, `/design-html`, `/review`, `/ship`, `/land-and-deploy`, `/canary`, `/benchmark`, `/browse`, `/connect-chrome`, `/qa`, `/qa-only`, `/design-review`, `/setup-browser-cookies`, `/setup-deploy`, `/setup-gbrain`, `/retro`, `/investigate`, `/document-release`, `/document-generate`, `/careful`, `/freeze`, `/guard`, `/unfreeze`, `/gstack-upgrade`, `/learn`

Note: `/browse`, `/connect-chrome`, and browser-dependent skills require Playwright Chromium. In this remote environment the browser download is network-restricted — browser skills will not work until Chromium is available.

---

## Project identity

A Telegram bot + FastAPI web backend that acts as a personalised AI bodybuilding coach.
Primary file: `telegram_bot.py` (~5 200 lines). Web API: `main.py`. AI layer: `claude_service.py`.

---

## Coding conventions

### Error shape
API errors from FastAPI return `{"detail": "message"}`. Bot errors are sent as plain text to the user via `reply_text`. Never expose exception class names or stack traces to end users.

```python
# FastAPI
raise HTTPException(status_code=400, detail="Profile not found.")

# Bot
await update.message.reply_text("❌ No profile found. Run /start to set up.")
```

### Identity from JWT — never from client body
`get_current_user_id()` in `main.py` extracts the user ID from the HS256 JWT. Never trust a `user_id` field in the request body for authorisation decisions.

### XSS prevention — non-negotiable
**Telegram**: every user-supplied or AI-generated string interpolated into a `parse_mode="Markdown"` message MUST be wrapped in `esc()`. The `esc()` function escapes `_`, `*`, `` ` ``, `[`.

**Web SPA**: every value set via `.innerHTML` MUST go through the JS `esc()` helper. `textContent` assignments are safe without escaping.

```python
# Correct — Telegram
await update.message.reply_text(f"Your goal: *{esc(user['profile']['goal'])}*", parse_mode="Markdown")

# Wrong
await update.message.reply_text(f"Your goal: *{user['profile']['goal']}*", parse_mode="Markdown")
```

### Caching on the frontend
Use `cachedApi(url)` for GET requests (30 s TTL). Call `invalidateCache(url)` or `invalidateCache()` after mutations to keep the UI fresh.

### Secrets from env only
All secrets via `os.getenv("KEY")`. Raise `ValueError` or `RuntimeError` if a required key is missing (see `crypto_utils.py` pattern). Never hardcode tokens, keys, or passwords.

### Type hints + docstrings
All new Python functions: type hints on all parameters and return value. One-line docstring. No multi-paragraph blocks.

```python
def build_plan_prompt(profile: dict, context: str) -> str:
    """Construct the system prompt for full plan generation."""
    ...
```

### No new dependencies without justification
If an existing stdlib or already-imported library can do the job, use it. Document the justification in the CHANGELOG when adding to `requirements.txt`.

---

## Async contract

All calls to external services inside `async def` handlers MUST use:

```python
loop = asyncio.get_running_loop()
result = await loop.run_in_executor(None, blocking_fn, arg1, arg2)
```

Use `asyncio.get_running_loop()` — NOT `asyncio.get_event_loop()` (deprecated in Python 3.10+).

External services that MUST go through run_in_executor:
- `anthropic.messages.create()` (10–40 s)
- All `garmin_service.*` calls (2–8 s)
- All `mfp_service.*` calls (1–3 s)
- All `nutrition_service.*` calls
- All `requests.*` calls

---

## State management

Two stores that MUST remain consistent:

| Store | Key | Lock |
|---|---|---|
| `bot_state.json` | `int(chat_id)` | `_STORE_LOCK` (threading.Lock) |
| SQLite/PostgreSQL | SQLAlchemy ORM | Connection pool |

Writes to `bot_state.json` must use `with _STORE_LOCK:` and call `_save_store()`. The save function writes atomically via `.tmp` + `os.replace()`.

---

## Agent pipeline

```
scout → architect → HUMAN APPROVAL → builder(s) → [read-only guards in parallel] → test-runner gate
```

1. **scout**: maps affected files, call chains, risks. Read-only.
2. **architect**: produces implementation plan with diffs, API contracts, migration steps. Read-only.
3. **HUMAN APPROVAL**: plan must be reviewed and approved before any file is touched.
4. **builders** (backend-engineer, frontend-engineer, integrations-engineer, database-engineer, prompt-engineer): implement from the approved plan.
5. **Read-only guards in parallel** (code-reviewer, security-auditor, science-fact-checker, performance-auditor, accessibility-auditor, privacy-compliance-auditor, cost-observability-auditor): review the diff.
6. **test-runner**: runs pytest, blocks merge on failures.

---

## Hard stops — never auto-execute, always require human sign-off

| Action | Reason |
|---|---|
| Production deployments | Risk of outage or data loss |
| `ENCRYPTION_KEY` rotation | Invalidates all stored Garmin/MFP credentials |
| `SECRET_KEY` rotation | Invalidates all active JWTs |
| Database migrations in production | Risk of irreversible schema change |
| Any medical dosing claim | Legal/safety liability |
| Calorie target < 1 200 kcal (female) or < 1 500 kcal (male) | Eating disorder / health risk |
| New third-party data processor | GDPR disclosure required |

For any of the above, present the action and wait for explicit `yes, proceed` before executing.

---

## Telegram bot conventions

### New commands — TWO registration sites required
```python
# 1. In main() handler block:
app.add_handler(CommandHandler("name", cmd_name))

# 2. In set_my_commands list:
BotCommand("name", "short description ≤32 chars")
```

### Callback handlers
`query.answer()` must be the first `await` in every `CallbackQueryHandler`. Missing it leaves a spinner on the button forever.

`delete_message()` and `edit_message_text()` must be in `try/except Exception`.

### Cooldowns
New features that call the Anthropic API must check `_check_cooldown()` before the call.

### Markdown escaping
Use `parse_mode="Markdown"` (legacy, not MarkdownV2). The `esc()` function handles the necessary escaping.

---

## Model selection

| Use case | Constant | Model |
|---|---|---|
| Photo physique analysis | `ANALYSIS_MODEL` | `claude-opus-4-7` |
| Bulk plan generation | `ANALYSIS_MODEL` / `CHAT_MODEL` | opus or sonnet |
| Chat / coaching replies | `CHAT_MODEL` | `claude-sonnet-4-6` |
| Reports, summaries, macros | `SUMMARY_MODEL` | `claude-haiku-4-5-20251001` |
| Weak points, meal macros | `WEAK_POINT_MODEL` | `claude-haiku-4-5-20251001` |

Using opus where haiku suffices is a ~60× cost bug. Justify any deviation in a comment.

---

## Prompt engineering

- Multi-paragraph prompts belong in `prompt_builder.py`.
- Inline f-strings only for < 3-line snippets.
- User-supplied text must be clearly bounded in prompts (use `<user_input>...</user_input>`).
- JSON output prompts must include the exact schema and instruct Claude to return ONLY JSON.
- Safety disclaimers mandatory: physique analysis outputs must end with `_⚠️ AI estimate only — not medical advice. Consult a qualified professional._`

---

## CHANGELOG

Every PR must append to `CHANGELOG.md`:
```markdown
## [YYYY-MM-DD] Sprint N — <title>
### Changed/Added/Fixed
- `file.py` — description
### Rollback
- `git revert <hash>` — no schema changes
```
