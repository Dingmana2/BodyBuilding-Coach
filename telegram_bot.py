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
from datetime import date as _date
from io import BytesIO
from pathlib import Path

import anthropic
import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
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
            "active_command": None,   # "checkin" | "workout" | None
            "command_state": {},      # step data for multi-step commands
            "active_session_id": None,  # open WorkoutSession id
            "reminders": {},          # {"workout": {"hour": 7, "minute": 0}, ...}
            "checkins": [],           # list of DailyCheckIn dicts (local cache)
            "set_logs": [],           # list of SetLog dicts (local cache)
            "prs": {},                # exercise_name → {weight_kg, reps, estimated_1rm, date}
            "meal_logs": [],          # list of MealLog dicts (local cache)
            "measurements": [],       # list of BodyMeasurement dicts (local cache)
            "session_counter": 0,     # total sessions completed
        }
    else:
        # Back-fill fields added in later versions
        u = user_data[chat_id]
        defaults = {
            "active_command": None, "command_state": {}, "active_session_id": None,
            "reminders": {}, "checkins": [], "set_logs": [], "prs": {},
            "meal_logs": [], "measurements": [], "session_counter": 0,
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
    "/logset — Log a set: `/logset bench 100kg 8`\n"
    "/meal — Log food: `/meal 2 eggs, oatmeal, banana`\n"
    "/macros — Today's macro targets vs. logged\n\n"
    "*Progress & stats:*\n"
    "/progress — 30-day trend: weight, BF%, strength\n"
    "/stats — Personal records + volume by muscle\n"
    "/measurements — Log body measurements\n\n"
    "*Other:*\n"
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

    remaining = _check_cooldown(_plan_cooldowns, chat_id, PLAN_COOLDOWN)
    if remaining:
        await update.message.reply_text(
            f"⏳ Please wait {remaining}s before generating another plan."
        )
        return

    msg = await update.message.reply_text(
        "🧬 Generating your plan… (30-60 seconds)"
    )
    try:
        if user["last_analysis"]:
            plan = _generate_plan(user["last_analysis"], user["profile"])
        else:
            if not user["profile"]:
                await msg.edit_text(
                    "Set your stats first with /profile, then I can build your plan.\n\n"
                    "Example:\n"
                    "`/profile age=25 gender=female height=165 weight=65 goal=recomp experience=beginner days=3`\n\n"
                    "Or send a photo and I'll analyze your physique directly 📸",
                    parse_mode="Markdown",
                )
                return
            plan = _generate_plan_from_profile(user["profile"])

        user["last_plan"] = plan
        _save_store()
        await msg.delete()
        await _send_plan(update, plan)
        await update.message.reply_text(
            "💬 Not happy with something? Just tell me — "
            "e.g. 'remove leg day', 'I'm vegetarian', 'train only 3 days' — and I'll update your plan."
        )
    except Exception as e:
        await msg.edit_text(f"❌ Plan generation failed: {e}")


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

    # Multi-step flow
    user["active_command"] = "checkin"
    user["command_state"] = {"step": 0, "data": {}}
    await update.message.reply_text(
        "😴 *Sleep quality?* Rate 1-10\n_(1 = terrible, 10 = perfect)_",
        parse_mode="Markdown",
    )


async def _handle_checkin_step(update: Update, user: dict, text: str) -> None:
    state = user["command_state"]
    steps = ["sleep", "energy", "soreness", "stress"]
    prompts = [
        "⚡ *Energy levels?* Rate 1-10",
        "🤕 *Muscle soreness?* Rate 1-10\n_(1 = very sore, 10 = fresh)_",
        "🧠 *Stress level?* Rate 1-10\n_(1 = very stressed, 10 = calm)_",
    ]

    try:
        score = max(1, min(10, int(text.strip())))
    except ValueError:
        await update.message.reply_text("Please enter a number from 1 to 10.")
        return

    key = steps[state["step"]]
    state["data"][key] = score
    state["step"] += 1

    if state["step"] < len(steps):
        await update.message.reply_text(prompts[state["step"] - 1], parse_mode="Markdown")
    else:
        user["active_command"] = None
        await _finish_checkin(update, user, state["data"])


async def _finish_checkin(update: Update, user: dict, data: dict) -> None:
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
        "data_source": "manual",
    }
    user["checkins"].append(entry)
    # Keep only last 90 days
    user["checkins"] = user["checkins"][-90:]
    _save_store()

    bar = "🟢" if score >= 75 else ("🟡" if score >= 50 else "🔴")
    await msg.edit_text(
        f"✅ *Check-in saved!*\n\n"
        f"{bar} Recovery Score: *{score}/100*\n\n"
        f"😴 Sleep: {data['sleep']}/10  ⚡ Energy: {data['energy']}/10\n"
        f"🤕 Soreness: {data['soreness']}/10  🧠 Stress: {data['stress']}/10\n\n"
        f"_{tip}_",
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

        plan_text = ""
        if user["last_plan"]:
            today_name = _date.today().strftime("%A")
            days = user["last_plan"].get("workout", {}).get("days", [])
            today_day = next((d for d in days if d.get("day", "").lower() == today_name.lower()), None)
            if today_day:
                exercises = today_day.get("exercises", [])
                lines = "\n".join(
                    f"• {e['name']}: {e['sets']}×{e['reps']}"
                    for e in exercises
                )
                plan_text = f"\n\n*Today ({today_name} — {today_day.get('focus', '')}):*\n{lines}"

        await update.message.reply_text(
            f"🏋️ *Session #{sid} started!*{plan_text}\n\n"
            "Log sets as you go:\n`/logset bench 100kg 8`\n\n"
            "Type `/workout end` when you're done.",
            parse_mode="Markdown",
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
        _save_store()

        exercise_summary = ""
        if session_sets:
            by_ex: dict[str, list] = {}
            for s in session_sets:
                by_ex.setdefault(s["exercise_name"], []).append(s)
            lines = []
            for ex, sets in by_ex.items():
                best = max(sets, key=lambda x: x["estimated_1rm"])
                lines.append(f"  {ex}: {len(sets)} sets | best {best['weight_kg']}kg×{best['reps']} (1RM ~{best['estimated_1rm']}kg)")
            exercise_summary = "\n" + "\n".join(lines)

        await update.message.reply_text(
            f"✅ *Session #{sid} complete!*\n\n"
            f"Sets: {total_sets} | Volume: {total_volume:,.0f}kg{exercise_summary}\n\n"
            "Type /stats to see your PRs.",
            parse_mode="Markdown",
        )

    else:
        # Show today's planned workout
        if not user["last_plan"]:
            await update.message.reply_text(
                "No plan yet. Run /plan to generate one first, or `/workout start` to begin a free session.",
                parse_mode="Markdown",
            )
            return
        today_name = _date.today().strftime("%A")
        days = user["last_plan"].get("workout", {}).get("days", [])
        today_day = next((d for d in days if d.get("day", "").lower() == today_name.lower()), None)
        if not today_day:
            await update.message.reply_text(
                f"No session planned for {today_name}. Rest day! Or `/workout start` for a free session.",
                parse_mode="Markdown",
            )
            return
        exercises = today_day.get("exercises", [])
        lines = "\n".join(
            f"• {e['name']}: {e['sets']}×{e['reps']} | {e.get('notes', '')}".rstrip(" |")
            for e in exercises
        )
        status = f"🔴 Session open (#{user['active_session_id']})" if user["active_session_id"] else "⚪ No open session"
        await update.message.reply_text(
            f"🏋️ *{today_name} — {today_day.get('focus', '')}*\n{status}\n\n{lines}\n\n"
            "Start logging: `/workout start` → `/logset bench 100kg 8`",
            parse_mode="Markdown",
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
        f"✅ *{exercise}* — {weight_kg}kg × {reps} reps\n"
        f"1RM estimate: ~{one_rm}kg (Epley){pr_text}",
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
        lines.append(f"*Weight:* {first_w}kg → {last_w}kg ({arrow} {abs(diff)}kg) {spark}")

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
            f"  {ex}: {v['weight_kg']}kg×{v['reps']} (1RM ~{v['estimated_1rm']}kg)"
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
            "Log a meal:\n`/meal 2 eggs, 1 cup oatmeal, banana`\n`/meal 200g chicken breast 1 cup rice broccoli`",
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
        f"*{ex}:* {v['weight_kg']}kg × {v['reps']} reps  (1RM ~{v['estimated_1rm']}kg)  📅 {v['date']}"
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


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("🔬 Fetching latest PubMed research…")
    try:
        summaries = await _fetch_research_summaries()
        text = "*Latest Research Highlights*\n\n" + "\n\n".join(summaries)
        await msg.edit_text(text[:4096], parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Research fetch failed: {e}")


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
        await update.message.reply_text(
            "Type /plan to generate your full workout + diet + supplement plan 💪"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Analysis failed: {e}")


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

    msg = await update.message.reply_text("💬 Thinking…")
    try:
        reply, plan_update = _chat_with_coach(text, user)

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
        "If the user asks you to modify their plan (e.g. 'remove leg day', 'I'm vegetarian', "
        "'change to 3 days a week', 'swap the creatine'), output the changes in a fenced code "
        "block tagged `plan_update` containing ONLY the modified JSON fields. Example:\n"
        "```plan_update\n"
        '{{"workout": {{"split": "3-Day Full Body"}}}}\n'
        "```\n"
        "Only include fields that actually change. If nothing needs to change, omit the block entirely."
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

    history.append({"role": "assistant", "content": full_reply})

    return full_reply, plan_update


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
    workout = plan.get("workout", {})
    diet = plan.get("diet", {})
    supplements = plan.get("supplements", [])
    coaching = plan.get("coaching", {})

    # ── Workout ──
    days_text = ""
    for day in workout.get("days", []):
        ex_lines = "\n".join(
            f"    • {e['name']}: {e['sets']}×{e['reps']} — rest {e.get('rest', '')} | {e.get('notes', '')}"
            for e in day.get("exercises", [])
        )
        days_text += f"\n*{day['day']} — {day.get('focus', '')}*\n{ex_lines}\n"

    await update.message.reply_text(
        f"🏋️ *Workout — {workout.get('split', '')}*\n"
        f"{days_text}\n"
        f"📈 *Progression:* {workout.get('progression', '')}\n"
        f"🔄 *Deload:* {workout.get('deload', '')}",
        parse_mode="Markdown",
    )

    # ── Diet ──
    meals = "\n".join(f"  • {m}" for m in diet.get("sample_meals", []))
    await update.message.reply_text(
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
    await update.message.reply_text(
        f"💊 *Supplement Stack*\n\n{supp_lines}",
        parse_mode="Markdown",
    )

    # ── Coaching ──
    await update.message.reply_text(
        f"💬 *Coaching Notes*\n\n"
        f"🎯 *Top Priority:* {coaching.get('top_priority', '')}\n\n"
        f"😴 *Sleep:* {coaching.get('sleep', '')}\n\n"
        f"🧘 *Stress:* {coaching.get('stress', '')}\n\n"
        f"📊 *Tracking:* {coaching.get('tracking', '')}\n\n"
        f"📅 *12-Week Outlook:* {coaching.get('expectations', '')}\n\n"
        f"_{coaching.get('coach_message', '')}_",
        parse_mode="Markdown",
    )


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
    app.add_handler(CommandHandler("logset", cmd_logset))
    app.add_handler(CommandHandler("progress", cmd_progress))
    app.add_handler(CommandHandler("measurements", cmd_measurements))
    app.add_handler(CommandHandler("meal", cmd_meal))
    app.add_handler(CommandHandler("macros", cmd_macros))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("reminders", cmd_reminders))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # post_init runs inside the event loop — the right place to start AsyncIOScheduler
    async def _post_init(app_ref) -> None:
        global _app
        _app = app_ref
        _scheduler.start()  # must start within a running event loop

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
