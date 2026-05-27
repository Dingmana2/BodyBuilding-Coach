"""coach_brain — single orchestration layer for all domain logic.

Both telegram_bot.py and main.py import from this module for all domain
operations. Neither transport layer implements business logic directly.

Dependency rule: coach_brain may import from claude_service, nutrition_service,
research_service, garmin_service, mfp_service, database, and prompt_builder.
It must NOT import from telegram_bot or main.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta


# ── Domain exception ──────────────────────────────────────────────────────────

class CoachBrainError(Exception):
    """Domain error with a user-safe message."""
    def __init__(self, user_message: str) -> None:
        self.user_message = user_message
        super().__init__(user_message)


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


# ── Private DB helpers ────────────────────────────────────────────────────────

def _empty_profile() -> UserProfileSnapshot:
    return UserProfileSnapshot(
        age=None, gender=None, height_cm=None, weight_kg=None,
        goal="", experience="", days_per_week=0,
        dietary_restrictions=None, equipment=None, injuries=None,
    )


def _load_profile(db, user_id: int, chat_id: int | None) -> UserProfileSnapshot:
    from models import User, UserProfile
    profile = None
    if user_id:
        profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    if not profile and chat_id:
        user_row = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user_row:
            profile = db.query(UserProfile).filter(UserProfile.user_id == user_row.id).first()
    if not profile:
        return _empty_profile()
    return UserProfileSnapshot(
        age=profile.age,
        gender=profile.gender,
        height_cm=profile.height_cm,
        weight_kg=profile.weight_kg,
        goal=profile.goal or "",
        experience=profile.training_experience or "",
        days_per_week=profile.training_days_per_week or 0,
        dietary_restrictions=profile.dietary_restrictions,
        equipment=profile.equipment_available,
        injuries=profile.injuries,
    )


def _load_sessions(db, user_id: int, chat_id: int | None, cutoff: str) -> list[SessionSnapshot]:
    from sqlalchemy import or_
    from models import WorkoutSession, SetLog
    filters = []
    if user_id:
        filters.append(WorkoutSession.user_id == user_id)
    if chat_id:
        filters.append(WorkoutSession.chat_id == chat_id)
    if not filters:
        return []
    sessions = db.query(WorkoutSession).filter(
        or_(*filters),
        WorkoutSession.started_at >= cutoff,
    ).all()
    result = []
    for s in sessions:
        sets_rows = db.query(SetLog).filter(SetLog.session_id == s.id).all()
        set_snaps = tuple(
            SetSnapshot(
                exercise=row.exercise_name,
                weight_kg=row.weight_kg,
                reps=row.reps,
                estimated_1rm=row.estimated_1rm or 0.0,
            )
            for row in sets_rows
        )
        total_vol = sum(ss.weight_kg * ss.reps for ss in set_snaps)
        result.append(SessionSnapshot(
            session_id=s.id,
            session_date=s.started_at.date() if s.started_at else date.today(),
            sets=set_snaps,
            total_volume_kg=round(total_vol, 1),
        ))
    return result


def _load_prs(db, chat_id: int | None) -> dict[str, PRSnapshot]:
    from models import PersonalRecord
    if not chat_id:
        return {}
    rows = db.query(PersonalRecord).filter(PersonalRecord.chat_id == chat_id).all()
    best: dict[str, PRSnapshot] = {}
    for r in rows:
        ex = r.exercise_name
        if ex not in best or r.estimated_1rm > best[ex].estimated_1rm:
            best[ex] = PRSnapshot(
                exercise=ex,
                weight_kg=r.weight_kg,
                reps=r.reps,
                estimated_1rm=r.estimated_1rm,
                achieved_at=r.achieved_at,
            )
    return best


def _load_checkins(db, chat_id: int | None, cutoff: str) -> list[CheckInSnapshot]:
    from models import DailyCheckIn
    if not chat_id:
        return []
    rows = (
        db.query(DailyCheckIn)
        .filter(DailyCheckIn.chat_id == chat_id, DailyCheckIn.date >= cutoff)
        .order_by(DailyCheckIn.date)
        .all()
    )
    return [
        CheckInSnapshot(
            checkin_date=date.fromisoformat(r.date),
            sleep_score=r.sleep_score or 0,
            energy_score=r.energy_score or 0,
            soreness_score=r.soreness_score or 0,
            stress_score=r.stress_score or 0,
            recovery_score=r.recovery_score or 0,
            hrv_ms=r.hrv_ms,
            resting_hr_bpm=r.resting_hr_bpm,
        )
        for r in rows
    ]


def _load_meals(db, chat_id: int | None, cutoff: str) -> list[MealSnapshot]:
    from models import MealLog
    if not chat_id:
        return []
    rows = (
        db.query(MealLog)
        .filter(MealLog.chat_id == chat_id, MealLog.date >= cutoff)
        .order_by(MealLog.date)
        .all()
    )
    return [
        MealSnapshot(
            meal_date=date.fromisoformat(r.date),
            description=r.description or "",
            calories=r.calories or 0,
            protein_g=r.protein_g or 0,
            carbs_g=r.carbs_g or 0,
            fat_g=r.fat_g or 0,
        )
        for r in rows
    ]


def _load_goals(db, chat_id: int | None) -> list[GoalSnapshot]:
    from models import UserGoal
    if not chat_id:
        return []
    rows = db.query(UserGoal).filter(
        UserGoal.chat_id == chat_id, UserGoal.is_active == True  # noqa: E712
    ).all()
    return [
        GoalSnapshot(
            goal_type=r.goal_type,
            target_weight_kg=r.target_weight_kg,
            target_bf_pct=r.target_bf_pct,
            target_date=r.target_date,
            is_active=r.is_active,
        )
        for r in rows
    ]


def _load_last_analysis(db) -> BodyAnalysisSnapshot | None:
    from models import BodyAnalysis
    row = db.query(BodyAnalysis).order_by(BodyAnalysis.created_at.desc()).first()
    if not row:
        return None
    strengths: list[str] = []
    areas: list[str] = []
    muscle: dict[str, str] = {}
    try:
        strengths = json.loads(row.strengths) if row.strengths else []
    except Exception:
        pass
    try:
        areas = json.loads(row.areas_to_improve) if row.areas_to_improve else []
    except Exception:
        pass
    try:
        raw_muscle = json.loads(row.muscle_development) if row.muscle_development else {}
        muscle = {
            k: v.get("notes", "") if isinstance(v, dict) else str(v)
            for k, v in raw_muscle.items()
        }
    except Exception:
        pass
    return BodyAnalysisSnapshot(
        body_fat_estimate=row.body_fat_estimate,
        overall_physique_score=row.overall_physique_score,
        strengths=strengths,
        areas_to_improve=areas,
        muscle_development=muscle,
        coach_message=row.coach_message,
    )


def _load_research(db) -> dict[str, str]:
    from models import ResearchCache
    rows = db.query(ResearchCache).all()
    return {r.topic: (r.summary or "") for r in rows if r.summary}


def _load_garmin_today(chat_id: int | None) -> GarminSnapshot | None:
    if not chat_id:
        return None
    try:
        import garmin_service
        data = garmin_service.get_cached(chat_id)
        if not data:
            return None
        return GarminSnapshot(
            reading_date=date.today(),
            hrv_ms=data.get("hrv_ms"),
            resting_hr_bpm=data.get("resting_hr_bpm"),
            sleep_hrs=data.get("sleep_duration_hrs"),
            steps=data.get("steps"),
        )
    except Exception:
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

async def build_context(db, user_id: int, chat_id: int | None = None) -> CoachContext:
    """Load all user data from SQLite into a CoachContext snapshot.

    Args:
        db: SQLAlchemy session (FastAPI dependency or direct SessionLocal).
        user_id: Web user ID (0 for unauthenticated/bot-only users).
        chat_id: Telegram chat ID (None for web-only users without linked Telegram).

    Returns:
        Fully populated CoachContext with last-7-day windows for training,
        check-ins, meals, and current PRs/goals.

    Raises:
        CoachBrainError: If DB queries fail. Callers should catch and fall back
        to calling AI without context (degraded, not broken).
    """
    from models import User

    # Resolve chat_id from linked User record if web user has connected Telegram
    if user_id:
        try:
            user_row = db.query(User).filter(User.id == user_id).first()
            if user_row and user_row.telegram_chat_id:
                chat_id = chat_id or user_row.telegram_chat_id
        except Exception:
            pass

    if not user_id and chat_id is None:
        return CoachContext(
            user_id=0, chat_id=None,
            profile=_empty_profile(),
            last_analysis=None,
            recent_sessions=(),
            recent_checkins=(),
            recent_meals=(),
            prs={},
            goals=(),
            research_cache={},
            garmin_today=None,
        )

    try:
        cutoff_7d = str(date.today() - timedelta(days=7))
        return CoachContext(
            user_id=user_id,
            chat_id=chat_id,
            profile=_load_profile(db, user_id, chat_id),
            last_analysis=_load_last_analysis(db),
            recent_sessions=tuple(_load_sessions(db, user_id, chat_id, cutoff_7d)),
            recent_checkins=tuple(_load_checkins(db, chat_id, cutoff_7d)),
            recent_meals=tuple(_load_meals(db, chat_id, cutoff_7d)),
            prs=_load_prs(db, chat_id),
            goals=tuple(_load_goals(db, chat_id)),
            research_cache=_load_research(db),
            garmin_today=_load_garmin_today(chat_id),
        )
    except Exception as e:
        raise CoachBrainError(f"Failed to load athlete context: {e}") from e


# ── Domain methods (Phase 5+ stubs) ──────────────────────────────────────────

async def generate_plan(ctx: CoachContext, days: int) -> dict:
    """Fetch research, build prompt, call Claude Opus, persist plan.

    Args:
        ctx: Populated CoachContext.
        days: Requested training days per week (2-6).

    Returns:
        PlanBundle dict with keys workout_plan, diet_plan, supplement_plan.
    """
    raise NotImplementedError("Phase 5")


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
    raise NotImplementedError("Phase 5")


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
    raise NotImplementedError("Phase 5")


async def end_session(ctx: CoachContext, session_id: int) -> dict:
    """Close a workout session and return a summary.

    Args:
        ctx: Populated CoachContext.
        session_id: Session to close.

    Returns:
        Summary dict: total_sets, total_volume_kg, new_prs, next_session_targets.
    """
    raise NotImplementedError("Phase 5")


async def generate_report(ctx: CoachContext) -> dict:
    """Generate weekly coaching report via Claude Opus, persist to DB.

    Args:
        ctx: Populated CoachContext with at least 7 days of data.

    Returns:
        ReportBundle dict with deterministic metrics + AI narrative sections.
    """
    raise NotImplementedError("Phase 5")


async def analyze_weak_points(ctx: CoachContext) -> dict:
    """Compute volume-per-muscle and call Claude Haiku for weak-point analysis.

    Args:
        ctx: Populated CoachContext.

    Returns:
        WeakPointAnalysis dict: weak_muscles, volume_gaps, exercise_prescriptions,
        frequency_adjustment, priority_rank.
    """
    raise NotImplementedError("Phase 5")


async def detect_plateau(ctx: CoachContext) -> PlateauSignal | None:
    """Run all plateau detection algorithms against recent data.

    Checks weight stall (14-day regression), strength stall (per-exercise,
    last 4 sessions), and recovery decline (7-day vs prior 7-day average).

    Args:
        ctx: Populated CoachContext.

    Returns:
        PlateauSignal if any signal detected, else None.
    """
    raise NotImplementedError("Phase 5")


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
    raise NotImplementedError("Phase 5")


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
    raise NotImplementedError("Phase 5")
