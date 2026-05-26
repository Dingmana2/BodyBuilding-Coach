#!/usr/bin/env python3
"""
BodyBuilding Coach AI — Telegram Bot
Deploy free on Railway.app. See README for setup steps.
"""

import asyncio
import base64
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date as _date, timedelta
from io import BytesIO
from pathlib import Path

import anthropic
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
BOT_SECRET = os.getenv("BOT_SECRET", "")
ANALYSIS_MODEL = "claude-opus-4-7"
CHAT_MODEL = "claude-sonnet-4-6"
SUMMARY_MODEL = "claude-haiku-4-5-20251001"
MAX_HISTORY = 20

# Cooldown in seconds between expensive per-user operations.
PLAN_COOLDOWN = 300     # 5 minutes between /plan calls
ANALYZE_COOLDOWN = 60   # 1 minute between photo analyses
_plan_cooldowns: dict[int, float] = {}
_analyze_cooldowns: dict[int, float] = {}

# ── State persistence ─────────────────────────────────────────────────────────
# User data is saved to a JSON file so it survives bot restarts / redeploys.
# On Railway, mount a volume at /data and set DATA_DIR=/data in env vars.

_STORE_PATH = Path(os.getenv("DATA_DIR", ".")) / "bot_state.json"


def _load_store() -> dict[int, dict]:
    if _STORE_PATH.exists():
        try:
            raw = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
            return {int(k): v for k, v in raw.items()}
        except Exception as e:
            print(f"Warning: could not load bot state: {e}")
    return {}


def _save_store() -> None:
    try:
        _STORE_PATH.write_text(
            json.dumps(user_data, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        print(f"Warning: could not save bot state: {e}")


# In-memory store: chat_id → {profile, last_analysis, last_plan, conversation_history}
user_data: dict[int, dict] = _load_store()

# chat_id → web user_id, populated after /link succeeds
_linked_user_ids: dict[int, int] = {}


async def _api_get(path: str, chat_id: int | None = None) -> dict:
    """Call the FastAPI backend as the bot. Returns parsed JSON or raises."""
    headers = {}
    if BOT_SECRET and chat_id is not None:
        linked_uid = _linked_user_ids.get(chat_id)
        if linked_uid:
            headers["X-Bot-Secret"] = BOT_SECRET
            headers["X-Telegram-User-ID"] = str(linked_uid)
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{API_BASE_URL}/api{path}", headers=headers)
        r.raise_for_status()
        return r.json()


async def _api_post(path: str, data: dict, chat_id: int | None = None) -> dict:
    """POST to the FastAPI backend as the bot. Returns parsed JSON or raises."""
    headers = {"Content-Type": "application/json"}
    if BOT_SECRET and chat_id is not None:
        linked_uid = _linked_user_ids.get(chat_id)
        if linked_uid:
            headers["X-Bot-Secret"] = BOT_SECRET
            headers["X-Telegram-User-ID"] = str(linked_uid)
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{API_BASE_URL}/api{path}", json=data, headers=headers)
        r.raise_for_status()
        return r.json()


RESEARCH_TOPICS = [
    "muscle hypertrophy resistance training 2024",
    "protein requirements bodybuilding athletes",
    "creatine supplementation strength performance",
    "body recomposition simultaneous fat loss muscle gain",
]


_anthropic_client: anthropic.Anthropic | None = None


def claude() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        if not ANTHROPIC_KEY:
            raise ValueError("ANTHROPIC_API_KEY not set in environment.")
        _anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    return _anthropic_client


def _check_cooldown(cooldown_dict: dict[int, float], chat_id: int, seconds: int) -> int | None:
    """Returns remaining seconds if on cooldown, None if clear (and records the call)."""
    elapsed = time.monotonic() - cooldown_dict.get(chat_id, 0)
    if elapsed < seconds:
        return int(seconds - elapsed)
    cooldown_dict[chat_id] = time.monotonic()
    return None


def get_user(chat_id: int) -> dict:
    if chat_id not in user_data:
        user_data[chat_id] = {
            "profile": {},
            "last_analysis": None,
            "last_plan": None,
            "conversation_history": [],
            "active_command": None,   # "checkin" | "workout" | "connect_garmin" | None
            "command_state": {},      # step data for multi-step commands
            "active_session_id": None,  # open WorkoutSession id
            "reminders": {},          # {"workout": {"hour": 7, "minute": 0}, ...}
            "checkins": [],           # list of DailyCheckIn dicts (local cache)
            "set_logs": [],           # list of SetLog dicts (local cache)
            "prs": {},                # exercise_name → {weight_kg, reps, estimated_1rm, date}
            "meal_logs": [],          # list of MealLog dicts (local cache)
            "measurements": [],       # list of BodyMeasurement dicts (local cache)
            "session_counter": 0,     # total sessions completed
            "garmin_email": None,
            "garmin_pass_enc": None,
            "mfp_username": None,
            "mfp_pass_enc": None,
            "units": "kg",            # display unit: "kg" or "lbs"
        }
    else:
        # Back-fill fields added in later versions
        u = user_data[chat_id]
        defaults = {
            "active_command": None, "command_state": {}, "active_session_id": None,
            "reminders": {}, "checkins": [], "set_logs": [], "prs": {},
            "meal_logs": [], "measurements": [], "session_counter": 0,
            "garmin_email": None, "garmin_pass_enc": None,
            "mfp_username": None, "mfp_pass_enc": None,
            "units": "kg",
        }
        for k, v in defaults.items():
            u.setdefault(k, v)
    return user_data[chat_id]


# ── Scheduler ────────────────────────────────────────────────────────────────

_scheduler = AsyncIOScheduler()
_app: "Application | None" = None   # set in main(); used by scheduler jobs to send messages


def _today() -> str:
    return _date.today().isoformat()


# ── Unit helpers ─────────────────────────────────────────────────────────────

def _parse_height(s: str) -> str:
    s = s.strip().lower().replace('"', '').replace(' ', '')
    m = re.match(r"(\d+)'(\d+)", s)
    if m:
        return str(round(int(m.group(1)) * 30.48 + int(m.group(2)) * 2.54))
    m = re.match(r"(\d+(?:\.\d+)?)in$", s)
    if m:
        return str(round(float(m.group(1)) * 2.54))
    return re.sub(r"cm$", "", s)


def _parse_weight(s: str) -> str:
    s = s.strip().lower().replace(' ', '')
    m = re.match(r"(\d+(?:\.\d+)?)(?:lbs?|pounds?)$", s)
    if m:
        return str(round(float(m.group(1)) * 0.453592))
    return re.sub(r"kg$", "", s)


def _parse_measurement_cm(s: str) -> float | None:
    """Convert a measurement string to cm as a float. Supports in/cm/bare number."""
    s = s.strip().lower().replace(' ', '')
    m = re.match(r"(\d+(?:\.\d+)?)(?:in|inches?|\"?)$", s)
    if m:
        return round(float(m.group(1)) * 2.54, 1)
    m = re.match(r"(\d+(?:\.\d+)?)(?:cm)?$", s)
    if m:
        return float(m.group(1))
    return None


def _parse_logset_weight_kg(s: str) -> float | None:
    """Parse a weight token like '100kg', '225lbs', '100' → kg float."""
    s = s.strip().lower().replace(' ', '')
    m = re.match(r"(\d+(?:\.\d+)?)(?:lbs?|pounds?)$", s)
    if m:
        return round(float(m.group(1)) * 0.453592, 2)
    m = re.match(r"(\d+(?:\.\d+)?)(?:kg)?$", s)
    if m:
        return float(m.group(1))
    return None


def _epley_1rm(weight_kg: float, reps: int) -> float:
    """Epley formula: weight × (1 + reps/30)."""
    return round(weight_kg * (1 + reps / 30), 1)


# ── Unit display helpers ──────────────────────────────────────────────────────

_LBS_PER_KG = 2.20462


def _wu(user: dict) -> str:
    """User's preferred weight unit: 'kg' or 'lbs'."""
    return user.get("units", "kg")


def _w(weight_kg: float, user: dict) -> float:
    """Convert a stored kg value to the user's display unit."""
    if _wu(user) == "lbs":
        return round(weight_kg * _LBS_PER_KG, 1)
    return weight_kg


def _wfmt(weight_kg: float, user: dict) -> str:
    """Format a stored kg value as a display string, e.g. '102.5kg' or '226lbs'."""
    return f"{_w(weight_kg, user):g}{_wu(user)}"


def _parse_weight_input(text: str, user: dict) -> float | None:
    """Parse user-typed weight respecting their unit preference.

    Explicit suffixes (kg/lbs) always win; bare numbers are treated as the
    user's preferred unit and converted to kg for storage.
    """
    s = text.strip().lower().replace(" ", "")
    # Explicit lbs
    m = re.match(r"(\d+(?:\.\d+)?)(?:lbs?|pounds?)$", s)
    if m:
        return round(float(m.group(1)) * 0.453592, 4)
    # Explicit kg
    m = re.match(r"(\d+(?:\.\d+)?)kg$", s)
    if m:
        return float(m.group(1))
    # Bare number — interpret in user's preferred unit
    m = re.match(r"(\d+(?:\.\d+)?)$", s)
    if m:
        val = float(m.group(1))
        return round(val / _LBS_PER_KG, 4) if _wu(user) == "lbs" else val
    return None


# ── Workout inline keyboard helpers ──────────────────────────────────────────

def _get_today_exercises(user: dict) -> list:
    """Return today's exercise list from the user's current plan (empty list if none)."""
    if not user.get("last_plan"):
        return []
    today_name = _date.today().strftime("%A")
    days = user["last_plan"].get("workout", {}).get("days", [])
    today_day = next((d for d in days if d.get("day", "").lower() == today_name.lower()), None)
    return today_day.get("exercises", []) if today_day else []


def _get_session_exercises(user: dict) -> list:
    """Return exercises for the active session day (falls back to today)."""
    if not user.get("last_plan"):
        return []
    day_name = (
        user.get("command_state", {}).get("active_session_day")
        or _date.today().strftime("%A")
    )
    days = user["last_plan"].get("workout", {}).get("days", [])
    day = next((d for d in days if d.get("day", "").lower() == day_name.lower()), None)
    return day.get("exercises", []) if day else []


def _days_keyboard(user: dict) -> InlineKeyboardMarkup:
    """Inline keyboard listing all plan days so the user can pick a different one."""
    days = user.get("last_plan", {}).get("workout", {}).get("days", []) if user.get("last_plan") else []
    rows: list[list[InlineKeyboardButton]] = []
    for d in days:
        name = d.get("day", "")
        focus = d.get("focus", "")
        label = f"{name} — {focus}" if focus else name
        rows.append([InlineKeyboardButton(label, callback_data=f"wk:day:{name}")])
    rows.append([InlineKeyboardButton("‹ Back", callback_data="wk:pick")])
    return InlineKeyboardMarkup(rows)


def _do_log_set(user: dict, exercise: str, weight_kg: float, reps: int) -> tuple[dict, bool]:
    """Append a set to user state, check for PR. Returns (entry, is_pr)."""
    one_rm = _epley_1rm(weight_kg, reps)
    entry = {
        "exercise_name": exercise,
        "weight_kg": weight_kg,
        "reps": reps,
        "estimated_1rm": one_rm,
        "date": _today(),
        "session_id": user["active_session_id"],
    }
    user["set_logs"].append(entry)
    user["set_logs"] = user["set_logs"][-500:]
    if user["active_session_id"] is not None:
        user["command_state"].setdefault("current_session_sets", []).append(entry)
    pr = user["prs"].get(exercise)
    is_pr = pr is None or one_rm > pr["estimated_1rm"]
    if is_pr:
        user["prs"][exercise] = {"weight_kg": weight_kg, "reps": reps, "estimated_1rm": one_rm, "date": _today()}
    _save_store()
    return entry, is_pr


def _ex_keyboard(exercises: list, has_session: bool) -> InlineKeyboardMarkup:
    """Inline keyboard listing today's exercises as tap buttons."""
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for ex in exercises:
        name = ex["name"] if isinstance(ex, dict) else str(ex)
        pair.append(InlineKeyboardButton(name, callback_data=f"wk:ex:{name[:40]}"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton("📅 Different day…", callback_data="wk:days")])
    rows.append([InlineKeyboardButton("✏️ Other exercise…", callback_data="wk:ex_custom")])
    if has_session:
        rows.append([InlineKeyboardButton("✅ End Session", callback_data="wk:end")])
    else:
        rows.append([InlineKeyboardButton("🏋️ Start Session", callback_data="wk:start")])
    return InlineKeyboardMarkup(rows)


def _weight_keyboard(exercise_name: str, user: dict) -> InlineKeyboardMarkup:
    """Inline keyboard with recent weights ±increments for this exercise.

    Callback data always stores kg; labels display in the user's chosen unit.
    """
    recent = [s["weight_kg"] for s in reversed(user["set_logs"]) if s["exercise_name"] == exercise_name]
    last = recent[0] if recent else None
    if last:
        opts = sorted({max(0.0, last - 5), max(0.0, last - 2.5), last, last + 2.5, last + 5})
    else:
        opts = [20.0, 40.0, 60.0, 80.0, 100.0]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for w in opts:
        row.append(InlineKeyboardButton(_wfmt(w, user), callback_data=f"wk:w:{w}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Type weight…", callback_data="wk:w:type")])
    rows.append([InlineKeyboardButton("‹ Back to exercises", callback_data="wk:pick")])
    return InlineKeyboardMarkup(rows)


def _rep_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for selecting rep count."""
    counts = [3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for r in counts:
        row.append(InlineKeyboardButton(str(r), callback_data=f"wk:r:{r}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("✏️ Type reps…", callback_data="wk:r:type")])
    rows.append([InlineKeyboardButton("‹ Back to exercises", callback_data="wk:pick")])
    return InlineKeyboardMarkup(rows)


def _plan_days_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for choosing how many workout days/week before plan generation."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{n} day{'s' if n > 1 else ''}", callback_data=f"plan:days:{n}")
        for n in (2, 3, 4, 5, 6)
    ]])


def _after_set_keyboard() -> InlineKeyboardMarkup:
    """Keyboard shown after a set is successfully logged."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Same exercise", callback_data="wk:more"),
            InlineKeyboardButton("Other exercise", callback_data="wk:pick"),
        ],
        [InlineKeyboardButton("✅ End Session", callback_data="wk:end")],
    ])


def _sparkline(values: list[float]) -> str:
    """Return a Unicode sparkline string for a list of numeric values."""
    bars = " ▁▂▃▄▅▆▇█"
    if not values:
        return ""
    lo, hi = min(values), max(values)
    rng = hi - lo or 1
    return "".join(bars[round((v - lo) / rng * 8)] for v in values)


# ── Commands ──────────────────────────────────────────────────────────────────

WELCOME = (
    "⚡ *BodyBuilding Coach AI*\n\n"
    "Science-backed coaching for any age, any level, any goal.\n\n"
    "*Getting started:*\n"
    "/profile — Set your stats (age, weight, goal, etc.)\n"
    "/plan — Generate full workout + diet + supplement plan\n"
    "📸 Send a photo — physique analysis + plan\n\n"
    "*Daily tracking:*\n"
    "/checkin — Log sleep, energy, soreness → recovery score\n"
    "/workout — Start/end a session · see today's plan\n"
    "/log — Quick tap-based workout logger\n"
    "/logset — Log a set: `/logset bench 100kg 8`\n"
    "/weight — Quick weight log: `/weight 84.5`\n"
    "/meal — Log food: `/meal 2 eggs, oatmeal, banana`\n"
    "/macros — Today's macro targets vs. logged\n\n"
    "*Progress & analytics:*\n"
    "/progress — 30-day trend: weight, BF%, strength\n"
    "/stats — Personal records + volume by muscle\n"
    "/streak — Check-in streak + badges earned\n"
    "/goals — Set/view your target (weight, BF%, date)\n"
    "/measurements — Log body measurements\n"
    "/weakpoints — AI analysis of training imbalances\n"
    "/report — Weekly AI coaching report\n\n"
    "*Other:*\n"
    "/connect — Link Garmin or MyFitnessPal\n"
    "/mfp sync — Sync today's MFP diary\n"
    "/reminders — Set daily workout/check-in reminders\n"
    "/research — Latest PubMed research highlights\n"
    "/help — Show this message\n\n"
    "💬 Chat anytime — tell me to update your plan, ask questions, or give feedback."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME, parse_mode="Markdown")


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    profile = user["profile"]

    if not context.args:
        current = (
            "\n".join(f"• {k}: {v}" for k, v in profile.items())
            if profile
            else "Not set yet."
        )
        await update.message.reply_text(
            f"*Your Profile:*\n{current}\n\n"
            "Copy this line, fill in your info, and send it:\n"
            "`/profile age=25 gender=male height=5'6\" weight=165lbs goal=bulk experience=beginner days=4`\n\n"
            "Height: feet/inches *or* cm — `5'6\"` or `178cm`\n"
            "Weight: lbs *or* kg — `165lbs` or `75kg`\n"
            "Goals: `bulk` · `cut` · `recomp` · `maintain` · `health` · `performance`\n"
            "Experience: `beginner` · `intermediate` · `advanced`\n"
            "Gender: anything — male, female, non-binary, etc.",
            parse_mode="Markdown",
        )
        return

    for arg in context.args:
        if "=" in arg:
            key, _, value = arg.partition("=")
            key = key.strip().lower()
            value = value.strip()
            if key == "height":
                value = _parse_height(value)
            elif key == "weight":
                value = _parse_weight(value)
            profile[key] = value

    user["profile"] = profile
    _save_store()
    await update.message.reply_text(
        "✅ Profile saved!\n\n"
        "Send a photo for physique analysis, or type /plan to get your plan now."
    )


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    force_new = bool(context.args and context.args[0].lower() in ("new", "reset", "regenerate"))

    # If plan exists and not forcing a regeneration, just display it
    if user["last_plan"] and not force_new:
        await _send_plan(update, user["last_plan"])
        await update.message.reply_text(
            "💬 Tell me to update it — e.g. 'remove leg day', 'I'm vegetarian', 'train only 3 days'.\n"
            "Type `/plan new` to regenerate from scratch.",
            parse_mode="Markdown",
        )
        return

    remaining = _check_cooldown(_plan_cooldowns, chat_id, PLAN_COOLDOWN)
    if remaining:
        await update.message.reply_text(
            f"⏳ Please wait {remaining}s before generating another plan."
        )
        return

    if not user["last_analysis"] and not user["profile"]:
        await update.message.reply_text(
            "Set your stats first with /profile, then I can build your plan.\n\n"
            "Example:\n"
            "`/profile age=25 gender=female height=165 weight=65 goal=recomp experience=beginner days=3`\n\n"
            "Or send a photo and I'll analyze your physique directly 📸",
            parse_mode="Markdown",
        )
        return

    current_days = user["profile"].get("days", "4")
    await update.message.reply_text(
        f"How many days per week do you want to train? _(currently {current_days})_\n\n"
        "Tap a number to generate your plan instantly:",
        parse_mode="Markdown",
        reply_markup=_plan_days_keyboard(),
    )


async def cmd_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    # Allow inline: /checkin sleep=7 energy=6 soreness=5 stress=4
    if context.args:
        data: dict[str, int] = {}
        for arg in context.args:
            if "=" in arg:
                k, _, v = arg.partition("=")
                try:
                    data[k.strip().lower()] = int(v.strip())
                except ValueError:
                    pass
        if all(k in data for k in ("sleep", "energy", "soreness", "stress")):
            await _finish_checkin(update, user, data)
            return

    # Check for Garmin pre-fill (yesterday's cached data)
    garmin_data = None
    if user.get("garmin_email"):
        try:
            import garmin_service
            garmin_data = garmin_service.get_cached(chat_id)
        except Exception:
            pass

    if garmin_data and garmin_data.get("sleep_score_1_10"):
        sleep_score = garmin_data["sleep_score_1_10"]
        hrs = garmin_data.get("sleep_duration_hrs", "?")
        hrv = garmin_data.get("hrv_ms")
        stress_score = garmin_data.get("stress_score_1_10")

        hrv_text = f" | HRV: {int(hrv)}ms" if hrv else ""
        stress_note = f"\n_Stress pre-filled: {stress_score}/10 from Garmin_" if stress_score else ""
        pre_filled: dict[str, int] = {"sleep": sleep_score}
        if stress_score:
            pre_filled["stress"] = stress_score

        await update.message.reply_text(
            f"📡 *Garmin data pulled:*\n"
            f"😴 Sleep: {hrs}hrs → score {sleep_score}/10{hrv_text}{stress_note}\n\n"
            "I just need a couple more scores from you:",
            parse_mode="Markdown",
        )

        all_steps = ["sleep", "energy", "soreness", "stress"]
        remaining = [s for s in all_steps if s not in pre_filled]

        user["active_command"] = "checkin"
        user["command_state"] = {
            "step": 0,
            "data": pre_filled,
            "remaining_steps": remaining,
            "garmin_data": garmin_data,
        }

        _step_prompts = {
            "energy": "⚡ *Energy levels?* Rate 1-10",
            "soreness": "🤕 *Muscle soreness?* Rate 1-10\n_(1 = very sore, 10 = fresh)_",
            "stress": "🧠 *Stress level?* Rate 1-10\n_(1 = very stressed, 10 = calm)_",
        }
        await update.message.reply_text(_step_prompts[remaining[0]], parse_mode="Markdown")
        return

    # Normal multi-step flow
    user["active_command"] = "checkin"
    user["command_state"] = {
        "step": 0,
        "data": {},
        "remaining_steps": ["sleep", "energy", "soreness", "stress"],
    }
    await update.message.reply_text(
        "😴 *Sleep quality?* Rate 1-10\n_(1 = terrible, 10 = perfect)_",
        parse_mode="Markdown",
    )


async def _handle_checkin_step(update: Update, user: dict, text: str) -> None:
    state = user["command_state"]
    remaining = state.get("remaining_steps", ["sleep", "energy", "soreness", "stress"])

    _step_prompts = {
        "energy": "⚡ *Energy levels?* Rate 1-10",
        "soreness": "🤕 *Muscle soreness?* Rate 1-10\n_(1 = very sore, 10 = fresh)_",
        "stress": "🧠 *Stress level?* Rate 1-10\n_(1 = very stressed, 10 = calm)_",
    }

    try:
        score = max(1, min(10, int(text.strip())))
    except ValueError:
        await update.message.reply_text("Please enter a number from 1 to 10.")
        return

    step = state["step"]
    key = remaining[step]
    state["data"][key] = score
    state["step"] = step + 1

    if state["step"] < len(remaining):
        next_key = remaining[state["step"]]
        await update.message.reply_text(_step_prompts[next_key], parse_mode="Markdown")
    else:
        user["active_command"] = None
        garmin_data = state.get("garmin_data")
        await _finish_checkin(update, user, state["data"], garmin_data)


async def _finish_checkin(update: Update, user: dict, data: dict, garmin_data: dict | None = None) -> None:
    msg = await update.message.reply_text("Scoring your recovery…")
    try:
        from claude_service import generate_recovery_insight
        score, tip = generate_recovery_insight(
            data["sleep"], data["energy"], data["soreness"], data["stress"],
            user["profile"] or None,
        )
    except Exception:
        score = round((data["sleep"] + data["energy"] + (11 - data["soreness"]) + (11 - data["stress"])) / 4 * 10)
        tip = "Listen to your body and train accordingly today."

    entry = {
        "date": _today(),
        "sleep_score": data["sleep"],
        "energy_score": data["energy"],
        "soreness_score": data["soreness"],
        "stress_score": data["stress"],
        "recovery_score": score,
        "coaching_tip": tip,
        "hrv_ms": garmin_data.get("hrv_ms") if garmin_data else None,
        "resting_hr_bpm": garmin_data.get("resting_hr_bpm") if garmin_data else None,
        "sleep_duration_hrs": garmin_data.get("sleep_duration_hrs") if garmin_data else None,
        "data_source": "garmin" if garmin_data else "manual",
    }
    user["checkins"].append(entry)
    # Keep only last 90 days
    user["checkins"] = user["checkins"][-90:]
    _save_store()

    bar = "🟢" if score >= 75 else ("🟡" if score >= 50 else "🔴")
    garmin_line = ""
    if garmin_data:
        hrv = garmin_data.get("hrv_ms")
        rhr = garmin_data.get("resting_hr_bpm")
        parts = []
        if hrv:
            parts.append(f"HRV {int(hrv)}ms")
        if rhr:
            parts.append(f"RHR {rhr}bpm")
        if parts:
            garmin_line = f"\n📡 Garmin: {' | '.join(parts)}"

    # Calculate current streak
    sorted_dates = sorted({c["date"] for c in user["checkins"]}, reverse=True)
    streak_count = 0
    for d in sorted_dates:
        dt = _date.fromisoformat(d)
        expected = _date.today() - timedelta(days=streak_count)
        if dt == expected:
            streak_count += 1
        else:
            break

    streak_text = f"\n🔥 *{streak_count}-day streak!*" if streak_count >= 2 else ""
    deload_hint = ""
    if score < 50:
        deload_hint = "\n\n⚠️ _Recovery is low — consider a deload or active recovery session today._"

    await msg.edit_text(
        f"✅ *Check-in saved!*\n\n"
        f"{bar} Recovery Score: *{score}/100*{streak_text}\n\n"
        f"😴 Sleep: {data['sleep']}/10  ⚡ Energy: {data['energy']}/10\n"
        f"🤕 Soreness: {data['soreness']}/10  🧠 Stress: {data['stress']}/10{garmin_line}\n\n"
        f"_{tip}_{deload_hint}",
        parse_mode="Markdown",
    )

    total_checkins = len(user["checkins"])
    tier = user.get("subscription_tier", "free")
    # Conversion nudge after 3rd check-in
    if total_checkins == 3 and tier == "free":
        await update.message.reply_text(
            "📈 *3 check-ins done!*\n\n"
            "You're building a recovery tracking habit. "
            "Pro members get their full recovery trend analysis, "
            "Garmin HRV sync, and weekly AI coaching reports.\n\n"
            "Upgrade at the web app to unlock all features 🔓",
            parse_mode="Markdown",
        )
    # Nudge at 7-day streak
    elif streak_count == 7 and tier == "free":
        await update.message.reply_text(
            "🔥 *7-day streak!*\n\n"
            "You're in the top 10% for consistency. "
            "Pro users get automated weekly reports, "
            "progressive overload suggestions after every session, "
            "and unlimited photo analyses.\n\n"
            "Lock in your results with Pro — upgrade at the web app 🚀",
            parse_mode="Markdown",
        )


async def cmd_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    sub = context.args[0].lower() if context.args else ""

    if sub == "start":
        if user["active_session_id"] is not None:
            await update.message.reply_text(
                f"⚠️ Session #{user['active_session_id']} is already open. "
                "Type /workout end to close it first."
            )
            return
        user["session_counter"] += 1
        sid = user["session_counter"]
        user["active_session_id"] = sid
        user["command_state"]["current_session_sets"] = []
        _save_store()

        exercises = _get_session_exercises(user)
        plan_text = ""
        if user.get("last_plan"):
            session_day = user["command_state"].get("active_session_day") or _date.today().strftime("%A")
            days = user["last_plan"].get("workout", {}).get("days", [])
            today_day = next((d for d in days if d.get("day", "").lower() == session_day.lower()), None)
            focus = today_day.get("focus", "") if today_day else ""
            plan_text = f"\n*{session_day} — {focus}*" if focus else ""

        await update.message.reply_text(
            f"🏋️ *Session #{sid} started!*{plan_text}\n\nTap an exercise to log a set:",
            parse_mode="Markdown",
            reply_markup=_ex_keyboard(exercises, has_session=True),
        )

    elif sub == "end":
        sid = user["active_session_id"]
        if sid is None:
            await update.message.reply_text("No open session. Start one with `/workout start`.", parse_mode="Markdown")
            return

        session_sets = user["command_state"].get("current_session_sets", [])
        total_volume = sum(s["weight_kg"] * s["reps"] for s in session_sets)
        total_sets = len(session_sets)
        user["active_session_id"] = None
        user["command_state"]["current_session_sets"] = []
        user["command_state"].pop("active_session_day", None)
        _save_store()

        exercise_summary = ""
        if session_sets:
            by_ex: dict[str, list] = {}
            for s in session_sets:
                by_ex.setdefault(s["exercise_name"], []).append(s)
            lines = []
            for ex, sets in by_ex.items():
                best = max(sets, key=lambda x: x["estimated_1rm"])
                lines.append(f"  {ex}: {len(sets)} sets | best {_wfmt(best['weight_kg'], user)}×{best['reps']} (1RM ~{_wfmt(best['estimated_1rm'], user)})")
            exercise_summary = "\n" + "\n".join(lines)

        # Generate next-session targets (progressive overload suggestion)
        next_targets_text = ""
        if session_sets:
            try:
                from claude_service import generate_next_session_targets
                profile = user.get("profile") or {}
                plan_day_ctx = None
                if user.get("last_plan"):
                    today_name_end = _date.today().strftime("%A")
                    days_ctx = user["last_plan"].get("workout", {}).get("days", [])
                    plan_day_ctx = next(
                        (d for d in days_ctx if d.get("day", "").lower() == today_name_end.lower()),
                        None,
                    )
                targets = await asyncio.get_event_loop().run_in_executor(
                    None, generate_next_session_targets, session_sets, plan_day_ctx, profile
                )
                next_targets_text = f"\n\n🎯 *Next session targets:*\n_{targets}_"
            except Exception:
                pass

        await update.message.reply_text(
            f"✅ *Session #{sid} complete!*\n\n"
            f"Sets: {total_sets} | Volume: {_w(total_volume, user):,.0f}{_wu(user)}{exercise_summary}"
            f"{next_targets_text}\n\n"
            "Type /stats to see your PRs.",
            parse_mode="Markdown",
        )

        # Freemium nudge: after 7th session suggest upgrading for reports
        if user.get("session_counter", 0) % 7 == 0 and user.get("subscription_tier", "free") == "free":
            await update.message.reply_text(
                "📊 *7 sessions logged!*\n\n"
                "Pro members get a weekly AI coaching report that summarizes your progress, "
                "spots plateaus, and prioritizes your training. \n\n"
                "Upgrade at the web app (Settings → Billing) to unlock it 🚀",
                parse_mode="Markdown",
            )

    else:
        # Show today's planned workout
        if not user["last_plan"]:
            has_session = user["active_session_id"] is not None
            await update.message.reply_text(
                "No plan yet — run /plan to generate one, or tap below to start a free session.",
                reply_markup=_ex_keyboard([], has_session=has_session),
            )
            return
        today_name = _date.today().strftime("%A")
        days = user["last_plan"].get("workout", {}).get("days", [])
        today_day = next((d for d in days if d.get("day", "").lower() == today_name.lower()), None)
        if not today_day:
            has_session = user["active_session_id"] is not None
            await update.message.reply_text(
                f"No session planned for {today_name} — rest day! Or start a free session:",
                reply_markup=_ex_keyboard([], has_session=has_session),
            )
            return
        exercises = today_day.get("exercises", [])
        lines = "\n".join(
            f"• {e['name']}: {e['sets']}×{e['reps']} | {e.get('notes', '')}".rstrip(" |")
            for e in exercises
        )
        has_session = user["active_session_id"] is not None
        status = f"🔴 Session open (#{user['active_session_id']})" if has_session else "⚪ No open session"
        await update.message.reply_text(
            f"🏋️ *{today_name} — {today_day.get('focus', '')}*\n{status}\n\n{lines}",
            parse_mode="Markdown",
            reply_markup=_ex_keyboard(exercises, has_session=has_session),
        )


async def cmd_logset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage: `/logset <exercise> <weight> <reps>`\n"
            "Examples:\n"
            "`/logset bench 100kg 8`\n"
            "`/logset squat 225lbs 5`\n"
            "`/logset deadlift 140 3`  ← bare number = kg",
            parse_mode="Markdown",
        )
        return

    # Last arg = reps, second-to-last = weight, everything before = exercise name
    try:
        reps = int(context.args[-1])
    except ValueError:
        await update.message.reply_text("Last argument must be the number of reps (e.g. `8`).", parse_mode="Markdown")
        return

    weight_kg = _parse_logset_weight_kg(context.args[-2])
    if weight_kg is None:
        await update.message.reply_text("Could not parse weight. Use formats like `100kg`, `225lbs`, or bare `100`.", parse_mode="Markdown")
        return

    exercise = " ".join(context.args[:-2]).title()
    one_rm = _epley_1rm(weight_kg, reps)

    entry = {
        "exercise_name": exercise,
        "weight_kg": weight_kg,
        "reps": reps,
        "estimated_1rm": one_rm,
        "date": _today(),
        "session_id": user["active_session_id"],
    }
    user["set_logs"].append(entry)
    user["set_logs"] = user["set_logs"][-500:]  # cap at 500 sets

    # Track current session sets
    if user["active_session_id"] is not None:
        user["command_state"].setdefault("current_session_sets", []).append(entry)

    # Check for PR
    pr = user["prs"].get(exercise)
    is_pr = pr is None or one_rm > pr["estimated_1rm"]
    pr_text = ""
    if is_pr:
        user["prs"][exercise] = {"weight_kg": weight_kg, "reps": reps, "estimated_1rm": one_rm, "date": _today()}
        pr_text = "\n🏆 *New PR!*"

    _save_store()
    await update.message.reply_text(
        f"✅ *{exercise}* — {_wfmt(weight_kg, user)} × {reps} reps\n"
        f"1RM estimate: ~{_wfmt(one_rm, user)} (Epley){pr_text}",
        parse_mode="Markdown",
    )


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/log — quick shortcut: starts session if needed, then shows exercise picker."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if user["active_session_id"] is None:
        # Auto-start a new session
        user["session_counter"] += 1
        sid = user["session_counter"]
        user["active_session_id"] = sid
        user["command_state"]["current_session_sets"] = []
        _save_store()
        exercises = _get_session_exercises(user)
        await update.message.reply_text(
            f"🏋️ *Session #{sid} started!* Tap an exercise:",
            parse_mode="Markdown",
            reply_markup=_ex_keyboard(exercises, has_session=True),
        )
    else:
        exercises = _get_session_exercises(user)
        await update.message.reply_text(
            f"🏋️ Session #{user['active_session_id']} — Tap an exercise:",
            reply_markup=_ex_keyboard(exercises, has_session=True),
        )


async def handle_workout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles all wk: callback queries from inline workout keyboards."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    data: str = query.data  # starts with "wk:"
    part = data[3:]          # strip "wk:"

    if part == "start":
        if user["active_session_id"] is not None:
            exercises = _get_session_exercises(user)
            await query.edit_message_text(
                f"⚠️ Session #{user['active_session_id']} already open. Tap an exercise:",
                reply_markup=_ex_keyboard(exercises, has_session=True),
            )
            return
        user["session_counter"] += 1
        sid = user["session_counter"]
        user["active_session_id"] = sid
        user["command_state"]["current_session_sets"] = []
        _save_store()
        exercises = _get_session_exercises(user)
        await query.edit_message_text(
            f"🏋️ *Session #{sid} started!* Tap an exercise:",
            parse_mode="Markdown",
            reply_markup=_ex_keyboard(exercises, has_session=True),
        )

    elif part.startswith("ex:"):
        name = part[3:]
        context.user_data["wk_ex"] = name
        pr = user["prs"].get(name)
        pr_hint = f"\n_Current PR: {_wfmt(pr['weight_kg'], user)}×{pr['reps']} (~{_wfmt(pr['estimated_1rm'], user)} 1RM)_" if pr else ""
        await query.edit_message_text(
            f"*{name}*{pr_hint}\n\nSelect weight ({_wu(user)}):",
            parse_mode="Markdown",
            reply_markup=_weight_keyboard(name, user),
        )

    elif part == "ex_custom":
        context.user_data["wk_awaiting"] = "exercise"
        await query.edit_message_text("Type the exercise name:")

    elif part.startswith("w:"):
        val = part[2:]
        if val == "type":
            context.user_data["wk_awaiting"] = "weight"
            ex = context.user_data.get("wk_ex", "exercise")
            await query.edit_message_text(
                f"*{ex}* — Type weight in {_wu(user)} (e.g. `{'225' if _wu(user) == 'lbs' else '102.5'}`):",
                parse_mode="Markdown",
            )
        else:
            weight = float(val)
            context.user_data["wk_w"] = weight
            ex = context.user_data.get("wk_ex", "exercise")
            await query.edit_message_text(
                f"*{ex}* — {_wfmt(weight, user)}\n\nSelect reps:",
                parse_mode="Markdown",
                reply_markup=_rep_keyboard(),
            )

    elif part.startswith("r:"):
        val = part[2:]
        if val == "type":
            context.user_data["wk_awaiting"] = "reps"
            ex = context.user_data.get("wk_ex", "?")
            w = context.user_data.get("wk_w", "?")
            await query.edit_message_text(
                f"*{ex}* @ {_wfmt(w, user) if isinstance(w, (int, float)) else w} — Type reps:",
                parse_mode="Markdown",
            )
        else:
            reps = int(val)
            ex = context.user_data.get("wk_ex")
            weight = context.user_data.get("wk_w")
            if not ex or weight is None:
                await query.edit_message_text("Session state lost. Start over with /log.")
                return
            entry, is_pr = _do_log_set(user, ex, weight, reps)
            pr_badge = " 🏆 *NEW PR!*" if is_pr else ""
            await query.edit_message_text(
                f"✅ *{ex}* — {_wfmt(weight, user)} × {reps}{pr_badge}\n"
                f"Est. 1RM: ~{_wfmt(entry['estimated_1rm'], user)}",
                parse_mode="Markdown",
                reply_markup=_after_set_keyboard(),
            )

    elif part == "more":
        ex = context.user_data.get("wk_ex", "")
        if not ex:
            exercises = _get_session_exercises(user)
            await query.edit_message_text(
                "Pick an exercise:",
                reply_markup=_ex_keyboard(exercises, has_session=user["active_session_id"] is not None),
            )
            return
        await query.edit_message_text(
            f"*{ex}* — Select weight:",
            parse_mode="Markdown",
            reply_markup=_weight_keyboard(ex, user),
        )

    elif part == "pick":
        exercises = _get_session_exercises(user)
        await query.edit_message_text(
            "Select exercise:",
            reply_markup=_ex_keyboard(exercises, has_session=user["active_session_id"] is not None),
        )

    elif part == "days":
        if not user.get("last_plan"):
            await query.answer("No plan loaded — generate one with /plan first.", show_alert=True)
            return
        await query.edit_message_text(
            "Pick which day to train:",
            reply_markup=_days_keyboard(user),
        )

    elif part.startswith("day:"):
        day_name = part[4:]
        user["command_state"]["active_session_day"] = day_name
        _save_store()
        exercises = _get_session_exercises(user)
        await query.edit_message_text(
            f"📅 *{day_name}* — Tap an exercise:",
            parse_mode="Markdown",
            reply_markup=_ex_keyboard(exercises, has_session=user["active_session_id"] is not None),
        )

    elif part == "end":
        sid = user["active_session_id"]
        if sid is None:
            await query.edit_message_text("No open session. Use /log to start one.")
            return
        session_sets = user["command_state"].get("current_session_sets", [])
        total_volume = sum(s["weight_kg"] * s["reps"] for s in session_sets)
        total_sets = len(session_sets)
        user["active_session_id"] = None
        user["command_state"]["current_session_sets"] = []
        user["command_state"].pop("active_session_day", None)
        _save_store()

        ex_summary = ""
        if session_sets:
            by_ex: dict[str, list] = {}
            for s in session_sets:
                by_ex.setdefault(s["exercise_name"], []).append(s)
            lines = []
            for ex_name, sets in by_ex.items():
                best = max(sets, key=lambda x: x["estimated_1rm"])
                lines.append(f"  {ex_name}: {len(sets)} sets | best {_wfmt(best['weight_kg'], user)}×{best['reps']}")
            ex_summary = "\n" + "\n".join(lines)

        await query.edit_message_text(
            f"✅ *Session #{sid} complete!*\n\n"
            f"Sets: {total_sets} | Volume: {_w(total_volume, user):,.0f}{_wu(user)}{ex_summary}\n\n"
            "Type /stats to see your PRs.",
            parse_mode="Markdown",
        )


async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    lines = ["📈 *Your Progress*\n"]

    # Weight trend from measurements
    weights = [(m["date"], m["body_weight_kg"]) for m in user["measurements"] if m.get("body_weight_kg")]
    if len(weights) >= 2:
        first_w, last_w = weights[0][1], weights[-1][1]
        diff = round(last_w - first_w, 1)
        arrow = "▼" if diff < 0 else ("▲" if diff > 0 else "→")
        spark = _sparkline([w[1] for w in weights[-10:]])
        lines.append(f"*Weight:* {_wfmt(first_w, user)} → {_wfmt(last_w, user)} ({arrow} {_wfmt(abs(diff), user)}) {spark}")

    # Recovery trend
    recent_checkins = user["checkins"][-30:]
    if recent_checkins:
        avg_rec = round(sum(c["recovery_score"] for c in recent_checkins) / len(recent_checkins))
        rec_spark = _sparkline([c["recovery_score"] for c in recent_checkins[-10:]])
        lines.append(f"*Avg Recovery (30d):* {avg_rec}/100 {rec_spark}")

    # Top PRs
    if user["prs"]:
        pr_lines = sorted(user["prs"].items(), key=lambda x: x[1]["estimated_1rm"], reverse=True)[:3]
        pr_text = "\n".join(
            f"  {ex}: {_wfmt(v['weight_kg'], user)}×{v['reps']} (1RM ~{_wfmt(v['estimated_1rm'], user)})"
            for ex, v in pr_lines
        )
        lines.append(f"\n*Top PRs:*\n{pr_text}")

    # Sessions count
    lines.append(f"\n*Sessions logged:* {user['session_counter']}")

    if len(lines) == 2:
        lines.append("\nLog workouts with `/workout start` + `/logset`, check in daily with `/checkin`, and track weight with `/measurements weight=83kg`.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_measurements(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    FIELDS = {
        "weight": "body_weight_kg",
        "waist": "waist_cm",
        "chest": "chest_cm",
        "hips": "hips_cm",
        "arm": "left_arm_cm",
        "leftarm": "left_arm_cm",
        "rightarm": "right_arm_cm",
        "thigh": "left_thigh_cm",
        "leftthigh": "left_thigh_cm",
        "rightthigh": "right_thigh_cm",
    }

    if not context.args:
        last = user["measurements"][-1] if user["measurements"] else None
        if last:
            vals = "\n".join(
                f"• {k}: {v}"
                for k, v in last.items()
                if k not in ("date",) and v is not None
            )
            current = f"Last entry ({last['date']}):\n{vals}"
        else:
            current = "No measurements yet."

        await update.message.reply_text(
            f"📏 *Measurements*\n{current}\n\n"
            "Log measurements (supports lbs/kg and in/cm):\n"
            "`/measurements weight=83kg waist=32in chest=42in arm=16in`",
            parse_mode="Markdown",
        )
        return

    entry: dict = {"date": _today()}
    for arg in context.args:
        if "=" not in arg:
            continue
        k, _, v = arg.partition("=")
        k = k.strip().lower().replace("-", "").replace("_", "")
        v = v.strip()
        db_key = FIELDS.get(k)
        if not db_key:
            continue
        if db_key == "body_weight_kg":
            val = _parse_logset_weight_kg(v)
        else:
            val = _parse_measurement_cm(v)
        if val is not None:
            entry[db_key] = val

    if len(entry) <= 1:
        await update.message.reply_text("No valid measurements found. Example: `/measurements weight=83kg waist=32in`", parse_mode="Markdown")
        return

    # Compare vs previous
    prev = user["measurements"][-1] if user["measurements"] else None
    user["measurements"].append(entry)
    user["measurements"] = user["measurements"][-100:]
    _save_store()

    lines = [f"✅ *Measurements saved ({_today()})*\n"]
    for db_key, val in entry.items():
        if db_key == "date":
            continue
        label = db_key.replace("_cm", "").replace("_kg", "").replace("_", " ").title()
        unit = "kg" if db_key == "body_weight_kg" else "cm"
        change = ""
        if prev and db_key in prev and prev[db_key] is not None:
            diff = round(val - prev[db_key], 1)
            if diff != 0:
                arrow = "▲" if diff > 0 else "▼"
                change = f" ({arrow} {abs(diff)}{unit})"
        lines.append(f"• {label}: {val}{unit}{change}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if not context.args:
        await update.message.reply_text(
            "Log a meal:\n`/meal 2 eggs, 1 cup oatmeal, banana`\n`/meal 200g chicken breast 1 cup rice broccoli`\n\n"
            "Log MFP daily totals:\n`/meal total calories=1750 protein=140 carbs=205 fat=43`",
            parse_mode="Markdown",
        )
        return

    # /meal total calories=1750 protein=140 carbs=205 fat=43
    if context.args[0].lower() == "total":
        aliases = {
            "cal": "calories", "kcal": "calories",
            "p": "protein", "prot": "protein",
            "c": "carbs", "carb": "carbohydrates", "carbohydrates": "carbs",
            "f": "fat", "fats": "fat",
        }
        totals: dict[str, float] = {}
        for arg in context.args[1:]:
            if "=" not in arg:
                continue
            k, _, v = arg.partition("=")
            k = k.strip().lower()
            k = aliases.get(k, k)
            try:
                totals[k] = float(v.strip())
            except ValueError:
                pass
        if not totals:
            await update.message.reply_text(
                "Usage: `/meal total calories=1750 protein=140 carbs=205 fat=43`\n"
                "Shortcuts: `cal=`, `p=`, `c=`, `f=` also work.",
                parse_mode="Markdown",
            )
            return
        entry = {
            "date": _today(),
            "description": "Daily totals (MFP)",
            "calories": round(totals.get("calories", 0)),
            "protein_g": round(totals.get("protein", 0), 1),
            "carbs_g": round(totals.get("carbs", 0), 1),
            "fat_g": round(totals.get("fat", 0), 1),
            "macro_source": "manual_total",
        }
        user["meal_logs"].append(entry)
        user["meal_logs"] = user["meal_logs"][-200:]
        _save_store()
        today_meals = [m for m in user["meal_logs"] if m["date"] == _today()]
        day_cals = sum(m["calories"] for m in today_meals)
        day_protein = round(sum(m["protein_g"] for m in today_meals), 1)
        await update.message.reply_text(
            f"✅ *Daily totals logged!*\n"
            f"{entry['calories']} kcal | P: {entry['protein_g']}g | C: {entry['carbs_g']}g | F: {entry['fat_g']}g\n\n"
            f"Today so far: *{day_cals} kcal* | Protein: *{day_protein}g*\n"
            "Run /macros to see full targets vs. logged.",
            parse_mode="Markdown",
        )
        return

    description = " ".join(context.args)
    msg = await update.message.reply_text("🍽️ Looking up macros…")

    try:
        from nutrition_service import lookup_food_macros
        macros = await lookup_food_macros(description)
    except Exception as e:
        await msg.edit_text(f"❌ Could not estimate macros: {e}")
        return

    entry = {
        "date": _today(),
        "description": description,
        "calories": macros.get("calories", 0),
        "protein_g": macros.get("protein_g", 0),
        "carbs_g": macros.get("carbs_g", 0),
        "fat_g": macros.get("fat_g", 0),
        "macro_source": macros.get("source", "estimated"),
    }
    user["meal_logs"].append(entry)
    # Keep last 200 entries
    user["meal_logs"] = user["meal_logs"][-200:]
    _save_store()

    today_meals = [m for m in user["meal_logs"] if m["date"] == _today()]
    day_cals = sum(m["calories"] for m in today_meals)
    day_protein = sum(m["protein_g"] for m in today_meals)

    items_text = ""
    items = macros.get("items", [])
    if items:
        items_text = f"\n_{', '.join(items)}_\n"

    source_note = "" if macros.get("source") == "nutritionix" else " _(estimated)_"
    await msg.edit_text(
        f"✅ *Meal logged!*{items_text}\n"
        f"{macros['calories']} kcal | P: {macros['protein_g']}g | C: {macros['carbs_g']}g | F: {macros['fat_g']}g{source_note}\n\n"
        f"Today so far: *{day_cals} kcal* | Protein: *{day_protein}g*\n"
        "Run /macros to see full targets vs. logged.",
        parse_mode="Markdown",
    )


async def cmd_macros(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    plan = user["last_plan"]
    diet = plan.get("diet", {}) if plan else {}
    target_cal = diet.get("calories")
    target_p = diet.get("protein_g")
    target_c = diet.get("carbs_g")
    target_f = diet.get("fat_g")

    today_meals = [m for m in user["meal_logs"] if m["date"] == _today()]
    logged_cal = round(sum(m["calories"] for m in today_meals))
    logged_p = round(sum(m["protein_g"] for m in today_meals), 1)
    logged_c = round(sum(m["carbs_g"] for m in today_meals), 1)
    logged_f = round(sum(m["fat_g"] for m in today_meals), 1)

    def row(label: str, logged, target) -> str:
        if target:
            left = round(target - logged, 1)
            bar_pct = min(1.0, logged / target)
            bar = "█" * round(bar_pct * 8) + "░" * (8 - round(bar_pct * 8))
            return f"{label:<10} {str(logged):>6} / {str(target):<6}  [{bar}]  left: {left}"
        return f"{label:<10} {str(logged):>6}"

    table = (
        f"`{row('Calories', logged_cal, target_cal)}`\n"
        f"`{row('Protein g', logged_p, target_p)}`\n"
        f"`{row('Carbs g', logged_c, target_c)}`\n"
        f"`{row('Fat g', logged_f, target_f)}`"
    )

    tip = ""
    if target_p and logged_p < target_p * 0.4:
        tip = "\n\n⚠️ Protein is low — add a high-protein meal or shake."
    elif not plan:
        tip = "\n\nRun /plan to set your macro targets."

    await update.message.reply_text(
        f"🥗 *Today's Macros*\n\n{table}{tip}",
        parse_mode="Markdown",
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    filter_ex = " ".join(context.args).lower() if context.args else None

    if not user["prs"]:
        await update.message.reply_text(
            "No PRs yet. Log sets with `/logset bench 100kg 8` during a `/workout start` session.",
            parse_mode="Markdown",
        )
        return

    prs = user["prs"]
    if filter_ex:
        prs = {k: v for k, v in prs.items() if filter_ex in k.lower()}
        if not prs:
            await update.message.reply_text(f"No PRs found for '{filter_ex}'.")
            return

    sorted_prs = sorted(prs.items(), key=lambda x: x[1]["estimated_1rm"], reverse=True)
    pr_lines = "\n".join(
        f"*{ex}:* {_wfmt(v['weight_kg'], user)} × {v['reps']} reps  (1RM ~{_wfmt(v['estimated_1rm'], user)})  📅 {v['date']}"
        for ex, v in sorted_prs
    )

    # Volume per muscle group (last 30 days)
    cutoff = str(_date.today().replace(day=max(1, _date.today().day - 30)))
    recent_sets = [s for s in user["set_logs"] if s.get("date", "") >= cutoff]
    muscle_map = {
        "chest": ["bench", "chest", "fly", "push"],
        "back": ["row", "pulldown", "pull-up", "deadlift", "back"],
        "shoulders": ["press", "lateral", "shoulder", "ohp"],
        "arms": ["curl", "tricep", "bicep", "extension"],
        "legs": ["squat", "lunge", "leg press", "leg curl", "leg extension", "calf"],
        "core": ["plank", "crunch", "ab", "core"],
    }
    vol: dict[str, int] = {}
    for s in recent_sets:
        name = s["exercise_name"].lower()
        for muscle, keywords in muscle_map.items():
            if any(k in name for k in keywords):
                vol[muscle] = vol.get(muscle, 0) + 1
                break

    vol_text = ""
    if vol:
        max_sets = max(vol.values())
        vol_lines = "\n".join(
            f"  {m.capitalize()}: {'█' * round(v / max_sets * 8)}{'░' * (8 - round(v / max_sets * 8))} {v} sets"
            for m, v in sorted(vol.items(), key=lambda x: x[1], reverse=True)
        )
        vol_text = f"\n\n*Volume last 30d by muscle:*\n{vol_lines}"

    await update.message.reply_text(
        f"🏆 *Personal Records*\n\n{pr_lines}{vol_text}",
        parse_mode="Markdown",
    )


async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if not context.args:
        reminders = user["reminders"]
        if not reminders:
            await update.message.reply_text(
                "⏰ *Reminders*\nNone set.\n\n"
                "Set a reminder:\n"
                "`/reminders workout 7am`\n"
                "`/reminders checkin 9pm`\n"
                "`/reminders off workout`",
                parse_mode="Markdown",
            )
            return
        lines = "\n".join(
            f"✅ {k.capitalize()}: {v['hour']:02d}:{v['minute']:02d}"
            for k, v in reminders.items()
        )
        await update.message.reply_text(f"⏰ *Your Reminders:*\n{lines}", parse_mode="Markdown")
        return

    if context.args[0].lower() == "off" and len(context.args) >= 2:
        kind = context.args[1].lower()
        job_id = f"{kind}_{chat_id}"
        job = _scheduler.get_job(job_id)
        if job:
            job.remove()
        user["reminders"].pop(kind, None)
        _save_store()
        await update.message.reply_text(f"🔕 {kind.capitalize()} reminder removed.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/reminders workout 7am` or `/reminders off workout`", parse_mode="Markdown")
        return

    kind = context.args[0].lower()
    time_str = context.args[1].lower().replace(".", ":")
    # Parse time: "7am" "7:30am" "19:00" "7pm"
    hour, minute = 8, 0
    m = re.match(r"(\d{1,2})(?::(\d{2}))?([ap]m)?$", time_str)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0

    MESSAGES = {
        "workout": "🏋️ Time to train! Type /workout to see today's session.",
        "checkin": "📋 Daily check-in time! How are you recovering? /checkin",
        "meal": "🥗 Don't forget to log your last meal! /meal",
    }
    message_text = MESSAGES.get(kind, f"⏰ {kind.capitalize()} reminder!")

    job_id = f"{kind}_{chat_id}"
    existing = _scheduler.get_job(job_id)
    if existing:
        existing.remove()

    _scheduler.add_job(
        _send_reminder,
        trigger="cron",
        hour=hour,
        minute=minute,
        args=[chat_id, message_text],
        id=job_id,
        replace_existing=True,
    )

    user["reminders"][kind] = {"hour": hour, "minute": minute}
    _save_store()
    await update.message.reply_text(
        f"✅ {kind.capitalize()} reminder set for *{hour:02d}:{minute:02d}* daily.",
        parse_mode="Markdown",
    )


async def _send_reminder(chat_id: int, message: str) -> None:
    global _app
    if _app is None:
        return
    try:
        await _app.bot.send_message(chat_id=chat_id, text=message)
    except Exception as e:
        print(f"Reminder failed for {chat_id}: {e}")


async def cmd_connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if not context.args:
        garmin_status = "✅ Connected" if user.get("garmin_email") else "❌ Not connected"
        mfp_status = "✅ Connected" if user.get("mfp_username") else "❌ Not connected"
        await update.message.reply_text(
            "*Connected Services:*\n"
            f"• Garmin: {garmin_status}\n"
            f"• MyFitnessPal: {mfp_status}\n\n"
            "Connect:\n"
            "`/connect garmin` — auto-fills sleep & HRV in /checkin\n"
            "`/connect mfp` — enables `/mfp sync` to import your diary",
            parse_mode="Markdown",
        )
        return

    service = context.args[0].lower()
    if service not in ("garmin", "mfp"):
        await update.message.reply_text(
            "Unknown service. Use: `/connect garmin` or `/connect mfp`",
            parse_mode="Markdown",
        )
        return

    user["active_command"] = f"connect_{service}"
    user["command_state"] = {"step": 0}

    if service == "garmin":
        await update.message.reply_text(
            "Enter your *Garmin Connect email:*\n_(stored encrypted — only used to fetch your sleep & HRV)_",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "Enter your *MyFitnessPal username:*\n_(stored encrypted — only used to fetch your diary)_",
            parse_mode="Markdown",
        )


async def _handle_connect_step(update: Update, user: dict, text: str) -> None:
    active = user["active_command"]   # "connect_garmin" or "connect_mfp"
    state = user["command_state"]
    chat_id = update.effective_chat.id
    text = text.strip()

    if active == "connect_garmin":
        if state["step"] == 0:
            state["garmin_email_pending"] = text
            state["step"] = 1
            await update.message.reply_text(
                "Enter your *Garmin Connect password:*\n_(will be stored encrypted)_",
                parse_mode="Markdown",
            )
        elif state["step"] == 1:
            email = state.get("garmin_email_pending", "")
            msg = await update.message.reply_text("Testing Garmin connection…")
            try:
                import garmin_service
                from crypto_utils import encrypt
                garmin_service.test_login(email, text)
                enc_pass = encrypt(text)
                user["garmin_email"] = email
                user["garmin_pass_enc"] = enc_pass
                user["active_command"] = None
                _save_store()
                await msg.edit_text("✅ *Garmin connected!* Fetching your latest data…", parse_mode="Markdown")
                try:
                    garmin_service.fetch_and_cache(chat_id, email, enc_pass)
                    await update.message.reply_text(
                        "Data synced! Sleep & HRV will now auto-fill /checkin each morning."
                    )
                except Exception as sync_err:
                    await update.message.reply_text(
                        f"Connected, but couldn't fetch data yet: {sync_err}\n"
                        "This will retry automatically at 6am."
                    )
            except Exception as e:
                user["active_command"] = None
                await msg.edit_text(
                    f"❌ Couldn't connect to Garmin: {e}\n"
                    "Check your credentials and try `/connect garmin` again.",
                    parse_mode="Markdown",
                )

    elif active == "connect_mfp":
        if state["step"] == 0:
            state["mfp_username_pending"] = text
            state["step"] = 1
            await update.message.reply_text(
                "Enter your *MyFitnessPal password:*\n_(will be stored encrypted)_",
                parse_mode="Markdown",
            )
        elif state["step"] == 1:
            username = state.get("mfp_username_pending", "")
            msg = await update.message.reply_text("Testing MFP connection…")
            try:
                import mfp_service
                from crypto_utils import encrypt
                mfp_service.test_login(username, text)
                enc_pass = encrypt(text)
                user["mfp_username"] = username
                user["mfp_pass_enc"] = enc_pass
                user["active_command"] = None
                _save_store()
                await msg.edit_text(
                    "✅ *MyFitnessPal connected!* Use `/mfp sync` to import today's diary.",
                    parse_mode="Markdown",
                )
            except Exception as e:
                user["active_command"] = None
                await msg.edit_text(
                    f"❌ Couldn't connect to MFP: {e}\n"
                    "Check your credentials and try `/connect mfp` again.",
                    parse_mode="Markdown",
                )


async def cmd_mfp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    sub = context.args[0].lower() if context.args else "sync"

    if sub == "sync":
        if not user.get("mfp_username"):
            await update.message.reply_text(
                "MyFitnessPal not connected. Run `/connect mfp` first.",
                parse_mode="Markdown",
            )
            return
        msg = await update.message.reply_text("Syncing MFP diary…")
        try:
            import mfp_service
            meals = mfp_service.fetch_today(user["mfp_username"], user["mfp_pass_enc"])
            for m in meals:
                m["date"] = _today()
            user["meal_logs"].extend(meals)
            user["meal_logs"] = user["meal_logs"][-200:]
            _save_store()
            today_cals = sum(m["calories"] for m in user["meal_logs"] if m["date"] == _today())
            today_protein = round(sum(m["protein_g"] for m in user["meal_logs"] if m["date"] == _today()), 1)
            await msg.edit_text(
                f"✅ *MFP synced!* {len(meals)} meal(s) imported.\n"
                f"Today: {today_cals} kcal | Protein: {today_protein}g\n"
                "Run /macros for the full breakdown.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await msg.edit_text(
                f"❌ MFP sync failed: {e}\n"
                "You can still log manually with `/meal total calories=1750 protein=140 carbs=205 fat=43`",
                parse_mode="Markdown",
            )
    else:
        await update.message.reply_text(
            "Usage: `/mfp sync` — import today's MFP diary\n\nNot connected? Run `/connect mfp`",
            parse_mode="Markdown",
        )


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("🔬 Fetching latest PubMed research…")
    try:
        summaries = await _fetch_research_summaries()
        text = "*Latest Research Highlights*\n\n" + "\n\n".join(summaries)
        await msg.edit_text(text[:4096], parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Research fetch failed: {e}")


async def cmd_streak(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    checkins = user["checkins"]
    if not checkins:
        await update.message.reply_text(
            "No streak yet — do your first /checkin to start one! 🔥"
        )
        return

    # Calculate streak from local checkin cache
    sorted_dates = sorted({c["date"] for c in checkins}, reverse=True)
    today_str = _today()
    current = 0
    longest = 0
    run = 0
    prev: _date | None = None
    for d in reversed(sorted_dates):
        dt = _date.fromisoformat(d)
        if prev is None or (dt - prev).days == 1:
            run += 1
        else:
            run = 1
        longest = max(longest, run)
        prev = dt

    # current streak: count back from today or yesterday
    current = 0
    for d in sorted_dates:
        dt = _date.fromisoformat(d)
        expected = _date.today() - timedelta(days=current)
        if dt == expected:
            current += 1
        else:
            break

    badge_emojis = {7: "🥉", 14: "🥈", 30: "🥇", 60: "💎", 90: "👑"}
    badges_earned = [f"{badge_emojis[n]} {n}-day streak" for n in badge_emojis if longest >= n]
    badge_text = "\n" + "\n".join(badges_earned) if badges_earned else ""

    flame = "🔥" if current >= 3 else ("✅" if current >= 1 else "💤")
    await update.message.reply_text(
        f"{flame} *Your Streaks*\n\n"
        f"Check-in streak: *{current} days* (longest: {longest})\n"
        f"Total check-ins: {len(checkins)}{badge_text}\n\n"
        f"Keep it going — /checkin to extend your streak!",
        parse_mode="Markdown",
    )


async def cmd_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick weight log: /weight 84.5"""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if not context.args:
        last = next((m for m in reversed(user["measurements"]) if m.get("body_weight_kg")), None)
        last_str = f"\nLast logged: {_wfmt(last['body_weight_kg'], user)} on {last['date']}" if last else ""
        await update.message.reply_text(
            f"Usage: `/weight {'186' if _wu(user) == 'lbs' else '84.5'}`{last_str}",
            parse_mode="Markdown",
        )
        return

    weight_kg = _parse_weight_input(context.args[0], user)
    if weight_kg is None:
        await update.message.reply_text(f"Couldn't parse weight. Try `/weight {'186' if _wu(user) == 'lbs' else '84.5'}` or explicit units like `186lbs` or `84.5kg`.", parse_mode="Markdown")
        return

    entry = {"date": _today(), "body_weight_kg": weight_kg}
    user["measurements"].append(entry)
    user["measurements"] = user["measurements"][-100:]
    _save_store()

    # Compare to previous weight log
    prev_weights = [m for m in user["measurements"][:-1] if m.get("body_weight_kg")]
    change_text = ""
    diff = 0.0
    if prev_weights:
        prev = prev_weights[-1]["body_weight_kg"]
        diff = round(weight_kg - prev, 3)
        if diff != 0:
            arrow = "▲" if diff > 0 else "▼"
            change_text = f" ({arrow} {_wfmt(abs(diff), user)} from last log)"

    goal = user["profile"].get("goal", "")
    motivation = ""
    if goal == "cut" and diff < 0:
        motivation = " Keep it up! 📉"
    elif goal == "bulk" and diff > 0:
        motivation = " Gaining! 📈"

    await update.message.reply_text(
        f"✅ *{_wfmt(weight_kg, user)} logged*{change_text}{motivation}\n"
        f"Track more with `/measurements weight={'186lbs' if _wu(user) == 'lbs' else '84.5kg'} waist=32in`",
        parse_mode="Markdown",
    )


async def cmd_goals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if not context.args:
        goals_data = user.get("goals", [])
        active = [g for g in goals_data if g.get("is_active")]
        if not active:
            await update.message.reply_text(
                "🎯 *Goals*\nNo active goal set.\n\n"
                "Set one:\n"
                "`/goals set cut 10%bf by 2026-09-01`\n"
                "`/goals set bulk 90kg by 2026-12-01`\n"
                "`/goals set strength`",
                parse_mode="Markdown",
            )
            return
        g = active[-1]
        target_parts = []
        if g.get("target_weight_kg"):
            target_parts.append(f"Weight: {g['target_weight_kg']}kg")
        if g.get("target_bf_pct"):
            target_parts.append(f"Body fat: {g['target_bf_pct']}%")
        if g.get("target_date"):
            from datetime import date as _date_cls
            days_left = (_date_cls.fromisoformat(g["target_date"]) - _date_cls.today()).days
            target_parts.append(f"Date: {g['target_date']} ({days_left} days away)")
        target_str = " | ".join(target_parts) if target_parts else "No specific target"
        await update.message.reply_text(
            f"🎯 *Active Goal: {g['goal_type'].title()}*\n{target_str}\n\n"
            "Update with `/goals set <type> <target>`",
            parse_mode="Markdown",
        )
        return

    if context.args[0].lower() == "set" and len(context.args) >= 2:
        goal_type = context.args[1].lower()
        target_weight_kg = None
        target_bf_pct = None
        target_date = None

        for arg in context.args[2:]:
            if arg.endswith("%bf") or arg.endswith("%"):
                try:
                    target_bf_pct = float(arg.rstrip("%bf").rstrip("%"))
                except ValueError:
                    pass
            elif "kg" in arg or (arg.replace(".", "").isdigit() and "." in arg):
                try:
                    target_weight_kg = float(arg.replace("kg", ""))
                except ValueError:
                    pass
            elif re.match(r"\d{4}-\d{2}-\d{2}", arg):
                target_date = arg

        # Deactivate previous
        for g in user.get("goals", []):
            if g.get("goal_type") == goal_type:
                g["is_active"] = False

        new_goal = {
            "goal_type": goal_type,
            "target_weight_kg": target_weight_kg,
            "target_bf_pct": target_bf_pct,
            "target_date": target_date,
            "start_weight_kg": float(user["profile"].get("weight", 0) or 0) or None,
            "created_at": _today(),
            "is_active": True,
        }
        user.setdefault("goals", []).append(new_goal)
        _save_store()

        parts = [f"Type: {goal_type}"]
        if target_weight_kg:
            parts.append(f"Target weight: {target_weight_kg}kg")
        if target_bf_pct:
            parts.append(f"Target body fat: {target_bf_pct}%")
        if target_date:
            parts.append(f"Target date: {target_date}")
        await update.message.reply_text(
            "🎯 *Goal set!*\n" + "\n".join(f"• {p}" for p in parts),
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "Usage: `/goals set <type> [target] [date]`\n"
            "Examples:\n"
            "`/goals set cut 10%bf by 2026-09-01`\n"
            "`/goals set bulk 90kg`\n"
            "`/goals set recomp`",
            parse_mode="Markdown",
        )


async def cmd_weakpoints(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if not user["last_analysis"]:
        await update.message.reply_text(
            "No physique analysis yet. Send a photo first and I'll identify your weak points. 📸"
        )
        return

    msg = await update.message.reply_text("🔬 Analyzing training imbalances…")
    try:
        from claude_service import analyze_weak_points
        analyses = [user["last_analysis"]]
        # Last 30 days of set logs
        cutoff = str(_date.today().replace(day=max(1, _date.today().day - 30)))
        recent_sets = [s for s in user["set_logs"] if s.get("date", "") >= cutoff]
        result = await asyncio.run_in_executor(
            None, analyze_weak_points, analyses, recent_sets, user["profile"] or None
        )
        weak_pts = "\n".join(f"• {w}" for w in result.get("weak_points", []))
        vol_recs = result.get("volume_recommendations", {})
        vol_text = "\n".join(f"• {m.capitalize()}: {rec}" for m, rec in vol_recs.items()) if vol_recs else ""
        priority = result.get("priority_fix", "")

        reply = f"📊 *Weak Point Analysis*\n\n"
        if weak_pts:
            reply += f"*Current Imbalances:*\n{weak_pts}\n\n"
        if vol_text:
            reply += f"*Volume Adjustments:*\n{vol_text}\n\n"
        if priority:
            reply += f"🎯 *#1 Priority Fix:* {priority}"

        await msg.edit_text(reply, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Weak point analysis failed: {e}")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    msg = await update.message.reply_text("📊 Generating your weekly report…")
    try:
        from claude_service import generate_weekly_report
        from datetime import timedelta

        cutoff = str(_date.today() - timedelta(days=7))
        recent_sessions_count = user.get("session_counter", 0)
        recent_checkins = [c for c in user["checkins"] if c.get("date", "") >= cutoff]
        recent_meals = [m for m in user["meal_logs"] if m.get("date", "") >= cutoff]
        recent_sets = [s for s in user["set_logs"] if s.get("date", "") >= cutoff]

        # Top PRs as a list
        prs_list = [
            {"exercise_name": ex, "weight_kg": v["weight_kg"], "reps": v["reps"]}
            for ex, v in user["prs"].items()
        ]

        sessions_data = [{"id": i} for i in range(recent_sessions_count)][:7]
        checkins_data = [{"recovery_score": c.get("recovery_score", 0)} for c in recent_checkins]
        meals_data = [{"protein_g": m.get("protein_g", 0)} for m in recent_meals]

        result = await asyncio.get_event_loop().run_in_executor(
            None, generate_weekly_report, sessions_data, checkins_data, meals_data, prs_list, user["profile"] or None
        )

        insights = result.get("insights", [])
        focus = result.get("next_week_focus", "")
        adherence = result.get("adherence_rating", "")
        avg_rec = result.get("avg_recovery")
        avg_prot = result.get("avg_protein_g")

        insights_text = "\n".join(f"• {i}" for i in insights) if insights else "No insights generated."

        reply = (
            f"📊 *Weekly Report*\n\n"
            f"Sessions: {len(sessions_data)} | Check-ins: {len(recent_checkins)}\n"
        )
        if avg_rec:
            reply += f"Avg recovery: {avg_rec}/100\n"
        if avg_prot:
            reply += f"Avg protein: {avg_prot}g/day\n"
        reply += f"\n*Insights:*\n{insights_text}\n\n"
        if focus:
            reply += f"🎯 *Next Week Focus:* {focus}\n"
        if adherence:
            reply += f"📈 *Adherence:* {adherence}"

        await msg.edit_text(reply, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Report generation failed: {e}")


async def cmd_billing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show subscription tier and upgrade info."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    tier = user.get("subscription_tier", "free")

    tier_labels = {"free": "Free", "pro": "Pro ($19.99/mo)", "elite": "Elite ($49.99/mo)"}
    tier_label = tier_labels.get(tier, tier.capitalize())

    pro_features = [
        "Unlimited AI photo analyses",
        "Weekly AI coaching report (/report)",
        "Garmin / MFP sync",
        "Progressive overload suggestions after sessions",
        "Weak-point analysis (/weakpoints)",
        "Meal logging with macro lookup",
        "Full bot access (all commands)",
    ]
    elite_extras = [
        "Daily AI coaching messages",
        "Competition prep mode",
        "Before/after photo comparison",
        "PDF progress reports",
        "Priority analysis queue",
    ]

    if tier == "free":
        pro_list = "\n".join(f"✅ {f}" for f in pro_features)
        await update.message.reply_text(
            f"💳 *Your Plan: {tier_label}*\n\n"
            f"*Pro features you're missing:*\n{pro_list}\n\n"
            f"*Upgrade to Pro — $19.99/month*\n"
            f"Visit the web app → Profile → Billing to upgrade.",
            parse_mode="Markdown",
        )
    elif tier == "pro":
        elite_list = "\n".join(f"✅ {f}" for f in elite_extras)
        await update.message.reply_text(
            f"💳 *Your Plan: {tier_label}*\n\n"
            f"*Elite extras available:*\n{elite_list}\n\n"
            f"Visit the web app → Profile → Billing to upgrade to Elite.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"💳 *Your Plan: {tier_label}*\n\nYou have full access to all features. Thank you! 🏆",
            parse_mode="Markdown",
        )


async def cmd_units(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/units [kg|lbs] — view or change your preferred weight unit."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if not context.args:
        current = _wu(user)
        other = "lbs" if current == "kg" else "kg"
        await update.message.reply_text(
            f"Current unit: *{current}*\n\nSwitch with `/units {other}`.",
            parse_mode="Markdown",
        )
        return

    arg = context.args[0].lower().strip()
    if arg in ("lbs", "lb", "pounds"):
        user["units"] = "lbs"
        _save_store()
        await update.message.reply_text("Done! Weights will now be displayed in *lbs*.", parse_mode="Markdown")
    elif arg == "kg":
        user["units"] = "kg"
        _save_store()
        await update.message.reply_text("Done! Weights will now be displayed in *kg*.", parse_mode="Markdown")
    else:
        await update.message.reply_text("Unknown unit. Use `/units kg` or `/units lbs`.", parse_mode="Markdown")


async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a one-time code to link this Telegram account to the web app."""
    chat_id = update.effective_chat.id

    if not BOT_SECRET or not API_BASE_URL:
        await update.message.reply_text(
            "⚠️ Account linking is not configured. Set BOT_SECRET and API_BASE_URL in the environment."
        )
        return

    # Check if already linked
    try:
        info = await _api_get(f"/internal/telegram/{chat_id}/user", chat_id=None)
        if info.get("linked"):
            _linked_user_ids[chat_id] = info["user_id"]
            await update.message.reply_text(
                f"✅ *Already linked!*\n\n"
                f"Your Telegram is connected to *{info.get('email', '?')}*.\n"
                f"Plan: *{info.get('subscription_tier', 'free').title()}*",
                parse_mode="Markdown",
            )
            return
    except Exception:
        pass

    # Generate new link code via API
    try:
        headers = {"X-Bot-Secret": BOT_SECRET, "X-Chat-ID": str(chat_id)}
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{API_BASE_URL}/api/internal/link-code/generate",
                headers=headers,
            )
            r.raise_for_status()
            result = r.json()
        code = result["code"]
    except Exception as e:
        await update.message.reply_text(f"❌ Could not generate link code: {e}")
        return

    await update.message.reply_text(
        f"🔗 *Link your Telegram to the web app*\n\n"
        f"Your one-time code:\n\n"
        f"```\n{code}\n```\n\n"
        f"1. Open the web app\n"
        f"2. Go to **Profile → Link Telegram**\n"
        f"3. Enter the code above\n\n"
        f"_Code expires in 10 minutes. Use /link\\-status to verify._",
        parse_mode="Markdown",
    )


async def cmd_link_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check whether this Telegram account is linked to a web account."""
    chat_id = update.effective_chat.id

    if not BOT_SECRET or not API_BASE_URL:
        await update.message.reply_text("Account linking is not configured.")
        return

    try:
        headers = {"X-Bot-Secret": BOT_SECRET}
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{API_BASE_URL}/api/internal/telegram/{chat_id}/user",
                headers=headers,
            )
            r.raise_for_status()
            info = r.json()
    except Exception as e:
        await update.message.reply_text(f"❌ Could not check link status: {e}")
        return

    if info.get("linked"):
        _linked_user_ids[chat_id] = info["user_id"]
        get_user(chat_id)["web_user_id"] = info["user_id"]
        _save_store()
        await update.message.reply_text(
            f"✅ *Linked!*\n\n"
            f"Telegram → *{info.get('email', '?')}*\n"
            f"Plan: *{info.get('subscription_tier', 'free').title()}*\n"
            f"User ID: `{info['user_id']}`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "❌ *Not linked yet.*\n\nUse /link to generate a code, then enter it on the web app.",
            parse_mode="Markdown",
        )


# ── Photo handler ─────────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    remaining = _check_cooldown(_analyze_cooldowns, chat_id, ANALYZE_COOLDOWN)
    if remaining:
        await update.message.reply_text(
            f"⏳ Please wait {remaining}s before submitting another photo."
        )
        return

    msg = await update.message.reply_text(
        "📸 Analyzing your physique… (20-40 seconds)"
    )
    try:
        photo = update.message.photo[-1]  # highest resolution
        file = await context.bot.get_file(photo.file_id)
        buf = BytesIO()
        await file.download_to_memory(buf)
        img_b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

        analysis = _analyze_photo(img_b64, user["profile"])
        user["last_analysis"] = analysis
        _save_store()

        await msg.edit_text(_format_analysis(analysis), parse_mode="Markdown")
        current_days = user["profile"].get("days", "4")
        await update.message.reply_text(
            f"How many days per week do you want to train? _(currently {current_days})_\n\n"
            "Tap a number to generate your plan instantly:",
            parse_mode="Markdown",
            reply_markup=_plan_days_keyboard(),
        )
    except Exception as e:
        await msg.edit_text(f"❌ Analysis failed: {e}")


async def handle_plan_days_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles plan:days:{n} — stores chosen day count then generates the plan."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    days = int(query.data.split(":")[-1])
    user["profile"]["days"] = str(days)
    _save_store()

    await query.edit_message_text(f"🧬 Building your {days}-day plan… (30-60 seconds)")
    try:
        if user["last_analysis"]:
            plan = _generate_plan(user["last_analysis"], user["profile"])
        else:
            plan = _generate_plan_from_profile(user["profile"])
        user["last_plan"] = plan
        _save_store()
        await query.delete_message()
        await _send_plan(update, plan)
        await update.effective_chat.send_message(
            "💬 Not happy with something? Just tell me — "
            "e.g. 'remove leg day', 'I'm vegetarian' — and I'll update it.\n"
            "Type `/plan new` anytime to regenerate.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ Plan generation failed: {e}")


# ── Text message handler (conversational coaching) ────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    text = update.message.text.strip()

    # Route to active multi-step command if one is open
    active = user.get("active_command")
    if active == "checkin":
        await _handle_checkin_step(update, user, text)
        return
    if active in ("connect_garmin", "connect_mfp"):
        await _handle_connect_step(update, user, text)
        return

    # Workout keyboard — handle free-text input when user tapped "Type…" buttons
    wk_awaiting = context.user_data.get("wk_awaiting")
    if wk_awaiting == "exercise":
        context.user_data.pop("wk_awaiting")
        name = text.strip().title()
        context.user_data["wk_ex"] = name
        await update.message.reply_text(
            f"*{name}* — Select weight:",
            parse_mode="Markdown",
            reply_markup=_weight_keyboard(name, user),
        )
        return
    if wk_awaiting == "weight":
        context.user_data.pop("wk_awaiting")
        weight = _parse_weight_input(text.strip(), user)
        if weight is None:
            unit = _wu(user)
            await update.message.reply_text(f"Couldn't parse that. Try `{'225' if unit == 'lbs' else '100'}` or `{'225lbs' if unit == 'lbs' else '100kg'}`.")
            return
        context.user_data["wk_w"] = weight
        ex = context.user_data.get("wk_ex", "exercise")
        await update.message.reply_text(
            f"*{ex}* — {_wfmt(weight, user)}\n\nSelect reps:",
            parse_mode="Markdown",
            reply_markup=_rep_keyboard(),
        )
        return
    if wk_awaiting == "reps":
        context.user_data.pop("wk_awaiting")
        try:
            reps = int(text.strip())
        except ValueError:
            await update.message.reply_text("Type a whole number for reps, e.g. `8`.")
            return
        ex = context.user_data.get("wk_ex")
        weight = context.user_data.get("wk_w")
        if not ex or weight is None:
            await update.message.reply_text("Session state lost. Start over with /log.")
            return
        entry, is_pr = _do_log_set(user, ex, weight, reps)
        pr_badge = " 🏆 *NEW PR!*" if is_pr else ""
        await update.message.reply_text(
            f"✅ *{ex}* — {_wfmt(weight, user)} × {reps}{pr_badge}\n"
            f"Est. 1RM: ~{_wfmt(entry['estimated_1rm'], user)}",
            parse_mode="Markdown",
            reply_markup=_after_set_keyboard(),
        )
        return

    msg = await update.message.reply_text("💬 Thinking…")
    try:
        reply, plan_update, plan_regen = _chat_with_coach(text, user)

        if plan_regen:
            for k, v in plan_regen.items():
                user["profile"][k] = str(v)
            regen_msg = await update.message.reply_text(
                "🧬 Got it — rebuilding your full plan with the changes… (30-60 seconds)"
            )
            try:
                if user["last_analysis"]:
                    plan = _generate_plan(user["last_analysis"], user["profile"])
                else:
                    plan = _generate_plan_from_profile(user["profile"])
                user["last_plan"] = plan
                _save_store()
                await regen_msg.delete()
                await msg.edit_text(reply, parse_mode="Markdown")
                await _send_plan(update, plan)
            except Exception as e:
                await regen_msg.edit_text(f"❌ Plan rebuild failed: {e}")
            return

        if plan_update and user["last_plan"]:
            _deep_merge(user["last_plan"], plan_update)
            _save_store()
            reply += "\n\n✅ _Your plan has been updated. Type /plan to see the full updated version._"

        await msg.edit_text(reply, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Something went wrong: {e}")


def _chat_with_coach(text: str, user: dict) -> tuple[str, dict | None]:
    profile = user["profile"]
    plan = user["last_plan"]
    history = user["conversation_history"]

    profile_str = (
        ", ".join(f"{k}={v}" for k, v in profile.items())
        if profile else "No profile set yet."
    )
    plan_str = (
        f"Current plan summary: {json.dumps(plan, indent=None)[:1500]}"
        if plan else "No plan generated yet — suggest they type /plan or send a photo."
    )

    system = (
        "You are a personal fitness and nutrition coach. You give specific, "
        "evidence-based advice tailored to the individual.\n\n"
        f"Athlete profile: {profile_str}\n"
        f"{plan_str}\n\n"
        "Support every fitness level (complete beginner to advanced), any age, any gender, any goal. "
        "Be direct, warm, and practical. Keep replies concise — 3-5 sentences unless a detailed "
        "breakdown is genuinely needed.\n\n"
        "When the user asks to modify their plan, choose ONE of two paths:\n\n"
        "MINOR changes (remove one exercise, swap a supplement, adjust a macro target, "
        "change meal timing, add a note) — output ONLY the changed fields in a `plan_update` block:\n"
        "```plan_update\n"
        '{{"supplements": [{{"name": "Beta-Alanine", "dose": "3.2g", "timing": "pre-workout", "benefit": "reduces fatigue", "grade": "B", "priority": 2}}]}}\n'
        "```\n\n"
        "MAJOR restructures (changing the NUMBER of training days, switching split type e.g. PPL↔Upper/Lower↔Full Body, "
        "changing the user's primary goal) — output a `plan_regenerate` block with ONLY the profile "
        "parameters that changed. The bot will generate a complete new plan automatically. Example:\n"
        "```plan_regenerate\n"
        '{{"days": 5}}\n'
        "```\n"
        "Valid keys for plan_regenerate: 'days' (int), 'goal' (string), 'experience' (string).\n"
        "Do NOT try to write the full exercise list yourself in plan_update — use plan_regenerate for structural changes.\n"
        "If nothing needs to change, omit both blocks entirely."
    )

    history.append({"role": "user", "content": text})
    if len(history) > MAX_HISTORY:
        history[:] = history[-MAX_HISTORY:]

    response = claude().messages.create(
        model=CHAT_MODEL,
        max_tokens=800,
        system=system,
        messages=history,
    )

    full_reply = response.content[0].text.strip()

    plan_update = None
    match = re.search(r"```plan_update\s*([\s\S]*?)```", full_reply)
    if match:
        try:
            plan_update = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
        full_reply = re.sub(r"```plan_update[\s\S]*?```", "", full_reply).strip()

    plan_regen = None
    match_regen = re.search(r"```plan_regenerate\s*([\s\S]*?)```", full_reply)
    if match_regen:
        try:
            plan_regen = json.loads(match_regen.group(1).strip())
        except json.JSONDecodeError:
            pass
        full_reply = re.sub(r"```plan_regenerate[\s\S]*?```", "", full_reply).strip()

    history.append({"role": "assistant", "content": full_reply})

    return full_reply, plan_update, plan_regen


def _deep_merge(base: dict, updates: dict) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ── Claude: body analysis ─────────────────────────────────────────────────────

def _analyze_photo(img_b64: str, profile: dict) -> dict:
    profile_ctx = ""
    if profile:
        profile_ctx = (
            f"\nAthlete: {profile.get('age','?')}yo {profile.get('gender','?')}, "
            f"{profile.get('height','?')}cm, {profile.get('weight','?')}kg, "
            f"goal={profile.get('goal','?')}, "
            f"experience={profile.get('experience','?')}, "
            f"{profile.get('days','?')} training days/week"
        )

    prompt = (
        "You are an expert fitness coach who works with athletes of all ages, genders, and "
        "experience levels — from complete beginners to competitive athletes. "
        f"Analyze this physique photo.{profile_ctx}\n\n"
        "Return ONLY valid JSON with this exact structure:\n"
        '{\n'
        '    "body_fat_estimate": "15-18%",\n'
        '    "body_fat_confidence": "medium",\n'
        '    "overall_physique_score": 7.2,\n'
        '    "muscle_development": {\n'
        '        "chest": {"score": 7, "notes": "Good upper chest, lower needs work"},\n'
        '        "back": {"score": 6, "notes": "Width decent, thickness lacking"},\n'
        '        "shoulders": {"score": 7, "notes": "Front delts strong, laterals lag"},\n'
        '        "arms": {"score": 7, "notes": "Good bicep peak, tricep mass needed"},\n'
        '        "legs": {"score": 5, "notes": "Significantly behind upper body"},\n'
        '        "core": {"score": 6, "notes": "Abs visible, obliques need work"}\n'
        '    },\n'
        '    "strengths": ["Good shoulder-to-waist ratio", "Chest fullness"],\n'
        '    "areas_to_improve": ["Leg development", "Overall conditioning"],\n'
        '    "symmetry_notes": "Left shoulder slightly higher. Overall symmetry good.",\n'
        '    "priority_improvements": ["Most impactful change 1", "Most impactful change 2"],\n'
        '    "coach_message": "Specific, motivating 2-sentence message for this athlete based on their experience level and goals"\n'
        '}'
    )

    message = claude().messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=1500,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    text = message.content[0].text
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return json.loads(text.strip())


# ── Claude: plan generation ───────────────────────────────────────────────────

def _build_plan_prompt(profile: dict, analysis: dict | None, days: int) -> str:
    profile_ctx = (
        f"Age: {profile.get('age', 'not specified')} | "
        f"Gender: {profile.get('gender', 'not specified')} | "
        f"Height: {profile.get('height', '?')}cm | "
        f"Weight: {profile.get('weight', '?')}kg | "
        f"Goal: {profile.get('goal', 'general health')} | "
        f"Experience: {profile.get('experience', 'beginner')} | "
        f"Training days: {days}/week"
    ) if profile else "No profile data — assume healthy adult beginner with general fitness goal."

    if analysis:
        body_ctx = (
            f"BODY ANALYSIS:\n"
            f"- Body fat: {analysis.get('body_fat_estimate', '?')}\n"
            f"- Physique score: {analysis.get('overall_physique_score', '?')}/10\n"
            f"- Priority improvements: {', '.join(analysis.get('priority_improvements', []))}\n"
            f"- Weakest areas: {', '.join(analysis.get('areas_to_improve', []))}\n"
            f"- Muscle development: {json.dumps(analysis.get('muscle_development', {}))}"
        )
    else:
        body_ctx = "No photo analysis — build the plan entirely from the profile stats above."

    return (
        "You are an expert strength coach and sports nutritionist who works with all populations — "
        "beginners to advanced athletes, all ages (teens to 70+), all genders, all goals "
        "(fat loss, muscle gain, general health, sport performance, recomp).\n\n"
        f"ATHLETE: {profile_ctx}\n"
        f"{body_ctx}\n\n"
        "Tailor EVERYTHING to this specific athlete. A beginner gets simpler movements and lower volume. "
        "An older athlete gets joint-friendly exercise selection. Nutrition targets must match their "
        "actual goal and body weight.\n\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '    "workout": {\n'
        '        "split": "4-Day Upper/Lower",\n'
        '        "days": [\n'
        '            {\n'
        '                "day": "Monday",\n'
        '                "focus": "Upper Push",\n'
        '                "exercises": [\n'
        '                    {"name": "Barbell Bench Press", "sets": 4, "reps": "6-8", "rest": "3min", "notes": "Full ROM, 2-sec descent"},\n'
        '                    {"name": "Incline Dumbbell Press", "sets": 3, "reps": "8-10", "rest": "2min", "notes": "Focus on upper chest stretch"},\n'
        '                    {"name": "Overhead Press", "sets": 4, "reps": "6-8", "rest": "3min", "notes": "Strict form, no leg drive"},\n'
        '                    {"name": "Lateral Raises", "sets": 4, "reps": "12-15", "rest": "90s", "notes": "Controlled, slight forward lean"},\n'
        '                    {"name": "Tricep Pushdowns", "sets": 3, "reps": "10-12", "rest": "90s", "notes": "Full extension"}\n'
        '                ]\n'
        '            }\n'
        '        ],\n'
        '        "progression": "Add 2.5kg when you complete all sets at top of rep range for 2 consecutive sessions.",\n'
        '        "deload": "Every 4-6 weeks: reduce load 40%, maintain volume."\n'
        '    },\n'
        '    "diet": {\n'
        '        "calories": 2800,\n'
        '        "protein_g": 180,\n'
        '        "carbs_g": 320,\n'
        '        "fat_g": 78,\n'
        '        "rationale": "Why these exact numbers for this athlete",\n'
        '        "meal_timing": "Pre/post workout nutrition guidance",\n'
        '        "sample_meals": ["Breakfast: ...", "Lunch: ...", "Dinner: ..."],\n'
        '        "foods_to_prioritize": ["Chicken breast", "Eggs", "Rice", "Oats"],\n'
        '        "foods_to_limit": ["Ultra-processed foods", "Alcohol"]\n'
        '    },\n'
        '    "supplements": [\n'
        '        {"priority": 1, "name": "Creatine Monohydrate", "dose": "5g daily", "timing": "Anytime", "grade": "A", "benefit": "5-15% strength gains. Most evidence-backed supplement."},\n'
        '        {"priority": 2, "name": "Whey Protein", "dose": "25-40g per serving", "timing": "Post-workout or to hit daily protein", "grade": "A", "benefit": "High leucine triggers muscle protein synthesis."},\n'
        '        {"priority": 3, "name": "Caffeine", "dose": "200-400mg", "timing": "30-45min pre-workout", "grade": "A", "benefit": "Increases power output, reduces perceived exertion."},\n'
        '        {"priority": 4, "name": "Vitamin D3 + K2", "dose": "3000 IU D3 + 100mcg K2", "timing": "With a fat-containing meal", "grade": "B", "benefit": "Supports testosterone, bone density, immunity."},\n'
        '        {"priority": 5, "name": "Omega-3 Fish Oil", "dose": "2-3g EPA+DHA", "timing": "With meals", "grade": "B", "benefit": "Reduces DOMS, supports joint health."},\n'
        '        {"priority": 6, "name": "Magnesium Glycinate", "dose": "300-400mg", "timing": "Before bed", "grade": "B", "benefit": "Improves sleep quality and recovery."}\n'
        '    ],\n'
        '    "coaching": {\n'
        '        "top_priority": "The single most impactful change for this athlete",\n'
        '        "sleep": "Sleep guidance",\n'
        '        "stress": "Stress management",\n'
        '        "tracking": "What to track and how",\n'
        '        "expectations": "Realistic 12-week outcome for this athlete",\n'
        '        "coach_message": "Inspiring, specific closing message"\n'
        '    }\n'
        '}\n\n'
        f"Build ALL {days} training days. Be specific with numbers. Only return valid JSON."
    )


def _parse_plan_response(text: str) -> dict:
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return json.loads(text.strip())


def _generate_plan(analysis: dict, profile: dict) -> dict:
    days = int(profile.get("days", 4))
    prompt = _build_plan_prompt(profile, analysis, days)
    message = claude().messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=5000,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_plan_response(message.content[0].text)


def _generate_plan_from_profile(profile: dict) -> dict:
    days = int(profile.get("days", 3))
    prompt = _build_plan_prompt(profile, None, days)
    message = claude().messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=5000,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_plan_response(message.content[0].text)


# ── Research ──────────────────────────────────────────────────────────────────

async def _fetch_research_summaries() -> list[str]:
    summaries = []
    for topic in RESEARCH_TOPICS[:3]:
        try:
            papers = await _search_pubmed(topic)
            if papers:
                summary = _summarize_papers(topic, papers)
                summaries.append(f"*{topic.replace('2024', '').strip().title()}*\n{summary}")
            await asyncio.sleep(0.5)
        except Exception:
            continue
    return summaries or ["No research data available. Try again in a moment."]


async def _search_pubmed(query: str, max_results: int = 4) -> list:
    params = {
        "db": "pubmed",
        "term": f"({query})[Title/Abstract]",
        "retmax": max_results,
        "sort": "relevance",
        "datetype": "pdat",
        "mindate": "2023/01/01",
        "retmode": "json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=params
        )
        ids = resp.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    await asyncio.sleep(0.35)
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(ids), "rettype": "abstract", "retmode": "xml"},
        )
    return _parse_pubmed_xml(resp.text)


def _parse_pubmed_xml(xml_text: str) -> list:
    papers = []
    try:
        root = ET.fromstring(xml_text)
        for article in root.findall(".//PubmedArticle"):
            title = article.findtext(".//ArticleTitle", "")
            abstract = " ".join(
                (t.text or "") for t in article.findall(".//AbstractText")
            )
            year = article.findtext(".//PubDate/Year", "")
            if title and abstract:
                papers.append({"title": title, "abstract": abstract[:600], "year": year})
    except Exception:
        pass
    return papers


def _summarize_papers(topic: str, papers: list) -> str:
    text = "\n\n".join(
        f"Title: {p['title']} ({p.get('year', '')})\n{p['abstract']}"
        for p in papers[:4]
    )
    message = claude().messages.create(
        model=SUMMARY_MODEL,
        max_tokens=280,
        messages=[{
            "role": "user",
            "content": (
                f'Summarize key ACTIONABLE findings for a fitness enthusiast from these papers on "{topic}". '
                f"3 sentences max. Practical, specific:\n\n{text}"
            ),
        }],
    )
    return message.content[0].text.strip()


# ── Formatters ────────────────────────────────────────────────────────────────

def _format_analysis(a: dict) -> str:
    muscle = a.get("muscle_development", {})
    muscle_lines = "\n".join(
        f"  {k.capitalize()}: {v.get('score', '?')}/10 — {v.get('notes', '')}"
        for k, v in muscle.items()
    )
    strengths = "\n".join(f"✅ {s}" for s in a.get("strengths", []))
    priorities = "\n".join(f"🎯 {s}" for s in a.get("priority_improvements", []))

    return (
        f"📊 *Physique Analysis*\n\n"
        f"Body Fat: *{a.get('body_fat_estimate', '?')}* (confidence: {a.get('body_fat_confidence', '?')})\n"
        f"Score: *{a.get('overall_physique_score', '?')}/10*\n\n"
        f"*Muscle Development:*\n{muscle_lines}\n\n"
        f"*Strengths:*\n{strengths}\n\n"
        f"*Top Priorities:*\n{priorities}\n\n"
        f"📐 {a.get('symmetry_notes', '')}\n\n"
        f"_{a.get('coach_message', '')}_"
    )


async def _send_plan(update: Update, plan: dict) -> None:
    """Send plan messages. Works from both command and callback-query contexts."""
    # Build a send callable that works regardless of context type
    if update.message:
        send = update.message.reply_text
    else:
        # callback query context — update.effective_chat is always available
        async def send(text, **kw):
            return await update.effective_chat.send_message(text, **kw)

    workout = plan.get("workout", {})
    diet = plan.get("diet", {})
    supplements = plan.get("supplements", [])
    coaching = plan.get("coaching", {})

    # ── Workout ──
    days_text = ""
    for day in workout.get("days", []):
        exercises = day.get("exercises", [])
        if not exercises:
            days_text += f"\n*{day['day']} — {day.get('focus', '')}*\n    _(No exercises — type `/plan new` to regenerate)_\n"
            continue
        ex_lines = "\n".join(
            f"    • {e['name']}: {e['sets']}×{e['reps']} — rest {e.get('rest', '')} | {e.get('notes', '')}"
            for e in exercises
        )
        days_text += f"\n*{day['day']} — {day.get('focus', '')}*\n{ex_lines}\n"

    # Build one button per training day so the user can tap to start that day's workout
    plan_days = workout.get("days", [])
    day_buttons = [
        [InlineKeyboardButton(
            f"{d['day']} — {d.get('focus', '')}".strip(" —"),
            callback_data=f"wk:day:{d['day']}",
        )]
        for d in plan_days if d.get("day")
    ]
    day_keyboard = InlineKeyboardMarkup(day_buttons) if day_buttons else None

    await send(
        f"🏋️ *Workout — {workout.get('split', '')}*\n"
        f"{days_text}\n"
        f"📈 *Progression:* {workout.get('progression', '')}\n"
        f"🔄 *Deload:* {workout.get('deload', '')}",
        parse_mode="Markdown",
        reply_markup=day_keyboard,
    )

    # ── Diet ──
    meals = "\n".join(f"  • {m}" for m in diet.get("sample_meals", []))
    await send(
        f"🥗 *Diet Plan*\n\n"
        f"Calories: *{diet.get('calories', '?')} kcal*\n"
        f"Protein: *{diet.get('protein_g', '?')}g* | "
        f"Carbs: *{diet.get('carbs_g', '?')}g* | "
        f"Fat: *{diet.get('fat_g', '?')}g*\n\n"
        f"_{diet.get('rationale', '')}_\n\n"
        f"*Meal Timing:*\n{diet.get('meal_timing', '')}\n\n"
        f"*Sample Day:*\n{meals}\n\n"
        f"*Prioritize:* {', '.join(diet.get('foods_to_prioritize', []))}",
        parse_mode="Markdown",
    )

    # ── Supplements ──
    supp_lines = "\n\n".join(
        f"*#{s.get('priority', '?')} {s['name']}* — Grade {s.get('grade', '?')}\n"
        f"  {s.get('dose', '?')} | {s.get('timing', '?')}\n"
        f"  _{s.get('benefit', '')}_"
        for s in supplements
    )
    await send(
        f"💊 *Supplement Stack*\n\n{supp_lines}",
        parse_mode="Markdown",
    )

    # ── Coaching ──
    await send(
        f"💬 *Coaching Notes*\n\n"
        f"🎯 *Top Priority:* {coaching.get('top_priority', '')}\n\n"
        f"😴 *Sleep:* {coaching.get('sleep', '')}\n\n"
        f"🧘 *Stress:* {coaching.get('stress', '')}\n\n"
        f"📊 *Tracking:* {coaching.get('tracking', '')}\n\n"
        f"📅 *12-Week Outlook:* {coaching.get('expectations', '')}\n\n"
        f"_{coaching.get('coach_message', '')}_",
        parse_mode="Markdown",
    )


async def _daily_garmin_sync() -> None:
    """Scheduled 6am job: refresh Garmin data for all connected users."""
    import garmin_service
    for chat_id, u in list(user_data.items()):
        if u.get("garmin_email") and u.get("garmin_pass_enc"):
            try:
                garmin_service.fetch_and_cache(chat_id, u["garmin_email"], u["garmin_pass_enc"])
                print(f"Garmin synced for chat_id={chat_id}")
            except Exception as e:
                print(f"Garmin sync failed for chat_id={chat_id}: {e}")


async def _weekly_stall_check() -> None:
    """Monday 9am: detect weight stalls vs goal and send coaching nudge."""
    global _app
    if _app is None:
        return
    for chat_id, u in list(user_data.items()):
        try:
            goal = u.get("profile", {}).get("goal", "")
            if goal not in ("bulk", "cut"):
                continue
            measurements = u.get("measurements", [])
            weights = sorted(
                [(m["date"], m["body_weight_kg"]) for m in measurements if m.get("body_weight_kg")],
                key=lambda x: x[0],
            )
            if len(weights) < 4:
                continue
            # Compare last 14 days
            cutoff_14 = str(_date.today() - timedelta(days=14))
            recent = [w for w in weights if w[0] >= cutoff_14]
            if len(recent) < 2:
                continue
            change = abs(recent[-1][1] - recent[0][1])
            if change < 0.3:
                direction = "gaining weight" if goal == "bulk" else "losing weight"
                await _app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"📊 *Weekly Check-In*\n\n"
                        f"Your weight has been stable for 2 weeks ({recent[0][1]}kg → {recent[-1][1]}kg).\n\n"
                        f"For your *{goal}* goal you should be {direction}. "
                        f"{'Try adding 150-200 kcal/day to break the plateau.' if goal == 'bulk' else 'Try reducing calories by 150-200 kcal/day or adding 20 min cardio.'}\n\n"
                        f"Type /macros to review your nutrition or chat with me for a personalised fix."
                    ),
                    parse_mode="Markdown",
                )
        except Exception as e:
            print(f"Stall check failed for {chat_id}: {e}")


async def _missed_workout_check() -> None:
    """9pm daily: check if user had a planned workout today but logged nothing."""
    global _app
    if _app is None:
        return
    today_name = _date.today().strftime("%A")
    for chat_id, u in list(user_data.items()):
        try:
            plan = u.get("last_plan", {})
            if not plan:
                continue
            days = plan.get("workout", {}).get("days", [])
            today_day = next((d for d in days if d.get("day", "").lower() == today_name.lower()), None)
            if not today_day:
                continue  # rest day
            # Check if any sets were logged today
            today_str = _today()
            logged_today = any(s.get("date") == today_str for s in u.get("set_logs", []))
            if not logged_today and u.get("active_session_id") is None:
                focus = today_day.get("focus", "workout")
                await _app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🏋️ *Missed session reminder*\n\n"
                        f"You had *{focus}* planned for today but haven't logged any sets.\n\n"
                        f"Still time to squeeze it in! Use /log to start quickly, "
                        f"or tell me if something came up and I'll adjust your plan."
                    ),
                    parse_mode="Markdown",
                )
        except Exception as e:
            print(f"Missed workout check failed for {chat_id}: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if not TELEGRAM_TOKEN:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Get it from @BotFather on Telegram, then set the env var."
        )
    if not ANTHROPIC_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set.")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("checkin", cmd_checkin))
    app.add_handler(CommandHandler("workout", cmd_workout))
    app.add_handler(CommandHandler("log", cmd_log))
    app.add_handler(CommandHandler("logset", cmd_logset))
    app.add_handler(CommandHandler("progress", cmd_progress))
    app.add_handler(CommandHandler("measurements", cmd_measurements))
    app.add_handler(CommandHandler("meal", cmd_meal))
    app.add_handler(CommandHandler("macros", cmd_macros))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("connect", cmd_connect))
    app.add_handler(CommandHandler("mfp", cmd_mfp))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(CommandHandler("streak", cmd_streak))
    app.add_handler(CommandHandler("weight", cmd_weight))
    app.add_handler(CommandHandler("goals", cmd_goals))
    app.add_handler(CommandHandler("weakpoints", cmd_weakpoints))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("billing", cmd_billing))
    app.add_handler(CommandHandler("units", cmd_units))
    app.add_handler(CommandHandler("link", cmd_link))
    app.add_handler(CommandHandler("link_status", cmd_link_status))
    app.add_handler(CallbackQueryHandler(handle_workout_callback, pattern=r"^wk:"))
    app.add_handler(CallbackQueryHandler(handle_plan_days_callback, pattern=r"^plan:days:"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # post_init runs inside the event loop — the right place to start AsyncIOScheduler
    async def _post_init(app_ref) -> None:
        global _app
        _app = app_ref
        _scheduler.start()  # must start within a running event loop

        await app_ref.bot.set_my_commands([
            BotCommand("start",        "Welcome & quick-start"),
            BotCommand("help",         "List all commands"),
            BotCommand("profile",      "Set your stats (age, weight, goal…)"),
            BotCommand("plan",         "Generate or view your workout & diet plan"),
            BotCommand("log",          "Start logging a workout (tap-based)"),
            BotCommand("workout",      "Start / end a workout session"),
            BotCommand("logset",       "Log a set: /logset bench 100kg 8"),
            BotCommand("checkin",      "Daily check-in (sleep, energy, soreness)"),
            BotCommand("progress",     "View weight trend, PRs, recovery"),
            BotCommand("stats",        "Personal records by exercise"),
            BotCommand("measurements", "Log body measurements"),
            BotCommand("weight",       "Quick body-weight log: /weight 84.5"),
            BotCommand("meal",         "Log a meal and get macros"),
            BotCommand("macros",       "Today's macro totals"),
            BotCommand("goals",        "Set or view a target (weight, date…)"),
            BotCommand("streak",       "Check-in & workout streak"),
            BotCommand("weakpoints",   "AI weak-point analysis from your data"),
            BotCommand("report",       "Generate weekly AI coaching report"),
            BotCommand("research",     "Search fitness research papers"),
            BotCommand("reminders",    "Set daily reminders"),
            BotCommand("units",        "Switch between kg and lbs"),
            BotCommand("connect",      "Connect Garmin account"),
            BotCommand("mfp",          "Connect MyFitnessPal account"),
            BotCommand("billing",      "Subscription & billing info"),
            BotCommand("link",         "Link Telegram to the web app"),
            BotCommand("link_status",  "Check web-app link status"),
        ])

        _scheduler.add_job(
            _daily_garmin_sync,
            trigger="cron",
            hour=6,
            minute=0,
            id="daily_garmin_sync",
            replace_existing=True,
        )

        _scheduler.add_job(
            _weekly_stall_check,
            trigger="cron",
            day_of_week="mon",
            hour=9,
            minute=0,
            id="weekly_stall_check",
            replace_existing=True,
        )

        _scheduler.add_job(
            _missed_workout_check,
            trigger="cron",
            hour=21,
            minute=0,
            id="missed_workout_check",
            replace_existing=True,
        )

        REMINDER_MSGS = {
            "workout": "🏋️ Time to train! Type /workout to see today's session.",
            "checkin": "📋 Daily check-in time! /checkin",
            "meal": "🥗 Don't forget to log your last meal! /meal",
        }
        for chat_id, udata in user_data.items():
            for kind, r in udata.get("reminders", {}).items():
                msg_text = REMINDER_MSGS.get(kind, f"⏰ {kind.capitalize()} reminder!")
                _scheduler.add_job(
                    _send_reminder,
                    trigger="cron",
                    hour=r["hour"],
                    minute=r["minute"],
                    args=[chat_id, msg_text],
                    id=f"{kind}_{chat_id}",
                    replace_existing=True,
                )

    app.post_init = _post_init

    print("✅ BodyBuilding Coach Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
