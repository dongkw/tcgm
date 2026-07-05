"""File-backed account and position ledger for paper trading."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .cost_model import money
from .decision_utils import ensure_dir, now_stamp
from .file_store import append_jsonl, load_json, read_jsonl, write_json, write_jsonl
from .timekeeper import _tzinfo


DEFAULT_ACCOUNT_NAME = "默认模拟账户"


def now_iso() -> str:
    return datetime.now(_tzinfo()).isoformat()


def compact_now() -> str:
    return now_stamp(datetime.now(_tzinfo()))


def portfolio_dir(output_root: Path) -> Path:
    return output_root / "portfolio"


def accounts_path(output_root: Path) -> Path:
    return portfolio_dir(output_root) / "accounts.json"


def positions_path(output_root: Path) -> Path:
    return portfolio_dir(output_root) / "positions.json"


def position_locks_path(output_root: Path) -> Path:
    return portfolio_dir(output_root) / "position_locks.jsonl"


def cash_ledger_path(output_root: Path) -> Path:
    return portfolio_dir(output_root) / "cash_ledger.jsonl"


def position_ledger_path(output_root: Path) -> Path:
    return portfolio_dir(output_root) / "position_ledger.jsonl"


def account_snapshots_path(output_root: Path) -> Path:
    return portfolio_dir(output_root) / "account_snapshots.jsonl"


def position_snapshots_path(output_root: Path) -> Path:
    return portfolio_dir(output_root) / "position_snapshots.jsonl"


def closed_positions_path(output_root: Path) -> Path:
    return portfolio_dir(output_root) / "closed_positions.jsonl"


def load_accounts(output_root: Path) -> dict[str, Any]:
    return load_json(accounts_path(output_root), {})


def save_accounts(output_root: Path, accounts: dict[str, Any]) -> None:
    path = accounts_path(output_root)
    write_json(path, accounts)
    _mirror_records(output_root, "account", list(accounts.values()), path)


def load_positions(output_root: Path) -> dict[str, Any]:
    return load_json(positions_path(output_root), {})


def save_positions(output_root: Path, positions: dict[str, Any]) -> None:
    path = positions_path(output_root)
    write_json(path, positions)
    records: list[dict[str, Any]] = []
    for account_id, account_positions in positions.items():
        if not isinstance(account_positions, dict):
            continue
        for symbol, position in account_positions.items():
            row = dict(position)
            row["account_id"] = row.get("account_id") or account_id
            row["symbol"] = row.get("symbol") or symbol
            records.append(row)
    _mirror_records(output_root, "position", records, path)


def load_position_locks(output_root: Path) -> list[dict[str, Any]]:
    return read_jsonl(position_locks_path(output_root))


def save_position_locks(output_root: Path, locks: list[dict[str, Any]]) -> None:
    path = position_locks_path(output_root)
    write_jsonl(path, locks)
    _mirror_records(output_root, "position_lock", locks, path)


def _mirror_records(output_root: Path, record_type: str, records: list[dict[str, Any]], path: Path) -> None:
    if not records:
        return
    from .db.sync import mirror_records

    mirror_records(output_root, record_type, records, source_path=path)


def ensure_account_positions(positions: dict[str, Any], account_id: str) -> dict[str, Any]:
    account_positions = positions.setdefault(account_id, {})
    if not isinstance(account_positions, dict):
        raise ValueError(f"invalid positions for account: {account_id}")
    return account_positions


def init_account(
    output_root: Path,
    account_id: str,
    initial_cash: float,
    account_name: str = DEFAULT_ACCOUNT_NAME,
    cash_reserve_pct: float = 20.0,
    max_single_position_pct: float = 30.0,
    max_daily_buy_amount: float = 30000.0,
) -> dict[str, Any]:
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")

    ensure_dir(portfolio_dir(output_root))
    accounts = load_accounts(output_root)
    if account_id in accounts:
        raise ValueError(f"account already exists: {account_id}")

    ts = now_iso()
    account = {
        "account_id": account_id,
        "account_name": account_name,
        "account_type": "PAPER",
        "base_currency": "CNY",
        "initial_cash": money(initial_cash),
        "available_cash": money(initial_cash),
        "frozen_cash": 0.0,
        "market_value": 0.0,
        "total_assets": money(initial_cash),
        "cash_reserve_pct": float(cash_reserve_pct),
        "max_single_position_pct": float(max_single_position_pct),
        "max_daily_buy_amount": money(max_daily_buy_amount),
        "today_buy_used": 0.0,
        "today_sell_amount": 0.0,
        "last_rollover_trade_date": None,
        "created_at": ts,
        "updated_at": ts,
    }
    accounts[account_id] = account
    save_accounts(output_root, accounts)

    positions = load_positions(output_root)
    ensure_account_positions(positions, account_id)
    save_positions(output_root, positions)

    append_cash_ledger(
        output_root,
        account_id=account_id,
        event_type="INITIAL_CASH",
        amount=money(initial_cash),
        cash_before=0.0,
        cash_after=money(initial_cash),
        trade_date=None,
        note="paper account initialized",
    )
    return account


def require_account(output_root: Path, account_id: str) -> dict[str, Any]:
    accounts = load_accounts(output_root)
    account = accounts.get(account_id)
    if not account:
        raise ValueError(f"account not found: {account_id}")
    return account


def append_cash_ledger(
    output_root: Path,
    *,
    account_id: str,
    event_type: str,
    amount: float,
    cash_before: float,
    cash_after: float,
    trade_date: str | None,
    related_order_id: str | None = None,
    related_trade_id: str | None = None,
    related_decision_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    record = {
        "cash_ledger_id": f"cl_{compact_now()}",
        "account_id": account_id,
        "trade_date": trade_date,
        "event_time": now_iso(),
        "event_type": event_type,
        "amount": money(amount),
        "cash_before": money(cash_before),
        "cash_after": money(cash_after),
        "related_order_id": related_order_id,
        "related_trade_id": related_trade_id,
        "related_decision_id": related_decision_id,
        "note": note,
        "created_at": now_iso(),
    }
    path = cash_ledger_path(output_root)
    append_jsonl(path, record)
    _mirror_records(output_root, "cash_ledger", [record], path)
    return record


def append_position_ledger(
    output_root: Path,
    *,
    account_id: str,
    symbol: str,
    trade_date: str | None,
    event_type: str,
    quantity_change: int,
    before: dict[str, Any],
    after: dict[str, Any],
    related_order_id: str | None = None,
    related_trade_id: str | None = None,
    related_decision_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    record = {
        "position_ledger_id": f"pl_{compact_now()}",
        "account_id": account_id,
        "symbol": symbol,
        "trade_date": trade_date,
        "event_time": now_iso(),
        "event_type": event_type,
        "quantity_change": int(quantity_change),
        "total_before": before.get("total_quantity", 0),
        "total_after": after.get("total_quantity", 0),
        "available_before": before.get("available_quantity", 0),
        "available_after": after.get("available_quantity", 0),
        "locked_before": before.get("locked_quantity", 0),
        "locked_after": after.get("locked_quantity", 0),
        "avg_cost_before": before.get("avg_cost"),
        "avg_cost_after": after.get("avg_cost"),
        "related_order_id": related_order_id,
        "related_trade_id": related_trade_id,
        "related_decision_id": related_decision_id,
        "note": note,
        "created_at": now_iso(),
    }
    path = position_ledger_path(output_root)
    append_jsonl(path, record)
    _mirror_records(output_root, "position_ledger", [record], path)
    return record


def next_trade_date(trade_date: str) -> str:
    current = date.fromisoformat(trade_date)
    nxt = current + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt.isoformat()


def is_weekday_trade_date(trade_date: str) -> bool:
    return date.fromisoformat(trade_date).weekday() < 5


def recompute_account_totals(account: dict[str, Any], account_positions: dict[str, Any]) -> None:
    market_value = 0.0
    active_count = 0
    for position in account_positions.values():
        if position.get("position_status") == "CLOSED":
            continue
        total_quantity = int(position.get("total_quantity") or 0)
        market_price = float(position.get("market_price") or 0.0)
        position["market_value"] = money(total_quantity * market_price)
        market_value += position["market_value"]
        if total_quantity > 0:
            active_count += 1

    account["market_value"] = money(market_value)
    account["total_assets"] = money(
        float(account.get("available_cash") or 0.0)
        + float(account.get("frozen_cash") or 0.0)
        + market_value
    )
    total_assets = float(account.get("total_assets") or 0.0)
    account["equity_position_pct"] = round(market_value / total_assets * 100, 4) if total_assets > 0 else None
    account["cash_pct"] = round(float(account.get("available_cash") or 0.0) / total_assets * 100, 4) if total_assets > 0 else None
    account["position_count"] = active_count
    account["updated_at"] = now_iso()

    for position in account_positions.values():
        total_quantity = int(position.get("total_quantity") or 0)
        avg_cost = float(position.get("avg_cost") or 0.0)
        market_price = float(position.get("market_price") or 0.0)
        market_value = float(position.get("market_value") or 0.0)
        position["position_pct"] = round(market_value / total_assets * 100, 4) if total_assets > 0 else None
        position["unrealized_pnl"] = money(market_value - total_quantity * avg_cost) if total_quantity > 0 else 0.0
        position["unrealized_pnl_pct"] = round((market_price / avg_cost - 1) * 100, 4) if total_quantity > 0 and avg_cost > 0 else None
        position["updated_at"] = now_iso()


def add_position_lock(
    output_root: Path,
    *,
    account_id: str,
    symbol: str,
    buy_trade_id: str,
    buy_trade_date: str,
    locked_quantity: int,
) -> dict[str, Any]:
    locks = load_position_locks(output_root)
    lock = {
        "lock_id": f"lock_{compact_now()}",
        "account_id": account_id,
        "symbol": symbol,
        "buy_trade_id": buy_trade_id,
        "buy_trade_date": buy_trade_date,
        "unlock_trade_date": next_trade_date(buy_trade_date),
        "locked_quantity": int(locked_quantity),
        "remaining_locked_quantity": int(locked_quantity),
        "status": "OPEN",
        "created_at": now_iso(),
        "released_at": None,
    }
    locks.append(lock)
    save_position_locks(output_root, locks)
    return lock


def apply_buy_trade(output_root: Path, account_id: str, trade: dict[str, Any]) -> dict[str, Any]:
    accounts = load_accounts(output_root)
    positions = load_positions(output_root)
    account = accounts[account_id]
    account_positions = ensure_account_positions(positions, account_id)
    symbol = trade["symbol"]
    before_cash = float(account.get("available_cash") or 0.0)
    position = dict(account_positions.get(symbol) or {})
    before_position = dict(position)
    old_qty = int(position.get("total_quantity") or 0)
    buy_qty = int(trade["quantity"])
    net_amount = float(trade["net_amount"])
    new_qty = old_qty + buy_qty
    old_cost_value = old_qty * float(position.get("avg_cost") or 0.0)
    new_avg_cost = round((old_cost_value + net_amount) / new_qty, 4)

    account["available_cash"] = money(before_cash - net_amount)
    account["today_buy_used"] = money(float(account.get("today_buy_used") or 0.0) + net_amount)

    position.update(
        {
            "account_id": account_id,
            "symbol": symbol,
            "name": trade.get("name"),
            "asset_type": "A_STOCK",
            "total_quantity": new_qty,
            "available_quantity": int(position.get("available_quantity") or 0),
            "locked_quantity": int(position.get("locked_quantity") or 0) + buy_qty,
            "avg_cost": new_avg_cost,
            "market_price": float(trade["reference_price"]),
            "first_buy_date": position.get("first_buy_date") or trade.get("trade_date"),
            "last_trade_date": trade.get("trade_date"),
            "buy_logic": position.get("buy_logic") or trade.get("action_reason"),
            "invalidation_point": position.get("invalidation_point") or trade.get("invalidation_point"),
            "stop_loss_price": position.get("stop_loss_price") or trade.get("stop_loss_price"),
            "planned_position_pct": position.get("planned_position_pct") or trade.get("planned_position_pct"),
            "position_status": "ACTIVE",
        }
    )
    account_positions[symbol] = position

    add_position_lock(
        output_root,
        account_id=account_id,
        symbol=symbol,
        buy_trade_id=trade["trade_id"],
        buy_trade_date=trade["trade_date"],
        locked_quantity=buy_qty,
    )
    recompute_account_totals(account, account_positions)
    save_accounts(output_root, accounts)
    save_positions(output_root, positions)

    append_cash_ledger(
        output_root,
        account_id=account_id,
        event_type="BUY_OUTFLOW",
        amount=-net_amount,
        cash_before=before_cash,
        cash_after=float(account["available_cash"]),
        trade_date=trade.get("trade_date"),
        related_order_id=trade.get("order_id"),
        related_trade_id=trade.get("trade_id"),
        related_decision_id=trade.get("decision_id"),
    )
    append_position_ledger(
        output_root,
        account_id=account_id,
        symbol=symbol,
        trade_date=trade.get("trade_date"),
        event_type="BUY",
        quantity_change=buy_qty,
        before=before_position,
        after=position,
        related_order_id=trade.get("order_id"),
        related_trade_id=trade.get("trade_id"),
        related_decision_id=trade.get("decision_id"),
    )
    return {"account": account, "position": position}


def apply_sell_trade(output_root: Path, account_id: str, trade: dict[str, Any]) -> dict[str, Any]:
    accounts = load_accounts(output_root)
    positions = load_positions(output_root)
    account = accounts[account_id]
    account_positions = ensure_account_positions(positions, account_id)
    symbol = trade["symbol"]
    if symbol not in account_positions:
        raise ValueError(f"position not found: {symbol}")

    before_cash = float(account.get("available_cash") or 0.0)
    position = account_positions[symbol]
    before_position = dict(position)
    sell_qty = int(trade["quantity"])
    avg_cost = float(position.get("avg_cost") or 0.0)
    total_qty = int(position.get("total_quantity") or 0)
    available_qty = int(position.get("available_quantity") or 0)

    position["total_quantity"] = total_qty - sell_qty
    position["available_quantity"] = available_qty - sell_qty
    position["market_price"] = float(trade["reference_price"])
    position["last_trade_date"] = trade.get("trade_date")
    position["realized_pnl"] = money(
        float(position.get("realized_pnl") or 0.0)
        + sell_qty * float(trade["fill_price"])
        - sell_qty * avg_cost
        - float(trade.get("commission") or 0.0)
        - float(trade.get("stamp_tax") or 0.0)
    )
    if int(position["total_quantity"]) == 0:
        position["position_status"] = "CLOSED"
        position["market_value"] = 0.0
        closed_record = {
            "account_id": account_id,
            "symbol": symbol,
            "open_date": position.get("first_buy_date"),
            "close_date": trade.get("trade_date"),
            "holding_days": None,
            "avg_buy_cost": avg_cost,
            "avg_sell_price": trade.get("fill_price"),
            "total_sell_amount": trade.get("gross_amount"),
            "realized_pnl": position["realized_pnl"],
            "buy_logic": position.get("buy_logic"),
            "invalidation_point": position.get("invalidation_point"),
            "close_reason": trade.get("action_reason"),
            "related_decision_ids": [trade.get("decision_id")],
            "created_at": now_iso(),
        }
        closed_path = closed_positions_path(output_root)
        append_jsonl(closed_path, closed_record)
        _mirror_records(output_root, "closed_position", [closed_record], closed_path)

    account["available_cash"] = money(before_cash + float(trade["net_amount"]))
    account["today_sell_amount"] = money(float(account.get("today_sell_amount") or 0.0) + float(trade["gross_amount"]))

    recompute_account_totals(account, account_positions)
    save_accounts(output_root, accounts)
    save_positions(output_root, positions)

    append_cash_ledger(
        output_root,
        account_id=account_id,
        event_type="SELL_INFLOW",
        amount=float(trade["net_amount"]),
        cash_before=before_cash,
        cash_after=float(account["available_cash"]),
        trade_date=trade.get("trade_date"),
        related_order_id=trade.get("order_id"),
        related_trade_id=trade.get("trade_id"),
        related_decision_id=trade.get("decision_id"),
    )
    append_position_ledger(
        output_root,
        account_id=account_id,
        symbol=symbol,
        trade_date=trade.get("trade_date"),
        event_type="SELL",
        quantity_change=-sell_qty,
        before=before_position,
        after=position,
        related_order_id=trade.get("order_id"),
        related_trade_id=trade.get("trade_id"),
        related_decision_id=trade.get("decision_id"),
    )
    return {"account": account, "position": position}


def rollover(output_root: Path, account_id: str, trade_date: str) -> dict[str, Any]:
    if not is_weekday_trade_date(trade_date):
        raise ValueError(f"trade_date is not a weekday trading candidate: {trade_date}")

    accounts = load_accounts(output_root)
    positions = load_positions(output_root)
    locks = load_position_locks(output_root)
    if account_id not in accounts:
        raise ValueError(f"account not found: {account_id}")

    account = accounts[account_id]
    account_positions = ensure_account_positions(positions, account_id)
    if account.get("last_rollover_trade_date") != trade_date:
        account["today_buy_used"] = 0.0
        account["today_sell_amount"] = 0.0

    released_quantity = 0
    for lock in locks:
        if lock.get("account_id") != account_id or lock.get("status") != "OPEN":
            continue
        if lock.get("unlock_trade_date") and lock["unlock_trade_date"] <= trade_date:
            symbol = lock["symbol"]
            position = account_positions.get(symbol)
            if not position:
                continue
            qty = int(lock.get("remaining_locked_quantity") or 0)
            if qty <= 0:
                continue
            before_position = dict(position)
            position["available_quantity"] = int(position.get("available_quantity") or 0) + qty
            position["locked_quantity"] = max(0, int(position.get("locked_quantity") or 0) - qty)
            lock["remaining_locked_quantity"] = 0
            lock["status"] = "RELEASED"
            lock["released_at"] = now_iso()
            released_quantity += qty
            append_position_ledger(
                output_root,
                account_id=account_id,
                symbol=symbol,
                trade_date=trade_date,
                event_type="T_PLUS_UNLOCK",
                quantity_change=qty,
                before=before_position,
                after=position,
                note=f"unlock lock_id={lock.get('lock_id')}",
            )

    account["last_rollover_trade_date"] = trade_date
    account["updated_at"] = now_iso()
    recompute_account_totals(account, account_positions)
    save_accounts(output_root, accounts)
    save_positions(output_root, positions)
    save_position_locks(output_root, locks)

    return {
        "account": account,
        "released_quantity": released_quantity,
        "trade_date": trade_date,
    }
