CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    execution_time_ms INTEGER
);

CREATE TABLE IF NOT EXISTS file_artifacts (
    artifact_id TEXT PRIMARY KEY,
    artifact_type TEXT,
    source_module TEXT,
    relative_path TEXT,
    absolute_path TEXT,
    content_hash TEXT,
    mime_type TEXT,
    record_count INTEGER,
    trade_date TEXT,
    symbol TEXT,
    account_id TEXT,
    strategy_version TEXT,
    created_at TEXT,
    indexed_at TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_file_artifacts_type_created ON file_artifacts (artifact_type, created_at);
CREATE INDEX IF NOT EXISTS idx_file_artifacts_symbol_date ON file_artifacts (symbol, trade_date);
CREATE INDEX IF NOT EXISTS idx_file_artifacts_account_date ON file_artifacts (account_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_file_artifacts_hash ON file_artifacts (content_hash);

CREATE TABLE IF NOT EXISTS import_batches (
    batch_id TEXT PRIMARY KEY,
    source_type TEXT,
    source_path TEXT,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    record_count INTEGER,
    success_count INTEGER,
    failed_count INTEGER,
    error_message TEXT,
    metadata_json TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    account_name TEXT,
    account_type TEXT,
    base_currency TEXT,
    initial_cash NUMERIC,
    cash_reserve_pct NUMERIC,
    max_single_position_pct NUMERIC,
    max_daily_buy_amount NUMERIC,
    is_active INTEGER,
    created_at TEXT,
    updated_at TEXT,
    payload_json TEXT
);

CREATE TABLE IF NOT EXISTS account_states (
    account_state_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    as_of_time TEXT,
    trade_date TEXT,
    available_cash NUMERIC,
    frozen_cash NUMERIC,
    market_value NUMERIC,
    total_assets NUMERIC,
    equity_position_pct NUMERIC,
    cash_pct NUMERIC,
    today_buy_used NUMERIC,
    today_sell_amount NUMERIC,
    last_rollover_trade_date TEXT,
    updated_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_account_states_account_date ON account_states (account_id, trade_date);

CREATE TABLE IF NOT EXISTS positions (
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT,
    asset_type TEXT,
    total_quantity INTEGER,
    available_quantity INTEGER,
    locked_quantity INTEGER,
    avg_cost NUMERIC,
    market_price NUMERIC,
    market_value NUMERIC,
    unrealized_pnl NUMERIC,
    unrealized_pnl_pct NUMERIC,
    position_pct NUMERIC,
    first_buy_date TEXT,
    last_trade_date TEXT,
    buy_logic TEXT,
    invalidation_point NUMERIC,
    stop_loss_price NUMERIC,
    planned_position_pct NUMERIC,
    position_status TEXT,
    updated_at TEXT,
    payload_json TEXT,
    PRIMARY KEY (account_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions (symbol);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions (position_status);

CREATE TABLE IF NOT EXISTS position_locks (
    lock_id TEXT PRIMARY KEY,
    account_id TEXT,
    symbol TEXT,
    buy_trade_id TEXT,
    buy_trade_date TEXT,
    unlock_trade_date TEXT,
    locked_quantity INTEGER,
    remaining_locked_quantity INTEGER,
    status TEXT,
    created_at TEXT,
    released_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_position_locks_account_symbol ON position_locks (account_id, symbol);
CREATE INDEX IF NOT EXISTS idx_position_locks_status ON position_locks (status);

CREATE TABLE IF NOT EXISTS cash_ledger (
    cash_ledger_id TEXT PRIMARY KEY,
    account_id TEXT,
    trade_date TEXT,
    event_time TEXT,
    event_type TEXT,
    amount NUMERIC,
    cash_before NUMERIC,
    cash_after NUMERIC,
    related_order_id TEXT,
    related_trade_id TEXT,
    related_decision_id TEXT,
    note TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_cash_ledger_account_date ON cash_ledger (account_id, trade_date);

CREATE TABLE IF NOT EXISTS position_ledger (
    position_ledger_id TEXT PRIMARY KEY,
    account_id TEXT,
    symbol TEXT,
    trade_date TEXT,
    event_time TEXT,
    event_type TEXT,
    quantity_change INTEGER,
    total_before INTEGER,
    total_after INTEGER,
    available_before INTEGER,
    available_after INTEGER,
    locked_before INTEGER,
    locked_after INTEGER,
    avg_cost_before NUMERIC,
    avg_cost_after NUMERIC,
    related_order_id TEXT,
    related_trade_id TEXT,
    related_decision_id TEXT,
    note TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_position_ledger_account_symbol ON position_ledger (account_id, symbol);
CREATE INDEX IF NOT EXISTS idx_position_ledger_date ON position_ledger (trade_date);

CREATE TABLE IF NOT EXISTS closed_positions (
    closed_position_id TEXT PRIMARY KEY,
    account_id TEXT,
    symbol TEXT,
    open_date TEXT,
    close_date TEXT,
    holding_days INTEGER,
    avg_buy_cost NUMERIC,
    avg_sell_price NUMERIC,
    total_sell_amount NUMERIC,
    realized_pnl NUMERIC,
    buy_logic TEXT,
    invalidation_point NUMERIC,
    close_reason TEXT,
    related_decision_ids_json TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_closed_positions_account_symbol ON closed_positions (account_id, symbol);

CREATE TABLE IF NOT EXISTS account_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    account_id TEXT,
    trade_date TEXT,
    snapshot_time TEXT,
    available_cash NUMERIC,
    frozen_cash NUMERIC,
    market_value NUMERIC,
    total_assets NUMERIC,
    daily_pnl NUMERIC,
    daily_return_pct NUMERIC,
    total_return_pct NUMERIC,
    max_drawdown_pct NUMERIC,
    equity_position_pct NUMERIC,
    cash_pct NUMERIC,
    position_count INTEGER,
    created_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_account_snapshots_account_date ON account_snapshots (account_id, trade_date);

CREATE TABLE IF NOT EXISTS position_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    account_id TEXT,
    symbol TEXT,
    trade_date TEXT,
    snapshot_time TEXT,
    total_quantity INTEGER,
    available_quantity INTEGER,
    locked_quantity INTEGER,
    avg_cost NUMERIC,
    market_price NUMERIC,
    market_value NUMERIC,
    unrealized_pnl NUMERIC,
    unrealized_pnl_pct NUMERIC,
    position_pct NUMERIC,
    buy_logic TEXT,
    invalidation_point NUMERIC,
    stop_loss_price NUMERIC,
    quote_time TEXT,
    quote_source TEXT,
    position_status TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_position_snapshots_account_symbol ON position_snapshots (account_id, symbol);
CREATE INDEX IF NOT EXISTS idx_position_snapshots_date ON position_snapshots (trade_date);

CREATE TABLE IF NOT EXISTS paper_signals (
    signal_id TEXT PRIMARY KEY,
    account_id TEXT,
    decision_id TEXT,
    snapshot_id TEXT,
    symbol TEXT,
    name TEXT,
    task_type TEXT,
    final_action TEXT,
    confidence TEXT,
    signal_action TEXT,
    signal_quantity INTEGER,
    source_decision_time TEXT,
    source_decision_path TEXT,
    source_decision_hash TEXT,
    strategy_version TEXT,
    action_reason TEXT,
    status TEXT,
    blocked_reason TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_paper_signals_account_symbol ON paper_signals (account_id, symbol);
CREATE INDEX IF NOT EXISTS idx_paper_signals_decision ON paper_signals (decision_id);

CREATE TABLE IF NOT EXISTS paper_orders (
    order_id TEXT PRIMARY KEY,
    account_id TEXT,
    signal_id TEXT,
    decision_id TEXT,
    snapshot_id TEXT,
    symbol TEXT,
    side TEXT,
    order_type TEXT,
    requested_quantity INTEGER,
    limit_price NUMERIC,
    reference_price NUMERIC,
    status TEXT,
    reject_reason TEXT,
    created_at TEXT,
    updated_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_paper_orders_account_symbol ON paper_orders (account_id, symbol);
CREATE INDEX IF NOT EXISTS idx_paper_orders_signal ON paper_orders (signal_id);

CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id TEXT PRIMARY KEY,
    order_id TEXT,
    account_id TEXT,
    decision_id TEXT,
    snapshot_id TEXT,
    symbol TEXT,
    name TEXT,
    side TEXT,
    quantity INTEGER,
    reference_price NUMERIC,
    fill_price NUMERIC,
    gross_amount NUMERIC,
    commission NUMERIC,
    stamp_tax NUMERIC,
    slippage_cost NUMERIC,
    net_amount NUMERIC,
    trade_time TEXT,
    trade_date TEXT,
    quote_source TEXT,
    quote_time TEXT,
    action_reason TEXT,
    invalidation_point NUMERIC,
    stop_loss_price NUMERIC,
    planned_position_pct NUMERIC,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_account_date ON paper_trades (account_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_paper_trades_decision ON paper_trades (decision_id);
CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol ON paper_trades (symbol);

CREATE TABLE IF NOT EXISTS strategy_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    symbol TEXT,
    name TEXT,
    task_type TEXT,
    trade_date TEXT,
    decision_time TEXT,
    strategy_version TEXT,
    schema_version TEXT,
    data_quality_level TEXT,
    data_quality_score NUMERIC,
    payload_json TEXT,
    source_file TEXT,
    artifact_id TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_strategy_snapshots_symbol_date ON strategy_snapshots (symbol, trade_date);

CREATE TABLE IF NOT EXISTS decision_results (
    decision_id TEXT PRIMARY KEY,
    snapshot_id TEXT,
    symbol TEXT,
    name TEXT,
    task_type TEXT,
    trade_date TEXT,
    decision_time TEXT,
    strategy_version TEXT,
    schema_version TEXT,
    final_action TEXT,
    confidence TEXT,
    action_reason TEXT,
    human_review_required INTEGER,
    trigger_prices_json TEXT,
    payload_json TEXT,
    artifact_id TEXT,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_decision_results_symbol_date ON decision_results (symbol, trade_date);
CREATE INDEX IF NOT EXISTS idx_decision_results_action ON decision_results (final_action);
CREATE INDEX IF NOT EXISTS idx_decision_results_snapshot ON decision_results (snapshot_id);

CREATE TABLE IF NOT EXISTS risk_checks (
    risk_check_id TEXT PRIMARY KEY,
    account_id TEXT,
    decision_id TEXT,
    snapshot_id TEXT,
    symbol TEXT,
    name TEXT,
    trade_date TEXT,
    risk_status TEXT,
    risk_level TEXT,
    allowed_action TEXT,
    original_action TEXT,
    max_cash_amount NUMERIC,
    max_quantity INTEGER,
    reference_price NUMERIC,
    quote_source TEXT,
    blocking_rules_json TEXT,
    warning_rules_json TEXT,
    human_review_required INTEGER,
    execution_allowed INTEGER,
    source_decision_path TEXT,
    source_snapshot_path TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_risk_checks_decision ON risk_checks (decision_id);
CREATE INDEX IF NOT EXISTS idx_risk_checks_status ON risk_checks (risk_status);

CREATE TABLE IF NOT EXISTS allocation_plans (
    allocation_id TEXT PRIMARY KEY,
    account_id TEXT,
    trade_date TEXT,
    strategy_version TEXT,
    cash_before NUMERIC,
    cash_reserved NUMERIC,
    buy_budget NUMERIC,
    planned_buy_amount NUMERIC,
    planned_position_count INTEGER,
    candidate_count INTEGER,
    rejected_count INTEGER,
    deferred_count INTEGER,
    record_only_count INTEGER,
    status TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_allocation_plans_account_date ON allocation_plans (account_id, trade_date);

CREATE TABLE IF NOT EXISTS order_intents (
    intent_id TEXT PRIMARY KEY,
    allocation_id TEXT,
    account_id TEXT,
    decision_id TEXT,
    snapshot_id TEXT,
    symbol TEXT,
    name TEXT,
    side TEXT,
    rank INTEGER,
    score NUMERIC,
    planned_cash_amount NUMERIC,
    planned_quantity INTEGER,
    reference_price NUMERIC,
    reason TEXT,
    status TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_order_intents_allocation ON order_intents (allocation_id);
CREATE INDEX IF NOT EXISTS idx_order_intents_decision ON order_intents (decision_id);

CREATE TABLE IF NOT EXISTS workflow_runs (
    workflow_run_id TEXT PRIMARY KEY,
    workflow_type TEXT,
    account_id TEXT,
    trade_date TEXT,
    calendar_date TEXT,
    session_name TEXT,
    is_trading_day INTEGER,
    effective_data_cutoff TEXT,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    input_params_json TEXT,
    output_refs_json TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_type_date ON workflow_runs (workflow_type, trade_date);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status_started ON workflow_runs (status, started_at);

CREATE TABLE IF NOT EXISTS pre_market_plans (
    plan_id TEXT PRIMARY KEY,
    workflow_run_id TEXT,
    account_id TEXT,
    trade_date TEXT,
    session_name TEXT,
    execution_allowed INTEGER,
    symbols_json TEXT,
    rollover_json TEXT,
    allocation_id TEXT,
    trigger_price_list_path TEXT,
    payload_json TEXT,
    artifact_id TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS trigger_price_items (
    trigger_item_id TEXT PRIMARY KEY,
    account_id TEXT,
    trade_date TEXT,
    symbol TEXT,
    task TEXT,
    task_type TEXT,
    decision_id TEXT,
    snapshot_id TEXT,
    final_action TEXT,
    confidence TEXT,
    reduce_trigger_price NUMERIC,
    clear_trigger_price NUMERIC,
    middle_trend_price NUMERIC,
    resistance_price NUMERIC,
    stop_loss_price NUMERIC,
    source_decision_path TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_trigger_price_items_key ON trigger_price_items (account_id, trade_date, symbol, task_type, decision_id);

CREATE TABLE IF NOT EXISTS intraday_scans (
    scan_id TEXT PRIMARY KEY,
    account_id TEXT,
    trade_date TEXT,
    calendar_date TEXT,
    session_name TEXT,
    is_trading_day INTEGER,
    allow_non_trading INTEGER,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    symbols_scanned INTEGER,
    trigger_count INTEGER,
    blocked_count INTEGER,
    duplicate_count INTEGER,
    input_refs_json TEXT,
    report_path TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_intraday_scans_account_date ON intraday_scans (account_id, trade_date);

CREATE TABLE IF NOT EXISTS trigger_events (
    trigger_event_id TEXT PRIMARY KEY,
    account_id TEXT,
    trade_date TEXT,
    scan_id TEXT,
    symbol TEXT,
    name TEXT,
    event_type TEXT,
    trigger_price NUMERIC,
    current_price NUMERIC,
    price_source TEXT,
    quote_trade_date TEXT,
    quote_time TEXT,
    data_time_precision TEXT,
    decision_id TEXT,
    snapshot_id TEXT,
    source_decision_path TEXT,
    source_plan_path TEXT,
    severity TEXT,
    suggested_action TEXT,
    execution_allowed INTEGER,
    requires_human_confirm INTEGER,
    risk_status TEXT,
    blocked_reason TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_trigger_events_dedupe ON trigger_events (account_id, trade_date, symbol, event_type, decision_id);
CREATE INDEX IF NOT EXISTS idx_trigger_events_scan ON trigger_events (scan_id);

CREATE TABLE IF NOT EXISTS replay_runs (
    replay_id TEXT PRIMARY KEY,
    account_id TEXT,
    symbols_json TEXT,
    start_date TEXT,
    end_date TEXT,
    initial_cash NUMERIC,
    replay_mode TEXT,
    strategy_version TEXT,
    execution_mode TEXT,
    bar_dir TEXT,
    cash_reserve_pct NUMERIC,
    max_single_position_pct NUMERIC,
    max_daily_buy_amount NUMERIC,
    default_watch_cash NUMERIC,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    output_root TEXT,
    report_path TEXT,
    performance_path TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_replay_runs_strategy ON replay_runs (strategy_version, started_at);

CREATE TABLE IF NOT EXISTS replay_daily_records (
    daily_record_id TEXT PRIMARY KEY,
    replay_id TEXT,
    trade_date TEXT,
    symbols_scanned INTEGER,
    released_quantity INTEGER,
    holding_decisions INTEGER,
    buy_decisions INTEGER,
    risk_checks INTEGER,
    ready_intents INTEGER,
    orders INTEGER,
    trades INTEGER,
    account_snapshot_id TEXT,
    total_assets NUMERIC,
    available_cash NUMERIC,
    market_value NUMERIC,
    daily_pnl NUMERIC,
    blocked_reasons_json TEXT,
    allocation_status TEXT,
    created_at TEXT,
    payload_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_replay_daily_records_key ON replay_daily_records (replay_id, trade_date);

CREATE TABLE IF NOT EXISTS performance_metrics (
    performance_metric_id TEXT PRIMARY KEY,
    source_type TEXT,
    source_id TEXT,
    account_id TEXT,
    strategy_version TEXT,
    start_date TEXT,
    end_date TEXT,
    initial_cash NUMERIC,
    final_assets NUMERIC,
    total_return_pct NUMERIC,
    annualized_return_pct NUMERIC,
    max_drawdown_pct NUMERIC,
    max_drawdown_start TEXT,
    max_drawdown_end TEXT,
    trade_count INTEGER,
    buy_count INTEGER,
    sell_count INTEGER,
    win_rate NUMERIC,
    profit_loss_ratio NUMERIC,
    average_win_pct NUMERIC,
    average_loss_pct NUMERIC,
    largest_win_pct NUMERIC,
    largest_loss_pct NUMERIC,
    average_holding_days NUMERIC,
    cash_usage_pct NUMERIC,
    turnover_rate NUMERIC,
    benchmark_return_pct NUMERIC,
    excess_return_pct NUMERIC,
    payload_json TEXT,
    created_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_performance_metrics_source ON performance_metrics (source_type, source_id);

CREATE TABLE IF NOT EXISTS strategy_tuning_records (
    iteration_id TEXT PRIMARY KEY,
    created_at TEXT,
    source_type TEXT,
    source_id TEXT,
    source_path TEXT,
    strategy_version TEXT,
    previous_strategy_version TEXT,
    account_id TEXT,
    symbols_json TEXT,
    period_start TEXT,
    period_end TEXT,
    metrics_json TEXT,
    auto_issues_json TEXT,
    manual_issues_json TEXT,
    blocked_reason_counts_json TEXT,
    worst_days_json TEXT,
    error_case_count INTEGER,
    closed_position_count INTEGER,
    hypothesis TEXT,
    rule_changes TEXT,
    risk_changes TEXT,
    position_changes TEXT,
    next_action TEXT,
    conclusion TEXT,
    tags_json TEXT,
    notes TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_strategy_tuning_strategy ON strategy_tuning_records (strategy_version, created_at);
CREATE INDEX IF NOT EXISTS idx_strategy_tuning_source ON strategy_tuning_records (source_type, source_id);

CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    report_type TEXT,
    title TEXT,
    account_id TEXT,
    symbol TEXT,
    trade_date TEXT,
    strategy_version TEXT,
    source_type TEXT,
    source_id TEXT,
    artifact_id TEXT,
    relative_path TEXT,
    created_at TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_reports_type_date ON reports (report_type, trade_date);
CREATE INDEX IF NOT EXISTS idx_reports_symbol_date ON reports (symbol, trade_date);

CREATE TABLE IF NOT EXISTS ai_calls (
    ai_call_id TEXT PRIMARY KEY,
    purpose TEXT,
    model TEXT,
    request_time TEXT,
    response_time TEXT,
    status TEXT,
    input_hash TEXT,
    input_json TEXT,
    output_json TEXT,
    token_usage_json TEXT,
    cost_amount NUMERIC,
    related_type TEXT,
    related_id TEXT,
    created_at TEXT
);
