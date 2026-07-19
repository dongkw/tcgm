CREATE TABLE IF NOT EXISTS analysis_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    name TEXT,
    task_type TEXT NOT NULL,
    market_phase TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    source_cutoff_time TEXT NOT NULL,
    feature_set_version TEXT NOT NULL,
    data_status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analysis_snapshots_symbol_date
ON analysis_snapshots (symbol, trade_date, decision_time);

CREATE TABLE IF NOT EXISTS strategy_runs (
    run_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    task_type TEXT NOT NULL,
    market_phase TEXT NOT NULL,
    status TEXT NOT NULL,
    registry_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    error_json TEXT,
    payload_json TEXT NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES analysis_snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_strategy_runs_symbol_time
ON strategy_runs (symbol, started_at);

CREATE INDEX IF NOT EXISTS idx_strategy_runs_snapshot
ON strategy_runs (snapshot_id);

CREATE TABLE IF NOT EXISTS strategy_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    parameter_version TEXT NOT NULL,
    strategy_family TEXT NOT NULL,
    implementation_type TEXT NOT NULL,
    maturity TEXT NOT NULL,
    calibration_status TEXT NOT NULL,
    applicable INTEGER NOT NULL,
    data_status TEXT NOT NULL,
    raw_score NUMERIC,
    calibrated_score NUMERIC,
    signal TEXT NOT NULL,
    confidence TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    scoring_config_hash TEXT,
    error_message TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, strategy_id, strategy_version, parameter_version),
    FOREIGN KEY (run_id) REFERENCES strategy_runs(run_id),
    FOREIGN KEY (snapshot_id) REFERENCES analysis_snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_strategy
ON strategy_evaluations (strategy_id, strategy_version, created_at);

CREATE INDEX IF NOT EXISTS idx_strategy_evaluations_run
ON strategy_evaluations (run_id);

CREATE TABLE IF NOT EXISTS strategy_aggregations (
    aggregation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    snapshot_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    conclusion TEXT NOT NULL,
    effective_strategy_count INTEGER NOT NULL,
    support_count INTEGER NOT NULL,
    oppose_count INTEGER NOT NULL,
    neutral_count INTEGER NOT NULL,
    unknown_count INTEGER NOT NULL,
    blocked_strategy_count INTEGER NOT NULL,
    failed_strategy_count INTEGER NOT NULL,
    aggregator_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES strategy_runs(run_id),
    FOREIGN KEY (snapshot_id) REFERENCES analysis_snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_strategy_aggregations_snapshot
ON strategy_aggregations (snapshot_id);

CREATE TABLE IF NOT EXISTS strategy_analysis_sessions (
    analysis_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    name TEXT,
    trade_date TEXT NOT NULL,
    decision_time TEXT NOT NULL,
    source TEXT NOT NULL,
    has_position INTEGER NOT NULL,
    buy_snapshot_id TEXT NOT NULL,
    buy_run_id TEXT NOT NULL,
    buy_aggregation_id TEXT NOT NULL,
    buy_conclusion TEXT NOT NULL,
    holding_snapshot_id TEXT,
    holding_run_id TEXT,
    holding_aggregation_id TEXT,
    holding_conclusion TEXT,
    effective_strategy_count INTEGER NOT NULL,
    blocked_strategy_count INTEGER NOT NULL,
    failed_strategy_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    report_relative_path TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (buy_snapshot_id) REFERENCES analysis_snapshots(snapshot_id),
    FOREIGN KEY (buy_run_id) REFERENCES strategy_runs(run_id),
    FOREIGN KEY (buy_aggregation_id) REFERENCES strategy_aggregations(aggregation_id),
    FOREIGN KEY (holding_snapshot_id) REFERENCES analysis_snapshots(snapshot_id),
    FOREIGN KEY (holding_run_id) REFERENCES strategy_runs(run_id),
    FOREIGN KEY (holding_aggregation_id) REFERENCES strategy_aggregations(aggregation_id)
);

CREATE INDEX IF NOT EXISTS idx_strategy_analysis_sessions_symbol_time
ON strategy_analysis_sessions (symbol, decision_time);

CREATE INDEX IF NOT EXISTS idx_strategy_analysis_sessions_trade_date
ON strategy_analysis_sessions (trade_date, decision_time);
