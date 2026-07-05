"""Runtime settings for the local dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..db.connection import resolve_db_path


@dataclass(frozen=True)
class DashboardSettings:
    output_root: Path
    db_path: Path
    host: str = "127.0.0.1"
    port: int = 8000


def build_settings(
    output_dir: str = "data",
    db_path: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> DashboardSettings:
    output_root = Path(output_dir)
    resolved_db_path = resolve_db_path(output_root, db_path)
    return DashboardSettings(
        output_root=output_root,
        db_path=resolved_db_path,
        host=host,
        port=port,
    )
