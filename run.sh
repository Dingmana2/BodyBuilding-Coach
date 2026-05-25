#!/usr/bin/env bash
set -e

# Check for .env file
if [ ! -f .env ]; then
    echo "⚠️  No .env file found. Creating from template..."
    cp .env.example .env
    echo "📝 Edit .env and add your ANTHROPIC_API_KEY, then re-run this script."
    exit 1
fi

# Check for virtualenv
if [ ! -d venv ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "📦 Installing dependencies..."
pip install -q -r requirements.txt

echo "🚀 Starting BodyBuilding Coach AI..."
echo "   Open http://localhost:8000 in your browser"
echo ""
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
