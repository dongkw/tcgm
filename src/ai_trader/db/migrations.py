"""SQLite migration runner."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

from ..portfolio import now_iso
from .connection import connect, resolve_db_path


def migration_root() -> Path:
    cwd_root = Path.cwd() / "migrations" / "sqlite"
    if cwd_root.exists():
        return cwd_root
    return Path(__file__).resolve().parents[3] / "migrations" / "sqlite"


def _ensure_migration_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            execution_time_ms INTEGER
        )
        """
    )


def _migration_version(path: Path) -> str:
    return path.stem.split("_", 1)[0]


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def init_database(output_root: Path, db_path: str | None = None) -> dict[str, object]:
    resolved = resolve_db_path(output_root, db_path)
    root = migration_root()
    if not root.exists():
        raise FileNotFoundError(f"migration directory not found: {root}")

    applied: list[str] = []
    skipped: list[str] = []
    with connect(resolved) as conn:
        _ensure_migration_table(conn)
        existing = {
            row["version"]: row["checksum"]
            for row in conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        }
        for path in sorted(root.glob("*.sql")):
            version = _migration_version(path)
            sql = path.read_text(encoding="utf-8")
            checksum = _checksum(sql)
            if version in existing:
                if existing[version] != checksum:
                    raise ValueError(f"migration checksum changed: {path.name}")
                skipped.append(path.name)
                continue
            started = time.perf_counter()
            conn.executescript(sql)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            conn.execute(
                """
                INSERT INTO schema_migrations (version, name, checksum, applied_at, execution_time_ms)
                VALUES (?, ?, ?, ?, ?)
                """,
                (version, path.name, checksum, now_iso(), elapsed_ms),
            )
            applied.append(path.name)
        conn.commit()
    return {"db_path": str(resolved), "applied": applied, "skipped": skipped}
