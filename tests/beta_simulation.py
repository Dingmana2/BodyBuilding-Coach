"""
Open-beta simulation for the BodyBuilding Coach bot.

Self-contained: inlines the pure-logic functions under test so no Telegram
library import is needed. Tests unit consistency, analysis formatting, Markdown
escaping, plan JSON shape, and button placement via source inspection.

Run with:
    python tests/beta_simulation.py
"""

import ast
import re
import sys
from dataclasses import dataclass, field
from typing import Any


# ── Inline: esc() ─────────────────────────────────────────────────────────────
# Mirrors telegram_bot.py esc()
def _esc(s: str) -> str:
    """Escape Telegram legacy Markdown special characters."""
    for ch in ("_", "*", "`", "["):
        s = s.replace(ch, f"\\{ch}")
    return s


# ── Inline: _format_analysis() ────────────────────────────────────────────────
# Mirrors the rewritten _format_analysis from telegram_bot.py
def _format_analysis(a: dict) -> str:
    """Format physique analysis for Telegram display."""
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
        block = f"*{k.capitalize()}*\n  {_esc(v.get('notes', ''))}"
        if v.get("action"):
            block += f"\n  → _{_esc(v['action'])}_"
        muscle_blocks.append(block)
    muscle_section = ("\n\n".join(muscle_blocks)) if muscle_blocks else "_No muscle data_"

    strengths = "\n".join(f"• {_esc(s)}" for s in a.get("strengths", []))
    priorities = "\n".join(f"• {_esc(s)}" for s in a.get("priority_improvements", []))

    parts = [
        f"📊 *Physique Analysis*\n{angle_line}\n"
        f"Body Fat: *{a.get('body_fat_estimate', '?')}*  ·  "
        f"Confidence: {a.get('body_fat_confidence', '?')}",

        f"💪 *Muscle Assessment*\n\n{muscle_section}",

        f"✅ *Strengths*\n{strengths}" if strengths else None,

        f"🎯 *Top Priorities*\n{priorities}" if priorities else None,
    ]

    body = _SEP.join(p for p in parts if p)

    footer_lines = []
    if a.get("symmetry_notes"):
        footer_lines.append(f"📐 _{_esc(a.get('symmetry_notes', ''))}_")
    if a.get("coach_message"):
        footer_lines.append(f"💬 _{_esc(a.get('coach_message', ''))}_")
    footer_lines.append(
        "_⚠️ AI estimate only — not a medical assessment. Use /progress for objective tracking._"
    )
    footer = "\n\n".join(footer_lines)
    return f"{body}{_SEP}{footer}"


# ── Inline: _build_plan_prompt unit injection ──────────────────────────────────
# Extracts only the unit-conversion logic from _build_plan_prompt to validate
# that display values and instructions are unit-aware.
def _extract_unit_display(profile: dict, user_units: str) -> tuple[str, str, str, str]:
    """Mirrors the unit conversion block in _build_plan_prompt."""
    if user_units == "lbs" and profile:
        try:
            _w_lbs = round(float(profile.get("weight", 0) or 0) * 2.20462, 1)
            w_display = f"{_w_lbs}lbs"
        except (ValueError, TypeError):
            w_display = "?lbs"
        try:
            _h_in = round(float(profile.get("height", 0) or 0) / 2.54)
            h_display = f"{_h_in // 12}'{_h_in % 12}\""
        except (ValueError, TypeError):
            h_display = "?"
    else:
        w_display = f"{profile.get('weight', '?')}kg" if profile else "?"
        h_display = f"{profile.get('height', '?')}cm" if profile else "?"

    unit_label = "lbs" if user_units == "lbs" else "kg"
    height_unit = "feet and inches" if user_units == "lbs" else "cm"
    return w_display, h_display, unit_label, height_unit


# ── Source-text inspection helpers ────────────────────────────────────────────

def _read_bot_source() -> str:
    import os
    bot_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telegram_bot.py")
    with open(bot_path, encoding="utf-8") as f:
        return f.read()


# ── Archetype definition ──────────────────────────────────────────────────────

@dataclass
class Archetype:
    name: str
    profile: dict[str, Any]
    units: str = "kg"
    has_injury: bool = False


def _arch(
    name: str,
    gender: str,
    age: int,
    level: str,
    goal: str,
    units: str,
    weight_kg: float,
    height_cm: float,
    injured: bool = False,
) -> Archetype:
    return Archetype(
        name=name,
        profile={
            "gender": gender,
            "age": str(age),
            "experience_level": level,
            "goal": goal,
            "weight": str(weight_kg),
            "height": str(height_cm),
            "training_days": "4",
            "injuries": "none" if not injured else "left knee tendinopathy",
        },
        units=units,
        has_injury=injured,
    )


ARCHETYPES: list[Archetype] = [
    # ── Beginner ──────────────────────────────────────────────────────────────
    _arch("Beginner Male Bulk kg", "male", 22, "beginner", "muscle_gain", "kg", 70, 175),
    _arch("Beginner Male Bulk lbs", "male", 22, "beginner", "muscle_gain", "lbs", 70, 175),
    _arch("Beginner Female Cut kg", "female", 24, "beginner", "fat_loss", "kg", 65, 165),
    _arch("Beginner Female Cut lbs", "female", 24, "beginner", "fat_loss", "lbs", 65, 165),
    _arch("Beginner Male Recomp kg", "male", 19, "beginner", "recomposition", "kg", 80, 180),
    _arch("Beginner Male Recomp lbs", "male", 19, "beginner", "recomposition", "lbs", 80, 180),
    _arch("Beginner Female Strength kg", "female", 28, "beginner", "strength", "kg", 60, 160),
    _arch("Beginner Female Strength lbs", "female", 28, "beginner", "strength", "lbs", 60, 160),
    _arch("Beginner Male Injured kg", "male", 30, "beginner", "muscle_gain", "kg", 75, 178, injured=True),
    _arch("Beginner Female Injured lbs", "female", 26, "beginner", "fat_loss", "lbs", 68, 162, injured=True),

    # ── Intermediate ──────────────────────────────────────────────────────────
    _arch("Intermediate Male Bulk kg", "male", 25, "intermediate", "muscle_gain", "kg", 82, 182),
    _arch("Intermediate Male Bulk lbs", "male", 25, "intermediate", "muscle_gain", "lbs", 82, 182),
    _arch("Intermediate Female Cut kg", "female", 27, "intermediate", "fat_loss", "kg", 62, 167),
    _arch("Intermediate Female Cut lbs", "female", 27, "intermediate", "fat_loss", "lbs", 62, 167),
    _arch("Intermediate Male Recomp kg", "male", 32, "intermediate", "recomposition", "kg", 88, 183),
    _arch("Intermediate Male Recomp lbs", "male", 32, "intermediate", "recomposition", "lbs", 88, 183),
    _arch("Intermediate Female Strength kg", "female", 30, "intermediate", "strength", "kg", 58, 163),
    _arch("Intermediate Female Strength lbs", "female", 30, "intermediate", "strength", "lbs", 58, 163),
    _arch("Intermediate Male Injured kg", "male", 28, "intermediate", "muscle_gain", "kg", 85, 180, injured=True),
    _arch("Intermediate Female Injured lbs", "female", 29, "intermediate", "fat_loss", "lbs", 63, 165, injured=True),
    _arch("Intermediate Male 5day kg", "male", 26, "intermediate", "muscle_gain", "kg", 79, 177),
    _arch("Intermediate Female 5day lbs", "female", 31, "intermediate", "muscle_gain", "lbs", 61, 168),

    # ── Advanced ──────────────────────────────────────────────────────────────
    _arch("Advanced Male Bulk kg", "male", 28, "advanced", "muscle_gain", "kg", 95, 185),
    _arch("Advanced Male Bulk lbs", "male", 28, "advanced", "muscle_gain", "lbs", 95, 185),
    _arch("Advanced Female Cut kg", "female", 26, "advanced", "fat_loss", "kg", 57, 165),
    _arch("Advanced Female Cut lbs", "female", 26, "advanced", "fat_loss", "lbs", 57, 165),
    _arch("Advanced Male Recomp kg", "male", 35, "advanced", "recomposition", "kg", 100, 187),
    _arch("Advanced Male Recomp lbs", "male", 35, "advanced", "recomposition", "lbs", 100, 187),
    _arch("Advanced Female Strength kg", "female", 29, "advanced", "strength", "kg", 67, 170),
    _arch("Advanced Female Strength lbs", "female", 29, "advanced", "strength", "lbs", 67, 170),
    _arch("Advanced Male Injured kg", "male", 33, "advanced", "muscle_gain", "kg", 98, 186, injured=True),
    _arch("Advanced Female Injured lbs", "female", 32, "advanced", "strength", "lbs", 66, 169, injured=True),
    _arch("Advanced Male Competition kg", "male", 27, "advanced", "fat_loss", "kg", 90, 183),
    _arch("Advanced Female Competition lbs", "female", 25, "advanced", "fat_loss", "lbs", 59, 163),

    # ── Older athletes ────────────────────────────────────────────────────────
    _arch("Senior Male Bulk kg 50", "male", 50, "intermediate", "muscle_gain", "kg", 85, 178),
    _arch("Senior Male Bulk lbs 50", "male", 50, "intermediate", "muscle_gain", "lbs", 85, 178),
    _arch("Senior Female Cut kg 45", "female", 45, "intermediate", "fat_loss", "kg", 70, 162),
    _arch("Senior Female Cut lbs 45", "female", 45, "intermediate", "fat_loss", "lbs", 70, 162),
    _arch("Senior Male Strength kg 55", "male", 55, "beginner", "strength", "kg", 90, 175),
    _arch("Senior Female Recomp lbs 48", "female", 48, "beginner", "recomposition", "lbs", 72, 164),
    _arch("Senior Male Injured kg 60", "male", 60, "beginner", "muscle_gain", "kg", 82, 172, injured=True),
    _arch("Senior Female Injured lbs 52", "female", 52, "intermediate", "fat_loss", "lbs", 68, 160, injured=True),

    # ── Young athletes ────────────────────────────────────────────────────────
    _arch("Young Male Bulk kg 18", "male", 18, "beginner", "muscle_gain", "kg", 65, 175),
    _arch("Young Male Bulk lbs 18", "male", 18, "beginner", "muscle_gain", "lbs", 65, 175),
    _arch("Young Female Strength kg 20", "female", 20, "beginner", "strength", "kg", 55, 160),
    _arch("Young Female Strength lbs 20", "female", 20, "beginner", "strength", "lbs", 55, 160),

    # ── High body weight ──────────────────────────────────────────────────────
    _arch("Overweight Male Cut kg", "male", 35, "beginner", "fat_loss", "kg", 130, 180),
    _arch("Overweight Male Cut lbs", "male", 35, "beginner", "fat_loss", "lbs", 130, 180),
    _arch("Overweight Female Recomp kg", "female", 38, "beginner", "recomposition", "kg", 100, 165),
    _arch("Overweight Female Recomp lbs", "female", 38, "beginner", "recomposition", "lbs", 100, 165),

    # ── Low body weight ───────────────────────────────────────────────────────
    _arch("Underweight Male Bulk kg", "male", 21, "beginner", "muscle_gain", "kg", 55, 178),
    _arch("Underweight Male Bulk lbs", "male", 21, "beginner", "muscle_gain", "lbs", 55, 178),
    _arch("Underweight Female Bulk kg", "female", 22, "beginner", "muscle_gain", "kg", 44, 162),

    # ── Edge cases ────────────────────────────────────────────────────────────
    _arch("Missing height", "male", 25, "intermediate", "muscle_gain", "kg", 80, 0),
    _arch("Missing weight", "male", 25, "intermediate", "muscle_gain", "lbs", 0, 178),
    _arch("Non-binary Recomp kg", "non-binary", 27, "intermediate", "recomposition", "kg", 70, 170),
    _arch("Non-binary Bulk lbs", "non-binary", 24, "beginner", "muscle_gain", "lbs", 68, 173),
    _arch("Very tall Male kg 200cm", "male", 30, "advanced", "muscle_gain", "kg", 105, 200),
    _arch("Very short Female lbs 150cm", "female", 28, "intermediate", "fat_loss", "lbs", 52, 150),
    _arch("5day Advanced Male lbs", "male", 29, "advanced", "muscle_gain", "lbs", 92, 183),
    _arch("6day Advanced Female kg", "female", 27, "advanced", "strength", "kg", 65, 168),
]


# ── Test: analysis format ─────────────────────────────────────────────────────

SAMPLE_ANALYSIS: dict[str, Any] = {
    "photo_angle": "front",
    "body_fat_estimate": "15-18%",
    "body_fat_confidence": "medium",
    "muscle_development": {
        "chest": {"notes": "Good upper chest, lower needs work", "action": "Add incline press 3×10"},
        "back": {"notes": "Width decent, thickness needs work", "action": "Add barbell rows"},
        "shoulders": {"notes": "Rounded, well developed", "action": ""},
        "legs": {"notes": "Underdeveloped vs upper body", "action": "Add 2 dedicated leg days/week"},
        "arms": {"notes": "Bicep peak good, tricep size lacking", "action": "Add overhead extensions"},
    },
    "strengths": ["Strong shoulder-to-waist ratio", "Good muscle belly fullness"],
    "priority_improvements": ["Leg frequency", "Reduce calories 200-300 kcal"],
    "symmetry_notes": "Left shoulder slightly higher. Overall symmetry good.",
    "coach_message": "Solid foundation, focus on weak points.",
}


def _test_analysis_format() -> list[str]:
    errors: list[str] = []
    try:
        result = _format_analysis(SAMPLE_ANALYSIS)
    except Exception as e:
        return [f"_format_analysis raised: {e}"]

    checks = {
        "section divider present": "——" in result,
        "bold muscle header Chest": "*Chest*" in result,
        "bold muscle header Back": "*Back*" in result,
        "action arrow present": "→" in result,
        "strengths bullet": "•" in result,
        "priorities bullet": "•" in result,
        "body fat present": "15-18%" in result,
        "AI disclaimer present": "AI estimate only" in result,
        "coach message present": "Solid foundation" in result,
        "symmetry present": "symmetry" in result.lower(),
        "no /10 score leaking": "/10" not in result,
        "no 'score' field leaking": "score" not in result.lower(),
    }
    for label, ok in checks.items():
        if not ok:
            errors.append(f"Analysis format: {label}")
    return errors


def _test_analysis_empty_fields() -> list[str]:
    """Analysis with empty optional fields should not raise."""
    errors: list[str] = []
    try:
        result = _format_analysis({
            "photo_angle": "back",
            "body_fat_estimate": "?",
            "body_fat_confidence": "low",
            "muscle_development": {},
            "strengths": [],
            "priority_improvements": [],
            "symmetry_notes": "",
            "coach_message": "",
        })
    except Exception as e:
        errors.append(f"Empty analysis raised: {e}")
        return errors
    if "AI estimate only" not in result:
        errors.append("Disclaimer missing from empty analysis")
    return errors


# ── Test: esc() function ──────────────────────────────────────────────────────

_DANGEROUS_STRINGS = [
    "user*s bold goal",
    "goal_with_underscores",
    "name [with brackets]",
    "text `with backticks`",
    "_multi_word_italic_",
    "nested *bold _and_ italic*",
]

_SPECIAL_CHARS = {"*", "_", "`", "["}


def _test_esc_function() -> list[str]:
    errors: list[str] = []
    for s in _DANGEROUS_STRINGS:
        escaped = _esc(s)
        for ch in _SPECIAL_CHARS:
            pos = 0
            while True:
                idx = escaped.find(ch, pos)
                if idx == -1:
                    break
                if idx == 0 or escaped[idx - 1] != "\\":
                    errors.append(f"esc() left unescaped '{ch}' in: {repr(escaped)}")
                pos = idx + 1
    return errors


# ── Test: unit display values ─────────────────────────────────────────────────

def _test_unit_display_per_archetype(arch: Archetype) -> list[str]:
    errors: list[str] = []
    try:
        w_display, h_display, unit_label, height_unit = _extract_unit_display(arch.profile, arch.units)
    except Exception as e:
        return [f"_extract_unit_display raised: {e}"]

    if arch.units == "lbs":
        if not w_display.endswith("lbs"):
            errors.append(f"lbs user: w_display='{w_display}' should end with 'lbs'")
        if unit_label != "lbs":
            errors.append(f"lbs user: unit_label='{unit_label}' should be 'lbs'")
        if "feet" not in height_unit:
            errors.append(f"lbs user: height_unit='{height_unit}' should mention 'feet'")
    else:
        if not w_display.endswith("kg"):
            errors.append(f"kg user: w_display='{w_display}' should end with 'kg'")
        if unit_label != "kg":
            errors.append(f"kg user: unit_label='{unit_label}' should be 'kg'")
        if height_unit != "cm":
            errors.append(f"kg user: height_unit='{height_unit}' should be 'cm'")

    # Values should be non-empty
    for name, val in (("w_display", w_display), ("h_display", h_display)):
        if not val:
            errors.append(f"{name} is empty for archetype {arch.name}")

    # lbs conversion sanity: 70kg ≈ 154lbs
    weight_kg = float(arch.profile.get("weight", 0) or 0)
    if arch.units == "lbs" and weight_kg > 0:
        lbs_val = float(w_display.replace("lbs", "").strip())
        expected = round(weight_kg * 2.20462, 1)
        if abs(lbs_val - expected) > 0.5:
            errors.append(
                f"lbs conversion off: {weight_kg}kg → '{w_display}' (expected ~{expected}lbs)"
            )

    return errors


# ── Test: plan JSON shape ─────────────────────────────────────────────────────

SAMPLE_PLAN: dict[str, Any] = {
    "workout": {
        "split": "Upper/Lower 4-day",
        "days": [
            {
                "day": "Monday",
                "focus": "Upper Push",
                "exercises": [
                    {"name": "Bench Press", "sets": 4, "reps": "6-8", "rest": "3 min", "notes": ""},
                ],
                "warmup": "5 min bike",
            },
            {
                "day": "Wednesday",
                "focus": "Lower",
                "exercises": [
                    {"name": "Squat", "sets": 4, "reps": "5", "rest": "3 min", "notes": ""},
                ],
            },
        ],
        "progression": "Add 2.5kg per session",
        "deload": "Week 4 drop volume 40%",
        "zone2_cardio": "2×30 min Zone 2 per week",
    },
    "diet": {
        "calories": 2800,
        "protein_g": 180,
        "carbs_g": 320,
        "fat_g": 80,
        "sample_meals": ["Oats + eggs", "Chicken + rice"],
        "rationale": "Slight surplus to drive hypertrophy",
        "meal_timing": "Pre-workout: carbs 2h prior",
        "foods_to_prioritize": ["Chicken", "Rice"],
    },
    "supplements": [
        {"priority": 1, "name": "Creatine", "grade": "A", "dose": "5g/day", "timing": "Post", "benefit": "Strength"},
    ],
    "coaching": {
        "top_priority": "Progressive overload",
        "sleep": "8h minimum",
        "stress": "Meditate",
        "tracking": "Log every session",
        "expectations": "4-6kg in 12 weeks",
        "coach_message": "Stay consistent.",
    },
}

_REQUIRED_PLAN = {
    "workout": ["split", "days", "progression", "deload"],
    "diet": ["calories", "protein_g", "carbs_g", "fat_g", "sample_meals"],
    "supplements": None,
    "coaching": ["top_priority", "sleep"],
}


def _test_plan_shape(plan: dict) -> list[str]:
    errors: list[str] = []
    for top_key, sub_keys in _REQUIRED_PLAN.items():
        if top_key not in plan:
            errors.append(f"Plan missing key: {top_key}")
            continue
        if sub_keys is None:
            if not isinstance(plan[top_key], list):
                errors.append(f"Plan['{top_key}'] should be a list")
            continue
        section = plan[top_key]
        if not isinstance(section, dict):
            errors.append(f"Plan['{top_key}'] should be a dict")
            continue
        for k in sub_keys:
            if k not in section:
                errors.append(f"Plan['{top_key}']['{k}'] missing")
    for day in plan.get("workout", {}).get("days", []):
        for k in ("day", "focus", "exercises"):
            if k not in day:
                errors.append(f"Workout day missing field: {k}")
    return errors


# ── Test: source-level structural checks ─────────────────────────────────────

def _test_source_button_placement() -> list[str]:
    """Verify _send_plan no longer attaches reply_markup to the Workout message."""
    errors: list[str] = []
    src = _read_bot_source()

    # Find _send_plan function boundaries
    start = src.find("async def _send_plan(")
    if start == -1:
        return ["_send_plan not found in source"]
    next_def = src.find("\nasync def ", start + 1)
    fn_src = src[start:next_def] if next_def != -1 else src[start:]

    # The Workout send block: between "🏋️ *Workout" and the next "await send("
    workout_block_start = fn_src.find("🏋️ *Workout")
    if workout_block_start == -1:
        errors.append("Workout section marker not found in _send_plan")
        return errors

    # Find the closing paren of the first send() call (for the workout section)
    # We look for the first standalone ")" after the workout send
    diet_marker = fn_src.find("# ── Diet", workout_block_start)
    workout_send_block = fn_src[workout_block_start:diet_marker] if diet_marker != -1 else fn_src[workout_block_start:]

    if "reply_markup=day_keyboard" in workout_send_block:
        errors.append(
            "reply_markup=day_keyboard still on the Workout send() call "
            "(should be on the trailing '🏋️ Ready to train?' message)"
        )

    if "Ready to train" not in fn_src:
        errors.append("Trailing 'Ready to train?' message not found in _send_plan")

    return errors


def _test_source_unit_param_propagation() -> list[str]:
    """Verify all run_in_executor calls for plan generation pass user_units."""
    errors: list[str] = []
    src = _read_bot_source()

    # Find all run_in_executor calls that use _generate_plan or _generate_plan_from_profile
    pattern = re.compile(
        r'run_in_executor\(\s*None\s*,\s*(_generate_plan(?:_from_profile)?)\s*,([^)]+)\)',
        re.DOTALL,
    )
    for m in pattern.finditer(src):
        fn_name = m.group(1)
        args = m.group(2)
        if "user.get" not in args and "user_units" not in args and "units" not in args:
            # Get line number for context
            line_no = src[:m.start()].count("\n") + 1
            errors.append(
                f"Line ~{line_no}: {fn_name}() call missing user_units argument"
            )

    return errors


def _test_source_asyncio_get_event_loop() -> list[str]:
    """Flag any remaining asyncio.get_event_loop() (deprecated) in async handlers."""
    errors: list[str] = []
    src = _read_bot_source()
    for i, line in enumerate(src.splitlines(), 1):
        if "asyncio.get_event_loop()" in line and "# noqa" not in line:
            errors.append(f"Line {i}: asyncio.get_event_loop() found (use get_running_loop)")
    return errors


def _test_source_py_compile() -> list[str]:
    """Verify all key source files compile cleanly."""
    import os
    import py_compile
    errors: list[str] = []
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for fname in ("telegram_bot.py", "prompt_builder.py", "claude_service.py", "crypto_utils.py"):
        path = os.path.join(root, fname)
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(f"{fname}: {e}")
    return errors


# ── Runner ────────────────────────────────────────────────────────────────────

@dataclass
class Result:
    name: str
    passed: bool
    errors: list[str] = field(default_factory=list)


def run_all() -> int:
    results: list[Result] = []

    def _add(name: str, errs: list[str]) -> None:
        results.append(Result(name, not errs, errs))

    # Static / structural checks
    _add("py_compile", _test_source_py_compile())
    _add("analysis_format_full", _test_analysis_format())
    _add("analysis_format_empty_fields", _test_analysis_empty_fields())
    _add("esc_function", _test_esc_function())
    _add("plan_shape_sample", _test_plan_shape(SAMPLE_PLAN))
    _add("workout_button_placement", _test_source_button_placement())
    _add("unit_param_propagation", _test_source_unit_param_propagation())
    _add("no_deprecated_get_event_loop", _test_source_asyncio_get_event_loop())

    # Per-archetype unit display checks (60+ archetypes)
    for arch in ARCHETYPES:
        errs = _test_unit_display_per_archetype(arch)
        _add(f"units:{arch.name}", errs)

    # ── Report ────────────────────────────────────────────────────────────────
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = [r for r in results if not r.passed]

    print(f"\n{'='*62}")
    print(f"Beta Simulation — {passed}/{total} passed")
    print(f"{'='*62}")

    if failed:
        print("\nFAILURES:")
        for r in failed:
            print(f"\n  FAIL  {r.name}")
            for e in r.errors:
                print(f"        {e}")
    else:
        print("\nAll checks passed.")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(run_all())
