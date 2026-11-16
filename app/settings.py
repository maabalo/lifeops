"""
LifeOps settings: read from environment variables only (never hard-coded secrets).

    LIFEOPS_DATA_DIR       where the database and uploaded files live (default: ./data, git-ignored)
    LIFEOPS_COOKIE_SECURE  "1" when served over HTTPS (online); "0" for http://127.0.0.1 on this Mac
    LIFEOPS_SESSION_DAYS   how long a login lasts (default 14)
    LIFEOPS_TIMEZONE       default timezone for new accounts (default Asia/Manila)
    LIFEOPS_SCHEDULER_SECONDS  how often reminders are checked (default 60; 0 turns the scheduler off)
    LIFEOPS_VAPID_PRIVATE_KEY  push signing key (PEM); when unset, one is made in the data folder
    ANTHROPIC_API_KEY      lets you choose Claude as the document reader (M5)

See .env.example for the full list. Nothing here is secret yet; the Anthropic key (M5) and push keys (M4)
will also come from environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    cookie_secure: bool
    session_days: int
    default_timezone: str
    scheduler_seconds: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lifeops.sqlite3"


def load_settings() -> Settings:
    """Read the environment each time (tests point LIFEOPS_DATA_DIR at a temporary folder)."""
    data_dir = Path(os.environ.get("LIFEOPS_DATA_DIR", ROOT / "data")).expanduser().resolve()
    return Settings(
        data_dir=data_dir,
        cookie_secure=os.environ.get("LIFEOPS_COOKIE_SECURE", "0") == "1",
        session_days=int(os.environ.get("LIFEOPS_SESSION_DAYS", "14")),
        default_timezone=os.environ.get("LIFEOPS_TIMEZONE", "Asia/Manila"),
        scheduler_seconds=int(os.environ.get("LIFEOPS_SCHEDULER_SECONDS", "60")),  # 0 = off (tests)
    )
