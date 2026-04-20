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
