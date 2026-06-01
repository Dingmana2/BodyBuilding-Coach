#!/usr/bin/env bash
set -e

echo "=== BodyBuilding Coach AI — dev setup ==="

# ── gstack (Claude Code skills) ──────────────────────────────────────────────
GSTACK_DIR="$HOME/.claude/skills/gstack"
if [ -d "$GSTACK_DIR" ]; then
    echo "✓ gstack already installed at $GSTACK_DIR"
else
    echo "📦 Installing gstack..."
    git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git "$GSTACK_DIR"
    cd "$GSTACK_DIR" && ./setup
    cd - > /dev/null
    echo "✓ gstack installed"
fi

# ── Python virtualenv + deps ──────────────────────────────────────────────────
if [ ! -d venv ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
echo "📦 Installing Python dependencies..."
pip install -q -r requirements.txt
echo "✓ Python dependencies installed"

# ── .env check ────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo ""
        echo "⚠️  .env created from template — fill in your secrets before running."
    else
        echo ""
        echo "⚠️  No .env found and no .env.example to copy. Create .env manually."
    fi
else
    echo "✓ .env present"
fi

echo ""
echo "=== Setup complete. Run ./run.sh to start the app. ==="
