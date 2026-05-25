from sqlalchemy import Column, Integer, String, Float, Text, DateTime
from sqlalchemy.sql import func
from database import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"
    id = Column(Integer, primary_key=True)
    age = Column(Integer)
    gender = Column(String)
    height_cm = Column(Float)
    weight_kg = Column(Float)
    goal = Column(String)  # bulk | cut | recomp | maintain
    training_experience = Column(String)  # beginner | intermediate | advanced
    training_days_per_week = Column(Integer)
    dietary_restrictions = Column(Text)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class BodyAnalysis(Base):
    __tablename__ = "body_analyses"
    id = Column(Integer, primary_key=True)
    photo_path = Column(String)
    body_fat_estimate = Column(String)
    overall_physique_score = Column(Float)
    strengths = Column(Text)        # JSON array
    areas_to_improve = Column(Text) # JSON array
    muscle_development = Column(Text)  # JSON object
    symmetry_notes = Column(Text)
    coach_message = Column(Text)
    raw_analysis = Column(Text)     # Full JSON from Claude
    created_at = Column(DateTime, server_default=func.now())


class WorkoutPlan(Base):
    __tablename__ = "workout_plans"
    id = Column(Integer, primary_key=True)
    raw_plan = Column(Text)  # Full JSON from Claude
    created_at = Column(DateTime, server_default=func.now())


class DietPlan(Base):
    __tablename__ = "diet_plans"
    id = Column(Integer, primary_key=True)
    calories = Column(Integer)
    protein_g = Column(Integer)
    carbs_g = Column(Integer)
    fat_g = Column(Integer)
    raw_plan = Column(Text)  # Full JSON from Claude
    created_at = Column(DateTime, server_default=func.now())


class SupplementPlan(Base):
    __tablename__ = "supplement_plans"
    id = Column(Integer, primary_key=True)
    raw_plan = Column(Text)  # Full JSON from Claude
    created_at = Column(DateTime, server_default=func.now())


class ResearchCache(Base):
    __tablename__ = "research_cache"
    id = Column(Integer, primary_key=True)
    topic = Column(String, unique=True)
    papers = Column(Text)   # JSON array
    summary = Column(Text)  # Claude-generated summary
    last_updated = Column(DateTime, server_default=func.now())
