import logging
import os
import tempfile
from pathlib import Path
from db import get_stale_items, today_iso, iso_to_display
import config

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent / "data"
_LAST_DIGEST_FILE = _DATA_DIR / "last_digest.txt"


def should_run_digest() -> bool:
    from datetime import datetime
    if datetime.now().hour != config.DIGEST_HOUR:
        return False
    today = today_iso()
    try:
        last = _LAST_DIGEST_FILE.read_text().strip()
        return last != today
    except FileNotFoundError:
        return True


def mark_digest_ran():
    """Write today's date atomically so a crash can't leave a corrupt file."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=_DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(today_iso())
        os.replace(tmp_path, _LAST_DIGEST_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def build_digest_message() -> str | None:
    items = get_stale_items(config.STALE_DAYS)
    if not items:
        return None

    lines = [f"**Stale inventory check — {len(items)} item{'s' if len(items) != 1 else ''} past {config.STALE_DAYS} days**\n"]
    for item in items:
        size_str = f" (size {item['size']})" if item["size"] else ""
        lines.append(
            f"{item['days_held']}d — #{item['id']} {item['item_name']}{size_str}, bought ${item['purchase_price']:.2f}"
        )
    return "\n".join(lines)
