"""coach_brain — single orchestration layer for all domain logic.

Both telegram_bot.py and main.py import from this module for all domain
operations. Neither transport layer implements business logic directly.

Dependency rule: coach_brain may import from claude_service, nutrition_service,
research_service, garmin_service, mfp_service, database, and prompt_builder.
It must NOT import from telegram_bot or main.

Phase 3: typed dataclass skeletons + stub async domain methods only.
Phase 4 will wire each method to the real services and DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


# ── Typed snapshot dataclasses ────────────────────────────────────────────────
# All snapshots are frozen (immutable). ORM objects never escape coach_brain —
# callers receive plain dataclasses, making unit testing straightforward and
# preventing accidental lazy-load N+1 queries.

@dataclass(frozen=True)
class UserProfileSnapshot:
    age: int | None
    gender: str | None
    height_cm: float | None
    weight_kg: float | None
    goal: str                  # "bulk" | "cut" | "recomp" | "strength" | "prep"
    experience: str            # "beginner" | "intermediate" | "advanced"
    days_per_week: int
    dietary_restrictions: str | None
    equipment: str | None
    injuries: str | None


@dataclass(frozen=True)
class SetSnapshot:
    exercise: str
    weight_kg: float
    reps: int
    estimated_1rm: float       # Epley — computed once here, not per caller


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: int
    session_date: date
    sets: tuple[SetSnapshot, ...]
    total_volume_kg: float


@dataclass(frozen=True)
class CheckInSnapshot:
    checkin_date: date
    sleep_score: int
    energy_score: int
    soreness_score: int
    stress_score: int
    recovery_score: int
    hrv_ms: float | None
    resting_hr_bpm: int | None


@dataclass(frozen=True)
class MealSnapshot:
    meal_date: date
    description: str
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float


@dataclass(frozen=True)
class PRSnapshot:
    exercise: str
    weight_kg: float
    reps: int
    estimated_1rm: float
    achieved_at: datetime


@dataclass(frozen=True)
class GoalSnapshot:
    goal_type: str
    target_weight_kg: float | None
    target_bf_pct: float | None
    target_date: date | None
    is_active: bool


@dataclass(frozen=True)
class GarminSnapshot:
    reading_date: date
    hrv_ms: float | None
    resting_hr_bpm: int | None
    sleep_hrs: float | None
    steps: int | None


@dataclass(frozen=True)
class BodyAnalysisSnapshot:
    body_fat_estimate: str | None
    overall_physique_score: float | None
    strengths: list[str]
    areas_to_improve: list[str]
    muscle_development: dict[str, str]
    coach_message: str | None


@dataclass(frozen=True)
class CoachContext:
    """Complete athlete context passed to every domain method and prompt builder."""
    user_id: int
    chat_id: int | None
    profile: UserProfileSnapshot
    last_analysis: BodyAnalysisSnapshot | None
    recent_sessions: tuple[SessionSnapshot, ...]    # last 7 days
    recent_checkins: tuple[CheckInSnapshot, ...]   # last 7 days
    recent_meals: tuple[MealSnapshot, ...]          # last 7 days
    prs: dict[str, PRSnapshot]                      # exercise → best PR
    goals: tuple[GoalSnapshot, ...]
    research_cache: dict[str, str]                  # topic → summary
    garmin_today: GarminSnapshot | None


# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SetResult:
    set_id: int
    is_pr: bool
    overload_suggestion: str | None


@dataclass(frozen=True)
class CheckInResult:
    recovery_score: int
    coaching_tip: str
    checkin_streak: int


@dataclass(frozen=True)
class PlateauSignal:
    signal_type: str      # "weight_stall" | "strength_stall" | "recovery_decline"
    exercise: str | None  # None for weight/recovery signals
    details: str


# ── Domain methods (stubs) ────────────────────────────────────────────────────

async def build_context(db, user_id: int, chat_id: int | None = None) -> CoachContext:
    """Load all user data from SQLite into a CoachContext snapshot.

    Args:
        db: SQLAlchemy session (FastAPI dependency or direct).
        user_id: Web user ID (0 if unauthenticated web request).
        chat_id: Telegram chat ID (None for web-only users).

    Returns:
        Fully populated CoachContext with last-7-day windows for training,
        checkins, meals, and current PRs/goals.
    """
    raise NotImplementedError("Phase 4")


async def generate_plan(ctx: CoachContext, days: int) -> dict:
    """Fetch research, build prompt, call Claude Opus, persist plan.

    Args:
        ctx: Populated CoachContext.
        days: Requested training days per week (2-6).

    Returns:
        PlanBundle dict with keys workout_plan, diet_plan, supplement_plan.
    """
    raise NotImplementedError("Phase 4")


async def run_checkin(
    ctx: CoachContext,
    scores: dict[str, int],
) -> CheckInResult:
    """Compute recovery score, call Claude Haiku for tip, persist check-in.

    Args:
        ctx: Populated CoachContext (used for Garmin injection and profile).
        scores: Dict with keys sleep, energy, soreness, stress (each 1-10).

    Returns:
        CheckInResult with deterministic score, AI tip, and updated streak.
    """
    raise NotImplementedError("Phase 4")


async def log_set(
    ctx: CoachContext,
    session_id: int,
    exercise: str,
    weight_kg: float,
    reps: int,
) -> SetResult:
    """Persist a set, detect PRs, and generate overload suggestion.

    Args:
        ctx: Populated CoachContext.
        session_id: Active workout session ID.
        exercise: Canonical exercise name.
        weight_kg: Weight in kilograms (storage always in kg).
        reps: Rep count.

    Returns:
        SetResult with set_id, is_pr flag, and optional overload_suggestion.
    """
    raise NotImplementedError("Phase 4")


async def end_session(ctx: CoachContext, session_id: int) -> dict:
    """Close a workout session and return a summary.

    Args:
        ctx: Populated CoachContext.
        session_id: Session to close.

    Returns:
        Summary dict: total_sets, total_volume_kg, new_prs, next_session_targets.
    """
    raise NotImplementedError("Phase 4")


async def generate_report(ctx: CoachContext) -> dict:
    """Generate weekly coaching report via Claude Opus, persist to DB.

    Args:
        ctx: Populated CoachContext with at least 7 days of data.

    Returns:
        ReportBundle dict with deterministic metrics + AI narrative sections.
    """
    raise NotImplementedError("Phase 4")


async def analyze_weak_points(ctx: CoachContext) -> dict:
    """Compute volume-per-muscle and call Claude Haiku for weak-point analysis.

    Args:
        ctx: Populated CoachContext.

    Returns:
        WeakPointAnalysis dict: weak_muscles, volume_gaps, exercise_prescriptions,
        frequency_adjustment, priority_rank.
    """
    raise NotImplementedError("Phase 4")


async def detect_plateau(ctx: CoachContext) -> PlateauSignal | None:
    """Run all plateau detection algorithms against recent data.

    Checks weight stall (14-day regression), strength stall (per-exercise,
    last 4 sessions), and recovery decline (7-day vs prior 7-day average).

    Args:
        ctx: Populated CoachContext.

    Returns:
        PlateauSignal if any signal detected, else None.
    """
    raise NotImplementedError("Phase 4")


async def suggest_overload(ctx: CoachContext, exercise: str) -> str:
    """Return a progressive overload suggestion for the given exercise.

    Uses deterministic rules for 80% of cases; calls Claude Haiku only for
    complex situations (deload cycles, long stalls, injury flags).

    Args:
        ctx: Populated CoachContext.
        exercise: Canonical exercise name.

    Returns:
        Human-readable suggestion string, e.g. "Try 102.5kg × 5 next session".
    """
    raise NotImplementedError("Phase 4")


async def chat(
    ctx: CoachContext,
    message: str,
    history: list[dict[str, str]],
) -> str:
    """Send a conversational message to the AI coach.

    Args:
        ctx: Populated CoachContext injected as system-prompt context block.
        message: Latest user message.
        history: Prior conversation turns as list of {role, content} dicts.

    Returns:
        Assistant reply string.
    """
    raise NotImplementedError("Phase 4")
