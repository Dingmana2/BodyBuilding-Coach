#!/usr/bin/env python3
"""
BodyBuilding Coach AI — Telegram Bot
Deploy free on Railway.app. See README for setup steps.
"""

import asyncio
import base64
import json
import os
import re
import xml.etree.ElementTree as ET
from io import BytesIO

import anthropic
import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ANALYSIS_MODEL = "claude-opus-4-7"
CHAT_MODEL = "claude-sonnet-4-6"
SUMMARY_MODEL = "claude-haiku-4-5-20251001"
MAX_HISTORY = 20

# In-memory store: chat_id → {profile, last_analysis, last_plan, conversation_history}
# Note: resets on bot restart. Set /profile again if needed.
user_data: dict[int, dict] = {}

RESEARCH_TOPICS = [
    "muscle hypertrophy resistance training 2024",
    "protein requirements bodybuilding athletes",
    "creatine supplementation strength performance",
    "body recomposition simultaneous fat loss muscle gain",
]


def claude() -> anthropic.Anthropic:
    if not ANTHROPIC_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set in environment.")
    return anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def get_user(chat_id: int) -> dict:
    if chat_id not in user_data:
        user_data[chat_id] = {
            "profile": {},
            "last_analysis": None,
            "last_plan": None,
            "conversation_history": [],
        }
    return user_data[chat_id]


# ── Commands ──────────────────────────────────────────────────────────────────

WELCOME = (
    "⚡ *BodyBuilding Coach AI*\n\n"
    "I build science-backed workout, diet, and supplement plans tailored to you — "
    "whatever your age, fitness level, or goal.\n\n"
    "*Two ways to get your plan:*\n"
    "📸 Send a photo — I'll analyze your physique and build a full plan\n"
    "💬 Type /plan — I'll build from your profile stats (no photo needed)\n\n"
    "*Commands:*\n"
    "/profile — View or set your stats and goals\n"
    "/plan — Generate your full plan\n"
    "/research — Fetch latest PubMed research highlights\n"
    "/help — Show this message\n\n"
    "You can also chat with me anytime — ask questions, give feedback on your plan "
    "(e.g. 'I hate leg days' or 'I'm vegetarian'), and I'll update it permanently.\n\n"
    "Start with /profile to set your stats, or just send a photo 📸"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME, parse_mode="Markdown")


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    profile = user["profile"]

    if not context.args:
        current = (
            "\n".join(f"• {k}: {v}" for k, v in profile.items())
            if profile
            else "Not set yet."
        )
        await update.message.reply_text(
            f"*Your Profile:*\n{current}\n\n"
            "To update, send:\n"
            "`/profile age=28 gender=male height=178 weight=85 goal=cut experience=intermediate days=4`\n\n"
            "Goals: `bulk` `cut` `recomp` `maintain` `health` `performance`\n"
            "Experience: `beginner` `intermediate` `advanced`\n"
            "Gender: anything — male, female, non-binary, prefer not to say, etc.",
            parse_mode="Markdown",
        )
        return

    for arg in context.args:
        if "=" in arg:
            key, _, value = arg.partition("=")
            profile[key.strip().lower()] = value.strip()

    user["profile"] = profile
    await update.message.reply_text(
        "✅ Profile saved!\n\n"
        "Send a photo for physique analysis, or type /plan to get your plan now."
    )


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    msg = await update.message.reply_text(
        "🧬 Generating your plan… (30-60 seconds)"
    )
    try:
        if user["last_analysis"]:
            plan = _generate_plan(user["last_analysis"], user["profile"])
        else:
            if not user["profile"]:
                await msg.edit_text(
                    "Set your stats first with /profile, then I can build your plan.\n\n"
                    "Example:\n"
                    "`/profile age=25 gender=female height=165 weight=65 goal=recomp experience=beginner days=3`\n\n"
                    "Or send a photo and I'll analyze your physique directly 📸",
                    parse_mode="Markdown",
                )
                return
            plan = _generate_plan_from_profile(user["profile"])

        user["last_plan"] = plan
        await msg.delete()
        await _send_plan(update, plan)
        await update.message.reply_text(
            "💬 Not happy with something? Just tell me — "
            "e.g. 'remove leg day', 'I'm vegetarian', 'train only 3 days' — and I'll update your plan."
        )
    except Exception as e:
        await msg.edit_text(f"❌ Plan generation failed: {e}")


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("🔬 Fetching latest PubMed research…")
    try:
        summaries = await _fetch_research_summaries()
        text = "*Latest Research Highlights*\n\n" + "\n\n".join(summaries)
        await msg.edit_text(text[:4096], parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Research fetch failed: {e}")


# ── Photo handler ─────────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    msg = await update.message.reply_text(
        "📸 Analyzing your physique… (20-40 seconds)"
    )
    try:
        photo = update.message.photo[-1]  # highest resolution
        file = await context.bot.get_file(photo.file_id)
        buf = BytesIO()
        await file.download_to_memory(buf)
        img_b64 = base64.standard_b64encode(buf.getvalue()).decode("utf-8")

        analysis = _analyze_photo(img_b64, user["profile"])
        user["last_analysis"] = analysis

        await msg.edit_text(_format_analysis(analysis), parse_mode="Markdown")
        await update.message.reply_text(
            "Type /plan to generate your full workout + diet + supplement plan 💪"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Analysis failed: {e}")


# ── Text message handler (conversational coaching) ────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)
    text = update.message.text.strip()

    msg = await update.message.reply_text("💬 Thinking…")
    try:
        reply, plan_update = _chat_with_coach(text, user)

        if plan_update and user["last_plan"]:
            _deep_merge(user["last_plan"], plan_update)
            reply += "\n\n✅ _Your plan has been updated. Type /plan to see the full updated version._"

        await msg.edit_text(reply, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Something went wrong: {e}")


def _chat_with_coach(text: str, user: dict) -> tuple[str, dict | None]:
    profile = user["profile"]
    plan = user["last_plan"]
    history = user["conversation_history"]

    profile_str = (
        ", ".join(f"{k}={v}" for k, v in profile.items())
        if profile else "No profile set yet."
    )
    plan_str = (
        f"Current plan summary: {json.dumps(plan, indent=None)[:1500]}"
        if plan else "No plan generated yet — suggest they type /plan or send a photo."
    )

    system = (
        "You are a personal fitness and nutrition coach. You give specific, "
        "evidence-based advice tailored to the individual.\n\n"
        f"Athlete profile: {profile_str}\n"
        f"{plan_str}\n\n"
        "Support every fitness level (complete beginner to advanced), any age, any gender, any goal. "
        "Be direct, warm, and practical. Keep replies concise — 3-5 sentences unless a detailed "
        "breakdown is genuinely needed.\n\n"
        "If the user asks you to modify their plan (e.g. 'remove leg day', 'I'm vegetarian', "
        "'change to 3 days a week', 'swap the creatine'), output the changes in a fenced code "
        "block tagged `plan_update` containing ONLY the modified JSON fields. Example:\n"
        "```plan_update\n"
        '{{"workout": {{"split": "3-Day Full Body"}}}}\n'
        "```\n"
        "Only include fields that actually change. If nothing needs to change, omit the block entirely."
    )

    history.append({"role": "user", "content": text})
    if len(history) > MAX_HISTORY:
        history[:] = history[-MAX_HISTORY:]

    response = claude().messages.create(
        model=CHAT_MODEL,
        max_tokens=800,
        system=system,
        messages=history,
    )

    full_reply = response.content[0].text.strip()

    plan_update = None
    match = re.search(r"```plan_update\s*([\s\S]*?)```", full_reply)
    if match:
        try:
            plan_update = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
        full_reply = re.sub(r"```plan_update[\s\S]*?```", "", full_reply).strip()

    history.append({"role": "assistant", "content": full_reply})

    return full_reply, plan_update


def _deep_merge(base: dict, updates: dict) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ── Claude: body analysis ─────────────────────────────────────────────────────

def _analyze_photo(img_b64: str, profile: dict) -> dict:
    profile_ctx = ""
    if profile:
        profile_ctx = (
            f"\nAthlete: {profile.get('age','?')}yo {profile.get('gender','?')}, "
            f"{profile.get('height','?')}cm, {profile.get('weight','?')}kg, "
            f"goal={profile.get('goal','?')}, "
            f"experience={profile.get('experience','?')}, "
            f"{profile.get('days','?')} training days/week"
        )

    prompt = (
        "You are an expert fitness coach who works with athletes of all ages, genders, and "
        "experience levels — from complete beginners to competitive athletes. "
        f"Analyze this physique photo.{profile_ctx}\n\n"
        "Return ONLY valid JSON with this exact structure:\n"
        '{\n'
        '    "body_fat_estimate": "15-18%",\n'
        '    "body_fat_confidence": "medium",\n'
        '    "overall_physique_score": 7.2,\n'
        '    "muscle_development": {\n'
        '        "chest": {"score": 7, "notes": "Good upper chest, lower needs work"},\n'
        '        "back": {"score": 6, "notes": "Width decent, thickness lacking"},\n'
        '        "shoulders": {"score": 7, "notes": "Front delts strong, laterals lag"},\n'
        '        "arms": {"score": 7, "notes": "Good bicep peak, tricep mass needed"},\n'
        '        "legs": {"score": 5, "notes": "Significantly behind upper body"},\n'
        '        "core": {"score": 6, "notes": "Abs visible, obliques need work"}\n'
        '    },\n'
        '    "strengths": ["Good shoulder-to-waist ratio", "Chest fullness"],\n'
        '    "areas_to_improve": ["Leg development", "Overall conditioning"],\n'
        '    "symmetry_notes": "Left shoulder slightly higher. Overall symmetry good.",\n'
        '    "priority_improvements": ["Most impactful change 1", "Most impactful change 2"],\n'
        '    "coach_message": "Specific, motivating 2-sentence message for this athlete based on their experience level and goals"\n'
        '}'
    )

    message = claude().messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=1500,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )

    text = message.content[0].text
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return json.loads(text.strip())


# ── Claude: plan generation ───────────────────────────────────────────────────

def _build_plan_prompt(profile: dict, analysis: dict | None, days: int) -> str:
    profile_ctx = (
        f"Age: {profile.get('age', 'not specified')} | "
        f"Gender: {profile.get('gender', 'not specified')} | "
        f"Height: {profile.get('height', '?')}cm | "
        f"Weight: {profile.get('weight', '?')}kg | "
        f"Goal: {profile.get('goal', 'general health')} | "
        f"Experience: {profile.get('experience', 'beginner')} | "
        f"Training days: {days}/week"
    ) if profile else "No profile data — assume healthy adult beginner with general fitness goal."

    if analysis:
        body_ctx = (
            f"BODY ANALYSIS:\n"
            f"- Body fat: {analysis.get('body_fat_estimate', '?')}\n"
            f"- Physique score: {analysis.get('overall_physique_score', '?')}/10\n"
            f"- Priority improvements: {', '.join(analysis.get('priority_improvements', []))}\n"
            f"- Weakest areas: {', '.join(analysis.get('areas_to_improve', []))}\n"
            f"- Muscle development: {json.dumps(analysis.get('muscle_development', {}))}"
        )
    else:
        body_ctx = "No photo analysis — build the plan entirely from the profile stats above."

    return (
        "You are an expert strength coach and sports nutritionist who works with all populations — "
        "beginners to advanced athletes, all ages (teens to 70+), all genders, all goals "
        "(fat loss, muscle gain, general health, sport performance, recomp).\n\n"
        f"ATHLETE: {profile_ctx}\n"
        f"{body_ctx}\n\n"
        "Tailor EVERYTHING to this specific athlete. A beginner gets simpler movements and lower volume. "
        "An older athlete gets joint-friendly exercise selection. Nutrition targets must match their "
        "actual goal and body weight.\n\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '    "workout": {\n'
        '        "split": "4-Day Upper/Lower",\n'
        '        "days": [\n'
        '            {\n'
        '                "day": "Monday",\n'
        '                "focus": "Upper Push",\n'
        '                "exercises": [\n'
        '                    {"name": "Barbell Bench Press", "sets": 4, "reps": "6-8", "rest": "3min", "notes": "Full ROM, 2-sec descent"},\n'
        '                    {"name": "Incline Dumbbell Press", "sets": 3, "reps": "8-10", "rest": "2min", "notes": "Focus on upper chest stretch"},\n'
        '                    {"name": "Overhead Press", "sets": 4, "reps": "6-8", "rest": "3min", "notes": "Strict form, no leg drive"},\n'
        '                    {"name": "Lateral Raises", "sets": 4, "reps": "12-15", "rest": "90s", "notes": "Controlled, slight forward lean"},\n'
        '                    {"name": "Tricep Pushdowns", "sets": 3, "reps": "10-12", "rest": "90s", "notes": "Full extension"}\n'
        '                ]\n'
        '            }\n'
        '        ],\n'
        '        "progression": "Add 2.5kg when you complete all sets at top of rep range for 2 consecutive sessions.",\n'
        '        "deload": "Every 4-6 weeks: reduce load 40%, maintain volume."\n'
        '    },\n'
        '    "diet": {\n'
        '        "calories": 2800,\n'
        '        "protein_g": 180,\n'
        '        "carbs_g": 320,\n'
        '        "fat_g": 78,\n'
        '        "rationale": "Why these exact numbers for this athlete",\n'
        '        "meal_timing": "Pre/post workout nutrition guidance",\n'
        '        "sample_meals": ["Breakfast: ...", "Lunch: ...", "Dinner: ..."],\n'
        '        "foods_to_prioritize": ["Chicken breast", "Eggs", "Rice", "Oats"],\n'
        '        "foods_to_limit": ["Ultra-processed foods", "Alcohol"]\n'
        '    },\n'
        '    "supplements": [\n'
        '        {"priority": 1, "name": "Creatine Monohydrate", "dose": "5g daily", "timing": "Anytime", "grade": "A", "benefit": "5-15% strength gains. Most evidence-backed supplement."},\n'
        '        {"priority": 2, "name": "Whey Protein", "dose": "25-40g per serving", "timing": "Post-workout or to hit daily protein", "grade": "A", "benefit": "High leucine triggers muscle protein synthesis."},\n'
        '        {"priority": 3, "name": "Caffeine", "dose": "200-400mg", "timing": "30-45min pre-workout", "grade": "A", "benefit": "Increases power output, reduces perceived exertion."},\n'
        '        {"priority": 4, "name": "Vitamin D3 + K2", "dose": "3000 IU D3 + 100mcg K2", "timing": "With a fat-containing meal", "grade": "B", "benefit": "Supports testosterone, bone density, immunity."},\n'
        '        {"priority": 5, "name": "Omega-3 Fish Oil", "dose": "2-3g EPA+DHA", "timing": "With meals", "grade": "B", "benefit": "Reduces DOMS, supports joint health."},\n'
        '        {"priority": 6, "name": "Magnesium Glycinate", "dose": "300-400mg", "timing": "Before bed", "grade": "B", "benefit": "Improves sleep quality and recovery."}\n'
        '    ],\n'
        '    "coaching": {\n'
        '        "top_priority": "The single most impactful change for this athlete",\n'
        '        "sleep": "Sleep guidance",\n'
        '        "stress": "Stress management",\n'
        '        "tracking": "What to track and how",\n'
        '        "expectations": "Realistic 12-week outcome for this athlete",\n'
        '        "coach_message": "Inspiring, specific closing message"\n'
        '    }\n'
        '}\n\n'
        f"Build ALL {days} training days. Be specific with numbers. Only return valid JSON."
    )


def _parse_plan_response(text: str) -> dict:
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return json.loads(text.strip())


def _generate_plan(analysis: dict, profile: dict) -> dict:
    days = int(profile.get("days", 4))
    prompt = _build_plan_prompt(profile, analysis, days)
    message = claude().messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=5000,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_plan_response(message.content[0].text)


def _generate_plan_from_profile(profile: dict) -> dict:
    days = int(profile.get("days", 3))
    prompt = _build_plan_prompt(profile, None, days)
    message = claude().messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=5000,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_plan_response(message.content[0].text)


# ── Research ──────────────────────────────────────────────────────────────────

async def _fetch_research_summaries() -> list[str]:
    summaries = []
    for topic in RESEARCH_TOPICS[:3]:
        try:
            papers = await _search_pubmed(topic)
            if papers:
                summary = _summarize_papers(topic, papers)
                summaries.append(f"*{topic.replace('2024', '').strip().title()}*\n{summary}")
            await asyncio.sleep(0.5)
        except Exception:
            continue
    return summaries or ["No research data available. Try again in a moment."]


async def _search_pubmed(query: str, max_results: int = 4) -> list:
    params = {
        "db": "pubmed",
        "term": f"({query})[Title/Abstract]",
        "retmax": max_results,
        "sort": "relevance",
        "datetype": "pdat",
        "mindate": "2023/01/01",
        "retmode": "json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=params
        )
        ids = resp.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    await asyncio.sleep(0.35)
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(ids), "rettype": "abstract", "retmode": "xml"},
        )
    return _parse_pubmed_xml(resp.text)


def _parse_pubmed_xml(xml_text: str) -> list:
    papers = []
    try:
        root = ET.fromstring(xml_text)
        for article in root.findall(".//PubmedArticle"):
            title = article.findtext(".//ArticleTitle", "")
            abstract = " ".join(
                (t.text or "") for t in article.findall(".//AbstractText")
            )
            year = article.findtext(".//PubDate/Year", "")
            if title and abstract:
                papers.append({"title": title, "abstract": abstract[:600], "year": year})
    except Exception:
        pass
    return papers


def _summarize_papers(topic: str, papers: list) -> str:
    text = "\n\n".join(
        f"Title: {p['title']} ({p.get('year', '')})\n{p['abstract']}"
        for p in papers[:4]
    )
    message = claude().messages.create(
        model=SUMMARY_MODEL,
        max_tokens=280,
        messages=[{
            "role": "user",
            "content": (
                f'Summarize key ACTIONABLE findings for a fitness enthusiast from these papers on "{topic}". '
                f"3 sentences max. Practical, specific:\n\n{text}"
            ),
        }],
    )
    return message.content[0].text.strip()


# ── Formatters ────────────────────────────────────────────────────────────────

def _format_analysis(a: dict) -> str:
    muscle = a.get("muscle_development", {})
    muscle_lines = "\n".join(
        f"  {k.capitalize()}: {v.get('score', '?')}/10 — {v.get('notes', '')}"
        for k, v in muscle.items()
    )
    strengths = "\n".join(f"✅ {s}" for s in a.get("strengths", []))
    priorities = "\n".join(f"🎯 {s}" for s in a.get("priority_improvements", []))

    return (
        f"📊 *Physique Analysis*\n\n"
        f"Body Fat: *{a.get('body_fat_estimate', '?')}* (confidence: {a.get('body_fat_confidence', '?')})\n"
        f"Score: *{a.get('overall_physique_score', '?')}/10*\n\n"
        f"*Muscle Development:*\n{muscle_lines}\n\n"
        f"*Strengths:*\n{strengths}\n\n"
        f"*Top Priorities:*\n{priorities}\n\n"
        f"📐 {a.get('symmetry_notes', '')}\n\n"
        f"_{a.get('coach_message', '')}_"
    )


async def _send_plan(update: Update, plan: dict) -> None:
    workout = plan.get("workout", {})
    diet = plan.get("diet", {})
    supplements = plan.get("supplements", [])
    coaching = plan.get("coaching", {})

    # ── Workout ──
    days_text = ""
    for day in workout.get("days", []):
        ex_lines = "\n".join(
            f"    • {e['name']}: {e['sets']}×{e['reps']} — rest {e.get('rest', '')} | {e.get('notes', '')}"
            for e in day.get("exercises", [])
        )
        days_text += f"\n*{day['day']} — {day.get('focus', '')}*\n{ex_lines}\n"

    await update.message.reply_text(
        f"🏋️ *Workout — {workout.get('split', '')}*\n"
        f"{days_text}\n"
        f"📈 *Progression:* {workout.get('progression', '')}\n"
        f"🔄 *Deload:* {workout.get('deload', '')}",
        parse_mode="Markdown",
    )

    # ── Diet ──
    meals = "\n".join(f"  • {m}" for m in diet.get("sample_meals", []))
    await update.message.reply_text(
        f"🥗 *Diet Plan*\n\n"
        f"Calories: *{diet.get('calories', '?')} kcal*\n"
        f"Protein: *{diet.get('protein_g', '?')}g* | "
        f"Carbs: *{diet.get('carbs_g', '?')}g* | "
        f"Fat: *{diet.get('fat_g', '?')}g*\n\n"
        f"_{diet.get('rationale', '')}_\n\n"
        f"*Meal Timing:*\n{diet.get('meal_timing', '')}\n\n"
        f"*Sample Day:*\n{meals}\n\n"
        f"*Prioritize:* {', '.join(diet.get('foods_to_prioritize', []))}",
        parse_mode="Markdown",
    )

    # ── Supplements ──
    supp_lines = "\n\n".join(
        f"*#{s.get('priority', '?')} {s['name']}* — Grade {s.get('grade', '?')}\n"
        f"  {s.get('dose', '?')} | {s.get('timing', '?')}\n"
        f"  _{s.get('benefit', '')}_"
        for s in supplements
    )
    await update.message.reply_text(
        f"💊 *Supplement Stack*\n\n{supp_lines}",
        parse_mode="Markdown",
    )

    # ── Coaching ──
    await update.message.reply_text(
        f"💬 *Coaching Notes*\n\n"
        f"🎯 *Top Priority:* {coaching.get('top_priority', '')}\n\n"
        f"😴 *Sleep:* {coaching.get('sleep', '')}\n\n"
        f"🧘 *Stress:* {coaching.get('stress', '')}\n\n"
        f"📊 *Tracking:* {coaching.get('tracking', '')}\n\n"
        f"📅 *12-Week Outlook:* {coaching.get('expectations', '')}\n\n"
        f"_{coaching.get('coach_message', '')}_",
        parse_mode="Markdown",
    )


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if not TELEGRAM_TOKEN:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN is not set. "
            "Get it from @BotFather on Telegram, then set the env var."
        )
    if not ANTHROPIC_KEY:
        raise ValueError("ANTHROPIC_API_KEY is not set.")

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ BodyBuilding Coach Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
