import asyncio
import base64
import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

load_dotenv()

# ── Auth helpers (stdlib only — no cryptography dependency) ───────────────────

_SECRET_KEY = os.getenv("SECRET_KEY", "change-me-use-a-long-random-string-in-production").encode()
_TOKEN_EXPIRE_DAYS = 30
_bearer = HTTPBearer(auto_error=False)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))


def _hash_password(plain: str) -> str:
    import hashlib as _hl
    salt = os.urandom(16).hex()
    h = _hl.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), 260_000).hex()
    return f"pbkdf2$sha256$260000${salt}${h}"


def _verify_password(plain: str, stored: str) -> bool:
    import hashlib as _hl
    try:
        _, algo, iters, salt, expected = stored.split("$")
        h = _hl.pbkdf2_hmac(algo, plain.encode(), salt.encode(), int(iters)).hex()
        return hmac.compare_digest(h, expected)
    except Exception:
        return False


def _create_token(user_id: int) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    expire = int((datetime.now(timezone.utc) + timedelta(days=_TOKEN_EXPIRE_DAYS)).timestamp())
    payload = _b64url(json.dumps({"sub": str(user_id), "exp": expire}).encode())
    signing_input = f"{header}.{payload}".encode()
    sig = _b64url(hmac.new(_SECRET_KEY, signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> int:
    """Extract user_id from Bearer JWT. Returns 0 for unauthenticated/invalid."""
    if not credentials:
        return 0
    try:
        parts = credentials.credentials.split(".")
        if len(parts) != 3:
            return 0
        header_b64, payload_b64, sig_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected_sig = _b64url(hmac.new(_SECRET_KEY, signing_input, hashlib.sha256).digest())
        if not hmac.compare_digest(expected_sig, sig_b64):
            return 0
        payload = json.loads(_b64url_decode(payload_b64))
        if payload.get("exp", 0) < datetime.now(timezone.utc).timestamp():
            return 0
        return int(payload.get("sub", 0))
    except Exception:
        return 0


from database import engine, get_db
import models
from claude_service import (
    analyze_body_photo,
    estimate_meal_macros,
    generate_comprehensive_plan,
    generate_recovery_insight,
    generate_weekly_report,
    analyze_weak_points,
)
from research_service import refresh_all_research

models.Base.metadata.create_all(bind=engine)

# ── Safe column migrations (idempotent ALTER TABLE for schema evolution) ──────
def _migrate_db():
    """Add columns that may not exist in pre-existing SQLite databases."""
    from sqlalchemy import text
    migrations = [
        ("user_profiles", "user_id", "INTEGER REFERENCES users(id)"),
        ("user_profiles", "equipment_available", "VARCHAR"),
        ("user_profiles", "injuries", "TEXT"),
        ("user_profiles", "show_date", "DATE"),
        ("body_analyses", "body_fat_confidence", "VARCHAR"),
    ]
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
            except Exception:
                pass  # Column already exists

_migrate_db()

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="BodyBuilding Coach AI", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@app.get("/")
def root():
    return FileResponse("static/index.html")


# ── Auth ─────────────────────────────────────────────────────────────────────

@app.post("/api/auth/register")
async def register(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required.")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with that email already exists.")
    user = models.User(email=email, hashed_password=_hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {
        "token": _create_token(user.id),
        "user": {"id": user.id, "email": user.email, "subscription_tier": user.subscription_tier},
    }


@app.post("/api/auth/login")
async def login(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not _verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled.")
    return {
        "token": _create_token(user.id),
        "user": {"id": user.id, "email": user.email, "subscription_tier": user.subscription_tier},
    }


@app.get("/api/auth/me")
def get_me(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    user = db.query(models.User).filter(models.User.id == current_user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return {
        "id": user.id,
        "email": user.email,
        "subscription_tier": user.subscription_tier,
        "telegram_linked": user.telegram_chat_id is not None,
        "created_at": user.created_at.isoformat(),
    }


# ── Profile ──────────────────────────────────────────────────────────────────

@app.get("/api/profile")
def get_profile(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    profile = (
        db.query(models.UserProfile)
        .filter(models.UserProfile.user_id == current_user_id)
        .first()
    ) if current_user_id else db.query(models.UserProfile).filter(models.UserProfile.user_id == None).first()
    if not profile:
        return {}
    return {
        "id": profile.id,
        "age": profile.age,
        "gender": profile.gender,
        "height_cm": profile.height_cm,
        "weight_kg": profile.weight_kg,
        "goal": profile.goal,
        "training_experience": profile.training_experience,
        "training_days_per_week": profile.training_days_per_week,
        "dietary_restrictions": profile.dietary_restrictions,
        "equipment_available": profile.equipment_available,
        "injuries": profile.injuries,
    }


@app.post("/api/profile")
async def save_profile(
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    data = await request.json()
    profile = (
        db.query(models.UserProfile)
        .filter(models.UserProfile.user_id == current_user_id)
        .first()
    ) if current_user_id else db.query(models.UserProfile).filter(models.UserProfile.user_id == None).first()
    if not profile:
        profile = models.UserProfile(user_id=current_user_id if current_user_id else None)
        db.add(profile)

    profile.age = data.get("age")
    profile.gender = data.get("gender")
    profile.height_cm = data.get("height_cm")
    profile.weight_kg = data.get("weight_kg")
    profile.goal = data.get("goal")
    profile.training_experience = data.get("training_experience")
    profile.training_days_per_week = data.get("training_days_per_week")
    profile.dietary_restrictions = data.get("dietary_restrictions")
    profile.equipment_available = data.get("equipment_available")
    profile.injuries = data.get("injuries")
    db.commit()
    return {"status": "saved"}


# ── Body Analysis ─────────────────────────────────────────────────────────────

@app.post("/api/analyze")
async def analyze_photo(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    # Reject oversized uploads before reading the body into RAM.
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large. Max 20MB.")

    ext = Path(file.filename or "photo.jpg").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type {ext} not supported. Use JPG, PNG, or WebP.")

    filename = f"{uuid.uuid4()}{ext}"
    filepath = UPLOAD_DIR / filename

    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large. Max 20MB.")

    with open(filepath, "wb") as f:
        f.write(content)

    profile = db.query(models.UserProfile).first()
    prev = (
        db.query(models.BodyAnalysis)
        .order_by(models.BodyAnalysis.created_at.desc())
        .first()
    )

    try:
        result = analyze_body_photo(str(filepath), profile, prev)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    analysis = models.BodyAnalysis(
        photo_path=filename,
        body_fat_estimate=result.get("body_fat_estimate"),
        overall_physique_score=result.get("overall_physique_score"),
        strengths=json.dumps(result.get("strengths", [])),
        areas_to_improve=json.dumps(result.get("areas_to_improve", [])),
        muscle_development=json.dumps(result.get("muscle_development", {})),
        symmetry_notes=result.get("symmetry_notes"),
        coach_message=result.get("coach_message"),
        raw_analysis=json.dumps(result),
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    return {
        "analysis_id": analysis.id,
        "analysis": result,
        "photo_url": f"/uploads/{filename}",
        "created_at": analysis.created_at.isoformat(),
    }


@app.get("/api/analyses")
def list_analyses(limit: int = 20, db: Session = Depends(get_db)):
    rows = (
        db.query(models.BodyAnalysis)
        .order_by(models.BodyAnalysis.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "photo_url": f"/uploads/{r.photo_path}",
            "body_fat_estimate": r.body_fat_estimate,
            "overall_physique_score": r.overall_physique_score,
            "coach_message": r.coach_message,
            "created_at": r.created_at.isoformat(),
            "raw_analysis": json.loads(r.raw_analysis) if r.raw_analysis else {},
        }
        for r in rows
    ]


# ── Plans ─────────────────────────────────────────────────────────────────────

@app.post("/api/plan/generate")
async def generate_plan(db: Session = Depends(get_db)):
    analysis = (
        db.query(models.BodyAnalysis)
        .order_by(models.BodyAnalysis.created_at.desc())
        .first()
    )
    if not analysis:
        raise HTTPException(
            status_code=400,
            detail="No body analysis found. Upload a photo first.",
        )

    profile = db.query(models.UserProfile).first()
    research_cache = db.query(models.ResearchCache).all()

    try:
        # Run the synchronous Claude call in a thread pool so it doesn't block
        # the event loop while waiting for the ~30-60s API response.
        plan = await asyncio.to_thread(
            generate_comprehensive_plan, analysis, research_cache, profile
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plan generation failed: {e}")

    workout_data = plan.get("workout_plan", {})
    diet_data = plan.get("diet_plan", {})
    supps_data = plan.get("supplement_plan", [])

    workout = models.WorkoutPlan(raw_plan=json.dumps(workout_data))
    diet = models.DietPlan(
        calories=diet_data.get("daily_calories"),
        protein_g=diet_data.get("macros", {}).get("protein_g"),
        carbs_g=diet_data.get("macros", {}).get("carbs_g"),
        fat_g=diet_data.get("macros", {}).get("fat_g"),
        raw_plan=json.dumps(diet_data),
    )
    supps = models.SupplementPlan(raw_plan=json.dumps(supps_data))

    db.add_all([workout, diet, supps])
    db.commit()

    return plan


@app.get("/api/plan/current")
def get_current_plan(db: Session = Depends(get_db)):
    workout = (
        db.query(models.WorkoutPlan)
        .order_by(models.WorkoutPlan.created_at.desc())
        .first()
    )
    diet = (
        db.query(models.DietPlan)
        .order_by(models.DietPlan.created_at.desc())
        .first()
    )
    supps = (
        db.query(models.SupplementPlan)
        .order_by(models.SupplementPlan.created_at.desc())
        .first()
    )
    analysis = (
        db.query(models.BodyAnalysis)
        .order_by(models.BodyAnalysis.created_at.desc())
        .first()
    )

    return {
        "workout_plan": json.loads(workout.raw_plan) if workout else None,
        "workout_created_at": workout.created_at.isoformat() if workout else None,
        "diet_plan": json.loads(diet.raw_plan) if diet else None,
        "diet_macros": {
            "calories": diet.calories,
            "protein_g": diet.protein_g,
            "carbs_g": diet.carbs_g,
            "fat_g": diet.fat_g,
        } if diet else None,
        "supplement_plan": json.loads(supps.raw_plan) if supps else None,
        "latest_analysis": {
            "body_fat_estimate": analysis.body_fat_estimate,
            "overall_physique_score": analysis.overall_physique_score,
            "photo_url": f"/uploads/{analysis.photo_path}",
            "created_at": analysis.created_at.isoformat(),
        } if analysis else None,
    }


# ── Research ──────────────────────────────────────────────────────────────────

@app.get("/api/research")
def get_research(db: Session = Depends(get_db)):
    rows = (
        db.query(models.ResearchCache)
        .order_by(models.ResearchCache.last_updated.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "topic": r.topic,
            "summary": r.summary,
            "papers": json.loads(r.papers) if r.papers else [],
            "last_updated": r.last_updated.isoformat(),
        }
        for r in rows
    ]


@app.post("/api/research/refresh")
async def refresh_research(db: Session = Depends(get_db)):
    profile = db.query(models.UserProfile).first()

    try:
        findings = await refresh_all_research(profile)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Research refresh failed: {e}")

    updated = []
    for topic, data in findings.items():
        existing = (
            db.query(models.ResearchCache)
            .filter(models.ResearchCache.topic == topic)
            .first()
        )
        if existing:
            existing.papers = json.dumps(data["papers"])
            existing.summary = data["summary"]
            existing.last_updated = datetime.now(timezone.utc)
        else:
            db.add(
                models.ResearchCache(
                    topic=topic,
                    papers=json.dumps(data["papers"]),
                    summary=data["summary"],
                )
            )
        updated.append(topic)

    db.commit()
    return {"status": "success", "topics_updated": updated, "count": len(updated)}


# ── Progress ──────────────────────────────────────────────────────────────────

@app.get("/api/progress")
def get_progress(db: Session = Depends(get_db)):
    rows = (
        db.query(models.BodyAnalysis)
        .order_by(models.BodyAnalysis.created_at.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "photo_url": f"/uploads/{r.photo_path}",
            "body_fat_estimate": r.body_fat_estimate,
            "overall_physique_score": r.overall_physique_score,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


# ── Workout Sessions ──────────────────────────────────────────────────────────

@app.get("/api/sessions/active")
def get_active_session(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    session = (
        db.query(models.WorkoutSession)
        .filter(models.WorkoutSession.chat_id == current_user_id, models.WorkoutSession.ended_at == None)
        .order_by(models.WorkoutSession.started_at.desc())
        .first()
    )
    if not session:
        return {"session": None, "sets": []}
    sets = (
        db.query(models.SetLog)
        .filter(models.SetLog.session_id == session.id)
        .order_by(models.SetLog.logged_at.asc())
        .all()
    )
    return {
        "session": {
            "id": session.id,
            "started_at": session.started_at.isoformat(),
            "notes": session.notes,
        },
        "sets": [
            {
                "id": s.id,
                "exercise_name": s.exercise_name,
                "weight_kg": s.weight_kg,
                "reps": s.reps,
                "estimated_1rm": s.estimated_1rm,
                "logged_at": s.logged_at.isoformat(),
            }
            for s in sets
        ],
    }


@app.post("/api/sessions/start")
async def start_session(
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    data = await request.json()
    db.query(models.WorkoutSession).filter(
        models.WorkoutSession.chat_id == current_user_id,
        models.WorkoutSession.ended_at == None,
    ).update({"ended_at": datetime.now(timezone.utc)})
    session = models.WorkoutSession(chat_id=current_user_id, notes=data.get("notes"))
    db.add(session)
    db.commit()
    db.refresh(session)
    return {"id": session.id, "started_at": session.started_at.isoformat()}


@app.post("/api/sessions/{session_id}/end")
def end_session(session_id: int, db: Session = Depends(get_db)):
    session = db.query(models.WorkoutSession).filter(models.WorkoutSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.ended_at = datetime.now(timezone.utc)
    sets = db.query(models.SetLog).filter(models.SetLog.session_id == session_id).all()
    total_volume = sum(s.weight_kg * s.reps for s in sets)
    db.commit()
    return {
        "status": "ended",
        "set_count": len(sets),
        "total_volume_kg": round(total_volume, 1),
        "exercises": list({s.exercise_name for s in sets}),
    }


@app.post("/api/sessions/{session_id}/sets")
async def log_set(
    session_id: int,
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    data = await request.json()
    exercise = data["exercise_name"].strip()
    weight_kg = float(data["weight_kg"])
    reps = int(data["reps"])
    estimated_1rm = round(weight_kg * (1 + reps / 30), 1)

    set_log = models.SetLog(
        session_id=session_id,
        exercise_name=exercise,
        weight_kg=weight_kg,
        reps=reps,
        estimated_1rm=estimated_1rm,
    )
    db.add(set_log)
    db.flush()

    existing_pr = (
        db.query(models.PersonalRecord)
        .filter(
            models.PersonalRecord.chat_id == current_user_id,
            models.PersonalRecord.exercise_name == exercise,
        )
        .first()
    )

    is_pr = False
    prev_pr = None
    if not existing_pr or estimated_1rm > existing_pr.estimated_1rm:
        is_pr = True
        if existing_pr:
            prev_pr = {
                "weight_kg": existing_pr.weight_kg,
                "reps": existing_pr.reps,
                "estimated_1rm": existing_pr.estimated_1rm,
            }
            existing_pr.weight_kg = weight_kg
            existing_pr.reps = reps
            existing_pr.estimated_1rm = estimated_1rm
            existing_pr.set_log_id = set_log.id
            existing_pr.achieved_at = datetime.now(timezone.utc)
        else:
            db.add(
                models.PersonalRecord(
                    chat_id=current_user_id,
                    exercise_name=exercise,
                    weight_kg=weight_kg,
                    reps=reps,
                    estimated_1rm=estimated_1rm,
                    set_log_id=set_log.id,
                )
            )

    db.commit()
    db.refresh(set_log)
    return {
        "id": set_log.id,
        "exercise_name": exercise,
        "weight_kg": weight_kg,
        "reps": reps,
        "estimated_1rm": estimated_1rm,
        "logged_at": set_log.logged_at.isoformat(),
        "is_pr": is_pr,
        "prev_pr": prev_pr,
    }


@app.get("/api/sessions/history")
def get_session_history(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    sessions = (
        db.query(models.WorkoutSession)
        .filter(
            models.WorkoutSession.chat_id == current_user_id,
            models.WorkoutSession.ended_at != None,
        )
        .order_by(models.WorkoutSession.started_at.desc())
        .limit(10)
        .all()
    )
    result = []
    for s in sessions:
        sets = db.query(models.SetLog).filter(models.SetLog.session_id == s.id).all()
        result.append({
            "id": s.id,
            "started_at": s.started_at.isoformat(),
            "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            "set_count": len(sets),
            "exercises": list({x.exercise_name for x in sets}),
            "total_volume_kg": round(sum(x.weight_kg * x.reps for x in sets), 1),
        })
    return result


@app.get("/api/prs")
def get_prs(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(models.PersonalRecord)
        .filter(models.PersonalRecord.chat_id == current_user_id)
        .order_by(models.PersonalRecord.exercise_name)
        .all()
    )
    return [
        {
            "exercise_name": r.exercise_name,
            "weight_kg": r.weight_kg,
            "reps": r.reps,
            "estimated_1rm": r.estimated_1rm,
            "achieved_at": r.achieved_at.isoformat(),
        }
        for r in rows
    ]


# ── Shared helpers ────────────────────────────────────────────────────────────

def _checkin_dict(r) -> dict:
    return {
        "id": r.id, "date": r.date,
        "sleep_score": r.sleep_score, "energy_score": r.energy_score,
        "soreness_score": r.soreness_score, "stress_score": r.stress_score,
        "recovery_score": r.recovery_score, "coaching_tip": r.coaching_tip,
        "hrv_ms": r.hrv_ms, "resting_hr_bpm": r.resting_hr_bpm,
        "sleep_duration_hrs": r.sleep_duration_hrs, "data_source": r.data_source,
        "created_at": r.created_at.isoformat(),
    }


def _measurement_dict(r) -> dict:
    return {
        "id": r.id, "date": r.date,
        "body_weight_kg": r.body_weight_kg, "waist_cm": r.waist_cm,
        "chest_cm": r.chest_cm, "hips_cm": r.hips_cm,
        "left_arm_cm": r.left_arm_cm, "right_arm_cm": r.right_arm_cm,
        "left_thigh_cm": r.left_thigh_cm, "right_thigh_cm": r.right_thigh_cm,
        "created_at": r.created_at.isoformat(),
    }


def _meal_dict(r) -> dict:
    return {
        "id": r.id, "date": r.date, "description": r.description,
        "calories": r.calories, "protein_g": r.protein_g,
        "carbs_g": r.carbs_g, "fat_g": r.fat_g,
        "macro_source": r.macro_source, "logged_at": r.logged_at.isoformat(),
    }


def _goal_dict(r) -> dict:
    return {
        "id": r.id, "goal_type": r.goal_type,
        "target_weight_kg": r.target_weight_kg, "target_bf_pct": r.target_bf_pct,
        "target_date": r.target_date.isoformat() if r.target_date else None,
        "start_weight_kg": r.start_weight_kg, "start_bf_pct": r.start_bf_pct,
        "is_active": r.is_active, "created_at": r.created_at.isoformat(),
    }


def _get_streak(db: Session, chat_id: int, streak_type: str):
    return (
        db.query(models.UserStreak)
        .filter(models.UserStreak.chat_id == chat_id, models.UserStreak.streak_type == streak_type)
        .first()
    )


def _update_streak(db: Session, chat_id: int, streak_type: str) -> None:
    from datetime import date, timedelta as td
    today = date.today()
    streak = _get_streak(db, chat_id, streak_type)
    if not streak:
        db.add(models.UserStreak(
            chat_id=current_user_id, streak_type=streak_type,
            current_streak=1, longest_streak=1,
            last_activity_date=today, total_days_active=1,
        ))
    else:
        last = streak.last_activity_date
        if last == today:
            return
        elif last == today - td(days=1):
            streak.current_streak += 1
        else:
            streak.current_streak = 1
        streak.longest_streak = max(streak.longest_streak, streak.current_streak)
        streak.last_activity_date = today
        streak.total_days_active = (streak.total_days_active or 0) + 1
    db.commit()


def _award_badge(db: Session, chat_id: int, badge_type: str, metadata: dict | None = None) -> bool:
    existing = db.query(models.Badge).filter(
        models.Badge.chat_id == chat_id, models.Badge.badge_type == badge_type
    ).first()
    if existing:
        return False
    db.add(models.Badge(
        chat_id=current_user_id, badge_type=badge_type,
        badge_metadata=json.dumps(metadata) if metadata else None,
    ))
    db.commit()
    return True


# ── Check-Ins ─────────────────────────────────────────────────────────────────

@app.get("/api/checkins")
def list_checkins(limit: int = 90, current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db)):
    rows = (
        db.query(models.DailyCheckIn)
        .filter(models.DailyCheckIn.chat_id == current_user_id)
        .order_by(models.DailyCheckIn.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_checkin_dict(r) for r in rows]


@app.post("/api/checkins")
async def create_checkin(request: Request, current_user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    data = await request.json()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    existing = (
        db.query(models.DailyCheckIn)
        .filter(models.DailyCheckIn.chat_id == current_user_id, models.DailyCheckIn.date == today)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Already checked in today.")

    sleep_score = int(data["sleep_score"])
    energy_score = int(data["energy_score"])
    soreness_score = int(data["soreness_score"])
    stress_score = int(data["stress_score"])

    profile = db.query(models.UserProfile).first()
    profile_dict = None
    if profile:
        profile_dict = {"age": profile.age, "goal": profile.goal, "experience": profile.training_experience}

    try:
        recovery_score, coaching_tip = await asyncio.to_thread(
            generate_recovery_insight, sleep_score, energy_score, soreness_score, stress_score, profile_dict
        )
    except Exception:
        recovery_score, coaching_tip = 50, "Listen to your body and train accordingly."

    checkin = models.DailyCheckIn(
        chat_id=current_user_id, date=today,
        sleep_score=sleep_score, energy_score=energy_score,
        soreness_score=soreness_score, stress_score=stress_score,
        recovery_score=recovery_score, coaching_tip=coaching_tip,
        hrv_ms=data.get("hrv_ms"), resting_hr_bpm=data.get("resting_hr_bpm"),
        sleep_duration_hrs=data.get("sleep_duration_hrs"),
        data_source=data.get("data_source", "manual"),
    )
    db.add(checkin)
    db.commit()
    db.refresh(checkin)

    _update_streak(db, current_user_id, "checkin")
    streak = _get_streak(db, current_user_id, "checkin")
    streak_count = streak.current_streak if streak else 1
    if streak_count in (7, 14, 30, 60, 90):
        _award_badge(db, current_user_id, f"{streak_count}_day_checkin_streak")

    return {**_checkin_dict(checkin), "streak": streak_count}


@app.get("/api/checkins/streak")
def get_checkin_streak(current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db)):
    streak = _get_streak(db, current_user_id, "checkin")
    if not streak:
        return {"current_streak": 0, "longest_streak": 0, "total_days_active": 0}
    return {
        "current_streak": streak.current_streak,
        "longest_streak": streak.longest_streak,
        "total_days_active": streak.total_days_active,
        "last_activity_date": streak.last_activity_date.isoformat() if streak.last_activity_date else None,
    }


# ── Measurements ──────────────────────────────────────────────────────────────

@app.get("/api/measurements")
def list_measurements(limit: int = 30, current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db)):
    rows = (
        db.query(models.BodyMeasurement)
        .filter(models.BodyMeasurement.chat_id == current_user_id)
        .order_by(models.BodyMeasurement.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_measurement_dict(r) for r in rows]


@app.post("/api/measurements")
async def create_measurement(request: Request, current_user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    data = await request.json()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    m = models.BodyMeasurement(
        chat_id=current_user_id, date=data.get("date", today),
        body_weight_kg=data.get("body_weight_kg"),
        waist_cm=data.get("waist_cm"), chest_cm=data.get("chest_cm"),
        hips_cm=data.get("hips_cm"), left_arm_cm=data.get("left_arm_cm"),
        right_arm_cm=data.get("right_arm_cm"), left_thigh_cm=data.get("left_thigh_cm"),
        right_thigh_cm=data.get("right_thigh_cm"),
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return _measurement_dict(m)


# ── Meals ─────────────────────────────────────────────────────────────────────

@app.get("/api/meals")
def list_meals(limit: int = 30, current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db)):
    rows = (
        db.query(models.MealLog)
        .filter(models.MealLog.chat_id == current_user_id)
        .order_by(models.MealLog.logged_at.desc())
        .limit(limit)
        .all()
    )
    return [_meal_dict(r) for r in rows]


@app.get("/api/meals/today")
def get_today_meals(current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db)):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = (
        db.query(models.MealLog)
        .filter(models.MealLog.chat_id == current_user_id, models.MealLog.date == today)
        .order_by(models.MealLog.logged_at.asc())
        .all()
    )
    return {
        "meals": [_meal_dict(r) for r in rows],
        "totals": {
            "calories": round(sum(r.calories or 0 for r in rows)),
            "protein_g": round(sum(r.protein_g or 0 for r in rows), 1),
            "carbs_g": round(sum(r.carbs_g or 0 for r in rows), 1),
            "fat_g": round(sum(r.fat_g or 0 for r in rows), 1),
        },
    }


@app.post("/api/meals")
async def log_meal(request: Request, current_user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    data = await request.json()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    description = data.get("description", "")
    calories = data.get("calories")
    protein_g = data.get("protein_g")
    carbs_g = data.get("carbs_g")
    fat_g = data.get("fat_g")
    macro_source = data.get("macro_source", "manual")

    if description and (calories is None or protein_g is None):
        try:
            estimated = await asyncio.to_thread(estimate_meal_macros, description)
            calories = estimated.get("calories")
            protein_g = estimated.get("protein_g")
            carbs_g = estimated.get("carbs_g")
            fat_g = estimated.get("fat_g")
            macro_source = "estimated"
        except Exception:
            pass

    meal = models.MealLog(
        chat_id=current_user_id, date=data.get("date", today), description=description,
        calories=calories, protein_g=protein_g, carbs_g=carbs_g, fat_g=fat_g,
        macro_source=macro_source,
    )
    db.add(meal)
    db.commit()
    db.refresh(meal)
    return _meal_dict(meal)


# ── Weekly Reports ────────────────────────────────────────────────────────────

@app.get("/api/reports")
def list_reports(limit: int = 10, current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db)):
    rows = (
        db.query(models.WeeklyReport)
        .filter(models.WeeklyReport.chat_id == current_user_id)
        .order_by(models.WeeklyReport.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id, "week_start": r.week_start, "sessions_count": r.sessions_count,
            "avg_recovery": r.avg_recovery, "prs_count": r.prs_count,
            "avg_protein_g": r.avg_protein_g,
            "ai_insights": json.loads(r.ai_insights) if r.ai_insights else [],
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@app.post("/api/reports/generate")
async def generate_report(request: Request, current_user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    data = await request.json()
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

    sessions = db.query(models.WorkoutSession).filter(
        models.WorkoutSession.chat_id == current_user_id,
        models.WorkoutSession.ended_at != None,
        models.WorkoutSession.started_at >= datetime.fromisoformat(seven_days_ago),
    ).all()

    checkins = db.query(models.DailyCheckIn).filter(
        models.DailyCheckIn.chat_id == current_user_id,
        models.DailyCheckIn.date >= seven_days_ago,
    ).all()

    meals = db.query(models.MealLog).filter(
        models.MealLog.chat_id == current_user_id,
        models.MealLog.date >= seven_days_ago,
    ).all()

    prs = (
        db.query(models.PersonalRecord)
        .filter(models.PersonalRecord.chat_id == current_user_id)
        .order_by(models.PersonalRecord.achieved_at.desc())
        .limit(10)
        .all()
    )

    profile = db.query(models.UserProfile).first()
    profile_dict = {"goal": profile.goal, "experience": profile.training_experience} if profile else None

    sessions_data = [{"id": s.id, "started_at": s.started_at.isoformat()} for s in sessions]
    checkins_data = [{"recovery_score": c.recovery_score} for c in checkins]
    meals_data = [{"protein_g": m.protein_g} for m in meals]
    prs_data = [{"exercise_name": p.exercise_name, "weight_kg": p.weight_kg, "reps": p.reps} for p in prs]

    try:
        report_data = await asyncio.to_thread(
            generate_weekly_report, sessions_data, checkins_data, meals_data, prs_data, profile_dict
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")

    week_start = seven_days_ago
    report = models.WeeklyReport(
        chat_id=current_user_id, week_start=week_start, sessions_count=len(sessions),
        avg_recovery=report_data.get("avg_recovery"), prs_count=len(prs_data),
        avg_protein_g=report_data.get("avg_protein_g"),
        ai_insights=json.dumps(report_data.get("insights", [])),
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    return {
        "id": report.id, "week_start": week_start,
        "insights": report_data.get("insights", []),
        "next_week_focus": report_data.get("next_week_focus"),
        "adherence_rating": report_data.get("adherence_rating"),
        "sessions_count": len(sessions), "avg_recovery": report_data.get("avg_recovery"),
        "avg_protein_g": report_data.get("avg_protein_g"), "prs_count": len(prs_data),
    }


# ── Streaks & Badges ──────────────────────────────────────────────────────────

@app.get("/api/streaks")
def get_streaks(current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db)):
    streaks = db.query(models.UserStreak).filter(models.UserStreak.chat_id == current_user_id).all()
    return {
        s.streak_type: {
            "current_streak": s.current_streak,
            "longest_streak": s.longest_streak,
            "total_days_active": s.total_days_active,
            "last_activity_date": s.last_activity_date.isoformat() if s.last_activity_date else None,
        }
        for s in streaks
    }


@app.get("/api/badges")
def get_badges(current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db)):
    rows = (
        db.query(models.Badge)
        .filter(models.Badge.chat_id == current_user_id)
        .order_by(models.Badge.earned_at.desc())
        .all()
    )
    return [
        {
            "id": r.id, "badge_type": r.badge_type,
            "metadata": json.loads(r.badge_metadata) if r.badge_metadata else {},
            "earned_at": r.earned_at.isoformat(),
        }
        for r in rows
    ]


# ── Goals ─────────────────────────────────────────────────────────────────────

@app.get("/api/goals")
def list_goals(current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db)):
    rows = (
        db.query(models.UserGoal)
        .filter(models.UserGoal.chat_id == current_user_id)
        .order_by(models.UserGoal.created_at.desc())
        .all()
    )
    return [_goal_dict(r) for r in rows]


@app.post("/api/goals")
async def create_goal(request: Request, current_user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    data = await request.json()

    db.query(models.UserGoal).filter(
        models.UserGoal.chat_id == current_user_id,
        models.UserGoal.goal_type == data.get("goal_type"),
        models.UserGoal.is_active == True,
    ).update({"is_active": False})

    target_date = None
    if data.get("target_date"):
        from datetime import date
        target_date = date.fromisoformat(data["target_date"])

    goal = models.UserGoal(
        chat_id=current_user_id, goal_type=data.get("goal_type"),
        target_weight_kg=data.get("target_weight_kg"), target_bf_pct=data.get("target_bf_pct"),
        target_date=target_date, start_weight_kg=data.get("start_weight_kg"),
        start_bf_pct=data.get("start_bf_pct"), is_active=True,
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return _goal_dict(goal)


# ── Dashboard Summary ─────────────────────────────────────────────────────────

@app.get("/api/dashboard/summary")
def dashboard_summary(current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db)):
    streaks = {
        s.streak_type: s.current_streak
        for s in db.query(models.UserStreak).filter(models.UserStreak.chat_id == current_user_id).all()
    }
    prs_count = db.query(models.PersonalRecord).filter(models.PersonalRecord.chat_id == current_user_id).count()

    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    sessions_week = db.query(models.WorkoutSession).filter(
        models.WorkoutSession.chat_id == current_user_id,
        models.WorkoutSession.started_at >= datetime.fromisoformat(seven_days_ago),
        models.WorkoutSession.ended_at != None,
    ).count()

    checkins = db.query(models.DailyCheckIn).filter(
        models.DailyCheckIn.chat_id == current_user_id,
        models.DailyCheckIn.date >= seven_days_ago,
    ).all()
    avg_recovery = round(sum(c.recovery_score or 0 for c in checkins) / len(checkins)) if checkins else None

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_meals = db.query(models.MealLog).filter(
        models.MealLog.chat_id == current_user_id,
        models.MealLog.date == today_str,
    ).all()

    latest_analysis = (
        db.query(models.BodyAnalysis)
        .order_by(models.BodyAnalysis.created_at.desc())
        .first()
    )

    badges = db.query(models.Badge).filter(models.Badge.chat_id == current_user_id).count()

    return {
        "streaks": streaks,
        "prs_count": prs_count,
        "badges_count": badges,
        "sessions_this_week": sessions_week,
        "avg_recovery_7d": avg_recovery,
        "today_protein_g": round(sum(m.protein_g or 0 for m in today_meals), 1),
        "today_calories": round(sum(m.calories or 0 for m in today_meals)),
        "latest_bf": latest_analysis.body_fat_estimate if latest_analysis else None,
        "latest_score": latest_analysis.overall_physique_score if latest_analysis else None,
    }


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    api_key_set = bool(os.getenv("ANTHROPIC_API_KEY"))
    return {
        "status": "ok",
        "api_key_configured": api_key_set,
        "timestamp": datetime.utcnow().isoformat(),
    }
