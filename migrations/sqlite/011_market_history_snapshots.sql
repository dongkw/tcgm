CREATE TABLE IF NOT EXISTS market_data_batches (
    batch_id TEXT PRIMARY KEY,
    batch_type TEXT NOT NULL,
    trade_date TEXT,
    session_type TEXT NOT NULL,
    scope_type TEXT,
    scope_json TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    total_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    error_message TEXT,
    params_json TEXT,
    payload_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_data_batches_type_date
ON market_data_batches (batch_type, trade_date, status);

CREATE INDEX IF NOT EXISTS idx_market_data_batches_started
ON market_data_batches (started_at);

CREATE TABLE IF NOT EXISTS daily_market_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    batch_id TEXT,
    symbol TEXT NOT NULL,
    name TEXT,
    exchange TEXT,
    asset_type TEXT NOT NULL DEFAULT 'stock',
    trade_date TEXT NOT NULL,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    pre_close NUMERIC,
    pct_change NUMERIC,
    volume NUMERIC,
    amount NUMERIC,
    turnover_pct NUMERIC,
    pe_ttm NUMERIC,
    pb NUMERIC,
    market_cap_yuan NUMERIC,
    ma20 NUMERIC,
    ma60 NUMERIC,
    change_20d_pct NUMERIC,
    atr14_pct NUMERIC,
    source TEXT,
    observed_at TEXT NOT NULL,
    effective_from TEXT,
    quality_status TEXT NOT NULL DEFAULT 'OK',
    quality_flags_json TEXT,
    payload_hash TEXT,
    payload_json TEXT,
    data_origin TEXT NOT NULL DEFAULT 'LIVE_CAPTURE',
    is_backfilled INTEGER NOT NULL DEFAULT 0,
    backfilled_at TEXT,
    backfill_batch_id TEXT,
    source_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES market_data_batches(batch_id) ON DELETE SET NULL,
    UNIQUE(symbol, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_market_snapshots_trade_date
ON daily_market_snapshots (trade_date);

CREATE INDEX IF NOT EXISTS idx_daily_market_snapshots_symbol_date
ON daily_market_snapshots (symbol, trade_date);

CREATE INDEX IF NOT EXISTS idx_daily_market_snapshots_batch
ON daily_market_snapshots (batch_id);

CREATE INDEX IF NOT EXISTS idx_daily_market_snapshots_origin
ON daily_market_snapshots (data_origin, quality_status);
