"""
LifeOps backups (M4).

    backup(settings)            → backups/lifeops-YYYYMMDD-HHMMSS/  (lifeops.sqlite3 + files/ + vapid key)
    restore(settings, folder)   → puts that backup back; the current data is first moved aside, never lost

The database copy uses SQLite's online backup, so it is consistent even while LifeOps is running.
Backups contain personal data: the backups/ folder is git-ignored and should stay on this computer (or an
encrypted disk). Stop the server before restoring.
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .push import VAPID_FILE
from .settings import ROOT, Settings

BACKUPS = ROOT / "backups"


def backup(settings: Settings, into: Path = BACKUPS) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = into / f"lifeops-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    with closing(sqlite3.connect(settings.db_path)) as source, closing(sqlite3.connect(target / "lifeops.sqlite3")) as copy:
        source.backup(copy)
    files = settings.data_dir / "files"
    if files.is_dir():
        shutil.copytree(files, target / "files")
    key = settings.data_dir / VAPID_FILE
    if key.is_file():
        shutil.copy2(key, target / VAPID_FILE)
    for path in [target, *target.rglob("*")]:  # personal data: only you can read it
        path.chmod(0o700 if path.is_dir() else 0o600)
    check(target)
    return target


def check(folder: Path) -> int:
    """A backup is only good if it opens and passes SQLite's integrity check. Returns the number of tasks."""
    with closing(sqlite3.connect(f"file:{folder / 'lifeops.sqlite3'}?mode=ro", uri=True)) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"Backup {folder.name} failed the integrity check")
        return conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]


def restore(settings: Settings, folder: Path) -> Path:
    """Replace the current data with a backup. The current data is kept in data-before-restore-<time>/."""
    check(folder)
    data = settings.data_dir
    aside = data.with_name(f"{data.name}-before-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    if data.exists():
        data.rename(aside)
    data.mkdir(parents=True)
    shutil.copy2(folder / "lifeops.sqlite3", data / "lifeops.sqlite3")
    if (folder / "files").is_dir():
        shutil.copytree(folder / "files", data / "files")
    if (folder / VAPID_FILE).is_file():
        shutil.copy2(folder / VAPID_FILE, data / VAPID_FILE)
    return aside
