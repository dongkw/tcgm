"""Intraday trigger scanning for manual workflow runs."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .db.sync import mirror_record, mirror_report
from .decision_utils import ensure_dir
from .file_store import append_jsonl, load_json, read_jsonl
from .portfolio import compact_now, ensure_account_positions, load_accounts, load_positions, now_iso
from .timekeeper import build_time_context


TRADE_SESSIONS = {"CALL_AUCTION", "MORNING_TRADE", "AFTERNOON_TRADE"}
TRIGGER_EVENT_STATUSES = {"SUCCESS", "NO_TRIGGER", "BLOCKED"}


def intraday_dir(output_root: Path) -> Path:
    return output_root / "intraday"


def trigger_events_path(output_root: Path) -> Path:
    return intraday_dir(output_root) / "trigger_events.jsonl"


def intraday_scans_path(output_root: Path) -> Path:
    return intraday_dir(output_root) / "intraday_scans.jsonl"


def reports_dir(output_root: Path) -> Path:
    return output_root / "reports"


def default_plan_path(output_root: Path, account_id: str, trade_date: str) -> Path:
    return output_root / "workflows" / f"pre_market_plan_{account_id}_{trade_date}.json"


def default_trigger_path(output_root: Path, account_id: str, trade_date: str) -> Path:
    return output_root / "workflows" / f"trigger_price_list_{account_id}_{trade_date}.json"


def _split_symbols(value: str | None) -> list[str]:
    if not value:
        return []
    symbols: list[str] = []
    for item in value.replace("\n", ",").split(","):
        symbol = item.strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _load_required_json(path: Path, label: str) -> dict[str, Any]:
    data = load_json(path, None)
    if data is None:
        raise FileNotFoundError(f"{label} not found: {path}")
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return data


def _load_quote(data_dir: Path, symbol: str) -> dict[str, Any]:
    path = data_dir / f"stock_data_{symbol}.json"
    if not path.exists():
        return {
            "symbol": symbol,
            "name": None,
            "path": str(path),
            "price": None,
            "trade_date": None,
            "quote_time": None,
            "price_source": "stock_json",
            "status": "MISSING",
            "warnings": [f"stock json missing: {path}"],
        }

    raw = load_json(path, {})
    quote = raw.get("quote") or {}
    technical = raw.get("technical") or {}
    meta = raw.get("meta") or {}

    quote_price = _as_float(quote.get("price"))
    technical_close = _as_float(technical.get("close"))
    current_price = quote_price if quote_price is not None else technical_close
    price_source = "stock_json.quote.price" if quote_price is not None else "stock_json.technical.close"
    trade_date = quote.get("trade_date") or technical.get("last_date")
    previous_close = _as_float(quote.get("previous_close"))
    trading_status = quote.get("trading_status")
    warnings: list[str] = []

    if current_price is None or current_price <= 0:
        warnings.append("PRICE_MISSING")
    if not trade_date:
        warnings.append("QUOTE_TRADE_DATE_MISSING")

    quote_time = quote.get("quote_time") or quote.get("time") or meta.get("generated_at")
    data_time_precision = "NORMAL" if quote.get("quote_time") else "LOW"
    if not quote.get("quote_time"):
        warnings.append("QUOTE_TIME_LOW_PRECISION")

    is_suspended = bool(quote.get("is_suspended")) or str(trading_status or "").find("停牌") >= 0
    explicit_limit_up = quote.get("is_limit_up")
    explicit_limit_down = quote.get("is_limit_down")
    is_limit_up = bool(explicit_limit_up) if explicit_limit_up is not None else False
    is_limit_down = bool(explicit_limit_down) if explicit_limit_down is not None else False
    near_limit_up = False
    near_limit_down = False

    if current_price is not None and previous_close and previous_close > 0:
        up_ratio = current_price / previous_close
        near_limit_up = up_ratio >= 1.095
        near_limit_down = up_ratio <= 0.905
        is_limit_up = is_limit_up or up_ratio >= 1.099
        is_limit_down = is_limit_down or up_ratio <= 0.901
        if up_ratio > 1.215 or up_ratio < 0.785:
            warnings.append("PRICE_ABNORMAL")

    return {
        "symbol": quote.get("code") or symbol,
        "name": quote.get("name"),
        "path": str(path),
        "price": current_price,
        "trade_date": trade_date,
        "quote_time": quote_time,
        "data_time_precision": data_time_precision,
        "price_source": price_source,
        "previous_close": previous_close,
        "trading_status": trading_status,
        "is_suspended": is_suspended,
        "is_limit_up": is_limit_up,
        "is_limit_down": is_limit_down,
        "near_limit_up": near_limit_up,
        "near_limit_down": near_limit_down,
        "status": "SUCCESS",
        "warnings": warnings,
    }


def _resolve_symbols(
    plan: dict[str, Any],
    trigger_list: dict[str, Any],
    positions: dict[str, Any],
    account_id: str,
    requested_symbols: str | None,
) -> list[str]:
    requested = _split_symbols(requested_symbols)
    if requested:
        return requested

    symbols: list[str] = []
    for item in trigger_list.get("items") or []:
        symbol = item.get("symbol")
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    for item in plan.get("order_intents") or []:
        symbol = item.get("symbol")
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    for symbol, position in ensure_account_positions(positions, account_id).items():
        if position.get("position_status") != "CLOSED" and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _items_by_symbol(trigger_list: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in trigger_list.get("items") or []:
        symbol = item.get("symbol")
        if symbol:
            grouped.setdefault(symbol, []).append(item)
    return grouped


def _ready_buy_intents_by_symbol(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ready: dict[str, dict[str, Any]] = {}
    for intent in plan.get("order_intents") or []:
        if intent.get("status") != "READY_FOR_CONFIRM":
            continue
        if intent.get("side") not in {None, "BUY"}:
            continue
        symbol = intent.get("symbol")
        if symbol and symbol not in ready:
            ready[symbol] = intent
    return ready


def _existing_event_keys(output_root: Path) -> set[tuple[str, str, str, str, str | None]]:
    keys: set[tuple[str, str, str, str, str | None]] = set()
    for record in read_jsonl(trigger_events_path(output_root)):
        keys.add(
            (
                record.get("account_id"),
                record.get("trade_date"),
                record.get("symbol"),
                record.get("event_type"),
                record.get("decision_id"),
            )
        )
    return keys


def _event_key(event: dict[str, Any]) -> tuple[str, str, str, str, str | None]:
    return (
        event.get("account_id"),
        event.get("trade_date"),
        event.get("symbol"),
        event.get("event_type"),
        event.get("decision_id"),
    )


def _base_event(
    *,
    account_id: str,
    trade_date: str,
    scan_id: str,
    symbol: str,
    quote: dict[str, Any],
    item: dict[str, Any] | None,
    event_type: str,
    trigger_price: float | None,
    suggested_action: str,
    severity: str,
    risk_status: str,
    blocked_reason: str | None,
    plan_path: Path,
    execution_allowed: bool = False,
) -> dict[str, Any]:
    event_time = compact_now()
    return {
        "trigger_event_id": f"trig_{event_time}_{symbol}_{event_type.lower()}",
        "account_id": account_id,
        "trade_date": trade_date,
        "scan_id": scan_id,
        "symbol": symbol,
        "name": quote.get("name") or (item or {}).get("name"),
        "event_type": event_type,
        "trigger_price": trigger_price,
        "current_price": quote.get("price"),
        "price_source": quote.get("price_source"),
        "quote_trade_date": quote.get("trade_date"),
        "quote_time": quote.get("quote_time"),
        "data_time_precision": quote.get("data_time_precision"),
        "decision_id": (item or {}).get("decision_id"),
        "snapshot_id": (item or {}).get("snapshot_id"),
        "source_decision_path": (item or {}).get("source_decision_path"),
        "source_plan_path": str(plan_path),
        "severity": severity,
        "suggested_action": suggested_action,
        "execution_allowed": execution_allowed,
        "requires_human_confirm": True,
        "risk_status": risk_status,
        "blocked_reason": blocked_reason,
        "created_at": now_iso(),
    }


def _quote_block_event(
    *,
    account_id: str,
    trade_date: str,
    scan_id: str,
    symbol: str,
    quote: dict[str, Any],
    item: dict[str, Any] | None,
    plan_path: Path,
    reason: str,
) -> dict[str, Any]:
    event_type = "PRICE_TIME_MISMATCH" if reason == "PRICE_TIME_MISMATCH" else reason
    return _base_event(
        account_id=account_id,
        trade_date=trade_date,
        scan_id=scan_id,
        symbol=symbol,
        quote=quote,
        item=item,
        event_type=event_type,
        trigger_price=None,
        suggested_action="BLOCK_TRIGGER",
        severity="HIGH",
        risk_status="BLOCK_REMIND",
        blocked_reason=reason,
        plan_path=plan_path,
    )


def _price_triggered(current_price: float | None, trigger_price: Any, direction: str) -> bool:
    target = _as_float(trigger_price)
    if current_price is None or target is None:
        return False
    if direction == "gte":
        return current_price >= target
    if direction == "lte":
        return current_price <= target
    if direction == "lt":
        return current_price < target
    raise ValueError(f"unsupported direction: {direction}")


def _sell_blocked_reason(position: dict[str, Any] | None, quote: dict[str, Any]) -> str | None:
    if not position or position.get("position_status") == "CLOSED":
        return "NO_POSITION"
    if _as_int(position.get("available_quantity")) <= 0:
        return "NO_AVAILABLE_QUANTITY"
    if quote.get("is_suspended"):
        return "SUSPENDED"
    if quote.get("is_limit_down"):
        return "LIMIT_DOWN_RISK"
    return None


def _buy_blocked_reason(
    account: dict[str, Any],
    position: dict[str, Any] | None,
    quote: dict[str, Any],
    intent: dict[str, Any] | None,
) -> str | None:
    if not intent:
        return "NO_READY_BUY_INTENT"
    if quote.get("is_suspended"):
        return "SUSPENDED"
    if quote.get("is_limit_up"):
        return "LIMIT_UP_RISK"
    if _as_float(account.get("available_cash")) is not None and float(account.get("available_cash") or 0.0) <= 0:
        return "NO_AVAILABLE_CASH"
    if position and position.get("position_status") != "CLOSED" and _as_int(position.get("total_quantity")) > 0:
        return "ALREADY_HOLDING"
    return None


def _build_buy_events(
    *,
    account_id: str,
    trade_date: str,
    scan_id: str,
    symbol: str,
    account: dict[str, Any],
    position: dict[str, Any] | None,
    quote: dict[str, Any],
    item: dict[str, Any],
    intent: dict[str, Any] | None,
    plan_path: Path,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    triggers = item.get("trigger_prices") or {}
    current_price = quote.get("price")
    if _price_triggered(current_price, triggers.get("resistance_price"), "gte"):
        blocked_reason = _buy_blocked_reason(account, position, quote, intent)
        risk_status = "BLOCK_REMIND" if blocked_reason else "PASS_REMIND"
        events.append(
            _base_event(
                account_id=account_id,
                trade_date=trade_date,
                scan_id=scan_id,
                symbol=symbol,
                quote=quote,
                item=item,
                event_type="BUY_BREAKOUT",
                trigger_price=_as_float(triggers.get("resistance_price")),
                suggested_action="REMIND_BUY_CONFIRM",
                severity="INFO" if not blocked_reason else "WARN",
                risk_status=risk_status,
                blocked_reason=blocked_reason,
                plan_path=plan_path,
            )
        )
    if quote.get("near_limit_up") or quote.get("is_limit_up"):
        events.append(
            _base_event(
                account_id=account_id,
                trade_date=trade_date,
                scan_id=scan_id,
                symbol=symbol,
                quote=quote,
                item=item,
                event_type="LIMIT_UP_RISK",
                trigger_price=None,
                suggested_action="REMIND_BUY_RISK_REVIEW",
                severity="WARN",
                risk_status="WARN_REMIND",
                blocked_reason="LIMIT_UP_RISK" if quote.get("is_limit_up") else None,
                plan_path=plan_path,
            )
        )
    return events


def _build_sell_events(
    *,
    account_id: str,
    trade_date: str,
    scan_id: str,
    symbol: str,
    position: dict[str, Any] | None,
    quote: dict[str, Any],
    item: dict[str, Any],
    plan_path: Path,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    triggers = item.get("trigger_prices") or {}
    current_price = quote.get("price")
    checks = [
        ("SELL_STOP_LOSS", "stop_loss_price", "lte", "REMIND_SELL_CONFIRM", "HIGH"),
        ("SELL_BREAK_MA20", "reduce_trigger_price", "lt", "REMIND_REDUCE_CONFIRM", "MEDIUM"),
        ("SELL_BREAK_MA60", "middle_trend_price", "lt", "REMIND_RISK_REVIEW", "MEDIUM"),
        ("SELL_BREAK_LOW20", "clear_trigger_price", "lt", "REMIND_CLEAR_CONFIRM", "HIGH"),
    ]
    for event_type, trigger_key, direction, suggested_action, severity in checks:
        if not _price_triggered(current_price, triggers.get(trigger_key), direction):
            continue
        blocked_reason = _sell_blocked_reason(position, quote)
        risk_status = "BLOCK_REMIND" if blocked_reason else "PASS_REMIND"
        events.append(
            _base_event(
                account_id=account_id,
                trade_date=trade_date,
                scan_id=scan_id,
                symbol=symbol,
                quote=quote,
                item=item,
                event_type=event_type,
                trigger_price=_as_float(triggers.get(trigger_key)),
                suggested_action=suggested_action,
                severity=severity,
                risk_status=risk_status,
                blocked_reason=blocked_reason,
                plan_path=plan_path,
            )
        )
    if quote.get("near_limit_down") or quote.get("is_limit_down"):
        events.append(
            _base_event(
                account_id=account_id,
                trade_date=trade_date,
                scan_id=scan_id,
                symbol=symbol,
                quote=quote,
                item=item,
                event_type="LIMIT_DOWN_RISK",
                trigger_price=None,
                suggested_action="REMIND_SELL_RISK_REVIEW",
                severity="HIGH",
                risk_status="WARN_REMIND",
                blocked_reason="LIMIT_DOWN_RISK" if quote.get("is_limit_down") else None,
                plan_path=plan_path,
            )
        )
    return events


def _should_scan_buy(item: dict[str, Any]) -> bool:
    return item.get("task") == "buy" or item.get("task_type") == "BUY_EVALUATION"


def _should_scan_sell(item: dict[str, Any], position: dict[str, Any] | None) -> bool:
    if position and position.get("position_status") != "CLOSED" and _as_int(position.get("total_quantity")) > 0:
        return True
    return item.get("task") == "sell" or item.get("task_type") == "HOLDING_REVIEW"


def _scan_symbol(
    *,
    account_id: str,
    trade_date: str,
    scan_id: str,
    account: dict[str, Any],
    positions: dict[str, Any],
    plan_path: Path,
    symbol: str,
    quote: dict[str, Any],
    trigger_items: list[dict[str, Any]],
    ready_buy_intent: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    account_positions = ensure_account_positions(positions, account_id)
    position = account_positions.get(symbol)

    if quote.get("status") == "MISSING":
        return [
            _quote_block_event(
                account_id=account_id,
                trade_date=trade_date,
                scan_id=scan_id,
                symbol=symbol,
                quote=quote,
                item=trigger_items[0] if trigger_items else None,
                plan_path=plan_path,
                reason="PRICE_MISSING",
            )
        ]

    if quote.get("trade_date") != trade_date:
        return [
            _quote_block_event(
                account_id=account_id,
                trade_date=trade_date,
                scan_id=scan_id,
                symbol=symbol,
                quote=quote,
                item=trigger_items[0] if trigger_items else None,
                plan_path=plan_path,
                reason="PRICE_TIME_MISMATCH",
            )
        ]

    if quote.get("price") is None or quote.get("price") <= 0:
        return [
            _quote_block_event(
                account_id=account_id,
                trade_date=trade_date,
                scan_id=scan_id,
                symbol=symbol,
                quote=quote,
                item=trigger_items[0] if trigger_items else None,
                plan_path=plan_path,
                reason="PRICE_MISSING",
            )
        ]

    events: list[dict[str, Any]] = []
    for item in trigger_items:
        if _should_scan_buy(item):
            events.extend(
                _build_buy_events(
                    account_id=account_id,
                    trade_date=trade_date,
                    scan_id=scan_id,
                    symbol=symbol,
                    account=account,
                    position=position,
                    quote=quote,
                    item=item,
                    intent=ready_buy_intent,
                    plan_path=plan_path,
                )
            )
        if _should_scan_sell(item, position):
            events.extend(
                _build_sell_events(
                    account_id=account_id,
                    trade_date=trade_date,
                    scan_id=scan_id,
                    symbol=symbol,
                    position=position,
                    quote=quote,
                    item=item,
                    plan_path=plan_path,
                )
            )
    return events


def _write_report(
    *,
    output_root: Path,
    account_id: str,
    trade_date: str,
    scan: dict[str, Any],
    events: list[dict[str, Any]],
    duplicate_events: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    blocked_reason: str | None = None,
) -> Path:
    ensure_dir(reports_dir(output_root))
    path = reports_dir(output_root) / f"intraday_report_{account_id}_{trade_date}.md"
    buy_events = [event for event in events if event.get("event_type", "").startswith("BUY")]
    sell_events = [event for event in events if event.get("event_type", "").startswith("SELL")]
    risk_events = [
        event
        for event in events
        if event.get("event_type") in {"LIMIT_UP_RISK", "LIMIT_DOWN_RISK", "PRICE_TIME_MISMATCH", "PRICE_MISSING"}
    ]
    blocked_events = [event for event in events if event.get("risk_status") == "BLOCK_REMIND"]

    lines = [
        f"# 盘中触发扫描报告 {account_id} {trade_date}",
        "",
        "## 运行摘要",
        f"- 扫描：`{scan.get('scan_id')}`",
        f"- 状态：`{scan.get('status')}`",
        f"- 当前时段：`{scan.get('session_name')}`",
        f"- 是否交易日：{scan.get('is_trading_day')}",
        f"- 是否允许非盘中调试：{scan.get('allow_non_trading')}",
        f"- 扫描股票数：{scan.get('symbols_scanned')}",
        f"- 新触发事件数：{len(events)}",
        f"- 已存在重复事件数：{len(duplicate_events)}",
        f"- 阻塞事件数：{len(blocked_events)}",
        "- 执行权限：false",
    ]
    if blocked_reason:
        lines.append(f"- 扫描阻塞原因：`{blocked_reason}`")

    lines.extend(["", "## 买入触发"])
    if not buy_events:
        lines.append("- 无。")
    for event in buy_events:
        lines.append(
            "- "
            f"{event.get('symbol')} {event.get('name') or ''} "
            f"`{event.get('event_type')}` 当前价={event.get('current_price')} "
            f"触发价={event.get('trigger_price')} "
            f"风控={event.get('risk_status')} "
            f"阻塞={event.get('blocked_reason') or '无'}"
        )

    lines.extend(["", "## 卖出/持仓风险触发"])
    if not sell_events:
        lines.append("- 无。")
    for event in sell_events:
        lines.append(
            "- "
            f"{event.get('symbol')} {event.get('name') or ''} "
            f"`{event.get('event_type')}` 当前价={event.get('current_price')} "
            f"触发价={event.get('trigger_price')} "
            f"风控={event.get('risk_status')} "
            f"阻塞={event.get('blocked_reason') or '无'}"
        )

    lines.extend(["", "## 数据和涨跌停风险"])
    if not risk_events:
        lines.append("- 无。")
    for event in risk_events:
        lines.append(
            "- "
            f"{event.get('symbol')} `{event.get('event_type')}` "
            f"行情日期={event.get('quote_trade_date')} "
            f"当前价={event.get('current_price')} "
            f"阻塞={event.get('blocked_reason') or '无'}"
        )

    lines.extend(["", "## 已触发过但本次不重复写入"])
    if not duplicate_events:
        lines.append("- 无。")
    for event in duplicate_events:
        lines.append(
            "- "
            f"{event.get('symbol')} `{event.get('event_type')}` "
            f"decision_id={event.get('decision_id') or '无'}"
        )

    lines.extend(["", "## 行情时间检查"])
    for symbol, quote in quotes.items():
        warnings = ", ".join(quote.get("warnings") or []) or "无"
        lines.append(
            "- "
            f"{symbol} 行情日期={quote.get('trade_date')} "
            f"价格={quote.get('price')} "
            f"时间={quote.get('quote_time') or '未知'} "
            f"精度={quote.get('data_time_precision')} "
            f"提示={warnings}"
        )

    lines.extend(
        [
            "",
            "## 说明",
            "- 本报告是盘中提醒，不是成交记录。",
            "- 第一版盘中扫描不会修改账户现金、持仓、订单或成交。",
            "- 触发后仍需人工复核，真正模拟成交必须单独执行确认命令。",
            f"- 生成时间：{now_iso()}",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mirror_report(
        output_root,
        path,
        report_type="intraday",
        account_id=account_id,
        trade_date=trade_date,
        source_type="intraday_scan",
        source_id=scan.get("scan_id"),
    )
    return path


def _append_unique_events(output_root: Path, events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    existing_keys = _existing_event_keys(output_root)
    path = trigger_events_path(output_root)
    written: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for event in events:
        key = _event_key(event)
        if key in existing_keys:
            duplicates.append(event)
            continue
        append_jsonl(path, event)
        mirror_record(output_root, "trigger_event", event, source_path=path)
        existing_keys.add(key)
        written.append(event)
    return written, duplicates


def _append_scan(output_root: Path, scan: dict[str, Any]) -> None:
    path = intraday_scans_path(output_root)
    append_jsonl(path, scan)
    mirror_record(output_root, "intraday_scan", scan, source_path=path)


def _apply_time_block(events: list[dict[str, Any]], reason: str) -> None:
    for event in events:
        event["risk_status"] = "BLOCK_REMIND"
        event["blocked_reason"] = event.get("blocked_reason") or reason
        event["execution_allowed"] = False


def run_intraday_scan(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output_dir)
    account_id = args.account
    trade_date = args.trade_date
    plan_path = Path(args.plan_path) if getattr(args, "plan_path", None) else default_plan_path(output_root, account_id, trade_date)
    trigger_path = (
        Path(args.trigger_path)
        if getattr(args, "trigger_path", None)
        else default_trigger_path(output_root, account_id, trade_date)
    )
    data_dir = Path(args.data_dir)
    scan_id = f"scan_{compact_now()}"
    time_context = build_time_context({"quote": {"trade_date": trade_date, "market": "A_STOCK"}}, "INTRADAY_SCAN")
    started_at = now_iso()
    session_name = time_context.get("session_name")
    scan = {
        "scan_id": scan_id,
        "account_id": account_id,
        "trade_date": trade_date,
        "calendar_date": time_context.get("calendar_date"),
        "session_name": session_name,
        "is_trading_day": time_context.get("is_trading_day"),
        "allow_non_trading": bool(getattr(args, "allow_non_trading", False)),
        "time_warnings": time_context.get("time_warnings") or [],
        "started_at": started_at,
        "finished_at": None,
        "status": None,
        "symbols_scanned": 0,
        "trigger_count": 0,
        "blocked_count": 0,
        "duplicate_count": 0,
        "input_refs": {
            "pre_market_plan": str(plan_path),
            "trigger_price_list": str(trigger_path),
            "data_dir": str(data_dir),
        },
        "report_path": None,
        "error_code": None,
        "error_message": None,
    }

    try:
        plan = _load_required_json(plan_path, "pre-market plan")
        trigger_list = _load_required_json(trigger_path, "trigger price list")
        accounts = load_accounts(output_root)
        account = accounts.get(account_id)
        if not account:
            raise ValueError(f"account not found: {account_id}")
        positions = load_positions(output_root)

        blocked_reason = None
        if session_name not in TRADE_SESSIONS and not getattr(args, "allow_non_trading", False):
            blocked_reason = "NOT_INTRADAY_TRADE_SESSION"
            symbols = _resolve_symbols(plan, trigger_list, positions, account_id, args.symbols)
            quotes = {symbol: _load_quote(data_dir, symbol) for symbol in symbols}
            scan.update(
                {
                    "status": "BLOCKED",
                    "symbols_scanned": len(symbols),
                    "finished_at": now_iso(),
                    "error_code": blocked_reason,
                    "error_message": "current session is not an intraday trading session",
                }
            )
            report_path = _write_report(
                output_root=output_root,
                account_id=account_id,
                trade_date=trade_date,
                scan=scan,
                events=[],
                duplicate_events=[],
                quotes=quotes,
                blocked_reason=blocked_reason,
            )
            scan["report_path"] = str(report_path)
            _append_scan(output_root, scan)
            return {"scan": scan, "events": [], "duplicate_events": [], "report_path": str(report_path)}

        symbols = _resolve_symbols(plan, trigger_list, positions, account_id, args.symbols)
        grouped_items = _items_by_symbol(trigger_list)
        ready_buy_intents = _ready_buy_intents_by_symbol(plan)
        quotes: dict[str, dict[str, Any]] = {}
        candidate_events: list[dict[str, Any]] = []

        for symbol in symbols:
            quote = _load_quote(data_dir, symbol)
            quotes[symbol] = quote
            items = grouped_items.get(symbol) or []
            if not items:
                continue
            candidate_events.extend(
                _scan_symbol(
                    account_id=account_id,
                    trade_date=trade_date,
                    scan_id=scan_id,
                    account=account,
                    positions=positions,
                    plan_path=plan_path,
                    symbol=symbol,
                    quote=quote,
                    trigger_items=items,
                    ready_buy_intent=ready_buy_intents.get(symbol),
                )
            )

        if session_name not in TRADE_SESSIONS:
            _apply_time_block(candidate_events, "NOT_INTRADAY_TRADE_SESSION")

        written_events, duplicate_events = _append_unique_events(output_root, candidate_events)
        blocked_count = len([event for event in written_events if event.get("risk_status") == "BLOCK_REMIND"])
        status = "NO_TRIGGER" if not written_events and not duplicate_events else "SUCCESS"
        if status not in TRIGGER_EVENT_STATUSES:
            status = "SUCCESS"

        scan.update(
            {
                "status": status,
                "symbols_scanned": len(symbols),
                "trigger_count": len(written_events),
                "blocked_count": blocked_count,
                "duplicate_count": len(duplicate_events),
                "finished_at": now_iso(),
            }
        )
        report_path = _write_report(
            output_root=output_root,
            account_id=account_id,
            trade_date=trade_date,
            scan=scan,
            events=written_events,
            duplicate_events=duplicate_events,
            quotes=quotes,
        )
        scan["report_path"] = str(report_path)
        _append_scan(output_root, scan)
        return {"scan": scan, "events": written_events, "duplicate_events": duplicate_events, "report_path": str(report_path)}
    except Exception as exc:
        scan.update(
            {
                "status": "FAILED",
                "finished_at": now_iso(),
                "error_code": exc.__class__.__name__,
                "error_message": str(exc),
            }
        )
        _append_scan(output_root, scan)
        raise
