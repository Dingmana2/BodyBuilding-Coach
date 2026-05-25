#!/usr/bin/env python3
"""
BodyBuilding Coach AI — Telegram Bot
Deploy free on Railway.app. See README for setup steps.
"""

import asyncio
import base64
import json
import os
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
SUMMARY_MODEL = "claude-haiku-4-5-20251001"

# In-memory store: chat_id → {profile, last_analysis}
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
        user_data[chat_id] = {"profile": {}, "last_analysis": None}
    return user_data[chat_id]


# ── Commands ──────────────────────────────────────────────────────────────────

WELCOME = (
    "⚡ *BodyBuilding Coach AI*\n\n"
    "Send me a physique photo and I'll analyze your body composition "
    "and build a science-backed workout, diet, and supplement plan.\n\n"
    "*Commands:*\n"
    "/profile — View or set your stats and goals\n"
    "/plan — Generate your full plan from last analysis\n"
    "/research — Fetch latest PubMed research highlights\n"
    "/help — Show this message\n\n"
    "Start by sending a front or side pose photo 📸"
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
            "Goals: `bulk` `cut` `recomp` `maintain`\n"
            "Experience: `beginner` `intermediate` `advanced`",
            parse_mode="Markdown",
        )
        return

    for arg in context.args:
        if "=" in arg:
            key, _, value = arg.partition("=")
            profile[key.strip().lower()] = value.strip()

    user["profile"] = profile
    await update.message.reply_text(
        "✅ Profile saved! Now send a photo for analysis."
    )


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = get_user(chat_id)

    if not user["last_analysis"]:
        await update.message.reply_text(
            "No analysis yet — send me a physique photo first!"
        )
        return

    msg = await update.message.reply_text(
        "🧬 Generating your plan using the latest research… (30-60 seconds)"
    )
    try:
        plan = _generate_plan(user["last_analysis"], user["profile"])
        await msg.delete()
        await _send_plan(update, plan)
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

    prompt = f"""You are an elite physique coach with 20+ years of competitive bodybuilding experience. Analyze this physique photo.{profile_ctx}

Return ONLY valid JSON with this exact structure:
{{
    "body_fat_estimate": "15-18%",
    "body_fat_confidence": "medium",
    "overall_physique_score": 7.2,
    "muscle_development": {{
        "chest": {{"score": 7, "notes": "Good upper chest, lower needs work"}},
        "back": {{"score": 6, "notes": "Width decent, thickness lacking"}},
        "shoulders": {{"score": 7, "notes": "Front delts strong, laterals lag"}},
        "arms": {{"score": 7, "notes": "Good bicep peak, tricep mass needed"}},
        "legs": {{"score": 5, "notes": "Significantly behind upper body"}},
        "core": {{"score": 6, "notes": "Abs visible, obliques need work"}}
    }},
    "strengths": ["Good shoulder-to-waist ratio", "Chest fullness"],
    "areas_to_improve": ["Leg development", "Overall conditioning"],
    "symmetry_notes": "Left shoulder slightly higher. Overall symmetry good.",
    "priority_improvements": ["Most impactful change 1", "Most impactful change 2"],
    "coach_message": "Specific, motivating 2-sentence message for this athlete"
}}"""

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

def _generate_plan(analysis: dict, profile: dict) -> dict:
    days = int(profile.get("days", 4))
    profile_ctx = (
        f"Age: {profile.get('age','?')} | Gender: {profile.get('gender','?')} | "
        f"Height: {profile.get('height','?')}cm | Weight: {profile.get('weight','?')}kg | "
        f"Goal: {profile.get('goal','?')} | Experience: {profile.get('experience','?')} | "
        f"Training days: {days}/week"
    ) if profile else "No profile data."

    prompt = f"""You are an elite strength coach and sports nutritionist. Build a complete, evidence-based program.

ATHLETE: {profile_ctx}
BODY ANALYSIS:
- Body fat: {analysis.get('body_fat_estimate','?')}
- Physique score: {analysis.get('overall_physique_score','?')}/10
- Priority improvements: {', '.join(analysis.get('priority_improvements', []))}
- Weakest areas: {', '.join(analysis.get('areas_to_improve', []))}
- Muscle development: {json.dumps(analysis.get('muscle_development', {}))}

Return ONLY valid JSON:
{{
    "workout": {{
        "split": "4-Day Upper/Lower",
        "days": [
            {{
                "day": "Monday",
                "focus": "Upper Push",
                "exercises": [
                    {{"name": "Barbell Bench Press", "sets": 4, "reps": "6-8", "rest": "3min", "notes": "Full ROM, 2-sec descent"}},
                    {{"name": "Incline Dumbbell Press", "sets": 3, "reps": "8-10", "rest": "2min", "notes": "Focus on upper chest stretch"}},
                    {{"name": "Overhead Press", "sets": 4, "reps": "6-8", "rest": "3min", "notes": "Strict form, no leg drive"}},
                    {{"name": "Lateral Raises", "sets": 4, "reps": "12-15", "rest": "90s", "notes": "Controlled, slight forward lean"}},
                    {{"name": "Tricep Pushdowns", "sets": 3, "reps": "10-12", "rest": "90s", "notes": "Full extension"}}
                ]
            }}
        ],
        "progression": "Add 2.5kg when you complete all sets at the top of the rep range for 2 consecutive sessions. Track every session.",
        "deload": "Every 4-6 weeks: reduce load 40%, maintain volume. Deload fully if joints are achy."
    }},
    "diet": {{
        "calories": 2800,
        "protein_g": 180,
        "carbs_g": 320,
        "fat_g": 78,
        "rationale": "Why these exact numbers for this athlete's goal and body",
        "meal_timing": "Pre-workout (90min before): 40g carbs + 30g protein. Post-workout: 40g fast carbs + 35g whey within 30min. Before bed: casein or cottage cheese.",
        "sample_meals": [
            "Breakfast: 5 eggs + oats 80g + Greek yogurt 200g + berries",
            "Lunch: 200g chicken breast + white rice 250g + salad",
            "Pre-workout: Rice cakes + 1 scoop whey",
            "Post-workout: Banana + 2 scoops whey",
            "Dinner: 200g salmon + sweet potato 200g + broccoli",
            "Before bed: Cottage cheese 250g + casein shake"
        ],
        "foods_to_prioritize": ["Chicken breast", "Lean beef", "Eggs", "Greek yogurt", "Salmon", "White/brown rice", "Oats", "Sweet potato"],
        "foods_to_limit": ["Ultra-processed foods", "Alcohol (suppresses protein synthesis 24-48hrs)", "Excess dietary fat peri-workout"]
    }},
    "supplements": [
        {{"priority": 1, "name": "Creatine Monohydrate", "dose": "5g daily", "timing": "Anytime — consistency matters most", "grade": "A", "benefit": "1000+ RCTs. 5-15% strength gains. Most evidence-backed supplement."}},
        {{"priority": 2, "name": "Whey Protein", "dose": "25-40g per serving", "timing": "Post-workout or to hit daily protein", "grade": "A", "benefit": "High leucine triggers mTOR and muscle protein synthesis."}},
        {{"priority": 3, "name": "Caffeine", "dose": "200-400mg", "timing": "30-45min pre-workout. Cycle off 1-2 weeks every 2 months.", "grade": "A", "benefit": "ISSN endorsed. Increases power output, reduces perceived exertion."}},
        {{"priority": 4, "name": "Vitamin D3 + K2", "dose": "3000 IU D3 + 100mcg K2", "timing": "With a fat-containing meal", "grade": "B", "benefit": "Most athletes are deficient. Supports testosterone, bone density, immunity."}},
        {{"priority": 5, "name": "Omega-3 Fish Oil", "dose": "2-3g EPA+DHA combined", "timing": "With meals", "grade": "B", "benefit": "Reduces DOMS, supports joint health, anti-inflammatory."}},
        {{"priority": 6, "name": "Magnesium Glycinate", "dose": "300-400mg", "timing": "Before bed", "grade": "B", "benefit": "Improves sleep quality and muscle recovery. High deficiency rate."}}
    ],
    "coaching": {{
        "top_priority": "The single most impactful change this specific athlete should make",
        "sleep": "7-9 hrs nightly. GH release peaks during deep sleep. Track sleep quality.",
        "stress": "High cortisol is catabolic. Meditation, walks, breathing exercises.",
        "tracking": "Log every workout (weight, reps). Weigh yourself 3x/week (average). Photo every 4 weeks.",
        "expectations": "Realistic, specific 12-week outcome for this athlete's goal and starting point",
        "coach_message": "Inspiring, specific closing message"
    }}
}}

Build ALL {days} training days. Be specific with numbers. Evidence-ground every recommendation. Only return valid JSON."""

    message = claude().messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=5000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    return json.loads(text.strip())


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
        f"Title: {p['title']} ({p.get('year','')})\n{p['abstract']}"
        for p in papers[:4]
    )
    message = claude().messages.create(
        model=SUMMARY_MODEL,
        max_tokens=280,
        messages=[{
            "role": "user",
            "content": (
                f'Summarize key ACTIONABLE findings for a bodybuilder from these papers on "{topic}". '
                f"3 sentences max. Practical, specific:\n\n{text}"
            ),
        }],
    )
    return message.content[0].text.strip()


# ── Formatters ────────────────────────────────────────────────────────────────

def _format_analysis(a: dict) -> str:
    muscle = a.get("muscle_development", {})
    muscle_lines = "\n".join(
        f"  {k.capitalize()}: {v.get('score','?')}/10 — {v.get('notes','')}"
        for k, v in muscle.items()
    )
    strengths = "\n".join(f"✅ {s}" for s in a.get("strengths", []))
    priorities = "\n".join(f"🎯 {s}" for s in a.get("priority_improvements", []))

    return (
        f"📊 *Physique Analysis*\n\n"
        f"Body Fat: *{a.get('body_fat_estimate','?')}* (confidence: {a.get('body_fat_confidence','?')})\n"
        f"Score: *{a.get('overall_physique_score','?')}/10*\n\n"
        f"*Muscle Development:*\n{muscle_lines}\n\n"
        f"*Strengths:*\n{strengths}\n\n"
        f"*Top Priorities:*\n{priorities}\n\n"
        f"📐 {a.get('symmetry_notes','')}\n\n"
        f"_{a.get('coach_message','')}_"
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
            f"    • {e['name']}: {e['sets']}×{e['reps']} — rest {e.get('rest','')} | {e.get('notes','')}"
            for e in day.get("exercises", [])
        )
        days_text += f"\n*{day['day']} — {day.get('focus','')}*\n{ex_lines}\n"

    await update.message.reply_text(
        f"🏋️ *Workout — {workout.get('split','')}*\n"
        f"{days_text}\n"
        f"📈 *Progression:* {workout.get('progression','')}\n"
        f"🔄 *Deload:* {workout.get('deload','')}",
        parse_mode="Markdown",
    )

    # ── Diet ──
    meals = "\n".join(f"  • {m}" for m in diet.get("sample_meals", []))
    await update.message.reply_text(
        f"🥗 *Diet Plan*\n\n"
        f"Calories: *{diet.get('calories','?')} kcal*\n"
        f"Protein: *{diet.get('protein_g','?')}g* | "
        f"Carbs: *{diet.get('carbs_g','?')}g* | "
        f"Fat: *{diet.get('fat_g','?')}g*\n\n"
        f"_{diet.get('rationale','')}_\n\n"
        f"*Meal Timing:*\n{diet.get('meal_timing','')}\n\n"
        f"*Sample Day:*\n{meals}\n\n"
        f"*Prioritize:* {', '.join(diet.get('foods_to_prioritize',[]))}",
        parse_mode="Markdown",
    )

    # ── Supplements ──
    supp_lines = "\n\n".join(
        f"*#{s.get('priority','?')} {s['name']}* — Grade {s.get('grade','?')}\n"
        f"  {s.get('dose','?')} | {s.get('timing','?')}\n"
        f"  _{s.get('benefit','')}_"
        for s in supplements
    )
    await update.message.reply_text(
        f"💊 *Supplement Stack*\n\n{supp_lines}",
        parse_mode="Markdown",
    )

    # ── Coaching ──
    await update.message.reply_text(
        f"💬 *Coaching Notes*\n\n"
        f"🎯 *Top Priority:* {coaching.get('top_priority','')}\n\n"
        f"😴 *Sleep:* {coaching.get('sleep','')}\n\n"
        f"🧘 *Stress:* {coaching.get('stress','')}\n\n"
        f"📊 *Tracking:* {coaching.get('tracking','')}\n\n"
        f"📅 *12-Week Outlook:* {coaching.get('expectations','')}\n\n"
        f"_{coaching.get('coach_message','')}_",
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

    print("✅ BodyBuilding Coach Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
