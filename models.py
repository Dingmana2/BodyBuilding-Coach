from sqlalchemy import Boolean, Column, Date, ForeignKey, Integer, String, Float, Text, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False, index=True)
    hashed_password = Column(String, nullable=False)
    telegram_chat_id = Column(Integer, unique=True, nullable=True, index=True)
    subscription_tier = Column(String, default="free")  # free|pro|elite
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class UserProfile(Base):
    __tablename__ = "user_profiles"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    age = Column(Integer)
    gender = Column(String)
    height_cm = Column(Float)
    weight_kg = Column(Float)
    goal = Column(String)  # bulk | cut | recomp | maintain
    training_experience = Column(String)  # beginner | intermediate | advanced
    training_days_per_week = Column(Integer)
    dietary_restrictions = Column(Text)
    equipment_available = Column(String, nullable=True)
    injuries = Column(Text, nullable=True)
    show_date = Column(Date, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class BodyAnalysis(Base):
    __tablename__ = "body_analyses"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True, index=True)
    photo_path = Column(String)
    body_fat_estimate = Column(String)
    overall_physique_score = Column(Float)
    strengths = Column(Text)        # JSON array
    areas_to_improve = Column(Text) # JSON array
    muscle_development = Column(Text)  # JSON object
    symmetry_notes = Column(Text)
    coach_message = Column(Text)
    raw_analysis = Column(Text)     # Full JSON from Claude
    body_fat_confidence = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class WorkoutPlan(Base):
    __tablename__ = "workout_plans"
    id = Column(Integer, primary_key=True)
    raw_plan = Column(Text)  # Full JSON from Claude
    created_at = Column(DateTime, server_default=func.now(), index=True)


class DietPlan(Base):
    __tablename__ = "diet_plans"
    id = Column(Integer, primary_key=True)
    calories = Column(Integer)
    protein_g = Column(Integer)
    carbs_g = Column(Integer)
    fat_g = Column(Integer)
    raw_plan = Column(Text)  # Full JSON from Claude
    created_at = Column(DateTime, server_default=func.now(), index=True)


class SupplementPlan(Base):
    __tablename__ = "supplement_plans"
    id = Column(Integer, primary_key=True)
    raw_plan = Column(Text)  # Full JSON from Claude
    created_at = Column(DateTime, server_default=func.now(), index=True)


class ResearchCache(Base):
    __tablename__ = "research_cache"
    id = Column(Integer, primary_key=True)
    topic = Column(String, unique=True)
    papers = Column(Text)   # JSON array
    summary = Column(Text)  # Claude-generated summary
    last_updated = Column(DateTime, server_default=func.now())


class WorkoutSession(Base):
    __tablename__ = "workout_sessions"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, index=True)   # Telegram chat_id; 0 for web app
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    started_at = Column(DateTime, server_default=func.now())
    ended_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    next_session_targets = Column(Text, nullable=True)


class SetLog(Base):
    __tablename__ = "set_logs"
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("workout_sessions.id"), index=True)
    exercise_name = Column(String, index=True)
    weight_kg = Column(Float)
    reps = Column(Integer)
    estimated_1rm = Column(Float)   # Epley: weight * (1 + reps/30)
    rpe = Column(Float, nullable=True)            # Rate of Perceived Exertion 1-10 (supports 8.5, 9.5)
    rir = Column(Integer, nullable=True)          # Reps in Reserve 0-5
    set_notes = Column(String, nullable=True)     # optional coach note for this set
    logged_at = Column(DateTime, server_default=func.now())


class PersonalRecord(Base):
    __tablename__ = "personal_records"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, index=True)
    exercise_name = Column(String, index=True)
    weight_kg = Column(Float)
    reps = Column(Integer)
    estimated_1rm = Column(Float)
    set_log_id = Column(Integer, ForeignKey("set_logs.id"), nullable=True)
    achieved_at = Column(DateTime, server_default=func.now())


class DailyCheckIn(Base):
    __tablename__ = "daily_checkins"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, index=True)
    date = Column(String, index=True)       # "2026-05-25"
    sleep_score = Column(Integer)           # 1-10
    energy_score = Column(Integer)          # 1-10
    soreness_score = Column(Integer)        # 1-10
    stress_score = Column(Integer)          # 1-10
    recovery_score = Column(Integer)        # 0-100, Claude-generated
    coaching_tip = Column(Text)
    hrv_ms = Column(Float, nullable=True)
    resting_hr_bpm = Column(Integer, nullable=True)
    sleep_duration_hrs = Column(Float, nullable=True)
    data_source = Column(String, default="manual")  # "manual" | "garmin" | "oura" etc.
    created_at = Column(DateTime, server_default=func.now())


class BodyMeasurement(Base):
    __tablename__ = "body_measurements"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, index=True)
    date = Column(String)
    body_weight_kg = Column(Float, nullable=True)
    waist_cm = Column(Float, nullable=True)
    chest_cm = Column(Float, nullable=True)
    hips_cm = Column(Float, nullable=True)
    left_arm_cm = Column(Float, nullable=True)
    right_arm_cm = Column(Float, nullable=True)
    left_thigh_cm = Column(Float, nullable=True)
    right_thigh_cm = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class MealLog(Base):
    __tablename__ = "meal_logs"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, index=True)
    date = Column(String, index=True)       # "2026-05-25"
    description = Column(Text)
    calories = Column(Integer)
    protein_g = Column(Float)
    carbs_g = Column(Float)
    fat_g = Column(Float)
    macro_source = Column(String, default="estimated")  # "nutritionix" | "estimated"
    logged_at = Column(DateTime, server_default=func.now())


class WeeklyReport(Base):
    __tablename__ = "weekly_reports"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, index=True)
    week_start = Column(String)             # "2026-05-19"
    sessions_count = Column(Integer)
    avg_recovery = Column(Float, nullable=True)
    prs_count = Column(Integer, default=0)
    avg_protein_g = Column(Float, nullable=True)
    ai_insights = Column(Text)              # JSON array of insight strings
    next_week_focus = Column(Text, nullable=True)
    adherence_rating = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class UserStreak(Base):
    __tablename__ = "user_streaks"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, index=True)
    streak_type = Column(String)            # checkin|workout|overall
    current_streak = Column(Integer, default=0)
    longest_streak = Column(Integer, default=0)
    last_activity_date = Column(Date, nullable=True)
    total_days_active = Column(Integer, default=0)


class UserGoal(Base):
    __tablename__ = "user_goals"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, index=True)
    goal_type = Column(String)              # cut|bulk|recomp|strength|prep
    target_weight_kg = Column(Float, nullable=True)
    target_bf_pct = Column(Float, nullable=True)
    target_date = Column(Date, nullable=True)
    start_weight_kg = Column(Float, nullable=True)
    start_bf_pct = Column(Float, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class Badge(Base):
    __tablename__ = "badges"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, index=True)
    badge_type = Column(String)             # first_pr|7_day_streak|100_sets|etc
    badge_metadata = Column(Text, nullable=True)  # JSON string
    earned_at = Column(DateTime, server_default=func.now())


class TelegramLinkCode(Base):
    """One-time code that ties a Telegram chat_id to a web User account."""
    __tablename__ = "telegram_link_codes"
    id = Column(Integer, primary_key=True)
    code = Column(String(8), unique=True, index=True)
    telegram_chat_id = Column(Integer, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    expires_at = Column(DateTime)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class CoachMemory(Base):
    """Persistent coaching observations — PRs, recovery patterns, notes."""
    __tablename__ = "coach_memories"
    id = Column(Integer, primary_key=True)
    chat_id = Column(Integer, index=True)
    content = Column(Text, nullable=False)
    memory_type = Column(String, default="observation")  # pr|recovery|note|observation
    created_at = Column(DateTime, server_default=func.now())


class DailyBriefing(Base):
    """AI-generated morning briefing stored per user per day.

    Generated nightly at 6 AM by _generate_morning_briefings().
    Unique on (user_id, date) — upsert on conflict so scheduler restarts
    don't create duplicate rows.
    """
    __tablename__ = "daily_briefings"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_briefing_user_date"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    date = Column(String, nullable=False, index=True)   # "2026-06-02"
    briefing_text = Column(Text, nullable=False)
    data_sources = Column(Text, nullable=True)          # JSON: {"garmin": true, "mfp": false, "checkin": true}
    created_at = Column(DateTime, server_default=func.now())


class PushSubscription(Base):
    """Web Push subscription stored per user.

    endpoint is globally unique (per browser/device).
    Upsert on endpoint so re-subscribing the same device updates keys.
    """
    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint", name="uq_push_endpoint"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    endpoint = Column(Text, nullable=False)
    p256dh = Column(Text, nullable=False)   # browser public key
    auth = Column(Text, nullable=False)     # auth secret
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
