# BodyBuilding Coach AI — local dev launcher (Windows PowerShell)
# Usage: .\start.ps1           → web server only (browse at localhost:8000)
#        .\start.ps1 -Bot      → web server + Telegram bot in a second window
#        .\start.ps1 -Tunnel   → web server + ngrok tunnel (for Mini App testing)

param(
    [switch]$Bot,
    [switch]$Tunnel
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# ── 1. Check .env ─────────────────────────────────────────────────────────────
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Write-Host ""
    Write-Host "  No .env file found." -ForegroundColor Red
    Write-Host "  Copy .env.example to .env and fill in your keys:" -ForegroundColor Yellow
    Write-Host "    ANTHROPIC_API_KEY   — from console.anthropic.com"
    Write-Host "    TELEGRAM_BOT_TOKEN  — from @BotFather"
    Write-Host "    SECRET_KEY          — any long random string"
    Write-Host "    BOT_SECRET          — any long random string"
    Write-Host ""
    exit 1
}

# ── 2. Activate .venv ─────────────────────────────────────────────────────────
$activate = Join-Path $root ".venv\Scripts\Activate.ps1"
if (-not (Test-Path $activate)) {
    Write-Host "Creating .venv..." -ForegroundColor Cyan
    python -m venv (Join-Path $root ".venv")
}
& $activate

# ── 3. Install / sync deps ────────────────────────────────────────────────────
$reqs = Join-Path $root "requirements-local.txt"
Write-Host "Checking dependencies..." -ForegroundColor Cyan
pip install -q -r $reqs

# ── 4. Optional: start bot in a second terminal ───────────────────────────────
if ($Bot) {
    Write-Host "Starting Telegram bot in a new window..." -ForegroundColor Cyan
    Start-Process powershell -ArgumentList "-NoExit", "-Command",
        "cd '$root'; & '$root\.venv\Scripts\Activate.ps1'; python telegram_bot.py"
}

# ── 5. Optional: ngrok tunnel for Mini App testing ───────────────────────────
if ($Tunnel) {
    $ngrok = Get-Command ngrok -ErrorAction SilentlyContinue
    if (-not $ngrok) {
        Write-Host ""
        Write-Host "  ngrok not found. Install it:" -ForegroundColor Yellow
        Write-Host "    winget install ngrok.ngrok"
        Write-Host "  Then run: .\start.ps1 -Tunnel"
        Write-Host ""
        Write-Host "  Starting without tunnel (Mini App won't work from Telegram)..." -ForegroundColor Gray
    } else {
        Write-Host "Starting ngrok tunnel on port 8000..." -ForegroundColor Cyan
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "ngrok http 8000"
        Write-Host ""
        Write-Host "  Copy the https://xxxx.ngrok-free.app URL from the ngrok window" -ForegroundColor Yellow
        Write-Host "  Set API_BASE_URL=https://xxxx.ngrok-free.app in your .env" -ForegroundColor Yellow
        Write-Host "  Restart this script after updating .env" -ForegroundColor Yellow
        Write-Host ""
    }
}

# ── 6. Start web server + open browser ───────────────────────────────────────
Write-Host ""
Write-Host "  Starting web server at http://localhost:8000" -ForegroundColor Green
Write-Host ""
Start-Sleep -Seconds 1
Start-Process "http://localhost:8000"

uvicorn main:app --host 0.0.0.0 --port 8000 --reload
