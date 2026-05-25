import json
import os
import uuid
from datetime import datetime
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
async def analyze_photo(file: UploadFile = File(...), db: Session = Depends(get_db)):
    ext = Path(file.filename or "photo.jpg").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type {ext} not supported. Use JPG, PNG, or WebP.")

    filename = f"{uuid.uuid4()}{ext}"
    filepath = UPLOAD_DIR / filename

    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Max 20MB.")

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
def generate_plan(db: Session = Depends(get_db)):
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
        plan = generate_comprehensive_plan(analysis, research_cache, profile)
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
            existing.last_updated = datetime.utcnow()
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


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    api_key_set = bool(os.getenv("ANTHROPIC_API_KEY"))
    return {
        "status": "ok",
        "api_key_configured": api_key_set,
        "timestamp": datetime.utcnow().isoformat(),
    }
