CREATE TABLE IF NOT EXISTS strategy_context_profiles (
    profile_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL UNIQUE,
    holding_period TEXT,
    stock_type TEXT,
    future_eps NUMERIC,
    high_risk INTEGER,
    core_logic TEXT,
    catalyst TEXT,
    medium_term_improvement TEXT,
    tracking_metric TEXT,
    fundamental_invalidation TEXT,
    technical_invalidation TEXT,
    event_invalidation TEXT,
    business_model_stable INTEGER,
    profit_quality_5y INTEGER,
    cashflow_reliable INTEGER,
    competition_not_worse INTEGER,
    logic_still_valid INTEGER,
    thesis_fully_realized INTEGER,
    would_rebuy_now INTEGER,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_context_profiles_updated
ON strategy_context_profiles (updated_at);

CREATE TABLE IF NOT EXISTS strategy_context_revisions (
    revision_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (profile_id) REFERENCES strategy_context_profiles(profile_id)
);

CREATE INDEX IF NOT EXISTS idx_strategy_context_revisions_symbol_time
ON strategy_context_revisions (symbol, created_at);
