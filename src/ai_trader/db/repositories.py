"""Small repository primitives for SQLite imports and queries."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def json_text(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def bool_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0


def upsert(
    conn: sqlite3.Connection,
    table: str,
    record: dict[str, Any],
    conflict_columns: list[str],
) -> None:
    if not record:
        return
    columns = list(record.keys())
    placeholders = ", ".join(["?"] * len(columns))
    column_sql = ", ".join(columns)
    conflict_sql = ", ".join(conflict_columns)
    update_columns = [column for column in columns if column not in conflict_columns]
    values = [record[column] for column in columns]
    if update_columns:
        update_sql = ", ".join(f"{column}=excluded.{column}" for column in update_columns)
        sql = (
            f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {update_sql}"
        )
    else:
        sql = (
            f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_sql}) DO NOTHING"
        )
    conn.execute(sql, values)


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    return int(row["count"] if row else 0)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)
