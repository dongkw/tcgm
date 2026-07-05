"""CLI for the second-round paper-trading loop."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .cost_model import CostModel, estimate_trade, money
from .db.sync import mirror_record, mirror_report
from .decision_utils import ensure_dir, first_non_missing
from .file_store import append_jsonl, load_json, read_jsonl, write_json, write_jsonl
from .portfolio import (
    account_snapshots_path,
    apply_buy_trade,
    apply_sell_trade,
    compact_now,
    ensure_account_positions,
    init_account,
    is_weekday_trade_date,
    load_accounts,
    load_positions,
    next_trade_date,
    now_iso,
    position_snapshots_path,
    recompute_account_totals,
    rollover,
    save_accounts,
    save_positions,
)


BUY_ACTIONS = {"BUY", "WATCH_SMALL"}
SELL_ACTIONS = {"REDUCE_HALF", "REDUCE_TO_WATCH", "CLEAR"}
RECORD_ONLY_ACTIONS = {"WAIT", "DO_NOT_BUY", "DATA_BLOCKED", "HOLD", "NO_SELL_T_PLUS", "PRE_EVALUATION"}


def paper_dir(output_root: Path) -> Path:
    return output_root / "paper_trading"


def signals_path(output_root: Path) -> Path:
    return paper_dir(output_root) / "signals.jsonl"


def orders_path(output_root: Path) -> Path:
    return paper_dir(output_root) / "orders.jsonl"


def trades_path(output_root: Path) -> Path:
    return paper_dir(output_root) / "trades.jsonl"


def reports_dir(output_root: Path) -> Path:
    return output_root / "reports"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _find_snapshot(decision: dict[str, Any], output_root: Path, explicit_path: str | None = None) -> tuple[dict[str, Any] | None, Path | None]:
    if explicit_path:
        path = Path(explicit_path)
        return _read_json(path), path

    snapshot_id = decision.get("snapshot_id")
    if not snapshot_id:
        return None, None

    expected_name = snapshot_id.replace("ss_", "strategy_snapshot_", 1) + ".json"
    expected_path = output_root / "strategy_snapshots" / expected_name
    if expected_path.exists():
        return _read_json(expected_path), expected_path

    suffix = snapshot_id.replace("ss_", "", 1)
    matches = sorted((output_root / "strategy_snapshots").glob(f"*{suffix}*.json"))
    if matches:
        return _read_json(matches[0]), matches[0]
    return None, None


def _stock_json_path(output_root: Path, symbol: str) -> Path:
    return output_root / "stock_json" / f"stock_data_{symbol}.json"


def _reference_quote(decision: dict[str, Any], snapshot: dict[str, Any] | None, output_root: Path) -> dict[str, Any]:
    symbol = decision.get("symbol")
    decision_trade_date = (decision.get("time_context") or {}).get("trade_date")

    if snapshot:
        price = (snapshot.get("quote") or {}).get("price")
        if price:
            return {
                "price": float(price),
                "trade_date": snapshot.get("trade_date") or decision_trade_date,
                "quote_time": snapshot.get("decision_time"),
                "quote_source": "strategy_snapshot.quote.price",
            }

    reference_price = decision.get("reference_price")
    if reference_price:
        return {
            "price": float(reference_price),
            "trade_date": decision_trade_date,
            "quote_time": decision.get("decision_time"),
            "quote_source": "decision_result.reference_price",
        }

    decision_quote = decision.get("quote") or {}
    if decision_quote.get("price"):
        return {
            "price": float(decision_quote["price"]),
            "trade_date": decision_quote.get("trade_date") or decision_trade_date,
            "quote_time": decision.get("decision_time"),
            "quote_source": "decision_result.quote.price",
        }

    if symbol:
        stock_path = _stock_json_path(output_root, symbol)
        stock_data = load_json(stock_path, {})
        quote = stock_data.get("quote") or {}
        if quote.get("price"):
            quote_trade_date = quote.get("trade_date")
            if decision_trade_date and quote_trade_date and quote_trade_date != decision_trade_date:
                return {
                    "price": None,
                    "trade_date": quote_trade_date,
                    "quote_time": None,
                    "quote_source": "stock_json.quote.price",
                    "reject_reason": "PRICE_TIME_MISMATCH",
                }
            return {
                "price": float(quote["price"]),
                "trade_date": quote_trade_date,
                "quote_time": None,
                "quote_source": "stock_json.quote.price",
            }

    return {
        "price": None,
        "trade_date": decision_trade_date,
        "quote_time": None,
        "quote_source": None,
        "reject_reason": "PRICE_MISSING",
    }


def _base_signal(
    decision: dict[str, Any],
    account_id: str,
    decision_path: Path,
    strategy_version: str,
) -> dict[str, Any]:
    final_action = decision.get("final_action")
    if final_action in BUY_ACTIONS:
        signal_action = "BUY"
    elif final_action in SELL_ACTIONS:
        signal_action = "SELL"
    else:
        signal_action = "NONE"

    return {
        "signal_id": f"sig_{compact_now()}",
        "account_id": account_id,
        "decision_id": decision.get("decision_id"),
        "snapshot_id": decision.get("snapshot_id"),
        "symbol": decision.get("symbol"),
        "name": decision.get("name"),
        "task_type": decision.get("task_type"),
        "final_action": final_action,
        "confidence": decision.get("confidence"),
        "signal_action": signal_action,
        "signal_quantity": None,
        "signal_cash_amount": (decision.get("position_plan") or {}).get("suggested_cash_amount"),
        "source_decision_time": decision.get("decision_time"),
        "source_decision_path": str(decision_path),
        "source_decision_hash": _file_hash(decision_path),
        "decision_schema_version": decision.get("schema_version"),
        "strategy_version": strategy_version,
        "action_reason": decision.get("action_reason"),
        "position_plan_snapshot": decision.get("position_plan") or {},
        "created_at": now_iso(),
        "status": "RECORDED",
        "blocked_reason": None,
    }


def _time_block_reason(decision: dict[str, Any]) -> str | None:
    time_context = decision.get("time_context") or {}
    if not time_context.get("is_trading_day"):
        return "NON_TRADING_DAY"
    if time_context.get("session_name") == "NON_TRADING":
        return "NON_TRADING_DAY"
    return None


def _rollover_block_reason(account: dict[str, Any], decision: dict[str, Any]) -> str | None:
    trade_date = (decision.get("time_context") or {}).get("trade_date")
    if not trade_date:
        return "PRICE_TIME_MISMATCH"
    if account.get("last_rollover_trade_date") != trade_date:
        return "DAILY_ROLLOVER_MISSING"
    return None


def _make_order(
    signal: dict[str, Any],
    decision: dict[str, Any],
    side: str,
    quantity: int,
    reference_price: float | None,
    reject_reason: str | None = None,
) -> dict[str, Any]:
    status = "REJECTED" if reject_reason else "PENDING"
    return {
        "order_id": f"ord_{compact_now()}",
        "account_id": signal["account_id"],
        "signal_id": signal["signal_id"],
        "decision_id": decision.get("decision_id"),
        "snapshot_id": decision.get("snapshot_id"),
        "symbol": decision.get("symbol"),
        "name": decision.get("name"),
        "side": side,
        "order_type": "MARKET",
        "requested_quantity": int(quantity),
        "limit_price": None,
        "reference_price": reference_price,
        "status": status,
        "reject_reason": reject_reason,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def _append_signal(output_root: Path, signal: dict[str, Any]) -> None:
    path = signals_path(output_root)
    append_jsonl(path, signal)
    mirror_record(output_root, "paper_signal", signal, source_path=path)


def _append_order(output_root: Path, order: dict[str, Any]) -> None:
    path = orders_path(output_root)
    append_jsonl(path, order)
    mirror_record(output_root, "paper_order", order, source_path=path)


def _append_trade(output_root: Path, trade: dict[str, Any]) -> None:
    path = trades_path(output_root)
    append_jsonl(path, trade)
    mirror_record(output_root, "paper_trade", trade, source_path=path)


def _normalize_buy_quantity(raw_quantity: float) -> int:
    return int(math.floor(raw_quantity / 100) * 100)


def _normalize_sell_quantity(action: str, raw_quantity: int, available_quantity: int) -> int:
    if action == "CLEAR":
        return max(0, int(available_quantity))
    return int(math.floor(raw_quantity / 100) * 100)


def _buy_quantity_and_cost(
    decision: dict[str, Any],
    account: dict[str, Any],
    account_positions: dict[str, Any],
    reference_price: float,
    default_watch_cash: float,
    cost_model: CostModel,
) -> tuple[int, dict[str, float] | None, str | None]:
    plan = decision.get("position_plan") or {}
    suggested_quantity = plan.get("suggested_quantity")
    suggested_cash = plan.get("suggested_cash_amount")

    if suggested_quantity:
        quantity = _normalize_buy_quantity(float(suggested_quantity))
    else:
        cash_amount = float(first_non_missing(suggested_cash, default_watch_cash))
        quantity = _normalize_buy_quantity(cash_amount / reference_price)

    if quantity < 100:
        return 0, None, "CASH_NOT_ENOUGH_FOR_ONE_LOT"

    symbol = decision.get("symbol")
    current_position = account_positions.get(symbol) or {}
    current_market_value = float(current_position.get("market_value") or 0.0)
    total_assets = float(account.get("total_assets") or 0.0)
    reserve_cash = total_assets * float(account.get("cash_reserve_pct") or 0.0) / 100
    max_single_position_value = total_assets * float(account.get("max_single_position_pct") or 100.0) / 100
    max_daily_buy_amount = float(account.get("max_daily_buy_amount") or 0.0)
    today_buy_used = float(account.get("today_buy_used") or 0.0)
    available_cash = float(account.get("available_cash") or 0.0)

    while quantity >= 100:
        estimate = estimate_trade("BUY", quantity, reference_price, cost_model)
        cash_after = available_cash - estimate["net_amount"]
        projected_position_value = current_market_value + quantity * reference_price
        within_cash = cash_after >= 0
        within_reserve = cash_after >= reserve_cash
        within_daily = max_daily_buy_amount <= 0 or today_buy_used + estimate["net_amount"] <= max_daily_buy_amount
        within_single = total_assets <= 0 or projected_position_value <= max_single_position_value
        if within_cash and within_reserve and within_daily and within_single:
            return quantity, estimate, None
        quantity -= 100

    return 0, None, "CASH_NOT_ENOUGH"


def _sell_quantity_and_cost(
    decision: dict[str, Any],
    account_positions: dict[str, Any],
    reference_price: float,
    default_watch_cash: float,
    cost_model: CostModel,
) -> tuple[int, dict[str, float] | None, str | None]:
    symbol = decision.get("symbol")
    position = account_positions.get(symbol)
    if not position or position.get("position_status") == "CLOSED":
        return 0, None, "NO_POSITION"

    available_quantity = int(position.get("available_quantity") or 0)
    if available_quantity <= 0:
        return 0, None, "NO_AVAILABLE_QUANTITY"

    action = decision.get("final_action")
    if action == "REDUCE_HALF":
        raw_quantity = int(math.floor(available_quantity * 0.5))
    elif action == "REDUCE_TO_WATCH":
        target_watch_quantity = _normalize_buy_quantity(default_watch_cash / reference_price)
        raw_quantity = max(available_quantity - target_watch_quantity, 0)
    elif action == "CLEAR":
        raw_quantity = available_quantity
    else:
        return 0, None, "ACTION_NOT_TRADABLE"

    quantity = _normalize_sell_quantity(action, raw_quantity, available_quantity)
    if quantity <= 0:
        return 0, None, "SELL_QUANTITY_NOT_ALLOWED"
    if quantity > available_quantity:
        return 0, None, "NO_AVAILABLE_QUANTITY"

    estimate = estimate_trade("SELL", quantity, reference_price, cost_model)
    if estimate["net_amount"] <= 0:
        return 0, None, "ACTION_NOT_TRADABLE"
    return quantity, estimate, None


def _make_trade(
    order: dict[str, Any],
    decision: dict[str, Any],
    quote: dict[str, Any],
    estimate: dict[str, float],
) -> dict[str, Any]:
    triggers = decision.get("trigger_prices") or {}
    plan = decision.get("position_plan") or {}
    trade_date = (decision.get("time_context") or {}).get("trade_date") or quote.get("trade_date")
    trade = {
        "trade_id": f"trd_{compact_now()}",
        "order_id": order["order_id"],
        "account_id": order["account_id"],
        "decision_id": decision.get("decision_id"),
        "snapshot_id": decision.get("snapshot_id"),
        "symbol": decision.get("symbol"),
        "name": decision.get("name"),
        "side": order["side"],
        "quantity": order["requested_quantity"],
        "reference_price": estimate["reference_price"],
        "fill_price": estimate["fill_price"],
        "gross_amount": estimate["gross_amount"],
        "commission": estimate["commission"],
        "stamp_tax": estimate["stamp_tax"],
        "slippage_cost": estimate["slippage_cost"],
        "net_amount": estimate["net_amount"],
        "trade_time": now_iso(),
        "trade_date": trade_date,
        "quote_source": quote.get("quote_source"),
        "quote_time": quote.get("quote_time"),
        "action_reason": decision.get("action_reason"),
        "invalidation_point": (decision.get("invalidation_points") or {}).get("original"),
        "stop_loss_price": triggers.get("stop_loss_price"),
        "planned_position_pct": plan.get("max_position_pct"),
    }
    return trade


def apply_decision(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_dir)
    decision_path = Path(args.decision_path)
    decision = _read_json(decision_path)
    snapshot, snapshot_path = _find_snapshot(decision, output_root, args.snapshot_path)
    accounts = load_accounts(output_root)
    account = accounts.get(args.account)
    if not account:
        raise ValueError(f"account not found: {args.account}")

    positions = load_positions(output_root)
    account_positions = ensure_account_positions(positions, args.account)
    signal = _base_signal(decision, args.account, decision_path, args.strategy_version)

    final_action = decision.get("final_action")
    if final_action in RECORD_ONLY_ACTIONS:
        if final_action in {"DATA_BLOCKED", "NO_SELL_T_PLUS"}:
            signal["status"] = "BLOCKED"
            signal["blocked_reason"] = final_action
        _append_signal(output_root, signal)
        return {"signal": signal, "order": None, "trade": None, "snapshot_path": str(snapshot_path) if snapshot_path else None}

    if not args.confirm:
        _append_signal(output_root, signal)
        return {"signal": signal, "order": None, "trade": None, "snapshot_path": str(snapshot_path) if snapshot_path else None}

    for blocker in [_time_block_reason(decision), _rollover_block_reason(account, decision)]:
        if blocker:
            signal["status"] = "BLOCKED"
            signal["blocked_reason"] = blocker
            _append_signal(output_root, signal)
            return {"signal": signal, "order": None, "trade": None, "snapshot_path": str(snapshot_path) if snapshot_path else None}

    quote = _reference_quote(decision, snapshot, output_root)
    reference_price = quote.get("price")
    if not reference_price:
        signal["status"] = "BLOCKED"
        signal["blocked_reason"] = quote.get("reject_reason") or "PRICE_MISSING"
        order = _make_order(signal, decision, signal["signal_action"], 0, None, signal["blocked_reason"])
        _append_signal(output_root, signal)
        _append_order(output_root, order)
        return {"signal": signal, "order": order, "trade": None, "snapshot_path": str(snapshot_path) if snapshot_path else None}

    cost_model = CostModel()
    if final_action in BUY_ACTIONS:
        quantity, estimate, reject_reason = _buy_quantity_and_cost(
            decision,
            account,
            account_positions,
            float(reference_price),
            args.default_watch_cash,
            cost_model,
        )
        side = "BUY"
    elif final_action in SELL_ACTIONS:
        quantity, estimate, reject_reason = _sell_quantity_and_cost(
            decision,
            account_positions,
            float(reference_price),
            args.default_watch_cash,
            cost_model,
        )
        side = "SELL"
    else:
        quantity, estimate, reject_reason, side = 0, None, "ACTION_NOT_TRADABLE", signal["signal_action"]

    signal["signal_quantity"] = quantity if quantity > 0 else None
    order = _make_order(signal, decision, side, quantity, float(reference_price), reject_reason)
    if reject_reason or estimate is None:
        signal["status"] = "BLOCKED"
        signal["blocked_reason"] = reject_reason
        _append_signal(output_root, signal)
        _append_order(output_root, order)
        return {"signal": signal, "order": order, "trade": None, "snapshot_path": str(snapshot_path) if snapshot_path else None}

    trade = _make_trade(order, decision, quote, estimate)
    order["status"] = "FILLED"
    order["updated_at"] = now_iso()
    signal["status"] = "ORDER_CREATED"

    if side == "BUY":
        apply_buy_trade(output_root, args.account, trade)
    else:
        apply_sell_trade(output_root, args.account, trade)

    _append_signal(output_root, signal)
    _append_order(output_root, order)
    _append_trade(output_root, trade)
    return {"signal": signal, "order": order, "trade": trade, "snapshot_path": str(snapshot_path) if snapshot_path else None}


def _position_quote(output_root: Path, symbol: str, requested_trade_date: str | None, current_price: float | None) -> dict[str, Any]:
    stock_data = load_json(_stock_json_path(output_root, symbol), {})
    quote = stock_data.get("quote") or {}
    if quote.get("price") and (not requested_trade_date or quote.get("trade_date") == requested_trade_date):
        return {
            "market_price": float(quote["price"]),
            "quote_time": None,
            "quote_source": "stock_json.quote.price",
        }
    return {
        "market_price": current_price,
        "quote_time": None,
        "quote_source": "position.market_price",
    }


def create_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_dir)
    accounts = load_accounts(output_root)
    positions = load_positions(output_root)
    account = accounts.get(args.account)
    if not account:
        raise ValueError(f"account not found: {args.account}")
    account_positions = ensure_account_positions(positions, args.account)
    trade_date = args.trade_date or account.get("last_rollover_trade_date")
    if not trade_date:
        raise ValueError("trade date is required before snapshot; run rollover or pass --trade-date")

    for position in account_positions.values():
        if position.get("position_status") == "CLOSED" and position.get("last_trade_date") != trade_date:
            continue
        quote = _position_quote(output_root, position["symbol"], trade_date, position.get("market_price"))
        if quote.get("market_price"):
            position["market_price"] = float(quote["market_price"])
            position["quote_source"] = quote["quote_source"]
            position["quote_time"] = quote["quote_time"]

    recompute_account_totals(account, account_positions)
    save_accounts(output_root, accounts)
    save_positions(output_root, positions)

    account_snapshot_records = read_jsonl(account_snapshots_path(output_root))
    same_day_account = lambda r: r.get("account_id") == args.account and r.get("trade_date") == trade_date
    existing = [
        r
        for r in account_snapshot_records
        if r.get("account_id") == args.account and r.get("trade_date") != trade_date
    ]
    previous = existing[-1] if existing else None
    previous_assets = float(previous.get("total_assets")) if previous else None
    total_assets = float(account.get("total_assets") or 0.0)
    initial_cash = float(account.get("initial_cash") or 0.0)
    peak_assets = max([float(r.get("total_assets") or 0.0) for r in existing] + [total_assets])
    daily_pnl = money(total_assets - previous_assets) if previous_assets is not None else 0.0
    daily_return_pct = round(daily_pnl / previous_assets * 100, 4) if previous_assets else 0.0
    total_return_pct = round((total_assets / initial_cash - 1) * 100, 4) if initial_cash > 0 else None
    max_drawdown_pct = round((total_assets / peak_assets - 1) * 100, 4) if peak_assets > 0 else None

    snapshot_id = f"acct_{args.account}_{trade_date}_{compact_now()}"
    account_snapshot = {
        "snapshot_id": snapshot_id,
        "account_id": args.account,
        "trade_date": trade_date,
        "snapshot_time": now_iso(),
        "available_cash": account.get("available_cash"),
        "frozen_cash": account.get("frozen_cash"),
        "market_value": account.get("market_value"),
        "total_assets": account.get("total_assets"),
        "daily_pnl": daily_pnl,
        "daily_return_pct": daily_return_pct,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "equity_position_pct": account.get("equity_position_pct"),
        "cash_pct": account.get("cash_pct"),
        "position_count": account.get("position_count"),
        "created_at": now_iso(),
    }
    write_jsonl(account_snapshots_path(output_root), [r for r in account_snapshot_records if not same_day_account(r)])
    account_snapshots_file = account_snapshots_path(output_root)
    append_jsonl(account_snapshots_file, account_snapshot)
    mirror_record(output_root, "account_snapshot", account_snapshot, source_path=account_snapshots_file)

    position_snapshots: list[dict[str, Any]] = []
    position_snapshot_records = read_jsonl(position_snapshots_path(output_root))
    write_jsonl(
        position_snapshots_path(output_root),
        [
            r
            for r in position_snapshot_records
            if not (r.get("account_id") == args.account and r.get("trade_date") == trade_date)
        ],
    )
    for position in account_positions.values():
        if position.get("position_status") == "CLOSED" and position.get("last_trade_date") != trade_date:
            continue
        if int(position.get("total_quantity") or 0) <= 0 and position.get("position_status") != "CLOSED":
            continue
        position_snapshot = {
            "snapshot_id": f"pos_{args.account}_{position['symbol']}_{trade_date}_{compact_now()}",
            "account_id": args.account,
            "symbol": position.get("symbol"),
            "trade_date": trade_date,
            "snapshot_time": now_iso(),
            "total_quantity": position.get("total_quantity"),
            "available_quantity": position.get("available_quantity"),
            "locked_quantity": position.get("locked_quantity"),
            "avg_cost": position.get("avg_cost"),
            "market_price": position.get("market_price"),
            "market_value": position.get("market_value"),
            "unrealized_pnl": position.get("unrealized_pnl"),
            "unrealized_pnl_pct": position.get("unrealized_pnl_pct"),
            "position_pct": position.get("position_pct"),
            "buy_logic": position.get("buy_logic"),
            "invalidation_point": position.get("invalidation_point"),
            "stop_loss_price": position.get("stop_loss_price"),
            "quote_time": position.get("quote_time"),
            "quote_source": position.get("quote_source"),
            "position_status": position.get("position_status"),
            "created_at": now_iso(),
        }
        position_snapshots_file = position_snapshots_path(output_root)
        append_jsonl(position_snapshots_file, position_snapshot)
        mirror_record(output_root, "position_snapshot", position_snapshot, source_path=position_snapshots_file)
        position_snapshots.append(position_snapshot)

    report_path = _write_paper_report(output_root, account_snapshot, position_snapshots)
    return {
        "account_snapshot": account_snapshot,
        "position_snapshots": position_snapshots,
        "report_path": str(report_path),
    }


def _write_paper_report(output_root: Path, account_snapshot: dict[str, Any], position_snapshots: list[dict[str, Any]]) -> Path:
    ensure_dir(reports_dir(output_root))
    account_id = account_snapshot["account_id"]
    trade_date = account_snapshot["trade_date"]
    path = reports_dir(output_root) / f"paper_report_{account_id}_{trade_date}.md"
    lines = [
        f"# 模拟盘报告 {account_id} {trade_date}",
        "",
        "## 账户",
        f"- 总资产：{account_snapshot.get('total_assets')}",
        f"- 可用现金：{account_snapshot.get('available_cash')}",
        f"- 持仓市值：{account_snapshot.get('market_value')}",
        f"- 现金比例：{account_snapshot.get('cash_pct')}%",
        f"- 权益仓位：{account_snapshot.get('equity_position_pct')}%",
        f"- 累计收益率：{account_snapshot.get('total_return_pct')}%",
        f"- 最大回撤：{account_snapshot.get('max_drawdown_pct')}%",
        "",
        "## 持仓",
    ]
    if not position_snapshots:
        lines.append("- 当前无持仓。")
    for position in position_snapshots:
        lines.append(
            "- "
            f"{position.get('symbol')} "
            f"数量={position.get('total_quantity')} "
            f"可卖={position.get('available_quantity')} "
            f"锁定={position.get('locked_quantity')} "
            f"成本={position.get('avg_cost')} "
            f"现价={position.get('market_price')} "
            f"浮盈亏={position.get('unrealized_pnl')} "
            f"仓位={position.get('position_pct')}%"
        )
    lines.extend(
        [
            "",
            "## 说明",
            "- 本报告来自本地模拟盘账本，不代表真实券商账户。",
            "- 第一版成交是粗略模拟，不能代表真实可成交价格。",
            "- 交易策略有效性需要通过连续模拟盘和后续历史回放验证。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mirror_report(
        output_root,
        path,
        report_type="paper",
        account_id=account_id,
        trade_date=trade_date,
        source_type="account_snapshot",
        source_id=account_snapshot.get("snapshot_id"),
    )
    return path


def init_command(args: argparse.Namespace) -> None:
    account = init_account(
        Path(args.output_dir),
        args.account,
        args.cash,
        account_name=args.name,
        cash_reserve_pct=args.cash_reserve_pct,
        max_single_position_pct=args.max_single_position_pct,
        max_daily_buy_amount=args.max_daily_buy_amount,
    )
    print(f"account initialized: {account['account_id']}")
    print(f"available_cash: {account['available_cash']}")


def rollover_command(args: argparse.Namespace) -> None:
    result = rollover(Path(args.output_dir), args.account, args.trade_date)
    print(f"rollover account: {args.account}")
    print(f"trade_date: {result['trade_date']}")
    print(f"released_quantity: {result['released_quantity']}")


def apply_command(args: argparse.Namespace) -> None:
    result = apply_decision(args)
    signal = result["signal"]
    order = result["order"]
    trade = result["trade"]
    print(f"signal: {signal['signal_id']} status={signal['status']} blocked={signal.get('blocked_reason')}")
    if order:
        print(f"order: {order['order_id']} status={order['status']} reject={order.get('reject_reason')}")
    if trade:
        print(f"trade: {trade['trade_id']} side={trade['side']} quantity={trade['quantity']} net_amount={trade['net_amount']}")


def snapshot_command(args: argparse.Namespace) -> None:
    result = create_snapshot(args)
    account_snapshot = result["account_snapshot"]
    print(f"account_snapshot: {account_snapshot['snapshot_id']}")
    print(f"total_assets: {account_snapshot['total_assets']}")
    print(f"positions: {len(result['position_snapshots'])}")
    print(f"report: {result['report_path']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Paper trading ledger for decision_result outputs.")
    parser.add_argument("--output-dir", default="data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a paper account.")
    init_parser.add_argument("--account", default="paper_default")
    init_parser.add_argument("--name", default="默认模拟账户")
    init_parser.add_argument("--cash", type=float, required=True)
    init_parser.add_argument("--cash-reserve-pct", type=float, default=20.0)
    init_parser.add_argument("--max-single-position-pct", type=float, default=30.0)
    init_parser.add_argument("--max-daily-buy-amount", type=float, default=30000.0)
    init_parser.set_defaults(func=init_command)

    rollover_parser = subparsers.add_parser("rollover", help="Run daily rollover and T+1 unlock.")
    rollover_parser.add_argument("--account", default="paper_default")
    rollover_parser.add_argument("--trade-date", required=True)
    rollover_parser.set_defaults(func=rollover_command)

    apply_parser = subparsers.add_parser("apply", help="Apply a decision_result to the paper account.")
    apply_parser.add_argument("decision_path")
    apply_parser.add_argument("--account", default="paper_default")
    apply_parser.add_argument("--snapshot-path")
    apply_parser.add_argument("--confirm", action="store_true")
    apply_parser.add_argument("--default-watch-cash", type=float, default=5000.0)
    apply_parser.add_argument("--strategy-version", default="strategy_v0.1")
    apply_parser.set_defaults(func=apply_command)

    snapshot_parser = subparsers.add_parser("snapshot", help="Write account and position snapshots.")
    snapshot_parser.add_argument("--account", default="paper_default")
    snapshot_parser.add_argument("--trade-date")
    snapshot_parser.set_defaults(func=snapshot_command)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if getattr(args, "trade_date", None) and not is_weekday_trade_date(args.trade_date):
        raise SystemExit(f"trade_date is not a weekday trading candidate: {args.trade_date}")
    args.func(args)


if __name__ == "__main__":
    main()
