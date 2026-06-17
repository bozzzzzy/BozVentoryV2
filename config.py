import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        print(f"ERROR: Required environment variable '{key}' is missing or empty.")
        print(f"       Copy .env.example to .env and fill in all values.")
        sys.exit(1)
    return val


def _require_int(key: str) -> int:
    raw = _require(key)
    try:
        return int(raw)
    except ValueError:
        print(f"ERROR: Environment variable '{key}' must be an integer, got: {raw!r}")
        sys.exit(1)


DISCORD_TOKEN = _require("DISCORD_TOKEN")
ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")
DISCORD_USER_ID = _require_int("DISCORD_USER_ID")
DISCORD_CHANNEL_ID = _require_int("DISCORD_CHANNEL_ID")
GOOGLE_SHEETS_ID = _require("GOOGLE_SHEETS_ID")
GOOGLE_SERVICE_ACCOUNT_PATH = os.environ.get("GOOGLE_SERVICE_ACCOUNT_PATH", "./creds.json")
DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "9"))
STALE_DAYS = int(os.environ.get("STALE_DAYS", "30"))
CONFIRMATION_TIMEOUT_MINUTES = int(os.environ.get("CONFIRMATION_TIMEOUT_MINUTES", "60"))

CATEGORIES = ["cards", "clothing", "sneakers", "electronics", "other"]
DATE_FORMAT_DISPLAY = "%m/%d/%Y"
DATE_FORMAT_STORAGE = "%Y-%m-%d"
CONFIRM_EMOJI = "✅"
CANCEL_EMOJI = "❌"
