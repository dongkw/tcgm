"""Safe dashboard-triggered commands."""

from __future__ import annotations

from typing import Any

from ..db.consistency import reconcile_database
from ..db.importers import import_json_ledgers
from ..db.validators import backup_database, validate_database
from .settings import DashboardSettings


def import_json(settings: DashboardSettings) -> dict[str, Any]:
    return import_json_ledgers(settings.output_root, str(settings.db_path))


def validate(settings: DashboardSettings) -> dict[str, Any]:
    return validate_database(settings.output_root, str(settings.db_path))


def reconcile(settings: DashboardSettings) -> dict[str, Any]:
    return reconcile_database(settings.output_root, str(settings.db_path))


def backup(settings: DashboardSettings) -> dict[str, Any]:
    return backup_database(settings.output_root, str(settings.db_path))
