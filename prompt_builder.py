"""prompt_builder — centralised prompt construction for all Claude API calls.

Every string sent to the Anthropic API as a system prompt or user-turn message
should be assembled here. No other module should build multi-paragraph prompts
inline. This module has no side effects.

Phase 3: stubs only. Phase 4 migrated context_block, goal_system_prompt, and
bot_json_context_block. Remaining stubs will be migrated in later phases.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coach_brain import CoachContext, GarminSnapshot

__all__ = [
    "goal_system_prompt",
    "context_block",
    "bot_json_context_block",
    "plan_prompt",
    "checkin_prompt",
    "overload_prompt",
    "report_prompt",
    "weak_points_prompt",
    "research_filter",
]

# ── Goal-specific coaching persona prompts ────────────────────────────────────
# Mirrors the dict in claude_service._GOAL_SYSTEM_PROMPTS. Defined here so
# prompt_builder is the canonical location; claude_service keeps its own copy
# until a later consolidation refactor.

_GOAL_PROMPTS: dict[str, str] = {
    "bulk": (
        "You are coaching an athlete in a BULK phase. "
        "Emphasize progressive overload above all else. "
        "Flag immediately if body weight is not rising — suggest caloric increases of 150–200 kcal/day. "
        "Push protein and carbohydrate compliance. "
        "Prioritize compound lifts and volume accumulation. "
        "Celebrate strength PRs. Deload warnings only when joints are compromised."
    ),
    "cut": (
        "You are coaching an athlete in a CUT phase. "
        "Caloric deficit adherence is the top priority — flag any days over target. "
        "Watch for excessive soreness (muscle loss risk) and recommend deloads proactively. "
        "Emphasize high protein to protect lean mass. "
        "Suggest adding 20 min steady-state cardio if weight loss stalls for 10+ days. "
        "Reinforce that strength maintenance, not gain, is success during a cut."
    ),
    "recomp": (
        "You are coaching an athlete in a RECOMPOSITION phase. "
        "Balance training intensity and nutrition compliance equally. "
        "Prefer training consistency over maximum volume. "
        "Flag macro cycling opportunities — higher carbs on training days, lower on rest days. "
        "Progress is slower than dedicated bulk/cut; frame this as normal and expected. "
        "Weekly body measurements matter more than scale weight for tracking recomp."
    ),
    "strength": (
        "You are coaching an ATHLETE prioritizing STRENGTH gains. "
        "PRs on the main lifts (squat, bench, deadlift, overhead press) are the primary success metric. "
        "Use Prilepin chart rep ranges (1–6 reps, 70–90% 1RM). "
        "Flag HRV drops >15% from 7-day average as neural fatigue risk. "
        "Recommend longer rest periods (3–5 min) and lower daily rep volume. "
        "Nutrition supports performance — keep protein high, carbs timed around sessions."
    ),
    "prep": (
        "You are coaching an athlete in COMPETITION PREP (show prep). "
        "Strict caloric and macro adherence is non-negotiable. "
        "Weekly conditioning assessments are critical. "
        "Count down to show date; flag if current trajectory won't hit target conditioning. "
        "Posing practice is as important as training — recommend 15 min/day. "
        "In peak week, adjust carb cycling and water manipulation guidance."
    ),
    "beginner": (
        "You are coaching a BEGINNER athlete. "
        "Use simple, jargon-free language. Explain the 'why' behind every recommendation. "
        "Celebrate every PR and milestone — early wins build the habit. "
        "Prioritize movement quality and consistency over intensity. "
        "Keep nutrition advice simple: hit protein target, eat mostly whole foods. "
        "Never overwhelm with too many changes at once — one adjustment at a time."
    ),
}

_DEFAULT_PROMPT = (
    "You are an elite strength coach and sports nutritionist with 20+ years of experience "
    "coaching competitive physique athletes. Be specific, evidence-based, and actionable."
)


def goal_system_prompt(goal: str | None, version: str = "v1") -> str:
    """Return the coaching persona system prompt for the given goal.

    Args:
        goal: Athlete goal — "bulk", "cut", "recomp", "strength", "prep",
              "beginner", or None (falls back to default).
        version: Prompt version tag for A/B tracking (default "v1").

    Returns:
        System prompt string to pass as the ``system`` parameter to the API.
    """
    if not goal:
        return _DEFAULT_PROMPT
    return _GOAL_PROMPTS.get(goal.lower(), _DEFAULT_PROMPT)


def context_block(ctx: "CoachContext") -> str:
    """Serialise a CoachContext into a compact text block for prompt injection.

    Assembles sub-blocks for athlete profile, recent training, recovery,
    PRs, active goals, and last body analysis. Total length is capped at
    6 000 characters by truncating older records first.

    Args:
        ctx: Populated CoachContext dataclass from coach_brain.build_context().

    Returns:
        Plain-text string ready to prepend to any system or user-turn prompt.
    """
    lines: list[str] = []
    p = ctx.profile

    weight_str = f"{p.weight_kg}kg" if p.weight_kg else "?"
    lines.append(
        f"Profile: goal={p.goal or '?'}, experience={p.experience or '?'}, "
        f"age={p.age or '?'}, weight={weight_str}"
    )

    total_sets = sum(len(s.sets) for s in ctx.recent_sessions)
    total_vol = sum(s.total_volume_kg for s in ctx.recent_sessions)
    lines.append(
        f"Training 7d: {len(ctx.recent_sessions)} sessions, {total_sets} sets, "
        f"{total_vol:,.0f}kg total volume"
    )

    if ctx.recent_checkins:
        avg_rec = sum(c.recovery_score for c in ctx.recent_checkins) / len(ctx.recent_checkins)
        avg_slp = sum(c.sleep_score for c in ctx.recent_checkins) / len(ctx.recent_checkins)
        avg_sor = sum(c.soreness_score for c in ctx.recent_checkins) / len(ctx.recent_checkins)
        lines.append(
            f"Recovery 7d: avg score={avg_rec:.0f}/100, "
            f"avg sleep={avg_slp:.1f}/10, avg soreness={avg_sor:.1f}/10"
        )
    else:
        lines.append("Recovery 7d: no check-ins this week")

    if ctx.recent_meals:
        avg_protein = sum(m.protein_g for m in ctx.recent_meals) / len(ctx.recent_meals)
        lines.append(f"Avg daily protein 7d: {avg_protein:.0f}g")

    if ctx.prs:
        top = sorted(ctx.prs.values(), key=lambda x: x.estimated_1rm, reverse=True)[:5]
        prs_text = ", ".join(f"{pr.exercise} {pr.weight_kg}kg×{pr.reps}" for pr in top)
        lines.append(f"Top PRs: {prs_text}")

    active_goals = [g for g in ctx.goals if g.is_active]
    if active_goals:
        g = active_goals[0]
        parts = [f"type={g.goal_type}"]
        if g.target_weight_kg:
            parts.append(f"target={g.target_weight_kg}kg")
        if g.target_date:
            parts.append(f"by={g.target_date}")
        lines.append(f"Active goal: {', '.join(parts)}")

    if ctx.last_analysis:
        a = ctx.last_analysis
        score_str = f", score={a.overall_physique_score}/10" if a.overall_physique_score else ""
        lines.append(f"Last analysis: {a.body_fat_estimate or '?'} BF est.{score_str}")

    if ctx.garmin_today:
        g = ctx.garmin_today
        garmin_parts = []
        if g.hrv_ms:
            garmin_parts.append(f"HRV={g.hrv_ms:.0f}ms")
        if g.resting_hr_bpm:
            garmin_parts.append(f"RHR={g.resting_hr_bpm}bpm")
        if g.sleep_hrs:
            garmin_parts.append(f"sleep={g.sleep_hrs:.1f}h")
        if garmin_parts:
            lines.append(f"Garmin today: {', '.join(garmin_parts)}")

    result = "\n".join(lines)
    if len(result) > 6000:
        result = result[:5997] + "..."
    return result


def bot_json_context_block(user_data: dict) -> str:
    """Assemble a rich context string from bot JSON user data for AI prompts.

    Migrated from claude_service.build_rich_context() which was dead code.
    Used by Telegram bot paths where athlete data lives in bot_state.json
    rather than SQLite.

    Args:
        user_data: The user dict from bot_state.json (get_user(chat_id)).

    Returns:
        Plain-text context block string, or empty string if no data.
    """
    profile = user_data.get("profile") or {}
    set_logs = user_data.get("set_logs") or []
    checkins = user_data.get("checkins") or []
    meal_logs = user_data.get("meal_logs") or []
    measurements = user_data.get("measurements") or []
    prs = user_data.get("prs") or {}
    session_counter = user_data.get("session_counter", 0)

    today = date.today()
    seven_days_ago = str(today - timedelta(days=7))
    thirty_days_ago = str(today - timedelta(days=30))

    recent_sets = [s for s in set_logs if s.get("date", "") >= seven_days_ago]
    session_ids_7d = {s.get("session_id") for s in recent_sets if s.get("session_id") is not None}
    volume_7d = sum(s.get("weight_kg", 0) * s.get("reps", 0) for s in recent_sets)

    recent_checkins = [c for c in checkins if c.get("date", "") >= seven_days_ago]
    avg_recovery = (
        round(sum(c.get("recovery_score", 0) for c in recent_checkins) / len(recent_checkins))
        if recent_checkins else None
    )
    avg_sleep = (
        round(sum(c.get("sleep_score", 0) for c in recent_checkins) / len(recent_checkins), 1)
        if recent_checkins else None
    )
    avg_soreness = (
        round(sum(c.get("soreness_score", 0) for c in recent_checkins) / len(recent_checkins), 1)
        if recent_checkins else None
    )

    recent_weights = [
        (m.get("date", ""), m.get("body_weight_kg"))
        for m in measurements
        if m.get("body_weight_kg") and m.get("date", "") >= thirty_days_ago
    ]
    weight_change_30d = None
    if len(recent_weights) >= 2:
        sorted_w = sorted(recent_weights, key=lambda x: x[0])
        weight_change_30d = round(sorted_w[-1][1] - sorted_w[0][1], 1)
    latest_weight = recent_weights[-1][1] if recent_weights else profile.get("weight")

    protein_target = None
    plan = user_data.get("last_plan") or {}
    if plan:
        protein_target = plan.get("diet", {}).get("protein_g")
    recent_meals_by_day: dict[str, float] = {}
    for m in meal_logs:
        d = m.get("date", "")
        if d >= seven_days_ago:
            recent_meals_by_day[d] = recent_meals_by_day.get(d, 0) + (m.get("protein_g") or 0)
    protein_compliance_pct = None
    if protein_target and recent_meals_by_day:
        days_hit = sum(1 for p in recent_meals_by_day.values() if p >= protein_target * 0.9)
        protein_compliance_pct = round(days_hit / max(len(recent_meals_by_day), 1) * 100)

    top_prs = sorted(prs.items(), key=lambda x: x[1].get("estimated_1rm", 0), reverse=True)[:5]
    prs_text = (
        ", ".join(f"{ex} {v['weight_kg']}kg×{v['reps']}" for ex, v in top_prs)
        if top_prs else "none"
    )

    lines = [
        f"Profile: goal={profile.get('goal', '?')}, experience={profile.get('experience', '?')}, "
        f"age={profile.get('age', '?')}",
        f"Training 7d: {len(session_ids_7d)} sessions, {len(recent_sets)} sets, "
        f"{volume_7d:,.0f}kg total volume",
    ]
    if avg_recovery is not None:
        lines.append(
            f"Recovery 7d: avg score={avg_recovery}/100, avg sleep={avg_sleep}/10, "
            f"avg soreness={avg_soreness}/10"
        )
    else:
        lines.append("Recovery 7d: no check-ins this week")

    if weight_change_30d is not None:
        lines.append(f"Weight: current={latest_weight}kg, 30d change={weight_change_30d:+.1f}kg")
    elif latest_weight:
        lines.append(f"Weight: current={latest_weight}kg")

    if protein_compliance_pct is not None:
        lines.append(f"Protein compliance 7d: {protein_compliance_pct}% of days hit target")
    else:
        lines.append("Protein: not tracked this week")

    lines.append(f"Total sessions logged: {session_counter}")
    lines.append(f"Top PRs: {prs_text}")

    return "\n".join(lines)


# ── Remaining stubs (Phase 5+) ────────────────────────────────────────────────

def plan_prompt(ctx: "CoachContext", days: int, research: dict[str, str]) -> str:
    """Full user-turn prompt for comprehensive plan generation.

    Args:
        ctx: Populated CoachContext.
        days: Requested training days per week (2-6).
        research: Dict of {topic: summary} from research_service, pre-filtered
                  by research_filter() for the athlete's goal.

    Returns:
        User-turn message string for generate_comprehensive_plan().
    """
    raise NotImplementedError("Phase 5: migrate _build_plan_prompt() from telegram_bot.py")


def checkin_prompt(scores: dict[str, int], garmin: "GarminSnapshot | None") -> str:
    """Prompt for recovery insight generation.

    Args:
        scores: Dict with keys sleep, energy, soreness, stress (each 1-10).
        garmin: Optional Garmin data snapshot for HRV/RHR injection.

    Returns:
        User-turn message string for generate_recovery_insight().
    """
    raise NotImplementedError("Phase 5")


def overload_prompt(ctx: "CoachContext", exercise: str) -> str:
    """Prompt for progressive overload suggestion for a specific exercise.

    Args:
        ctx: Populated CoachContext (recent set history extracted internally).
        exercise: Canonical exercise name, e.g. "Bench Press".

    Returns:
        User-turn message string for generate_progressive_overload_suggestion().
    """
    raise NotImplementedError("Phase 5")


def report_prompt(ctx: "CoachContext") -> str:
    """Full user-turn prompt for weekly report generation.

    Args:
        ctx: Populated CoachContext with at least 7 days of data.

    Returns:
        User-turn message string for generate_weekly_report().
    """
    raise NotImplementedError("Phase 5")


def weak_points_prompt(ctx: "CoachContext") -> str:
    """Prompt for weak-point analysis.

    Args:
        ctx: Populated CoachContext.

    Returns:
        User-turn message string for analyze_weak_points().
    """
    raise NotImplementedError("Phase 5")


def research_filter(research: dict[str, str], goal: str) -> dict[str, str]:
    """Return the subset of research topics relevant to the given goal.

    Args:
        research: Full research cache dict {topic: summary}.
        goal: Athlete goal string.

    Returns:
        Filtered subset of the input dict (same values, subset of keys).
    """
    raise NotImplementedError("Phase 5")
