"""Historical daily bar loading and lite feature calculation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_BAR_FIELDS = ["symbol", "trade_date", "open", "high", "low", "close"]


def daily_bar_path(bar_dir: Path, symbol: str) -> Path:
    return bar_dir / f"{symbol}.jsonl"


def load_daily_bars(symbol: str, bar_dir: Path) -> list[dict[str, Any]]:
    path = daily_bar_path(bar_dir, symbol)
    if not path.exists():
        raise FileNotFoundError(f"historical daily bars not found: {path}")

    bars: list[dict[str, Any]] = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        bar = json.loads(line)
        _validate_bar(bar, path, line_no)
        bars.append(_normalize_bar(bar, symbol))

    bars.sort(key=lambda item: item["trade_date"])
    return bars


def load_bars_by_symbol(symbols: list[str], bar_dir: Path) -> dict[str, list[dict[str, Any]]]:
    return {symbol: load_daily_bars(symbol, bar_dir) for symbol in symbols}


def _validate_bar(bar: dict[str, Any], path: Path, line_no: int) -> None:
    missing = [field for field in REQUIRED_BAR_FIELDS if bar.get(field) in {None, ""}]
    if missing:
        raise ValueError(f"{path}:{line_no} missing required fields: {missing}")
    for field in ["open", "high", "low", "close"]:
        value = float(bar[field])
        if value <= 0:
            raise ValueError(f"{path}:{line_no} invalid {field}: {value}")


def _normalize_bar(bar: dict[str, Any], fallback_symbol: str) -> dict[str, Any]:
    normalized = dict(bar)
    normalized["symbol"] = str(bar.get("symbol") or fallback_symbol)
    normalized["trade_date"] = str(bar["trade_date"])
    for field in ["open", "high", "low", "close", "pre_close", "volume", "amount", "pct_change", "adj_factor"]:
        if normalized.get(field) not in {None, ""}:
            normalized[field] = float(normalized[field])
    normalized["is_suspended"] = bool(normalized.get("is_suspended", False))
    normalized["is_limit_up"] = bool(normalized.get("is_limit_up", False))
    normalized["is_limit_down"] = bool(normalized.get("is_limit_down", False))
    return normalized


def trade_dates_from_bars(bars_by_symbol: dict[str, list[dict[str, Any]]], start_date: str, end_date: str) -> list[str]:
    if not bars_by_symbol:
        return []
    common_dates: set[str] | None = None
    for bars in bars_by_symbol.values():
        dates = {bar["trade_date"] for bar in bars if start_date <= bar["trade_date"] <= end_date}
        common_dates = dates if common_dates is None else common_dates & dates
    return sorted(common_dates or [])


def bar_by_date(bars: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {bar["trade_date"]: bar for bar in bars}


def previous_bar(bars: list[dict[str, Any]], trade_date: str) -> tuple[int | None, dict[str, Any] | None]:
    candidate: tuple[int | None, dict[str, Any] | None] = (None, None)
    for index, bar in enumerate(bars):
        if bar["trade_date"] >= trade_date:
            break
        candidate = (index, bar)
    return candidate


def features_until(bars: list[dict[str, Any]], source_index: int) -> dict[str, Any]:
    visible = bars[: source_index + 1]
    closes = [float(bar["close"]) for bar in visible]
    highs = [float(bar["high"]) for bar in visible]
    lows = [float(bar["low"]) for bar in visible]

    ma20 = _moving_average(closes, 20)
    ma60 = _moving_average(closes, 60)
    previous_ma20 = _moving_average(closes[:-1], 20)
    previous_ma60 = _moving_average(closes[:-1], 60)
    high_20d = round(max(highs[-20:]), 4) if len(highs) >= 20 else None
    low_20d = round(min(lows[-20:]), 4) if len(lows) >= 20 else None
    change_20d_pct = _change_pct(closes, 20)
    change_60d_pct = _change_pct(closes, 60)

    return {
        "ma20": ma20,
        "ma60": ma60,
        "above_ma20": closes[-1] > ma20 if ma20 is not None else None,
        "above_ma60": closes[-1] > ma60 if ma60 is not None else None,
        "ma20_slope_up": ma20 > previous_ma20 if ma20 is not None and previous_ma20 is not None else None,
        "ma60_slope_up": ma60 > previous_ma60 if ma60 is not None and previous_ma60 is not None else None,
        "high_20d": high_20d,
        "low_20d": low_20d,
        "change_20d_pct": change_20d_pct,
        "change_60d_pct": change_60d_pct,
        "atr14_pct": _atr14_pct(visible),
        "source_bar_end_date": visible[-1]["trade_date"],
    }


def _moving_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return round(sum(values[-window:]) / window, 4)


def _change_pct(values: list[float], window: int) -> float | None:
    if len(values) <= window:
        return None
    base = values[-window - 1]
    if base == 0:
        return None
    return round((values[-1] / base - 1) * 100, 4)


def _atr14_pct(bars: list[dict[str, Any]]) -> float | None:
    if len(bars) < 15:
        return None
    true_ranges: list[float] = []
    recent = bars[-14:]
    previous_close = float(bars[-15]["close"])
    for bar in recent:
        high = float(bar["high"])
        low = float(bar["low"])
        true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
        true_ranges.append(true_range)
        previous_close = float(bar["close"])
    close = float(bars[-1]["close"])
    if close <= 0:
        return None
    return round(sum(true_ranges) / len(true_ranges) / close * 100, 4)
