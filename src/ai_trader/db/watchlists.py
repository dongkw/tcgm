"""Database-backed watchlist helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..portfolio import now_iso
from .connection import connect, resolve_db_path


DEFAULT_TABS = [
    ("positions", "持仓", "system", 10, 1),
    ("all", "全部", "system", 20, 1),
    ("decisions", "已决策", "system", 30, 1),
    ("next_day_watch", "明日关注", "system", 40, 1),
]


def normalize_symbol(symbol: str) -> str:
    return "".join(ch for ch in str(symbol).strip() if ch.isdigit())[:6]


def ensure_default_tabs(conn: sqlite3.Connection) -> None:
    now = now_iso()
    for tab_id, name, tab_type, sort_order, is_default in DEFAULT_TABS:
        conn.execute(
            """
            INSERT INTO watchlist_tabs (
                tab_id, name, tab_type, sort_order, is_default, is_active, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(tab_id) DO UPDATE SET
                name=excluded.name,
                tab_type=excluded.tab_type,
                sort_order=excluded.sort_order,
                is_default=excluded.is_default,
                is_active=1,
                updated_at=excluded.updated_at
            """,
            (tab_id, name, tab_type, sort_order, is_default, now, now),
        )


def bootstrap_watchlist_database(output_root: Path, db_path: str | None = None) -> dict[str, Any]:
    resolved = resolve_db_path(output_root, db_path)
    stats = {"tabs": 0, "items": 0, "quotes": 0}
    with connect(resolved) as conn:
        ensure_default_tabs(conn)
        stats["tabs"] = conn.execute("SELECT COUNT(*) AS count FROM watchlist_tabs").fetchone()["count"]
        stats["items"] += sync_positions_tab(conn)
        stats["items"] += seed_all_tab_from_file(conn, output_root / "imports" / "watchlist_all.txt")
        stats["items"] += seed_all_tab_from_file(conn, output_root / "watchlist.txt")
        stats["quotes"] += seed_quotes_from_json_dir(conn, output_root / "stock_json")
        conn.commit()
    stats["db_path"] = str(resolved)
    return stats


def sync_positions_tab(conn: sqlite3.Connection) -> int:
    ensure_default_tabs(conn)
    now = now_iso()
    rows = conn.execute(
        """
        SELECT symbol, COALESCE(name, symbol) AS name
        FROM positions
        WHERE COALESCE(position_status, 'ACTIVE') != 'CLOSED'
        ORDER BY symbol
        """
    ).fetchall()
    count = 0
    active_symbols: set[str] = set()
    for row in rows:
        symbol = normalize_symbol(row["symbol"])
        if not symbol:
            continue
        active_symbols.add(symbol)
        count += upsert_watchlist_item(conn, "positions", symbol, row["name"], now=now)
        count += upsert_watchlist_item(conn, "all", symbol, row["name"], now=now)
    if active_symbols:
        placeholders = ",".join("?" for _ in active_symbols)
        conn.execute(
            f"DELETE FROM watchlist_items WHERE tab_id='positions' AND symbol NOT IN ({placeholders})",
            tuple(active_symbols),
        )
    else:
        conn.execute("DELETE FROM watchlist_items WHERE tab_id='positions'")
    return count


def seed_all_tab_from_file(conn: sqlite3.Connection, path: Path) -> int:
    if not path.exists():
        return 0
    ensure_default_tabs(conn)
    count = 0
    now = now_iso()
    for line in path.read_text(encoding="utf-8").splitlines():
        symbol = normalize_symbol(line)
        if symbol:
            count += upsert_watchlist_item(conn, "all", symbol, None, now=now)
    return count


def seed_quotes_from_json_dir(conn: sqlite3.Connection, json_dir: Path) -> int:
    if not json_dir.exists():
        return 0
    count = 0
    for path in json_dir.glob("stock_data_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        symbol = normalize_symbol(path.stem.replace("stock_data_", ""))
        if upsert_market_quote(conn, symbol, payload, source_path=str(path)):
            count += 1
    return count


def upsert_watchlist_item(
    conn: sqlite3.Connection,
    tab_id: str,
    symbol: str,
    name: str | None = None,
    *,
    now: str | None = None,
) -> int:
    normalized = normalize_symbol(symbol)
    if not normalized:
        return 0
    now = now or now_iso()
    quote = conn.execute("SELECT name FROM market_quotes WHERE symbol=?", (normalized,)).fetchone()
    resolved_name = name or (quote["name"] if quote else None)
    item_id = f"watchlist_{tab_id}_{normalized}"
    cursor = conn.execute(
        """
        INSERT INTO watchlist_items (
            item_id, tab_id, symbol, name, asset_type, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'stock', ?, ?)
        ON CONFLICT(tab_id, symbol) DO UPDATE SET
            name=COALESCE(excluded.name, watchlist_items.name),
            updated_at=excluded.updated_at
        """,
        (item_id, tab_id, normalized, resolved_name, now, now),
    )
    return cursor.rowcount


def create_watchlist_tab(conn: sqlite3.Connection, name: str) -> str:
    clean_name = str(name).strip()
    if not clean_name:
        raise ValueError("tab name is required")
    existing = conn.execute("SELECT tab_id FROM watchlist_tabs WHERE name=?", (clean_name,)).fetchone()
    if existing:
        return existing["tab_id"]
    now = now_iso()
    base = "tab_" + "".join(ch.lower() for ch in clean_name if ch.isalnum())[:24]
    tab_id = base or f"tab_{compact_id(now)}"
    suffix = 1
    while conn.execute("SELECT 1 FROM watchlist_tabs WHERE tab_id=?", (tab_id,)).fetchone():
        suffix += 1
        tab_id = f"{base}_{suffix}" if base else f"tab_{compact_id(now)}_{suffix}"
    sort_order = conn.execute("SELECT COALESCE(MAX(sort_order), 20) + 10 AS sort_order FROM watchlist_tabs").fetchone()[
        "sort_order"
    ]
    conn.execute(
        """
        INSERT INTO watchlist_tabs (tab_id, name, tab_type, sort_order, is_default, is_active, created_at, updated_at)
        VALUES (?, ?, 'user', ?, 0, 1, ?, ?)
        """,
        (tab_id, clean_name, sort_order, now, now),
    )
    return tab_id


def delete_watchlist_tab(conn: sqlite3.Connection, tab_id: str) -> bool:
    row = conn.execute("SELECT tab_type FROM watchlist_tabs WHERE tab_id=?", (tab_id,)).fetchone()
    if not row:
        return False
    if row["tab_type"] == "system":
        raise ValueError("system tab cannot be deleted")
    conn.execute("DELETE FROM watchlist_tabs WHERE tab_id=?", (tab_id,))
    return True


def remove_watchlist_item(conn: sqlite3.Connection, tab_id: str, symbol: str) -> bool:
    normalized = normalize_symbol(symbol)
    if not normalized:
        return False
    if tab_id == "positions":
        raise ValueError("positions tab is synced from current holdings")
    cursor = conn.execute("DELETE FROM watchlist_items WHERE tab_id=? AND symbol=?", (tab_id, normalized))
    return cursor.rowcount > 0


def compact_id(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())[-12:]


def upsert_market_quote(
    conn: sqlite3.Connection,
    symbol: str,
    payload: dict[str, Any],
    *,
    source_path: str | None = None,
) -> bool:
    normalized = normalize_symbol(symbol or (payload.get("quote") or {}).get("code") or "")
    if not normalized:
        return False
    quote = payload.get("quote") or {}
    technical = payload.get("technical") or {}
    valuation = payload.get("valuation") or {}
    meta = payload.get("meta") or {}
    now = now_iso()
    conn.execute(
        """
        INSERT INTO market_quotes (
            symbol, name, exchange, asset_type, trade_date, quote_time, price,
            pct_change, pe_ttm, pb, market_cap_yuan, ma20, ma60, change_20d_pct,
            source, source_path, updated_at, payload_json
        )
        VALUES (?, ?, ?, 'stock', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            name=excluded.name,
            exchange=excluded.exchange,
            trade_date=excluded.trade_date,
            quote_time=excluded.quote_time,
            price=excluded.price,
            pct_change=excluded.pct_change,
            pe_ttm=excluded.pe_ttm,
            pb=excluded.pb,
            market_cap_yuan=excluded.market_cap_yuan,
            ma20=excluded.ma20,
            ma60=excluded.ma60,
            change_20d_pct=excluded.change_20d_pct,
            source=excluded.source,
            source_path=excluded.source_path,
            updated_at=excluded.updated_at,
            payload_json=excluded.payload_json
        """,
        (
            normalized,
            quote.get("name"),
            quote.get("market"),
            quote.get("trade_date"),
            quote.get("quote_time") or meta.get("generated_at"),
            quote.get("price"),
            quote.get("pct_change"),
            first_present(valuation.get("pe_ttm"), quote.get("pe_ttm")),
            first_present(valuation.get("pb"), quote.get("pb")),
            quote.get("market_cap_yuan"),
            technical.get("ma20"),
            technical.get("ma60"),
            technical.get("change_20d_pct"),
            meta.get("source"),
            source_path,
            now,
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    conn.execute(
        """
        UPDATE watchlist_items
        SET name=COALESCE(name, ?), updated_at=?
        WHERE symbol=?
        """,
        (quote.get("name"), now, normalized),
    )
    return True


def sync_position_valuations(conn: sqlite3.Connection, symbols: list[str] | None = None) -> dict[str, Any]:
    normalized_symbols = [normalize_symbol(symbol) for symbol in (symbols or [])]
    normalized_symbols = [symbol for symbol in normalized_symbols if symbol]
    where = ""
    params: list[Any] = []
    if normalized_symbols:
        placeholders = ",".join("?" for _ in normalized_symbols)
        where = f"AND p.symbol IN ({placeholders})"
        params.extend(normalized_symbols)

    now = now_iso()
    rows = conn.execute(
        f"""
        SELECT p.account_id, p.symbol, p.total_quantity, p.avg_cost, q.price
        FROM positions p
        JOIN market_quotes q ON q.symbol = p.symbol
        WHERE COALESCE(p.position_status, 'ACTIVE') != 'CLOSED'
          AND COALESCE(p.total_quantity, 0) > 0
          AND q.price IS NOT NULL
          {where}
        """,
        tuple(params),
    ).fetchall()
    touched_accounts: set[str] = set()
    position_count = 0
    for row in rows:
        quantity = float(row["total_quantity"] or 0)
        price = float(row["price"] or 0)
        avg_cost = float(row["avg_cost"] or 0)
        market_value = round(quantity * price, 4)
        unrealized_pnl = round(market_value - quantity * avg_cost, 4) if avg_cost > 0 else None
        unrealized_pnl_pct = round((price / avg_cost - 1) * 100, 4) if avg_cost > 0 else None
        conn.execute(
            """
            UPDATE positions
            SET market_price = ?,
                market_value = ?,
                unrealized_pnl = ?,
                unrealized_pnl_pct = ?,
                updated_at = ?
            WHERE account_id = ? AND symbol = ?
            """,
            (
                price,
                market_value,
                unrealized_pnl,
                unrealized_pnl_pct,
                now,
                row["account_id"],
                row["symbol"],
            ),
        )
        touched_accounts.add(row["account_id"])
        position_count += 1

    account_count = 0
    for account_id in sorted(touched_accounts):
        _sync_account_state_from_positions(conn, account_id, now)
        account_count += 1
    return {"positions": position_count, "accounts": account_count}


def _sync_account_state_from_positions(conn: sqlite3.Connection, account_id: str, now: str) -> None:
    state_id = f"state_{account_id}_current"
    state = conn.execute(
        "SELECT * FROM account_states WHERE account_state_id=?",
        (state_id,),
    ).fetchone()
    account = conn.execute("SELECT initial_cash FROM accounts WHERE account_id=?", (account_id,)).fetchone()
    totals = conn.execute(
        """
        SELECT COALESCE(SUM(COALESCE(market_value, 0)), 0) AS market_value,
               MAX(updated_at) AS updated_at
        FROM positions
        WHERE account_id = ?
          AND COALESCE(position_status, 'ACTIVE') != 'CLOSED'
          AND COALESCE(total_quantity, 0) > 0
        """,
        (account_id,),
    ).fetchone()
    latest_trade_date = conn.execute(
        """
        SELECT MAX(q.trade_date) AS trade_date
        FROM positions p
        JOIN market_quotes q ON q.symbol = p.symbol
        WHERE p.account_id = ?
          AND COALESCE(p.position_status, 'ACTIVE') != 'CLOSED'
        """,
        (account_id,),
    ).fetchone()
    market_value = round(float(totals["market_value"] or 0), 4)
    available_cash = float(state["available_cash"] if state and state["available_cash"] is not None else account["initial_cash"] if account else 0)
    frozen_cash = float(state["frozen_cash"] if state and state["frozen_cash"] is not None else 0)
    total_assets = round(available_cash + frozen_cash + market_value, 4)
    equity_position_pct = round(market_value / total_assets * 100, 4) if total_assets > 0 else None
    cash_pct = round(available_cash / total_assets * 100, 4) if total_assets > 0 else None
    trade_date = (latest_trade_date["trade_date"] if latest_trade_date else None) or (state["trade_date"] if state else None)
    conn.execute(
        """
        INSERT INTO account_states (
            account_state_id, account_id, as_of_time, trade_date, available_cash,
            frozen_cash, market_value, total_assets, equity_position_pct, cash_pct,
            today_buy_used, today_sell_amount, last_rollover_trade_date, updated_at, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_state_id) DO UPDATE SET
            as_of_time=excluded.as_of_time,
            trade_date=excluded.trade_date,
            available_cash=excluded.available_cash,
            frozen_cash=excluded.frozen_cash,
            market_value=excluded.market_value,
            total_assets=excluded.total_assets,
            equity_position_pct=excluded.equity_position_pct,
            cash_pct=excluded.cash_pct,
            updated_at=excluded.updated_at
        """,
        (
            state_id,
            account_id,
            now,
            trade_date,
            available_cash,
            frozen_cash,
            market_value,
            total_assets,
            equity_position_pct,
            cash_pct,
            state["today_buy_used"] if state else None,
            state["today_sell_amount"] if state else None,
            state["last_rollover_trade_date"] if state else None,
            now,
            state["payload_json"] if state else None,
        ),
    )
    position_rows = conn.execute(
        """
        SELECT symbol, market_value
        FROM positions
        WHERE account_id = ?
          AND COALESCE(position_status, 'ACTIVE') != 'CLOSED'
          AND COALESCE(total_quantity, 0) > 0
        """,
        (account_id,),
    ).fetchall()
    for row in position_rows:
        position_pct = round(float(row["market_value"] or 0) / total_assets * 100, 4) if total_assets > 0 else None
        conn.execute(
            """
            UPDATE positions
            SET position_pct = ?, updated_at = ?
            WHERE account_id = ? AND symbol = ?
            """,
            (position_pct, now, account_id, row["symbol"]),
        )


def first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None
