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

from claude_service import ANALYSIS_MODEL, SUMMARY_MODEL, epley_1rm, get_anthropic_client  # noqa: E402 — after load_dotenv()
from prompt_builder import bot_json_context_block

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_raw_api_url = os.getenv("API_BASE_URL", "http://localhost:8000").strip().rstrip("/")
if _raw_api_url and not _raw_api_url.startswith(("http://", "https://")):
    _raw_api_url = f"https://{_raw_api_url}"
API_BASE_URL = _raw_api_url
BOT_SECRET = os.getenv("BOT_SECRET", "")
CHAT_MODEL = "claude-sonnet-4-6"
MAX_HISTORY = 20

# Cooldown in seconds between expensive per-user operations.
PLAN_COOLDOWN = 300     # 5 minutes between /plan calls
ANALYZE_COOLDOWN = 60   # 1 minute between photo analyses
_plan_cooldowns: dict[int, float] = {}
_analyze_cooldowns: dict[int, float] = {}

# Exercise-name → muscle-group keyword mapping (shared across /stats, /progress, plan context).
_MUSCLE_MAP: dict[str, list[str]] = {
    "chest": ["bench", "chest", "fly", "push", "pec"],
    "back": ["row", "pulldown", "pull-up", "pullup", "chin", "deadlift", "back", "lat"],
    "shoulders": ["lateral", "shoulder", "ohp", "delt", "raise", "face pull"],
    "arms": ["curl", "tricep", "bicep", "extension", "dip", "skullcrusher"],
    "legs": ["squat", "lunge", "leg press", "leg curl", "leg extension", "calf", "rdl",
             "romanian", "hip thrust", "glute", "hamstring"],
    "core": ["plank", "crunch", "ab", "core", "sit-up", "situp", "woodchop"],
}

# Measurement fields that correspond to each muscle group (for size-trend tracking).
_MUSCLE_MEASURE: dict[str, tuple[str, ...]] = {
    "chest": ("chest_cm",),
    "arms": ("left_arm_cm", "right_arm_cm"),
    "legs": ("left_thigh_cm", "right_thigh_cm"),
}

# ── Exercise weight defaults ──────────────────────────────────────────────────
# Two separate tables: kg plate math ≠ lbs plate math — do NOT convert between them.
# Keyword matching: first key whose substring appears in exercise_name.lower() wins.

_EXERCISE_DEFAULTS_KG: dict[str, list[float]] = {
    # Lower compound (barbell = 20 kg)
    "squat":          [60,  80,  100, 120, 140],
    "deadlift":       [80,  100, 120, 140, 160],
    "leg press":      [60,  80,  100, 130, 160],
    "rdl":            [40,  60,  80,  100, 120],
    "romanian":       [40,  60,  80,  100, 120],
    "hip thrust":     [40,  60,  80,  100, 120],
    "lunge":          [10,  15,  20,  25,  30],
    "leg curl":       [25,  35,  45,  55,  65],
    "leg extension":  [25,  35,  45,  55,  65],
    "calf":           [20,  30,  40,  50,  60],
    # Upper push (barbell = 20 kg)
    "bench press":    [40,  60,  80,  100, 120],
    "incline":        [30,  40,  50,  60,  80],
    "dumbbell press": [12,  16,  20,  24,  28],
    "overhead press": [30,  40,  50,  60,  70],
    "ohp":            [30,  40,  50,  60,  70],
    "dip":            [0,   5,   10,  15,  20],
    # Upper pull
    "pull-up":        [0,   5,   10,  15,  20],
    "pullup":         [0,   5,   10,  15,  20],
    "chin":           [0,   5,   10,  15,  20],
    "pulldown":       [40,  50,  60,  70,  80],
    "row":            [40,  50,  60,  70,  80],
    # Isolation
    "curl":           [8,   10,  12,  15,  20],
    "tricep":         [15,  20,  25,  30,  35],
    "lateral raise":  [5,   8,   10,  12,  15],
    "face pull":      [12,  15,  20,  25,  30],
    "fly":            [8,   10,  12,  15,  20],
}

_EXERCISE_DEFAULTS_LBS: dict[str, list[float]] = {
    # Lower compound (barbell = 45 lbs)
    "squat":          [135, 185, 225, 275, 315],
    "deadlift":       [135, 185, 225, 275, 315],
    "leg press":      [90,  135, 180, 225, 270],
    "rdl":            [95,  135, 185, 225, 275],
    "romanian":       [95,  135, 185, 225, 275],
    "hip thrust":     [95,  135, 185, 225, 275],
    "lunge":          [20,  30,  40,  50,  60],
    "leg curl":       [50,  70,  90,  110, 130],
    "leg extension":  [50,  70,  90,  110, 130],
    "calf":           [45,  70,  90,  115, 135],
    # Upper push (barbell = 45 lbs)
    "bench press":    [95,  135, 185, 225, 275],
    "incline":        [65,  95,  115, 135, 155],
    "dumbbell press": [25,  35,  45,  55,  65],
    "overhead press": [65,  95,  115, 135, 155],
    "ohp":            [65,  95,  115, 135, 155],
    "dip":            [0,   10,  25,  35,  45],
    # Upper pull
    "pull-up":        [0,   10,  25,  35,  45],
    "pullup":         [0,   10,  25,  35,  45],
    "chin":           [0,   10,  25,  35,  45],
    "pulldown":       [70,  90,  110, 130, 150],
    "row":            [95,  115, 135, 155, 185],
    # Isolation (dumbbells, 5 lb increments)
    "curl":           [15,  20,  25,  30,  35],
    "tricep":         [30,  40,  50,  60,  70],
    "lateral raise":  [10,  15,  20,  25,  30],
    "face pull":      [25,  35,  45,  55,  65],
    "fly":            [15,  20,  25,  30,  35],
}


_SUPPLEMENT_INTERACTIONS: dict[str, list[tuple[str, str]]] = {
    "blood thinner":   [("Omega-3 Fish Oil", "increases bleeding risk"), ("Vitamin E", "increases bleeding risk")],
    "warfarin":        [("Omega-3 Fish Oil", "increases bleeding risk"), ("Vitamin K", "antagonises warfarin — avoid high-dose K2")],
    "ssri":            [("5-HTP", "serotonin syndrome risk"), ("St John's Wort", "serotonin syndrome risk")],
    "antidepressant":  [("5-HTP", "serotonin syndrome risk"), ("St John's Wort", "serotonin syndrome risk")],
    "maoi":            [("Caffeine", "hypertensive crisis risk — avoid stimulants"), ("5-HTP", "serotonin syndrome risk")],
    "thyroid":         [("Calcium", "impairs thyroid medication absorption — take 4hr apart"), ("Iron", "impairs thyroid medication absorption"), ("Magnesium", "impairs thyroid medication absorption")],
    "statin":          [("Red Yeast Rice", "additive myopathy risk"), ("Niacin", "additive myopathy risk")],
    "metformin":       [("Berberine", "additive blood glucose lowering — monitor closely")],
    "aspirin":         [("Omega-3 Fish Oil", "increased bleeding risk at high doses")],
    "beta-blocker":    [("Caffeine", "blunts cardio-protective effect; avoid high doses")],
}


def _check_supplement_interactions(medications_str: str) -> list[str]:
    """Return advisory warnings for supplement-medication interactions."""
    if not medications_str or medications_str.lower() == "none":
        return []
    meds_lower = medications_str.lower()
    warnings: list[str] = []
    for kw, interactions in _SUPPLEMENT_INTERACTIONS.items():
        if kw in meds_lower:
            for supp, risk in interactions:
                warnings.append(f"⚠️ *{supp}* + {kw}: {risk}")
    return warnings


def _default_weights_for(exercise_name: str, user: dict) -> list[float]:
    """Return 5 first-time weight options in kg, matched to the exercise and unit system."""
    name = exercise_name.lower()
    if user.get("units") == "lbs":
        for kw, lbs_vals in _EXERCISE_DEFAULTS_LBS.items():
            if kw in name:
                return [round(v / _LBS_PER_KG, 4) for v in lbs_vals]
        return [round(v / _LBS_PER_KG, 4) for v in [45, 95, 135, 185, 225]]
    for kw, kg_vals in _EXERCISE_DEFAULTS_KG.items():
        if kw in name:
            return [float(v) for v in kg_vals]
    return [20.0, 40.0, 60.0, 80.0, 100.0]


def _score_label(score_1_10: int | float, invert: bool = False) -> str:
    """Convert a 1–10 score to a qualitative label. Set invert=True for stress (high=bad)."""
    s = (11 - score_1_10) if invert else score_1_10
    if s >= 8: return "Great"
    if s >= 6: return "Good"
    if s >= 4: return "Fair"
    return "Poor"


def _next_steps(*items: tuple[str, str]) -> str:
    """Return a contextual 'What's next' footer line. Each item is (command, short label)."""
    parts = " · ".join(f"`{cmd}` {label}" for cmd, label in items)
    return f"\n\n_💡 What's next: {parts}_"


def esc(text: str) -> str:
    """Escape special chars for Telegram legacy Markdown mode."""
    return str(text).replace("_", r"\_").replace("*", r"\*").replace("`", r"\`").replace("[", r"\[")


# ── State persistence ─────────────────────────────────────────────────────────
# User data is saved to a JSON file so it survives bot restarts / redeploys.
# On Railway, mount a volume at /data and set DATA_DIR=/data in env vars.

_STORE_PATH = Path(os.getenv("DATA_DIR", ".")) / "bot_state.json"
_STORE_LOCK = __import__("threading").Lock()


def _load_store() -> dict[int, dict]:
    if _STORE_PATH.exists():
        try:
            raw = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
            return {int(k): v for k, v in raw.items()}
        except Exception as e:
            print(f"Warning: could not load bot state: {e}")
    return {}


def _save_store() -> None:
    with _STORE_LOCK:
        try:
            tmp = _STORE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(user_data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, _STORE_PATH)
        except Exception as e:
            print(f"Warning: could not save bot state: {e}")


# In-memory store: chat_id → {profile, last_analysis, last_plan, conversation_history}
user_data: dict[int, dict] = _load_store()

# chat_id → web user_id, populated after /link succeeds
_linked_user_ids: dict[int, int] = {}


def _eff_uid(chat_id: int, user: dict) -> int:
    """Return web User.id for SQLite writes; falls back to Telegram chat_id if not linked."""
    return _linked_user_ids.get(chat_id) or int(user.get("web_user_id") or chat_id)


# ── Bot→SQLite sync helpers ───────────────────────────────────────────────────
# Each helper is synchronous and safe to call from run_in_executor.
# All use _eff_uid() so rows land under the web User.id when the account is linked.

def _db_sync_profile(chat_id: int, user: dict) -> None:
    """Upsert UserProfile in SQLite from the bot's in-memory profile dict."""
    uid = _eff_uid(chat_id, user)
    p = user.get("profile", {})
    if not p:
        return
    try:
        from database import SessionLocal
        import models as _m
        _db = SessionLocal()
        try:
            row = _db.query(_m.UserProfile).filter(_m.UserProfile.user_id == uid).first()
            if not row:
                row = _m.UserProfile(user_id=uid)
                _db.add(row)
            try:
                row.age = int(p["age"]) if p.get("age") else None
            except (ValueError, TypeError):
                pass
            row.gender = p.get("gender")
            try:
                row.height_cm = float(p["height"]) if p.get("height") else None
            except (ValueError, TypeError):
                pass
            try:
                row.weight_kg = float(p["weight"]) if p.get("weight") else None
            except (ValueError, TypeError):
                pass
            row.goal = p.get("goal")
            row.training_experience = p.get("experience")
            try:
                row.training_days_per_week = int(p["days"]) if p.get("days") else None
            except (ValueError, TypeError):
                pass
            row.dietary_restrictions = p.get("dietary_restrictions")
            row.injuries = p.get("injuries")
            _db.commit()
        finally:
            _db.close()
    except Exception as _e:
        print(f"Warning: profile DB sync failed: {_e}")


def _db_sync_session_start(chat_id: int, user: dict) -> None:
    """Create a WorkoutSession row and store its DB id in user['active_session_db_id']."""
    uid = _eff_uid(chat_id, user)
    try:
        from database import SessionLocal
        import models as _m
        from datetime import datetime, timezone
        _db = SessionLocal()
        try:
            row = _m.WorkoutSession(chat_id=uid, user_id=uid, started_at=datetime.now(timezone.utc))
            _db.add(row)
            _db.commit()
            _db.refresh(row)
            user["active_session_db_id"] = row.id
            _save_store()
        finally:
            _db.close()
    except Exception as _e:
        print(f"Warning: session start DB sync failed: {_e}")


def _db_sync_session_end(chat_id: int, user: dict) -> None:
    """Mark the active WorkoutSession as ended."""
    db_id = user.get("active_session_db_id")
    if not db_id:
        return
    try:
        from database import SessionLocal
        import models as _m
        from datetime import datetime, timezone
        _db = SessionLocal()
        try:
            row = _db.query(_m.WorkoutSession).filter(_m.WorkoutSession.id == db_id).first()
            if row:
                row.ended_at = datetime.now(timezone.utc)
                _db.commit()
            user.pop("active_session_db_id", None)
            _save_store()
        finally:
            _db.close()
    except Exception as _e:
        print(f"Warning: session end DB sync failed: {_e}")


def _db_sync_set(chat_id: int, user: dict, entry: dict) -> None:
    """Append a SetLog row to the active WorkoutSession."""
    db_session_id = user.get("active_session_db_id")
    if not db_session_id:
        return
    try:
        from database import SessionLocal
        import models as _m
        from datetime import datetime, timezone
        _db = SessionLocal()
        try:
            _db.add(_m.SetLog(
                session_id=db_session_id,
                exercise_name=entry["exercise_name"],
                weight_kg=entry.get("weight_kg"),
                reps=entry.get("reps"),
                estimated_1rm=entry.get("estimated_1rm"),
                logged_at=datetime.now(timezone.utc),
            ))
            _db.commit()
        finally:
            _db.close()
    except Exception as _e:
        print(f"Warning: set DB sync failed: {_e}")


def _db_sync_pr(chat_id: int, user: dict, exercise: str, weight_kg: float, reps: int, e1rm: float) -> None:
    """Upsert a PersonalRecord row."""
    uid = _eff_uid(chat_id, user)
    try:
        from database import SessionLocal
        import models as _m
        from datetime import datetime, timezone
        _db = SessionLocal()
        try:
            existing = _db.query(_m.PersonalRecord).filter(
                _m.PersonalRecord.chat_id == uid,
                _m.PersonalRecord.exercise_name == exercise,
            ).first()
            if existing:
                existing.weight_kg = weight_kg
                existing.reps = reps
                existing.estimated_1rm = e1rm
                existing.achieved_at = datetime.now(timezone.utc)
            else:
                _db.add(_m.PersonalRecord(
                    chat_id=uid, exercise_name=exercise,
                    weight_kg=weight_kg, reps=reps, estimated_1rm=e1rm,
                    achieved_at=datetime.now(timezone.utc),
                ))
            _db.commit()
        finally:
            _db.close()
    except Exception as _e:
        print(f"Warning: PR DB sync failed: {_e}")


def _db_sync_meal(chat_id: int, user: dict, entry: dict) -> None:
    """Append a MealLog row."""
    uid = _eff_uid(chat_id, user)
    try:
        from database import SessionLocal
        import models as _m
        _db = SessionLocal()
        try:
            _db.add(_m.MealLog(
                chat_id=uid,
                date=entry["date"],
                description=entry.get("description", ""),
                calories=entry.get("calories"),
                protein_g=entry.get("protein_g"),
                carbs_g=entry.get("carbs_g"),
                fat_g=entry.get("fat_g"),
                macro_source=entry.get("macro_source", "estimated"),
            ))
            _db.commit()
        finally:
            _db.close()
    except Exception as _e:
        print(f"Warning: meal DB sync failed: {_e}")


def _db_sync_measurement(chat_id: int, user: dict, entry: dict) -> None:
    """Append a BodyMeasurement row."""
    uid = _eff_uid(chat_id, user)
    try:
        from database import SessionLocal
        import models as _m
        _db = SessionLocal()
        try:
            _db.add(_m.BodyMeasurement(
                chat_id=uid,
                date=entry["date"],
                body_weight_kg=entry.get("body_weight_kg"),
                waist_cm=entry.get("waist_cm"),
                chest_cm=entry.get("chest_cm"),
                hips_cm=entry.get("hips_cm"),
                left_arm_cm=entry.get("left_arm_cm"),
                right_arm_cm=entry.get("right_arm_cm"),
                left_thigh_cm=entry.get("left_thigh_cm"),
                right_thigh_cm=entry.get("right_thigh_cm"),
            ))
            _db.commit()
        finally:
            _db.close()
    except Exception as _e:
        print(f"Warning: measurement DB sync failed: {_e}")


def _db_sync_plan(chat_id: int, user: dict, plan: dict) -> None:
    """Write generated plan as a WorkoutPlan row (with user_id if linked)."""
    import json as _json
    uid = _eff_uid(chat_id, user)
    if not uid or not plan:
        return
    try:
        from database import SessionLocal
        import models as _m
        _db = SessionLocal()
        try:
            _db.add(_m.WorkoutPlan(
                raw_plan=_json.dumps(plan),
                user_id=uid,
            ))
            _db.commit()
        finally:
            _db.close()
    except Exception as _e:
        print(f"Warning: plan DB sync failed: {_e}")


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

REDDIT_SUBREDDITS = [
    "bodybuilding",
    "naturalbodybuilding",
    "nutrition",
    "fitness",
    "longevity",
    "Supplements",
    "powerlifting",
    "weightlifting",
]




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
            "streak_freezes": [],
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
            "units": "kg", "analyses": [], "streak_freezes": [],
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
    # Normalise curly/smart quotes to ASCII before parsing
    for _ch in ('‘', '’', 'ʼ'):
        s = s.replace(_ch, "'")
    for _ch in ('“', '”'):
        s = s.replace(_ch, '')
    s = s.strip().lower().replace(' ', '')
    # feet-inches: 5'10, 5ft10, 5ft10in, 5feet10, 5feet10in
    m = re.match(r"(\d+)(?:'|ft)\s*(\d+)", s) or re.match(r"(\d+)feet(\d+)", s)
    if m:
        return str(round(int(m.group(1)) * 30.48 + int(m.group(2)) * 2.54))
    # bare feet e.g. 5ft (no inches)
    m = re.match(r"(\d+)ft$", s)
    if m:
        return str(round(int(m.group(1)) * 30.48))
    # bare inches e.g. 68in
    m = re.match(r"(\d+(?:\.\d+)?)in$", s)
    if m:
        return str(round(float(m.group(1)) * 2.54))
    return re.sub(r"cm$", "", s)


def _parse_weight(s: str) -> float:
    s = s.strip().lower().replace(' ', '')
    m = re.match(r"(\d+(?:\.\d+)?)(?:lbs?|pounds?)$", s)
    if m:
        return round(float(m.group(1)) * 0.453592, 4)
    return float(re.sub(r"kg$", "", s))


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


# ── Check-in inline keyboard ──────────────────────────────────────────────────

_STEP_LABELS: dict[str, tuple[str, str, str]] = {
    "sleep":      ("😴", "Sleep quality",          "1=terrible, 10=perfect"),
    "energy":     ("⚡", "Energy levels",          "1=drained, 10=energized"),
    "soreness":   ("🤕", "Muscle soreness (DOMS)",  "1=very sore, 10=fresh"),
    "joint_pain": ("🦴", "Joint / sharp pain",     "1=painful, 10=pain-free"),
    "stress":     ("🧠", "Stress level",            "1=very stressed, 10=calm"),
    "motivation": ("🔥", "Motivation to train",    "1=zero motivation, 10=pumped"),
}


def _score_keyboard(step: str) -> InlineKeyboardMarkup:
    """10-button (2 rows) inline keyboard for a 1-10 check-in score."""
    row1 = [InlineKeyboardButton(str(i), callback_data=f"ci:{step}:{i}") for i in range(1, 6)]
    row2 = [InlineKeyboardButton(str(i), callback_data=f"ci:{step}:{i}") for i in range(6, 11)]
    return InlineKeyboardMarkup([row1, row2])


def _checkins_this_week(user: dict) -> int:
    """Count check-ins recorded in the last 7 days (bot JSON)."""
    from datetime import date, timedelta
    cutoff = str(date.today() - timedelta(days=7))
    return sum(1 for c in user.get("checkins", []) if c.get("date", "") > cutoff)


# ── Workout / plan day keyboards ──────────────────────────────────────────────

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
    one_rm = epley_1rm(weight_kg, reps)
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
        # Use plate-friendly increments for each unit system
        if user.get("units") == "lbs":
            step_s = 5.0 / _LBS_PER_KG    # 5 lbs in kg
            step_l = 10.0 / _LBS_PER_KG   # 10 lbs in kg
        else:
            step_s, step_l = 2.5, 5.0
        opts = sorted({max(0.0, last - step_l), max(0.0, last - step_s),
                       last, last + step_s, last + step_l})
    else:
        opts = _default_weights_for(exercise_name, user)
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
    "Type /help for the full command list, or just start chatting!"
)

_HELP_TEXT = (
    "⚡ *BodyBuilding Coach — Commands*\n\n"
    "*📋 Setup*\n"
    "/profile — Set age, weight, goal, injuries, etc.\n"
    "/plan — Generate or view your workout + diet plan\n"
    "📸 Send a photo — physique analysis\n\n"
    "*🏋️ Logging*\n"
    "/checkin — Daily check-in: sleep, energy, soreness, joints, motivation\n"
    "/workout — Start / end a session\n"
    "/log — Tap-based workout logger\n"
    "/logset bench 100kg 8 — Quick set log\n"
    "/meal 2 eggs oatmeal — Log food + estimate macros\n"
    "/weight 84.5 — Log body weight\n"
    "/measurements — Log body measurements\n\n"
    "*📈 Progress & Analysis*\n"
    "/stats — Personal records ranked by estimated 1RM\n"
    "/progress — 30-day weight, PRs, recovery trend\n"
    "/macros — Today's targets vs. logged\n"
    "/weakpoints — AI imbalance analysis from training data\n"
    "/report — Weekly AI coaching report\n"
    "/streak — Check-in streak + badges\n"
    "/goals — Set target weight / body fat / date\n"
    "/research — Latest PubMed + community fitness insights\n\n"
    "*⚙️ Settings & Integrations*\n"
    "/connect — Link Garmin / MyFitnessPal\n"
    "/mfp sync — Sync today's MFP diary\n"
    "/reminders — Set daily reminders\n"
    "/units — Switch kg ↔ lbs\n"
    "/freeze — Protect today's streak (1 per 30 days)\n"
    "/fridge — Scan fridge photo → macro-matched recipes\n"
    "/peakweek — Contest peak week protocol (prep/cut only)\n"
    "/link — Link Telegram to the web app\n"
    "/link\\_status — Check web-app link status\n"
    "/billing — Billing & partnership info\n\n"
    "*🔒 Privacy*\n"
    "/privacy — View data policy\n"
    "/export — Download all your data (JSON)\n"
    "/delete\\_my\\_data — Erase all data permanently\n\n"
    "💬 Chat anytime — ask questions or tell me to tweak your plan."
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    # Returning user — must have a goal set to be considered complete
    if user.get("profile") and user["profile"].get("goal"):
        goal = user["profile"].get("goal", "?")
        sessions = user.get("session_counter", 0)
        checkins = len(user.get("checkins", []))
        await update.message.reply_text(
            f"👋 Welcome back!\n\n"
            f"Goal: *{goal}* · Sessions logged: *{sessions}* · Check-ins: *{checkins}*\n\n"
            f"Type /plan to see your plan, /checkin to log today, or /help for all commands.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✏️ Update profile", callback_data="prof:menu"),
            ]]),
        )
        return

    # New user or incomplete profile — run onboarding quiz
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💪 Build Muscle (Bulk)", callback_data="onboard:goal:bulk"),
         InlineKeyboardButton("🔥 Lose Fat (Cut)", callback_data="onboard:goal:cut")],
        [InlineKeyboardButton("⚖️ Recomposition", callback_data="onboard:goal:recomp"),
         InlineKeyboardButton("🏋️ Strength", callback_data="onboard:goal:strength")],
        [InlineKeyboardButton("🌱 General Health", callback_data="onboard:goal:health")],
    ])
    await update.message.reply_text(
        "⚡ *Welcome to BodyBuilding Coach AI!*\n\n"
        "Let's set up your profile in 3 quick questions.\n\n"
        "*What's your main goal?*",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def handle_onboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle onboard:goal:X / onboard:exp:X / onboard:days:X inline keyboard steps."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    parts = query.data.split(":")  # ["onboard", step, value]
    step, value = parts[1], parts[2]

    if step == "goal":
        user.setdefault("profile", {})["goal"] = value
        user["profile"]["goal_set_date"] = _today()
        _save_store()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌱 Beginner (< 1 year)", callback_data="onboard:exp:beginner"),
             InlineKeyboardButton("💪 Intermediate (1-3 yrs)", callback_data="onboard:exp:intermediate")],
            [InlineKeyboardButton("🏆 Advanced (3+ years)", callback_data="onboard:exp:advanced")],
        ])
        await query.edit_message_text(
            f"✅ Goal set: *{value}*\n\n*What's your training experience?*",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    elif step == "exp":
        user["profile"]["experience"] = value
        _save_store()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("2", callback_data="onboard:days:2"),
             InlineKeyboardButton("3", callback_data="onboard:days:3"),
             InlineKeyboardButton("4", callback_data="onboard:days:4")],
            [InlineKeyboardButton("5", callback_data="onboard:days:5"),
             InlineKeyboardButton("6", callback_data="onboard:days:6")],
        ])
        await query.edit_message_text(
            f"✅ Experience: *{value}*\n\n*How many days per week can you train?*",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    elif step == "days":
        user["profile"]["days"] = value
        _save_store()
        await query.edit_message_text(
            f"✅ Training days: *{value}/week*\n\n"
            "Which unit system do you prefer?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🇺🇸 Imperial  (lbs / ft-in)", callback_data="onboard:units:lbs"),
                    InlineKeyboardButton("🌍 Metric  (kg / cm)", callback_data="onboard:units:kg"),
                ],
            ]),
        )

    elif step == "units":
        user["units"] = value if value in ("kg", "lbs") else "kg"
        _save_store()
        await query.edit_message_text(
            f"✅ Units: *{'Imperial (lbs / ft-in)' if value == 'lbs' else 'Metric (kg / cm)'}*\n\n"
            "What's your gender? _(helps personalise your calorie and hormone coaching)_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👨 Male", callback_data="onboard:gender:male"),
                 InlineKeyboardButton("👩 Female", callback_data="onboard:gender:female")],
                [InlineKeyboardButton("⚧️ Non-binary", callback_data="onboard:gender:non-binary"),
                 InlineKeyboardButton("⏭️ Skip — generate now", callback_data="onboard:skip:gender")],
            ]),
        )

    elif step == "gender":
        user["profile"]["gender"] = value
        _save_store()
        user["active_command"] = "onboard_text"
        user["command_state"] = {"step": "stats"}
        _save_store()
        # Show unit-appropriate example
        if _wu(user) == "lbs":
            example = "`23 / 5'7\" / 155lbs`"
        else:
            example = "`23 / 178cm / 82kg`"
        await query.edit_message_text(
            f"✅ Gender: *{value}*\n\n"
            "Last step — type your *age, height and weight* so I can personalise your macros.\n\n"
            f"Example: {example}\n\n"
            "_Tap Skip to generate your plan now with just your goal and training days:_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭️ Skip — generate now", callback_data="onboard:skip:stats"),
            ]]),
        )

    elif step == "skip":
        user["active_command"] = None
        user["command_state"] = {}
        _save_store()
        await query.edit_message_text("🧬 Building your personalised plan… (30-60 seconds)")
        ctx_str = _get_bot_context_str(user)
        loop = asyncio.get_running_loop()
        try:
            plan = await loop.run_in_executor(
                None, _generate_plan_from_profile, user["profile"], ctx_str, user.get("units", "kg")
            )
            user["last_plan"] = plan
            _save_store()
            await asyncio.get_running_loop().run_in_executor(None, _db_sync_plan, chat_id, user, plan)
            try:
                await query.delete_message()
            except Exception:
                pass
            await _send_plan(update, plan)
            await update.effective_chat.send_message(
                "📸 *Want a more personalised plan?*\n\n"
                "Send me a front + back + side photo and I'll update it based on "
                "your specific physique and muscle development.\n\n"
                "Tell me to change anything, or type `/plan new` to regenerate.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.effective_chat.send_message(f"❌ Plan generation failed: {e}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_HELP_TEXT, parse_mode="Markdown")


# ── Profile / Goals / Measurements inline-keyboard helpers ───────────────────

_HIDDEN_PROFILE_KEYS: frozenset[str] = frozenset({"goal_set_date"})


def _profile_menu_keyboard(profile: dict, user: dict | None = None) -> InlineKeyboardMarkup:
    """Inline keyboard for /profile — each button shows the current value."""
    def _val(field: str, default: str = "—") -> str:
        return str(profile.get(field, default))

    _units = (user or {}).get("units", "kg")
    _height_unit = "cm" if _units == "kg" else "in"
    _weight_unit = "kg" if _units == "kg" else "lbs"

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🎯 Goal: {_val('goal')}", callback_data="prof:f:goal"),
            InlineKeyboardButton(f"📊 Exp: {_val('experience')}", callback_data="prof:f:experience"),
        ],
        [
            InlineKeyboardButton(f"📅 Days/wk: {_val('days')}", callback_data="prof:f:days"),
            InlineKeyboardButton(f"⚤ Gender: {_val('gender')}", callback_data="prof:f:gender"),
        ],
        [
            InlineKeyboardButton(f"📈 Periodization: {_val('periodization', 'linear')}", callback_data="prof:f:periodization"),
            InlineKeyboardButton(f"🕐 Workout time: {_val('workout_time', 'variable')}", callback_data="prof:f:workout_time"),
        ],
        [
            InlineKeyboardButton(f"🎂 Age: {_val('age')}", callback_data="prof:input:age"),
            InlineKeyboardButton(f"📏 Height: {_val('height')} {_height_unit}", callback_data="prof:input:height"),
            InlineKeyboardButton(f"⚖️ Weight: {_val('weight')} {_weight_unit}", callback_data="prof:input:weight"),
        ],
        [
            InlineKeyboardButton(
                f"📸 Physique photos: {'✅ ON' if _val('physique_analysis', 'on') == 'on' else '🚫 OFF'} — tap to toggle",
                callback_data="prof:toggle:physique_analysis",
            ),
        ],
        [
            InlineKeyboardButton(f"🩹 Injuries: {_val('injuries')}", callback_data="prof:input:injuries"),
        ],
        [
            InlineKeyboardButton(f"🥗 Diet: {_val('dietary_restrictions')}", callback_data="prof:input:dietary_restrictions"),
        ],
        [
            InlineKeyboardButton(f"💊 Medications: {_val('medications', 'none')}", callback_data="prof:input:medications"),
        ],
        [
            InlineKeyboardButton("✏️ Type custom (field=value)", callback_data="prof:custom"),
        ],
        [
            InlineKeyboardButton("🚀 Generate my plan", callback_data="prof:generate"),
        ],
    ])


def _goals_menu_keyboard(user: dict) -> InlineKeyboardMarkup:
    """Inline keyboard for /goals — type selector and target inputs."""
    goals_data = user.get("goals", [])
    active = next((g for g in reversed(goals_data) if g.get("is_active")), None)
    goal_type = active["goal_type"].title() if active else "—"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📌 Current: {goal_type}", callback_data="goals:noop")],
        [
            InlineKeyboardButton("💪 Bulk", callback_data="goals:type:bulk"),
            InlineKeyboardButton("✂️ Cut", callback_data="goals:type:cut"),
            InlineKeyboardButton("🔄 Recomp", callback_data="goals:type:recomp"),
        ],
        [
            InlineKeyboardButton("🏋️ Strength", callback_data="goals:type:strength"),
            InlineKeyboardButton("🏆 Prep", callback_data="goals:type:prep"),
            InlineKeyboardButton("❤️ Health", callback_data="goals:type:health"),
        ],
        [
            InlineKeyboardButton("⚖️ Target weight", callback_data="goals:input:weight"),
            InlineKeyboardButton("📉 Target body fat %", callback_data="goals:input:bf"),
        ],
        [
            InlineKeyboardButton("📅 Target date (YYYY-MM-DD)", callback_data="goals:input:date"),
        ],
        [
            InlineKeyboardButton("✏️ Type full command", callback_data="goals:custom"),
        ],
    ])


def _measurements_menu_keyboard(last: dict | None, user: dict | None = None) -> InlineKeyboardMarkup:
    """Inline keyboard for /measurements — each button shows the last logged value."""
    def _val(key: str) -> str:
        if not last:
            return "—"
        v = last.get(key)
        return str(v) if v is not None else "—"

    _units = (user or {}).get("units", "kg")
    _wu_label = "kg" if _units == "kg" else "lbs"
    _cm_label = "cm" if _units == "kg" else "in"

    def _wval(key: str) -> str:
        if not last:
            return "—"
        v = last.get(key)
        if v is None:
            return "—"
        if _units == "lbs":
            return f"{round(float(v) * 2.20462, 1)}"
        return str(v)

    def _cmval(key: str) -> str:
        if not last:
            return "—"
        v = last.get(key)
        if v is None:
            return "—"
        if _units == "lbs":
            return f"{round(float(v) * 0.393701, 1)}"
        return str(v)

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"⚖️ Weight: {_wval('body_weight_kg')} {_wu_label}", callback_data="meas:input:weight"),
            InlineKeyboardButton(f"📏 Waist: {_cmval('waist_cm')} {_cm_label}", callback_data="meas:input:waist"),
        ],
        [
            InlineKeyboardButton(f"🫀 Chest: {_cmval('chest_cm')} {_cm_label}", callback_data="meas:input:chest"),
            InlineKeyboardButton(f"🍑 Hips: {_cmval('hips_cm')} {_cm_label}", callback_data="meas:input:hips"),
        ],
        [
            InlineKeyboardButton(f"💪 Arm: {_cmval('left_arm_cm')} {_cm_label}", callback_data="meas:input:arm"),
            InlineKeyboardButton(f"🦵 Thigh: {_cmval('left_thigh_cm')} {_cm_label}", callback_data="meas:input:thigh"),
        ],
        [
            InlineKeyboardButton("✏️ Type multiple fields at once", callback_data="meas:custom"),
        ],
    ])


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    profile = user["profile"]

    if not context.args:
        current = (
            "\n".join(f"• {k}: {esc(str(v))}" for k, v in profile.items() if k not in _HIDDEN_PROFILE_KEYS)
            if profile
            else "Not set yet."
        )
        await update.message.reply_text(
            f"*Your Profile*\n{current}\n\nTap a field to update it:",
            parse_mode="Markdown",
            reply_markup=_profile_menu_keyboard(profile, user),
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
                try:
                    value = _parse_weight(value)
                except (ValueError, TypeError):
                    pass  # store raw string if unparseable
            elif key == "goal" and profile.get("goal") != value:
                profile["goal_set_date"] = _today()
            profile[key] = value

    user["profile"] = profile
    _save_store()
    await update.message.reply_text(
        "✅ Profile saved!\n\n"
        "Send a photo for physique analysis, or type /plan to get your plan now."
    )


async def handle_profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route prof:* callback queries for the /profile inline keyboard."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    profile = user["profile"]
    data = query.data
    parts = data.split(":", 3)
    action = parts[1] if len(parts) > 1 else ""

    _OPTIONS: dict[str, list[str]] = {
        "goal":              ["bulk", "cut", "recomp", "strength", "prep", "health", "maintain"],
        "experience":        ["beginner", "intermediate", "advanced"],
        "days":              ["2", "3", "4", "5", "6"],
        "gender":            ["male", "female", "non-binary", "other"],
        "periodization":     ["linear", "undulating", "block"],
        "workout_time":      ["morning", "afternoon", "evening", "variable"],
    }

    def _current_summary() -> str:
        return "\n".join(f"• {k}: {esc(str(v))}" for k, v in profile.items() if k not in _HIDDEN_PROFILE_KEYS) or "Not set yet."

    if action == "f":
        field = parts[2] if len(parts) > 2 else ""
        opts = _OPTIONS.get(field)
        if opts:
            rows = [
                [InlineKeyboardButton(o.title(), callback_data=f"prof:v:{field}:{o}") for o in opts[i:i+3]]
                for i in range(0, len(opts), 3)
            ]
            rows.append([InlineKeyboardButton("← Back to profile", callback_data="prof:menu")])
            label = field.replace("_", " ").title()
            await query.edit_message_text(
                f"*{label}* — choose one:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(rows),
            )
        else:
            user["active_command"] = "profile_input"
            user["command_state"] = {"field": field}
            _save_store()
            await query.edit_message_text(f"Type your *{esc(field)}* and send it:", parse_mode="Markdown")

    elif action == "toggle":
        field = parts[2] if len(parts) > 2 else ""
        current = profile.get(field, "on")
        profile[field] = "off" if current == "on" else "on"
        user["profile"] = profile
        _save_store()
        await query.edit_message_text(
            f"*Your Profile*\n{_current_summary()}\n\nTap a field to update it:",
            parse_mode="Markdown",
            reply_markup=_profile_menu_keyboard(profile, user),
        )

    elif action == "v":
        field = parts[2] if len(parts) > 2 else ""
        value = parts[3] if len(parts) > 3 else ""
        if field == "goal" and profile.get("goal") != value:
            profile["goal_set_date"] = _today()
        profile[field] = value
        user["profile"] = profile
        _save_store()
        await asyncio.get_running_loop().run_in_executor(None, _db_sync_profile, chat_id, user)
        await query.edit_message_text(
            f"*Your Profile*\n{_current_summary()}\n\nTap a field to update it:",
            parse_mode="Markdown",
            reply_markup=_profile_menu_keyboard(profile, user),
        )

    elif action == "input":
        field = parts[2] if len(parts) > 2 else ""
        user["active_command"] = "profile_input"
        user["command_state"] = {"field": field}
        _save_store()
        _prompts = {
            "age":     "Type your age (e.g. `28`):",
            "height":  "Type your height (e.g. `5'10\"` or `178cm`):",
            "weight":  "Type your weight (e.g. `85kg` or `188lbs`):",
            "injuries": "Describe any injuries or pain areas (e.g. `bad left knee, shoulder impingement`):",
            "dietary_restrictions": "Describe your dietary restrictions (e.g. `vegan`, `gluten-free`, `lactose intolerant`, `nut allergy`, `no pork`):",
            "email":   "Type your email address:",
            "medications": "List any medications or supplements you already take (e.g. `metformin, SSRIs, blood thinners`). Type 'none' if none:",
        }
        await query.edit_message_text(
            _prompts.get(field, f"Type your *{esc(field)}*:"),
            parse_mode="Markdown",
        )

    elif action == "menu":
        await query.edit_message_text(
            f"*Your Profile*\n{_current_summary()}\n\nTap a field to update it:",
            parse_mode="Markdown",
            reply_markup=_profile_menu_keyboard(profile, user),
        )

    elif action == "custom":
        user["active_command"] = "profile_input"
        user["command_state"] = {"field": "_custom"}
        _save_store()
        await query.edit_message_text(
            "Type one or more `field=value` pairs, e.g.:\n"
            "`age=28 height=178cm weight=85kg`\n\n"
            "Fields: goal, experience, days, gender, age, height, weight, injuries, email, physique\\_analysis",
            parse_mode="Markdown",
        )

    elif action == "generate":
        remaining = _check_cooldown(_plan_cooldowns, chat_id, PLAN_COOLDOWN)
        if remaining:
            await query.answer(f"⏳ Wait {remaining}s before regenerating.", show_alert=True)
            return
        await query.edit_message_text("🧬 Building your plan… (30-60 seconds)")
        ctx_str = _get_bot_context_str(user)
        loop = asyncio.get_running_loop()
        try:
            import garmin_service as _gs_prof
            _gd_prof = _gs_prof.get_cached(chat_id)
        except Exception:
            _gd_prof = None
        try:
            if user["last_analysis"]:
                plan = await loop.run_in_executor(
                    None, _generate_plan, user["last_analysis"], user["profile"], ctx_str, user.get("units", "kg"), _gd_prof
                )
            else:
                plan = await loop.run_in_executor(
                    None, _generate_plan_from_profile, user["profile"], ctx_str, user.get("units", "kg"), _gd_prof
                )
            user["last_plan"] = plan
            _save_store()
            await asyncio.get_running_loop().run_in_executor(None, _db_sync_plan, chat_id, user, plan)
            try:
                await query.delete_message()
            except Exception:
                pass
            await _send_plan(update, plan)
            await update.effective_chat.send_message(
                "💬 Your plan is built from your profile"
                + (" + photo analysis" if user["last_analysis"] else "")
                + ".\nTell me to adjust anything, or type `/plan new` to regenerate.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.effective_chat.send_message(f"❌ Plan generation failed: {e}")


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    user["active_command"] = None
    user["command_state"] = {}

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


def _workout_load_summary(user: dict) -> str:
    """One-line summary of training load for the last 7 days."""
    cutoff = str(_date.today() - timedelta(days=7))
    recent = [s for s in user.get("set_logs", []) if s.get("date", "") >= cutoff]
    if not recent:
        return ""
    volume = sum(s.get("weight_kg", 0) * s.get("reps", 0) for s in recent)
    sessions = len({s.get("session_id") for s in recent if s.get("session_id") is not None})
    return f"Training load 7d: {sessions} sessions, {volume:,.0f}kg total volume"


def _nutrition_today_summary(user: dict) -> str:
    """Today's macro totals from meal_logs, empty string if no meals logged today."""
    today = _today()
    today_meals = [m for m in user.get("meal_logs", []) if m.get("date") == today]
    if not today_meals:
        return ""
    cals = round(sum(m.get("calories", 0) for m in today_meals))
    prot = round(sum(m.get("protein_g", 0) for m in today_meals))
    carbs = round(sum(m.get("carbs_g", 0) for m in today_meals))
    fat = round(sum(m.get("fat_g", 0) for m in today_meals))
    return f"Nutrition today: {cals} kcal | {prot}g protein | {carbs}g carbs | {fat}g fat"


def _garmin_review_lines(garmin_data: dict, age: int | None = None) -> list[str]:
    """Human-readable metric parts from a Garmin cache entry for display and context."""
    parts: list[str] = []
    if garmin_data.get("sleep_duration_hrs"):
        hrs = garmin_data["sleep_duration_hrs"]
        stage_parts = []
        if garmin_data.get("deep_sleep_mins"):
            stage_parts.append(f"Deep {garmin_data['deep_sleep_mins']}min")
        if garmin_data.get("rem_sleep_mins"):
            stage_parts.append(f"REM {garmin_data['rem_sleep_mins']}min")
        stage_text = f" ({', '.join(stage_parts)})" if stage_parts else ""
        raw_score = garmin_data.get("sleep_score_1_10")
        quality = f" — {_score_label(raw_score)}" if raw_score else ""
        parts.append(f"Sleep {hrs:.1f}h{quality}{stage_text}")
    if garmin_data.get("body_battery_end") is not None:
        parts.append(f"Body Battery {garmin_data['body_battery_end']}%")
    if garmin_data.get("hrv_ms"):
        parts.append(f"HRV {garmin_data['hrv_ms']:.0f}ms")
    if garmin_data.get("resting_hr_bpm"):
        parts.append(f"RHR {garmin_data['resting_hr_bpm']}bpm")
    if garmin_data.get("stress_score_1_10"):
        parts.append(f"Stress — {_score_label(garmin_data['stress_score_1_10'], invert=True)}")
    if garmin_data.get("avg_spo2_pct"):
        parts.append(f"SpO2 {garmin_data['avg_spo2_pct']:.0f}%")
    if garmin_data.get("avg_respiration_rpm"):
        parts.append(f"Resp {garmin_data['avg_respiration_rpm']:.0f}rpm")
    if garmin_data.get("steps_yesterday"):
        parts.append(f"Steps {garmin_data['steps_yesterday']:,}")
    if garmin_data.get("vo2_max"):
        vo2 = garmin_data["vo2_max"]
        if age:
            max_hr = 220 - age
            z2_lo, z2_hi = int(max_hr * 0.60), int(max_hr * 0.70)
            z3_lo, z3_hi = int(max_hr * 0.70), int(max_hr * 0.80)
            parts.append(
                f"VO2max {vo2:.0f} → Zone2 {z2_lo}–{z2_hi}bpm | Zone3 {z3_lo}–{z3_hi}bpm"
            )
        else:
            parts.append(f"VO2max {vo2:.0f}")
    return parts


def _garmin_to_scores(garmin_data: dict, user: dict) -> dict[str, int]:
    """Derive all four check-in scores from Garmin metrics and training history."""
    # Sleep — Garmin score > duration estimate > neutral default
    if garmin_data.get("sleep_score_1_10"):
        sleep = garmin_data["sleep_score_1_10"]
        # Bonus for good deep + REM sleep
        if garmin_data.get("deep_sleep_mins", 0) > 90 and garmin_data.get("rem_sleep_mins", 0) > 60:
            sleep = min(10, sleep + 1)
    elif garmin_data.get("sleep_duration_hrs"):
        hrs = garmin_data["sleep_duration_hrs"]
        sleep = 2 if hrs < 5 else (4 if hrs < 6 else (6 if hrs < 7 else (8 if hrs < 8 else 9)))
    else:
        sleep = 6

    # Energy — Body Battery is the best proxy; fall back to HRV
    if garmin_data.get("body_battery_end") is not None:
        energy = max(1, min(10, round(garmin_data["body_battery_end"] / 10)))
    elif garmin_data.get("hrv_ms"):
        hrv = garmin_data["hrv_ms"]
        energy = 4 if hrv < 30 else (5 if hrv < 40 else (7 if hrv < 55 else 8))
    else:
        energy = 6

    # Soreness — estimated from training load in the last 48h
    cutoff_48h = str(_date.today() - timedelta(days=2))
    recent = [s for s in user.get("set_logs", []) if s.get("date", "") >= cutoff_48h]
    vol = sum(s.get("weight_kg", 0) * s.get("reps", 0) for s in recent)
    soreness = 4 if vol > 5000 else (6 if vol > 2000 else 7)

    # Stress — directly from Garmin
    stress = garmin_data.get("stress_score_1_10") or 6

    return {"sleep": sleep, "energy": energy, "soreness": soreness, "joint_pain": 10, "stress": stress, "motivation": 7}


async def cmd_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    user["active_command"] = None
    user["command_state"] = {}

    # Warn if already checked in today (allow update via inline format)
    if not context.args and any(c.get("date") == _today() for c in user.get("checkins", [])):
        await update.message.reply_text(
            "✅ You've already checked in today!\n\n"
            "To update it, use the inline format:\n"
            "`/checkin sleep=8 energy=7 soreness=4 stress=3 joint_pain=9 motivation=8`",
            parse_mode="Markdown",
        )
        return

    # Allow inline: /checkin sleep=7 energy=6 soreness=5 stress=4 joint_pain=9 motivation=8
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
            # Fill optional fields with interactive-flow defaults if not provided
            data.setdefault("joint_pain", 10)
            data.setdefault("motivation", 7)
            await _finish_checkin(update, user, data)
            return

    # Live Garmin fetch — always pull fresh data at check-in time, fall back to cache
    garmin_data = None
    if user.get("garmin_email") and user.get("garmin_pass_enc"):
        try:
            import garmin_service
            await update.effective_chat.send_action("typing")
            # Run the blocking network call off the event loop so the bot stays responsive
            garmin_data = await asyncio.get_running_loop().run_in_executor(
                None,
                garmin_service.fetch_and_cache,
                chat_id, user["garmin_email"], user["garmin_pass_enc"],
            )
        except Exception as e:
            print(f"Warning: live Garmin fetch failed for chat_id={chat_id}: {e}")
            try:
                garmin_data = garmin_service.get_cached(chat_id)
            except Exception:
                pass

    _all_steps = list(_STEP_LABELS.keys())  # ["sleep", "energy", "soreness", "stress"]

    # Garmin path — fully automatic when any useful metric is available
    _garmin_useful = garmin_data and any(
        garmin_data.get(k)
        for k in ("sleep_score_1_10", "sleep_duration_hrs", "hrv_ms",
                  "resting_hr_bpm", "stress_score_1_10", "body_battery_end")
    )
    if garmin_data:
        if _garmin_useful:
            # Derive all 4 scores automatically — zero questions asked
            auto_scores = _garmin_to_scores(garmin_data, user)
            workout_line = _workout_load_summary(user)
            await _finish_checkin(
                update, user, auto_scores,
                garmin_data=garmin_data,
                workout_summary=workout_line,
                nutrition_summary=_nutrition_today_summary(user),
                auto_filled=True,
            )
            return
        else:
            # Connected but watch hasn't synced yet
            await update.message.reply_text(
                "📡 *Garmin is connected but yesterday's data isn't available yet.*\n\n"
                "Make sure your watch has synced to the Garmin Connect app, then try again.\n\n"
                "Answering manually for now:",
                parse_mode="Markdown",
            )

    # Manual multi-step flow (no Garmin or data unavailable)
    user["active_command"] = "checkin"
    user["command_state"] = {
        "step": 0,
        "data": {},
        "remaining_steps": _all_steps,
        "garmin_data": garmin_data,
        "workout_summary": _workout_load_summary(user),
        "nutrition_summary": _nutrition_today_summary(user),
    }
    _save_store()
    emoji, label, hint = _STEP_LABELS["sleep"]
    await update.message.reply_text(
        f"{emoji} *{label}?* _{hint}_",
        parse_mode="Markdown",
        reply_markup=_score_keyboard("sleep"),
    )


async def _handle_checkin_step(update: Update, user: dict, text: str) -> None:
    """Text fallback for check-in steps (used when user types instead of tapping a button)."""
    state = user["command_state"]
    remaining = state.get("remaining_steps", list(_STEP_LABELS.keys()))

    try:
        score = max(1, min(10, int(text.strip())))
    except ValueError:
        await update.message.reply_text(
            "Please enter a number from 1 to 10, or tap a button above.",
        )
        return

    step = state["step"]
    key = remaining[step]
    state["data"][key] = score
    state["step"] = step + 1

    if state["step"] < len(remaining):
        next_key = remaining[state["step"]]
        emoji, label, hint = _STEP_LABELS[next_key]
        await update.message.reply_text(
            f"{emoji} *{label}?* _{hint}_",
            parse_mode="Markdown",
            reply_markup=_score_keyboard(next_key),
        )
    else:
        user["active_command"] = None
        await _finish_checkin(
            update, user, state["data"],
            garmin_data=state.get("garmin_data"),
            workout_summary=state.get("workout_summary", ""),
            nutrition_summary=state.get("nutrition_summary", ""),
        )


def _get_bot_context_str(user: dict) -> str:
    """Return a rich context string for AI prompts built from bot JSON user data."""
    try:
        return bot_json_context_block(user)
    except Exception as e:
        print(f"Warning: bot context build failed: {e}")
        return ""


async def _finish_checkin(
    update: Update,
    user: dict,
    data: dict,
    garmin_data: dict | None = None,
    workout_summary: str = "",
    nutrition_summary: str = "",
    auto_filled: bool = False,
) -> None:
    chat_id = update.effective_chat.id
    msg = await update.effective_chat.send_message("Scoring your recovery…")

    # Build context string enriched with today's objective data
    ctx_str = _get_bot_context_str(user)
    age_val = None
    try:
        age_val = int(user["profile"].get("age", 0)) or None
    except (TypeError, ValueError):
        pass
    if garmin_data:
        garmin_parts = _garmin_review_lines(garmin_data, age=age_val)
        if garmin_parts:
            ctx_str += f"\nGarmin today: {' | '.join(garmin_parts)}"
    if workout_summary:
        ctx_str += f"\n{workout_summary}"
    if nutrition_summary:
        ctx_str += f"\n{nutrition_summary}"

    try:
        from claude_service import generate_recovery_insight
        score, tip = generate_recovery_insight(
            data.get("sleep", 5), data.get("energy", 5), data.get("soreness", 5), data.get("stress", 5),
            user["profile"] or None,
            ctx_str,
        )
    except Exception:
        jp = data.get("joint_pain", 10)
        mot = data.get("motivation", 7)
        score = round((data["sleep"] + data["energy"] + (11 - data["soreness"]) + (11 - data["stress"]) + jp + mot) / 6 * 10)
        tip = "Listen to your body and train accordingly today."

    joint_pain = data.get("joint_pain", 10)
    motivation = data.get("motivation", 7)
    entry = {
        "date": _today(),
        "sleep_score": data.get("sleep", 5),
        "energy_score": data.get("energy", 5),
        "soreness_score": data.get("soreness", 5),
        "joint_pain_score": joint_pain,
        "stress_score": data.get("stress", 5),
        "motivation_score": motivation,
        "recovery_score": score,
        "coaching_tip": tip,
        "hrv_ms": garmin_data.get("hrv_ms") if garmin_data else None,
        "resting_hr_bpm": garmin_data.get("resting_hr_bpm") if garmin_data else None,
        "sleep_duration_hrs": garmin_data.get("sleep_duration_hrs") if garmin_data else None,
        "data_source": "garmin" if garmin_data else "manual",
    }
    # Overwrite same-day entry if one exists, otherwise append
    today = entry["date"]
    user["checkins"] = [c for c in user["checkins"] if c.get("date") != today]
    user["checkins"].append(entry)
    user["checkins"] = user["checkins"][-90:]
    _save_store()

    # First check-in milestone hint
    if len(user["checkins"]) == 1:
        asyncio.create_task(update.effective_chat.send_message(
            "💡 _First check-in done! Keep the streak going — /report after 7 days shows your weekly coaching summary._"
        ))

    # Bridge to SQLite so build_context() and the web app can see bot check-ins
    try:
        from database import SessionLocal
        from models import DailyCheckIn as _DailyCheckInModel
        _db = SessionLocal()
        _uid = _eff_uid(chat_id, user)
        try:
            _existing = _db.query(_DailyCheckInModel).filter(
                _DailyCheckInModel.chat_id == _uid,
                _DailyCheckInModel.date == entry["date"],
            ).first()
            if not _existing:
                _row = _DailyCheckInModel(
                    chat_id=_uid,
                    date=entry["date"],
                    sleep_score=entry["sleep_score"],
                    energy_score=entry["energy_score"],
                    soreness_score=entry["soreness_score"],
                    stress_score=entry["stress_score"],
                    recovery_score=entry["recovery_score"],
                    coaching_tip=entry["coaching_tip"],
                    hrv_ms=entry.get("hrv_ms"),
                    resting_hr_bpm=entry.get("resting_hr_bpm"),
                    sleep_duration_hrs=entry.get("sleep_duration_hrs"),
                    data_source=entry.get("data_source", "manual"),
                )
                _db.add(_row)
                _db.commit()
        finally:
            _db.close()
    except Exception as e:
        print(f"Warning: Failed to write checkin to SQLite: {e}")

    bar = "🟢" if score >= 75 else ("🟡" if score >= 50 else "🔴")

    # Calculate current streak (checkin dates + freeze dates)
    checkin_dates = {c["date"] for c in user["checkins"]}
    freeze_dates = set(user.get("streak_freezes", []))
    sorted_dates = sorted(checkin_dates | freeze_dates, reverse=True)
    streak_count = 0
    for d in sorted_dates:
        dt = _date.fromisoformat(d)
        expected = _date.today() - timedelta(days=streak_count)
        if dt == expected:
            streak_count += 1
        else:
            break

    streak_text = f"  🔥 *{streak_count}-day streak!*" if streak_count >= 2 else ""
    deload_hint = ""
    if score < 50:
        deload_hint = "\n\n⚠️ _Recovery is low — consider a deload or active recovery session today._"

    deload_trend_alert = ""
    prior_checkins = sorted(
        [c for c in user["checkins"] if c.get("date", "") < _today()],
        key=lambda c: c.get("date", "")
    )[-5:]
    if len(prior_checkins) >= 4:
        poor_count = sum(1 for c in prior_checkins if c.get("recovery_score", 100) < 55)
        if poor_count >= 3:
            deload_trend_alert = (
                f"\n\n🔴 *Deload Week Overdue* — {poor_count} of your last {len(prior_checkins)} "
                "check-ins showed low recovery. Schedule a deload this week: cut volume by 40%, "
                "keep frequency, use lighter loads. Your body needs to consolidate its gains."
            )

    # Joint pain red flag
    joint_alert = ""
    if joint_pain <= 3:
        joint_alert = (
            "\n\n🚨 *Joint/sharp pain reported* — avoid loading that area today. "
            "If pain persists 48h+, see a physio."
        )

    # Sleep hygiene tip when sleep quality is poor
    sleep_tip = ""
    if data["sleep"] <= 5:
        sleep_tip = (
            "\n\n😴 _Sleep tip: Aim for a consistent sleep/wake time, avoid screens 1h before bed, "
            "keep the room cool (18–20°C), and consider 300–400mg magnesium glycinate before bed._"
        )

    # Build optional data sections (only shown when data is present)
    garmin_section = ""
    if garmin_data:
        garmin_parts = _garmin_review_lines(garmin_data, age=age_val)
        if garmin_parts:
            garmin_section = f"\n📡 *Garmin:* {' | '.join(garmin_parts)}"

    load_section = f"\n🏋️ *Load:* _{workout_summary}_" if workout_summary else ""

    nutrition_section = ""
    if nutrition_summary:
        # Strip the "Nutrition today: " prefix for compact display
        nutr_display = nutrition_summary.replace("Nutrition today: ", "")
        nutrition_section = f"\n🍽️ *Nutrition:* {nutr_display}"

    motivation_alert = ""
    if motivation <= 3:
        motivation_alert = "\n\n💤 _Motivation is low — this is normal. Show up anyway; the session will feel better once you start._"

    # HRV methodology transparency
    hrv_note = ""
    if garmin_data and garmin_data.get("hrv_ms"):
        hrv_note = "\n_HRV = 7-day rolling average (RMSSD, Garmin). Wearable sleep staging ≈70–80% accurate vs clinical polysomnography._"

    scores_line = (
        "\n📲 _Auto-filled from Garmin_"
        if auto_filled else
        f"\n⚡ Energy: {data['energy']}/10  🤕 Soreness: {data['soreness']}/10  🦴 Joints: {joint_pain}/10  🔥 Motivation: {motivation}/10"
    )

    await msg.edit_text(
        f"✅ *Recovery Check-in*\n\n"
        f"{bar} Recovery Score: *{score}/100*{streak_text}\n"
        f"_(0 = completely exhausted · 100 = peak readiness)_"
        f"{garmin_section}{load_section}{nutrition_section}"
        f"{scores_line}\n\n"
        f"💡 _{tip}_{deload_hint}{joint_alert}{motivation_alert}{sleep_tip}{hrv_note}{deload_trend_alert}"
        + _next_steps(
            ("/workout start", "train now"),
            ("/progress", "see trends"),
            ("/report", "weekly summary"),
        ),
        parse_mode="Markdown",
    )



async def cmd_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    prev_active = user.get("active_command")
    user["active_command"] = None
    user["command_state"] = {}
    sub = context.args[0].lower() if context.args else ""
    if prev_active and prev_active not in ("checkin",) and sub in ("start", "end"):
        pass  # silently discard stale state for non-critical commands

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
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _db_sync_session_start, chat_id, user)

        readiness_note = ""
        today_str = _today()
        today_checkin = next(
            (c for c in reversed(user.get("checkins", [])) if c.get("date") == today_str),
            None,
        )
        if today_checkin:
            rec = today_checkin.get("recovery_score", 70)
            if rec < 50:
                readiness_note = (
                    f"\n\n⚠️ *Recovery {rec}/100 — Low readiness.* "
                    "Reduce working weight by 5–10% and drop one set per exercise. "
                    "A quality sub-max session beats grinding while depleted."
                )
            elif rec >= 85:
                readiness_note = (
                    f"\n\n✨ *Recovery {rec}/100 — You're primed!* "
                    "Great day to push for PRs or add an extra set where you feel strong."
                )
            else:
                readiness_note = f"\n\n📊 *Recovery: {rec}/100*"

        exercises = _get_session_exercises(user)
        plan_text = ""
        if user.get("last_plan"):
            session_day = user["command_state"].get("active_session_day") or _date.today().strftime("%A")
            days = user["last_plan"].get("workout", {}).get("days", [])
            today_day = next((d for d in days if d.get("day", "").lower() == session_day.lower()), None)
            focus = today_day.get("focus", "") if today_day else ""
            plan_text = f"\n*{session_day} — {focus}*" if focus else ""

        await update.message.reply_text(
            f"🏋️ *Session #{sid} started!*{plan_text}{readiness_note}\n\nTap an exercise to log a set:",
            parse_mode="Markdown",
            reply_markup=_ex_keyboard(exercises, has_session=True),
        )

    elif sub == "end":
        sid = user["active_session_id"]
        if sid is None:
            await update.message.reply_text("No open session. Start one with `/workout start`.", parse_mode="Markdown")
            return

        session_sets = user["command_state"].get("current_session_sets", [])
        total_sets = len(session_sets)

        # Warn if ending a session with zero sets logged
        if total_sets == 0 and "confirm" not in (context.args or []):
            await update.message.reply_text(
                "⚠️ You haven't logged any sets yet.\n\n"
                "Log at least one set, or tap below to end the session anyway.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🚫 Keep training", callback_data="wk:noop"),
                    InlineKeyboardButton("✓ End anyway", callback_data="wk:end_confirm"),
                ]]),
            )
            return

        total_volume = sum(s["weight_kg"] * s["reps"] for s in session_sets)
        user["active_session_id"] = None
        user["command_state"]["current_session_sets"] = []
        user["command_state"].pop("active_session_day", None)
        _save_store()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _db_sync_session_end, chat_id, user)

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
                targets = await asyncio.get_running_loop().run_in_executor(
                    None, generate_next_session_targets, session_sets, plan_day_ctx, profile
                )
                next_targets_text = f"\n\n🎯 *Next session targets:*\n_{targets}_"
            except Exception as e:
                print(f"Warning: generate_next_session_targets failed: {e}")

        await update.message.reply_text(
            f"✅ *Session #{sid} complete!*\n\n"
            f"Sets: {total_sets} | Volume: {_w(total_volume, user):,.0f}{_wu(user)}{exercise_summary}"
            f"{next_targets_text}"
            + _next_steps(
                ("/stats", "see PRs"),
                ("/checkin", "log recovery"),
                ("/measurements", "track size"),
            ),
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
            "Usage: `/logset <exercise> <weight> <reps> [rpe=N]`\n"
            "Examples:\n"
            "`/logset bench 100kg 8`\n"
            "`/logset squat 225lbs 5 rpe=8`\n"
            "`/logset deadlift 140 3`  ← bare number = kg",
            parse_mode="Markdown",
        )
        return

    # Optional trailing rpe=N argument
    args = list(context.args)
    rpe: float | None = None
    if args and args[-1].lower().startswith("rpe="):
        try:
            rpe = float(args.pop(-1).split("=")[1])
        except (ValueError, IndexError):
            pass

    if len(args) < 3:
        await update.message.reply_text("Not enough arguments. Usage: `/logset bench 100kg 8`", parse_mode="Markdown")
        return

    try:
        reps = int(args[-1])
    except ValueError:
        await update.message.reply_text("Reps must be a number (e.g. `8`).", parse_mode="Markdown")
        return

    weight_kg = _parse_logset_weight_kg(args[-2])
    if weight_kg is None:
        await update.message.reply_text(
            "Could not parse weight. Use `100kg`, `225lbs`, or bare `100`.\n"
            "Example: `/logset bench press 100kg 8`",
            parse_mode="Markdown",
        )
        return
    if weight_kg <= 0:
        await update.message.reply_text("Weight must be greater than 0.", parse_mode="Markdown")
        return
    if reps <= 0:
        await update.message.reply_text("Reps must be greater than 0.", parse_mode="Markdown")
        return

    exercise = " ".join(args[:-2]).title()
    one_rm = epley_1rm(weight_kg, reps)

    entry: dict = {
        "exercise_name": exercise,
        "weight_kg": weight_kg,
        "reps": reps,
        "estimated_1rm": one_rm,
        "date": _today(),
        "session_id": user["active_session_id"],
    }
    if rpe is not None:
        entry["rpe"] = rpe

    # Weight-drop alert (≥10% below previous recorded weight for this exercise)
    drop_alert = ""
    prev_sets = [s for s in user["set_logs"] if s.get("exercise_name", "").lower() == exercise.lower()]
    if prev_sets:
        last_weight = prev_sets[-1].get("weight_kg", 0)
        if last_weight > 0 and weight_kg < last_weight * 0.9:
            pct = round((1 - weight_kg / last_weight) * 100)
            drop_alert = f"\n\n⚠️ _{pct}% below your last logged weight for this exercise. If this wasn't intentional, check fatigue or technique._"

    user["set_logs"].append(entry)
    user["set_logs"] = user["set_logs"][-500:]

    if user["active_session_id"] is not None:
        user["command_state"].setdefault("current_session_sets", []).append(entry)

    pr = user["prs"].get(exercise)
    is_pr = pr is None or one_rm > pr["estimated_1rm"]
    pr_text = ""
    if is_pr:
        user["prs"][exercise] = {"weight_kg": weight_kg, "reps": reps, "estimated_1rm": one_rm, "date": _today()}
        pr_text = "\n🏆 *New PR!*"

    rpe_text = f"  RPE {rpe}" if rpe is not None else ""
    _save_store()

    # First set logged milestone hint
    if len(user["set_logs"]) == 1:
        drop_alert += "\n\n💡 _Tip: Use /stats after your session to see personal records by exercise._"

    await update.message.reply_text(
        f"✅ *{exercise}* — {_wfmt(weight_kg, user)} × {reps} reps{rpe_text}\n"
        f"1RM estimate: ~{_wfmt(one_rm, user)} (Epley){pr_text}{drop_alert}",
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
        await asyncio.get_running_loop().run_in_executor(None, _db_sync_session_start, chat_id, user)
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
        await asyncio.get_running_loop().run_in_executor(None, _db_sync_session_start, chat_id, user)
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
            _loop = asyncio.get_running_loop()
            await _loop.run_in_executor(None, _db_sync_set, chat_id, user, entry)
            if is_pr:
                await _loop.run_in_executor(None, _db_sync_pr, chat_id, user, ex, weight, reps, entry["estimated_1rm"])
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

    elif part == "noop":
        pass  # "Keep training" button — do nothing, just dismiss the spinner

    elif part == "end_confirm":
        # Force-end session even with 0 sets
        sid = user["active_session_id"]
        if sid is None:
            await query.edit_message_text("No open session.")
            return
        user["active_session_id"] = None
        user["command_state"]["current_session_sets"] = []
        user["command_state"].pop("active_session_day", None)
        _save_store()
        await asyncio.get_running_loop().run_in_executor(None, _db_sync_session_end, chat_id, user)
        await query.edit_message_text(
            f"✅ *Session #{sid} ended* (no sets logged).\n\nNext time, try to log at least one set to track progress!",
            parse_mode="Markdown",
        )

    elif part == "end":
        sid = user["active_session_id"]
        if sid is None:
            await query.edit_message_text("No open session. Use /log to start one.")
            return
        session_sets = user["command_state"].get("current_session_sets", [])
        total_sets = len(session_sets)

        # Warn if ending with zero sets
        if total_sets == 0:
            await query.edit_message_text(
                "⚠️ You haven't logged any sets yet.\n\nLog at least one set, or end the session anyway.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🚫 Keep training", callback_data="wk:noop"),
                    InlineKeyboardButton("✓ End anyway", callback_data="wk:end_confirm"),
                ]]),
            )
            return

        total_volume = sum(s["weight_kg"] * s["reps"] for s in session_sets)
        user["active_session_id"] = None
        user["command_state"]["current_session_sets"] = []
        user["command_state"].pop("active_session_day", None)
        _save_store()
        await asyncio.get_running_loop().run_in_executor(None, _db_sync_session_end, chat_id, user)

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
            f"Sets: {total_sets} | Volume: {_w(total_volume, user):,.0f}{_wu(user)}{ex_summary}"
            + _next_steps(
                ("/stats", "see PRs"),
                ("/checkin", "log recovery"),
                ("/measurements", "track size"),
            ),
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

    # Photo analysis history (body fat trend — no subjective scores)
    analyses = user.get("analyses", [])
    if analyses:
        photo_lines = ["📸 *Photo Check-ins:*"]
        for a in analyses:
            bf = a.get("body_fat_estimate", "?")
            dt = a.get("date", "?")
            angle = a.get("photo_angle", "")
            angle_label = f" [{angle.replace('_', ' ')}]" if angle else ""
            photo_lines.append(f"  {dt}{angle_label}: {bf} body fat")
        lines.append("\n" + "\n".join(photo_lines))

    # Longevity Score — composite from recent Garmin/checkin data
    try:
        import garmin_service as _gs
        garmin_today = _gs.get_cached(chat_id)
        if garmin_today:
            ls_parts = []
            hrv = garmin_today.get("hrv_ms", 0) or 0
            rhr = garmin_today.get("resting_hr_bpm", 0) or 0
            sleep_hrs = garmin_today.get("sleep_duration_hrs", 0) or 0
            steps = garmin_today.get("steps_yesterday", 0) or 0
            vo2 = garmin_today.get("vo2_max", 0) or 0
            # Simple 0-100 composite (each metric 0-20)
            hrv_score = min(20, round(hrv / 100 * 20)) if hrv else 0
            rhr_score = min(20, max(0, round((80 - rhr) / 30 * 20))) if rhr else 0
            sleep_score = min(20, round(sleep_hrs / 9 * 20)) if sleep_hrs else 0
            steps_score = min(20, round(steps / 10000 * 20)) if steps else 0
            vo2_score = min(20, round(vo2 / 55 * 20)) if vo2 else 0
            longevity_score = hrv_score + rhr_score + sleep_score + steps_score + vo2_score
            if longevity_score > 0:
                lines.append(
                    f"\n🫀 *Longevity Score (today):* {longevity_score}/100\n"
                    f"  HRV {hrv:.0f}ms | RHR {rhr}bpm | Sleep {sleep_hrs:.1f}h | "
                    f"Steps {steps:,} | VO2max {vo2:.0f}"
                    if vo2 else
                    f"\n🫀 *Longevity Score (today):* {longevity_score}/100\n"
                    f"  HRV {hrv:.0f}ms | RHR {rhr}bpm | Sleep {sleep_hrs:.1f}h | Steps {steps:,}"
                )
    except Exception:
        pass

    if len(lines) == 2:
        lines.append("\nLog workouts with `/workout start` + `/logset`, check in daily with `/checkin`, and track weight with `/measurements weight=83kg`.")

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("💪 Muscle Progress", callback_data="progress:muscles"),
    ]])
    lines.append(_next_steps(
        ("/checkin", "log today's recovery"),
        ("/measurements", "track size"),
        ("/plan new", "adjust plan"),
    ))
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=keyboard)


async def handle_progress_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle progress: callback buttons (currently: progress:muscles)."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    action = query.data.split(":", 1)[1] if ":" in query.data else ""

    if action == "muscles":
        text = _build_muscle_progress_text(user)
        try:
            await query.edit_message_text(text, parse_mode="Markdown")
        except Exception:
            await update.effective_chat.send_message(text, parse_mode="Markdown")


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
            f"📏 *Measurements*\n{current}\n\nTap a field to log it:",
            parse_mode="Markdown",
            reply_markup=_measurements_menu_keyboard(last, user),
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
    await asyncio.get_running_loop().run_in_executor(None, _db_sync_measurement, chat_id, user, entry)

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


async def handle_measurements_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route meas:* callback queries for the /measurements inline keyboard."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    data = query.data
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""

    _MEAS_PROMPTS = {
        "weight": "Type your body weight (e.g. `83kg` or `185lbs`):",
        "waist":  "Type your waist measurement (e.g. `32in` or `81cm`):",
        "chest":  "Type your chest measurement (e.g. `42in` or `107cm`):",
        "hips":   "Type your hips measurement (e.g. `38in` or `97cm`):",
        "arm":    "Type your arm measurement (e.g. `16in` or `40cm`):",
        "thigh":  "Type your thigh measurement (e.g. `24in` or `61cm`):",
    }

    if action == "input":
        field = parts[2] if len(parts) > 2 else ""
        user["active_command"] = "measurements_input"
        user["command_state"] = {"field": field}
        _save_store()
        await query.edit_message_text(
            _MEAS_PROMPTS.get(field, "Type the measurement:"),
            parse_mode="Markdown",
        )

    elif action == "custom":
        user["active_command"] = "measurements_input"
        user["command_state"] = {"field": "_custom"}
        _save_store()
        await query.edit_message_text(
            "Type all your measurements:\n"
            "`weight=83kg waist=32in chest=42in arm=16in`\n\n"
            "Supports kg/lbs and in/cm.",
            parse_mode="Markdown",
        )


async def cmd_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    user["active_command"] = None
    user["command_state"] = {}

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
        await asyncio.get_running_loop().run_in_executor(None, _db_sync_meal, chat_id, user, entry)
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
    await update.effective_chat.send_action("typing")
    msg = await update.message.reply_text("🍽️ Estimating macros…")

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
    await asyncio.get_running_loop().run_in_executor(None, _db_sync_meal, chat_id, user, entry)

    today_meals = [m for m in user["meal_logs"] if m["date"] == _today()]
    day_cals = sum(m["calories"] for m in today_meals)
    day_protein = sum(m["protein_g"] for m in today_meals)

    items_text = ""
    items = macros.get("items", [])
    if items:
        items_text = f"\n_{', '.join(items)}_\n"

    if macros.get("source") == "nutritionix":
        source_note = ""
    else:
        conf = macros.get("confidence", "medium")
        conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(conf, "🟡")
        source_note = f" _{conf_emoji} AI estimate ({conf} confidence)_"
    await msg.edit_text(
        f"✅ *Meal logged!*{items_text}\n"
        f"{macros['calories']} kcal | P: {macros['protein_g']}g | C: {macros['carbs_g']}g | F: {macros['fat_g']}g{source_note}\n\n"
        f"Today so far: *{day_cals} kcal* | Protein: *{day_protein}g*\n"
        "Run /macros to see full targets vs. logged.",
        parse_mode="Markdown",
    )


async def cmd_fridge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to send a fridge/pantry photo for macro-aligned recipe suggestions."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    user["command_state"] = {}
    user["active_command"] = "awaiting_fridge_photo"
    _save_store()

    diet = (user.get("last_plan") or {}).get("diet") or {}
    plan_note = ""
    if diet.get("calories"):
        plan_note = (
            f"\n\n_Your targets: {diet['calories']} kcal | "
            f"{diet.get('protein_g', '?')}g protein | "
            f"{diet.get('carbs_g', '?')}g carbs | "
            f"{diet.get('fat_g', '?')}g fat_"
        )

    await update.message.reply_text(
        "📷 *Send me a photo of your fridge or pantry*\n\n"
        "I'll identify what's in there and suggest recipes that hit your macro targets."
        f"{plan_note}",
        parse_mode="Markdown",
    )


_FRIDGE_CATEGORIES: dict[str, dict] = {
    "proteins":   {"label": "🥩 Proteins",      "items": ["Chicken", "Eggs", "Beef", "Tuna", "Salmon", "Turkey", "Shrimp", "Tofu", "Tempeh", "Lentils"]},
    "grains":     {"label": "🌾 Grains & Carbs", "items": ["Rice", "Oats", "Bread", "Pasta", "Quinoa", "Potato", "Sweet Potato", "Tortilla"]},
    "vegetables": {"label": "🥦 Vegetables",     "items": ["Broccoli", "Spinach", "Peppers", "Onion", "Tomato", "Carrot", "Cucumber", "Zucchini", "Kale", "Mushrooms"]},
    "dairy":      {"label": "🥛 Dairy",          "items": ["Milk", "Greek Yogurt", "Cottage Cheese", "Cheddar", "Mozzarella", "Butter"]},
    "fats":       {"label": "🫒 Fats",           "items": ["Olive Oil", "Avocado", "Almonds", "Peanut Butter", "Walnuts", "Hummus"]},
    "fruits":     {"label": "🍎 Fruits",         "items": ["Banana", "Apple", "Berries", "Orange", "Mango", "Grapes"]},
}


def _fridge_review_text(ingredients_by_cat: dict[str, list[str]], added: list[str]) -> str:
    """Format the ingredient review message with items grouped by category."""
    lines = ["🔍 *Fridge Scan — Review Ingredients*\n"]
    total = sum(len(v) for v in ingredients_by_cat.values())
    if total == 0:
        lines.append("_Nothing detected — try a clearer photo_")
    else:
        for cat_key, cat_items in ingredients_by_cat.items():
            if not cat_items:
                continue
            label = _FRIDGE_CATEGORIES[cat_key]["label"] if cat_key in _FRIDGE_CATEGORIES else "🗂 Other"
            lines.append(f"{label}: {esc(', '.join(cat_items))}")
    if added:
        lines.append(f"\n✅ *You added:* {esc(', '.join(added))}")
    lines.append("\nTap a category to add missing items, or just type them:")
    return "\n".join(lines)


def _fridge_category_keyboard(added: list[str]) -> InlineKeyboardMarkup:
    """2-column category grid + Done button."""
    cats = list(_FRIDGE_CATEGORIES.items())
    rows = []
    for i in range(0, len(cats), 2):
        row = [InlineKeyboardButton(v["label"], callback_data=f"fridge:cat:{k}") for k, v in cats[i:i+2]]
        rows.append(row)
    rows.append([InlineKeyboardButton(f"✅ Generate Recipes ({len(added)} added)" if added else "✅ Generate Recipes →", callback_data="fridge:done")])
    return InlineKeyboardMarkup(rows)


def _fridge_items_keyboard(category: str, added: list[str]) -> InlineKeyboardMarkup:
    """Items for a category; checkmark if already added. Back button at bottom."""
    cat = _FRIDGE_CATEGORIES.get(category, {})
    items = cat.get("items", [])
    added_lower = {a.lower() for a in added}
    rows = []
    for i in range(0, len(items), 2):
        row = []
        for item in items[i:i+2]:
            label = f"✓ {item}" if item.lower() in added_lower else item
            row.append(InlineKeyboardButton(label, callback_data=f"fridge:add:{item}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("← Back to Categories", callback_data="fridge:back")])
    return InlineKeyboardMarkup(rows)


def _fridge_scan_call(img_b64: str) -> dict[str, list[str]]:
    """Sync vision scan — returns ingredients grouped by category."""
    message = get_anthropic_client().messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=500,
        timeout=60.0,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text", "text": (
                    "List every food item or ingredient you can see in this fridge/pantry photo. "
                    "Group them by food category. "
                    "Return ONLY valid JSON, no explanation:\n"
                    '{"proteins": ["eggs","chicken breast"], "grains": ["brown rice","oats"], '
                    '"vegetables": ["broccoli","spinach"], "dairy": ["greek yogurt","cheddar"], '
                    '"fats": ["olive oil","avocado"], "fruits": ["banana"], "other": ["hot sauce","soy sauce"]}'
                )},
            ],
        }],
    )
    raw = message.content[0].text
    json_match = re.search(r"\{[\s\S]*\}", raw)
    if not json_match:
        return {}
    data = json.loads(json_match.group())
    # Keep only known category keys + "other"; normalise values to list[str]
    valid_keys = set(_FRIDGE_CATEGORIES.keys()) | {"other"}
    return {k: [str(i) for i in v] for k, v in data.items() if k in valid_keys and isinstance(v, list)}


def _fridge_recipes_call(ingredients: list[str], diet_context: str) -> dict:
    """Sync recipe generation from a confirmed ingredient list — no image needed."""
    ingr_list = ", ".join(ingredients) if ingredients else "various ingredients"
    message = get_anthropic_client().messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=1500,
        timeout=90.0,
        messages=[{
            "role": "user",
            "content": [{
                "type": "text",
                "text": (
                    f"Available ingredients: {ingr_list}\n\n"
                    f"{diet_context}\n\n"
                    "Suggest 3 recipes using ONLY those ingredients that best fit this athlete's plan.\n"
                    "Return ONLY valid JSON (no markdown, no explanation):\n"
                    '{"recipes": ['
                    '{"name": "...", "ingredients": ["200g chicken breast", "1 cup rice"], '
                    '"macros": {"calories": 520, "protein_g": 48, "carbs_g": 40, "fat_g": 9}, '
                    '"prep": "Short 2-sentence cooking method.", '
                    '"meal_timing": "Post-workout", "why_it_fits": "One sentence."}'
                    "]}"
                ),
            }],
        }],
    )
    raw = message.content[0].text
    json_match = re.search(r"\{[\s\S]*\}", raw)
    return json.loads(json_match.group()) if json_match else {}


def _build_fridge_diet_context(user: dict) -> str:
    """Build a diet context string to pass to the recipe generation call."""
    diet = (user.get("last_plan") or {}).get("diet") or {}
    profile = user.get("profile") or {}
    cal = diet.get("calories", "?")
    prot = diet.get("protein_g", "?")
    carbs = diet.get("carbs_g", "?")
    fat = diet.get("fat_g", "?")
    goal = profile.get("goal", "general fitness")
    prioritize = ", ".join(diet.get("foods_to_prioritize") or []) or "whole foods"
    avoid = ", ".join(diet.get("foods_to_limit") or []) or "ultra-processed foods"
    timing = diet.get("meal_timing", "")
    try:
        per_meal_str = f"~{round(int(cal)/3)} kcal, ~{round(int(prot)/3)}g protein per meal"
    except (TypeError, ValueError):
        per_meal_str = "balanced macros per meal"
    return (
        f"Athlete goal: {goal}\n"
        f"Per meal target: {per_meal_str}\n"
        f"Daily targets: {cal} kcal | {prot}g protein | {carbs}g carbs | {fat}g fat\n"
        f"Foods to prioritise: {prioritize}\n"
        f"Foods to avoid: {avoid}\n"
        f"Meal timing: {timing}"
    )


async def _handle_fridge_photo(update: Update, user: dict, img_b64: str) -> None:
    """Scan fridge photo for ingredients then show the ingredient review UI."""
    chat_id = update.effective_chat.id
    msg = await update.effective_chat.send_message("🔍 Scanning your fridge for ingredients…")

    try:
        ingredients_by_cat = await asyncio.get_running_loop().run_in_executor(
            None, _fridge_scan_call, img_b64
        )
    except Exception as e:
        print(f"Warning: fridge scan failed: {e}")
        await msg.edit_text(
            "⚠️ Couldn't scan the photo. Make sure it's a clear fridge/pantry image and try again."
        )
        return

    ingredients_flat = [item for items in ingredients_by_cat.values() for item in items]
    user["active_command"] = "fridge_reviewing"
    user["command_state"] = {
        "ingredients": ingredients_flat,
        "ingredients_by_cat": ingredients_by_cat,
        "added": [],
        "review_msg_id": msg.message_id,
    }
    _save_store()

    await msg.edit_text(
        _fridge_review_text(ingredients_by_cat, []),
        parse_mode="Markdown",
        reply_markup=_fridge_category_keyboard([]),
    )


async def _fridge_send_recipes(msg, ingredients: list[str], added: list[str], diet_context: str) -> None:
    """Generate and display recipes; msg is the message to edit."""
    all_ingredients = list(dict.fromkeys(ingredients + added))  # deduplicate, preserve order
    await msg.edit_text("🍳 Generating recipes from your ingredients…")

    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None, _fridge_recipes_call, all_ingredients, diet_context
        )
    except Exception as e:
        print(f"Warning: fridge recipe generation failed: {e}")
        await msg.edit_text("⚠️ Couldn't generate recipes. Please try again.")
        return

    recipes = result.get("recipes", [])
    if not recipes:
        await msg.edit_text(
            "📷 Couldn't generate recipes from those ingredients. Try adding more items and try again."
        )
        return

    try:
        ingr_summary = esc(", ".join(all_ingredients[:15]) + ("…" if len(all_ingredients) > 15 else ""))
        lines = [f"🛒 *Ingredients used:* {ingr_summary}\n"]
        for i, r in enumerate(recipes[:3], 1):
            m = r.get("macros", {})
            ingr_list = esc(", ".join(r.get("ingredients", [])))
            lines.append(
                f"*{i}. {esc(r.get('name', 'Recipe'))}*\n"
                f"🥩 {m.get('protein_g', '?')}g protein  🔥 {m.get('calories', '?')} kcal  "
                f"🍚 {m.get('carbs_g', '?')}g carbs  🫒 {m.get('fat_g', '?')}g fat\n"
                f"📋 {ingr_list}\n"
                f"👨‍🍳 _{esc(r.get('prep', ''))}_\n"
                f"⏱ {esc(r.get('meal_timing', ''))} — {esc(r.get('why_it_fits', ''))}\n"
            )
        await msg.edit_text(
            "🍽️ *Fridge Recipe Suggestions*\n\n" + "\n".join(lines),
            parse_mode="Markdown",
        )
    except Exception as e:
        print(f"Warning: fridge recipe formatting failed: {e}")
        await msg.edit_text("⚠️ Got the recipes but couldn't format them. Please try again.")


async def handle_fridge_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all fridge: callback queries — category nav, item toggling, done."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    state = user.get("command_state") or {}
    ingredients_by_cat: dict[str, list[str]] = state.get("ingredients_by_cat", {})
    ingredients: list[str] = state.get("ingredients", [])
    added: list[str] = state.get("added", [])
    data = query.data  # e.g. "fridge:cat:proteins", "fridge:add:Tuna", "fridge:done"

    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    value = parts[2] if len(parts) > 2 else ""

    if action == "cat":
        await query.edit_message_text(
            _fridge_review_text(ingredients_by_cat, added) + f"\n\n*{_FRIDGE_CATEGORIES[value]['label']}* — tap to add:",
            parse_mode="Markdown",
            reply_markup=_fridge_items_keyboard(value, added),
        )

    elif action == "add":
        added_lower = {a.lower() for a in added}
        if value.lower() in added_lower:
            added = [a for a in added if a.lower() != value.lower()]
        else:
            added = added + [value]
        user["command_state"]["added"] = added
        _save_store()
        # re-detect which category this item belongs to so we can stay in it
        current_cat = next(
            (k for k, v in _FRIDGE_CATEGORIES.items() if value in v["items"]), None
        )
        if current_cat:
            await query.edit_message_text(
                _fridge_review_text(ingredients_by_cat, added) + f"\n\n*{_FRIDGE_CATEGORIES[current_cat]['label']}* — tap to add:",
                parse_mode="Markdown",
                reply_markup=_fridge_items_keyboard(current_cat, added),
            )
        else:
            await query.edit_message_text(
                _fridge_review_text(ingredients_by_cat, added),
                parse_mode="Markdown",
                reply_markup=_fridge_category_keyboard(added),
            )

    elif action == "back" or action == "clear":
        if action == "clear":
            added = []
            user["command_state"]["added"] = []
            _save_store()
        await query.edit_message_text(
            _fridge_review_text(ingredients_by_cat, added),
            parse_mode="Markdown",
            reply_markup=_fridge_category_keyboard(added),
        )

    elif action == "done":
        user["active_command"] = None
        user["command_state"] = {}
        _save_store()
        diet_context = _build_fridge_diet_context(user)
        await _fridge_send_recipes(query.message, ingredients, added, diet_context)


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

    # Volume per muscle group (last 30 days) — uses shared _volume_by_muscle helper
    vol = _volume_by_muscle(user["set_logs"], days=30)
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

    import crypto_utils as _cu
    if not _cu.encryption_available():
        await update.message.reply_text(
            "⚠️ Garmin and MFP connections aren't available on this bot yet.\n\n"
            "The admin needs to set the ENCRYPTION_KEY environment variable and restart the bot."
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
                logger.warning("Garmin connect failed for chat %s: %s", chat_id, e)
                user["active_command"] = None
                await msg.edit_text(
                    "❌ Couldn't connect to Garmin — double-check your email and password, "
                    "then try /connect garmin again."
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
                logger.warning("MFP connect failed for chat %s: %s", chat_id, e)
                user["active_command"] = None
                await msg.edit_text(
                    "❌ Couldn't connect to MyFitnessPal — double-check your username and password, "
                    "then try /connect mfp again."
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
    query_topic = " ".join(context.args).strip() if context.args else ""
    msg = await update.message.reply_text(
        f"🔬 Fetching research on *{query_topic}*…" if query_topic else "🔬 Fetching research + community insights…",
        parse_mode="Markdown",
    )
    try:
        pubmed_task = asyncio.create_task(_fetch_research_summaries(topic=query_topic or None))
        reddit_task = asyncio.create_task(_fetch_reddit_summaries(topic=query_topic or None))
        pubmed, reddit = await asyncio.gather(pubmed_task, reddit_task, return_exceptions=True)

        parts: list[str] = []
        if isinstance(pubmed, list) and pubmed:
            parts.append("*📚 Latest Research*\n\n" + "\n\n".join(pubmed))
        if isinstance(reddit, list) and reddit:
            parts.append("*💬 Community Insights*\n\n" + "\n\n".join(reddit))

        text = "\n\n───\n\n".join(parts) if parts else "No data available. Try again in a moment."
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

    # Calculate streak from local checkin cache (freeze dates count as check-ins)
    all_dates = {c["date"] for c in checkins} | set(user.get("streak_freezes", []))
    sorted_dates = sorted(all_dates, reverse=True)
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
    await asyncio.get_running_loop().run_in_executor(None, _db_sync_measurement, chat_id, user, entry)

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
        active_goals = [g for g in goals_data if g.get("is_active")]
        if active_goals:
            g = active_goals[-1]
            target_parts = []
            if g.get("target_weight_kg"):
                target_parts.append(f"Weight: {_wfmt(float(g['target_weight_kg']), user)}")
            if g.get("target_bf_pct"):
                target_parts.append(f"Body fat: {g['target_bf_pct']}%")
            if g.get("target_date"):
                from datetime import date as _date_cls
                days_left = (_date_cls.fromisoformat(g["target_date"]) - _date_cls.today()).days
                target_parts.append(f"Date: {g['target_date']} ({days_left} days away)")
            target_str = " | ".join(target_parts) if target_parts else "No specific target"
            header = f"🎯 *Active Goal: {g['goal_type'].title()}*\n{target_str}\n\nChange it:"
        else:
            header = "🎯 *Goals*\nNo active goal set. Choose one:"
        await update.message.reply_text(
            header,
            parse_mode="Markdown",
            reply_markup=_goals_menu_keyboard(user),
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


async def handle_goals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route goals:* callback queries for the /goals inline keyboard."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    data = query.data
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""

    if action == "noop":
        return

    elif action == "type":
        goal_type = parts[2] if len(parts) > 2 else "health"
        for g in user.get("goals", []):
            if g.get("is_active"):
                g["is_active"] = False
        new_goal: dict = {
            "goal_type": goal_type,
            "target_weight_kg": None,
            "target_bf_pct": None,
            "target_date": None,
            "start_weight_kg": float(user["profile"].get("weight", 0) or 0) or None,
            "created_at": _today(),
            "is_active": True,
        }
        user.setdefault("goals", []).append(new_goal)
        user["profile"]["goal"] = goal_type
        user["profile"]["goal_set_date"] = _today()
        _save_store()
        await query.edit_message_text(
            f"🎯 *Goal set to: {goal_type.title()}*\n\nAdd a target (optional):",
            parse_mode="Markdown",
            reply_markup=_goals_menu_keyboard(user),
        )

    elif action == "input":
        field = parts[2] if len(parts) > 2 else ""
        user["active_command"] = "goals_input"
        user["command_state"] = {"field": field}
        _save_store()
        _prompts = {
            "weight": "Type your target weight (e.g. `90kg` or `200lbs`):",
            "bf":     "Type your target body fat % (e.g. `12`):",
            "date":   "Type your target date (e.g. `2026-12-01`):",
        }
        await query.edit_message_text(
            _prompts.get(field, "Type the value:"),
            parse_mode="Markdown",
        )

    elif action == "custom":
        user["active_command"] = "goals_input"
        user["command_state"] = {"field": "_custom"}
        _save_store()
        await query.edit_message_text(
            "Type your goal in full, e.g.:\n"
            "`/goals set cut 10%bf by 2026-09-01`\n"
            "`/goals set bulk 90kg`\n\n"
            "Or tap a button below to use the menu instead:",
            parse_mode="Markdown",
            reply_markup=_goals_menu_keyboard(user),
        )


async def cmd_weakpoints(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    cutoff = str(_date.today() - timedelta(days=30))
    recent_sets = [s for s in user["set_logs"] if s.get("date", "") >= cutoff]

    if not user["last_analysis"] and not recent_sets and not user["prs"]:
        await update.message.reply_text(
            "📊 Not enough data yet.\n\n"
            "Log some sets with /log or /logset, then I can identify your training imbalances.\n"
            "Send a photo for a full physique assessment too. 📸"
        )
        return

    if not user.get("analyses") and not user["last_analysis"]:
        await update.message.reply_text(
            "📸 *Upload a physique photo first.*\n\n"
            "Send a front, back, or side photo and I'll assess your muscle development. "
            "Then /weakpoints will give you a full imbalance analysis.",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text("🔬 Analyzing training imbalances…")
    try:
        from claude_service import analyze_weak_points
        all_analyses = user.get("analyses") or ([user["last_analysis"]] if user["last_analysis"] else [])
        ctx_str = _get_bot_context_str(user)
        result = await asyncio.get_running_loop().run_in_executor(
            None, analyze_weak_points, all_analyses, recent_sets, user["profile"] or None, ctx_str
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

        if len(reply) <= _TG_MAX:
            await msg.edit_text(reply, parse_mode="Markdown")
        else:
            await msg.delete()
            await _send_long(update.message.reply_text, reply, parse_mode="Markdown")
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

        ctx_str = _get_bot_context_str(user)
        result = await asyncio.get_running_loop().run_in_executor(
            None, generate_weekly_report, sessions_data, checkins_data, meals_data, prs_list,
            user["profile"] or None, ctx_str
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
            reply += f"📈 *Adherence:* {adherence}\n"

        # Phase transition warning — after 10+ weeks on same goal
        goal_set_date = user.get("profile", {}).get("goal_set_date")
        if goal_set_date:
            weeks_on_goal = (_date.today() - _date.fromisoformat(goal_set_date)).days // 7
            if weeks_on_goal >= 10:
                current_goal = user.get("profile", {}).get("goal", "current")
                reply += (
                    f"\n\n📅 *Phase Check:* You've been on a *{current_goal}* phase for ~{weeks_on_goal} weeks. "
                    "Consider evaluating whether to switch phases — extended cuts risk muscle loss, "
                    "extended bulks increase fat gain. Reply to discuss or type `/plan new` to regenerate."
                )

        # Micronutrient reminder after 3+ consecutive deficit weeks
        if user.get("profile", {}).get("goal") in ("cut", "recomp"):
            recent_weights_sorted = sorted(
                [(m["date"], m["body_weight_kg"]) for m in user.get("measurements", []) if m.get("body_weight_kg")],
                key=lambda x: x[0],
            )
            if len(recent_weights_sorted) >= 2:
                cutoff_3w = str(_date.today() - timedelta(days=21))
                recent_3w = [w for w in recent_weights_sorted if w[0] >= cutoff_3w]
                if len(recent_3w) >= 2 and recent_3w[-1][1] < recent_3w[0][1]:
                    reply += (
                        "\n\n🥦 *Micronutrient reminder:* You've been in a deficit for 3+ weeks. "
                        "Consider a nutrient-dense refeed day (higher carbs, prioritise leafy greens, "
                        "legumes, nuts, and colourful vegetables) to top up vitamins and minerals."
                    )

        reply += _next_steps(
            ("/checkin", "keep data flowing"),
            ("/plan new", "adjust plan"),
            ("/progress", "see trends"),
        )
        await msg.edit_text(reply[:4096], parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Report generation failed: {e}")


async def cmd_billing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Placeholder — billing is being reworked into a partner model."""
    await update.message.reply_text(
        "💳 *Billing & Partnerships*\n\n"
        "All features are currently available to everyone.\n\n"
        "Partnership and licensing info coming soon — stay tuned!",
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
            "⚠️ Web app linking is not set up yet.\n\n"
            "The bot owner needs to set `BOT_SECRET` and `API_BASE_URL` "
            "environment variables and redeploy.",
            parse_mode="Markdown",
        )
        return

    # Check if already linked
    try:
        info = await _api_get(f"/internal/telegram/{chat_id}/user", chat_id=None)
        if info.get("linked"):
            _linked_user_ids[chat_id] = info["user_id"]
            await update.message.reply_text(
                f"✅ *Already linked!*\n\n"
                f"Your Telegram is connected to *{esc(info.get('email', '?'))}*.",
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
    except httpx.ConnectError:
        await update.message.reply_text(
            "❌ Could not reach the web app. Make sure the web service is running."
        )
        return
    except Exception as e:
        err = str(e).lower()
        if "protocol" in err or "url" in err or "invalid" in err:
            await update.message.reply_text(
                "❌ Web app URL is misconfigured. "
                "Check that `API_BASE_URL` starts with `https://`.",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "❌ Could not generate a link code. Please try again in a moment."
            )
        return

    web_url = API_BASE_URL.removesuffix("/api")
    await update.message.reply_text(
        f"🔗 *Link your Telegram to the web app*\n\n"
        f"Your one-time code:\n\n"
        f"`{code}`\n\n"
        f"1. Open: {web_url}\n"
        f"2. Register or sign in\n"
        f"3. Go to *Profile → Link Telegram*\n"
        f"4. Enter the code above\n\n"
        f"_Code expires in 10 minutes._",
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
            f"User ID: `{info['user_id']}`",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "❌ *Not linked yet.*\n\nUse /link to generate a code, then enter it on the web app.",
            parse_mode="Markdown",
        )


# ── Photo handler ─────────────────────────────────────────────────────────────

async def _process_media_group(update: Update, context: ContextTypes.DEFAULT_TYPE, mg_key: str) -> None:
    """Download and analyze all photos from a buffered media group together."""
    await asyncio.sleep(2.0)  # wait for remaining album photos to arrive
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    photos = context.bot_data.pop(mg_key, [])
    context.bot_data.pop(f"{mg_key}_sent", None)
    context.bot_data.pop(f"{mg_key}_blocked", None)

    if not photos:
        return

    msg = await update.effective_chat.send_message(
        f"📸 Analyzing {len(photos)} photo{'s' if len(photos) != 1 else ''}… (20-40 seconds)"
    )
    try:
        images_b64: list[str] = []
        for photo in photos:
            file = await context.bot.get_file(photo.file_id)
            buf = BytesIO()
            await file.download_to_memory(buf)
            images_b64.append(base64.standard_b64encode(buf.getvalue()).decode("utf-8"))

        prev = (user.get("analyses") or [None])[-1]
        loop = asyncio.get_running_loop()
        analysis = await loop.run_in_executor(
            None, _analyze_photo, images_b64, user["profile"], prev
        )
        user["last_analysis"] = analysis
        entry = {**analysis, "date": _today(), "photo_count": len(photos)}
        user.setdefault("analyses", []).append(entry)
        user["analyses"] = user["analyses"][-20:]
        _save_store()

        result_text = _format_analysis(analysis)
        try:
            await msg.delete()
        except Exception:
            pass
        await _send_long(update.effective_chat.send_message, result_text, parse_mode="Markdown")
        await _auto_plan_after_analysis(update, user, chat_id)
    except Exception as e:
        try:
            await msg.edit_text(f"❌ Analysis failed: {e}")
        except Exception:
            await update.effective_chat.send_message(f"❌ Analysis failed: {e}")


async def _auto_plan_after_analysis(update: Update, user: dict, chat_id: int) -> None:
    """After a physique analysis, regenerate the plan immediately if days is set, else ask."""
    had_plan = bool(user.get("last_plan"))
    if user["profile"].get("days"):
        remaining = _check_cooldown(_plan_cooldowns, chat_id, PLAN_COOLDOWN)
        if remaining:
            await update.effective_chat.send_message(
                "📊 Analysis saved. Tap below to update your plan with this physique data:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🚀 Update my plan", callback_data="prof:generate"),
                ]]),
            )
            return
        verb = "Updating" if had_plan else "Building"
        gen_msg = await update.effective_chat.send_message(
            f"🧬 {verb} your plan from this analysis + your profile… (30-60 seconds)"
        )
        ctx_str = _get_bot_context_str(user)
        loop = asyncio.get_running_loop()
        try:
            plan = await loop.run_in_executor(
                None, _generate_plan, user["last_analysis"], user["profile"], ctx_str, user.get("units", "kg")
            )
            user["last_plan"] = plan
            _save_store()
            await asyncio.get_running_loop().run_in_executor(None, _db_sync_plan, chat_id, user, plan)
            try:
                await gen_msg.delete()
            except Exception:
                pass
            await _send_plan(update, plan)
            if had_plan:
                follow_up = (
                    "📊 *Plan updated* based on your physique analysis.\n"
                    "Your profile + photo data are now combined.\n"
                    "Tell me to adjust anything, or type `/plan new` to regenerate."
                )
            else:
                follow_up = (
                    "💬 Your plan is built from your profile + photo analysis.\n"
                    "Tell me to adjust anything, or type `/plan new` to regenerate."
                )
            await update.effective_chat.send_message(follow_up, parse_mode="Markdown")
        except Exception as e:
            await gen_msg.edit_text(f"❌ Plan generation failed: {e}")
    else:
        await update.effective_chat.send_message(
            "How many days per week do you want to train?\n\nTap to generate your plan instantly:"
            + _next_steps(
                ("/plan", "generate plan"),
                ("/progress", "track changes"),
            ),
            parse_mode="Markdown",
            reply_markup=_plan_days_keyboard(),
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    # Route to fridge analysis if that command is pending
    if user.get("active_command") == "awaiting_fridge_photo":
        user["active_command"] = None
        _save_store()
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        buf = BytesIO()
        await file.download_to_memory(buf)
        img_b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
        await _handle_fridge_photo(update, user, img_b64)
        return

    # Body image sensitivity check — skip physique analysis if user opted out
    if user.get("profile", {}).get("physique_analysis", "on").lower() == "off":
        await update.message.reply_text(
            "📸 Physique analysis is currently *off* for your account.\n\n"
            "To enable: `/profile physique_analysis=on`",
            parse_mode="Markdown",
        )
        return

    # Angle suggestion for single-photo submissions (first analysis only)
    analyses_count = len(user.get("analyses", []))
    if not update.message.media_group_id and analyses_count == 0:
        await update.message.reply_text(
            "📐 _For the most accurate analysis, send front + back + side photos as one album._\n"
            "_(Analysing this photo now — send more angles any time.)_",
            parse_mode="Markdown",
        )

    # Album (media group) — buffer all photos then analyze together
    media_group_id = update.message.media_group_id
    if media_group_id:
        mg_key = f"mg_{chat_id}_{media_group_id}"

        # Only check cooldown once per album (first photo of the group)
        if mg_key not in context.bot_data and f"{mg_key}_blocked" not in context.bot_data:
            remaining = _check_cooldown(_analyze_cooldowns, chat_id, ANALYZE_COOLDOWN)
            if remaining:
                context.bot_data[f"{mg_key}_blocked"] = True
                await update.message.reply_text(
                    f"⏳ Please wait {remaining}s before submitting another photo."
                )
                return

        # Silently drop remaining photos from a cooldown-blocked album
        if context.bot_data.get(f"{mg_key}_blocked"):
            return

        photos = context.bot_data.setdefault(mg_key, [])
        photos.append(update.message.photo[-1])
        if not context.bot_data.get(f"{mg_key}_sent"):
            context.bot_data[f"{mg_key}_sent"] = True
            asyncio.create_task(_process_media_group(update, context, mg_key))
        return

    # Single photo — cooldown applies normally
    remaining = _check_cooldown(_analyze_cooldowns, chat_id, ANALYZE_COOLDOWN)
    if remaining:
        await update.message.reply_text(
            f"⏳ Please wait {remaining}s before submitting another photo."
        )
        return

    msg = await update.message.reply_text("📸 Analyzing your physique… (20-40 seconds)")
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        buf = BytesIO()
        await file.download_to_memory(buf)
        img_b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

        prev = (user.get("analyses") or [None])[-1]
        loop = asyncio.get_running_loop()
        analysis = await loop.run_in_executor(
            None, _analyze_photo, [img_b64], user["profile"], prev
        )
        user["last_analysis"] = analysis
        entry = {**analysis, "date": _today()}
        user.setdefault("analyses", []).append(entry)
        user["analyses"] = user["analyses"][-20:]
        _save_store()

        result_text = _format_analysis(analysis)
        try:
            await msg.delete()
        except Exception:
            pass
        await _send_long(update.effective_chat.send_message, result_text, parse_mode="Markdown")
        await _auto_plan_after_analysis(update, user, chat_id)
    except Exception as e:
        try:
            await msg.edit_text(f"❌ Analysis failed: {e}")
        except Exception:
            await update.message.reply_text(f"❌ Analysis failed: {e}")


async def handle_plan_days_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles plan:days:{n} — stores chosen day count then generates the plan."""
    query = update.callback_query
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    remaining = _check_cooldown(_plan_cooldowns, chat_id, PLAN_COOLDOWN)
    if remaining:
        await query.answer(f"⏳ Wait {remaining}s before regenerating.", show_alert=True)
        return
    await query.answer()

    days = int(query.data.split(":")[-1])
    user["profile"]["days"] = str(days)
    _save_store()

    await query.edit_message_text(f"🧬 Building your {days}-day plan… (30-60 seconds)")
    ctx_str = _get_bot_context_str(user)
    loop = asyncio.get_running_loop()
    try:
        import garmin_service as _gs_days
        _gd_days = _gs_days.get_cached(chat_id)
    except Exception:
        _gd_days = None
    try:
        if user["last_analysis"]:
            plan = await loop.run_in_executor(
                None, _generate_plan, user["last_analysis"], user["profile"], ctx_str, user.get("units", "kg"), _gd_days
            )
        else:
            plan = await loop.run_in_executor(
                None, _generate_plan_from_profile, user["profile"], ctx_str, user.get("units", "kg"), _gd_days
            )
        user["last_plan"] = plan
        _save_store()
        await loop.run_in_executor(None, _db_sync_plan, chat_id, user, plan)
        try:
            await query.delete_message()
        except Exception:
            pass
        await _send_plan(update, plan)
        await update.effective_chat.send_message(
            "💬 Your plan is built from your profile"
            + (" + photo analysis" if user["last_analysis"] else "")
            + ".\nTell me to adjust anything, or type `/plan new` to regenerate.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await context.bot.send_message(chat_id, f"❌ Plan generation failed: {e}")


# ── Check-in callback handler ─────────────────────────────────────────────────

async def handle_checkin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles ci:{step}:{value} callbacks from the 1-10 score inline keyboards."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    parts = query.data.split(":")   # ["ci", step_key, value]
    step_key = parts[1]
    value = int(parts[2])

    state = user.get("command_state") or {}
    data = state.get("data", {})
    data[step_key] = value
    state["data"] = data
    remaining = state.get("remaining_steps", list(_STEP_LABELS.keys()))

    try:
        idx = remaining.index(step_key)
    except ValueError:
        idx = len(remaining) - 1

    if idx + 1 < len(remaining):
        next_step = remaining[idx + 1]
        state["step"] = idx + 1
        user["command_state"] = state
        _save_store()
        emoji, label, hint = _STEP_LABELS[next_step]
        await query.edit_message_text(
            f"{emoji} *{label}?* _{hint}_",
            parse_mode="Markdown",
            reply_markup=_score_keyboard(next_step),
        )
    else:
        _required_keys = {"sleep", "energy", "soreness", "stress"}
        if not _required_keys.issubset(data.keys()):
            await query.answer("Session expired — please run /checkin again.", show_alert=True)
            return
        user["active_command"] = None
        user["command_state"] = {}
        _save_store()
        try:
            await query.delete_message()
        except Exception:
            pass
        await _finish_checkin(
            update, user, data,
            garmin_data=state.get("garmin_data"),
            workout_summary=state.get("workout_summary", ""),
            nutrition_summary=state.get("nutrition_summary", ""),
        )


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
    if active == "awaiting_fridge_photo":
        await update.message.reply_text(
            "📷 Send a photo of your fridge or pantry and I'll suggest macro-matched recipes.\n\n"
            "Or type /fridge to restart.",
        )
        return
    if active == "fridge_reviewing":
        state = user.get("command_state") or {}
        new_items = [t.strip().title() for t in text.split(",") if t.strip()]
        if new_items:
            added_lower = {a.lower() for a in state.get("added", [])}
            to_add = [item for item in new_items if item.lower() not in added_lower]
            state.setdefault("added", []).extend(to_add)
            user["command_state"] = state
            _save_store()
            label = ", ".join(to_add) if to_add else "nothing new"
            await update.message.reply_text(f"✅ Added: {label}")
            review_msg_id = state.get("review_msg_id")
            if review_msg_id:
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=review_msg_id,
                        text=_fridge_review_text(state.get("ingredients_by_cat", {}), state["added"]),
                        parse_mode="Markdown",
                        reply_markup=_fridge_category_keyboard(state["added"]),
                    )
                except Exception:
                    pass
        return

    if active == "onboarding":
        await update.message.reply_text(
            "👆 Please tap one of the buttons above to continue your setup.\n\n"
            "Or type /start to restart the onboarding quiz."
        )
        return

    if active == "onboard_text":
        state = user.get("command_state") or {}
        user["active_command"] = None
        user["command_state"] = {}
        # Normalise curly quotes/apostrophes (iPhone auto-converts these)
        normalized = (
            text
            .replace("’", "'").replace("‘", "'")
            .replace("“", '"').replace("”", '"')
            .replace("/", " ").replace(",", " ")
        )
        tokens = normalized.split()
        for tok in tokens:
            tok = tok.strip()
            if not tok:
                continue
            if "=" in tok:
                k, _, v = tok.partition("=")
                k, v = k.strip().lower(), v.strip()
                if k == "age":
                    try:
                        user["profile"]["age"] = str(int(v))
                    except ValueError:
                        pass
                elif k in ("height", "h"):
                    user["profile"]["height"] = _parse_height(v)
                elif k in ("weight", "w"):
                    user["profile"]["weight"] = _parse_weight(v)
            else:
                # Feet/inches pattern: 5'7 or 5'7"
                if re.match(r"\d+'\d+", tok):
                    if "height" not in user["profile"]:
                        user["profile"]["height"] = _parse_height(tok)
                    continue
                clean = re.sub(r"[^0-9.]", "", tok)
                if not clean:
                    continue
                try:
                    n = float(clean)
                except ValueError:
                    continue
                if re.search(r"cm", tok):
                    user["profile"]["height"] = _parse_height(tok)
                elif re.search(r"lbs?|pounds?", tok):
                    user["profile"]["weight"] = _parse_weight(tok)
                elif re.search(r"kg", tok):
                    user["profile"]["weight"] = _parse_weight(tok)
                elif n < 110 and "age" not in user["profile"]:
                    user["profile"]["age"] = str(int(n))
                elif 140 <= n <= 230 and "height" not in user["profile"]:
                    user["profile"]["height"] = str(int(n))
                elif 40 <= n <= 200 and "weight" not in user["profile"]:
                    user["profile"]["weight"] = str(int(n))
        _save_store()
        gen_msg = await update.message.reply_text("🧬 Building your personalised plan… (30-60 seconds)")
        ctx_str = _get_bot_context_str(user)
        loop = asyncio.get_running_loop()
        try:
            plan = await loop.run_in_executor(
                None, _generate_plan_from_profile, user["profile"], ctx_str, user.get("units", "kg")
            )
            user["last_plan"] = plan
            _save_store()
            await loop.run_in_executor(None, _db_sync_plan, chat_id, user, plan)
            try:
                await gen_msg.delete()
            except Exception:
                pass
            await _send_plan(update, plan)
            await update.message.reply_text(
                "📸 *Want a more personalised plan?*\n\n"
                "Send me a front + back + side photo and I'll update it based on "
                "your specific physique and muscle development.\n\n"
                "Tell me to change anything, or type `/plan new` to regenerate.",
                parse_mode="Markdown",
            )
        except Exception as e:
            await gen_msg.edit_text(f"❌ Plan generation failed: {e}")
        return

    if active == "profile_input":
        state = user.get("command_state") or {}
        field = state.get("field", "")
        if field == "_custom":
            for arg in text.split():
                if "=" in arg:
                    k, _, v = arg.partition("=")
                    k = k.strip().lower()
                    v = v.strip()
                    if k == "height":
                        v = _parse_height(v)
                    elif k == "weight":
                        try:
                            v = _parse_weight(v)
                        except (ValueError, TypeError):
                            pass
                    elif k == "goal" and user["profile"].get("goal") != v:
                        user["profile"]["goal_set_date"] = _today()
                    user["profile"][k] = v
        else:
            value = text.strip()
            if field == "height":
                value = _parse_height(value)
            elif field == "weight":
                try:
                    value = _parse_weight(value)
                except (ValueError, TypeError):
                    pass
            elif field == "goal" and user["profile"].get("goal") != value:
                user["profile"]["goal_set_date"] = _today()
            if field:
                user["profile"][field] = value
        user["active_command"] = None
        user["command_state"] = {}
        _save_store()
        await asyncio.get_running_loop().run_in_executor(None, _db_sync_profile, chat_id, user)
        profile = user["profile"]
        current = "\n".join(f"• {k}: {esc(str(v))}" for k, v in profile.items() if k not in _HIDDEN_PROFILE_KEYS) or "Not set yet."
        await update.message.reply_text(
            f"✅ *Profile updated!*\n\n{current}\n\nTap a field to change another:",
            parse_mode="Markdown",
            reply_markup=_profile_menu_keyboard(profile, user),
        )
        return

    if active == "goals_input":
        state = user.get("command_state") or {}
        field = state.get("field", "")
        if field == "_custom":
            goals_list = user.setdefault("goals", [])
            active_goal = next((g for g in reversed(goals_list) if g.get("is_active")), None)
            if not active_goal:
                goal_type = user["profile"].get("goal", "health")
                active_goal = {
                    "goal_type": goal_type,
                    "target_weight_kg": None,
                    "target_bf_pct": None,
                    "target_date": None,
                    "start_weight_kg": float(user["profile"].get("weight", 0) or 0) or None,
                    "created_at": _today(),
                    "is_active": True,
                }
                goals_list.append(active_goal)
            active_goal["custom_description"] = text.strip()
        else:
            goals_list = user.setdefault("goals", [])
            active_goal = next((g for g in reversed(goals_list) if g.get("is_active")), None)
            if not active_goal:
                goal_type = user["profile"].get("goal", "health")
                active_goal = {
                    "goal_type": goal_type,
                    "target_weight_kg": None,
                    "target_bf_pct": None,
                    "target_date": None,
                    "start_weight_kg": float(user["profile"].get("weight", 0) or 0) or None,
                    "created_at": _today(),
                    "is_active": True,
                }
                goals_list.append(active_goal)
            val = text.strip()
            parsed_ok = False
            if field == "weight":
                try:
                    active_goal["target_weight_kg"] = float(_parse_weight(val) or val)
                    parsed_ok = True
                except (ValueError, TypeError):
                    pass
            elif field == "bf":
                try:
                    active_goal["target_bf_pct"] = float(val.replace("%", ""))
                    parsed_ok = True
                except ValueError:
                    pass
            elif field == "date":
                if re.match(r"\d{4}-\d{2}-\d{2}", val):
                    try:
                        from datetime import date as _date_cls
                        if _date_cls.fromisoformat(val[:10]) <= _date_cls.today():
                            await update.message.reply_text(
                                "❌ Target date must be in the future. Try again (e.g. `2027-01-01`):",
                                parse_mode="Markdown",
                            )
                            return
                    except ValueError:
                        pass
                    active_goal["target_date"] = val[:10]
                    user["profile"]["show_date"] = val[:10]
                    parsed_ok = True
            if not parsed_ok:
                await update.message.reply_text(
                    "❌ Couldn't parse that. Try:\n"
                    "• Weight: `90kg` or `200lbs`\n"
                    "• Body fat: `12` or `15%`\n"
                    "• Date: `2026-12-01`",
                    parse_mode="Markdown",
                    reply_markup=_goals_menu_keyboard(user),
                )
                return
        user["active_command"] = None
        user["command_state"] = {}
        _save_store()
        await update.message.reply_text(
            "🎯 *Goal updated!*\n\nTap to continue editing:",
            parse_mode="Markdown",
            reply_markup=_goals_menu_keyboard(user),
        )
        return

    if active == "measurements_input":
        state = user.get("command_state") or {}
        field = state.get("field", "")
        user["active_command"] = None
        user["command_state"] = {}
        _MEAS_FIELDS = {
            "weight": "body_weight_kg",
            "waist":  "waist_cm",
            "chest":  "chest_cm",
            "hips":   "hips_cm",
            "arm":    "left_arm_cm",
            "thigh":  "left_thigh_cm",
        }
        entry: dict = {"date": _today()}
        if field == "_custom":
            for arg in text.split():
                if "=" not in arg:
                    continue
                k, _, v = arg.partition("=")
                k = k.strip().lower().replace("-", "").replace("_", "")
                db_key = _MEAS_FIELDS.get(k)
                if not db_key:
                    continue
                parsed = _parse_logset_weight_kg(v) if db_key == "body_weight_kg" else _parse_measurement_cm(v)
                if parsed is not None:
                    entry[db_key] = parsed
        else:
            db_key = _MEAS_FIELDS.get(field)
            if db_key:
                parsed = _parse_logset_weight_kg(text.strip()) if db_key == "body_weight_kg" else _parse_measurement_cm(text.strip())
                if parsed is not None:
                    entry[db_key] = parsed
        last_meas = user["measurements"][-1] if user["measurements"] else None
        if len(entry) > 1:
            prev_meas = last_meas
            user["measurements"].append(entry)
            user["measurements"] = user["measurements"][-100:]
            _save_store()
            await asyncio.get_running_loop().run_in_executor(None, _db_sync_measurement, chat_id, user, entry)
            lines = [f"✅ *Measurement saved ({_today()})*\n"]
            for db_key, val in entry.items():
                if db_key == "date":
                    continue
                label = db_key.replace("_cm", "").replace("_kg", "").replace("_", " ").title()
                unit = "kg" if db_key == "body_weight_kg" else "cm"
                change = ""
                if prev_meas and db_key in prev_meas and prev_meas[db_key] is not None:
                    diff = round(val - prev_meas[db_key], 1)
                    if diff != 0:
                        arrow = "▲" if diff > 0 else "▼"
                        change = f" ({arrow} {abs(diff)}{unit})"
                lines.append(f"• {label}: {val}{unit}{change}")
            new_last = user["measurements"][-1]
            await update.message.reply_text(
                "\n".join(lines) + "\n\nLog another:",
                parse_mode="Markdown",
                reply_markup=_measurements_menu_keyboard(new_last, user),
            )
        else:
            await update.message.reply_text(
                "Couldn't parse that. Try `83kg`, `185lbs`, `32in`, or `81cm`.",
                reply_markup=_measurements_menu_keyboard(last_meas, user),
            )
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
        _loop = asyncio.get_running_loop()
        await _loop.run_in_executor(None, _db_sync_set, chat_id, user, entry)
        if is_pr:
            await _loop.run_in_executor(None, _db_sync_pr, chat_id, user, ex, weight, reps, entry["estimated_1rm"])
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
        ctx_str = _get_bot_context_str(user)
        reply, plan_update, plan_regen = _chat_with_coach(text, user, ctx_str)

        if plan_regen:
            for k, v in plan_regen.items():
                user["profile"][k] = str(v)
            regen_msg = await update.message.reply_text(
                "🧬 Got it — rebuilding your full plan with the changes… (30-60 seconds)"
            )
            try:
                loop = asyncio.get_running_loop()
                regen_ctx = _get_bot_context_str(user)
                if user["last_analysis"]:
                    plan = await loop.run_in_executor(
                        None, _generate_plan, user["last_analysis"], user["profile"], regen_ctx, user.get("units", "kg")
                    )
                else:
                    plan = await loop.run_in_executor(
                        None, _generate_plan_from_profile, user["profile"], regen_ctx, user.get("units", "kg")
                    )
                user["last_plan"] = plan
                _save_store()
                await loop.run_in_executor(None, _db_sync_plan, chat_id, user, plan)
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

        if len(reply) <= _TG_MAX:
            await msg.edit_text(reply, parse_mode="Markdown")
        else:
            await msg.delete()
            await _send_long(update.message.reply_text, reply, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Something went wrong: {e}")


def _chat_with_coach(text: str, user: dict, context_str: str = "") -> tuple[str, dict | None, dict | None]:
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
    ctx_section = f"Athlete recent history:\n{context_str}\n\n" if context_str else ""

    system = (
        "You are a personal fitness and nutrition coach. You give specific, "
        "evidence-based advice tailored to the individual.\n\n"
        f"{ctx_section}"
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

    response = get_anthropic_client().messages.create(
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
            full_reply += "\n\n⚠️ _Plan change detected but couldn't be applied — type /plan to regenerate._"
        full_reply = re.sub(r"```plan_update[\s\S]*?```", "", full_reply).strip()

    plan_regen = None
    match_regen = re.search(r"```plan_regenerate\s*([\s\S]*?)```", full_reply)
    if match_regen:
        try:
            plan_regen = json.loads(match_regen.group(1).strip())
        except json.JSONDecodeError:
            full_reply += "\n\n⚠️ _Plan regeneration requested but couldn't be parsed — type `/plan new` to regenerate._"
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

def _analyze_photo(images_b64: list[str], profile: dict, prev: dict | None = None) -> dict:
    """Analyze one or more base64-encoded photos as a single coaching assessment."""
    profile_ctx = ""
    if profile:
        profile_ctx = (
            f"\nAthlete: {profile.get('age','?')}yo {profile.get('gender','?')}, "
            f"{profile.get('height','?')}cm, {profile.get('weight','?')}kg, "
            f"goal={profile.get('goal','?')}, "
            f"experience={profile.get('experience','?')}, "
            f"{profile.get('days','?')} training days/week"
        )

    comparison_ctx = ""
    if prev:
        prev_angle = prev.get("photo_angle", "unknown")
        prev_areas = ", ".join(prev.get("areas_to_improve", [])[:2])
        angle_note = (
            "If this is the same angle as before, compare directly. "
            "If a different angle, note what is newly visible rather than repeating the prior score."
        )
        comparison_ctx = (
            f"\nPrevious analysis ({prev_angle} view) — "
            f"BF: {prev.get('body_fat_estimate', '?')}, score: {prev.get('overall_physique_score', '?')}/10"
            + (f", priority areas: {prev_areas}" if prev_areas else "")
            + f". {angle_note}"
        )

    multi_note = (
        f"I'm sending you {len(images_b64)} photos from different angles of the same athlete. "
        "Analyze them together as one combined progress check-in, noting the angle of each.\n\n"
    ) if len(images_b64) > 1 else ""

    photo_word = "these physique photos" if len(images_b64) > 1 else "this physique photo"
    prompt = (
        "You are an expert fitness coach who works with athletes of all ages, genders, and "
        f"experience levels — from complete beginners to competitive athletes. "
        f"Analyze {photo_word}.{profile_ctx}{comparison_ctx}\n\n"
        f"{multi_note}"
        "Identify the photo angle/pose for each image (front | back | side_left | side_right | three_quarter | unknown). "
        "Only score muscle groups that are clearly visible — omit any group that cannot be meaningfully assessed. "
        "Front: chest/core/arms/quads assessable. Back: lats/traps/hamstrings assessable. Side: posture/glutes assessable.\n\n"
        "Return ONLY valid JSON with this exact structure:\n"
        '{\n'
        '    "photo_angle": "front",\n'
        '    "angle_notes": "Front + back views provided. Full upper/lower body coverage.",\n'
        '    "body_fat_estimate": "15-18%",\n'
        '    "body_fat_confidence": "medium",\n'
        '    "overall_physique_score": 7.2,\n'
        '    "muscle_development": {\n'
        '        "chest": {"score": 7, "notes": "Good upper chest, lower needs work", "action": "Add 2 sets incline DB press; focus on full stretch at bottom"},\n'
        '        "back": {"score": 6, "notes": "Width decent, thickness lacking", "action": "Add 3 sets barbell row; increase dead-hang pull-up volume"}\n'
        '    },\n'
        '    "strengths": ["Good shoulder-to-waist ratio", "Chest fullness"],\n'
        '    "areas_to_improve": ["Leg development", "Overall conditioning"],\n'
        '    "symmetry_notes": "Left shoulder slightly higher. Overall symmetry good.",\n'
        '    "priority_improvements": ["Most impactful change 1", "Most impactful change 2"],\n'
        '    "coach_message": "Specific, motivating 2-sentence message for this athlete"\n'
        '}'
    )

    image_blocks = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}
        for b64 in images_b64
    ]
    message = get_anthropic_client().messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [*image_blocks, {"type": "text", "text": prompt}],
        }],
    )

    text = message.content[0].text
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        raise ValueError(f"Photo analysis returned non-JSON response: {text[:200]}")


# ── Claude: plan generation ───────────────────────────────────────────────────

def _build_plan_prompt(profile: dict, analysis: dict | None, days: int, context_str: str = "", user_units: str = "kg", garmin_data: dict | None = None) -> str:
    injuries_note = profile.get("injuries", "") if profile else ""
    diet_restrictions = profile.get("dietary_restrictions", "") if profile else ""
    show_date = profile.get("show_date", "") if profile else ""

    # Convert stored metric values to the user's preferred display unit for the prompt
    if user_units == "lbs" and profile:
        try:
            _w_lbs = round(float(profile.get("weight", 0) or 0) * 2.20462, 1)
            w_display = f"{_w_lbs}lbs" if _w_lbs else "?lbs"
        except (ValueError, TypeError):
            w_display = "?lbs"
        try:
            _h_in = round(float(profile.get("height", 0) or 0) / 2.54)
            h_display = f"{_h_in // 12}'{_h_in % 12}\"" if _h_in else "?"
        except (ValueError, TypeError):
            h_display = "?"
    else:
        w_display = f"{profile.get('weight', '?')}kg" if profile else "?"
        h_display = f"{profile.get('height', '?')}cm" if profile else "?"

    unit_label = "lbs" if user_units == "lbs" else "kg"
    height_unit = "feet and inches" if user_units == "lbs" else "cm"
    progression_example = "5lbs" if user_units == "lbs" else "2.5kg"

    profile_ctx = (
        f"Age: {profile.get('age', 'not specified')} | "
        f"Gender: {profile.get('gender', 'not specified')} | "
        f"Height: {h_display} | "
        f"Weight: {w_display} | "
        f"Goal: {profile.get('goal', 'general health')} | "
        f"Experience: {profile.get('experience', 'beginner')} | "
        f"Training days: {days}/week"
        + (f" | Injuries/limitations: {injuries_note}" if injuries_note else "")
        + (f" | Dietary restrictions: {diet_restrictions}" if diet_restrictions else "")
    ) if profile else "No profile data — assume healthy adult beginner with general fitness goal."

    if analysis:
        body_ctx = (
            f"BODY ANALYSIS:\n"
            f"- Body fat: {analysis.get('body_fat_estimate', '?')}\n"
            f"- Priority improvements: {', '.join(analysis.get('priority_improvements', []))}\n"
            f"- Weakest areas: {', '.join(analysis.get('areas_to_improve', []))}\n"
            f"- Muscle coaching notes: {json.dumps(analysis.get('muscle_development', {}))}"
        )
    else:
        body_ctx = "No photo analysis — build the plan entirely from the profile stats above."

    ctx_section = f"ATHLETE RECENT HISTORY:\n{context_str}\n\n" if context_str else ""
    injury_clause = (
        f"CRITICAL: avoid or modify exercises that stress these injuries/limitations: "
        f"{injuries_note}. Substitute with safe alternatives and note the substitution.\n\n"
        if injuries_note else "\n\n"
    )
    diet_clause = (
        f"ABSOLUTE DIETARY CONSTRAINT: This athlete has the following dietary restrictions: {diet_restrictions}. "
        f"Every single meal, food suggestion, sample meal, and foods_to_prioritize item MUST comply strictly. "
        f"Do NOT suggest any food that violates these restrictions under any circumstances. "
        f"If vegan — no meat, fish, dairy, or eggs. If vegetarian — no meat or fish. "
        f"If lactose intolerant — no dairy. If celiac or gluten-free — no wheat, barley, rye, or gluten. "
        f"Violation of this constraint is a critical error.\n\n"
        if diet_restrictions else ""
    )
    # Contest prep countdown
    show_date_clause = ""
    if show_date:
        try:
            from datetime import date as _date_cls
            show_dt = _date_cls.fromisoformat(str(show_date))
            days_out = (show_dt - _date_cls.today()).days
            if days_out > 0:
                show_date_clause = (
                    f"CONTEST PREP: Show date is {show_date} — {days_out} days out. "
                    f"This is competition prep. "
                    + ("Peak week protocol (water/sodium manipulation, carb load) should be referenced." if days_out <= 14 else
                       "Prioritise fat loss and conditioning. Include posing practice in coaching notes." if days_out <= 56 else
                       "Prioritise building stage muscle retention while reducing body fat.")
                    + "\n\n"
                )
        except Exception:
            pass

    periodization = profile.get("periodization", "linear") if profile else "linear"
    if periodization == "undulating":
        perio_clause = (
            "PERIODIZATION: Use Daily Undulating Periodization (DUP). "
            "Rotate rep ranges across the week: heavy day (3–5 reps, 85–90% 1RM), "
            "moderate day (6–10 reps, 70–80% 1RM), light day (12–15 reps, 60–70% 1RM). "
            "Label each day clearly by type (Heavy/Moderate/Light).\n\n"
        )
    elif periodization == "block":
        perio_clause = (
            "PERIODIZATION: Use Block Periodization. Design a 12-week plan in three phases: "
            "Hypertrophy (weeks 1–4, 8–12 reps, 65–75% 1RM, high volume), "
            "Strength (weeks 5–8, 4–6 reps, 80–90% 1RM, moderate volume), "
            "Peaking (weeks 9–12, 1–3 reps, 90–95% 1RM, low volume). "
            "Label each phase and its goals in the coaching section.\n\n"
        )
    else:
        perio_clause = ""

    vo2_clause = ""
    if garmin_data and garmin_data.get("vo2_max") and profile and profile.get("age"):
        try:
            age_int = int(profile["age"])
            max_hr = 220 - age_int
            z2_lo, z2_hi = int(max_hr * 0.60), int(max_hr * 0.70)
            vo2_clause = (
                f"CARDIO ZONES: Athlete VO2max = {garmin_data['vo2_max']:.0f} ml/kg/min (Garmin). "
                f"Estimated max HR = {max_hr}bpm (age {age_int}). "
                f"Zone 2 target: {z2_lo}–{z2_hi}bpm. Use these exact numbers in zone2_cardio recommendations.\n\n"
            )
        except (TypeError, ValueError):
            pass

    med_clause = ""
    medications = profile.get("medications", "") if profile else ""
    if medications and medications.lower() != "none":
        interaction_warnings = _check_supplement_interactions(medications)
        med_clause = (
            f"MEDICATIONS: Athlete takes: {medications}. "
            "Review ALL supplement recommendations for interactions with these medications. "
        )
        if interaction_warnings:
            med_clause += "Known interactions to flag in the plan: " + "; ".join(
                w.replace("*", "").replace("⚠️ ", "") for w in interaction_warnings
            ) + ". "
        med_clause += "Add interaction warnings to the relevant supplement entries' 'benefit' field.\n\n"

    workout_time = profile.get("workout_time", "variable") if profile else "variable"
    timing_clause = (
        f"MEAL TIMING: Athlete trains in the {workout_time}. "
        "Populate the meal_timing field with specific, timed nutrition windows:\n"
        "• Pre-workout (60–90 min before): 0.4g/kg body weight fast carbs (banana, white rice, sports drink) "
        "+ 20–30g fast-digesting protein (whey shake, Greek yogurt). "
        "Avoid high fat/fibre in this window.\n"
        "• Post-workout (within 30–45 min): 0.4g/kg protein + 0.8g/kg fast carbs. "
        "This is the anabolic window — do not skip.\n"
        "• Intra-workout (only for sessions >60 min): 30–60g/hr fast carbs (gels, banana, diluted juice).\n"
        "Tailor the exact foods and times to this athlete's schedule and dietary preferences.\n\n"
    )

    return (
        "You are an expert strength coach and sports nutritionist who works with all populations — "
        "beginners to advanced athletes, all ages (teens to 70+), all genders, all goals "
        "(fat loss, muscle gain, general health, sport performance, recomp).\n\n"
        f"{ctx_section}"
        f"ATHLETE: {profile_ctx}\n"
        f"{body_ctx}\n\n"
        "Tailor EVERYTHING to this specific athlete. A beginner gets simpler movements and lower volume. "
        "An older athlete gets joint-friendly exercise selection. Nutrition targets must match their "
        f"actual goal and body weight. {injury_clause}"
        f"{diet_clause}"
        f"{show_date_clause}"
        f"{perio_clause}"
        "Diet planning must account for gut health: "
        "(1) include at least one fermented probiotic food in foods_to_prioritize (Greek yogurt, kefir, kimchi, sauerkraut); "
        "(2) include prebiotic/high-fiber foods (garlic, onion, oats, legumes, bananas); "
        "(3) populate the gut_health_note field with 1–2 sentences specific to this athlete's goal; "
        "(4) flag any patterns likely to impair gut health (excess alcohol, low fiber, ultra-processed foods).\n\n"
        "WARM-UP: every training day MUST include a 5-minute warm-up block in the 'warmup' field — "
        "at least 2 specific warm-up exercises (e.g. band pull-aparts, hip circles, light goblet squats).\n\n"
        f"{vo2_clause}"
        "CARDIO: include 'zone2_cardio' in workout — recommend 2-3 sessions per week of 25-40 min "
        "Zone 2 (conversational pace, 60-70% max HR) for cardiovascular health and fat oxidation. "
        "Adjust volume based on goal (more for cut/recomp, less for pure bulk/strength).\n\n"
        "MACROS: provide BOTH training-day and rest-day macro variants in the diet section. "
        "Training days: higher carbs. Rest days: slightly lower carbs, same protein.\n\n"
        f"{timing_clause}"
        f"{med_clause}"
        "SUPPLEMENTS: always include Beta-Alanine (grade B) at priority 4 — "
        "3.2-6.4g/day for high-rep work (endurance/hypertrophy), causes tingling harmless paresthesia.\n\n"
        f"UNITS: This athlete uses {unit_label}. Output ALL weight references throughout the plan "
        f"(progression increments, exercise coaching cues, example loads) in {unit_label}. "
        f"Height references in {height_unit}. Do NOT mix units — no kg if the athlete uses lbs, "
        f"no lbs if the athlete uses kg.\n\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '    "workout": {\n'
        '        "split": "4-Day Upper/Lower",\n'
        '        "zone2_cardio": "2× 30 min at conversational pace (walking, cycling, light rowing) on rest days",\n'
        '        "days": [\n'
        '            {\n'
        '                "day": "Monday",\n'
        '                "focus": "Upper Push",\n'
        '                "warmup": "5 min: 15 band pull-aparts, 10 shoulder circles each arm, 10 scapular push-ups",\n'
        '                "exercises": [\n'
        '                    {"name": "Barbell Bench Press", "sets": 4, "reps": "6-8", "rest": "3min", "notes": "Full ROM, 2-sec descent"},\n'
        '                    {"name": "Incline Dumbbell Press", "sets": 3, "reps": "8-10", "rest": "2min", "notes": "Focus on upper chest stretch"},\n'
        '                    {"name": "Overhead Press", "sets": 4, "reps": "6-8", "rest": "3min", "notes": "Strict form, no leg drive"},\n'
        '                    {"name": "Lateral Raises", "sets": 4, "reps": "12-15", "rest": "90s", "notes": "Controlled, slight forward lean"},\n'
        '                    {"name": "Tricep Pushdowns", "sets": 3, "reps": "10-12", "rest": "90s", "notes": "Full extension"}\n'
        '                ]\n'
        '            }\n'
        '        ],\n'
        f'        "progression": "Add {progression_example} when you complete all sets at top of rep range for 2 consecutive sessions.",\n'
        '        "deload": "Every 4-6 weeks: reduce load 40%, maintain volume."\n'
        '    },\n'
        '    "diet": {\n'
        '        "calories": 2800,\n'
        '        "protein_g": 180,\n'
        '        "carbs_g": 320,\n'
        '        "fat_g": 78,\n'
        '        "training_day_macros": "protein=180g, carbs=350g, fat=78g (2900 kcal)",\n'
        '        "rest_day_macros": "protein=180g, carbs=220g, fat=78g (2250 kcal)",\n'
        '        "rationale": "Why these exact numbers for this athlete",\n'
        '        "meal_timing": "Pre/post workout nutrition guidance",\n'
        '        "sample_meals": ["Breakfast: ...", "Lunch: ...", "Dinner: ..."],\n'
        '        "foods_to_prioritize": ["Chicken breast", "Eggs", "Rice", "Oats", "Greek yogurt (probiotic)"],\n'
        '        "foods_to_limit": ["Ultra-processed foods", "Alcohol"],\n'
        '        "gut_health_note": "One sentence on gut health considerations for this athlete"\n'
        '    },\n'
        '    "supplements": [\n'
        '        {"priority": 1, "name": "Creatine Monohydrate", "dose": "5g daily", "timing": "Anytime", "grade": "A", "benefit": "5-15% strength gains. Most evidence-backed supplement."},\n'
        '        {"priority": 2, "name": "Whey Protein", "dose": "25-40g per serving", "timing": "Post-workout or to hit daily protein", "grade": "A", "benefit": "High leucine triggers muscle protein synthesis."},\n'
        '        {"priority": 3, "name": "Caffeine", "dose": "200-400mg", "timing": "30-45min pre-workout", "grade": "A", "benefit": "Increases power output, reduces perceived exertion."},\n'
        '        {"priority": 4, "name": "Beta-Alanine", "dose": "3.2-6.4g daily", "timing": "Split into 2-3 doses to reduce tingling", "grade": "B", "benefit": "Buffers lactic acid in high-rep sets; best for hypertrophy/endurance work."},\n'
        '        {"priority": 5, "name": "Vitamin D3 + K2", "dose": "3000 IU D3 + 100mcg K2", "timing": "With a fat-containing meal", "grade": "B", "benefit": "Supports testosterone, bone density, immunity."},\n'
        '        {"priority": 6, "name": "Omega-3 Fish Oil", "dose": "2-3g EPA+DHA", "timing": "With meals", "grade": "B", "benefit": "Reduces DOMS, supports joint health."},\n'
        '        {"priority": 7, "name": "Magnesium Glycinate", "dose": "300-400mg", "timing": "Before bed", "grade": "B", "benefit": "Improves sleep quality and recovery."}\n'
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


def _run_plan_api(prompt: str) -> dict:
    """Call the plan generation API with 8k tokens and auto-retry if truncated."""
    client = get_anthropic_client()
    resp = client.messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "max_tokens":
        compact_prompt = (
            prompt
            + "\n\nIMPORTANT: Keep every text field under 10 words. No filler. "
            "Return ONLY the JSON object, nothing else."
        )
        resp = client.messages.create(
            model=ANALYSIS_MODEL,
            max_tokens=8192,
            messages=[{"role": "user", "content": compact_prompt}],
        )
    return _parse_plan_response(resp.content[0].text)


def _generate_plan(analysis: dict, profile: dict, context_str: str = "", user_units: str = "kg", garmin_data: dict | None = None) -> dict:
    """Generate a full training plan from photo analysis + profile."""
    try:
        days = int(re.sub(r"[^0-9]", "", str(profile.get("days", "4"))) or "4")
    except (ValueError, TypeError):
        days = 4
    prompt = _build_plan_prompt(profile, analysis, days, context_str, user_units, garmin_data=garmin_data)
    return _run_plan_api(prompt)


def _generate_plan_from_profile(profile: dict, context_str: str = "", user_units: str = "kg", garmin_data: dict | None = None) -> dict:
    """Generate a full training plan from profile data only."""
    try:
        days = int(re.sub(r"[^0-9]", "", str(profile.get("days", "4"))) or "4")
    except (ValueError, TypeError):
        days = 4
    prompt = _build_plan_prompt(profile, None, days, context_str, user_units, garmin_data=garmin_data)
    return _run_plan_api(prompt)


# ── Research ──────────────────────────────────────────────────────────────────

async def _fetch_research_summaries(topic: str | None = None) -> list[str]:
    summaries = []
    topics = [topic] if topic else RESEARCH_TOPICS[:3]
    for t in topics:
        try:
            papers = await _search_pubmed(t)
            if papers:
                summary = _summarize_papers(t, papers)
                summaries.append(f"*{t.replace('2024', '').strip().title()}*\n{summary}")
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"Warning: research topic {t!r} failed: {e}")
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
    except Exception as e:
        print(f"Warning: _parse_pubmed_xml failed: {e}")
    return papers


def _summarize_papers(topic: str, papers: list) -> str:
    text = "\n\n".join(
        f"Title: {p['title']} ({p.get('year', '')})\n{p['abstract']}"
        for p in papers[:4]
    )
    message = get_anthropic_client().messages.create(
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


async def _fetch_reddit_posts(subreddit: str, limit: int = 12) -> list[dict]:
    """Fetch hot posts from a subreddit via the public JSON API (no auth required)."""
    url = f"https://www.reddit.com/r/{subreddit}/hot.json"
    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "BodyBuildingCoachBot/1.0 (fitness coaching research)"},
    ) as client:
        resp = await client.get(url, params={"limit": limit})
    children = resp.json().get("data", {}).get("children", [])
    posts = []
    for child in children:
        d = child.get("data", {})
        if d.get("stickied") or d.get("score", 0) < 30:
            continue
        posts.append({
            "title": d.get("title", ""),
            "selftext": (d.get("selftext") or "")[:400],
            "score": d.get("score", 0),
        })
    return posts


def _summarize_reddit(subreddit: str, posts: list[dict]) -> str:
    """Summarise hot Reddit posts into coach-relevant insights using Haiku."""
    text = "\n\n".join(
        f"[{p['score']}↑] {p['title']}\n{p['selftext']}".strip()
        for p in posts[:6]
    )
    message = get_anthropic_client().messages.create(
        model=SUMMARY_MODEL,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": (
                f'What are the key fitness, nutrition, or recovery insights a coach should know '
                f'from these top r/{subreddit} discussions? '
                f'3 sentences max. Practical and coach-relevant only:\n\n{text}'
            ),
        }],
    )
    return message.content[0].text.strip()


async def _fetch_reddit_summaries(subreddits: list[str] | None = None, topic: str | None = None) -> list[str]:
    """Fetch and summarise hot posts from fitness subreddits, optionally filtered by topic."""
    targets = (subreddits or REDDIT_SUBREDDITS)[:4]  # cap at 4 to control cost and latency
    summaries = []
    for sub in targets:
        try:
            posts = await _fetch_reddit_posts(sub)
            if posts:
                summary = await asyncio.get_running_loop().run_in_executor(
                    None, _summarize_reddit, sub, posts
                )
                summaries.append(f"*r/{sub}*\n{summary}")
            await asyncio.sleep(2.0)  # Reddit rate-limit guidance: 1 req/2s
        except Exception as e:
            print(f"Warning: Reddit fetch for r/{sub} failed: {e}")
    return summaries


# ── Safety / privacy commands ─────────────────────────────────────────────────

async def cmd_privacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Explain what data the bot stores and how to delete it."""
    await update.message.reply_text(
        "🔒 *Your Privacy & Data*\n\n"
        "*What is stored on our server:*\n"
        "• Telegram chat ID (to identify you)\n"
        "• Profile data you enter (age, weight, goal, injuries etc.)\n"
        "• Workout sets, personal records, meal logs\n"
        "• Daily check-ins and recovery scores\n"
        "• Garmin/MFP credentials (AES-256 encrypted)\n"
        "• Conversation history with the AI coach\n\n"
        "*Conversation history:*\n"
        "The bot retains a rolling window of your recent messages to "
        "provide coaching continuity across sessions. This history is "
        "stored in the bot's data file alongside your profile and logs. "
        "Use /delete\\_my\\_data to erase it permanently. "
        "Telegram itself also stores messages — see telegram.org/privacy.\n\n"
        "*Third-party services used:*\n"
        "• *Anthropic* — AI analysis. Your data is sent to process "
        "requests but is not used to train models (API usage).\n"
        "• *Garmin Connect* — synced only when you connect your account.\n"
        "• *MyFitnessPal* — synced only when you connect your account.\n"
        "• *PubMed / Reddit* — public data only; no personal info sent.\n\n"
        "*Your rights:*\n"
        "• Export your data: /export\n"
        "• Delete all data permanently: /delete\\_my\\_data\n\n"
        "_Data is hosted on Railway.app. No data is sold or shared with advertisers._",
        parse_mode="Markdown",
    )


async def cmd_delete_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask for confirmation before permanently deleting all user data."""
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, delete everything", callback_data="del:confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="del:cancel"),
    ]])
    await update.message.reply_text(
        "⚠️ *Delete all my data?*\n\n"
        "This will permanently erase:\n"
        "• Your profile, measurements and weight logs\n"
        "• All workout set logs and personal records\n"
        "• All check-ins, meal logs, and streaks\n"
        "• Connected Garmin/MFP credentials\n\n"
        "_This cannot be undone._",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def handle_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle del:confirm / del:cancel inline button presses."""
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id
    action = query.data.split(":")[1]

    if action == "confirm":
        if chat_id in user_data:
            del user_data[chat_id]
        _save_store()
        # Clear Garmin cache entry
        try:
            _gcache = Path(os.getenv("DATA_DIR", ".")) / "garmin_cache.json"
            if _gcache.exists():
                _gc = json.loads(_gcache.read_text(encoding="utf-8"))
                _gc.pop(str(chat_id), None)
                _gcache.write_text(json.dumps(_gc), encoding="utf-8")
        except Exception:
            pass
        # Delete any stored photo files belonging to this user
        try:
            _data_dir = Path(os.getenv("DATA_DIR", "."))
            for _f in _data_dir.glob(f"{chat_id}_*"):
                _f.unlink(missing_ok=True)
        except Exception:
            pass
        await query.edit_message_text(
            "✅ All your data has been permanently deleted.\n\n"
            "Type /start if you want to begin again.",
        )
    else:
        await query.edit_message_text("❌ Deletion cancelled. Your data is safe.")


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the user's full data as a JSON file attachment."""
    import io
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    # Strip sensitive encrypted credential fields
    safe = {k: v for k, v in user.items() if k not in ("garmin_pass_enc", "mfp_pass_enc")}
    safe["_export_date"] = _today()

    data_bytes = json.dumps(safe, ensure_ascii=False, indent=2).encode("utf-8")
    bio = io.BytesIO(data_bytes)
    bio.name = f"bodybuilding_coach_export_{_today()}.json"

    await update.message.reply_document(
        document=bio,
        filename=bio.name,
        caption=(
            "✅ Your full data export — workout logs, meals, check-ins, PRs and profile.\n\n"
            "To delete this data from our server, use /delete\\_my\\_data."
        ),
        parse_mode="Markdown",
    )


async def cmd_peakweek(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a contest peak week protocol (water/sodium taper, carb load, posing schedule)."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    profile = user.get("profile", {})

    if profile.get("goal", "").lower() not in ("prep", "cut"):
        await update.message.reply_text(
            "⚠️ /peakweek is designed for athletes in contest prep or a final cut.\n\n"
            "Set your goal to prep or cut first:\n"
            "• `/profile goal=prep` — for contest prep\n"
            "• `/profile goal=cut` — for a final cutting phase",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text("🏆 Generating peak week protocol…")
    try:
        prompt = (
            "Generate a 7-day peak week protocol for a competitive physique athlete. "
            f"Profile: {json.dumps(profile)}\n\n"
            "Include: daily water intake (litres), sodium intake (mg), carbohydrate intake (g), "
            "training recommendations, and a posing practice schedule (15 min/day minimum). "
            "Structure as a day-by-day plan (Day 1 = 7 days out, Day 7 = show day). "
            "Be specific with numbers. Format as clear markdown with daily sections."
        )

        def _peakweek_call() -> str:
            """Blocking Anthropic call — run via executor to avoid blocking the event loop."""
            resp = get_anthropic_client().messages.create(
                model=ANALYSIS_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text.strip()

        loop = asyncio.get_running_loop()
        protocol = await loop.run_in_executor(None, _peakweek_call)
        # Send in chunks to avoid Telegram 4096-char limit
        for i in range(0, len(protocol), 4000):
            if i == 0:
                await msg.edit_text(
                    f"🏆 *Peak Week Protocol*\n\n{protocol[i:i+4000]}",
                    parse_mode="Markdown",
                )
            else:
                await update.effective_chat.send_message(protocol[i:i+4000], parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Peak week generation failed: {e}")


async def cmd_freeze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Protect today's streak with a freeze (once per 30 days)."""
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    today = _today()

    # Already checked in today — freeze not needed
    if any(c.get("date") == today for c in user.get("checkins", [])):
        await update.message.reply_text(
            "✅ You've already checked in today — your streak is safe! 🔥"
        )
        return

    # Already frozen today
    freezes: list[str] = user.get("streak_freezes", [])
    if today in freezes:
        await update.message.reply_text("❄️ You already activated a streak freeze today.")
        return

    # Check if a freeze was used in the last 30 days
    cutoff_30d = str(_date.today() - timedelta(days=30))
    recent = [f for f in freezes if f >= cutoff_30d]
    if recent:
        last_used = max(recent)
        days_ago = (_date.today() - _date.fromisoformat(last_used)).days
        next_avail = (_date.fromisoformat(last_used) + timedelta(days=30)).isoformat()
        await update.message.reply_text(
            f"❄️ Streak freeze already used {days_ago} day(s) ago.\n\n"
            f"Next freeze available: *{next_avail}*",
            parse_mode="Markdown",
        )
        return

    freezes.append(today)
    user["streak_freezes"] = freezes
    _save_store()

    await update.message.reply_text(
        "❄️ *Streak Freeze activated!*\n\n"
        "Today counts toward your streak even without a check-in.\n\n"
        "_You get 1 freeze every 30 days._",
        parse_mode="Markdown",
    )


# ── Messaging helpers ─────────────────────────────────────────────────────────

_TG_MAX = 4096


async def _send_long(send_fn, text: str, **kwargs) -> None:
    """Send text respecting Telegram's 4096-char limit by chunking at newlines."""
    if len(text) <= _TG_MAX:
        await send_fn(text, **kwargs)
        return
    chunks = []
    while text:
        if len(text) <= _TG_MAX:
            chunks.append(text)
            break
        split = text.rfind("\n", 0, _TG_MAX)
        if split == -1:
            split = _TG_MAX
        chunks.append(text[:split])
        text = text[split:].lstrip("\n")
    for chunk in chunks:
        await send_fn(chunk, **kwargs)


# ── Per-muscle helpers ────────────────────────────────────────────────────────

def _volume_by_muscle(set_logs: list[dict], days: int = 30) -> dict[str, int]:
    """Count sets per muscle group over the last N days using _MUSCLE_MAP."""
    cutoff = str(_date.today() - timedelta(days=days))
    vol: dict[str, int] = {}
    for s in set_logs:
        if s.get("date", "") < cutoff:
            continue
        name = (s.get("exercise_name") or "").lower()
        for muscle, keywords in _MUSCLE_MAP.items():
            if any(k in name for k in keywords):
                vol[muscle] = vol.get(muscle, 0) + 1
                break
    return vol


def _muscle_strength_trend(set_logs: list[dict], weeks: int = 8) -> dict[str, dict]:
    """Return per-muscle best estimated-1RM split into early vs late half of window."""
    today = _date.today()
    start = str(today - timedelta(weeks=weeks))
    mid = str(today - timedelta(weeks=weeks // 2))
    trend: dict[str, dict] = {}
    for s in set_logs:
        d = s.get("date", "")
        if d < start:
            continue
        name = s.get("exercise_name") or ""
        rm = float(s.get("estimated_1rm") or 0)
        if rm <= 0:
            continue
        for muscle, keywords in _MUSCLE_MAP.items():
            if any(k in name.lower() for k in keywords):
                bucket = "early" if d < mid else "late"
                entry = trend.setdefault(muscle, {"early": (0.0, ""), "late": (0.0, "")})
                if rm > entry[bucket][0]:
                    entry[bucket] = (rm, name)
                break
    return trend


def _muscle_measurement_trend(measurements: list[dict], weeks: int = 8) -> dict[str, dict]:
    """Return measurement delta per muscle group over the last N weeks."""
    cutoff = str(_date.today() - timedelta(weeks=weeks))
    relevant = sorted(
        [m for m in measurements if m.get("date", "") >= cutoff],
        key=lambda m: m.get("date", ""),
    )
    result: dict[str, dict] = {}
    for muscle, fields in _MUSCLE_MEASURE.items():
        sides: dict[str, tuple[float, float]] = {}
        for field in fields:
            vals = [(m["date"], m[field]) for m in relevant if m.get(field) is not None]
            if len(vals) >= 2:
                sides[field] = (vals[0][1], vals[-1][1])
        if not sides:
            continue
        all_first = [v[0] for v in sides.values()]
        all_last = [v[1] for v in sides.values()]
        first_avg = sum(all_first) / len(all_first)
        last_avg = sum(all_last) / len(all_last)
        result[muscle] = {
            "first": round(first_avg, 1),
            "last": round(last_avg, 1),
            "delta": round(last_avg - first_avg, 1),
            "sides": sides,
        }
    return result


def _build_muscle_progress_text(user: dict) -> str:
    """Build the per-muscle progress message from set_logs, prs, and measurements."""
    set_logs = user.get("set_logs") or []
    measurements = user.get("measurements") or []
    u = user  # for unit helper

    parts: list[str] = ["💪 *Per-Muscle Progress (last 8 weeks)*\n"]
    has_data = False

    # ── Tape measurements ─────────────────────────────────────────────────────
    meas_trend = _muscle_measurement_trend(measurements, weeks=8)
    if meas_trend:
        has_data = True
        parts.append("*📐 Size (tape measurements)*")
        for muscle, info in meas_trend.items():
            delta = info["delta"]
            arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "→")
            if len(info["sides"]) == 1:
                # Single field (chest)
                field = list(info["sides"].keys())[0]
                f, l = info["sides"][field]
                parts.append(f"  {muscle.capitalize()}: {f}cm → {l}cm ({arrow} {abs(delta):.1f}cm)")
            else:
                # Paired (arms, legs) — show both sides
                side_lines = []
                for field, (f, l) in info["sides"].items():
                    side_label = "L" if "left" in field else "R"
                    side_lines.append(f"{side_label}: {f}→{l}cm")
                d_str = f"({arrow} {abs(delta):.1f}cm avg)"
                parts.append(f"  {muscle.capitalize()}: {', '.join(side_lines)}  {d_str}")
        parts.append("")

    # ── Strength trend ─────────────────────────────────────────────────────────
    str_trend = _muscle_strength_trend(set_logs, weeks=8)
    if str_trend:
        has_data = True
        parts.append("*🏋️ Strength trend (best est. 1RM)*")
        for muscle, info in str_trend.items():
            early_rm, early_ex = info["early"]
            late_rm, late_ex = info["late"]
            ex_name = late_ex or early_ex
            if early_rm > 0 and late_rm > 0:
                pct = round((late_rm - early_rm) / early_rm * 100, 1)
                arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "→")
                parts.append(
                    f"  {muscle.capitalize()} ({esc(ex_name)}): "
                    f"{_wfmt(early_rm, u)} → {_wfmt(late_rm, u)}  {arrow} {abs(pct):.1f}%"
                )
            elif late_rm > 0:
                parts.append(f"  {muscle.capitalize()} ({esc(ex_name)}): {_wfmt(late_rm, u)} 1RM (new)")
            elif early_rm > 0:
                parts.append(f"  {muscle.capitalize()} ({esc(ex_name)}): {_wfmt(early_rm, u)} 1RM (no recent sets)")
        parts.append("")

    # ── Volume balance ─────────────────────────────────────────────────────────
    vol = _volume_by_muscle(set_logs, days=56)  # 8 weeks
    if vol:
        has_data = True
        parts.append("*📊 Training volume (sets last 8 weeks)*")
        max_sets = max(vol.values())
        for muscle, sets in sorted(vol.items(), key=lambda x: x[1], reverse=True):
            bar = "█" * round(sets / max_sets * 10) + "░" * (10 - round(sets / max_sets * 10))
            parts.append(f"  {muscle.capitalize():10s} {bar}  {sets}s")
        parts.append("")

    if not has_data:
        return (
            "💪 *Per-Muscle Progress*\n\n"
            "No data yet. Start logging:\n"
            "• Sets: `/logset` or `/workout start`\n"
            "• Measurements: `/measurements arm=35cm chest=95cm`\n\n"
            "Once you have a few weeks of data, this view shows size, strength, and volume trends per muscle group."
        )

    # Tip: which muscles have no data
    all_muscles = set(_MUSCLE_MAP.keys())
    tracked = set(meas_trend.keys()) | set(str_trend.keys()) | set(vol.keys())
    untracked = all_muscles - tracked
    if untracked:
        parts.append(
            f"_No data for: {', '.join(sorted(untracked))} — "
            f"log sets or measurements to track them._"
        )

    return "\n".join(parts)


# ── Formatters ────────────────────────────────────────────────────────────────

def _format_analysis(a: dict) -> str:
    _SEP = "\n——————————————————\n"
    angle = a.get("photo_angle", "")
    _angle_emoji = {
        "front": "🔵", "back": "🔴",
        "side_left": "🟡", "side_right": "🟡", "three_quarter": "🟢",
    }
    angle_emoji = _angle_emoji.get(angle, "⚪")
    angle_line = f"{angle_emoji} *{angle.replace('_', ' ').title()} view*\n" if angle else ""

    muscle = a.get("muscle_development", {})
    muscle_blocks = []
    for k, v in muscle.items():
        if not v.get("notes"):
            continue
        block = f"*{esc(k.capitalize())}*\n  {esc(v.get('notes', ''))}"
        if v.get("action"):
            block += f"\n  → _{esc(v['action'])}_"
        muscle_blocks.append(block)
    muscle_section = ("\n\n".join(muscle_blocks)) if muscle_blocks else "_No muscle data_"

    strengths = "\n".join(f"• {esc(s)}" for s in a.get("strengths", []))
    priorities = "\n".join(f"• {esc(s)}" for s in a.get("priority_improvements", []))

    parts = [
        f"📊 *Physique Analysis*\n{angle_line}\n"
        f"Body Fat: *{esc(str(a.get('body_fat_estimate', '?')))}*  ·  "
        f"Confidence: {esc(str(a.get('body_fat_confidence', '?')))}",

        f"💪 *Muscle Assessment*\n\n{muscle_section}",

        f"✅ *Strengths*\n{strengths}" if strengths else None,

        f"🎯 *Top Priorities*\n{priorities}" if priorities else None,
    ]

    body = _SEP.join(p for p in parts if p)

    footer_lines = []
    if a.get("symmetry_notes"):
        footer_lines.append(f"📐 _{esc(a.get('symmetry_notes', ''))}_")
    if a.get("coach_message"):
        footer_lines.append(f"💬 _{esc(a.get('coach_message', ''))}_")
    footer_lines.append(
        "_⚠️ AI estimate only — not a medical assessment. Use /progress for objective tracking._"
    )
    footer = "\n\n".join(footer_lines)

    return f"{body}{_SEP}{footer}"


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
        warmup = day.get("warmup", "")
        if not exercises:
            days_text += f"\n*{day['day']} — {day.get('focus', '')}*\n    _(No exercises — type `/plan new` to regenerate)_\n"
            continue
        warmup_line = f"\n    🔥 _Warm-up: {warmup}_" if warmup else ""
        ex_lines = "\n".join(
            f"    • {e['name']}: {e['sets']}×{e['reps']} — rest {e.get('rest', '')} | {e.get('notes', '')}"
            for e in exercises
        )
        days_text += f"\n*{day['day']} — {day.get('focus', '')}*{warmup_line}\n{ex_lines}\n"

    zone2 = workout.get("zone2_cardio", "")
    zone2_line = f"\n🫀 *Zone 2 Cardio:* _{zone2}_" if zone2 else ""

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
        f"🏋️ *Workout — {esc(workout.get('split', ''))}*\n"
        f"{days_text}\n"
        f"📈 *Progression:* {esc(workout.get('progression', ''))}\n"
        f"🔄 *Deload:* {esc(workout.get('deload', ''))}"
        f"{zone2_line}",
        parse_mode="Markdown",
    )

    # ── Diet ──
    meals = "\n".join(f"  • {m}" for m in diet.get("sample_meals", []))
    kcal = diet.get("calories") or 0
    try:
        kcal = int(str(kcal).replace(",", ""))
    except (ValueError, TypeError):
        kcal = 0
    gender = ""
    if update.effective_user:
        pass  # gender comes from profile, not Telegram
    _profile = None
    # Attempt to get profile for gender-aware threshold
    try:
        _cid = update.effective_chat.id
        _profile = get_user(_cid).get("profile", {})
    except Exception:
        pass
    _gender = (_profile or {}).get("gender", "").lower() if _profile else ""
    _threshold = 1200 if "female" in _gender or "woman" in _gender else 1500
    _ed_warning = ""
    if 0 < kcal < _threshold:
        _ed_warning = (
            f"\n\n⚠️ *Calorie target note:* {kcal} kcal/day is below the general minimum "
            f"({_threshold} kcal for your profile). Very low intakes risk muscle loss, nutrient "
            f"deficiencies and metabolic adaptation. If you're experiencing restrictive eating "
            f"patterns, please speak with a registered dietitian or healthcare provider."
        )
    training_macros = diet.get("training_day_macros", "")
    rest_macros = diet.get("rest_day_macros", "")
    macro_variants = ""
    if training_macros and rest_macros:
        macro_variants = f"\n🏋️ _Training days:_ {training_macros}\n😴 _Rest days:_ {rest_macros}"

    await send(
        f"🥗 *Diet Plan*\n\n"
        f"Calories: *{diet.get('calories', '?')} kcal* (avg)\n"
        f"Protein: *{diet.get('protein_g', '?')}g* | "
        f"Carbs: *{diet.get('carbs_g', '?')}g* | "
        f"Fat: *{diet.get('fat_g', '?')}g*"
        f"{macro_variants}\n\n"
        f"_{esc(diet.get('rationale', ''))}_\n\n"
        f"*Meal Timing:*\n{esc(diet.get('meal_timing', ''))}\n\n"
        f"*Sample Day:*\n{meals}\n\n"
        f"*Prioritize:* {', '.join(esc(f) for f in diet.get('foods_to_prioritize', []))}"
        f"{_ed_warning}",
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
        f"🎯 *Top Priority:* {esc(coaching.get('top_priority', ''))}\n\n"
        f"😴 *Sleep:* {esc(coaching.get('sleep', ''))}\n\n"
        f"🧘 *Stress:* {esc(coaching.get('stress', ''))}\n\n"
        f"📊 *Tracking:* {esc(coaching.get('tracking', ''))}\n\n"
        f"📅 *12-Week Outlook:* {esc(coaching.get('expectations', ''))}\n\n"
        f"_{esc(coaching.get('coach_message', ''))}_\n\n"
        f"_⚠️ AI-generated plan — adjust based on how your body responds. "
        f"If anything feels wrong, trust your body and consult a coach or physio._",
        parse_mode="Markdown",
    )

    # ── Day picker + navigation footer (trailing, after all plan sections) ──
    if day_buttons:
        await send(
            "🏋️ *Ready to train? Choose your session:*"
            + _next_steps(
                ("/checkin", "log recovery first"),
                ("/plan new", "regenerate"),
                ("/progress", "see trends"),
            ),
            parse_mode="Markdown",
            reply_markup=day_keyboard,
        )
    else:
        await send(
            _next_steps(
                ("/checkin", "log recovery first"),
                ("/plan new", "regenerate"),
                ("/progress", "see trends"),
            ).strip(),
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
                w1 = _wfmt(recent[0][1], u)
                w2 = _wfmt(recent[-1][1], u)
                await _app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"📊 *Weekly Check-In*\n\n"
                        f"Your weight has been stable for 2 weeks ({w1} → {w2}).\n\n"
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

    import crypto_utils as _cu
    if not _cu.encryption_available():
        logger.warning(
            "ENCRYPTION_KEY is not set or invalid — Garmin and MFP integrations are "
            "disabled. Add ENCRYPTION_KEY to your environment variables to enable them."
        )

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
    app.add_handler(CommandHandler("fridge", cmd_fridge))
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
    app.add_handler(CommandHandler("privacy", cmd_privacy))
    app.add_handler(CommandHandler("delete_my_data", cmd_delete_my_data))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("freeze", cmd_freeze))
    app.add_handler(CommandHandler("peakweek", cmd_peakweek))
    app.add_handler(CallbackQueryHandler(handle_progress_callback, pattern=r"^progress:"))
    app.add_handler(CallbackQueryHandler(handle_onboard_callback, pattern=r"^onboard:"))
    app.add_handler(CallbackQueryHandler(handle_workout_callback, pattern=r"^wk:"))
    app.add_handler(CallbackQueryHandler(handle_plan_days_callback, pattern=r"^plan:days:"))
    app.add_handler(CallbackQueryHandler(handle_checkin_callback, pattern=r"^ci:"))
    app.add_handler(CallbackQueryHandler(handle_fridge_callback, pattern=r"^fridge:"))
    app.add_handler(CallbackQueryHandler(handle_delete_callback, pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(handle_profile_callback, pattern=r"^prof:"))
    app.add_handler(CallbackQueryHandler(handle_goals_callback, pattern=r"^goals:"))
    app.add_handler(CallbackQueryHandler(handle_measurements_callback, pattern=r"^meas:"))
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
            BotCommand("fridge",       "Scan fridge photo → macro-matched recipes"),
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
            BotCommand("billing",         "Billing & partnership info"),
            BotCommand("link",            "Link Telegram to the web app"),
            BotCommand("link_status",     "Check web-app link status"),
            BotCommand("privacy",         "View privacy & data storage info"),
            BotCommand("delete_my_data",  "Permanently delete all your data"),
            BotCommand("export",          "Download your full data as JSON"),
            BotCommand("freeze",          "Protect today's streak (1 per 30 days)"),
            BotCommand("peakweek",        "Contest peak week protocol (prep/cut only)"),
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
