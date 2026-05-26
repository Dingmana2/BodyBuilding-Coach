import anthropic
import base64
import json
import os
import re
from pathlib import Path

ANALYSIS_MODEL = "claude-opus-4-7"
SUMMARY_MODEL = "claude-haiku-4-5-20251001"

# Goal-specific system prompt variants injected into every AI coaching call.
_GOAL_SYSTEM_PROMPTS: dict[str, str] = {
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
        "Weekly conditioning assessments are critical — track vascularity, muscle fullness, and stage readiness. "
        "Count down to show date; flag if current trajectory won't hit target conditioning. "
        "Posing practice is as important as training — recommend 15 min/day. "
        "Flag any water retention or fullness changes that could indicate dietary issues. "
        "In peak week, adjust carb cycling and water manipulation guidance."
    ),
    "beginner": (
        "You are coaching a BEGINNER athlete. "
        "Use simple, jargon-free language. Explain the 'why' behind every recommendation. "
        "Celebrate every PR and milestone — early wins build the habit. "
        "Use longer progression cycles (add weight every 2–3 sessions, not every session). "
        "Prioritize movement quality and consistency over intensity. "
        "Keep nutrition advice simple: hit protein target, eat mostly whole foods. "
        "Never overwhelm with too many changes at once — one adjustment at a time."
    ),
}

_DEFAULT_SYSTEM_PROMPT = (
    "You are an elite strength coach and sports nutritionist with 20+ years of experience "
    "coaching competitive physique athletes. Be specific, evidence-based, and actionable."
)


def get_goal_system_prompt(goal: str | None) -> str:
    """Return the appropriate system prompt for the athlete's goal."""
    if not goal:
        return _DEFAULT_SYSTEM_PROMPT
    return _GOAL_SYSTEM_PROMPTS.get(goal.lower(), _DEFAULT_SYSTEM_PROMPT)

# Module-level singleton — one client, one connection pool for the process lifetime.
_anthropic_client: anthropic.Anthropic | None = None


def _client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
        _anthropic_client = anthropic.Anthropic(api_key=key)
    return _anthropic_client


def _encode_image(image_path: str) -> tuple[str, str]:
    path = Path(image_path)
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    media_type = media_types.get(path.suffix.lower(), "image/jpeg")
    with open(image_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")
    return data, media_type


def _extract_json(text: str) -> dict:
    # Try fenced blocks first (```json ... ``` or ``` ... ```)
    for pattern in [r"```json\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"]:
        m = re.search(pattern, text)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass
    # Fall back to raw text
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Could not parse Claude response as JSON: {e}\nRaw (first 300 chars): {text[:300]}"
        )


def analyze_body_photo(image_path: str, profile=None, previous_analysis=None) -> dict:
    client = _client()
    img_data, media_type = _encode_image(image_path)

    profile_ctx = ""
    if profile and profile.age:
        profile_ctx = f"""
Athlete Profile:
- Age: {profile.age} | Gender: {profile.gender}
- Height: {profile.height_cm}cm | Weight: {profile.weight_kg}kg
- Goal: {profile.goal} | Experience: {profile.training_experience}
- Training: {profile.training_days_per_week} days/week
"""

    prev_ctx = ""
    if previous_analysis and previous_analysis.raw_analysis:
        prev_data = json.loads(previous_analysis.raw_analysis)
        prev_ctx = f"""
Previous Analysis (for comparison):
- Body Fat: {previous_analysis.body_fat_estimate}
- Score: {previous_analysis.overall_physique_score}/10
- Strengths: {previous_analysis.strengths}
- Areas to improve: {previous_analysis.areas_to_improve}
"""

    prompt = f"""You are an elite physique coach and body composition expert with 20+ years of experience coaching competitive bodybuilders. Analyze this physique photo with the eye of a professional.

{profile_ctx}
{prev_ctx}

Provide a detailed, honest, and constructive assessment. Return ONLY valid JSON with this exact structure:
{{
    "body_fat_estimate": "15-18%",
    "body_fat_confidence": "medium",
    "overall_physique_score": 7.2,
    "muscle_development": {{
        "chest": {{"score": 7, "notes": "Good upper chest development, lower chest needs work"}},
        "back": {{"score": 6, "notes": "Width is decent, thickness/detail needs improvement"}},
        "shoulders": {{"score": 7, "notes": "Good front delt development, laterals are lagging"}},
        "arms": {{"score": 7, "notes": "Bicep peak is good, tricep mass needs work"}},
        "legs": {{"score": 5, "notes": "Quads are underdeveloped relative to upper body"}},
        "core": {{"score": 6, "notes": "Abs visible but obliques need more definition"}}
    }},
    "strengths": [
        "Strong shoulder-to-waist ratio",
        "Good muscle belly fullness in chest"
    ],
    "areas_to_improve": [
        "Leg development significantly behind upper body",
        "Body fat reduction would improve overall conditioning"
    ],
    "symmetry_notes": "Left shoulder appears slightly higher than right. Overall symmetry is good.",
    "conditioning_notes": "Subcutaneous fat is moderate. Vascularity visible in forearms only.",
    "posture_notes": "Slight anterior pelvic tilt observed. Recommend hip flexor mobility work.",
    "priority_improvements": [
        "Prioritize leg training — consider 2x/week dedicated leg days",
        "Reduce calories by 200-300/day to improve conditioning"
    ],
    "progress_vs_previous": "No previous analysis available for comparison.",
    "coach_message": "You have a solid foundation with good upper body mass. The work ahead is clear: bring up those legs and tighten your conditioning. Stay consistent and the results will follow.",
    "disclaimer": "Visual estimates are approximate. A DEXA scan or hydrostatic weighing provides clinical-grade body composition data."
}}

Be specific, honest, and actionable. Score muscle groups 1-10. Only return valid JSON — no other text."""

    message = client.messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": img_data,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    return _extract_json(message.content[0].text)


def generate_comprehensive_plan(analysis, research_cache, profile=None) -> dict:
    client = _client()

    analysis_data = {}
    if analysis and analysis.raw_analysis:
        analysis_data = json.loads(analysis.raw_analysis)

    research_ctx = "No research loaded yet. Base recommendations on established evidence-based practices."
    if research_cache:
        summaries = [
            f"• **{c.topic.title()}**: {c.summary}"
            for c in research_cache
            if c.summary
        ]
        if summaries:
            research_ctx = "\n".join(summaries[:8])

    profile_ctx = "No profile data provided."
    days = 4
    if profile and profile.age:
        days = profile.training_days_per_week or 4
        profile_ctx = f"""
- Age: {profile.age} | Gender: {profile.gender}
- Height: {profile.height_cm}cm | Weight: {profile.weight_kg}kg
- Goal: {profile.goal} | Experience: {profile.training_experience}
- Available training days: {days}/week
- Dietary restrictions: {profile.dietary_restrictions or "None"}
"""

    prompt = f"""You are an elite strength coach, sports nutritionist, and evidence-based fitness expert. Create a comprehensive, personalized program grounded in current science.

ATHLETE PROFILE:
{profile_ctx}

CURRENT BODY ANALYSIS:
- Body Fat: {analysis_data.get("body_fat_estimate", "Unknown")}
- Physique Score: {analysis_data.get("overall_physique_score", "N/A")}/10
- Strengths: {", ".join(analysis_data.get("strengths", []))}
- Priority Improvements: {", ".join(analysis_data.get("priority_improvements", []))}
- Weakest Areas: {", ".join(analysis_data.get("areas_to_improve", []))}
- Muscle Development: {json.dumps(analysis_data.get("muscle_development", {}))}

LATEST SCIENTIFIC RESEARCH:
{research_ctx}

Create a full program. Return ONLY valid JSON with this exact structure:
{{
    "workout_plan": {{
        "weekly_split": "Upper/Lower 4-Day Split",
        "split_rationale": "Why this split fits this athlete's goals and weak points",
        "days": [
            {{
                "day": "Monday",
                "focus": "Upper Body — Push",
                "exercises": [
                    {{
                        "name": "Barbell Bench Press",
                        "sets": 4,
                        "reps": "5-6",
                        "rest_seconds": 180,
                        "rpe": "8-9",
                        "notes": "Full ROM, controlled 2-sec descent",
                        "why": "Primary chest mass builder, highest loading potential"
                    }}
                ],
                "volume_note": "Total working sets: 16-18"
            }}
        ],
        "progression_strategy": "Add 2.5kg when you hit the top of the rep range for all sets across 2 consecutive sessions",
        "deload_protocol": "Every 4-6 weeks: reduce load by 40%, maintain volume. Full week deload if joints are achy.",
        "research_citations": [
            "Schoenfeld et al. (2017): 10-20 sets per muscle per week optimizes hypertrophy",
            "Krieger (2010): Multiple sets superior to single sets for muscle growth"
        ]
    }},
    "diet_plan": {{
        "daily_calories": 2800,
        "goal_phase": "Moderate deficit for recomposition",
        "macros": {{
            "protein_g": 180,
            "carbs_g": 310,
            "fat_g": 78,
            "protein_per_kg": 2.2,
            "macro_rationale": "High protein to preserve/build muscle. Carbs timed around training."
        }},
        "meal_timing": {{
            "pre_workout": "40-60g carbs + 25-30g protein, 90 min before training",
            "post_workout": "40-50g fast carbs + 30-40g protein within 30 min",
            "before_bed": "30-40g casein or cottage cheese to support overnight MPS"
        }},
        "sample_day": [
            {{
                "meal": "Meal 1 — 7:00 AM",
                "foods": ["5 whole eggs scrambled", "2 slices sourdough toast", "1 cup Greek yogurt", "Handful blueberries"],
                "approx_macros": {{"calories": 650, "protein": 48, "carbs": 60, "fat": 22}}
            }},
            {{
                "meal": "Meal 2 — 11:00 AM (Pre-workout)",
                "foods": ["200g chicken breast", "250g white rice", "Mixed greens salad", "Olive oil dressing"],
                "approx_macros": {{"calories": 720, "protein": 52, "carbs": 85, "fat": 14}}
            }},
            {{
                "meal": "Meal 3 — 2:00 PM (Post-workout)",
                "foods": ["Whey protein shake 40g", "Banana", "Rice cakes x2"],
                "approx_macros": {{"calories": 420, "protein": 42, "carbs": 55, "fat": 4}}
            }},
            {{
                "meal": "Meal 4 — 6:30 PM",
                "foods": ["200g salmon fillet", "Sweet potato 200g", "Broccoli 200g", "Avocado 1/2"],
                "approx_macros": {{"calories": 680, "protein": 46, "carbs": 65, "fat": 24}}
            }},
            {{
                "meal": "Meal 5 — 9:30 PM",
                "foods": ["Cottage cheese 250g", "Casein protein shake", "Almonds 20g"],
                "approx_macros": {{"calories": 430, "protein": 52, "carbs": 20, "fat": 18}}
            }}
        ],
        "foods_to_prioritize": [
            "Chicken breast, lean beef, eggs, Greek yogurt, cottage cheese",
            "White/brown rice, oats, sweet potato, fruit",
            "Salmon, mackerel (omega-3s for inflammation/recovery)",
            "Leafy greens, cruciferous vegetables"
        ],
        "foods_to_limit": [
            "Ultra-processed foods (spike inflammation)",
            "Alcohol (suppresses protein synthesis 24-48 hrs)",
            "Excessive dietary fat around training windows"
        ],
        "hydration": "Body weight (lbs) × 0.67 = daily oz minimum. Add 16oz per hour of training.",
        "research_citations": [
            "Morton et al. (2018): 1.6-2.2g/kg protein optimizes muscle gains",
            "Aragon & Schoenfeld (2013): Protein timing matters but total daily intake is primary"
        ]
    }},
    "supplement_plan": [
        {{
            "name": "Creatine Monohydrate",
            "dose": "5g",
            "timing": "Daily — time doesn't matter, consistency does",
            "evidence_grade": "A",
            "benefit": "Increases phosphocreatine stores → more ATP → more reps, more strength, more mass",
            "research_support": "1000+ RCTs. Most studied sports supplement. Consistent 5-15% strength gains.",
            "priority": 1,
            "cost_per_month": "$5-10"
        }},
        {{
            "name": "Whey Protein",
            "dose": "25-40g per serving as needed to hit daily protein target",
            "timing": "Post-workout or any time protein is low",
            "evidence_grade": "A",
            "benefit": "Convenient complete protein source with high leucine content",
            "research_support": "Leucine threshold (2-3g) triggers mTOR and muscle protein synthesis",
            "priority": 2,
            "cost_per_month": "$30-50"
        }},
        {{
            "name": "Caffeine",
            "dose": "3-6mg/kg body weight (200-400mg typical)",
            "timing": "30-45 min pre-workout. Cycle off 1-2 weeks every 2 months.",
            "evidence_grade": "A",
            "benefit": "Increases power output, reduces perceived exertion, improves endurance",
            "research_support": "ISSN position stand endorses caffeine as a top-tier ergogenic aid",
            "priority": 3,
            "cost_per_month": "$5-15"
        }},
        {{
            "name": "Vitamin D3 + K2",
            "dose": "2000-5000 IU D3 + 100mcg K2",
            "timing": "With a fat-containing meal",
            "evidence_grade": "B",
            "benefit": "Supports testosterone production, bone density, immune function. Most athletes are deficient.",
            "research_support": "Pilz et al. (2011): D3 supplementation increased testosterone 25% in deficient men",
            "priority": 4,
            "cost_per_month": "$10-20"
        }},
        {{
            "name": "Omega-3 Fish Oil",
            "dose": "2-3g EPA+DHA combined",
            "timing": "With meals",
            "evidence_grade": "B",
            "benefit": "Reduces muscle soreness, supports joint health, anti-inflammatory",
            "research_support": "Smith et al. (2011): 4g/day fish oil increased muscle protein synthesis rates",
            "priority": 5,
            "cost_per_month": "$15-25"
        }},
        {{
            "name": "Magnesium Glycinate",
            "dose": "300-400mg elemental magnesium",
            "timing": "Before bed",
            "evidence_grade": "B",
            "benefit": "Improves sleep quality, muscle relaxation, energy metabolism. High deficiency rate.",
            "research_support": "Abbasi et al. (2012): Magnesium improved sleep quality and duration",
            "priority": 6,
            "cost_per_month": "$10-20"
        }}
    ],
    "coaching_notes": {{
        "biggest_priority": "The single most impactful change to make right now",
        "lifestyle_factors": [
            "Sleep 7-9 hrs — GH release peaks during deep sleep. Non-negotiable for muscle growth.",
            "Manage stress — cortisol is catabolic. Meditate, walk, breathe.",
            "Track your food for at least 4 weeks to calibrate your intake accuracy"
        ],
        "12_week_expectations": "Realistic expectation based on goal and current state",
        "check_in_schedule": "Re-analyze body composition every 4-6 weeks. Track weekly: weight, lifts, photos.",
        "motivation": "Personalized closing message from the coach"
    }}
}}

Fill in ALL fields with real, specific data for THIS athlete. Make the workout plan complete for all {days} training days. Only return valid JSON."""

    goal = profile.goal if profile and hasattr(profile, "goal") else None
    system_prompt = get_goal_system_prompt(goal)

    message = client.messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=8000,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    )

    return _extract_json(message.content[0].text)


def estimate_meal_macros(description: str) -> dict:
    """Haiku estimates calories/protein/carbs/fat from a plain-text food description."""
    message = _client().messages.create(
        model=SUMMARY_MODEL,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                f'Estimate the macros for this meal: "{description}"\n\n'
                "Return ONLY valid JSON — no other text:\n"
                '{"calories": 520, "protein_g": 45.0, "carbs_g": 48.0, "fat_g": 10.0, '
                '"source": "estimated"}'
            ),
        }],
    )
    return _extract_json(message.content[0].text)


def generate_recovery_insight(sleep: int, energy: int, soreness: int, stress: int, profile: dict | None = None) -> tuple[int, str]:
    """Returns (recovery_score 0-100, one-sentence coaching tip) using Haiku."""
    profile_ctx = ""
    if profile:
        profile_ctx = (
            f"Athlete: {profile.get('age', '?')}yo, goal={profile.get('goal', '?')}, "
            f"experience={profile.get('experience', '?')}. "
        )

    message = _client().messages.create(
        model=SUMMARY_MODEL,
        max_tokens=150,
        messages=[{
            "role": "user",
            "content": (
                f"{profile_ctx}Daily check-in scores (1-10): "
                f"Sleep={sleep}, Energy={energy}, Soreness={soreness}, Stress={stress}.\n\n"
                "Return ONLY valid JSON:\n"
                '{"recovery_score": 74, "tip": "One specific, actionable sentence for today."}'
            ),
        }],
    )
    data = _extract_json(message.content[0].text)
    return int(data.get("recovery_score", 50)), data.get("tip", "Listen to your body today.")


def generate_progressive_overload_suggestion(exercise: str, set_history: list, profile: dict | None = None) -> str:
    """Analyzes recent sets for an exercise and recommends the next session's target."""
    if not set_history:
        return f"No history yet for {exercise}. Start logging sets with /logset."

    recent = set_history[-10:]
    history_text = "\n".join(
        f"  {s.get('date', '?')}: {s['weight_kg']}kg × {s['reps']} reps (1RM ~{s.get('estimated_1rm', '?')}kg)"
        for s in recent
    )
    profile_ctx = f"Experience: {profile.get('experience', 'intermediate')}. " if profile else ""

    message = _client().messages.create(
        model=SUMMARY_MODEL,
        max_tokens=120,
        messages=[{
            "role": "user",
            "content": (
                f"{profile_ctx}Recent {exercise} history:\n{history_text}\n\n"
                "Give one specific, actionable recommendation for the next session — "
                "exact weight and reps to target. One sentence only."
            ),
        }],
    )
    return message.content[0].text.strip()


def generate_next_session_targets(session_sets: list, plan_day: dict | None, profile: dict | None = None) -> str:
    """After a session ends, suggest targets for the next identical session."""
    if not session_sets:
        return "No sets logged for this session."

    by_exercise: dict[str, list] = {}
    for s in session_sets:
        ex = s.get("exercise_name", "Unknown")
        by_exercise.setdefault(ex, []).append(s)

    history_lines = []
    for ex, sets in by_exercise.items():
        last = sorted(sets, key=lambda x: x.get("logged_at", ""))[-1]
        history_lines.append(
            f"  {ex}: {last['weight_kg']}kg × {last['reps']} reps"
            f" (est. 1RM ~{last.get('estimated_1rm', '?')}kg)"
        )

    goal = (profile or {}).get("goal", "general")
    plan_focus = plan_day.get("focus", "") if plan_day else ""

    message = _client().messages.create(
        model=SUMMARY_MODEL,
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                f"Session completed. Goal: {goal}. Focus: {plan_focus}\n"
                f"Sets logged:\n" + "\n".join(history_lines) + "\n\n"
                "Give 3–4 specific targets for the NEXT identical session (exact weight+reps per exercise). "
                "One sentence per exercise. Be concrete — e.g. 'Bench: try 102.5kg × 5 reps'. "
                "Only return the recommendations, no preamble."
            ),
        }],
    )
    return message.content[0].text.strip()


def generate_weekly_report(sessions: list, checkins: list, meals: list, prs: list, profile: dict | None = None) -> dict:
    """Sonnet synthesizes a week of data into 3 coaching insights + next-week focus."""
    profile_ctx = ""
    if profile:
        profile_ctx = f"Athlete: goal={profile.get('goal', '?')}, experience={profile.get('experience', '?')}. "

    avg_recovery = round(sum(c.get("recovery_score", 0) for c in checkins) / len(checkins), 1) if checkins else None
    avg_protein = round(sum(m.get("protein_g", 0) for m in meals) / len(meals), 1) if meals else None
    pr_names = [p.get("exercise_name", "") for p in prs]

    summary = (
        f"{profile_ctx}"
        f"Sessions this week: {len(sessions)}. "
        f"Avg recovery score: {avg_recovery or 'N/A'}/100. "
        f"Avg daily protein: {avg_protein or 'N/A'}g. "
        f"New PRs: {', '.join(pr_names) if pr_names else 'none'}."
    )

    message = _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": (
                f"Weekly training data: {summary}\n\n"
                "Write a brief weekly coaching report. Return ONLY valid JSON:\n"
                '{"insights": ["insight 1", "insight 2", "insight 3"], '
                '"next_week_focus": "The single most important focus for next week.", '
                '"adherence_rating": "Good"}'
            ),
        }],
    )
    data = _extract_json(message.content[0].text)
    data["avg_recovery"] = avg_recovery
    data["avg_protein_g"] = avg_protein
    data["sessions_count"] = len(sessions)
    data["prs_count"] = len(prs)
    return data


def analyze_weak_points(analyses: list, set_logs: list, profile: dict | None = None) -> dict:
    """Cross-references photo weak points with logged volume to find training imbalances."""
    if not analyses:
        return {"error": "No photo analyses available. Send a photo first."}

    latest = analyses[-1]
    weak_from_photos = latest.get("areas_to_improve", [])
    muscle_scores = latest.get("muscle_development", {})

    volume_by_muscle: dict[str, int] = {}
    for s in set_logs:
        name = (s.get("exercise_name") or "").lower()
        for muscle in ["chest", "back", "shoulders", "arms", "legs", "core"]:
            if muscle in name or (muscle == "legs" and any(k in name for k in ["squat", "leg press", "lunge", "deadlift"])):
                volume_by_muscle[muscle] = volume_by_muscle.get(muscle, 0) + 1

    message = _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": (
                f"Photo weak points: {weak_from_photos}\n"
                f"Muscle scores from photo: {json.dumps(muscle_scores)}\n"
                f"Sets logged per muscle (last 30 days): {json.dumps(volume_by_muscle)}\n\n"
                "Identify the biggest training imbalances and give specific volume recommendations. "
                "Return ONLY valid JSON:\n"
                '{"weak_points": ["point 1", "point 2"], '
                '"volume_recommendations": {"chest": "Add 4 sets/week", "legs": "Double current volume"}, '
                '"priority_fix": "The single most impactful change."}'
            ),
        }],
    )
    return _extract_json(message.content[0].text)


def build_rich_context(user_data: dict) -> str:
    """Assemble a rich training/recovery/nutrition context string for AI prompts."""
    from datetime import date, timedelta

    profile = user_data.get("profile", {})
    set_logs = user_data.get("set_logs", [])
    checkins = user_data.get("checkins", [])
    meal_logs = user_data.get("meal_logs", [])
    measurements = user_data.get("measurements", [])
    prs = user_data.get("prs", {})
    session_counter = user_data.get("session_counter", 0)

    today = date.today()
    seven_days_ago = str(today - timedelta(days=7))
    thirty_days_ago = str(today - timedelta(days=30))

    # Training last 7 days
    recent_sets = [s for s in set_logs if s.get("date", "") >= seven_days_ago]
    session_ids_7d = {s.get("session_id") for s in recent_sets if s.get("session_id") is not None}
    volume_7d = sum(s.get("weight_kg", 0) * s.get("reps", 0) for s in recent_sets)

    # Recovery last 7 days
    recent_checkins = [c for c in checkins if c.get("date", "") >= seven_days_ago]
    avg_recovery = round(sum(c.get("recovery_score", 0) for c in recent_checkins) / len(recent_checkins)) if recent_checkins else None
    avg_sleep = round(sum(c.get("sleep_score", 0) for c in recent_checkins) / len(recent_checkins), 1) if recent_checkins else None
    avg_soreness = round(sum(c.get("soreness_score", 0) for c in recent_checkins) / len(recent_checkins), 1) if recent_checkins else None

    # Weight trend 30 days
    recent_weights = [(m.get("date", ""), m.get("body_weight_kg")) for m in measurements if m.get("body_weight_kg") and m.get("date", "") >= thirty_days_ago]
    weight_change_30d = None
    if len(recent_weights) >= 2:
        sorted_w = sorted(recent_weights, key=lambda x: x[0])
        weight_change_30d = round(sorted_w[-1][1] - sorted_w[0][1], 1)
    latest_weight = recent_weights[-1][1] if recent_weights else profile.get("weight")

    # Protein compliance 7 days
    protein_target = None
    plan = user_data.get("last_plan", {})
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

    # Top PRs
    top_prs = sorted(prs.items(), key=lambda x: x[1].get("estimated_1rm", 0), reverse=True)[:5]
    prs_text = ", ".join(f"{ex} {v['weight_kg']}kg×{v['reps']}" for ex, v in top_prs) if top_prs else "none"

    lines = [
        f"Profile: goal={profile.get('goal','?')}, experience={profile.get('experience','?')}, age={profile.get('age','?')}",
        f"Training 7d: {len(session_ids_7d)} sessions, {len(recent_sets)} sets, {volume_7d:,.0f}kg total volume",
        f"Recovery 7d: avg score={avg_recovery}/100, avg sleep={avg_sleep}/10, avg soreness={avg_soreness}/10" if avg_recovery else "Recovery: no check-ins this week",
        f"Weight: current={latest_weight}kg, 30d change={weight_change_30d:+.1f}kg" if weight_change_30d is not None else f"Weight: current={latest_weight}kg",
        f"Protein compliance (7d): {protein_compliance_pct}% of days hit target" if protein_compliance_pct is not None else "Protein: not tracked this week",
        f"Total sessions logged: {session_counter}",
        f"Top PRs: {prs_text}",
    ]
    return "\n".join(lines)


def summarize_research(topic: str, papers: list) -> str:
    client = _client()

    papers_text = "\n\n".join(
        f"Title: {p.get('title', '')}\nAbstract: {(p.get('abstract') or '')[:600]}"
        for p in papers[:6]
    )

    message = client.messages.create(
        model=SUMMARY_MODEL,
        max_tokens=400,
        messages=[
            {
                "role": "user",
                "content": f"""Summarize the key actionable findings from these recent research papers on "{topic}" for a bodybuilder/physique athlete. Focus on what the athlete should DO differently based on this research. Be specific and concise (3-4 sentences max).

{papers_text}

Actionable summary:""",
            }
        ],
    )

    return message.content[0].text.strip()
