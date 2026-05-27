"""Set env vars before any app imports so database.py picks up the test DB."""
import os
import tempfile

# Must come before any import from main, database, or models
_tmp_db = tempfile.mktemp(suffix=".db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp_db}")
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-bytes-paddingx")
os.environ.setdefault("BOT_SECRET", "test-bot-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
os.environ.setdefault("API_BASE_URL", "http://localhost:8000")
