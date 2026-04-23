CREATE TABLE run_registry (
    run_id VARCHAR NOT NULL,
    run_type VARCHAR NOT NULL,
    command_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    started_at_utc TIMESTAMPTZ NOT NULL,
    finished_at_utc TIMESTAMPTZ,
    data_snapshot_id VARCHAR,
    config_hash VARCHAR NOT NULL,
    git_commit VARCHAR NOT NULL,
    notes VARCHAR[],
    artifact_path VARCHAR
);

CREATE TABLE data_snapshot_registry (
    snapshot_id VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    vendor VARCHAR NOT NULL,
    dataset VARCHAR NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    requested_roots VARCHAR[] NOT NULL,
    requested_schemas VARCHAR[] NOT NULL,
    request_plan_hash VARCHAR NOT NULL,
    manifest_hash VARCHAR NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE data_file_registry (
    file_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    logical_table VARCHAR NOT NULL,
    source_schema VARCHAR,
    path VARCHAR NOT NULL,
    content_sha256 VARCHAR NOT NULL,
    row_count BIGINT NOT NULL,
    min_trade_date DATE,
    max_trade_date DATE,
    request_params_hash VARCHAR NOT NULL,
    cached BOOLEAN NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE contract_master (
    dataset VARCHAR NOT NULL,
    instrument_id BIGINT NOT NULL,
    raw_symbol VARCHAR NOT NULL,
    root VARCHAR NOT NULL,
    exchange VARCHAR,
    currency VARCHAR,
    expiration_date DATE,
    last_trade_date DATE,
    first_trade_date DATE,
    multiplier DOUBLE,
    tick_size DOUBLE,
    instrument_class VARCHAR NOT NULL,
    valid_from_utc TIMESTAMPTZ NOT NULL,
    valid_to_utc TIMESTAMPTZ,
    definition_hash VARCHAR NOT NULL,
    ingested_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE contracts_daily (
    trade_date DATE NOT NULL,
    root VARCHAR NOT NULL,
    raw_symbol VARCHAR NOT NULL,
    dataset VARCHAR NOT NULL,
    instrument_id BIGINT NOT NULL,
    open_price DOUBLE,
    high_price DOUBLE,
    low_price DOUBLE,
    close_price DOUBLE,
    settle_price DOUBLE,
    settle_status VARCHAR NOT NULL,
    volume DOUBLE,
    open_interest DOUBLE,
    price_source VARCHAR NOT NULL,
    volume_source VARCHAR NOT NULL,
    available_at_utc TIMESTAMPTZ,
    ingested_at_utc TIMESTAMPTZ NOT NULL,
    quality_flags VARCHAR[],
    override_id VARCHAR,
    snapshot_id VARCHAR NOT NULL
);

CREATE TABLE roll_events (
    roll_event_id VARCHAR NOT NULL,
    root VARCHAR NOT NULL,
    from_raw_symbol VARCHAR NOT NULL,
    to_raw_symbol VARCHAR NOT NULL,
    trigger_date DATE NOT NULL,
    effective_date DATE NOT NULL,
    roll_reason VARCHAR NOT NULL,
    front_volume_tminus1 DOUBLE,
    next_volume_tminus1 DOUBLE,
    confirmation_count BIGINT NOT NULL,
    from_settle DOUBLE,
    to_settle DOUBLE,
    ratio_adjustment DOUBLE,
    basis_at_roll DOUBLE,
    builder_version VARCHAR NOT NULL,
    override_id VARCHAR
);

CREATE TABLE lead_map (
    as_of_date DATE NOT NULL,
    root VARCHAR NOT NULL,
    roll_policy_version VARCHAR NOT NULL,
    lead_raw_symbol VARCHAR NOT NULL,
    next_raw_symbol VARCHAR,
    prev_lead_raw_symbol VARCHAR,
    roll_flag BOOLEAN NOT NULL,
    roll_event_id VARCHAR,
    days_to_expiry BIGINT,
    front_volume_tminus1 DOUBLE,
    next_volume_tminus1 DOUBLE,
    confirmation_count BIGINT NOT NULL,
    hard_roll_deadline DATE,
    selection_reason VARCHAR NOT NULL,
    builder_version VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL
);

CREATE TABLE continuous_daily (
    series_id VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    root VARCHAR NOT NULL,
    lead_raw_symbol VARCHAR NOT NULL,
    raw_settle_price DOUBLE,
    adj_settle_price DOUBLE,
    adj_factor DOUBLE NOT NULL,
    daily_return DOUBLE,
    settle_status VARCHAR NOT NULL,
    roll_flag BOOLEAN NOT NULL,
    roll_event_id VARCHAR,
    is_usable_for_signal BOOLEAN NOT NULL,
    quality_flags VARCHAR[],
    builder_version VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL
);

CREATE TABLE cpd_daily (
    feature_set_id VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    root VARCHAR NOT NULL,
    cpd_window_days BIGINT NOT NULL,
    cpd_score DOUBLE,
    cpd_age_days BIGINT,
    cpd_location_index BIGINT,
    cpd_is_valid BOOLEAN NOT NULL,
    cpd_method VARCHAR NOT NULL,
    builder_version VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL
);

CREATE TABLE features_daily (
    feature_set_id VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    root VARCHAR NOT NULL,
    series_id VARCHAR NOT NULL,
    ret_1 DOUBLE,
    ret_21 DOUBLE,
    ret_63 DOUBLE,
    ret_126 DOUBLE,
    ret_252 DOUBLE,
    macd_8_24 DOUBLE,
    macd_16_48 DOUBLE,
    macd_32_96 DOUBLE,
    cpd21_score DOUBLE,
    cpd21_age DOUBLE,
    cpd63_score DOUBLE,
    cpd63_age DOUBLE,
    vol_20_60 DOUBLE,
    vol_60_252 DOUBLE,
    annualized_vol_60 DOUBLE,
    is_complete BOOLEAN NOT NULL,
    warmup_status VARCHAR NOT NULL,
    feature_hash VARCHAR NOT NULL,
    builder_version VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL
);

CREATE TABLE signals_daily (
    run_id VARCHAR NOT NULL,
    strategy_id VARCHAR NOT NULL,
    model_id VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    root VARCHAR NOT NULL,
    signal_raw DOUBLE,
    signal_clipped DOUBLE,
    is_valid BOOLEAN NOT NULL,
    invalid_reason VARCHAR,
    feature_hash VARCHAR,
    created_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE training_runs (
    training_run_id VARCHAR NOT NULL,
    strategy_id VARCHAR NOT NULL,
    feature_set_id VARCHAR NOT NULL,
    train_start_date DATE NOT NULL,
    train_end_date DATE NOT NULL,
    validation_start_date DATE NOT NULL,
    validation_end_date DATE NOT NULL,
    seed BIGINT NOT NULL,
    hyperparams_hash VARCHAR NOT NULL,
    config_hash VARCHAR NOT NULL,
    data_snapshot_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    best_validation_metric DOUBLE,
    created_at_utc TIMESTAMPTZ NOT NULL,
    completed_at_utc TIMESTAMPTZ
);

CREATE TABLE model_registry (
    model_id VARCHAR NOT NULL,
    strategy_id VARCHAR NOT NULL,
    training_run_id VARCHAR NOT NULL,
    artifact_path VARCHAR NOT NULL,
    artifact_sha256 VARCHAR NOT NULL,
    feature_set_id VARCHAR NOT NULL,
    config_hash VARCHAR NOT NULL,
    model_status VARCHAR NOT NULL,
    registered_at_utc TIMESTAMPTZ NOT NULL,
    notes VARCHAR
);

CREATE TABLE targets_daily (
    run_id VARCHAR NOT NULL,
    strategy_id VARCHAR NOT NULL,
    execution_mode VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    execution_date DATE NOT NULL,
    root VARCHAR NOT NULL,
    lead_raw_symbol VARCHAR NOT NULL,
    target_contracts BIGINT NOT NULL,
    current_contracts BIGINT,
    order_delta_contracts BIGINT,
    control_action VARCHAR NOT NULL,
    model_id VARCHAR,
    quality_flags VARCHAR[],
    created_at_utc TIMESTAMPTZ
);

CREATE TABLE broker_positions_snapshot (
    position_snapshot_id VARCHAR NOT NULL,
    run_id VARCHAR,
    execution_mode VARCHAR NOT NULL,
    account_id VARCHAR,
    broker_contract_id VARCHAR,
    raw_symbol VARCHAR NOT NULL,
    root VARCHAR NOT NULL,
    position_contracts BIGINT NOT NULL,
    snapshot_time_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE monitoring_daily (
    run_id VARCHAR NOT NULL,
    execution_mode VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    execution_date DATE NOT NULL,
    final_action VARCHAR NOT NULL,
    highest_severity VARCHAR,
    alerts_json VARCHAR,
    created_at_utc TIMESTAMPTZ
);

CREATE TABLE order_intents (
    order_intent_id VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    strategy_id VARCHAR NOT NULL,
    execution_mode VARCHAR NOT NULL,
    as_of_date DATE NOT NULL,
    execution_date DATE NOT NULL,
    root VARCHAR NOT NULL,
    raw_symbol VARCHAR NOT NULL,
    broker_contract_id VARCHAR,
    side VARCHAR NOT NULL,
    quantity BIGINT NOT NULL,
    order_type VARCHAR NOT NULL,
    limit_price DOUBLE,
    reason VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    control_action VARCHAR NOT NULL,
    position_snapshot_id VARCHAR NOT NULL,
    sequence_no BIGINT NOT NULL,
    rejection_reason VARCHAR,
    created_at_utc TIMESTAMPTZ NOT NULL,
    submitted_at_utc TIMESTAMPTZ
);

CREATE TABLE journal_events (
    event_id VARCHAR NOT NULL,
    run_id VARCHAR NOT NULL,
    execution_mode VARCHAR NOT NULL,
    as_of_date DATE,
    execution_date DATE,
    root VARCHAR,
    component VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    code VARCHAR NOT NULL,
    message VARCHAR NOT NULL,
    details_json VARCHAR,
    created_at_utc TIMESTAMPTZ NOT NULL
);
