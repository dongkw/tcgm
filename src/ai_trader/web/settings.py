"""Runtime settings for the local dashboard."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..db.connection import resolve_db_path


@dataclass(frozen=True)
class DashboardSettings:
    output_root: Path
    db_path: Path
    host: str = "127.0.0.1"
    port: int = 8000
    ai_provider: str = "codex-cli"
    ai_model: str | None = None
    ai_timeout_seconds: float = 120.0


def build_settings(
    output_dir: str = "data",
    db_path: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    ai_provider: str | None = None,
    ai_model: str | None = None,
    ai_timeout_seconds: float | None = None,
) -> DashboardSettings:
    output_root = Path(output_dir)
    resolved_db_path = resolve_db_path(output_root, db_path)
    return DashboardSettings(
        output_root=output_root,
        db_path=resolved_db_path,
        host=host,
        port=port,
        ai_provider=ai_provider or os.getenv("AI_TRADER_AI_PROVIDER", "codex-cli"),
        ai_model=ai_model or os.getenv("AI_TRADER_AI_MODEL") or None,
        ai_timeout_seconds=(
            ai_timeout_seconds
            if ai_timeout_seconds is not None
            else float(os.getenv("AI_TRADER_AI_TIMEOUT_SECONDS", "120"))
        ),
    )
