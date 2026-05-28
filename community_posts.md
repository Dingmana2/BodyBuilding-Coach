# Community Post Templates — BodyBuilding Coach AI

Ready-to-post drafts for 8 platforms. Fill in `[YOUR_LINK]` and attach a screenshot before posting.

---

## 1. Reddit — r/bodybuilding

**Title:** Built an AI bodybuilding coach that lives in Telegram — roast my feature list

---

Hey everyone. I've been building a Telegram bot that acts as a full AI bodybuilding coach and I'd love this community's honest opinion — especially from people who've actually hired coaches or used serious tracking apps.

What it does:

- **Photo physique analysis** — estimates body fat %, scores 10 muscle groups, identifies weak points, generates a coaching message. Send a multi-angle album and it analyses all angles together.
- **Full program + diet generation** — personalised split (PPL, U/L, bro split, etc.), macro targets, supplement stack with evidence grades (A/B/C), gut health guidance baked in
- **Garmin integration** — when your watch is synced, `/checkin` auto-fills your sleep, HRV, Body Battery, resting HR, SpO2 with zero questions asked
- **Fridge scan** — photo your fridge, it detects and categorises ingredients, you add anything missed, then it generates 3 macro-aligned recipes from what you have
- **Conversational coaching** — just message it like a coach ("I'm going on holiday for 2 weeks", "swap deadlifts for trap bar", "I'm cutting now")
- `/weakpoints` — finds your lagging muscle groups from 30 days of volume data
- PubMed research integration — fetches and summarises actual studies on demand

My honest question for this sub: **what am I missing that would make this genuinely useful for someone who's been training for 3+ years?** The beginner features are solid — I'm more worried about whether serious lifters would actually use this day-to-day.

Also — is the physique scoring idea a good one or does it just make people feel bad without useful direction?

Link: [YOUR_LINK]
Screenshot: [attach the /plan or /checkin output]

---

## 2. Reddit — r/nutrition

**Title:** Built an AI nutrition coach — looking for critique from RDs and serious nutrition folks

---

I'm a developer building an AI-powered nutrition coaching tool and I'd genuinely like input from people with actual nutrition credentials or deep knowledge here, because I know AI nutrition advice can go very wrong.

What it does on the nutrition side:

- Generates macro targets personalised to goal (bulk/cut/recomp) + body weight, using 1.6–2.2g/kg protein guidance from Morton et al. (2018)
- Logs meals by natural language description with AI macro estimation
- MyFitnessPal sync for people who already track
- Fridge photo scanning → categorised ingredient list → 3 macro-aligned recipes
- **Gut health guidance** built into all plans: recommends 30+ plant varieties/week, fermented foods as probiotics, complex carbs and resistant starch, omega-3 anti-inflammatories, gut-brain axis awareness
- All diet plans include a `gut_health_note` field and prebiotic/fermented foods in the prioritise list

What I'm **not** doing that I maybe should be:
- Micronutrient tracking
- Fibre targets explicitly
- Special population handling (pregnancy, eating disorder history, medical conditions)
- Any medical disclaimer beyond general "consult a professional" language

**Questions for this community:**
1. What's the highest-risk nutritional claim an AI coach like this could make? What guardrails would you put in?
2. Is the body fat % estimation from photos — even with clear disclaimers — a net harm or net benefit?
3. For the gut health angle: what am I getting wrong or oversimplifying?
4. What would a responsible disclaimer/scope-of-practice statement look like for this kind of tool?

I'm genuinely trying to build something that doesn't cause harm. Constructive criticism very welcome.

Link: [YOUR_LINK]

---

## 3. Reddit — r/fitness

**Title:** Made a Telegram bot that generates workout plans, tracks your lifts, and coaches you in plain English — looking for honest feedback

---

Built an AI fitness coach that works entirely through Telegram (no app to download). It's been my side project for a while and I want real user feedback before I push it harder.

**What it does:**

- `/plan` — generates a full workout split + macro targets + supplements based on your profile. Covers beginner to advanced, all goals.
- `/log` — tap-based workout logger (select exercise → weight → reps with buttons, no typing)
- `/logset bench 100kg 8` — quick set logging if you prefer typing
- `/stats` — personal records + volume per muscle group
- `/checkin` — daily sleep/energy/soreness/stress check-in → recovery score + coaching tip. Connects to Garmin to auto-fill everything.
- `/fridge` — photo your fridge, get recipe suggestions that match your macro targets
- `/research` — searches PubMed and summarises studies on whatever topic you want
- Just message it like a person to modify your plan ("I hurt my shoulder", "add more cardio", "make it 4 days a week")

**What I want feedback on:**

1. Is a Telegram bot the right format, or would you only use this as a native app?
2. Which feature would actually change your training vs which sounds cool but you'd never use?
3. What does your current tracking setup look like — and what's missing from it that you wish existed?
4. Honest reaction to AI-generated physique analysis (from photos) — useful or creepy?

Happy to answer any questions about how it works. Link in comments: [YOUR_LINK]

---

## 4. Reddit — r/longevity

**Title:** Added longevity/healthspan tracking to my fitness app — what biomarkers am I missing?

---

I'm building an AI fitness coach and I've been trying to incorporate meaningful longevity signals alongside the usual bodybuilding metrics. Current longevity-relevant tracking:

**From Garmin integration:**
- HRV (heart rate variability) — used for recovery scoring
- Resting heart rate trends
- Sleep stages (deep/REM/light) with duration
- SpO2 (blood oxygen saturation)
- Respiratory rate during sleep
- Body Battery (Garmin's composite recovery metric)
- Daily step count

**Nutrition side:**
- Gut microbiome awareness (30+ plant varieties/week target, fermented foods, prebiotic fibre, omega-3 anti-inflammatories)
- Gut-brain axis coaching (stress → digestion → performance loop)

**Recovery scoring:**
- Composite score from HRV + sleep + subjective check-ins
- Weekly trend analysis

**What the AI coach currently doesn't address:**
- VO2 max (Garmin estimates it but I'm not pulling it yet)
- Glucose variability (no CGM integration)
- Grip strength proxy
- Zone 2 cardio tracking
- Inflammatory markers (obviously can't track without bloodwork)
- Hormonal markers

**Questions for this community:**
1. What's the single highest-signal longevity biomarker I should add next, given I already have HRV + sleep stages?
2. Is VO2 max from Garmin's estimation worth including, or is the error too high to be useful?
3. For the gut health angle: am I oversimplifying the microbiome-longevity connection?
4. What would you personally want an AI coach to do with your wearable data that existing apps don't?

Link: [YOUR_LINK]

---

## 5. IndieHackers

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

## 6. ProductHunt

**Tagline:** Your AI bodybuilding coach in Telegram — plans, tracking, Garmin sync, fridge recipes

**Description:**

BodyBuilding Coach AI is a Telegram bot that acts as your personal strength coach, nutritionist, and recovery analyst.

**What it does:**

📋 **Personalized plans** — `/plan` generates a full workout split, macro targets, and supplement stack tailored to your stats, goal, and experience level. Update it in plain English anytime.

🏋️ **Workout tracking** — Tap-based set logging with automatic 1RM calculation and personal records. `/weakpoints` identifies lagging muscle groups from your training history.

🥗 **Nutrition coaching** — Log meals in plain English, sync MyFitnessPal, scan your fridge for macro-matched recipe ideas. Gut health guidance built into every plan.

🫀 **Garmin integration** — When your watch is synced, daily check-ins auto-fill from your sleep, HRV, Body Battery, SpO2, and resting HR data. Zero manual input.

📸 **Physique analysis** — Send a photo (or multi-angle album) and get a body fat estimate, per-muscle scoring, and personalised coaching feedback.

📖 **Research-backed** — `/research` fetches and summarises PubMed studies on demand. All plans cite supporting research.

**Who it's for:** Intermediate–advanced bodybuilders and strength athletes who want data-driven coaching without paying $200/month for a real coach.

**What makes it different:** Works entirely in Telegram (no app install), connects real wearable data (Garmin) to AI coaching, and builds a longitudinal picture of your progress over time — not just today's workout.

Looking for: beta users, coaches who want to evaluate the AI advice quality, and anyone who's tried AI fitness tools and found them lacking.

[YOUR_LINK]

---

## 7. LinkedIn

**Post:**

I've been quietly building an AI fitness coach for the past several months and I think it's ready for honest professional critique.

The concept: a Telegram bot that combines Claude (Anthropic's AI) with Garmin wearable data, MyFitnessPal nutrition logs, and longitudinal workout tracking to deliver genuinely personalised coaching — the kind that used to cost $150–300/month.

Some of what it does that I'm proud of:

→ When your Garmin is synced, the daily recovery check-in asks you *zero* questions — it reads your sleep stages, HRV, Body Battery, SpO2, and resting HR directly from the watch and auto-calculates your recovery score.

→ Photo your fridge → AI scans and categorises every ingredient → you add anything hidden behind other items via tap buttons → it generates 3 recipes that actually hit your macro targets.

→ The nutrition coaching goes beyond macros: gut health awareness (fermented foods, prebiotic fibre, microbiome diversity, omega-3s) is baked into every plan, not bolted on.

→ `/weakpoints` analyses 30 days of your training volume by muscle group and identifies imbalances — the thing coaches usually charge for.

I'm looking for feedback from professionals in fitness, nutrition, sports science, strength coaching, longevity research, and health tech — people who can tell me what an AI coach like this gets wrong, what's missing, and where it could cause harm.

If you work in any of these fields and are willing to spend 10 minutes giving honest feedback, I'd genuinely value it. Not looking for "this is cool!" — I want the hard critique.

Comment below or DM me. [YOUR_LINK]

---

## 8. Discord / Slack Community (Fitness & Dev Communities)

**Post (works for: fitness dev Discords, quantified self groups, bodybuilding Discords, nutrition professional Slacks):**

---

Hey everyone 👋 I'm looking for honest feedback on a fitness AI project I've been building.

**What it is:** A Telegram bot that acts as an AI strength coach + nutritionist + recovery analyst. Uses Claude (Anthropic) for all the AI, connects to Garmin for wearable data, and MyFitnessPal for nutrition.

**Highlights worth knowing about:**
- Auto-fills daily recovery check-ins from Garmin data (sleep stages, HRV, Body Battery, SpO2) — no manual input when watch is synced
- Fridge photo scanning with categorised ingredient detection → add missed items via buttons or typing → generate macro-aligned recipes
- Physique analysis from photos (BF% estimate, per-muscle scoring, coaching feedback)
- Plan generation with gut health guidance, PubMed research integration, supplement stack grading

**What I'm specifically looking for feedback on:**
1. If you're a coach/nutritionist/researcher: does the AI advice quality hold up? Where does it go wrong?
2. If you're a developer: any obvious architecture red flags? (I'm on Telegram + Railway + JSON state right now)
3. If you're a serious athlete: would you actually use this day-to-day, or is something about the format broken?

Link: [YOUR_LINK]
Happy to give anyone access to test it directly — just DM me.

---

*Template notes: Replace `[YOUR_LINK]` with your bot link or landing page. Attach a screenshot of the fridge scan result, `/plan` output, or `/checkin` auto-fill for maximum engagement on visual platforms (Reddit, ProductHunt, LinkedIn).*
