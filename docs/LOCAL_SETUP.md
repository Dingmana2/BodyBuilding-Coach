# Local Setup — BodyBuilding Coach AI

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | **3.11** | Must be 3.11 — `myfitnesspal` dep needs `lxml` which has no 3.14 wheels |
| Git | any | Already installed |
| Telegram bot token | — | From @BotFather |
| Anthropic API key | — | From console.anthropic.com |

### Check your Python version

```powershell
py -3.11 --version   # should print Python 3.11.x
```

If 3.11 is not installed: download from python.org and install alongside your existing Python.

---

## 1. Clone and enter the project

```powershell
cd "C:\Users\Dingm\OneDrive\Desktop\AI-Workspace\Projects\BodyBuilding-Coach"
```

---

## 2. Create a virtual environment with Python 3.11

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

You should see `(.venv)` in your prompt.

---

## 3. Install dependencies

```powershell
pip install -r requirements.txt
```

If `lxml` fails (Python 3.14 issue), you're in the wrong venv. Ensure step 2 used `py -3.11`.

**Barebones install** (skip Garmin + MFP — no lxml needed):

```powershell
pip install anthropic fastapi "uvicorn[standard]" sqlalchemy python-multipart httpx python-dotenv python-telegram-bot apscheduler
```

---

## 4. Configure environment variables

```powershell
Copy-Item .env.example .env
```

Open `.env` and fill in at minimum:

```env
ANTHROPIC_API_KEY=sk-ant-...          # required — all AI features
TELEGRAM_BOT_TOKEN=1234...:ABC...     # required — bot
SECRET_KEY=<random 32 bytes base64>   # required — JWT signing
BOT_SECRET=<random 32 bytes base64>   # required — bot→API auth
DATABASE_URL=sqlite:///./fitness_coach.db  # default is fine locally
API_BASE_URL=http://localhost:8000         # default is fine locally
```

**Generate SECRET_KEY / BOT_SECRET:**

```powershell
python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

**Optional (skip these for now):**
- `ENCRYPTION_KEY` — only needed for Garmin/MFP credential storage
- `STRIPE_SECRET_KEY` — billing UI degrades gracefully without it
- `NUTRITIONIX_APP_ID` / `NUTRITIONIX_API_KEY` — macro lookup falls back to Claude Haiku

---

## 5. Run locally

### Option A — Web app only (no bot)

```powershell
uvicorn main:app --reload --port 8000
```

Open `http://localhost:8000` in your browser. Register an account and explore.

### Option B — Both web app + bot (full stack)

Terminal 1:
```powershell
uvicorn main:app --reload --port 8000
```

Terminal 2:
```powershell
python telegram_bot.py
```

Both must be running. The bot makes HTTP calls to `API_BASE_URL` (localhost:8000) for all data.

---

## 6. Telegram Mini App (local testing)

The Mini App only works over HTTPS. For local dev you need a tunnel:

```powershell
# Install ngrok (one-time)
winget install ngrok

# Start tunnel
ngrok http 8000
```

Copy the `https://xxxx.ngrok.io` URL. Then:

1. Set `API_BASE_URL=https://xxxx.ngrok.io` in `.env` and restart the bot
2. In BotFather: `/setmenubutton` → choose your bot → paste the ngrok URL
3. Open your bot in Telegram → tap the menu button → Mini App loads

---

## 7. Railway deployment

### Add web process to Procfile

The current `Procfile` only runs the bot. Add the web server:

```
web: uvicorn main:app --host 0.0.0.0 --port $PORT
worker: python telegram_bot.py
```

### Required Railway environment variables

| Variable | How to get |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` | @BotFather |
| `SECRET_KEY` | generate (see step 4) |
| `BOT_SECRET` | generate (see step 4) |
| `ENCRYPTION_KEY` | generate (see step 4) — optional but enables Garmin/MFP |
| `DATABASE_URL` | Railway PostgreSQL plugin → auto-filled |
| `API_BASE_URL` | Your Railway web service URL e.g. `https://your-app.railway.app` |

### Python version on Railway

Railway reads `runtime.txt`. The file already contains `python-3.11`. No changes needed.

---

## 8. Quick sanity checks

```powershell
# API health
curl http://localhost:8000/api/health

# Bot is running
# → Should see "✅ BodyBuilding Coach Bot is running…" in terminal 2
```

---

## Known local issues

| Issue | Fix |
|---|---|
| `lxml` install fails | Use Python 3.11 venv (not 3.14) |
| Bot can't reach API | Confirm `API_BASE_URL=http://localhost:8000` and web server is running |
| `ENCRYPTION_KEY is not configured` warning | Safe to ignore locally unless testing Garmin/MFP |
| Mini App shows login screen | Only works with HTTPS — use ngrok tunnel |
