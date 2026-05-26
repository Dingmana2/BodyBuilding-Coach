import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

load_dotenv()

from database import engine, get_db
import models
from claude_service import analyze_body_photo, generate_comprehensive_plan
from research_service import refresh_all_research

models.Base.metadata.create_all(bind=engine)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

app = FastAPI(title="BodyBuilding Coach AI", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@app.get("/")
def root():
    return FileResponse("static/index.html")


# ── Profile ──────────────────────────────────────────────────────────────────

@app.get("/api/profile")
def get_profile(db: Session = Depends(get_db)):
    profile = db.query(models.UserProfile).first()
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
    }


@app.post("/api/profile")
async def save_profile(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    profile = db.query(models.UserProfile).first()
    if not profile:
        profile = models.UserProfile()
        db.add(profile)

    profile.age = data.get("age")
    profile.gender = data.get("gender")
    profile.height_cm = data.get("height_cm")
    profile.weight_kg = data.get("weight_kg")
    profile.goal = data.get("goal")
    profile.training_experience = data.get("training_experience")
    profile.training_days_per_week = data.get("training_days_per_week")
    profile.dietary_restrictions = data.get("dietary_restrictions")
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
def get_active_session(db: Session = Depends(get_db)):
    session = (
        db.query(models.WorkoutSession)
        .filter(models.WorkoutSession.chat_id == 0, models.WorkoutSession.ended_at == None)
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
async def start_session(request: Request, db: Session = Depends(get_db)):
    data = await request.json()
    # Close any orphaned open session
    db.query(models.WorkoutSession).filter(
        models.WorkoutSession.chat_id == 0,
        models.WorkoutSession.ended_at == None,
    ).update({"ended_at": datetime.now(timezone.utc)})
    session = models.WorkoutSession(chat_id=0, notes=data.get("notes"))
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
async def log_set(session_id: int, request: Request, db: Session = Depends(get_db)):
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
            models.PersonalRecord.chat_id == 0,
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
                    chat_id=0,
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
def get_session_history(db: Session = Depends(get_db)):
    sessions = (
        db.query(models.WorkoutSession)
        .filter(
            models.WorkoutSession.chat_id == 0,
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
def get_prs(db: Session = Depends(get_db)):
    rows = (
        db.query(models.PersonalRecord)
        .filter(models.PersonalRecord.chat_id == 0)
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


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    api_key_set = bool(os.getenv("ANTHROPIC_API_KEY"))
    return {
        "status": "ok",
        "api_key_configured": api_key_set,
        "timestamp": datetime.utcnow().isoformat(),
    }
