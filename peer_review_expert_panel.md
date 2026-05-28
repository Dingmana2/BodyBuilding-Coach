# BodyBuilding Coach AI — Expert Panel Peer Review

Paste everything from **THE PROMPT** section below into Claude.ai (or any capable LLM) to get structured feedback from 15 professional archetypes in one run.

---

## APP BRIEF (Context for the Reviewer)

**What it is:** An AI-powered fitness, nutrition, and recovery coach delivered entirely through Telegram. Users interact with slash commands and inline keyboards; the bot uses Claude (Anthropic) for all AI features.

**Core features:**

*Workout tracking*
- `/log` — tap-based workout logger with inline weight/rep pickers
- `/logset bench 100kg 8` — quick set logging
- `/stats` — personal records ranked by estimated 1RM (Epley formula), volume per muscle group
- `/weakpoints` — AI analysis of training imbalances from 30-day volume data

*Nutrition*
- `/meal chicken rice broccoli` — natural language food logging with AI macro estimation
- `/macros` — today's targets vs logged intake
- `/fridge` — send a photo of your fridge → AI scans ingredients (categorised into Proteins, Grains, Vegetables, Dairy, Fats, Fruits, Other) → you add missed items via tap buttons or typing → generates 3 macro-aligned recipes
- MyFitnessPal integration (import diary automatically)

*Recovery*
- `/checkin` — daily sleep/energy/soreness/stress scores → AI recovery score (0–100) + coaching tip
- Garmin Connect integration: auto-fills all check-in scores from watch data (sleep stages, HRV, resting HR, Body Battery, SpO2, respiration, steps) — zero manual questions when watch is synced
- `/report` — weekly AI coaching report (adherence, volume trends, next-week focus)

*AI plan generation*
- `/plan` — generates a fully personalised workout split (2–6 days) + macro targets + supplement stack + meal timing + gut health guidance
- Conversational coaching: just message the bot in plain English to tweak the plan ("I'm vegetarian", "remove leg day", "I only have 3 days")
- Research-backed prompt engineering (cites Morton 2018, Schoenfeld 2017, etc.)

*Physique analysis*
- Send a photo → AI estimates body fat %, scores 10 muscle groups (0–10 each), identifies weak points, generates a coaching message
- Supports multi-angle albums (front + back + side analysed together)

*Longevity & health signals*
- Tracks HRV trends, sleep stage breakdowns (deep/REM/light), SpO2, respiratory rate, resting HR from Garmin
- Gut health coaching baked into all nutrition advice (microbiome diversity, fermented foods, prebiotic fibre, omega-3 anti-inflammatories)
- PubMed research integration (`/research`) — fetches and summarises 3 papers per topic

*Gamification*
- `/streak` — check-in streaks with badge tiers (Bronze 7d → Crown 90d)
- `/goals` — set targets (weight, body fat %, date)
- `/progress` — 30-day weight trend, top PRs, recovery averages

**Integrations:** Garmin Connect, MyFitnessPal, Anthropic Claude API (Opus for analysis/plans, Sonnet for chat, Haiku for quick lookups), PubMed EUtils, Nutritionix, Telegram Bot API

**Tech stack:** Python async, `python-telegram-bot` v21, APScheduler, Railway.app deployment, JSON file persistence + FastAPI backend, encrypted credential storage

**Target user:** Intermediate–advanced bodybuilders and strength athletes who want data-driven coaching. App explicitly supports all levels (beginner → advanced), all ages (14–70+), all genders, all goals.

---

## THE PROMPT

Copy everything below this line and paste it into Claude.

---

I'm building an AI fitness coaching app and need structured product feedback from multiple professional perspectives. I'll describe the app, then please respond as each of the 15 expert personas listed below — in sequence. For each persona, use exactly this format:

**What's working well:** (2–3 specific strengths)
**Critical gaps or risks:** (2–3 specific issues, each rated Low / Medium / High / Critical)
**Top 3 actionable recommendations:** numbered list
**One question I'd ask the founder:** one focused question

After all 15 personas, write a **Board-Level Synthesis** covering:
- The top 5 cross-cutting themes flagged by multiple experts
- The single highest-priority fix (and why)
- The single biggest untapped opportunity

---

**APP DESCRIPTION:**

[Paste the full APP BRIEF section above here]

---

**EXPERT PERSONAS:**

**1. Senior Mobile Developer (iOS/Android, 10+ years)**
Focus on: Telegram as a delivery channel vs native app, onboarding friction, inline keyboard UX patterns, notification design, offline capability, platform limitations.

**2. Backend / API Engineer (Python, distributed systems)**
Focus on: JSON file persistence vs a proper database, security of encrypted credentials in state files, Railway.app scalability, async architecture, API rate limits (Garmin, Anthropic, Telegram), error recovery.

**3. UX / Product Designer**
Focus on: discoverability of 31 commands, message formatting and information density, the multi-step fridge flow, onboarding journey, command vs conversational UI tension, progressive disclosure.

**4. Registered Dietitian (RD, clinical background)**
Focus on: accuracy and safety of AI-generated macro targets, evidence base for nutritional claims, gaps (micronutrients, hydration, fibre targets, special populations), eating disorder risk from body image features, medical disclaimer needs.

**5. Sports Nutritionist (performance-focused)**
Focus on: periworkout nutrition windows, supplement stack evidence grades, diet periodisation (bulk/cut/recomp cycles), carb cycling implementation, the gut health coaching additions.

**6. Strength & Conditioning Coach (CSCS)**
Focus on: program design quality (split selection, volume landmarks, progression model), deload protocol, exercise selection for different experience levels, RPE vs % 1RM tracking, lack of velocity/tempo prescription.

**7. Competitive Bodybuilding Coach (NPC/IFBB experience)**
Focus on: contest prep feature gaps (peak week, water/sodium manipulation, carb loading), posing guidance, stage-readiness assessment, diet break protocols, the physique scoring system accuracy.

**8. Longevity Researcher (academic, molecular biology background)**
Focus on: which biomarkers are tracked vs missing (VO2 max proxy, glucose variability, inflammatory markers), HRV interpretation accuracy, stress biomarker integration, zone 2 cardio guidance, sleep science depth.

**9. Sleep Scientist / Chronobiologist**
Focus on: how Garmin sleep stage data is interpreted and coached on, sleep hygiene recommendations, circadian rhythm guidance, recovery scoring validity from wearable data, gaps vs clinical-grade sleep tracking.

**10. Data Scientist / ML Engineer**
Focus on: the quality of the data model being built up over time, features that would enable personalised ML models, hallucination risks in AI-generated plans, bias in physique scoring, metrics that should be tracked but aren't.

**11. Sports Psychologist / Mental Performance Coach**
Focus on: streak gamification risks (anxiety, obsessive tracking), body image sensitivity in physique analysis and scoring, motivation design, how the app handles setbacks (missed sessions, bad weeks), autonomy vs dependency on AI coaching.

**12. Doctor of Physical Therapy (DPT)**
Focus on: injury risk in AI-generated program templates, pain vs soreness differentiation in check-ins, progressive overload safety for different populations, lack of mobility/flexibility programming, red flag detection.

**13. Wearables / Quantified Self Expert**
Focus on: Garmin API coverage (what's being used vs what's available), HRV methodology (RMSSD vs SDNN), multi-device support gaps, data quality and noise, comparison to Whoop/Oura/Apple Watch integration potential.

**14. Fitness App Founder / Investor**
Focus on: market positioning vs competitors (Whoop, MacroFactor, Trainerize, MyFitnessPal), moat and defensibility, Telegram as a channel (retention risk, discoverability), monetisation model, the biggest retention and churn levers.

**15. Privacy & Security Engineer**
Focus on: Telegram as a data channel (message retention, bot data access), encrypted credential storage patterns, GDPR / HIPAA considerations for health data, data minimisation, user data deletion, third-party data sharing (Garmin, MFP).

---

Begin with Persona 1 and work through all 15 in order, then write the Board-Level Synthesis. Be specific, direct, and constructive. Where you spot a genuine risk, flag it clearly. Where you see a real strength, say why it matters.
