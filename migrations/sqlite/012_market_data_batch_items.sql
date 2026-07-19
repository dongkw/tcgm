CREATE TABLE IF NOT EXISTS market_data_batch_items (
    item_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    exchange TEXT,
    status TEXT NOT NULL,
    trade_date TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    payload_json TEXT,
    FOREIGN KEY (batch_id) REFERENCES market_data_batches(batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_market_data_batch_items_batch_status
ON market_data_batch_items (batch_id, status, symbol);

CREATE INDEX IF NOT EXISTS idx_market_data_batch_items_symbol
ON market_data_batch_items (symbol, trade_date);
