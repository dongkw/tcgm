"""SQLite connection helpers with a narrow future-PostgreSQL boundary."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..decision_utils import ensure_dir


DEFAULT_DB_NAME = "ai_trader.sqlite3"


def default_db_path(output_root: Path) -> Path:
    return output_root / DEFAULT_DB_NAME


def connect(db_path: Path) -> sqlite3.Connection:
    ensure_dir(db_path.parent)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def resolve_db_path(output_root: Path, explicit_db_path: str | None = None) -> Path:
    return Path(explicit_db_path) if explicit_db_path else default_db_path(output_root)
