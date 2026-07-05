"""Historical replay CLI for REPLAY_LITE daily backtests."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .cost_model import CostModel, estimate_trade, money
from .decision_utils import ensure_dir
from .file_store import append_jsonl, read_jsonl, write_json
from .historical_data import bar_by_date, features_until, load_bars_by_symbol, trade_dates_from_bars
from .performance import calculate_replay_performance
from .portfolio import (
    apply_buy_trade,
    apply_sell_trade,
    compact_now,
    ensure_account_positions,
    init_account,
    load_accounts,
    load_positions,
    now_iso,
    recompute_account_totals,
    rollover,
    save_accounts,
    save_positions,
)
from .portfolio_construction import build_allocation
from .replay_snapshot_builder import (
    BUY_EVALUATION,
    HOLDING_REVIEW,
    build_replay_decision,
    build_visible_stock_json,
)
from .risk_control import check_decision_risk


SELL_ACTIONS = {"REDUCE_HALF", "REDUCE_TO_WATCH", "CLEAR"}


def replay_root(output_root: Path, replay_id: str) -> Path:
    return output_root / "replay" / replay_id


def _signals_path(output_root: Path) -> Path:
    return output_root / "paper_trading" / "signals.jsonl"


def _orders_path(output_root: Path) -> Path:
    return output_root / "paper_trading" / "orders.jsonl"


def _trades_path(output_root: Path) -> Path:
    return output_root / "paper_trading" / "trades.jsonl"


def _risk_checks_path(output_root: Path) -> Path:
    return output_root / "risk_checks.jsonl"


def _allocation_plans_path(output_root: Path) -> Path:
    return output_root / "allocation_plans.jsonl"


def _order_intents_path(output_root: Path) -> Path:
    return output_root / "order_intents.jsonl"


def _daily_records_path(output_root: Path) -> Path:
    return output_root / "daily_replay_records.jsonl"


def _error_cases_path(output_root: Path) -> Path:
    return output_root / "error_cases.jsonl"


def _split_symbols(value: str) -> list[str]:
    symbols: list[str] = []
    for item in value.replace("\n", ",").split(","):
        symbol = item.strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def run_replay(args: argparse.Namespace) -> dict[str, Any]:
    symbols = _split_symbols(args.symbols)
    if not symbols:
        raise ValueError("at least one symbol is required")

    replay_id = args.replay_id or f"replay_{compact_now()}"
    output_root = replay_root(Path(args.output_dir), replay_id)
    ensure_dir(output_root)

    config = {
        "replay_id": replay_id,
        "account_id": args.account,
        "symbols": symbols,
        "start_date": args.start,
        "end_date": args.end,
        "initial_cash": args.cash,
        "replay_mode": "REPLAY_LITE",
        "strategy_version": args.strategy_version,
        "execution_mode": "NEXT_OPEN",
        "bar_dir": args.bar_dir,
        "cash_reserve_pct": args.cash_reserve_pct,
        "max_single_position_pct": args.max_single_position_pct,
        "max_daily_buy_amount": args.max_daily_buy_amount,
        "default_watch_cash": args.default_watch_cash,
        "created_at": now_iso(),
    }
    write_json(output_root / "replay_config.json", config)
    run_record = {
        **config,
        "started_at": now_iso(),
        "finished_at": None,
        "status": "RUNNING",
        "error_code": None,
        "error_message": None,
    }
    write_json(output_root / "replay_run.json", run_record)

    try:
        result = _run_replay_inner(args, output_root, replay_id, symbols)
        run_record.update(
            {
                "finished_at": now_iso(),
                "status": "SUCCESS",
                "report_path": result["report_path"],
                "performance_path": result["performance_path"],
            }
        )
        write_json(output_root / "replay_run.json", run_record)
        return {"replay_id": replay_id, "output_root": str(output_root), **result}
    except Exception as exc:
        run_record.update(
            {
                "finished_at": now_iso(),
                "status": "FAILED",
                "error_code": exc.__class__.__name__,
                "error_message": str(exc),
            }
        )
        write_json(output_root / "replay_run.json", run_record)
        raise


def _run_replay_inner(
    args: argparse.Namespace,
    output_root: Path,
    replay_id: str,
    symbols: list[str],
) -> dict[str, Any]:
    bars_by_symbol = load_bars_by_symbol(symbols, Path(args.bar_dir))
    dates = trade_dates_from_bars(bars_by_symbol, args.start, args.end)
    if len(dates) < 2:
        raise ValueError("not enough common historical trade dates in requested range")

    init_account(
        output_root,
        args.account,
        args.cash,
        account_name=f"历史回放账户 {replay_id}",
        cash_reserve_pct=args.cash_reserve_pct,
        max_single_position_pct=args.max_single_position_pct,
        max_daily_buy_amount=args.max_daily_buy_amount,
    )

    bars_index = {symbol: bar_by_date(bars) for symbol, bars in bars_by_symbol.items()}
    daily_records: list[dict[str, Any]] = []

    for trade_date in dates:
        rollover_result = rollover(output_root, args.account, trade_date)
        sell_results = _run_holding_reviews(args, output_root, bars_by_symbol, bars_index, trade_date)
        buy_results = _run_buy_plan(args, output_root, bars_by_symbol, bars_index, trade_date)
        _mark_to_market_at_close(output_root, args.account, symbols, bars_by_symbol, bars_index, trade_date)
        snapshot_result = _create_replay_snapshot(output_root, args.account, trade_date)
        daily_record = _daily_record(
            replay_id,
            trade_date,
            symbols,
            rollover_result,
            sell_results,
            buy_results,
            snapshot_result,
        )
        append_jsonl(_daily_records_path(output_root), daily_record)
        daily_records.append(daily_record)

    performance = calculate_replay_performance(
        output_root,
        account_id=args.account,
        initial_cash=args.cash,
        benchmark_return_pct=_benchmark_return_pct(symbols[0], bars_index, dates),
    )
    performance_path = output_root / "performance_metrics.json"
    write_json(performance_path, performance)
    report_path = _write_replay_report(output_root, replay_id, args, performance, daily_records)
    return {
        "daily_count": len(daily_records),
        "performance": performance,
        "performance_path": str(performance_path),
        "report_path": str(report_path),
    }


def _run_holding_reviews(
    args: argparse.Namespace,
    output_root: Path,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    bars_index: dict[str, dict[str, dict[str, Any]]],
    trade_date: str,
) -> dict[str, Any]:
    accounts = load_accounts(output_root)
    account = accounts[args.account]
    positions = load_positions(output_root)
    account_positions = ensure_account_positions(positions, args.account)
    active_positions = [
        position
        for position in account_positions.values()
        if position.get("position_status") != "CLOSED" and int(position.get("total_quantity") or 0) > 0
    ]

    decisions = 0
    orders = 0
    trades = 0
    blocked: list[str] = []

    for position in active_positions:
        symbol = position["symbol"]
        stock_json, error = build_visible_stock_json(symbol, bars_by_symbol.get(symbol, []), trade_date)
        if error:
            blocked.append(error)
            continue
        user_context = {
            "avg_cost": position.get("avg_cost"),
            "position_pct": position.get("position_pct"),
            "total_quantity": position.get("total_quantity"),
            "available_quantity": position.get("available_quantity"),
            "buy_logic": position.get("buy_logic"),
            "invalidation_point": position.get("invalidation_point") or position.get("stop_loss_price"),
            "holding_period": None,
            "available_cash": account.get("available_cash"),
            "total_assets": account.get("total_assets"),
            "cash_reserve_pct": account.get("cash_reserve_pct"),
        }
        result = build_replay_decision(
            symbol=symbol,
            stock_json=stock_json,
            target_trade_date=trade_date,
            task_type=HOLDING_REVIEW,
            user_context=user_context,
            output_root=output_root,
        )
        decision = result["decision"]
        decisions += 1
        _append_signal(output_root, args.account, decision, result["decision_path"], args.strategy_version)
        if decision.get("final_action") in SELL_ACTIONS:
            outcome = _execute_sell_decision(
                args,
                output_root,
                decision,
                result["decision_path"],
                bars_index.get(symbol, {}).get(trade_date),
            )
            orders += 1 if outcome.get("order") else 0
            trades += 1 if outcome.get("trade") else 0
            if outcome.get("blocked_reason"):
                blocked.append(outcome["blocked_reason"])

    return {"decisions": decisions, "orders": orders, "trades": trades, "blocked": blocked}


def _run_buy_plan(
    args: argparse.Namespace,
    output_root: Path,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    bars_index: dict[str, dict[str, dict[str, Any]]],
    trade_date: str,
) -> dict[str, Any]:
    accounts = load_accounts(output_root)
    account = accounts[args.account]
    positions = load_positions(output_root)
    account_positions = ensure_account_positions(positions, args.account)
    trade_history = read_jsonl(_trades_path(output_root))

    candidates: list[dict[str, Any]] = []
    blocked: list[str] = []
    decisions = 0

    for symbol, bars in bars_by_symbol.items():
        stock_json, error = build_visible_stock_json(symbol, bars, trade_date)
        if error:
            blocked.append(error)
            continue
        user_context = {
            "available_cash": account.get("available_cash"),
            "total_assets": account.get("total_assets"),
            "cash_reserve_pct": account.get("cash_reserve_pct"),
        }
        result = build_replay_decision(
            symbol=symbol,
            stock_json=stock_json,
            target_trade_date=trade_date,
            task_type=BUY_EVALUATION,
            user_context=user_context,
            output_root=output_root,
        )
        decision = result["decision"]
        snapshot = result["snapshot"]
        decisions += 1
        _append_signal(output_root, args.account, decision, result["decision_path"], args.strategy_version)
        risk_check = check_decision_risk(
            decision,
            snapshot,
            account,
            account_positions,
            trade_history,
            output_root,
            config=None,
        )
        risk_check["source_decision_path"] = result["decision_path"]
        risk_check["source_snapshot_path"] = result["snapshot_path"]
        append_jsonl(_risk_checks_path(output_root), risk_check)
        candidates.append(
            {
                "decision": decision,
                "decision_path": result["decision_path"],
                "snapshot": snapshot,
                "snapshot_path": result["snapshot_path"],
                "risk_check": risk_check,
            }
        )

    allocation = build_allocation(
        candidates,
        account,
        account_positions,
        trade_date=trade_date,
        strategy_version=args.strategy_version,
        default_watch_cash=args.default_watch_cash,
        max_candidates=args.max_candidates,
    )
    allocation_plan = allocation["allocation_plan"]
    order_intents = allocation["order_intents"]
    append_jsonl(_allocation_plans_path(output_root), allocation_plan)
    for intent in order_intents:
        append_jsonl(_order_intents_path(output_root), intent)

    by_decision_id = {candidate["decision"].get("decision_id"): candidate for candidate in candidates}
    orders = 0
    trades = 0
    ready = 0
    for intent in order_intents:
        if intent.get("status") != "READY_FOR_CONFIRM":
            continue
        ready += 1
        candidate = by_decision_id.get(intent.get("decision_id"))
        if not candidate:
            continue
        bar = bars_index.get(intent["symbol"], {}).get(trade_date)
        outcome = _execute_buy_intent(args, output_root, candidate["decision"], intent, bar)
        orders += 1 if outcome.get("order") else 0
        trades += 1 if outcome.get("trade") else 0
        if outcome.get("blocked_reason"):
            blocked.append(outcome["blocked_reason"])

    return {
        "decisions": decisions,
        "risk_checks": len(candidates),
        "ready_intents": ready,
        "orders": orders,
        "trades": trades,
        "blocked": blocked,
        "allocation_status": allocation_plan.get("status"),
    }


def _execute_buy_intent(
    args: argparse.Namespace,
    output_root: Path,
    decision: dict[str, Any],
    intent: dict[str, Any],
    bar: dict[str, Any] | None,
) -> dict[str, Any]:
    blocked_reason = _bar_block_reason(bar, "BUY")
    quantity = int(intent.get("planned_quantity") or 0)
    reference_price = float(bar.get("open")) if bar and bar.get("open") else None
    order = _make_order(args.account, decision, "BUY", quantity, reference_price, blocked_reason)
    if blocked_reason:
        append_jsonl(_orders_path(output_root), order)
        append_jsonl(_error_cases_path(output_root), _error_case(decision, blocked_reason, "EXECUTION_ERROR"))
        return {"order": order, "trade": None, "blocked_reason": blocked_reason}

    quantity, estimate, reject_reason = _fit_buy_quantity(args, output_root, decision.get("symbol"), quantity, float(reference_price))
    order["requested_quantity"] = quantity
    if reject_reason or estimate is None:
        order["status"] = "REJECTED"
        order["reject_reason"] = reject_reason
        append_jsonl(_orders_path(output_root), order)
        return {"order": order, "trade": None, "blocked_reason": reject_reason}

    trade = _make_trade(order, decision, estimate, "historical_daily_bar.open")
    order["status"] = "FILLED"
    order["updated_at"] = now_iso()
    apply_buy_trade(output_root, args.account, trade)
    append_jsonl(_orders_path(output_root), order)
    append_jsonl(_trades_path(output_root), trade)
    return {"order": order, "trade": trade, "blocked_reason": None}


def _execute_sell_decision(
    args: argparse.Namespace,
    output_root: Path,
    decision: dict[str, Any],
    decision_path: str,
    bar: dict[str, Any] | None,
) -> dict[str, Any]:
    accounts = load_accounts(output_root)
    positions = load_positions(output_root)
    account_positions = ensure_account_positions(positions, args.account)
    position = account_positions.get(decision.get("symbol"))
    blocked_reason = _bar_block_reason(bar, "SELL")
    reference_price = float(bar.get("open")) if bar and bar.get("open") else None
    quantity, estimate, quantity_reject = 0, None, None
    if not blocked_reason and reference_price:
        quantity, estimate, quantity_reject = _sell_quantity_and_estimate(
            decision,
            position,
            reference_price,
            args.default_watch_cash,
        )
        blocked_reason = quantity_reject
    order = _make_order(args.account, decision, "SELL", quantity, reference_price, blocked_reason)
    if blocked_reason or estimate is None:
        append_jsonl(_orders_path(output_root), order)
        return {"order": order, "trade": None, "blocked_reason": blocked_reason}

    trade = _make_trade(order, decision, estimate, "historical_daily_bar.open")
    order["status"] = "FILLED"
    order["updated_at"] = now_iso()
    apply_sell_trade(output_root, args.account, trade)
    append_jsonl(_orders_path(output_root), order)
    append_jsonl(_trades_path(output_root), trade)
    return {"order": order, "trade": trade, "blocked_reason": None}


def _fit_buy_quantity(
    args: argparse.Namespace,
    output_root: Path,
    symbol: str,
    quantity: int,
    reference_price: float,
) -> tuple[int, dict[str, float] | None, str | None]:
    accounts = load_accounts(output_root)
    positions = load_positions(output_root)
    account = accounts[args.account]
    account_positions = ensure_account_positions(positions, args.account)
    current_position = account_positions.get(symbol) or {}
    current_market_value = float(current_position.get("market_value") or 0.0)
    total_assets = float(account.get("total_assets") or 0.0)
    available_cash = float(account.get("available_cash") or 0.0)
    reserve_cash = total_assets * float(account.get("cash_reserve_pct") or 0.0) / 100
    max_single_value = total_assets * float(account.get("max_single_position_pct") or 100.0) / 100
    max_daily_buy_amount = float(account.get("max_daily_buy_amount") or 0.0)
    today_buy_used = float(account.get("today_buy_used") or 0.0)

    while quantity >= 100:
        estimate = estimate_trade("BUY", quantity, reference_price, CostModel())
        if (
            available_cash - estimate["net_amount"] >= 0
            and available_cash - estimate["net_amount"] >= reserve_cash
            and (max_daily_buy_amount <= 0 or today_buy_used + estimate["net_amount"] <= max_daily_buy_amount)
            and (total_assets <= 0 or current_market_value + quantity * reference_price <= max_single_value)
        ):
            return quantity, estimate, None
        quantity -= 100
    return 0, None, "CASH_NOT_ENOUGH_FOR_ONE_LOT"


def _sell_quantity_and_estimate(
    decision: dict[str, Any],
    position: dict[str, Any] | None,
    reference_price: float,
    default_watch_cash: float,
) -> tuple[int, dict[str, float] | None, str | None]:
    if not position or position.get("position_status") == "CLOSED":
        return 0, None, "NO_POSITION"
    available_quantity = int(position.get("available_quantity") or 0)
    if available_quantity <= 0:
        return 0, None, "NO_AVAILABLE_QUANTITY"
    action = decision.get("final_action")
    if action == "CLEAR":
        quantity = available_quantity
    elif action == "REDUCE_HALF":
        quantity = int((available_quantity * 0.5) // 100 * 100)
    elif action == "REDUCE_TO_WATCH":
        target_watch_quantity = int((default_watch_cash / reference_price) // 100 * 100)
        quantity = int(max(available_quantity - target_watch_quantity, 0) // 100 * 100)
    else:
        return 0, None, "ACTION_NOT_TRADABLE"
    if quantity <= 0:
        return 0, None, "SELL_QUANTITY_NOT_ALLOWED"
    estimate = estimate_trade("SELL", quantity, reference_price, CostModel())
    return quantity, estimate, None


def _bar_block_reason(bar: dict[str, Any] | None, side: str) -> str | None:
    if not bar:
        return "OPEN_PRICE_MISSING"
    if bar.get("is_suspended"):
        return "SUSPENDED"
    if not bar.get("open") or float(bar["open"]) <= 0:
        return "OPEN_PRICE_MISSING"
    if side == "BUY" and bar.get("is_limit_up"):
        return "LIMIT_UP_BUY_BLOCKED"
    if side == "SELL" and bar.get("is_limit_down"):
        return "LIMIT_DOWN_SELL_BLOCKED"
    return None


def _make_order(
    account_id: str,
    decision: dict[str, Any],
    side: str,
    quantity: int,
    reference_price: float | None,
    reject_reason: str | None,
) -> dict[str, Any]:
    return {
        "order_id": f"ord_{compact_now()}",
        "account_id": account_id,
        "signal_id": None,
        "decision_id": decision.get("decision_id"),
        "snapshot_id": decision.get("snapshot_id"),
        "symbol": decision.get("symbol"),
        "name": decision.get("name"),
        "side": side,
        "order_type": "MARKET",
        "requested_quantity": int(quantity),
        "limit_price": None,
        "reference_price": reference_price,
        "status": "REJECTED" if reject_reason else "PENDING",
        "reject_reason": reject_reason,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def _make_trade(order: dict[str, Any], decision: dict[str, Any], estimate: dict[str, float], quote_source: str) -> dict[str, Any]:
    trade_date = (decision.get("time_context") or {}).get("trade_date")
    triggers = decision.get("trigger_prices") or {}
    plan = decision.get("position_plan") or {}
    return {
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
        "quote_source": quote_source,
        "quote_time": None,
        "action_reason": decision.get("action_reason"),
        "invalidation_point": (decision.get("invalidation_points") or {}).get("original"),
        "stop_loss_price": triggers.get("stop_loss_price"),
        "planned_position_pct": plan.get("max_position_pct"),
    }


def _append_signal(
    output_root: Path,
    account_id: str,
    decision: dict[str, Any],
    decision_path: str,
    strategy_version: str,
) -> None:
    final_action = decision.get("final_action")
    signal_action = "BUY" if final_action in {"BUY", "WATCH_SMALL"} else "SELL" if final_action in SELL_ACTIONS else "NONE"
    append_jsonl(
        _signals_path(output_root),
        {
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
            "source_decision_time": decision.get("decision_time"),
            "source_decision_path": decision_path,
            "strategy_version": strategy_version,
            "action_reason": decision.get("action_reason"),
            "status": "RECORDED",
            "blocked_reason": None,
            "created_at": now_iso(),
        },
    )


def _mark_to_market_at_close(
    output_root: Path,
    account_id: str,
    symbols: list[str],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    bars_index: dict[str, dict[str, dict[str, Any]]],
    trade_date: str,
) -> None:
    accounts = load_accounts(output_root)
    positions = load_positions(output_root)
    account = accounts[account_id]
    account_positions = ensure_account_positions(positions, account_id)

    for symbol in symbols:
        bar = bars_index.get(symbol, {}).get(trade_date)
        if not bar:
            continue
        _write_close_stock_json(output_root, symbol, bars_by_symbol[symbol], trade_date)
        position = account_positions.get(symbol)
        if position and position.get("position_status") != "CLOSED":
            position["market_price"] = float(bar["close"])
            position["quote_source"] = "historical_daily_bar.close"
            position["quote_time"] = None

    recompute_account_totals(account, account_positions)
    save_accounts(output_root, accounts)
    save_positions(output_root, positions)


def _write_close_stock_json(output_root: Path, symbol: str, bars: list[dict[str, Any]], trade_date: str) -> None:
    for index, bar in enumerate(bars):
        if bar["trade_date"] != trade_date:
            continue
        features = features_until(bars, index)
        stock_json = {
            "schema_version": "replay_close_stock_json.v0.1",
            "replay_mode": "REPLAY_LITE",
            "quote": {
                "code": symbol,
                "name": bar.get("name") or symbol,
                "market": bar.get("exchange") or bar.get("market") or ("SH" if symbol.startswith("6") else "SZ"),
                "trade_date": trade_date,
                "price": bar.get("close"),
                "open": bar.get("open"),
                "high": bar.get("high"),
                "low": bar.get("low"),
                "close": bar.get("close"),
                "pct_change": bar.get("pct_change"),
            },
            "technical": features,
            "valuation": {},
            "financial": {},
            "announcements": {},
            "engine_flags": {},
        }
        write_json(output_root / "stock_json" / f"stock_data_{symbol}.json", stock_json)
        return


def _create_replay_snapshot(output_root: Path, account_id: str, trade_date: str) -> dict[str, Any]:
    from .paper_trading import create_snapshot

    return create_snapshot(argparse.Namespace(output_dir=str(output_root), account=account_id, trade_date=trade_date))


def _daily_record(
    replay_id: str,
    trade_date: str,
    symbols: list[str],
    rollover_result: dict[str, Any],
    sell_results: dict[str, Any],
    buy_results: dict[str, Any],
    snapshot_result: dict[str, Any],
) -> dict[str, Any]:
    account_snapshot = snapshot_result["account_snapshot"]
    blocked = list(sell_results.get("blocked") or []) + list(buy_results.get("blocked") or [])
    return {
        "replay_id": replay_id,
        "trade_date": trade_date,
        "symbols_scanned": len(symbols),
        "released_quantity": rollover_result.get("released_quantity"),
        "holding_decisions": sell_results.get("decisions"),
        "buy_decisions": buy_results.get("decisions"),
        "risk_checks": buy_results.get("risk_checks"),
        "ready_intents": buy_results.get("ready_intents"),
        "orders": int(sell_results.get("orders") or 0) + int(buy_results.get("orders") or 0),
        "trades": int(sell_results.get("trades") or 0) + int(buy_results.get("trades") or 0),
        "account_snapshot_id": account_snapshot.get("snapshot_id"),
        "total_assets": account_snapshot.get("total_assets"),
        "available_cash": account_snapshot.get("available_cash"),
        "market_value": account_snapshot.get("market_value"),
        "daily_pnl": account_snapshot.get("daily_pnl"),
        "blocked_reasons": blocked,
        "allocation_status": buy_results.get("allocation_status"),
        "created_at": now_iso(),
    }


def _benchmark_return_pct(symbol: str, bars_index: dict[str, dict[str, dict[str, Any]]], dates: list[str]) -> float | None:
    first = bars_index.get(symbol, {}).get(dates[0])
    last = bars_index.get(symbol, {}).get(dates[-1])
    if not first or not last or not first.get("open") or not last.get("close"):
        return None
    return round((float(last["close"]) / float(first["open"]) - 1) * 100, 4)


def _error_case(decision: dict[str, Any], reason: str, case_type: str) -> dict[str, Any]:
    return {
        "error_case_id": f"err_{compact_now()}",
        "symbol": decision.get("symbol"),
        "trade_date": (decision.get("time_context") or {}).get("trade_date"),
        "decision_id": decision.get("decision_id"),
        "case_type": case_type,
        "suspected_cause": reason,
        "created_at": now_iso(),
    }


def _write_replay_report(
    output_root: Path,
    replay_id: str,
    args: argparse.Namespace,
    performance: dict[str, Any],
    daily_records: list[dict[str, Any]],
) -> Path:
    path = output_root / "replay_report.md"
    lines = [
        f"# 历史回放报告 {replay_id}",
        "",
        "## 回放配置",
        f"- 股票：{args.symbols}",
        f"- 区间：{args.start} 至 {args.end}",
        f"- 初始资金：{args.cash}",
        f"- 模式：`REPLAY_LITE`",
        f"- 执行：`D 盘前信号，D 开盘成交`",
        f"- 策略版本：`{args.strategy_version}`",
        "",
        "## 绩效摘要",
        f"- 期末资产：{performance.get('final_assets')}",
        f"- 总收益率：{performance.get('total_return_pct')}%",
        f"- 年化收益率：{performance.get('annualized_return_pct')}%",
        f"- 最大回撤：{performance.get('max_drawdown_pct')}%",
        f"- 交易次数：{performance.get('trade_count')}",
        f"- 买入次数：{performance.get('buy_count')}",
        f"- 卖出次数：{performance.get('sell_count')}",
        f"- 胜率：{performance.get('win_rate')}%",
        f"- 盈亏比：{performance.get('profit_loss_ratio')}",
        f"- 基准收益：{performance.get('benchmark_return_pct')}%",
        f"- 超额收益：{performance.get('excess_return_pct')}%",
        "",
        "## 每日摘要",
    ]
    for record in daily_records[-20:]:
        lines.append(
            "- "
            f"{record.get('trade_date')} "
            f"资产={record.get('total_assets')} "
            f"交易={record.get('trades')} "
            f"候选={record.get('ready_intents')} "
            f"阻塞={record.get('blocked_reasons')}"
        )
    lines.extend(
        [
            "",
            "## 重要说明",
            "- 第一版 `REPLAY_LITE` 只验证价格、技术、仓位、T+1、成本和执行链路。",
            "- 没有点时财报和公告时，不能证明完整基本面策略有效。",
            "- 样本数量不足时，结果只用于发现问题，不用于证明策略有效。",
            "- 回放结果不构成投资建议。",
            f"- 生成时间：{now_iso()}",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_command(args: argparse.Namespace) -> None:
    result = run_replay(args)
    perf = result["performance"]
    print(f"replay: {result['replay_id']}")
    print(f"output_root: {result['output_root']}")
    print(f"daily_count: {result['daily_count']}")
    print(f"final_assets: {perf.get('final_assets')}")
    print(f"total_return_pct: {perf.get('total_return_pct')}")
    print(f"max_drawdown_pct: {perf.get('max_drawdown_pct')}")
    print(f"trade_count: {perf.get('trade_count')}")
    print(f"report: {result['report_path']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run REPLAY_LITE historical replay.")
    parser.add_argument("--output-dir", default="data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run a historical replay.")
    run.add_argument("--replay-id")
    run.add_argument("--account", default="replay_default")
    run.add_argument("--symbols", required=True)
    run.add_argument("--start", required=True)
    run.add_argument("--end", required=True)
    run.add_argument("--cash", type=float, required=True)
    run.add_argument("--mode", choices=["lite"], default="lite")
    run.add_argument("--bar-dir", default="data/historical/daily_bars")
    run.add_argument("--strategy-version", default="strategy_v0.1")
    run.add_argument("--max-candidates", type=int)
    run.add_argument("--default-watch-cash", type=float, default=5000.0)
    run.add_argument("--cash-reserve-pct", type=float, default=20.0)
    run.add_argument("--max-single-position-pct", type=float, default=30.0)
    run.add_argument("--max-daily-buy-amount", type=float, default=30000.0)
    run.set_defaults(func=run_command)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
