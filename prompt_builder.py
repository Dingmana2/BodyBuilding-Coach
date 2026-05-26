"""prompt_builder — centralised prompt construction for all Claude API calls.

Every string sent to the Anthropic API as a system prompt or user-turn message
should be assembled here. No other module should build multi-paragraph prompts
inline. This module has no side effects and no imports from project modules —
it is pure string construction.

Phase 3: stubs only. Phase 4 will migrate logic from claude_service.py and
telegram_bot._build_plan_prompt() into these functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid circular imports at runtime; types used only for annotations.
    from coach_brain import CoachContext, GarminSnapshot

__all__ = [
    "goal_system_prompt",
    "context_block",
    "plan_prompt",
    "checkin_prompt",
    "overload_prompt",
    "report_prompt",
    "weak_points_prompt",
    "research_filter",
]


def goal_system_prompt(goal: str | None, version: str = "v1") -> str:
    """Return the coaching persona system prompt for the given goal.

    Args:
        goal: Athlete goal string — "bulk", "cut", "recomp", "strength", "prep",
              "beginner", or None (falls back to default).
        version: Prompt version tag for A/B tracking (default "v1").

    Returns:
        System prompt string to pass as the ``system`` parameter to the API.
    """
    raise NotImplementedError("Phase 4: migrate _GOAL_SYSTEM_PROMPTS from claude_service.py")


def context_block(ctx: "CoachContext") -> str:
    """Serialise a CoachContext into a structured markdown block for injection.

    Assembles sub-blocks for athlete profile, recent training, recovery, nutrition,
    PRs, active goals, and last body analysis. Total length is capped at ~6 000
    characters (~1 500 tokens) by truncating older records first.

    Args:
        ctx: Populated CoachContext dataclass from coach_brain.build_context().

    Returns:
        Markdown-formatted string ready to prepend to any user-turn prompt.
    """
    raise NotImplementedError("Phase 4: migrate build_rich_context() from claude_service.py")


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
    raise NotImplementedError("Phase 4: migrate _build_plan_prompt() from telegram_bot.py")


def checkin_prompt(scores: dict[str, int], garmin: "GarminSnapshot | None") -> str:
    """Prompt for recovery insight generation.

    Args:
        scores: Dict with keys sleep, energy, soreness, stress (each 1-10).
        garmin: Optional Garmin data snapshot for HRV/RHR injection.

    Returns:
        User-turn message string for generate_recovery_insight().
    """
    raise NotImplementedError("Phase 4")


def overload_prompt(ctx: "CoachContext", exercise: str) -> str:
    """Prompt for progressive overload suggestion for a specific exercise.

    Args:
        ctx: Populated CoachContext (recent set history extracted internally).
        exercise: Canonical exercise name, e.g. "Bench Press".

    Returns:
        User-turn message string for generate_progressive_overload_suggestion().
    """
    raise NotImplementedError("Phase 4")


def report_prompt(ctx: "CoachContext") -> str:
    """Full user-turn prompt for weekly report generation.

    Includes deterministic data block (sessions, PRs, nutrition, recovery averages)
    plus context_block(ctx). Claude fills in narrative sections only.

    Args:
        ctx: Populated CoachContext with at least 7 days of data.

    Returns:
        User-turn message string for generate_weekly_report().
    """
    raise NotImplementedError("Phase 4")


def weak_points_prompt(ctx: "CoachContext") -> str:
    """Prompt for weak-point analysis.

    Includes volume-per-muscle-group computed deterministically from set_logs,
    plus last body analysis highlights. Returns structured JSON schema for
    WeakPointAnalysis.

    Args:
        ctx: Populated CoachContext.

    Returns:
        User-turn message string for analyze_weak_points().
    """
    raise NotImplementedError("Phase 4")


def research_filter(research: dict[str, str], goal: str) -> dict[str, str]:
    """Return the subset of research topics relevant to the given goal.

    Bulk/hypertrophy goals → hypertrophy, progressive overload, protein synthesis.
    Cut goals → fat oxidation, muscle retention, HIIT.
    Prep goals → peak week, water manipulation, carb cycling.
    All other goals → all topics returned unchanged.

    Args:
        research: Full research cache dict {topic: summary}.
        goal: Athlete goal string.

    Returns:
        Filtered subset of the input dict (same values, subset of keys).
    """
    raise NotImplementedError("Phase 4")
