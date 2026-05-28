# Community Post Templates — BodyBuilding Coach AI

Ready-to-post drafts for 4 platforms. Fill in `[YOUR_LINK]` and attach a screenshot before posting.

> **Note on Reddit:** Reddit subreddits are used as a *knowledge source* for the AI coach — the `/research` command pulls hot posts from r/bodybuilding, r/naturalbodybuilding, r/nutrition, r/fitness, r/longevity, r/Supplements, r/powerlifting, and r/weightlifting and summarises community insights to inform coaching. Posts to Reddit are not part of this strategy.

---

## 1. IndieHackers

**Title:** Building an AI fitness coach on Telegram — architecture decisions I'm questioning + would love founder feedback

---

Hey IH. I've been building a Telegram-based AI fitness/nutrition/recovery coach for the past several months and I'm at the point where I need outside eyes on some architectural and product decisions. Sharing honestly because I think the community can see around corners I can't.

**What I'm building:**
Full-stack AI coaching: workout plan generation, set logging, daily recovery check-ins (Garmin-connected), nutrition tracking (MyFitnessPal sync + AI macro estimation), physique analysis from photos, fridge scan → recipe generation, weekly coaching reports. All delivered through Telegram. Using Claude (Anthropic) for all AI features.

**The decisions I'm second-guessing:**

*Technical:*
- **Telegram vs native app.** Telegram gets me to zero-install distribution and works on every phone, but I own nothing about the user relationship. If Telegram changes its bot API or gets banned in a region, I lose everything. Building a native app means months of work and a much higher acquire-to-activate drop-off. What would you do?
- **JSON file persistence.** I'm storing user state in a JSON file on Railway with a volume mount. It works, it's simple, it's fast to iterate on. But it doesn't scale beyond a few hundred concurrent users and has no query capability. Should I migrate to SQLite/Postgres now or wait until I hit a real constraint?
- **Claude API costs.** Plan generation (Opus) costs ~$0.15–0.40 per run. Physique analysis similar. Check-ins (Haiku) are ~$0.001. At what point does per-API-call pricing model break down vs flat-rate subscriptions?

*Product:*
- **Who is the real customer?** The features are best for intermediate–advanced bodybuilders. But the app technically works for beginners. Do I niche down and market hard to competitive bodybuilders/athletes, or stay broad?
- **Moat.** My real moat is the accumulated user data (workout history, physique progress, recovery trends) that personalises the coaching over time. But right now I don't actually do any personalised ML — I just inject the history into Claude's context. Is that a moat or a mirage?

**What I'd love:**
- Anyone who's built health/fitness apps: what retention looks like in practice
- Anyone who's shipped on Telegram: what you'd do differently
- Anyone who's navigated the "AI feature vs real intelligence" gap

Revenue model is subscription (monthly/annual), with all features unlocked. No freemium limits currently.

Link: [YOUR_LINK]

---

## 2. ProductHunt

**Tagline:** Your AI bodybuilding coach in Telegram — plans, tracking, Garmin sync, fridge recipes

**Description:**

BodyBuilding Coach AI is a Telegram bot that acts as your personal strength coach, nutritionist, and recovery analyst.

**What it does:**

📋 **Personalized plans** — `/plan` generates a full workout split, macro targets, and supplement stack tailored to your stats, goal, and experience level. Update it in plain English anytime.

🏋️ **Workout tracking** — Tap-based set logging with automatic 1RM calculation and personal records. `/weakpoints` identifies lagging muscle groups from your training history.

🥗 **Nutrition coaching** — Log meals in plain English, sync MyFitnessPal, scan your fridge for macro-matched recipe ideas. Gut health guidance built into every plan.

🫀 **Garmin integration** — When your watch is synced, daily check-ins auto-fill from your sleep, HRV, Body Battery, SpO2, and resting HR data. Zero manual input.

📸 **Physique analysis** — Send a photo (or multi-angle album) and get a body fat estimate, per-muscle scoring, and personalised coaching feedback.

📖 **Research-backed** — `/research` fetches PubMed studies AND community insights from top fitness subreddits, summarised into actionable coaching knowledge.

**Who it's for:** Intermediate–advanced bodybuilders and strength athletes who want data-driven coaching without paying $200/month for a real coach.

**What makes it different:** Works entirely in Telegram (no app install), connects real wearable data (Garmin) to AI coaching, and builds a longitudinal picture of your progress over time — not just today's workout.

Looking for: beta users, coaches who want to evaluate the AI advice quality, and anyone who's tried AI fitness tools and found them lacking.

[YOUR_LINK]

---

## 3. LinkedIn

**Post:**

I've been quietly building an AI fitness coach for the past several months and I think it's ready for honest professional critique.

The concept: a Telegram bot that combines Claude (Anthropic's AI) with Garmin wearable data, MyFitnessPal nutrition logs, and longitudinal workout tracking to deliver genuinely personalised coaching — the kind that used to cost $150–300/month.

Some of what it does that I'm proud of:

→ When your Garmin is synced, the daily recovery check-in asks you *zero* questions — it reads your sleep stages, HRV, Body Battery, SpO2, and resting HR directly from the watch and auto-calculates your recovery score.

→ Photo your fridge → AI scans and categorises every ingredient → you add anything hidden behind other items via tap buttons → it generates 3 recipes that actually hit your macro targets.

→ The nutrition coaching goes beyond macros: gut health awareness (fermented foods, prebiotic fibre, microbiome diversity, omega-3s) is baked into every plan, not bolted on.

→ `/research` pulls the latest PubMed papers AND synthesises what top fitness communities are discussing — feeding both scientific and practitioner knowledge into the coaching brain.

I'm looking for feedback from professionals in fitness, nutrition, sports science, strength coaching, longevity research, and health tech — people who can tell me what an AI coach like this gets wrong, what's missing, and where it could cause harm.

If you work in any of these fields and are willing to spend 10 minutes giving honest feedback, I'd genuinely value it. Not looking for "this is cool!" — I want the hard critique.

Comment below or DM me. [YOUR_LINK]

---

## 4. Discord / Slack Community (Fitness & Dev Communities)

**Post (works for: fitness dev Discords, quantified self groups, bodybuilding Discords, nutrition professional Slacks):**

---

Hey everyone 👋 I'm looking for honest feedback on a fitness AI project I've been building.

**What it is:** A Telegram bot that acts as an AI strength coach + nutritionist + recovery analyst. Uses Claude (Anthropic) for all the AI, connects to Garmin for wearable data, and MyFitnessPal for nutrition.

**Highlights worth knowing about:**
- Auto-fills daily recovery check-ins from Garmin data (sleep stages, HRV, Body Battery, SpO2) — no manual input when watch is synced
- Fridge photo scanning with categorised ingredient detection → add missed items via buttons or typing → generate macro-aligned recipes
- Physique analysis from photos (BF% estimate, per-muscle scoring, coaching feedback)
- `/research` now pulls from PubMed + hot posts across 8 fitness subreddits, summarised into coach-relevant insights

**What I'm specifically looking for feedback on:**
1. If you're a coach/nutritionist/researcher: does the AI advice quality hold up? Where does it go wrong?
2. If you're a developer: any obvious architecture red flags? (I'm on Telegram + Railway + JSON state right now)
3. If you're a serious athlete: would you actually use this day-to-day, or is something about the format broken?

Link: [YOUR_LINK]
Happy to give anyone access to test it directly — just DM me.

---

*Template notes: Replace `[YOUR_LINK]` with your bot link or landing page. Attach a screenshot of the fridge scan result, `/plan` output, or `/checkin` auto-fill for maximum engagement on visual platforms (ProductHunt, LinkedIn).*
