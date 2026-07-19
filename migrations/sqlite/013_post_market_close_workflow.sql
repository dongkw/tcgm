CREATE TABLE IF NOT EXISTS post_market_close_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    current_step TEXT,
    full_market_status TEXT,
    prepare_status TEXT,
    diagnosis_status TEXT,
    watchlist_status TEXT,
    market_batch_id TEXT,
    prepare_run_id TEXT,
    diagnosis_run_id TEXT,
    total_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    next_watch_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    params_json TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_post_market_close_runs_started
ON post_market_close_runs (started_at);

CREATE INDEX IF NOT EXISTS idx_post_market_close_runs_status
ON post_market_close_runs (status, current_step);
