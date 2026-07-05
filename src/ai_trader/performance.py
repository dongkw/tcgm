"""Performance metrics for paper trading and historical replay."""

from __future__ import annotations

from math import prod
from pathlib import Path
from typing import Any

from .file_store import read_jsonl
from .portfolio import account_snapshots_path, closed_positions_path


def calculate_replay_performance(
    output_root: Path,
    *,
    account_id: str,
    initial_cash: float,
    benchmark_return_pct: float | None = None,
) -> dict[str, Any]:
    snapshots = [
        item
        for item in read_jsonl(account_snapshots_path(output_root))
        if item.get("account_id") == account_id
    ]
    trades = [
        item
        for item in read_jsonl(output_root / "paper_trading" / "trades.jsonl")
        if item.get("account_id") == account_id
    ]
    closed_positions = [
        item
        for item in read_jsonl(closed_positions_path(output_root))
        if item.get("account_id") == account_id
    ]

    snapshots.sort(key=lambda item: item.get("trade_date") or "")
    final_assets = float(snapshots[-1].get("total_assets") or initial_cash) if snapshots else initial_cash
    total_return_pct = round((final_assets / initial_cash - 1) * 100, 4) if initial_cash > 0 else None
    max_drawdown = _max_drawdown(snapshots)
    daily_returns = [
        float(item.get("daily_return_pct") or 0.0) / 100
        for item in snapshots
        if item.get("daily_return_pct") is not None
    ]
    annualized_return_pct = _annualized_return(total_return_pct, len(snapshots))
    win_loss = _win_loss_metrics(closed_positions)
    average_cash_pct = _average([float(item.get("cash_pct") or 0.0) for item in snapshots])

    return {
        "start_date": snapshots[0].get("trade_date") if snapshots else None,
        "end_date": snapshots[-1].get("trade_date") if snapshots else None,
        "initial_cash": round(initial_cash, 2),
        "final_assets": round(final_assets, 2),
        "total_return_pct": total_return_pct,
        "annualized_return_pct": annualized_return_pct,
        "max_drawdown_pct": max_drawdown["max_drawdown_pct"],
        "max_drawdown_start": max_drawdown["start"],
        "max_drawdown_end": max_drawdown["end"],
        "trade_count": len(trades),
        "buy_count": len([trade for trade in trades if trade.get("side") == "BUY"]),
        "sell_count": len([trade for trade in trades if trade.get("side") == "SELL"]),
        "win_rate": win_loss["win_rate"],
        "profit_loss_ratio": win_loss["profit_loss_ratio"],
        "average_win_pct": win_loss["average_win_pct"],
        "average_loss_pct": win_loss["average_loss_pct"],
        "largest_win_pct": win_loss["largest_win_pct"],
        "largest_loss_pct": win_loss["largest_loss_pct"],
        "average_holding_days": None,
        "cash_usage_pct": round(100 - average_cash_pct, 4) if average_cash_pct is not None else None,
        "turnover_rate": _turnover_rate(trades, snapshots),
        "benchmark_return_pct": benchmark_return_pct,
        "excess_return_pct": (
            round(total_return_pct - benchmark_return_pct, 4)
            if total_return_pct is not None and benchmark_return_pct is not None
            else None
        ),
        "daily_compound_return_pct": round((prod([1 + item for item in daily_returns]) - 1) * 100, 4)
        if daily_returns
        else None,
    }


def _max_drawdown(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    peak = None
    peak_date = None
    max_drawdown_pct = 0.0
    max_start = None
    max_end = None
    for snapshot in snapshots:
        total_assets = float(snapshot.get("total_assets") or 0.0)
        trade_date = snapshot.get("trade_date")
        if peak is None or total_assets > peak:
            peak = total_assets
            peak_date = trade_date
        if peak and peak > 0:
            drawdown = (total_assets / peak - 1) * 100
            if drawdown < max_drawdown_pct:
                max_drawdown_pct = drawdown
                max_start = peak_date
                max_end = trade_date
    return {
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "start": max_start,
        "end": max_end,
    }


def _annualized_return(total_return_pct: float | None, days: int) -> float | None:
    if total_return_pct is None or days <= 0:
        return None
    return round(((1 + total_return_pct / 100) ** (252 / days) - 1) * 100, 4)


def _win_loss_metrics(closed_positions: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(item.get("realized_pnl") or 0.0) for item in closed_positions]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    win_rate = round(len(wins) / len(pnls) * 100, 4) if pnls else None
    avg_win = _average(wins)
    avg_loss = _average(losses)
    profit_loss_ratio = round(avg_win / abs(avg_loss), 4) if avg_win is not None and avg_loss not in {None, 0} else None
    return {
        "win_rate": win_rate,
        "profit_loss_ratio": profit_loss_ratio,
        "average_win_pct": None,
        "average_loss_pct": None,
        "largest_win_pct": None,
        "largest_loss_pct": None,
    }


def _turnover_rate(trades: list[dict[str, Any]], snapshots: list[dict[str, Any]]) -> float | None:
    if not snapshots:
        return None
    total_gross = sum(float(trade.get("gross_amount") or 0.0) for trade in trades)
    average_assets = _average([float(item.get("total_assets") or 0.0) for item in snapshots])
    if not average_assets:
        return None
    return round(total_gross / average_assets * 100, 4)


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
