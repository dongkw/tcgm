"""CLI for the local SQLite database ledger."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .db.importers import import_json_ledgers
from .db.consistency import reconcile_database
from .db.migrations import init_database
from .db.validators import backup_database, summary_database, validate_database


def _output_root(args: argparse.Namespace) -> Path:
    return Path(args.output_dir)


def _db_path(args: argparse.Namespace) -> str | None:
    return args.db_path


def init_command(args: argparse.Namespace) -> None:
    result = init_database(_output_root(args), _db_path(args))
    print(f"database: {result['db_path']}")
    print(f"applied: {', '.join(result['applied']) if result['applied'] else 'none'}")
    print(f"skipped: {', '.join(result['skipped']) if result['skipped'] else 'none'}")


def import_json_command(args: argparse.Namespace) -> None:
    result = import_json_ledgers(_output_root(args), _db_path(args))
    print(f"database: {result['db_path']}")
    print(f"batch: {result['batch_id']}")
    stats: dict[str, int] = result["stats"]
    if not stats:
        print("imported: none")
        return
    print("imported:")
    for table, count in sorted(stats.items()):
        print(f"  {table}: {count}")


def validate_command(args: argparse.Namespace) -> None:
    result = validate_database(_output_root(args), _db_path(args), json_sample_limit=args.json_sample_limit)
    print(f"database: {result['db_path']}")
    print(f"issues: {result['issue_count']}")
    issues: list[dict[str, Any]] = result["issues"]
    for issue in issues[: args.limit]:
        location = issue.get("table") or "database"
        row_id = issue.get("row_id")
        if row_id:
            location = f"{location}:{row_id}"
        print(f"[{issue['severity']}] {issue['code']} {location} - {issue['message']}")
        detail = issue.get("detail") or {}
        if detail:
            print(f"  detail: {detail}")
    if len(issues) > args.limit:
        print(f"... {len(issues) - args.limit} more issue(s)")

    has_error = any(issue.get("severity") == "ERROR" for issue in issues)
    if has_error or (args.strict and issues):
        raise SystemExit(1)


def summary_command(args: argparse.Namespace) -> None:
    result = summary_database(_output_root(args), _db_path(args))
    print(f"database: {result['db_path']}")
    if not result["exists"]:
        print("status: missing")
        return

    print("counts:")
    for table, count in sorted(result["counts"].items()):
        print(f"  {table}: {count}")

    print("accounts:")
    accounts = result["accounts"]
    if not accounts:
        print("  none")
    for account in accounts:
        print(
            "  "
            f"{account.get('account_id')} "
            f"assets={account.get('total_assets')} "
            f"cash={account.get('available_cash')} "
            f"market={account.get('market_value')} "
            f"position_pct={account.get('equity_position_pct')} "
            f"trade_date={account.get('trade_date')}"
        )

    print("active_positions:")
    active_positions = result["active_positions"]
    if not active_positions:
        print("  none")
    for position in active_positions[: args.limit]:
        print(
            "  "
            f"{position.get('account_id')}:{position.get('symbol')} "
            f"qty={position.get('total_quantity')} "
            f"available={position.get('available_quantity')} "
            f"value={position.get('market_value')} "
            f"pct={position.get('position_pct')}"
        )

    print("recent_reports:")
    recent_reports = result["recent_reports"]
    if not recent_reports:
        print("  none")
    for report in recent_reports[: args.limit]:
        print(
            "  "
            f"{report.get('report_type')} "
            f"{report.get('symbol') or '-'} "
            f"{report.get('trade_date') or '-'} "
            f"{report.get('relative_path')}"
        )


def reconcile_command(args: argparse.Namespace) -> None:
    result = reconcile_database(_output_root(args), _db_path(args))
    print(f"database: {result['db_path']}")
    if not result["exists"]:
        print("status: missing")
    else:
        print(f"status: {'ok' if result['ok'] else 'issues'}")
        print("counts:")
        for table, count in sorted(result["counts"].items()):
            print(f"  {table}: files={count['files']} db_rows={count['db_rows']}")

    print(f"issues: {result['issue_count']}")
    issues: list[dict[str, Any]] = result["issues"]
    for issue in issues[: args.limit]:
        location = issue.get("table") or "database"
        row_id = issue.get("row_id")
        if row_id:
            location = f"{location}:{row_id}"
        print(f"[{issue['severity']}] {issue['code']} {location} - {issue['message']}")
        detail = issue.get("detail") or {}
        if detail:
            print(f"  detail: {detail}")
    if len(issues) > args.limit:
        print(f"... {len(issues) - args.limit} more issue(s)")

    has_error = any(issue.get("severity") == "ERROR" for issue in issues)
    if has_error or (args.strict and issues):
        raise SystemExit(1)


def backup_command(args: argparse.Namespace) -> None:
    result = backup_database(_output_root(args), _db_path(args), args.backup_dir)
    print(f"source: {result['source']}")
    print(f"backup: {result['backup_path']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SQLite database tools for the AI trader project.")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--db-path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create or migrate the SQLite database.")
    init_parser.set_defaults(func=init_command)

    import_parser = subparsers.add_parser("import-json", help="Import existing JSON/JSONL ledgers into SQLite.")
    import_parser.set_defaults(func=import_json_command)

    validate_parser = subparsers.add_parser("validate", help="Validate database consistency.")
    validate_parser.add_argument("--limit", type=int, default=50)
    validate_parser.add_argument("--json-sample-limit", type=int, default=200)
    validate_parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings too.")
    validate_parser.set_defaults(func=validate_command)

    summary_parser = subparsers.add_parser("summary", help="Print a short database summary.")
    summary_parser.add_argument("--limit", type=int, default=10)
    summary_parser.set_defaults(func=summary_command)

    reconcile_parser = subparsers.add_parser("reconcile", help="Check current generated files are indexed in SQLite.")
    reconcile_parser.add_argument("--limit", type=int, default=50)
    reconcile_parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings too.")
    reconcile_parser.set_defaults(func=reconcile_command)

    backup_parser = subparsers.add_parser("backup", help="Create a SQLite backup copy.")
    backup_parser.add_argument("--backup-dir")
    backup_parser.set_defaults(func=backup_command)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
