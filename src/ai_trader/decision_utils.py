"""Shared helpers for the first decision pipeline."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def get_in(data: dict[str, Any], path: str, default: Any = None) -> Any:
    """Read a dotted path from a nested dict."""
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def now_stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S%f")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def is_missing(value: Any) -> bool:
    return value is None or value == ""


def first_non_missing(*values: Any) -> Any:
    for value in values:
        if not is_missing(value):
            return value
    return None
