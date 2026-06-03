import asyncio
import base64
import hashlib
import hmac
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

load_dotenv()

# ── VAPID config for Web Push ─────────────────────────────────────────────────
_VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
_VAPID_PUBLIC_KEY  = os.getenv("VAPID_PUBLIC_KEY", "")
_VAPID_EMAIL       = os.getenv("VAPID_EMAIL", "")

# ── Auth helpers (stdlib only — no cryptography dependency) ───────────────────

_SECRET_KEY = os.getenv("SECRET_KEY", "change-me-use-a-long-random-string-in-production").encode()
_BOT_SECRET = os.getenv("BOT_SECRET", "")
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
    request: Request = None,
) -> int:
    """Return user_id from Bearer JWT or bot-secret headers. Returns 0 when unauthenticated.

    Bot calls pass:
      X-Bot-Secret: <BOT_SECRET>
      X-Telegram-User-ID: <web user_id obtained after /link>
    """
    # ── 1. JWT Bearer token (web app) ─────────────────────────────────────────
    if credentials:
        try:
            parts = credentials.credentials.split(".")
            if len(parts) == 3:
                header_b64, payload_b64, sig_b64 = parts
                signing_input = f"{header_b64}.{payload_b64}".encode()
                expected_sig = _b64url(hmac.new(_SECRET_KEY, signing_input, hashlib.sha256).digest())
                if hmac.compare_digest(expected_sig, sig_b64):
                    payload = json.loads(_b64url_decode(payload_b64))
                    if payload.get("exp", 0) >= datetime.now(timezone.utc).timestamp():
                        return int(payload.get("sub", 0))
        except Exception:
            pass

    # ── 2. Bot-secret + Telegram user_id headers (Telegram bot API calls) ────
    if _BOT_SECRET and request:
        bot_secret = request.headers.get("X-Bot-Secret", "")
        if bot_secret and hmac.compare_digest(bot_secret, _BOT_SECRET):
            uid_str = request.headers.get("X-Telegram-User-ID", "")
            if uid_str.lstrip("-").isdigit():
                return int(uid_str)

    return 0


from database import engine, get_db
import models
from claude_service import (
    analyze_body_photo,
    epley_1rm,
    estimate_meal_macros,
    generate_comprehensive_plan,
    generate_next_session_targets,
    generate_recovery_insight,
    generate_weekly_report,
    analyze_weak_points,
    get_goal_system_prompt,
)
from research_service import refresh_all_research
from coach_brain import build_context, CoachBrainError
from prompt_builder import context_block

models.Base.metadata.create_all(bind=engine)

# ── Safe column migrations (idempotent ALTER TABLE for schema evolution) ──────
def _migrate_db():
    """Add columns and indexes that may not exist in pre-existing databases."""
    from sqlalchemy import text
    migrations = [
        ("user_profiles", "user_id", "INTEGER REFERENCES users(id)"),
        ("user_profiles", "equipment_available", "VARCHAR"),
        ("user_profiles", "injuries", "TEXT"),
        ("user_profiles", "show_date", "DATE"),
        ("body_analyses", "user_id", "INTEGER"),
        ("body_analyses", "body_fat_confidence", "VARCHAR"),
        ("workout_sessions", "user_id", "INTEGER REFERENCES users(id)"),
        ("workout_sessions", "next_session_targets", "TEXT"),
        ("weekly_reports", "next_week_focus", "TEXT"),
        ("weekly_reports", "adherence_rating", "VARCHAR"),
        # Sprint 15: per-user plan rows from bot sync
        ("workout_plans", "user_id", "INTEGER"),
        ("diet_plans", "user_id", "INTEGER"),
        ("supplement_plans", "user_id", "INTEGER"),
        # Sprint 22: RPE, RIR, set notes on set_logs
        ("set_logs", "rpe", "INTEGER"),
        ("set_logs", "rir", "INTEGER"),
        ("set_logs", "set_notes", "TEXT"),
    ]
    indexes = [
        "CREATE INDEX IF NOT EXISTS ix_daily_checkins_chat_date ON daily_checkins(chat_id, date)",
        "CREATE INDEX IF NOT EXISTS ix_set_logs_exercise_logged ON set_logs(exercise_name, logged_at)",
        "CREATE INDEX IF NOT EXISTS ix_meal_logs_chat_date ON meal_logs(chat_id, date)",
        "CREATE INDEX IF NOT EXISTS ix_body_measurements_chat_date ON body_measurements(chat_id, date)",
        "CREATE INDEX IF NOT EXISTS ix_prs_chat_exercise_1rm ON personal_records(chat_id, exercise_name, estimated_1rm)",
        "CREATE INDEX IF NOT EXISTS ix_workout_sessions_user_id ON workout_sessions(user_id)",
    ]
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
            except Exception:
                pass  # Column already exists
        for ddl in indexes:
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception:
                pass  # Index already exists

_migrate_db()

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


def _send_web_push(subscription_dict: dict, title: str, body: str) -> None:
    """Send a Web Push notification. Blocking call for executor."""
    try:
        from pywebpush import webpush
        webpush(
            subscription_info=subscription_dict,
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=_VAPID_PRIVATE_KEY,
            vapid_claims={"sub": _VAPID_EMAIL or "mailto:admin@example.com"},
        )
    except Exception as e:
        print(f"Web Push send failed: {e}")


async def _build_weakpoint_research(user_id: int) -> None:
    """Fetch → grade → synthesize → judge → verify → cache cited research for one user's
    weak points (research-integration T9).

    Runs on a new BodyAnalysis (FastAPI BackgroundTask) and on the weekly safety-net
    cron. Never fabricates: the integrity gate degrades to an honest note on empty.
    Pipeline matches the eng-review architecture (D2 membership+claim-match, A4 batched).
    """
    if not user_id:
        return
    from database import SessionLocal
    import research_service
    from claude_service import synthesize_weakpoint_report, judge_citation_claims

    db = SessionLocal()
    try:
        analysis = (
            db.query(models.BodyAnalysis)
            .filter(models.BodyAnalysis.user_id == user_id)
            .order_by(models.BodyAnalysis.created_at.desc())
            .first()
        )
        if not analysis:
            return
        try:
            areas = json.loads(analysis.areas_to_improve) if analysis.areas_to_improve else []
        except Exception:
            areas = []
        weak_points = [a for a in areas if isinstance(a, str) and a.strip()][:4]  # cap fan-out
        if not weak_points:
            return
        profile = db.query(models.UserProfile).filter(
            models.UserProfile.user_id == user_id
        ).first()
        goal = (profile.goal if profile else "") or ""

        # 1. Fetch + grade per weak point (throttled inside research_service).
        fetched = []
        for wp in weak_points:
            fetched.append(await research_service.fetch_weakpoint_research(wp, goal))

        # 2. Synthesize ONE batched cited report (sync Claude → executor, async contract).
        blocks = [
            {
                "weak_point": f["topic"],
                "goal": goal,
                "papers": [
                    {
                        "pmid": p.get("pmid"),
                        "title": p.get("title"),
                        "year": p.get("year"),
                        "evidence_label": p.get("evidence_label"),
                        "abstract": (p.get("abstract") or "")[:600],
                    }
                    for p in (f["papers"] or [])[:5]
                    if p.get("pmid")
                ],
            }
            for f in fetched
        ]
        loop = asyncio.get_running_loop()
        synth = await loop.run_in_executor(None, synthesize_weakpoint_report, blocks)
        reports = synth.get("reports", []) if isinstance(synth, dict) else []
        reports_by_topic = {r.get("weak_point"): r for r in reports if isinstance(r, dict)}

        # 3. Judge claims (batched) + 4. verify membership, then persist per weak point.
        for f in fetched:
            topic = f["topic"]
            abstracts = {
                str(p.get("pmid")): (p.get("abstract") or "")
                for p in f["papers"] if p.get("pmid")
            }
            fetched_pmids = set(abstracts.keys())
            report = reports_by_topic.get(topic) or {
                "weak_point": topic, "summary": "", "citations": []
            }
            pairs = [
                {
                    "pmid": str(c.get("pmid")),
                    "claim": c.get("claim", ""),
                    "abstract": abstracts.get(str(c.get("pmid")), ""),
                }
                for c in (report.get("citations") or [])
                if isinstance(c, dict) and str(c.get("pmid")) in fetched_pmids
            ]
            supported = (
                await loop.run_in_executor(None, judge_citation_claims, pairs)
                if pairs else {}
            )
            allowed = {pmid for pmid in fetched_pmids if supported.get(pmid, False)}
            verified = research_service.verify_report_citations(report, allowed)

            existing = db.query(models.ResearchCache).filter(
                models.ResearchCache.topic == topic
            ).first()
            payload = json.dumps(f["papers"])
            summary = verified.get("summary", "")
            if existing:
                existing.papers = payload
                existing.summary = summary
                existing.last_updated = datetime.now(timezone.utc)
            else:
                db.add(models.ResearchCache(topic=topic, papers=payload, summary=summary))
        db.commit()
    except Exception as e:
        print(f"Warning: weak-point research build failed for user {user_id}: {e}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


async def _refresh_weakpoint_research() -> None:
    """Weekly safety-net (Sun 07:00 UTC): rebuild weak-point research for active users."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        user_ids = [
            u.id for u in db.query(models.User).filter(models.User.is_active == True).all()
        ]
    finally:
        db.close()
    for uid in user_ids:
        await _build_weakpoint_research(uid)


async def _generate_morning_briefings() -> None:
    """Daily 06:00 UTC: generate morning briefing for every active user.

    Hero feature: answers "What should I do today?" by synthesizing Garmin (recovery),
    MFP (nutrition), checkins, sessions, and profile. Generated nightly so briefing
    is ready on app open with zero latency. Uses Haiku — cheap, fast, sufficient.
    Upserts on (user_id, date) so scheduler restarts don't create duplicate rows.
    Per-user try/except: one user failing never blocks the rest.
    """
    from database import SessionLocal
    from claude_service import generate_morning_briefing
    import garmin_service

    today = datetime.now(timezone.utc).date().isoformat()
    three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat()

    db = SessionLocal()
    try:
        active_users = db.query(models.User).filter(models.User.is_active == True).all()
        for user in active_users:
            try:
                # Skip if already generated today
                existing = db.query(models.DailyBriefing).filter(
                    models.DailyBriefing.user_id == user.id,
                    models.DailyBriefing.date == today,
                ).first()
                if existing:
                    continue

                # Gather data sources
                checkin = db.query(models.DailyCheckIn).filter(
                    models.DailyCheckIn.chat_id == user.id,
                ).order_by(models.DailyCheckIn.created_at.desc()).first()

                sessions = db.query(models.WorkoutSession).filter(
                    models.WorkoutSession.chat_id == user.id,
                    models.WorkoutSession.ended_at != None,
                    models.WorkoutSession.started_at >= datetime.fromisoformat(three_days_ago),
                ).order_by(models.WorkoutSession.started_at.desc()).limit(3).all()

                profile = db.query(models.UserProfile).filter(
                    models.UserProfile.user_id == user.id
                ).first()

                garmin_data = garmin_service.get_cached(user.id)

                checkin_dict = None
                if checkin:
                    checkin_dict = {
                        "sleep_score": checkin.sleep_score,
                        "energy_score": checkin.energy_score,
                        "soreness_score": checkin.soreness_score,
                        "recovery_score": checkin.recovery_score,
                    }

                session_list = []
                for s in sessions:
                    days_ago = (datetime.now(timezone.utc).date() - s.started_at.date()).days
                    set_count = db.query(models.SetLog).filter(
                        models.SetLog.session_id == s.id
                    ).count()
                    session_list.append({"days_ago": days_ago, "set_count": set_count})

                profile_dict = None
                if profile:
                    profile_dict = {"goal": profile.goal, "experience": profile.training_experience}

                data_sources = {
                    "garmin": garmin_data is not None,
                    "checkin": checkin_dict is not None,
                    "sessions": len(session_list),
                }

                loop = asyncio.get_running_loop()
                text = await loop.run_in_executor(
                    None,
                    generate_morning_briefing,
                    checkin_dict, session_list, garmin_data, [], profile_dict,
                )

                briefing = models.DailyBriefing(
                    user_id=user.id,
                    date=today,
                    briefing_text=text,
                    data_sources=json.dumps(data_sources),
                )
                db.add(briefing)
                db.commit()

                # Send Web Push notification if user has a subscription
                sub = db.query(models.PushSubscription).filter(
                    models.PushSubscription.user_id == user.id
                ).first()
                if sub and _VAPID_PRIVATE_KEY and _VAPID_PUBLIC_KEY:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        _send_web_push,
                        {
                            "endpoint": sub.endpoint,
                            "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                        },
                        "Morning Briefing",
                        text[:120],
                    )

            except Exception as e:
                print(f"Warning: Morning briefing failed for user {user.id}: {e}")
                try:
                    db.rollback()
                except Exception:
                    pass
    finally:
        db.close()


async def _auto_weekly_reports() -> None:
    """Sunday 8:00 UTC: generate weekly reports for all Pro/Elite users who don't have one yet."""
    from database import SessionLocal
    monday = (datetime.now(timezone.utc) - timedelta(days=datetime.now(timezone.utc).weekday())).strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        pro_users = db.query(models.User).filter(
            models.User.subscription_tier.in_(["pro", "elite"]),
            models.User.is_active == True,
        ).all()
        for user in pro_users:
            existing = db.query(models.WeeklyReport).filter(
                models.WeeklyReport.chat_id == user.id,
                models.WeeklyReport.week_start == monday,
            ).first()
            if existing:
                continue
            try:
                seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
                sessions = db.query(models.WorkoutSession).filter(
                    models.WorkoutSession.chat_id == user.id,
                    models.WorkoutSession.ended_at != None,
                    models.WorkoutSession.started_at >= datetime.fromisoformat(seven_days_ago),
                ).all()
                checkins = db.query(models.DailyCheckIn).filter(
                    models.DailyCheckIn.chat_id == user.id,
                    models.DailyCheckIn.date >= seven_days_ago,
                ).all()
                meals = db.query(models.MealLog).filter(
                    models.MealLog.chat_id == user.id,
                    models.MealLog.date >= seven_days_ago,
                ).all()
                prs = db.query(models.PersonalRecord).filter(
                    models.PersonalRecord.chat_id == user.id,
                ).order_by(models.PersonalRecord.achieved_at.desc()).limit(10).all()
                profile = db.query(models.UserProfile).filter(
                    models.UserProfile.user_id == user.id
                ).first()
                profile_dict = {"goal": profile.goal, "experience": profile.training_experience} if profile else None
                sessions_data = [{"id": s.id, "started_at": s.started_at.isoformat()} for s in sessions]
                checkins_data = [{"recovery_score": c.recovery_score} for c in checkins]
                meals_data = [{"protein_g": m.protein_g} for m in meals]
                prs_data = [{"exercise_name": p.exercise_name, "weight_kg": p.weight_kg, "reps": p.reps} for p in prs]
                report_data = await asyncio.to_thread(
                    generate_weekly_report, sessions_data, checkins_data, meals_data, prs_data, profile_dict
                )
                report = models.WeeklyReport(
                    chat_id=user.id, week_start=monday,
                    sessions_count=len(sessions),
                    avg_recovery=report_data.get("avg_recovery"),
                    prs_count=len(prs_data),
                    avg_protein_g=report_data.get("avg_protein_g"),
                    ai_insights=json.dumps(report_data.get("insights", [])),
                    next_week_focus=report_data.get("next_week_focus"),
                    adherence_rating=report_data.get("adherence_rating"),
                )
                db.add(report)
                db.commit()
            except Exception as e:
                print(f"Warning: Auto-report failed for user {user.id}: {e}")
    finally:
        db.close()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_generate_morning_briefings, "cron", hour=6, minute=0)
    scheduler.add_job(_auto_weekly_reports, "cron", day_of_week="sun", hour=8, minute=0)
    scheduler.add_job(_refresh_weakpoint_research, "cron", day_of_week="sun", hour=7, minute=0)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="BodyBuilding Coach AI", version="1.0.0", lifespan=_lifespan)

class _StaticCacheMiddleware(BaseHTTPMiddleware):
    """Set Cache-Control on versioned static assets."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/") and "v=" in str(request.url.query):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=3600"
        return response


app.add_middleware(_StaticCacheMiddleware)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@app.get("/")
def root():
    # Never let the Telegram webview / browser cache the HTML shell, or it keeps
    # loading a stale app.js?v= reference and never picks up new deploys. The
    # versioned JS/CSS stay immutable; only this shell must always revalidate.
    return FileResponse(
        "static/index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


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
        "telegram_linked": user.telegram_chat_id is not None,
        "created_at": user.created_at.isoformat(),
    }


@app.get("/api/subscription")
def get_subscription(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Return the current user's subscription tier (defaults to free)."""
    tier = "free"
    if current_user_id:
        user = db.query(models.User).filter(models.User.id == current_user_id).first()
        if user and user.subscription_tier:
            tier = user.subscription_tier
    return {"tier": tier}


# ── Telegram Mini App auth ────────────────────────────────────────────────────

_TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


def _validate_telegram_init_data(init_data: str) -> dict | None:
    """Validate Telegram WebApp initData HMAC and return parsed user dict or None."""
    from urllib.parse import parse_qsl, unquote
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash or not _TELEGRAM_BOT_TOKEN:
            return None
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret_key = hmac.new(b"WebAppData", _TELEGRAM_BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            return None
        import json as _json
        return _json.loads(unquote(pairs.get("user", "{}")))
    except Exception:
        return None


@app.post("/api/auth/telegram-webapp")
async def telegram_webapp_auth(request: Request, db: Session = Depends(get_db)):
    """Validate Telegram Mini App initData and return a JWT. Creates account if needed."""
    data = await request.json()
    init_data = (data.get("init_data") or "").strip()
    if not _TELEGRAM_BOT_TOKEN:
        # Distinguish "server misconfigured" from "bad signature" — without this the
        # Mini App can't tell why sign-in failed and the dashboard just goes blank.
        raise HTTPException(
            status_code=503,
            detail="Telegram login is not configured on the server (TELEGRAM_BOT_TOKEN missing). Set it on the web service and redeploy.",
        )
    tg_user = _validate_telegram_init_data(init_data)
    if not tg_user:
        raise HTTPException(status_code=401, detail="Invalid Telegram initData (signature mismatch).")

    tg_chat_id = tg_user.get("id")
    if not tg_chat_id:
        raise HTTPException(status_code=400, detail="No user ID in initData.")

    user = db.query(models.User).filter(models.User.telegram_chat_id == tg_chat_id).first()
    if not user:
        # Auto-create a stub account for this Telegram user
        first = tg_user.get("first_name", "")
        last = tg_user.get("last_name", "")
        username = tg_user.get("username", "")
        email = f"tg_{tg_chat_id}@telegram.local"
        user = models.User(
            email=email,
            hashed_password="",
            telegram_chat_id=tg_chat_id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    display = tg_user.get("first_name") or tg_user.get("username") or f"User {tg_chat_id}"
    return {
        "token": _create_token(user.id),
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": display,
            "subscription_tier": user.subscription_tier,
        },
    }


# ── Telegram account linking ──────────────────────────────────────────────────

@app.post("/api/internal/link-code/generate")
async def generate_link_code(request: Request, db: Session = Depends(get_db)):
    """Bot calls this to create a one-time link code for a Telegram chat_id."""
    if not _BOT_SECRET:
        raise HTTPException(status_code=503, detail="BOT_SECRET not configured.")
    bot_secret = request.headers.get("X-Bot-Secret", "")
    if not bot_secret or not hmac.compare_digest(bot_secret, _BOT_SECRET):
        raise HTTPException(status_code=403, detail="Invalid bot secret.")
    chat_id_str = request.headers.get("X-Chat-ID", "")
    if not chat_id_str.lstrip("-").isdigit():
        raise HTTPException(status_code=400, detail="X-Chat-ID header required.")
    chat_id = int(chat_id_str)

    # Invalidate existing unused codes for this chat_id
    db.query(models.TelegramLinkCode).filter(
        models.TelegramLinkCode.telegram_chat_id == chat_id,
        models.TelegramLinkCode.used_at == None,
    ).delete()

    code = os.urandom(3).hex().upper()  # 6 hex chars e.g. "A3F7B2"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    link = models.TelegramLinkCode(
        code=code, telegram_chat_id=chat_id, expires_at=expires_at
    )
    db.add(link)
    db.commit()
    return {"code": code, "expires_in_seconds": 600}


@app.post("/api/auth/link-telegram")
async def link_telegram(
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Web user submits a link code to bind their account to a Telegram chat_id."""
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Sign in first.")
    data = await request.json()
    code = (data.get("code") or "").strip().upper()
    if not code:
        raise HTTPException(status_code=400, detail="code is required.")

    link = db.query(models.TelegramLinkCode).filter(
        models.TelegramLinkCode.code == code,
        models.TelegramLinkCode.used_at == None,
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Code not found or already used.")
    if link.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Code has expired. Use /link in Telegram to get a new one.")

    user = db.query(models.User).filter(models.User.id == current_user_id).first()
    if not user:
        raise HTTPException(
            status_code=404,
            detail=(
                "Account not found — the server may have restarted and your session is stale. "
                "Please sign out (top-right), register again with your email, "
                "then use /link in Telegram for a new code."
            ),
        )

    # Check if this Telegram chat_id is already linked to another account
    existing = db.query(models.User).filter(
        models.User.telegram_chat_id == link.telegram_chat_id,
        models.User.id != current_user_id,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="This Telegram account is already linked to another user.")

    user.telegram_chat_id = link.telegram_chat_id
    link.used_at = datetime.now(timezone.utc)
    link.user_id = current_user_id
    db.commit()

    # Notify via Telegram (fire and forget)
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if bot_token:
        try:
            await asyncio.to_thread(
                lambda: httpx.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": link.telegram_chat_id,
                        "text": (
                            f"✅ *Telegram account linked!*\n\n"
                            f"Your Telegram is now connected to *{user.email}*.\n\n"
                            f"Your web user ID is `{user.id}` — the bot will use this for "
                            f"future API calls. Type /link\\-status to confirm."
                        ),
                        "parse_mode": "Markdown",
                    },
                    timeout=5,
                )
            )
        except Exception:
            pass

    return {
        "linked": True,
        "user_id": user.id,
        "email": user.email,
        "telegram_chat_id": user.telegram_chat_id,
    }


@app.get("/api/internal/telegram/{chat_id}/user")
def get_telegram_user(chat_id: int, request: Request, db: Session = Depends(get_db)):
    """Bot calls this to resolve a chat_id to a web user_id after linking."""
    if not _BOT_SECRET:
        raise HTTPException(status_code=503, detail="BOT_SECRET not configured.")
    bot_secret = request.headers.get("X-Bot-Secret", "")
    if not bot_secret or not hmac.compare_digest(bot_secret, _BOT_SECRET):
        raise HTTPException(status_code=403, detail="Invalid bot secret.")
    user = db.query(models.User).filter(models.User.telegram_chat_id == chat_id).first()
    if not user:
        return {"linked": False, "chat_id": chat_id}
    return {
        "linked": True,
        "user_id": user.id,
        "email": user.email,
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
        "show_date": str(profile.show_date) if profile.show_date else None,
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
    if data.get("show_date"):
        profile.show_date = data["show_date"]
    db.commit()
    return {"status": "saved"}


# ── Body Analysis ─────────────────────────────────────────────────────────────

@app.post("/api/analyze")
async def analyze_photo(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")
    if len(files) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 photos per analysis.")

    saved_paths: list[str] = []
    saved_filenames: list[str] = []
    for file in files:
        ext = Path(file.filename or "photo.jpg").suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"File type {ext} not supported. Use JPG, PNG, or WebP.")
        content = await file.read()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File too large. Max 20MB per photo.")
        filename = f"{uuid.uuid4()}{ext}"
        filepath = UPLOAD_DIR / filename
        with open(filepath, "wb") as f:
            f.write(content)
        saved_paths.append(str(filepath))
        saved_filenames.append(filename)

    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == current_user_id).first()
    prev = (
        db.query(models.BodyAnalysis)
        .filter(models.BodyAnalysis.user_id == current_user_id)
        .order_by(models.BodyAnalysis.created_at.desc())
        .first()
    )

    try:
        result = await asyncio.to_thread(analyze_body_photo, saved_paths, profile, prev)
    except ValueError as e:
        print(f"[ANALYZE] ValueError (often missing/invalid ANTHROPIC_API_KEY or unparseable response): {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        import traceback
        print(f"[ANALYZE] {type(e).__name__}: {e}")
        traceback.print_exc()
        # Include the exception type in the detail so the client can show what
        # actually broke (e.g. NotFoundError = bad model id, AuthenticationError
        # = bad key, BadRequestError = image rejected).
        raise HTTPException(status_code=500, detail=f"Analysis failed ({type(e).__name__}): {e}")

    analysis = models.BodyAnalysis(
        user_id=current_user_id or None,
        photo_path=saved_filenames[0],
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

    # New analysis ⇒ weak points changed ⇒ rebuild cited research in the background
    # (research-integration D3: on-analysis trigger; keeps the response fast).
    if current_user_id:
        background_tasks.add_task(_build_weakpoint_research, current_user_id)

    return {
        "analysis_id": analysis.id,
        "analysis": result,
        "photo_url": f"/uploads/{saved_filenames[0]}",
        "photo_urls": [f"/uploads/{fn}" for fn in saved_filenames],
        "created_at": analysis.created_at.isoformat(),
    }


@app.get("/api/analyses")
def list_analyses(
    limit: int = 20,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(models.BodyAnalysis)
        .filter(models.BodyAnalysis.user_id == current_user_id)
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


@app.post("/api/analysis/weak-points")
async def get_weak_points(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    analyses_rows = (
        db.query(models.BodyAnalysis)
        .filter(models.BodyAnalysis.user_id == current_user_id)
        .order_by(models.BodyAnalysis.created_at.desc())
        .limit(5)
        .all()
    )
    if not analyses_rows:
        raise HTTPException(status_code=404, detail="No body analyses found. Upload a photo first.")
    analyses_data = [json.loads(r.raw_analysis) if r.raw_analysis else {} for r in analyses_rows]

    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    sessions = db.query(models.WorkoutSession).filter(
        models.WorkoutSession.chat_id == current_user_id,
        models.WorkoutSession.started_at >= thirty_days_ago,
    ).all()
    set_logs_data = []
    if sessions:
        session_ids = [s.id for s in sessions]
        sets = db.query(models.SetLog).filter(
            models.SetLog.session_id.in_(session_ids)
        ).all()
        set_logs_data = [{"exercise_name": s.exercise_name} for s in sets]

    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == current_user_id).first()
    profile_dict = {"goal": profile.goal, "experience": profile.training_experience} if profile else None

    ctx_str = ""
    try:
        ctx = await build_context(db, current_user_id)
        ctx_str = context_block(ctx)
    except CoachBrainError:
        pass

    try:
        result = await asyncio.to_thread(
            analyze_weak_points, analyses_data, set_logs_data, profile_dict, ctx_str
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Weak-point analysis failed: {e}")
    return result


# ── Plans ─────────────────────────────────────────────────────────────────────

@app.post("/api/plan/generate")
async def generate_plan(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    analysis = (
        db.query(models.BodyAnalysis)
        .filter(models.BodyAnalysis.user_id == current_user_id)
        .order_by(models.BodyAnalysis.created_at.desc())
        .first()
    )
    if not analysis:
        raise HTTPException(
            status_code=400,
            detail="No body analysis found. Upload a photo first.",
        )

    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == current_user_id).first()
    research_cache = db.query(models.ResearchCache).all()

    ctx_str = ""
    try:
        ctx = await build_context(db, current_user_id)
        ctx_str = context_block(ctx)
    except CoachBrainError:
        pass

    try:
        # Run the synchronous Claude call in a thread pool so it doesn't block
        # the event loop while waiting for the ~30-60s API response.
        plan = await asyncio.to_thread(
            generate_comprehensive_plan, analysis, research_cache, profile, ctx_str
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plan generation failed: {e}")

    workout_data = plan.get("workout_plan", {})
    diet_data = plan.get("diet_plan", {})
    supps_data = plan.get("supplement_plan", [])

    workout = models.WorkoutPlan(raw_plan=json.dumps(workout_data), user_id=current_user_id or None)
    diet = models.DietPlan(
        calories=diet_data.get("daily_calories"),
        protein_g=diet_data.get("macros", {}).get("protein_g"),
        carbs_g=diet_data.get("macros", {}).get("carbs_g"),
        fat_g=diet_data.get("macros", {}).get("fat_g"),
        raw_plan=json.dumps(diet_data),
        user_id=current_user_id or None,
    )
    supps = models.SupplementPlan(raw_plan=json.dumps(supps_data), user_id=current_user_id or None)

    db.add_all([workout, diet, supps])
    db.commit()

    return plan


@app.post("/api/plan/generate/stream")
async def stream_plan_generate(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Stream plan generation progress as Server-Sent Events."""
    if not current_user_id:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    async def event_gen():
        try:
            yield f'data: {json.dumps({"status": "Fetching your profile and history…"})}\n\n'

            profile = db.query(models.UserProfile).filter(
                models.UserProfile.user_id == current_user_id
            ).first()
            latest_analysis = (
                db.query(models.BodyAnalysis)
                .filter(models.BodyAnalysis.user_id == current_user_id)
                .order_by(models.BodyAnalysis.created_at.desc())
                .first()
            )
            research_cache = db.query(models.ResearchCache).all()

            if not profile:
                yield f'data: {json.dumps({"error": "Complete your profile first before generating a plan."})}\n\n'
                return

            if not latest_analysis:
                yield f'data: {json.dumps({"error": "Upload a physique photo first — the AI needs it to personalize your plan."})}\n\n'
                return

            yield f'data: {json.dumps({"status": "Sending to Claude AI… (30–60 seconds)"})}\n\n'

            ctx_str = ""
            try:
                ctx = await build_context(db, current_user_id)
                ctx_str = context_block(ctx)
            except CoachBrainError:
                pass

            try:
                plan_result = await asyncio.to_thread(
                    generate_comprehensive_plan,
                    latest_analysis,
                    research_cache,
                    profile,
                    ctx_str,
                )
            except Exception as e:
                yield f'data: {json.dumps({"error": f"Plan generation failed: {str(e)}"})}\n\n'
                return

            yield f'data: {json.dumps({"status": "Saving your plan…"})}\n\n'

            workout_data = plan_result.get("workout_plan", {})
            diet_data = plan_result.get("diet_plan", {})
            supps_data = plan_result.get("supplement_plan", [])

            workout = models.WorkoutPlan(
                raw_plan=json.dumps(workout_data),
                user_id=current_user_id or None,
            )
            diet = models.DietPlan(
                calories=diet_data.get("daily_calories"),
                protein_g=diet_data.get("macros", {}).get("protein_g"),
                carbs_g=diet_data.get("macros", {}).get("carbs_g"),
                fat_g=diet_data.get("macros", {}).get("fat_g"),
                raw_plan=json.dumps(diet_data),
                user_id=current_user_id or None,
            )
            supps = models.SupplementPlan(
                raw_plan=json.dumps(supps_data),
                user_id=current_user_id or None,
            )
            db.add_all([workout, diet, supps])
            db.commit()

            yield f'data: {json.dumps({"status": "done"})}\n\n'

        except Exception as e:
            yield f'data: {json.dumps({"error": str(e)})}\n\n'

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/plan/current")
def get_current_plan(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    def _latest_plan(model):
        q = db.query(model)
        if current_user_id:
            # Prefer user-specific row; fall back to any row if none found
            user_row = (
                q.filter(model.user_id == current_user_id)
                .order_by(model.created_at.desc())
                .first()
            )
            return user_row or q.order_by(model.created_at.desc()).first()
        return q.order_by(model.created_at.desc()).first()

    workout = _latest_plan(models.WorkoutPlan)
    diet = _latest_plan(models.DietPlan)
    supps = _latest_plan(models.SupplementPlan)
    analysis = (
        db.query(models.BodyAnalysis)
        .filter(models.BodyAnalysis.user_id == current_user_id)
        .order_by(models.BodyAnalysis.created_at.desc())
        .first()
    )

    workout_raw = json.loads(workout.raw_plan) if workout else {}
    coaching_notes = workout_raw.get("coaching_notes") or workout_raw.get("text", "")

    return {
        "workout_plan": workout_raw if workout else None,
        "workout_created_at": workout.created_at.isoformat() if workout else None,
        "coaching_notes": coaching_notes,
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


@app.get("/api/research/weak-points")
def get_weakpoint_research_reports(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Cited per-weak-point research reports (research-integration). Reads cache only."""
    import coach_brain

    return coach_brain.get_weakpoint_reports(db)


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
def get_progress(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(models.BodyAnalysis)
        .filter(models.BodyAnalysis.user_id == current_user_id)
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
    session = models.WorkoutSession(
        chat_id=current_user_id,
        user_id=current_user_id if current_user_id else None,
        notes=data.get("notes"),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return {"id": session.id, "started_at": session.started_at.isoformat()}


@app.post("/api/sessions/{session_id}/end")
async def end_session(
    session_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    session = db.query(models.WorkoutSession).filter(models.WorkoutSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.ended_at = datetime.now(timezone.utc)
    sets = db.query(models.SetLog).filter(models.SetLog.session_id == session_id).all()
    total_volume = sum(s.weight_kg * s.reps for s in sets)
    db.commit()

    _update_streak(db, current_user_id, "workout")
    workout_streak = _get_streak(db, current_user_id, "workout")
    streak_count = workout_streak.current_streak if workout_streak else 1
    if streak_count in (7, 14, 30, 60, 90):
        _award_badge(db, current_user_id, f"{streak_count}_day_workout_streak")

    next_session_tip = None
    try:
        profile = db.query(models.UserProfile).filter(
            models.UserProfile.user_id == current_user_id
        ).first() if current_user_id else None
        sets_data = [
            {"exercise_name": s.exercise_name, "weight_kg": s.weight_kg,
             "reps": s.reps, "estimated_1rm": s.estimated_1rm,
             "logged_at": s.logged_at.isoformat()}
            for s in sets
        ]
        profile_dict = {"goal": profile.goal} if profile else None
        next_session_tip = await asyncio.to_thread(
            generate_next_session_targets, sets_data, None, profile_dict
        )
    except Exception:
        pass

    if next_session_tip:
        session.next_session_targets = next_session_tip
        db.commit()

    return {
        "status": "ended",
        "set_count": len(sets),
        "total_volume_kg": round(total_volume, 1),
        "exercises": list({s.exercise_name for s in sets}),
        "workout_streak": streak_count,
        "next_session_targets": next_session_tip,
    }


@app.post("/api/sessions/{session_id}/sets")
async def log_set(
    session_id: int,
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    data = await request.json()
    if not all(k in data for k in ("exercise_name", "weight_kg", "reps")):
        raise HTTPException(status_code=400, detail="exercise_name, weight_kg, and reps are required.")
    exercise = data["exercise_name"].strip()
    weight_kg = float(data["weight_kg"])
    reps = int(data["reps"])
    estimated_1rm = epley_1rm(weight_kg, reps)
    rpe = data.get("rpe")
    if rpe is not None:
        rpe = float(rpe)
    rir = data.get("rir")
    if rir is not None:
        rir = int(rir)
    set_notes = data.get("set_notes") or None

    set_log = models.SetLog(
        session_id=session_id,
        exercise_name=exercise,
        weight_kg=weight_kg,
        reps=reps,
        estimated_1rm=estimated_1rm,
        rpe=rpe,
        rir=rir,
        set_notes=set_notes,
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

    if is_pr and current_user_id:
        mem_content = (
            f"New PR: {exercise} — {weight_kg}kg × {reps} reps "
            f"(est. 1RM {estimated_1rm:.1f}kg)"
        )
        if prev_pr:
            mem_content += f"; previous best was {prev_pr['estimated_1rm']:.1f}kg 1RM"
        db.add(models.CoachMemory(
            chat_id=current_user_id,
            content=mem_content,
            memory_type="pr",
        ))
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
        "rpe": set_log.rpe,
        "rir": set_log.rir,
        "set_notes": set_log.set_notes,
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
    session_ids = [s.id for s in sessions]
    all_sets = (
        db.query(models.SetLog)
        .filter(models.SetLog.session_id.in_(session_ids))
        .order_by(models.SetLog.logged_at.asc())
        .all()
    ) if session_ids else []

    # Build a dict for O(1) lookup
    sets_by_session: dict[int, list] = {}
    for sl in all_sets:
        sets_by_session.setdefault(sl.session_id, []).append(sl)

    result = []
    for s in sessions:
        sets_for_session = sets_by_session.get(s.id, [])
        result.append({
            "id": s.id,
            "started_at": s.started_at.isoformat(),
            "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            "set_count": len(sets_for_session),
            "exercises": list({x.exercise_name for x in sets_for_session}),
            "total_volume_kg": round(sum(x.weight_kg * x.reps for x in sets_for_session if x.weight_kg and x.reps), 1),
            "next_session_targets": s.next_session_targets,
            "sets": [
                {
                    "exercise_name": sl.exercise_name,
                    "weight_kg": sl.weight_kg,
                    "reps": sl.reps,
                    "estimated_1rm": sl.estimated_1rm,
                    "rpe": sl.rpe,
                    "set_notes": sl.set_notes,
                }
                for sl in sets_for_session
            ],
        })
    return result


@app.patch("/api/sessions/{session_id}/notes")
async def patch_session_notes(
    session_id: int,
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Update the notes field on a workout session."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    notes = data.get("notes", "")
    session = db.query(models.WorkoutSession).filter(
        models.WorkoutSession.id == session_id,
        models.WorkoutSession.user_id == current_user_id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    session.notes = str(notes)[:2000]
    db.commit()
    return {"updated": session_id}


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


@app.get("/api/sessions/last-sets")
def get_last_sets(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Return most recent weight+reps per exercise for the current user.

    Used by the workout UI to pre-fill the weight input and show the
    progressive overload suggestion before each set.
    """
    # Subquery: latest logged_at per exercise for this user.
    # GROUP BY + MAX works on both SQLite (local) and PostgreSQL (Railway).
    subq = (
        db.query(
            models.SetLog.exercise_name,
            func.max(models.SetLog.logged_at).label("latest"),
        )
        .join(models.WorkoutSession, models.SetLog.session_id == models.WorkoutSession.id)
        .filter(models.WorkoutSession.chat_id == current_user_id)
        .group_by(models.SetLog.exercise_name)
        .subquery()
    )
    rows = (
        db.query(models.SetLog)
        .join(
            subq,
            (models.SetLog.exercise_name == subq.c.exercise_name)
            & (models.SetLog.logged_at == subq.c.latest),
        )
        .all()
    )
    return {
        r.exercise_name: {
            "weight_kg": r.weight_kg,
            "reps": r.reps,
            "logged_at": r.logged_at.isoformat(),
        }
        for r in rows
    }


@app.get("/api/memory")
def list_memories(
    limit: int = 20,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(models.CoachMemory)
        .filter(models.CoachMemory.chat_id == current_user_id)
        .order_by(models.CoachMemory.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {"id": r.id, "content": r.content, "memory_type": r.memory_type,
         "created_at": r.created_at.isoformat()}
        for r in rows
    ]


@app.post("/api/memory")
async def add_memory(
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    data = await request.json()
    content = (data.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    mem = models.CoachMemory(
        chat_id=current_user_id,
        content=content[:500],
        memory_type=data.get("memory_type", "note"),
    )
    db.add(mem)
    db.commit()
    db.refresh(mem)
    return {"id": mem.id, "content": mem.content, "memory_type": mem.memory_type,
            "created_at": mem.created_at.isoformat()}


@app.get("/api/progress/plateaus")
def get_plateaus(
    weeks: int = 4,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Return per-exercise weekly 1RM trend for the past N weeks.

    Exercises where the last 3 weeks show <2% 1RM change are flagged as stalled.
    """
    from collections import defaultdict
    cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    sessions = db.query(models.WorkoutSession).filter(
        models.WorkoutSession.chat_id == current_user_id,
        models.WorkoutSession.started_at >= cutoff,
        models.WorkoutSession.ended_at != None,
    ).all()
    if not sessions:
        return []
    session_ids = [s.id for s in sessions]
    sets = db.query(models.SetLog).filter(
        models.SetLog.session_id.in_(session_ids)
    ).all()

    by_exercise_week: dict[str, dict[str, float]] = defaultdict(dict)
    for s in sets:
        if not s.estimated_1rm:
            continue
        week_key = s.logged_at.strftime("%Y-W%W")
        ex = s.exercise_name
        prev = by_exercise_week[ex].get(week_key, 0)
        if s.estimated_1rm > prev:
            by_exercise_week[ex][week_key] = s.estimated_1rm

    result = []
    for exercise, week_data in by_exercise_week.items():
        weekly = [v for _, v in sorted(week_data.items())]
        if len(weekly) < 2:
            continue
        stalled = False
        if len(weekly) >= 3:
            last3 = weekly[-3:]
            peak = max(last3)
            if peak > 0 and (peak - min(last3)) / peak < 0.02:
                stalled = True
        result.append({
            "exercise": exercise,
            "stalled": stalled,
            "weeks_stalled": 3 if stalled else 0,
            "weekly_trend": [round(v, 1) for v in weekly],
            "current_1rm": round(weekly[-1], 1),
            "peak_1rm": round(max(weekly), 1),
        })

    result.sort(key=lambda x: (-int(x["stalled"]), -x["peak_1rm"]))
    return result


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
            chat_id=chat_id, streak_type=streak_type,
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
        chat_id=chat_id, badge_type=badge_type,
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

    sleep_score = int(data["sleep_score"])
    energy_score = int(data["energy_score"])
    soreness_score = int(data["soreness_score"])
    stress_score = int(data["stress_score"])

    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == current_user_id).first()
    profile_dict = None
    if profile:
        profile_dict = {"age": profile.age, "goal": profile.goal, "experience": profile.training_experience}

    ctx_str = ""
    try:
        ctx = await build_context(db, current_user_id)
        ctx_str = context_block(ctx)
    except CoachBrainError:
        pass

    try:
        recovery_score, coaching_tip = await asyncio.to_thread(
            generate_recovery_insight, sleep_score, energy_score, soreness_score, stress_score,
            profile_dict, ctx_str
        )
    except Exception:
        recovery_score, coaching_tip = 50, "Listen to your body and train accordingly."

    existing = (
        db.query(models.DailyCheckIn)
        .filter(models.DailyCheckIn.chat_id == current_user_id, models.DailyCheckIn.date == today)
        .first()
    )
    if existing:
        existing.sleep_score = sleep_score
        existing.energy_score = energy_score
        existing.soreness_score = soreness_score
        existing.stress_score = stress_score
        existing.recovery_score = recovery_score
        existing.coaching_tip = coaching_tip
        db.commit()
        db.refresh(existing)
        streak = _get_streak(db, current_user_id, "checkin")
        streak_count = streak.current_streak if streak else 1
        return {**_checkin_dict(existing), "streak": streak_count}

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


@app.put("/api/checkins/{checkin_id}")
async def update_checkin(checkin_id: int, request: Request,
    current_user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    """Update any past check-in scores and regenerate recovery coaching tip."""
    checkin = db.query(models.DailyCheckIn).filter(
        models.DailyCheckIn.id == checkin_id,
        models.DailyCheckIn.chat_id == current_user_id,
    ).first()
    if not checkin:
        raise HTTPException(status_code=404, detail="Check-in not found.")

    data = await request.json()
    checkin.sleep_score = int(data["sleep_score"])
    checkin.energy_score = int(data["energy_score"])
    checkin.soreness_score = int(data["soreness_score"])
    checkin.stress_score = int(data["stress_score"])

    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == current_user_id).first()
    profile_dict = None
    if profile:
        profile_dict = {"age": profile.age, "goal": profile.goal, "experience": profile.training_experience}

    ctx_str = ""
    try:
        ctx = await build_context(db, current_user_id)
        ctx_str = context_block(ctx)
    except CoachBrainError:
        pass

    try:
        recovery_score, coaching_tip = await asyncio.to_thread(
            generate_recovery_insight,
            checkin.sleep_score, checkin.energy_score,
            checkin.soreness_score, checkin.stress_score,
            profile_dict, ctx_str,
        )
    except Exception:
        recovery_score, coaching_tip = 50, "Listen to your body and train accordingly."

    checkin.recovery_score = recovery_score
    checkin.coaching_tip = coaching_tip
    db.commit()
    db.refresh(checkin)
    return _checkin_dict(checkin)


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


@app.delete("/api/meals/{meal_id}")
def delete_meal(
    meal_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Delete a meal log entry owned by the authenticated user."""
    meal = db.query(models.MealLog).filter(
        models.MealLog.id == meal_id,
        models.MealLog.chat_id == current_user_id,
    ).first()
    if not meal:
        raise HTTPException(status_code=404, detail="Meal not found.")
    db.delete(meal)
    db.commit()
    return {"deleted": meal_id}


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
            "next_week_focus": r.next_week_focus,
            "adherence_rating": r.adherence_rating,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@app.post("/api/reports/generate")
async def generate_report(request: Request, current_user_id: int = Depends(get_current_user_id), db: Session = Depends(get_db)):
    try:
        data = await request.json()
    except Exception:
        data = {}
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

    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == current_user_id).first()
    profile_dict = {"goal": profile.goal, "experience": profile.training_experience} if profile else None

    sessions_data = [{"id": s.id, "started_at": s.started_at.isoformat()} for s in sessions]
    checkins_data = [{"recovery_score": c.recovery_score} for c in checkins]
    meals_data = [{"protein_g": m.protein_g} for m in meals]
    prs_data = [{"exercise_name": p.exercise_name, "weight_kg": p.weight_kg, "reps": p.reps} for p in prs]

    ctx_str = ""
    try:
        ctx = await build_context(db, current_user_id)
        ctx_str = context_block(ctx)
    except CoachBrainError:
        pass

    try:
        report_data = await asyncio.to_thread(
            generate_weekly_report, sessions_data, checkins_data, meals_data, prs_data,
            profile_dict, ctx_str
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")

    week_start = seven_days_ago
    report = models.WeeklyReport(
        chat_id=current_user_id, week_start=week_start, sessions_count=len(sessions),
        avg_recovery=report_data.get("avg_recovery"), prs_count=len(prs_data),
        avg_protein_g=report_data.get("avg_protein_g"),
        ai_insights=json.dumps(report_data.get("insights", [])),
        next_week_focus=report_data.get("next_week_focus"),
        adherence_rating=report_data.get("adherence_rating"),
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

def _q_streaks_badges(db: Session, uid: int):
    """Query streaks, badge count, and PR count in a single thread."""
    streaks = db.query(models.UserStreak).filter(models.UserStreak.chat_id == uid).all()
    badges_count = db.query(models.Badge).filter(models.Badge.chat_id == uid).count()
    prs_count = db.query(models.PersonalRecord).filter(models.PersonalRecord.chat_id == uid).count()
    return streaks, badges_count, prs_count


def _q_recent_activity(db: Session, uid: int, today_str: str, seven_days_ago: str):
    """Query check-ins for the last 7 days and meals logged today."""
    checkins = (
        db.query(models.DailyCheckIn)
        .filter(
            models.DailyCheckIn.chat_id == uid,
            models.DailyCheckIn.date >= seven_days_ago,
        )
        .order_by(models.DailyCheckIn.date.desc())
        .all()
    )
    meals_today = (
        db.query(models.MealLog)
        .filter(
            models.MealLog.chat_id == uid,
            models.MealLog.date == today_str,
        )
        .all()
    )
    return checkins, meals_today


def _q_analysis_goal(db: Session, uid: int):
    """Query the latest body analysis and the current active goal."""
    latest_analysis = (
        db.query(models.BodyAnalysis)
        .filter(models.BodyAnalysis.user_id == uid)
        .order_by(models.BodyAnalysis.created_at.desc())
        .first()
    )
    active_goal = (
        db.query(models.UserGoal)
        .filter(
            models.UserGoal.chat_id == uid,
            models.UserGoal.is_active == True,
        )
        .first()
    )
    return latest_analysis, active_goal


def _q_sessions_profile(db: Session, uid: int, seven_days_ago: str):
    """Query session stats, last session, last check-in, and user profile."""
    sessions_week = (
        db.query(models.WorkoutSession)
        .filter(
            models.WorkoutSession.chat_id == uid,
            models.WorkoutSession.started_at >= seven_days_ago,
            models.WorkoutSession.ended_at != None,
        )
        .count()
    )
    last_session = (
        db.query(models.WorkoutSession)
        .filter(
            models.WorkoutSession.chat_id == uid,
            models.WorkoutSession.ended_at != None,
        )
        .order_by(models.WorkoutSession.ended_at.desc())
        .first()
    )
    last_checkin = (
        db.query(models.DailyCheckIn)
        .filter(models.DailyCheckIn.chat_id == uid)
        .order_by(models.DailyCheckIn.date.desc())
        .first()
    )
    profile = db.query(models.UserProfile).filter(models.UserProfile.user_id == uid).first()
    return sessions_week, last_session, last_checkin, profile


@app.get("/api/dashboard/summary")
async def dashboard_summary(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Return aggregated dashboard metrics, running independent DB batches in parallel."""
    uid = current_user_id
    today_str = datetime.now(timezone.utc).date().isoformat()
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()

    (
        (streaks_rows, badges_count, prs_count),
        (checkins, meals_today),
        (latest_analysis, active_goal),
        (sessions_week, last_session, last_checkin_row, user_profile),
    ) = await asyncio.gather(
        asyncio.to_thread(_q_streaks_badges, db, uid),
        asyncio.to_thread(_q_recent_activity, db, uid, today_str, seven_days_ago),
        asyncio.to_thread(_q_analysis_goal, db, uid),
        asyncio.to_thread(_q_sessions_profile, db, uid, seven_days_ago),
    )

    streaks = {s.streak_type: s.current_streak for s in streaks_rows}
    avg_recovery = round(sum(c.recovery_score or 0 for c in checkins) / len(checkins)) if checkins else None

    days_to_show = None
    if user_profile and user_profile.show_date and user_profile.goal in ("prep", "contest_prep"):
        from datetime import date as _date
        show_d = user_profile.show_date if isinstance(user_profile.show_date, _date) else _date.fromisoformat(str(user_profile.show_date))
        days_to_show = (show_d - _date.today()).days

    goal_progress = None
    if active_goal and user_profile:
        if active_goal.target_weight_kg and user_profile.weight_kg:
            start = active_goal.start_weight_kg or user_profile.weight_kg
            target = active_goal.target_weight_kg
            current_weight = user_profile.weight_kg
            if target != start:
                days_remaining = None
                if active_goal.target_date:
                    days_remaining = (active_goal.target_date - datetime.now(timezone.utc).date()).days
                goal_progress = {
                    "goal_type": active_goal.goal_type,
                    "target_weight_kg": target,
                    "start_weight_kg": start,
                    "current_weight_kg": current_weight,
                    "target_date": active_goal.target_date.isoformat() if active_goal.target_date else None,
                    "days_remaining": days_remaining,
                }

    return {
        "streaks": streaks,
        "prs_count": prs_count,
        "badges_count": badges_count,
        "sessions_this_week": sessions_week,
        "avg_recovery_7d": avg_recovery,
        "today_protein_g": round(sum(m.protein_g or 0 for m in meals_today), 1),
        "today_calories": round(sum(m.calories or 0 for m in meals_today)),
        "latest_bf": latest_analysis.body_fat_estimate if latest_analysis else None,
        "latest_score": latest_analysis.overall_physique_score if latest_analysis else None,
        "last_workout_date": last_session.ended_at.strftime("%Y-%m-%d") if last_session else None,
        "last_checkin_date": last_checkin_row.date if last_checkin_row else None,
        "days_to_show": days_to_show,
        "goal_progress": goal_progress,
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


@app.get("/api/push/public-key")
def get_push_public_key():
    """Get the VAPID public key for Web Push subscription."""
    if not _VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=503, detail="Push notifications not configured")
    return {"public_key": _VAPID_PUBLIC_KEY}


@app.post("/api/push/subscribe")
def push_subscribe(request: Request, db: Session = Depends(get_db)):
    """Store or update a Web Push subscription for the authenticated user."""
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = request.json()
    endpoint = body.get("endpoint")
    p256dh = body.get("keys", {}).get("p256dh")
    auth = body.get("keys", {}).get("auth")

    if not all([endpoint, p256dh, auth]):
        raise HTTPException(status_code=400, detail="Missing subscription fields")

    # Upsert: find by endpoint, update if exists
    sub = db.query(models.PushSubscription).filter(
        models.PushSubscription.endpoint == endpoint
    ).first()
    if sub:
        sub.user_id = user_id
        sub.p256dh = p256dh
        sub.auth = auth
        sub.updated_at = datetime.now(timezone.utc)
    else:
        sub = models.PushSubscription(
            user_id=user_id,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
        )
        db.add(sub)
    db.commit()
    return {"status": "subscribed"}


@app.get("/api/briefing/today")
def get_today_briefing(request: Request, db: Session = Depends(get_db)):
    """Fetch today's morning briefing for the authenticated user."""
    user_id = get_current_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    today = datetime.now(timezone.utc).date().isoformat()
    briefing = db.query(models.DailyBriefing).filter(
        models.DailyBriefing.user_id == user_id,
        models.DailyBriefing.date == today,
    ).first()

    if not briefing:
        return {"briefing_text": None, "data_sources": None}

    data_sources = {}
    if briefing.data_sources:
        try:
            data_sources = json.loads(briefing.data_sources)
        except Exception:
            pass

    return {
        "briefing_text": briefing.briefing_text,
        "data_sources": data_sources,
        "generated_at": briefing.created_at.isoformat(),
    }
