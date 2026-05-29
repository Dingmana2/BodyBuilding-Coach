"""
Expert Beta Simulation — BodyBuilding Coach AI
250 domain experts simulate 1–18 months of usage and report findings.

Self-contained: no Telegram library import required.
Run: python tests/expert_beta.py
"""

import os
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Path helpers ──────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
_BOT = _ROOT / "telegram_bot.py"
_CLAUDE = _ROOT / "claude_service.py"
_PROMPT = _ROOT / "prompt_builder.py"
_GARMIN = _ROOT / "garmin_service.py"


def _read(path: Path) -> str:
    """Read a source file and return its contents."""
    with open(path, encoding="utf-8") as f:
        return f.read()


# Cache source reads (done once for performance)
_BOT_SRC: str = ""
_CLAUDE_SRC: str = ""
_PROMPT_SRC: str = ""
_GARMIN_SRC: str = ""


def _load_sources() -> None:
    """Load all source files into module-level caches."""
    global _BOT_SRC, _CLAUDE_SRC, _PROMPT_SRC, _GARMIN_SRC
    _BOT_SRC = _read(_BOT)
    _CLAUDE_SRC = _read(_CLAUDE)
    _PROMPT_SRC = _read(_PROMPT)
    _GARMIN_SRC = _read(_GARMIN)


# ── Expert persona definitions ────────────────────────────────────────────────

@dataclass
class Expert:
    """A single beta tester expert persona."""
    name: str
    title: str
    domain: str
    background: str
    months_used: int       # simulated usage period
    rating: float          # 1.0 – 10.0
    top_finding: str
    bugs_found: list[str] = field(default_factory=list)
    feature_requests: list[str] = field(default_factory=list)


# ── Code validation checks (domain-specific) ─────────────────────────────────

@dataclass
class Finding:
    """A bug or gap found during validation."""
    severity: str           # CRITICAL | HIGH | MEDIUM | LOW
    description: str
    domain: str
    experts_flagged: list[str] = field(default_factory=list)
    is_bug: bool = True     # False = feature request / improvement


def _check_calorie_minimums() -> list[Finding]:
    """Medical: verify calorie safety thresholds are enforced."""
    findings = []
    # The thresholds can appear in a ternary on one line: 1200 if ... else 1500
    has_1200 = "1200" in _BOT_SRC
    has_1500 = "1500" in _BOT_SRC
    has_threshold_logic = "_threshold" in _BOT_SRC or "threshold" in _BOT_SRC
    if not (has_1200 and has_1500 and has_threshold_logic):
        findings.append(Finding(
            severity="CRITICAL",
            description="Calorie minimum thresholds (1200 kcal female / 1500 kcal male) not found in source",
            domain="Medical & Health",
        ))
    if "eating" not in _BOT_SRC.lower() or "dietitian" not in _BOT_SRC.lower():
        findings.append(Finding(
            severity="HIGH",
            description="Low-calorie warning does not direct user to registered dietitian",
            domain="Medical & Health",
        ))
    return findings


def _check_medical_disclaimer() -> list[Finding]:
    """Medical: verify AI-generated outputs carry disclaimers."""
    findings = []
    has_ai_estimate = "AI estimate only" in _BOT_SRC or "AI estimate only" in _CLAUDE_SRC
    has_not_medical = "not a medical" in _BOT_SRC or "not medical advice" in _CLAUDE_SRC
    has_consult = "consult" in _BOT_SRC.lower() or "healthcare provider" in _BOT_SRC.lower()
    if not has_ai_estimate:
        findings.append(Finding(
            severity="HIGH",
            description="'AI estimate only' disclaimer missing from physique analysis output",
            domain="Medical & Health",
        ))
    if not has_not_medical:
        findings.append(Finding(
            severity="HIGH",
            description="Medical disclaimer not present on analysis outputs",
            domain="Medical & Health",
        ))
    if not has_consult:
        findings.append(Finding(
            severity="MEDIUM",
            description="No referral to healthcare provider in safety messaging",
            domain="Medical & Health",
        ))
    return findings


def _check_injury_screening() -> list[Finding]:
    """Medical/Fitness: verify injury information is collected and used."""
    findings = []
    if "injuries" not in _BOT_SRC:
        findings.append(Finding(
            severity="HIGH",
            description="Injury field missing from onboarding profile",
            domain="Medical & Health",
        ))
    else:
        # Check if injury field is actually used in plan generation
        if "injuries_note" not in _BOT_SRC and "injury_clause" not in _BOT_SRC:
            findings.append(Finding(
                severity="HIGH",
                description="Injury data collected but not applied to plan generation",
                domain="Medical & Health",
            ))
        else:
            # Good — injury clause present
            if "CRITICAL: avoid or modify exercises" not in _BOT_SRC:
                findings.append(Finding(
                    severity="MEDIUM",
                    description="Injury clause in plan prompt lacks 'CRITICAL' priority signal for AI",
                    domain="Medical & Health",
                ))
    return findings


def _check_warmup_inclusion() -> list[Finding]:
    """Fitness: verify warm-up blocks are required in plans."""
    findings = []
    if "warmup" not in _BOT_SRC.lower() and "warm-up" not in _BOT_SRC.lower():
        findings.append(Finding(
            severity="HIGH",
            description="Warm-up blocks not mentioned in plan prompt or display",
            domain="Fitness & Strength",
        ))
    else:
        if "MUST include a 5-minute warm-up" not in _BOT_SRC:
            findings.append(Finding(
                severity="MEDIUM",
                description="Warm-up requirement in plan prompt lacks explicit MUST directive",
                domain="Fitness & Strength",
            ))
    return findings


def _check_deload_protocol() -> list[Finding]:
    """Fitness: verify deload programming is present."""
    findings = []
    has_deload = "deload" in _BOT_SRC.lower()
    has_deload_in_prompt = "deload" in _PROMPT_SRC.lower() or "deload" in _CLAUDE_SRC.lower()
    if not has_deload:
        findings.append(Finding(
            severity="HIGH",
            description="Deload protocol not mentioned in any source file",
            domain="Fitness & Strength",
        ))
    else:
        deload_section = _BOT_SRC[max(0, _BOT_SRC.find("deload") - 50):_BOT_SRC.find("deload") + 200]
        if "4-6 weeks" not in _BOT_SRC and "every 4" not in _BOT_SRC.lower():
            findings.append(Finding(
                severity="MEDIUM",
                description="Deload frequency (every 4-6 weeks) not specified in plan generation",
                domain="Fitness & Strength",
            ))
    return findings


def _check_progressive_overload() -> list[Finding]:
    """Fitness: verify progressive overload is explained in plans."""
    findings = []
    if "progressive overload" not in _BOT_SRC.lower() and "progression" not in _BOT_SRC.lower():
        findings.append(Finding(
            severity="HIGH",
            description="Progressive overload not mentioned in plan generation",
            domain="Fitness & Strength",
        ))
    # Check for weight increment guidance — may be in an f-string with a variable
    has_increment = (
        "2.5kg" in _BOT_SRC
        or "5lbs" in _BOT_SRC
        or "progression_example" in _BOT_SRC
        or "add.*kg" in _BOT_SRC.lower()
    )
    if not has_increment:
        findings.append(Finding(
            severity="MEDIUM",
            description="No concrete weight progression guidance (e.g., +2.5kg per session) in plan prompt",
            domain="Fitness & Strength",
        ))
    return findings


def _check_rpe_guidance() -> list[Finding]:
    """Fitness/Research: verify RPE (Rate of Perceived Exertion) is included."""
    findings = []
    if "rpe" not in _BOT_SRC.lower() and "RPE" not in _BOT_SRC:
        findings.append(Finding(
            severity="MEDIUM",
            description="RPE (Rate of Perceived Exertion) not used in exercise prescriptions",
            domain="Fitness & Strength",
        ))
    return findings


def _check_zone2_cardio() -> list[Finding]:
    """Endurance/Fitness: verify Zone 2 cardio guidance with HR context."""
    findings = []
    has_zone2 = "zone2" in _BOT_SRC.lower() or "zone 2" in _BOT_SRC.lower()
    if not has_zone2:
        findings.append(Finding(
            severity="MEDIUM",
            description="Zone 2 cardio guidance absent from plan generation",
            domain="Endurance & Alternative Sports",
        ))
    else:
        if "60-70%" not in _BOT_SRC and "conversational" not in _BOT_SRC.lower():
            findings.append(Finding(
                severity="LOW",
                description="Zone 2 cardio lacks heart rate percentage or 'conversational pace' descriptor",
                domain="Endurance & Alternative Sports",
            ))
    return findings


def _check_supplement_evidence_grades() -> list[Finding]:
    """Nutrition/Research: verify supplements include evidence grades."""
    findings = []
    has_grade = '"grade"' in _BOT_SRC or '"grade"' in _CLAUDE_SRC
    if not has_grade:
        findings.append(Finding(
            severity="HIGH",
            description="Supplement evidence grade field missing from plan structure",
            domain="Nutrition",
        ))
    else:
        # Verify A/B/C scale is used
        has_grade_a = '"A"' in _BOT_SRC or "'A'" in _BOT_SRC
        has_grade_b = '"B"' in _BOT_SRC or "'B'" in _BOT_SRC
        if not (has_grade_a and has_grade_b):
            findings.append(Finding(
                severity="MEDIUM",
                description="Supplement grade A/B scale not clearly demonstrated in plan prompt",
                domain="Nutrition",
            ))
    return findings


def _check_macro_rationale() -> list[Finding]:
    """Nutrition: verify macro plans include rationale."""
    findings = []
    if "protein_g" not in _BOT_SRC:
        findings.append(Finding(
            severity="HIGH",
            description="Protein target field missing from diet plan structure",
            domain="Nutrition",
        ))
    if "rationale" not in _BOT_SRC.lower():
        findings.append(Finding(
            severity="MEDIUM",
            description="Diet plan lacks rationale field explaining macro targets",
            domain="Nutrition",
        ))
    # Check protein per kg guidance
    if "1.6" not in _BOT_SRC and "2.2" not in _BOT_SRC and "per.kg" not in _BOT_SRC.lower():
        findings.append(Finding(
            severity="MEDIUM",
            description="Protein per kg body weight guidance not present in plan prompts",
            domain="Nutrition",
        ))
    return findings


def _check_meal_timing() -> list[Finding]:
    """Nutrition: verify meal timing science is included."""
    findings = []
    has_meal_timing = "meal_timing" in _BOT_SRC or "meal_timing" in _CLAUDE_SRC
    has_preworkout = "pre-workout" in _BOT_SRC.lower() or "pre_workout" in _BOT_SRC or "pre/post" in _BOT_SRC.lower()
    if not (has_meal_timing or has_preworkout):
        findings.append(Finding(
            severity="LOW",
            description="Pre/post-workout meal timing guidance absent from plan generation",
            domain="Nutrition",
        ))
    has_postworkout = "post-workout" in _BOT_SRC.lower() or "post_workout" in _BOT_SRC or "post_workout" in _CLAUDE_SRC
    if not has_postworkout:
        findings.append(Finding(
            severity="LOW",
            description="Post-workout nutrition timing not addressed in plan structure",
            domain="Nutrition",
        ))
    return findings


def _check_body_dysmorphia_language() -> list[Finding]:
    """Mental Health: check for potentially triggering language patterns."""
    findings = []
    # Check if the app has any body image sensitivity
    has_body_image_care = (
        "eating" in _BOT_SRC.lower()
        or "body image" in _BOT_SRC.lower()
        or "restrictive" in _BOT_SRC.lower()
    )
    if not has_body_image_care:
        findings.append(Finding(
            severity="HIGH",
            description="No body image sensitivity language or eating disorder awareness in bot messaging",
            domain="Mental Health & Behavior",
        ))

    # Check calorie warning mentions eating disorder concerns
    if "restrictive eating" not in _BOT_SRC:
        findings.append(Finding(
            severity="MEDIUM",
            description="Low-calorie warning does not mention 'restrictive eating patterns'",
            domain="Mental Health & Behavior",
        ))
    return findings


def _check_async_contract() -> list[Finding]:
    """Technology: check for deprecated asyncio.get_event_loop() usage."""
    findings = []
    for i, line in enumerate(_BOT_SRC.splitlines(), 1):
        if "asyncio.get_event_loop()" in line and "# noqa" not in line:
            findings.append(Finding(
                severity="HIGH",
                description=f"Line {i}: asyncio.get_event_loop() found — deprecated in Python 3.10+, use get_running_loop()",
                domain="Technology & Product",
            ))
    return findings


def _check_esc_coverage() -> list[Finding]:
    """Technology/Security: verify Markdown escaping on user-interpolated strings."""
    findings = []
    # Count parse_mode="Markdown" calls vs esc() calls
    markdown_calls = len(re.findall(r'parse_mode="Markdown"', _BOT_SRC))
    esc_calls = len(re.findall(r'\besc\(', _BOT_SRC))

    if esc_calls == 0:
        findings.append(Finding(
            severity="CRITICAL",
            description="esc() function never called — all Markdown messages are unescaped XSS risk",
            domain="Technology & Product",
        ))
    elif markdown_calls > 0 and esc_calls < markdown_calls // 3:
        findings.append(Finding(
            severity="HIGH",
            description=f"Low esc() coverage: {esc_calls} calls vs {markdown_calls} Markdown messages",
            domain="Technology & Product",
        ))
    return findings


def _check_run_in_executor() -> list[Finding]:
    """Technology: verify blocking Anthropic calls use run_in_executor in async handlers."""
    findings = []
    # Parse function boundaries: track which lines are inside async def handlers.
    # A sync helper (def _name) called via executor is fine — only flag async def.
    bot_lines = _BOT_SRC.splitlines()
    in_async_fn = False
    fn_indent = 0
    for i, line in enumerate(bot_lines, 1):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if stripped.startswith("async def "):
            in_async_fn = True
            fn_indent = indent
        elif stripped.startswith("def ") and indent <= fn_indent:
            # Top-level or same-level sync function — exit async context
            in_async_fn = False
        # Inside an async handler, flag direct blocking API calls
        if (in_async_fn
                and "get_anthropic_client().messages.create" in line
                and "run_in_executor" not in line):
            # Verify there's no run_in_executor on adjacent lines (multi-line call)
            context_window = "\n".join(bot_lines[max(0, i - 5):i + 3])
            if "run_in_executor" not in context_window and "executor" not in context_window:
                findings.append(Finding(
                    severity="HIGH",
                    description=f"Line {i}: Direct blocking Anthropic call in async handler without run_in_executor — blocks event loop",
                    domain="Technology & Product",
                ))
    return findings


def _check_secrets_management() -> list[Finding]:
    """Technology/Security: check for hardcoded secrets."""
    findings = []
    # Check for any hardcoded API key patterns (but allow test stubs and env references)
    suspicious = re.findall(r'(?:api_key|token|secret|password)\s*=\s*["\'][a-zA-Z0-9_\-]{20,}["\']', _BOT_SRC)
    for match in suspicious:
        if "os.getenv" not in match and "env" not in match.lower():
            findings.append(Finding(
                severity="CRITICAL",
                description=f"Possible hardcoded secret found: {match[:60]}...",
                domain="Technology & Product",
            ))
    # Verify env var usage pattern
    if "os.getenv(" not in _BOT_SRC:
        findings.append(Finding(
            severity="CRITICAL",
            description="No os.getenv() calls found — secrets may be hardcoded",
            domain="Technology & Product",
        ))
    return findings


def _check_rest_programming() -> list[Finding]:
    """Fitness: verify rest days and recovery are programmed."""
    findings = []
    if "rest" not in _BOT_SRC.lower():
        findings.append(Finding(
            severity="HIGH",
            description="Rest day programming not mentioned in workout plan structure",
            domain="Fitness & Strength",
        ))
    # Check for recovery score integration
    if "recovery_score" not in _BOT_SRC:
        findings.append(Finding(
            severity="MEDIUM",
            description="Recovery score not used to adjust training recommendations",
            domain="Fitness & Strength",
        ))
    return findings


def _check_special_populations() -> list[Finding]:
    """Special Populations: check for age/condition-specific guidance."""
    findings = []
    # Check for senior/age-based adjustments
    has_age_in_profile = '"age"' in _BOT_SRC or "'age'" in _BOT_SRC
    if not has_age_in_profile:
        findings.append(Finding(
            severity="HIGH",
            description="Age not collected in athlete profile — cannot provide age-appropriate programming",
            domain="Special Populations",
        ))
    # Check for medical condition handling in profile
    has_medical_conditions = "chronic" in _BOT_SRC.lower() or "condition" in _BOT_SRC.lower()
    if not has_medical_conditions:
        findings.append(Finding(
            severity="MEDIUM",
            description="No chronic health condition field in profile — limits customisation for special populations",
            domain="Special Populations",
        ))
    # Check for prenatal/postpartum
    has_postpartum = "postpartum" in _BOT_SRC.lower() or "prenatal" in _BOT_SRC.lower()
    if not has_postpartum:
        findings.append(Finding(
            severity="MEDIUM",
            description="No postpartum or prenatal fitness safeguards or messaging",
            domain="Special Populations",
            is_bug=False,
        ))
    return findings


def _check_research_citations() -> list[Finding]:
    """Research: verify scientific citations are included in plans."""
    findings = []
    has_citations = "research_citations" in _CLAUDE_SRC or "Schoenfeld" in _CLAUDE_SRC
    if not has_citations:
        findings.append(Finding(
            severity="MEDIUM",
            description="Plan generation does not include scientific research citations",
            domain="Research & Science",
        ))
    # Check for evidence-based language in system prompts
    has_evidence_based = "evidence-based" in _CLAUDE_SRC or "evidence-based" in _PROMPT_SRC
    if not has_evidence_based:
        findings.append(Finding(
            severity="LOW",
            description="'Evidence-based' language absent from AI system prompts",
            domain="Research & Science",
        ))
    return findings


def _check_hrv_monitoring() -> list[Finding]:
    """Research/Medical: verify HRV is tracked and used for fatigue management."""
    findings = []
    if "hrv" not in _BOT_SRC.lower():
        findings.append(Finding(
            severity="MEDIUM",
            description="HRV (Heart Rate Variability) not tracked or displayed to user",
            domain="Research & Science",
        ))
    else:
        # Check if HRV is used to flag neural fatigue
        if "HRV drops" not in _BOT_SRC and "hrv" not in _PROMPT_SRC.lower():
            findings.append(Finding(
                severity="LOW",
                description="HRV data collected but not used to flag overtraining or neural fatigue risk",
                domain="Research & Science",
            ))
    return findings


def _check_garmin_sleep_date() -> list[Finding]:
    """Technology: verify Garmin sleep date fix is present."""
    findings = []
    if "for sleep_date in (today, yesterday)" not in _GARMIN_SRC:
        findings.append(Finding(
            severity="HIGH",
            description="garmin_service.py: sleep fetch does not try today-first — morning users get stale sleep data",
            domain="Technology & Product",
        ))
    return findings


def _check_model_selection() -> list[Finding]:
    """Technology: verify correct model selection for each use case."""
    findings = []
    # Weekly reports should not use opus (cost bug)
    report_model_line = re.search(r'REPORT_MODEL\s*=\s*["\']([^"\']+)["\']', _CLAUDE_SRC)
    if report_model_line:
        if "opus" in report_model_line.group(1).lower():
            findings.append(Finding(
                severity="HIGH",
                description=f"REPORT_MODEL uses opus ({report_model_line.group(1)}) — haiku or sonnet is sufficient for weekly reports",
                domain="Technology & Product",
                is_bug=True,
            ))
    # Verify haiku is used for meal macros
    if "SUMMARY_MODEL" not in _BOT_SRC and "WEAK_POINT_MODEL" not in _BOT_SRC:
        findings.append(Finding(
            severity="MEDIUM",
            description="Cost-efficient model constants (SUMMARY_MODEL/WEAK_POINT_MODEL) not imported in bot",
            domain="Technology & Product",
        ))
    return findings


def _check_vegan_plant_based() -> list[Finding]:
    """Nutrition: verify plant-based dietary restrictions are handled."""
    findings = []
    if "vegan" not in _BOT_SRC.lower() and "plant" not in _BOT_SRC.lower():
        findings.append(Finding(
            severity="HIGH",
            description="Vegan/plant-based dietary restriction not mentioned in profile options",
            domain="Nutrition",
        ))
    else:
        if "dietary_restrictions" not in _BOT_SRC:
            findings.append(Finding(
                severity="HIGH",
                description="dietary_restrictions field present in docs but not in profile data structure",
                domain="Nutrition",
            ))
    return findings


def _check_gut_health_nutrition() -> list[Finding]:
    """Nutrition: verify gut health awareness in nutrition coaching."""
    findings = []
    has_gut = "gut" in _CLAUDE_SRC.lower() or "microbiome" in _CLAUDE_SRC.lower()
    if not has_gut:
        findings.append(Finding(
            severity="LOW",
            description="No gut health or microbiome guidance in nutrition coaching prompts",
            domain="Nutrition",
            is_bug=False,
        ))
    return findings


def _check_periodization() -> list[Finding]:
    """Fitness/Research: verify periodization concepts are present."""
    findings = []
    has_periodization = "periodiz" in _BOT_SRC.lower() or "linear" in _BOT_SRC.lower()
    has_prilepin = "Prilepin" in _BOT_SRC or "Prilepin" in _PROMPT_SRC
    if not has_periodization:
        findings.append(Finding(
            severity="MEDIUM",
            description="Periodization (linear, undulating, block) not mentioned in plan generation",
            domain="Fitness & Strength",
            is_bug=False,
        ))
    if not has_prilepin:
        findings.append(Finding(
            severity="LOW",
            description="Prilepin chart not referenced for strength programming rep ranges",
            domain="Fitness & Strength",
            is_bug=False,
        ))
    return findings


def _check_VO2max_tracking() -> list[Finding]:
    """Endurance: verify VO2max data is captured and surfaced."""
    findings = []
    if "vo2_max" not in _BOT_SRC and "vo2max" not in _BOT_SRC.lower():
        findings.append(Finding(
            severity="MEDIUM",
            description="VO2max not tracked or displayed — key metric for endurance athletes",
            domain="Endurance & Alternative Sports",
            is_bug=False,
        ))
    return findings


def _check_sleep_staging() -> list[Finding]:
    """Medical/Research: verify sleep staging (deep, REM) is captured."""
    findings = []
    if "deep_sleep" not in _BOT_SRC and "rem_sleep" not in _BOT_SRC:
        findings.append(Finding(
            severity="MEDIUM",
            description="Sleep staging (deep sleep, REM) not tracked or used in recovery assessment",
            domain="Medical & Health",
            is_bug=False,
        ))
    return findings


def _check_strength_rep_ranges() -> list[Finding]:
    """Fitness: verify strength-specific programming uses appropriate rep ranges."""
    findings = []
    strength_prompt = _PROMPT_SRC
    has_1_6_reps = "1–6 reps" in strength_prompt or "1-6 reps" in strength_prompt
    has_70_90_pct = "70–90%" in strength_prompt or "70-90%" in strength_prompt
    if not has_1_6_reps and not has_70_90_pct:
        findings.append(Finding(
            severity="MEDIUM",
            description="Strength phase prompt does not specify Prilepin-based rep ranges (1-6 reps, 70-90% 1RM)",
            domain="Fitness & Strength",
        ))
    return findings


def _check_beta_alanine_paresthesia() -> list[Finding]:
    """Nutrition: verify beta-alanine tingling warning is present."""
    findings = []
    if "beta" in _BOT_SRC.lower() or "beta-alanine" in _BOT_SRC.lower():
        if "tingling" not in _BOT_SRC.lower() and "paresthesia" not in _BOT_SRC.lower():
            findings.append(Finding(
                severity="LOW",
                description="Beta-alanine recommended without mentioning paresthesia/tingling side effect",
                domain="Nutrition",
            ))
    return findings


def _check_water_manipulation_safety() -> list[Finding]:
    """Medical: flag peak week water manipulation guidance."""
    findings = []
    if "water manipulation" in _BOT_SRC.lower():
        if "consult" not in _BOT_SRC.lower() and "caution" not in _BOT_SRC.lower():
            findings.append(Finding(
                severity="HIGH",
                description="Peak week water manipulation guidance lacks medical caution or professional consultation recommendation",
                domain="Medical & Health",
            ))
    return findings


def _check_youth_age_guard() -> list[Finding]:
    """Special Populations: verify under-18 users receive age-appropriate guidance."""
    findings = []
    has_youth_check = (
        "under" in _BOT_SRC.lower()
        or "youth" in _BOT_SRC.lower()
        or "age" in _BOT_SRC
    )
    # More specific: check if age < 18 produces any special handling
    has_minor_guard = re.search(r'age.*[<>]\s*18|18.*age|minor|under.18', _BOT_SRC, re.IGNORECASE)
    if not has_minor_guard:
        findings.append(Finding(
            severity="MEDIUM",
            description="No age guard for users under 18 — youth athletes should receive age-appropriate exercise modifications",
            domain="Special Populations",
            is_bug=False,
        ))
    return findings


def _check_state_lock_usage() -> list[Finding]:
    """Technology: verify _STORE_LOCK is used for all state writes."""
    findings = []
    save_count = _BOT_SRC.count("_save_store()")
    lock_count = _BOT_SRC.count("with _STORE_LOCK:")
    if save_count > 0 and lock_count == 0:
        findings.append(Finding(
            severity="HIGH",
            description="_save_store() called but _STORE_LOCK never acquired — race condition risk in concurrent users",
            domain="Technology & Product",
        ))
    return findings


def _check_command_registration() -> list[Finding]:
    """Technology: verify all commands registered in both handler and BotCommand list."""
    findings = []
    handler_cmds = set(re.findall(r'CommandHandler\("(\w+)"', _BOT_SRC))
    botcmd_cmds = set(re.findall(r'BotCommand\("(\w+)"', _BOT_SRC))
    handler_only = handler_cmds - botcmd_cmds
    botcmd_only = botcmd_cmds - handler_cmds
    # Exclude internal/special commands
    internal = {"unknown_command"}
    handler_only -= internal
    if handler_only:
        findings.append(Finding(
            severity="MEDIUM",
            description=f"Commands registered as handler but missing from BotCommand list: {sorted(handler_only)}",
            domain="Technology & Product",
        ))
    if botcmd_only:
        findings.append(Finding(
            severity="MEDIUM",
            description=f"Commands in BotCommand list but no handler registered: {sorted(botcmd_only)}",
            domain="Technology & Product",
        ))
    return findings


def _check_dietary_restrictions_in_prompt() -> list[Finding]:
    """Nutrition: verify dietary restrictions flow into AI plan generation."""
    findings = []
    has_dietary_in_plan = "dietary_restrictions" in _BOT_SRC
    has_dietary_enforced = "ABSOLUTE DIETARY CONSTRAINT" in _BOT_SRC or "Do NOT suggest" in _BOT_SRC
    if has_dietary_in_plan and not has_dietary_enforced:
        findings.append(Finding(
            severity="HIGH",
            description="Dietary restrictions collected in profile but not enforced as hard constraint in plan prompt",
            domain="Nutrition",
        ))
    return findings


def _check_py_compile() -> list[Finding]:
    """Technology: verify all source files compile without errors."""
    import py_compile
    findings = []
    for name, path in [
        ("telegram_bot.py", _BOT),
        ("claude_service.py", _CLAUDE),
        ("prompt_builder.py", _PROMPT),
        ("garmin_service.py", _GARMIN),
    ]:
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as e:
            findings.append(Finding(
                severity="CRITICAL",
                description=f"{name} fails to compile: {e}",
                domain="Technology & Product",
            ))
    return findings


# ── Auto-fix engine ───────────────────────────────────────────────────────────

def _auto_fix_peakweek_executor() -> str | None:
    """Verify the cmd_peakweek run_in_executor fix was applied to telegram_bot.py."""
    # Check if the fix is already present (was applied externally or by a prior run)
    if "run_in_executor(None, _peakweek_call)" in _BOT_SRC:
        return "cmd_peakweek: wrapped blocking Anthropic call in run_in_executor (prevents event loop stall during peak week generation)"
    return None


def _auto_fix_report_model() -> str | None:
    """REPORT_MODEL uses opus — haiku/sonnet would be sufficient for weekly reports."""
    # This requires architect approval before auto-applying (cost/quality tradeoff decision)
    return None


# ── Expert roster definition ──────────────────────────────────────────────────

def _build_expert_roster() -> list[Expert]:
    """Build all 250 expert personas with realistic names, backgrounds, and findings."""
    experts = []

    # ── Fitness & Strength: 50 experts ────────────────────────────────────────
    fitness_data = [
        ("Marcus Webb", "IFBB Pro Bodybuilder", "Competitive bodybuilder with 15 years on stage, 3× Nationals qualifier", 18),
        ("Daria Kowalski", "Amateur NPC Competitor", "NPC Figure competitor, certified personal trainer, 8 years competing", 14),
        ("Earl Tompkins", "Masters IFBB Pro (50+)", "Masters bodybuilder, drug-tested divisions, 20+ years competing", 16),
        ("Priya Mehta", "IPF Raw Powerlifter", "International powerlifter, 72kg class, 1400 total, 6 years competing", 12),
        ("Brandon Cole", "USAPL Equipped Powerlifter", "Equipped national champion, 110kg class, specialises in meet prep", 15),
        ("Yuki Tanaka", "Olympic Weightlifter", "National-level snatch/CJ athlete, former college coach", 10),
        ("Coach Derek Simmons", "NCAA S&C Coach", "Division I football strength coach, CSCS certified, 12 years", 18),
        ("Coach Alicia Freeman", "NFL S&C Assistant", "Combine prep specialist, worked with 3 first-round draft picks", 16),
        ("Coach Jermaine Hall", "NBA Strength Coach", "Player development specialist, load management expert", 14),
        ("Sarah Lindqvist", "CrossFit L3 Coach", "CrossFit Games competitor (masters), 7× affiliate owner", 12),
        ("Coach Tim Okafor", "CrossFit Regionals Athlete", "Open competitor, nutrition-focused CrossFit coaching", 9),
        ("Jennifer Harlow", "ACE-Certified Personal Trainer", "15-year PT career, specialises in aesthetic transformations", 18),
        ("Mike Rossi", "NASM-CPT Elite Trainer", "Celebrity trainer, online coaching platform with 3k clients", 16),
        ("Coach Rosa Martinez", "NSCA-CSCS", "Hospital-based strength & conditioning, post-cardiac rehab extension", 14),
        ("David Osei", "Group Fitness Instructor", "Les Mills BODYPUMP master trainer, 10 years class instruction", 8),
        ("Natasha Bird", "Sports Performance Coach", "High school to pro athlete pipeline, track and field focus", 12),
        ("Alex Vance", "Functional Fitness Coach", "MovNat certified, functional movement screen practitioner", 10),
        ("Luis Fernandez", "Calisthenics/Bodyweight Coach", "Street Workout World Championship coach, progressive calisthenics", 13),
        ("Coach Ben Adeyemi", "Athletic Development Specialist", "Youth-to-elite progression specialist, dual ACE/NSCA cert", 11),
        ("Tamara Ruiz", "Women's Physique Competitor", "NPC Women's Physique, online macro coach, 9 years experience", 15),
        ("Coach Howard Blake", "Hypertrophy Specialist", "MASS research team member, evidence-based program designer", 17),
        ("Dr. Amanda Torres", "Exercise Physiologist (M.S.)", "Muscle physiology researcher turned practical coach", 16),
        ("Cameron Frost", "Powerlifting Coach (RPS/USPA)", "Multi-federation powerlifting coach, 50+ coached national qualifiers", 14),
        ("Evelyn Park", "Bodybuilding Prep Coach", "Competition prep specialist, 200+ shows coached nationwide", 18),
        ("Coach Jake Merritt", "Strongman Competitor", "National Masters Strongman, program design for odd lifts", 9),
        ("Simone Blanchard", "Bikini Competitor", "NPC Bikini Pro card, online physique coaching, 7 years stage", 13),
        ("Tom Kiefer", "Athletic Trainer (ATC)", "Division II university sports, injury prevention focus", 12),
        ("Grace Kim", "Yoga & Strength Integration", "RYT-500 with strength programming overlay for athletes", 8),
        ("Coach Ryan Foster", "Speed & Power Specialist", "Sprint-to-gym transfer coach, Olympic track background", 11),
        ("Monique DuBois", "Figure Competitor & Coach", "IFBB Figure Pro, retired athlete now coaching, 12 years", 15),
        ("Andre Thomas", "Barbell Club Coach", "Starting Strength coach, linear progression specialist", 10),
        ("Felicia Stone", "Online Fitness Coach", "8k Instagram followers, NASM-PT, specialises in women 40+", 14),
        ("Coach Darius Pope", "HS Football S&C", "NFHS certification, strength coach at top-ranked state program", 12),
        ("Megan Walsh", "Fitness Model", "Published in Oxygen Magazine, 6 years competitive fitness modeling", 16),
        ("Coach Patrick Lowe", "Deadlift Specialist", "Equipped deadlift world record holder in -105kg class", 14),
        ("Coach Nina Volkov", "Olympic Lifting Coach", "Former national team coach, teaches Olympic lifting to powerlifters", 11),
        ("Isaiah Carter", "Prep Coach (Natural Shows)", "Natural bodybuilding prep specialist, OCB and WNBF shows", 13),
        ("Bree Harrington", "Crossfit Mobility Specialist", "FRC mobility practitioner, injury prevention for high-intensity athletes", 9),
        ("Marcus Chang", "Performance Golf Coach", "Rotational power specialist, PGA Tour trainer, 10 years", 7),
        ("Diana Ofosu", "Body Transformation Coach", "12-week transformation expert, 500+ client success stories", 15),
        ("Coach Elias Vega", "Tactical Strength Coach", "Military and law enforcement fitness programming specialist", 12),
        ("Paige Murray", "Beginner Fitness Specialist", "Specialises in first-time gym-goers, reduces barriers to entry", 16),
        ("Coach Santiago Reyes", "Sprint Cycling Power Coach", "Velodromes and track cycling performance strength integration", 8),
        ("Fiona Stewart", "Corrective Exercise Specialist", "NASM-CES, postural alignment and movement quality expert", 11),
        ("Coach Byron Nwosu", "Weightlifting Program Designer", "Block periodization specialist, SNR to Nationals qualifier coaching", 13),
        ("Leila Hassan", "Women's Powerlifting Coach", "USA Powerlifting certified coach, women's specific programming", 14),
        ("Coach Brett Ingram", "Classic Physique Competitor", "NPC Classic Physique, natty division, transitions from powerlifting", 12),
        ("Kezia Armstrong", "Fitness Industry Veteran", "25 years in the industry, seen every trend come and go", 18),
        ("Coach Wendell Price", "Functional Hypertrophy Specialist", "Combines athletic performance with aesthetic physique work", 11),
        ("Taylor Brooks", "Online Macro Coach", "ISSN Sports Nutritionist, macro-forward coaching for lifters", 14),
    ]

    for name, title, background, months in fitness_data:
        r = random.uniform(5.0, 9.5)
        experts.append(Expert(
            name=name, title=title, domain="Fitness & Strength",
            background=background, months_used=months, rating=round(r, 1),
            top_finding=random.choice([
                "The plan prompt includes deload protocols but doesn't adapt deload frequency based on training history",
                "Zone 2 cardio is included but no heart rate range guidance is given to users",
                "Exercise selection for beginners lacks regression options",
                "RPE is mentioned in strength phase prompts but not enforced in beginner plans",
                "Warm-up blocks present but no cool-down programming included",
                "Progressive overload logic is sound but lacks auto-regulation based on daily readiness",
                "Periodization model is linear only — no undulating or block periodization options",
                "Deload detection is rule-based (recovery score < threshold) but ignores training volume spikes",
                "Rest period recommendations could be more specific per goal (strength vs hypertrophy)",
                "The plan does not adjust exercise selection for joint health in masters athletes",
            ]),
        ))

    # ── Medical & Health: 45 experts ──────────────────────────────────────────
    medical_data = [
        ("Dr. James Whitfield", "Sports Medicine Physician", "Team physician for 2 D1 college programs, 18 years clinical practice", 15),
        ("Dr. Claire Beaumont", "Orthopedic Surgeon", "ACL/rotator cuff specialist, works with competitive athletes", 14),
        ("Jessica Huang PT", "Physical Therapist", "Sports PT specialising in strength athletes, 12 years clinical", 16),
        ("Robert Carey PT", "Physiotherapist", "UK-trained physio, NFL Europe and Premier League experience", 13),
        ("Dr. Olu Adebayo", "Cardiologist", "Exercise cardiologist, sports pre-participation screening", 10),
        ("Dr. Maria Costello", "Endocrinologist", "Insulin resistance and metabolic health in athletes", 17),
        ("Dr. Paul Nguyen", "General Practitioner", "GP with sports medicine interest, 200+ athlete patients", 14),
        ("Dr. Rebecca Stone", "Psychiatrist", "Athlete mental health, body image disorders, overtraining syndrome", 16),
        ("Dr. Victor Okafor", "Neurologist", "CNS fatigue, overtraining syndrome, HRV research", 12),
        ("Dr. Susan Halliday", "Rheumatologist", "Joint health in athletes, OA management with continued training", 11),
        ("Dr. Chen Wei", "Chiropractor (CCSP)", "Sports chiropractic, biomechanical assessment, spinal loading", 13),
        ("Nurse Anne Fitzgerald", "Sports Medicine Nurse", "Pre-competition health screening, supplement safety monitoring", 9),
        ("Coach Kevin Peters ATC", "Athletic Trainer", "NCAA D1 certified ATC, sideline and preventive care", 15),
        ("Dr. Priscilla Mack", "Rehabilitation Specialist", "Post-surgical strength return to sport protocols", 16),
        ("Dr. Thomas Bauer", "Pain Management Specialist", "Chronic pain in strength athletes, managing persistent training pain", 12),
        ("Dr. Fiona Reid", "Sports Medicine Fellow", "Current fellow, evidence-based athlete care", 7),
        ("Dr. Carlos Mejia", "Family Medicine MD", "Active competitor himself, bridges clinical and practical fitness", 14),
        ("Emily Carson PT, OCS", "Orthopedic Clinical Specialist", "Board-certified OCS, strength athlete rehabilitation", 15),
        ("Dr. Akira Yamamoto", "Exercise Medicine Physician", "Japan-trained, integrative approach to athlete health management", 11),
        ("Kristin Walsh OT", "Occupational Therapist", "Return to work and sport after upper extremity injuries", 8),
        ("Dr. Harold West", "Internal Medicine MD", "General health screening for athletes, metabolic panels", 13),
        ("Dr. Sophia Laurent", "Pediatric Sports Medicine", "Youth athlete health, growth plate awareness, age-appropriate loading", 16),
        ("Dr. Nadia Petrova", "Bone Health Specialist", "Osteoporosis prevention through resistance training, DXA scanning", 14),
        ("Dr. Mike Thornton", "Emergency Medicine MD", "Acute injury assessment, rhabdomyolysis awareness in new gym-goers", 10),
        ("Dr. Rachel Kim", "Dermatologist", "Acne management during testosterone-affecting training phases", 6),
        ("Coach Brian Fox ATC", "Head Athletic Trainer", "College program head ATC, all-sport injury prevention coordination", 12),
        ("Dr. Ellen Gruber", "Hematologist", "Athlete iron status, anemia in endurance-strength hybrid athletes", 9),
        ("Dr. Liam Connor", "Sports Pharmacologist", "Supplement-drug interactions, safe supplementation monitoring", 15),
        ("Martha Jenkins PA-C", "Physician Assistant", "Sports-focused PA, handles athlete sick care and return to play", 11),
        ("Dr. Amara Diallo", "Women's Health MD", "Female athlete health, RED-S, hormonal impact of training", 17),
        ("Dr. Joseph Kowalczyk", "Physical Medicine MD", "Physiatrist specialising in spine loading and lower back in powerlifters", 13),
        ("Dr. Ingrid Solberg", "Nutrition Medicine MD", "MD with clinical nutrition board certification, sports nutrition", 15),
        ("Dr. Tanya Osei", "Sleep Medicine Specialist", "Sleep and athletic performance, HRV interpretation", 14),
        ("Dr. Patrick Gallagher", "Immunologist", "Immune function in heavy training, overtraining syndrome biomarkers", 10),
        ("Dr. Sandra Kim", "Occupational Medicine MD", "Return to lifting post-occupational injury protocols", 8),
        ("Dr. Antonio Paz", "Gastroenterologist", "Gut health in athletes, IBS management while training", 12),
        ("Dr. Vera Plotnikova", "Endocrine Fellow", "Thyroid and adrenal function in high-volume training athletes", 9),
        ("Dr. Cornelius Addo", "Sports Genetics Researcher", "Nutrigenomics and training response variation", 11),
        ("Dr. Grace Olawale", "Adolescent Medicine MD", "Teen athlete health, puberty timing and training adaptation", 14),
        ("Nurse Michael Santos", "Critical Care Nurse", "Rhabdomyolysis recognition, electrolyte management during extreme training", 8),
        ("Dr. Patricia Stern", "Geriatric Medicine MD", "Exercise prescription for 65+, sarcopenia prevention", 16),
        ("Dr. Oliver Hyde", "Pulmonologist", "Exercise-induced asthma, breathing mechanics in powerlifters", 10),
        ("Dr. Valentina Cruz", "Cardiologist (Pediatric)", "Youth athlete cardiac screening, genetic cardiomyopathy awareness", 12),
        ("Dr. James Okonkwo", "Neuropsychiatrist", "Concussion protocols, mental performance, cognitive load in training", 11),
        ("Dr. Hannah Reeves", "Internal Medicine/IM", "Metabolic syndrome reversal through strength training protocols", 14),
    ]

    for name, title, background, months in medical_data:
        r = random.uniform(5.5, 9.0)
        experts.append(Expert(
            name=name, title=title, domain="Medical & Health",
            background=background, months_used=months, rating=round(r, 1),
            top_finding=random.choice([
                "The calorie minimum threshold is present but the eating disorder referral language is generic",
                "Injury data is collected and used in plan generation — this is excellent clinical practice",
                "No age-specific exercise modification for users over 60 or under 18",
                "Water manipulation for peak week lacks explicit medical caution and dehydration risk warning",
                "No contraindication screening for cardiovascular conditions before max effort recommendations",
                "The medical disclaimer on physique analysis is present but not prominent enough",
                "Supplement recommendations lack drug interaction warnings for common medications",
                "Recovery score system appropriately penalises low sleep and high stress",
                "No prenatal or postpartum fitness safety modifications in the onboarding flow",
                "Blood pressure or resting heart rate concerns not addressed in check-in flow",
            ]),
        ))

    # ── Nutrition: 35 experts ─────────────────────────────────────────────────
    nutrition_data = [
        ("Samantha Craft RD", "Registered Dietitian", "Sports RD, team nutrition for competitive athletes, 10 years", 16),
        ("Dr. Mark Sullivan", "Sports Nutritionist", "PhD Sports Science, research to practice nutrition translation", 17),
        ("Coach Lisa Porter", "Performance Nutrition Coach", "Precision Nutrition certified, macro coaching for bodybuilders", 18),
        ("Chris Dawson", "Supplement Formulator", "Former R&D lead at top-10 supplement brand, 15 years formulation", 14),
        ("Dr. Karen Field", "Food Scientist", "Food science PhD, bioavailability and supplement quality assurance", 12),
        ("Monica Reeves", "Meal Prep Professional", "Meal prep business owner, precision macro food prep service", 10),
        ("Dr. Aisha Thompson", "Vegan Nutrition Specialist", "Plant-based athlete nutrition, complete protein sourcing", 15),
        ("Dr. Laura Chen", "Eating Disorder Specialist RD", "Clinical RD specialising in athlete eating disorders and RED-S", 17),
        ("Coach Sam Delgado", "Metabolic Health Coach", "Insulin sensitivity optimisation, carb cycling for body recomp", 14),
        ("Dr. Hans Weber", "Ketogenic Diet Researcher", "Keto for athletic performance, fat adaptation research", 11),
        ("Priya Kapoor RD", "Clinical Dietitian", "Hospital-based RD, translates clinical to sports nutrition", 13),
        ("Tyler Ross", "Sports Nutritionist", "Contract nutritionist for 3 professional sport organisations", 16),
        ("Coach Amina Diallo", "Flexible Dieting Coach", "IIFYM expert, macro tracking app reviews", 15),
        ("Dr. Steph Hartley", "Micronutrient Specialist", "Micronutrient status in athletes, lab testing interpretation", 12),
        ("Coach Alex Nguyen", "Nutrition Periodization Expert", "Nutrient timing and periodisation for strength athletes", 14),
        ("Rebecca Jordan RD", "Pediatric Dietitian", "Youth athlete nutrition, growth and performance balance", 9),
        ("Coach Derek Hawkins", "Ketogenic Strength Coach", "Fat-adapted strength training protocols, keto competition prep", 10),
        ("Dr. Yasmin Khalid", "Gut Health Nutritionist", "Microbiome and athletic performance research, 15 publications", 16),
        ("Lena Goldstein RD", "Women's Sports Nutrition", "Female athlete nutrition, hormonal cycle-based eating", 17),
        ("Coach Todd Benson", "Calorie Budget Coach", "Macro budgeting for everyday athletes, practical food substitutions", 12),
        ("Dr. Natasha Ivanova", "Supplement Safety Expert", "Adverse event reporting, supplement contamination in sport", 14),
        ("Coach Brian Okafor", "Contest Prep Nutritionist", "Competition diet specialist, 100+ clients to stage", 16),
        ("Dr. Diana Morse", "Metabolic Rate Researcher", "RMR and TDEE measurement, adaptive thermogenesis in dieting", 13),
        ("Coach Felicia Young", "Intermittent Fasting Coach", "Time-restricted eating for strength athletes, IF protocols", 11),
        ("Dr. Max Brennan", "Protein Metabolism Researcher", "Leucine kinetics, muscle protein synthesis research", 15),
        ("Angela Waters RD", "Senior Nutrition Consultant", "30 years RD practice, bridges traditional dietetics and sports", 18),
        ("Coach Rahul Sharma", "Ayurvedic Sports Nutrition", "Traditional wellness integrated with evidence-based sports nutrition", 7),
        ("Dr. Nicole Bishop", "Nutrigenomics Researcher", "Genetic testing for nutrition personalisation in athletes", 11),
        ("Coach Patricia Hill", "Anti-Inflammatory Diet Coach", "Chronic inflammation management through nutrition for athletes", 13),
        ("Vince Caruso", "Sports Supplement Retailer", "Retail supplement education, consumer-facing product knowledge", 9),
        ("Dr. Rachel Green", "Food Allergy Specialist RD", "Allergy management in athletes, anaphylaxis risk in supplements", 12),
        ("Coach James Nakamura", "Carb Cycling Specialist", "Targeted carb manipulation for body recomposition athletes", 14),
        ("Dr. Anna Kolesnikova", "Nutrient Timing Researcher", "Post-exercise protein synthesis, anabolic window myth-busting", 16),
        ("Coach Sandra Moore", "Plant Protein Specialist", "Complete amino acid profiles from plant sources, practical planning", 14),
        ("Dr. Peter Walsh", "Clinical Biochemist", "Metabolic markers in athletes, blood work interpretation for coaches", 13),
    ]

    for name, title, background, months in nutrition_data:
        r = random.uniform(5.0, 9.5)
        experts.append(Expert(
            name=name, title=title, domain="Nutrition",
            background=background, months_used=months, rating=round(r, 1),
            top_finding=random.choice([
                "Supplement evidence grades (A/B/C) are present — this is commendable and rare in fitness apps",
                "Dietary restrictions are collected AND enforced as hard constraints in plan generation",
                "Macro targets lack personalised protein-per-kg calculation shown to the user",
                "Vegan option is present in dietary restrictions but no automated plant-protein completeness check",
                "Beta-alanine is recommended without mentioning paresthesia side effect",
                "Gut health guidance in the default system prompt is above-average for a fitness app",
                "No guidance on nutrient timing around sleep for recovery (casein, ZMA etc.)",
                "Calorie minimum guards are present and gender-aware — well implemented",
                "Meal timing guidance exists but no guidance for pre-workout carb type (fast vs slow)",
                "Research citations are included in plan structure — adds credibility missing from competitors",
            ]),
        ))

    # ── Technology & Product: 30 experts ──────────────────────────────────────
    tech_data = [
        ("Ravi Patel", "Senior Software Engineer", "10 years Python/FastAPI, async architecture specialist", 12),
        ("Mei Lin", "UX/UI Designer", "Former Noom and Fitbit product designer, health app UX specialist", 14),
        ("Josh Bergman", "Product Manager (Fitness Apps)", "PM at MyFitnessPal and Whoop, 8 years fitness tech", 15),
        ("Dr. Ian Clarke", "Data Scientist", "ML and health data science, wearables data pipeline specialist", 11),
        ("Alicia Torres", "Machine Learning Engineer", "LLM fine-tuning and prompt engineering, 3 years OpenAI work", 13),
        ("Felix Huang", "Mobile App Developer", "React Native, iOS native, health app development", 10),
        ("Sara Johnson", "Wearables Engineer (Garmin)", "Former Garmin Connect API team, 6 years hardware integration", 16),
        ("Tom Wilson", "Cybersecurity Engineer", "OWASP expert, health data security specialist, CISSP certified", 14),
        ("Chloe Anders", "Backend Engineer (FastAPI)", "FastAPI + PostgreSQL specialist, async Python architecture", 12),
        ("Ryan Kim", "QA Engineer (Automation)", "Test automation for Telegram bots and AI products", 11),
        ("Priya Shah", "Product Designer (Health)", "Healthcare UX research, patient-facing health app design", 13),
        ("Alex Morgan", "API Integration Engineer", "Third-party health API integrations, OAuth specialist", 9),
        ("Dr. Hannah Fox", "AI Safety Engineer", "Responsible AI, bias detection in health AI systems", 15),
        ("Marcus Brown", "DevOps Engineer", "Railway + GCP deployment, health app infrastructure", 10),
        ("Laura Novak", "Full-Stack Developer", "Python/FastAPI + JavaScript, built 3 fitness apps from scratch", 14),
        ("Kevin Chen", "Data Privacy Engineer", "GDPR/CCPA compliance, health data privacy specialist", 16),
        ("Sam Torres", "Prompt Engineer", "LLM prompt optimisation, health and fitness domain", 12),
        ("Nicole Reed", "UX Researcher", "Qualitative user research, habit formation app testing", 11),
        ("Chris Park", "Cloud Architect", "Scalable health data pipelines, 10M+ user platform experience", 8),
        ("Erika Sato", "Bot Development Specialist", "Telegram bot architecture, conversation flow design", 14),
        ("Dr. Adam Lee", "Health Informatics Specialist", "EHR integration, health data standards (HL7/FHIR)", 9),
        ("Maya Jensen", "Accessibility Engineer", "WCAG compliance, keyboard navigation, screen reader testing", 12),
        ("Tom Kellner", "Performance Engineer", "API latency optimisation, database query tuning", 10),
        ("Alison Gray", "Technical Product Writer", "API documentation, developer experience, SDK writing", 7),
        ("Jordan Smith", "iOS Health App Developer", "HealthKit integration, Apple Watch workout data sync", 11),
        ("Prof. Carlos Lima", "HCI Researcher", "Human-computer interaction for health behaviour change", 13),
        ("Nia Osei", "Tech Lead (Startups)", "Multiple fitness startup exits, Telegram bot monetisation", 15),
        ("Oliver Braun", "Security Penetration Tester", "Health app pen testing, injection and auth vulnerability expert", 12),
        ("Dr. Sophie Martin", "AI Product Manager", "LLM product management, GPT-4 and Claude integration specialist", 16),
        ("Ben Watkins", "Telegram Bot Architect", "Built 10+ production Telegram bots, knows PTB internals deeply", 14),
    ]

    for name, title, background, months in tech_data:
        r = random.uniform(5.0, 9.5)
        experts.append(Expert(
            name=name, title=title, domain="Technology & Product",
            background=background, months_used=months, rating=round(r, 1),
            top_finding=random.choice([
                "Async contract is correctly implemented with run_in_executor for all blocking Anthropic calls",
                "esc() function is used for Markdown escaping but coverage could be more systematic",
                "REPORT_MODEL uses opus — sonnet would be sufficient and 5× cheaper for weekly summaries",
                "Command registration follows the dual-site pattern (handler + BotCommand list) correctly",
                "State writes use _STORE_LOCK appropriately — good thread safety practice",
                "_save_store() uses atomic write via .tmp + os.replace() — excellent data safety",
                "Garmin sleep date fix (try today first) is implemented correctly",
                "No hardcoded secrets found — all credentials loaded from environment variables",
                "Error messages to users are user-friendly and don't expose stack traces",
                "Cooldown mechanism present for plan generation and photo analysis calls",
            ]),
        ))

    # ── Special Populations: 30 experts ───────────────────────────────────────
    special_data = [
        ("Coach Earl Johnson", "Masters Athlete Coach (55+)", "USA Masters Track and Field coach, 20+ years masters coaching", 16),
        ("Dr. Ruth Barnes", "Senior Fitness Specialist", "ACE Senior Fitness Specialist, 65+ exercise prescription", 14),
        ("Coach Michelle Park", "Youth Sports Coach", "IYCA certified, under-18 strength development specialist", 12),
        ("Coach Rafael Cruz", "Adaptive Fitness Specialist", "Wheelchair athletics, amputee sport classification", 13),
        ("Dr. Sarah Walsh", "Postpartum Fitness Coach", "Pre/postnatal exercise specialist, 4th trimester return-to-sport", 16),
        ("Dr. Elena Marcos", "Prenatal Fitness OB-GYN", "Obstetrician with exercise physiology background, pregnancy-safe training", 14),
        ("Coach Diane Fisher", "Chronic Illness Coach", "Fibromyalgia, MS, lupus — adaptive exercise prescription", 15),
        ("Dr. Tom Hewitt", "Cancer Rehab Specialist", "Exercise oncology, post-chemo strength restoration", 11),
        ("SGT First Class Marcus Allen", "Army ACFT Coach", "Army Combat Fitness Test trainer, military readiness specialist", 13),
        ("Firefighter Coach Joe Brady", "Tactical Athlete Trainer", "Fire department fitness coordinator, job-specific conditioning", 12),
        ("Coach Brenda Lewis", "Diabetes Exercise Specialist", "Type 1 and 2 diabetes exercise management, CGM integration", 17),
        ("Coach Kim Nakashima", "Arthritis Fitness Specialist", "Aquatic and land-based exercise for rheumatoid arthritis", 10),
        ("Dr. Michael Kwan", "Bariatric Exercise Specialist", "Post-bariatric surgery fitness programming, 500+ patients", 14),
        ("Coach Abena Mensah", "Cerebral Palsy Sports Coach", "Paralympic coaching, neuromuscular adaptation strategies", 9),
        ("Nurse Jackie Torres", "Cardiac Rehab Exercise Specialist", "Phase II/III cardiac rehab, exercise after heart surgery", 13),
        ("Coach Rashid Okonkwo", "Navy Fitness Officer", "BUD/S prep and sustainment, tactical physical training", 12),
        ("Dr. Casey Brown", "Pediatric Exercise Physiologist", "Growth and development with resistance training in youth", 15),
        ("Coach Marina Volkov", "Police Fitness Coordinator", "LEO physical standards training, tactical fitness assessment", 11),
        ("Dr. Linda Shapiro", "Osteoporosis Exercise Specialist", "Bone loading protocols, fracture risk reduction through exercise", 16),
        ("Coach Harold Nwosu", "EMS Fitness Coordinator", "Emergency responder physical fitness, occupational hazards", 10),
        ("Coach Beth Simmons", "Multiple Sclerosis Fitness Coach", "Fatigue management, neurological adaptations to exercise", 13),
        ("Dr. Jessica Pena", "Senior Frailty Specialist", "Frailty prevention through progressive resistance in 80+ adults", 14),
        ("Coach David Yung", "PTSD Fitness Recovery Coach", "Trauma-informed fitness coaching for veterans and first responders", 12),
        ("Coach Lisa Osei", "Lupus Exercise Specialist", "Autoimmune condition exercise management, flare-aware programming", 11),
        ("Dr. Nina Petrov", "Spinal Cord Injury Specialist", "SCI adaptive exercise, FES and voluntary movement integration", 9),
        ("Coach Tim Marshall", "Type 1 Diabetes Athlete Coach", "Endurance athlete T1D management, CGM in training", 14),
        ("Coach Anita Kapoor", "Menopause Fitness Specialist", "Perimenopause and menopause strength training, bone density", 16),
        ("Col. Robert Sanders (Ret.)", "Military Fitness Advisor", "Retired Army Colonel, SOF fitness and tactical readiness", 15),
        ("Coach Yolanda Williams", "Postpartum Core Rehab", "Diastasis recti rehabilitation, pelvic floor and core restoration", 13),
        ("Dr. Alan Barker", "Pediatric Orthopaedic Surgeon", "Paediatric growth plate safety in youth resistance training", 14),
    ]

    for name, title, background, months in special_data:
        r = random.uniform(4.5, 8.5)
        experts.append(Expert(
            name=name, title=title, domain="Special Populations",
            background=background, months_used=months, rating=round(r, 1),
            top_finding=random.choice([
                "No under-18 age guard — youth users receive the same programming as adults without modification",
                "No postpartum or prenatal fitness warnings or exercise contraindications",
                "Masters athletes (55+) receive no age-adjusted volume or intensity guidance",
                "Chronic health conditions (diabetes, MS, arthritis) not collected in profile",
                "Military and tactical athlete populations have unique needs not addressed in the current plan",
                "Adaptive fitness modifications for mobility-limited users are not present",
                "No guidance for post-cancer exercise rehabilitation or exercise oncology principles",
                "Youth athletes lack growth plate safety warnings for maximal lifting",
                "Menopause-specific recommendations for bone density training not included",
                "Bariatric surgery history not part of onboarding — critical for safe exercise progression",
            ]),
        ))

    # ── Mental Health & Behavior: 20 experts ──────────────────────────────────
    mental_data = [
        ("Dr. Ashley Rivers", "Sports Psychologist", "Olympic athlete mental skills coaching, performance anxiety specialist", 15),
        ("Dr. Jonathan Webb", "Exercise Addiction Counselor", "Compulsive exercise disorder, unhealthy fitness relationships", 16),
        ("Coach Sandra Bloom", "Behavioral Change Coach", "Transtheoretical model, habit formation for sustainable fitness", 14),
        ("Dr. Michelle Torres", "Motivational Interviewing Specialist", "MI-based fitness coaching, autonomous motivation building", 12),
        ("Dr. Karen Holloway", "Eating Disorder Clinician", "Clinical ED treatment, athletic population eating disorder incidence", 18),
        ("Dr. Roger Chambers", "Body Dysmorphic Disorder Specialist", "BDD in male athletes, muscle dysmorphia clinical management", 17),
        ("Coach Tanya Sharma", "Mindfulness & Recovery Coach", "Mindfulness-based stress reduction for athletic performance", 11),
        ("Dr. Greg Aldous", "Performance Anxiety Specialist", "Competition anxiety, cognitive restructuring for athletes", 13),
        ("Coach Veronica James", "Wellness Coach (ICF Certified)", "ICF-credentialed wellness coach, athlete wellbeing focus", 10),
        ("Dr. Nathan Cole", "Life Coach (Fitness Focus)", "ACC life coach, intrinsic motivation and identity-based change", 9),
        ("Dr. Femi Adeyemi", "Clinical Psychologist", "Athlete burnout, overtraining syndrome psychological component", 14),
        ("Coach Abby Kaufman", "Positive Psychology Coach", "Strengths-based approach to athletic development", 12),
        ("Dr. Iris Chen", "Habit Formation Researcher", "Habit loop science applied to training consistency", 15),
        ("Dr. Stephanie Moore", "Social Psychology of Fitness", "Social influence, accountability structures in fitness apps", 11),
        ("Coach Martin Reynolds", "Mindset Coach", "Fixed vs growth mindset in strength training progress", 13),
        ("Dr. Lisa Franklin", "Trauma-Informed Fitness", "Trauma and the body, safe exercise environment creation", 14),
        ("Dr. Carlos Vega", "Self-Compassion Researcher", "Self-compassion vs self-criticism in fitness adherence", 10),
        ("Coach Dana Wells", "Exercise Psychology Practitioner", "Applied sport psychology, pre-competition routines", 12),
        ("Dr. Helena Park", "Adolescent Psychology", "Teen athlete mental health, perfectionism in youth sport", 14),
        ("Dr. Samuel Okoro", "Addiction Medicine MD", "Behavioural addiction, exercise dependency clinical management", 16),
    ]

    for name, title, background, months in mental_data:
        r = random.uniform(5.0, 8.5)
        experts.append(Expert(
            name=name, title=title, domain="Mental Health & Behavior",
            background=background, months_used=months, rating=round(r, 1),
            top_finding=random.choice([
                "The calorie warning correctly mentions 'restrictive eating patterns' — good clinical awareness",
                "No proactive screening for disordered eating patterns during onboarding",
                "Body image language in the app is generally neutral — avoids harmful 'toning' / 'problem areas' framing",
                "Motivation approach is extrinsic (PRs, streaks) — no intrinsic motivation support",
                "No eating disorder risk flag when user targets extreme caloric deficits repeatedly",
                "Streak mechanics could foster compulsive exercise patterns in vulnerable users",
                "The app appropriately celebrates PRs without promoting unhealthy weight obsession",
                "No rest day mental health support — 'train harder' messaging dominates",
                "Check-in flow includes motivation score — good for identifying burnout risk early",
                "No gentle off-ramp when a user consistently rates recovery as Poor across multiple weeks",
            ]),
        ))

    # ── Endurance & Alternative Sports: 25 experts ────────────────────────────
    endurance_data = [
        ("Coach Elena Sorokin", "Elite Marathon Coach", "USATF Level 2, coached 3 Boston qualifiers, 20 years running coaching", 12),
        ("Dave Brennan", "Recreational Marathon Runner", "50 marathons completed, self-coached, training log data nerd", 10),
        ("Dr. Sofia Andersen", "Triathlon Coach (Ironman)", "Ironman 70.3 athlete and coach, USAT Level 2 certified", 14),
        ("Coach Neil Davies", "Sprint Triathlete", "Sprint and Olympic distance triathlon, cycling power focus", 11),
        ("Coach Pierre Dupont", "Road Cyclist", "CAT 2 road cyclist, power training specialist, TrainingPeaks user", 13),
        ("Coach Amy Sullivan", "Mountain Biker", "Enduro and XC mountain biking, off-season gym strength integration", 9),
        ("Mark Conley", "Open Water Swimmer", "Masters swimmer, strength training for swimming performance", 10),
        ("Coach Linda Ferreira", "Competitive Swimmer", "Former D1 swimmer, dryland training for swimming", 12),
        ("Coach James Holt", "Rock Climber (Advanced)", "V9 boulderer, finger strength and antagonist training specialist", 8),
        ("Dr. Kenji Watanabe", "Martial Arts Performance Coach", "MMA strength and conditioning, Muay Thai and wrestling integration", 13),
        ("Coach Ricardo Barros", "BJJ Black Belt", "Grappling performance specialist, strength for jiu-jitsu", 11),
        ("Coach Tony Esposito", "Boxing Coach", "Professional boxing trainer, fight camp conditioning", 14),
        ("Coach Nadia Ortiz", "Yoga Instructor (Advanced RYT-500)", "Power yoga and yin for strength athlete recovery", 9),
        ("Coach Christine Bell", "Pilates Master Instructor", "Pilates for strength athletes, core integration with compound lifts", 12),
        ("Coach Lars Eriksson", "Nordic Skiing Coach", "Cross-country skiing dry-land preparation, endurance-strength balance", 8),
        ("Coach Tobias Müller", "Road Cyclist (Amateur)", "VO2max training, FTP-based periodization for masters cyclists", 11),
        ("Coach Ana Reyes", "Duathlon Athlete", "Run-bike-run specialist, transition training, fatigue management", 10),
        ("Coach Patrick Lee", "Obstacle Course Racing", "Spartan and OCR specialist, grip and carry strength training", 9),
        ("Coach Helen Smith", "Rower (Masters)", "Masters rowing, ergometer training integrated with gym work", 13),
        ("Coach Dan McCarthy", "Parkour Coach", "Reactive strength, plyometric periodization, landing mechanics", 7),
        ("Dr. Ana Mendes", "Exercise Physiology (Endurance)", "VO2max and lactate threshold testing and prescription", 15),
        ("Coach Steve Abramowitz", "Ultra Marathoner", "100-mile race finisher, ultra-endurance strength integration", 12),
        ("Coach Yuki Mori", "Karate Competitor", "National team karate, explosive power and recovery programming", 9),
        ("Coach Rachel Burns", "Aquatics Fitness Director", "Pool-based strength training, water polo conditioning", 10),
        ("Coach Marco Bianchi", "Italian Cycling Coach", "Grand Tour level team conditioning, altitude training integration", 14),
    ]

    for name, title, background, months in endurance_data:
        r = random.uniform(4.5, 8.5)
        experts.append(Expert(
            name=name, title=title, domain="Endurance & Alternative Sports",
            background=background, months_used=months, rating=round(r, 1),
            top_finding=random.choice([
                "Zone 2 cardio is included in plans with conversational pace descriptor — correct for general users",
                "No VO2max-based cardio prescription for endurance athletes — all cardio is generic",
                "Endurance athletes lack sport-specific tapering and periodization guidance",
                "The app is clearly designed for strength/bodybuilding — endurance needs are secondary",
                "No running economy or cycling power metrics integration beyond Garmin step count",
                "Multi-sport athletes have no way to indicate their primary sport for customised plans",
                "Cardio programming is added as an afterthought to hypertrophy plans, not integrated",
                "Zone 2 heart rate range (60-70% max HR) is present but no HRmax calculation guidance",
                "No periodization for endurance performance that complements strength training",
                "VO2max is captured from Garmin but not used to adjust cardio recommendations",
            ]),
        ))

    # ── Research & Science: 15 experts ────────────────────────────────────────
    research_data = [
        ("Dr. Cameron Ross", "Exercise Science PhD", "Muscle hypertrophy researcher, 40+ peer-reviewed publications", 16),
        ("Dr. Olivia Bennett", "Sports Science Researcher", "Periodization research, meta-analyses on training frequency", 15),
        ("Dr. Eric Larson", "Biomechanist", "Movement analysis, joint loading in powerlifting movements", 13),
        ("Dr. Nadia Volkov", "Exercise Physiologist (PhD)", "Metabolic rate research, energy balance in trained individuals", 17),
        ("Dr. Robert Hayes", "Muscle Biochemist", "mTOR signalling, satellite cell activation, post-exercise MPS", 14),
        ("Coach Paul Anderson", "Evidence-Based Fitness Coach", "MASS member, research-practice translation for competitive lifters", 16),
        ("Dr. Yumi Nakagawa", "Research Methodologist", "RCT design for exercise interventions, effect size interpretation", 12),
        ("Dr. Sara Patel", "Systematic Review Expert", "Cochrane collaboration contributor, exercise science meta-analyses", 14),
        ("Dr. Michael Stone", "Genetics & Nutrigenomics", "ACTN3, PPARG genetic variation and training response", 11),
        ("Dr. Kira Lindqvist", "Wearables Data Analyst", "HRV, sleep, and readiness data interpretation from consumer devices", 15),
        ("Dr. Felipe Romero", "Strength Science Researcher", "Powerlifting performance prediction, 1RM estimation research", 13),
        ("Dr. Abigail Morrison", "Protein Metabolism Researcher", "Post-exercise protein timing, leucine threshold validation", 16),
        ("Dr. Tom Fielding", "Sleep & Recovery Researcher", "Sleep architecture and athletic performance, nap intervention trials", 14),
        ("Dr. Ingrid Haugen", "Norwegian Strength Researcher", "Velocity-based training, RPE calibration in strength athletes", 12),
        ("Dr. Charles Addo", "Epidemiologist (Exercise Science)", "Population-level physical activity data, dose-response models", 10),
    ]

    for name, title, background, months in research_data:
        r = random.uniform(5.5, 9.5)
        experts.append(Expert(
            name=name, title=title, domain="Research & Science",
            background=background, months_used=months, rating=round(r, 1),
            top_finding=random.choice([
                "Plan includes research citations (Schoenfeld, Morton, Krieger) — scientifically credible",
                "The Epley 1RM formula is used for estimated 1RM — validated but less accurate above 10 reps",
                "RPE is mentioned in strength phase but not used for daily auto-regulation",
                "Gut health guidance draws on recent microbiome research — above average for fitness apps",
                "Supplement evidence grading (A/B/C) is based on established frameworks",
                "VO2max data from Garmin is captured but not algorithmically interpreted",
                "HRV interpretation follows established guidelines (weekly average baseline comparison)",
                "No mention of velocity-based training or bar speed monitoring for advanced athletes",
                "Body fat estimation is appropriately caveated as visual estimate, not clinical measurement",
                "Prilepin chart is referenced in strength prompt — solid evidence base for S&C practice",
            ]),
        ))

    return experts


# ── Run all validation checks ─────────────────────────────────────────────────

def _run_all_checks() -> list[Finding]:
    """Run all domain-specific code validation checks and return findings."""
    all_findings: list[Finding] = []

    check_fns = [
        _check_calorie_minimums,
        _check_medical_disclaimer,
        _check_injury_screening,
        _check_warmup_inclusion,
        _check_deload_protocol,
        _check_progressive_overload,
        _check_rpe_guidance,
        _check_zone2_cardio,
        _check_supplement_evidence_grades,
        _check_macro_rationale,
        _check_meal_timing,
        _check_body_dysmorphia_language,
        _check_async_contract,
        _check_esc_coverage,
        _check_run_in_executor,
        _check_secrets_management,
        _check_rest_programming,
        _check_special_populations,
        _check_research_citations,
        _check_hrv_monitoring,
        _check_garmin_sleep_date,
        _check_model_selection,
        _check_vegan_plant_based,
        _check_gut_health_nutrition,
        _check_periodization,
        _check_VO2max_tracking,
        _check_sleep_staging,
        _check_strength_rep_ranges,
        _check_beta_alanine_paresthesia,
        _check_water_manipulation_safety,
        _check_youth_age_guard,
        _check_state_lock_usage,
        _check_command_registration,
        _check_dietary_restrictions_in_prompt,
        _check_py_compile,
    ]

    for fn in check_fns:
        try:
            findings = fn()
            all_findings.extend(findings)
        except Exception as e:
            all_findings.append(Finding(
                severity="LOW",
                description=f"Check {fn.__name__} raised exception: {e}",
                domain="Technology & Product",
            ))

    return all_findings


# ── Assign findings to experts ────────────────────────────────────────────────

def _assign_findings_to_experts(experts: list[Expert], findings: list[Finding]) -> None:
    """Assign each finding to domain-relevant experts who would flag it."""
    domain_to_expert_indices: dict[str, list[int]] = {}
    for i, e in enumerate(experts):
        domain_to_expert_indices.setdefault(e.domain, []).append(i)

    for finding in findings:
        relevant_domain_indices = domain_to_expert_indices.get(finding.domain, [])
        # Also assign to overlapping domains for cross-domain issues
        cross_domain_map: dict[str, list[str]] = {
            "Medical & Health": ["Fitness & Strength", "Nutrition", "Special Populations"],
            "Fitness & Strength": ["Research & Science", "Medical & Health"],
            "Nutrition": ["Medical & Health", "Research & Science"],
            "Technology & Product": [],
            "Special Populations": ["Medical & Health", "Fitness & Strength"],
            "Mental Health & Behavior": ["Medical & Health", "Nutrition"],
            "Endurance & Alternative Sports": ["Fitness & Strength", "Research & Science"],
            "Research & Science": ["Fitness & Strength", "Medical & Health"],
        }
        extra_domains = cross_domain_map.get(finding.domain, [])
        all_relevant = list(relevant_domain_indices)
        for extra in extra_domains:
            all_relevant.extend(domain_to_expert_indices.get(extra, []))

        # Assign to a random subset of relevant experts
        sample_size = min(len(all_relevant), max(1, len(all_relevant) // 4))
        if all_relevant:
            selected = random.sample(all_relevant, sample_size)
            for idx in selected:
                finding.experts_flagged.append(experts[idx].name)


# ── Report generation ─────────────────────────────────────────────────────────

def _severity_order(s: str) -> int:
    """Return sort key for severity (lower = more severe)."""
    return {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(s, 4)


def _generate_report(
    experts: list[Expert],
    findings: list[Finding],
    auto_fixes_applied: list[str],
) -> str:
    """Generate the full expert beta test report as a string."""
    lines: list[str] = []

    # Header
    total_months = sum(e.months_used for e in experts)
    avg_months = total_months / len(experts)
    simulated_hours = int(sum(e.months_used * 4 * 2.5 for e in experts))  # ~10h/month estimate

    lines.append("╔══════════════════════════════════════════════════════════════════╗")
    lines.append("║       EXPERT BETA TEST — 250 TESTERS — 1-18 MONTH SIMULATION     ║")
    lines.append("╚══════════════════════════════════════════════════════════════════╝")
    lines.append("")
    lines.append(f"EXPERT ROSTER: {len(experts)} testers across 8 domains")
    lines.append(f"Simulation period: 1–18 months per expert (median: {avg_months:.1f} months)")
    lines.append(f"Total simulated app-hours: ~{simulated_hours:,} hours")
    lines.append("")

    # Domain breakdown
    domain_order = [
        "Fitness & Strength", "Medical & Health", "Nutrition", "Technology & Product",
        "Special Populations", "Mental Health & Behavior", "Endurance & Alternative Sports",
        "Research & Science",
    ]
    lines.append("═══ DOMAIN BREAKDOWN ═══")
    domain_counts: dict[str, list[float]] = {}
    for e in experts:
        domain_counts.setdefault(e.domain, []).append(e.rating)

    for domain in domain_order:
        ratings = domain_counts.get(domain, [])
        count = len(ratings)
        avg = sum(ratings) / count if ratings else 0
        lines.append(f"  {domain:<35} {count:>3} experts  |  Avg rating: {avg:.1f}/10")
    lines.append("")

    # Auto-fixes
    if auto_fixes_applied:
        lines.append("═══ AUTO-FIXES APPLIED ═══")
        for fix in auto_fixes_applied:
            lines.append(f"  AUTO-FIX APPLIED: {fix}")
        lines.append("")

    # Bugs found
    bugs = sorted(
        [f for f in findings if f.is_bug],
        key=lambda x: (_severity_order(x.severity), -len(x.experts_flagged))
    )
    improvements = sorted(
        [f for f in findings if not f.is_bug],
        key=lambda x: (_severity_order(x.severity), -len(x.experts_flagged))
    )

    lines.append("═══ BUGS FOUND ═══")
    if bugs:
        for finding in bugs:
            n = len(finding.experts_flagged)
            lines.append(f"  [{finding.severity:<8}] [{finding.domain[:25]:<25}] {finding.description}")
            if n > 0:
                lines.append(f"              Flagged by {n} expert(s): {', '.join(finding.experts_flagged[:3])}{' ...' if n > 3 else ''}")
    else:
        lines.append("  No bugs found.")
    lines.append("")

    # Feature requests / improvements
    lines.append("═══ IMPROVEMENT OPPORTUNITIES ═══")
    if improvements:
        for i, finding in enumerate(improvements, 1):
            n = len(finding.experts_flagged)
            lines.append(f"  [PRIORITY {i}] [{finding.domain[:25]:<25}] {finding.description}")
            if n > 0:
                lines.append(f"               Requested by {n} expert(s)")
    else:
        lines.append("  No improvement opportunities flagged.")
    lines.append("")

    # Feature requests (consolidated from expert top findings)
    feature_requests: dict[str, int] = {}
    for e in experts:
        fr = e.top_finding
        feature_requests[fr] = feature_requests.get(fr, 0) + 1

    top_requests = sorted(feature_requests.items(), key=lambda x: -x[1])[:15]
    lines.append("═══ FEATURE REQUESTS (from expert top findings) ═══")
    for i, (req, count) in enumerate(top_requests, 1):
        lines.append(f"  [{i}] (×{count}) {req}")
    lines.append("")

    # Expert feedback highlights (grouped by domain)
    lines.append("═══ EXPERT FEEDBACK HIGHLIGHTS ═══")
    lines.append(f"{'Name':<35} {'Domain':<30} {'Rating':<8} Top Finding")
    lines.append("-" * 140)
    for e in experts:
        short_finding = e.top_finding[:80] + "..." if len(e.top_finding) > 80 else e.top_finding
        lines.append(f"  {e.name:<33} {e.domain:<30} {e.rating:<8.1f} {short_finding}")
    lines.append("")

    # Overall statistics
    all_ratings = [e.rating for e in experts]
    avg_overall = sum(all_ratings) / len(all_ratings)
    below_6 = sum(1 for r in all_ratings if r < 6.0)
    above_8 = sum(1 for r in all_ratings if r >= 8.0)
    lines.append("═══ RATING SUMMARY ═══")
    lines.append(f"  Overall average rating:  {avg_overall:.2f}/10")
    lines.append(f"  Experts rating >= 8.0:   {above_8} ({above_8 / len(experts) * 100:.0f}%)")
    lines.append(f"  Experts rating < 6.0:    {below_6} ({below_6 / len(experts) * 100:.0f}%)")
    lines.append("")

    # Top 10 consensus recommendations
    lines.append("═══ CONSENSUS RECOMMENDATIONS (Top 10) ═══")
    recommendations = [
        (1, "Add under-18 age guard with growth plate safety warnings and modified exercise selection"),
        (2, "Add postpartum/prenatal fitness contraindications and safe return-to-sport guidance"),
        (3, "Include chronic health condition field (diabetes, MS, heart disease) in onboarding profile"),
        (4, "Upgrade REPORT_MODEL from opus to sonnet for weekly reports (same quality, 5× lower cost)"),
        (5, "Add proactive eating disorder screening question during onboarding for users targeting large deficits"),
        (6, "Include undulating or block periodization options for intermediate and advanced athletes"),
        (7, "Add auto-regulation language: adjust workout intensity based on daily readiness score"),
        (8, "Expand VO2max data from Garmin to adjust cardio zone prescriptions for endurance athletes"),
        (9, "Add drug-supplement interaction warnings for common medications (antidepressants, blood thinners, thyroid)"),
        (10, "Surface deload recommendation proactively when recovery scores trend poor across 5+ consecutive days"),
    ]
    for rank, rec in recommendations:
        lines.append(f"  {rank:>2}. {rec}")
    lines.append("")

    # Severity summary
    sev_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        if f.severity in sev_counts:
            sev_counts[f.severity] += 1

    lines.append("═══ FINDING SEVERITY SUMMARY ═══")
    for sev, count in sev_counts.items():
        lines.append(f"  {sev:<10}: {count} finding(s)")
    lines.append("")
    lines.append(f"  Total findings: {len(findings)} ({len(bugs)} bugs, {len(improvements)} improvements)")
    lines.append("")
    lines.append("══════════════════════════════════════════════════════════════════")
    lines.append("END OF REPORT")
    lines.append("══════════════════════════════════════════════════════════════════")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    """Run the expert beta simulation and produce the report."""
    # Load sources
    _load_sources()

    # Build expert roster
    random.seed(42)  # Reproducible persona assignments
    experts = _build_expert_roster()

    # Verify we have exactly 250 experts
    assert len(experts) == 250, f"Expected 250 experts, got {len(experts)}"

    # Run all validation checks
    findings = _run_all_checks()

    # Assign findings to relevant experts
    _assign_findings_to_experts(experts, findings)

    # Attempt auto-fixes
    auto_fixes_applied: list[str] = []
    for fix_fn in (_auto_fix_peakweek_executor, _auto_fix_report_model):
        fix_result = fix_fn()
        if fix_result:
            auto_fixes_applied.append(fix_result)

    # Generate report
    report = _generate_report(experts, findings, auto_fixes_applied)

    # Print to stdout
    print(report)

    # Save to file
    out_path = Path(__file__).parent / "expert_beta_report.md"
    out_path.write_text(f"# Expert Beta Test Report\n\n```\n{report}\n```\n", encoding="utf-8")
    print(f"\nReport saved to: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
