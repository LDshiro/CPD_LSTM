from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
import yaml

from cpdshadow.instruments import AssetClass

CPD_LSTM_FEATURE_ORDER = [
    "ret_1",
    "ret_21",
    "ret_63",
    "ret_126",
    "ret_252",
    "macd_8_24",
    "macd_16_48",
    "macd_32_96",
    "cpd21_score",
    "cpd21_age",
    "cpd63_score",
    "cpd63_age",
    "vol_20_60",
    "vol_60_252",
]


class RuntimeConfig(BaseModel):
    timezone: str = Field(default="Asia/Tokyo")
    log_level: str = Field(default="INFO")


class RiskConfig(BaseModel):
    target_annual_vol: float = Field(default=0.10, gt=0)
    vol_lookback_days: int = Field(default=60, ge=2)
    active_signal_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    single_root_soft_cap_fraction_of_nav: float = Field(default=0.12, gt=0.0)
    asset_class_soft_cap_fraction_of_nav: float = Field(default=0.35, gt=0.0)
    initial_margin_soft_cap_fraction_of_nav: float = Field(default=0.35, gt=0.0)
    initial_margin_hard_cap_fraction_of_nav: float = Field(default=0.50, gt=0.0)
    contract_rounding: Literal["half_away_from_zero"] = "half_away_from_zero"
    post_rounding_recheck: bool = True
    max_integer_reduction_steps: int = Field(default=500, ge=0)

    @model_validator(mode="after")
    def validate_caps(self) -> "RiskConfig":
        if self.asset_class_soft_cap_fraction_of_nav < self.single_root_soft_cap_fraction_of_nav:
            raise ValueError("asset-class cap must be >= single-root cap")
        if self.initial_margin_hard_cap_fraction_of_nav < self.initial_margin_soft_cap_fraction_of_nav:
            raise ValueError("margin hard cap must be >= margin soft cap")
        return self


class PortfolioConfig(BaseModel):
    risk_budget_mode: Literal["equal_risk_sqrt_active"] = "equal_risk_sqrt_active"
    estimate_trade_costs_in_target_layer: bool = True
    zero_target_on_invalid_inputs: bool = True


class AssetClassFloatMap(BaseModel):
    equity_index: float
    rates: float
    fx: float
    metals: float
    energy: float
    agriculture: float

    def get(self, asset_class: AssetClass) -> float:
        return float(getattr(self, asset_class))


class CostsConfig(BaseModel):
    default_broker: str = "research_default"
    commission_per_contract_side_usd: AssetClassFloatMap = Field(default_factory=lambda: AssetClassFloatMap(
        equity_index=1.50,
        rates=1.35,
        fx=1.35,
        metals=1.85,
        energy=2.35,
        agriculture=1.85,
    ))
    slippage_ticks_per_side: AssetClassFloatMap = Field(default_factory=lambda: AssetClassFloatMap(
        equity_index=0.50,
        rates=0.50,
        fx=0.75,
        metals=0.75,
        energy=1.00,
        agriculture=1.00,
    ))
    roll_extra_ticks_per_contract_side: AssetClassFloatMap = Field(default_factory=lambda: AssetClassFloatMap(
        equity_index=0.25,
        rates=0.25,
        fx=0.25,
        metals=0.50,
        energy=0.75,
        agriculture=0.50,
    ))


class DataQualityMonitoringConfig(BaseModel):
    max_missing_settlement_roots: int = Field(default=0, ge=0)
    max_missing_volume_roots: int = Field(default=0, ge=0)
    max_contract_map_conflicts: int = Field(default=0, ge=0)
    max_duplicate_feature_rows: int = Field(default=0, ge=0)
    max_feature_missing_fraction: float = Field(default=0.01, ge=0.0, le=1.0)
    max_stale_feature_roots: int = Field(default=0, ge=0)


class ModelHealthMonitoringConfig(BaseModel):
    require_artifact_hash_match: bool = True
    require_cpd_service_success: bool = True
    max_nonfinite_signal_roots: int = Field(default=0, ge=0)
    max_signal_saturation_fraction: float = Field(default=0.35, ge=0.0, le=1.0)
    min_cross_sectional_signal_std: float = Field(default=0.02, ge=0.0)
    max_feature_psi: float = Field(default=0.20, ge=0.0)


class RiskHealthMonitoringConfig(BaseModel):
    realized_vol_warning: float = Field(default=0.12, ge=0.0)
    realized_vol_reduce_only: float = Field(default=0.14, ge=0.0)
    margin_usage_warning: float = Field(default=0.35, ge=0.0)
    margin_usage_reduce_only: float = Field(default=0.50, ge=0.0)
    max_position_reconciliation_mismatches: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_ordering(self) -> "RiskHealthMonitoringConfig":
        if self.realized_vol_reduce_only < self.realized_vol_warning:
            raise ValueError("realized_vol_reduce_only must be >= realized_vol_warning")
        if self.margin_usage_reduce_only < self.margin_usage_warning:
            raise ValueError("margin_usage_reduce_only must be >= margin_usage_warning")
        return self


class ExecutionHealthMonitoringConfig(BaseModel):
    require_broker_state_known: bool = True
    max_order_reject_count: int = Field(default=0, ge=0)
    min_fill_ratio_warning: float = Field(default=0.80, ge=0.0, le=1.0)
    max_slippage_ratio_warning: float = Field(default=1.25, ge=0.0)
    max_slippage_ratio_fallback: float = Field(default=1.50, ge=0.0)

    @model_validator(mode="after")
    def validate_ordering(self) -> "ExecutionHealthMonitoringConfig":
        if self.max_slippage_ratio_fallback < self.max_slippage_ratio_warning:
            raise ValueError("max_slippage_ratio_fallback must be >= max_slippage_ratio_warning")
        return self


class StrategyHealthMonitoringConfig(BaseModel):
    min_cpd_60d_net_sharpe_before_review: float = -0.25
    max_consecutive_cpd_underperformance_windows: int = Field(default=2, ge=0)
    min_reversal_edge_usd: float = 0.0


class MonitoringConfig(BaseModel):
    data_quality: DataQualityMonitoringConfig = Field(default_factory=DataQualityMonitoringConfig)
    model_health: ModelHealthMonitoringConfig = Field(default_factory=ModelHealthMonitoringConfig)
    risk_health: RiskHealthMonitoringConfig = Field(default_factory=RiskHealthMonitoringConfig)
    execution_health: ExecutionHealthMonitoringConfig = Field(default_factory=ExecutionHealthMonitoringConfig)
    strategy_health: StrategyHealthMonitoringConfig = Field(default_factory=StrategyHealthMonitoringConfig)


class RollConfig(BaseModel):
    policy_version: Literal["volume3_hardroll_v1"] = "volume3_hardroll_v1"
    builder_version: Literal["roll_engine_v1"] = "roll_engine_v1"
    volume_confirmation_days: int = Field(default=3, ge=1)
    effective_lag_trading_days: int = Field(default=1, ge=1)
    volume_column: str = "volume"
    volume_trigger_operator: Literal["next_strictly_greater_than_front"] = "next_strictly_greater_than_front"
    missing_volume_resets_confirmation: bool = True
    hard_roll_anchor_preference: list[str] = Field(default_factory=lambda: [
        "last_trade_date",
        "expiration_date",
    ])
    calendar_source: Literal["root_contracts_daily_dates"] = "root_contracts_daily_dates"
    no_rollback: bool = True


class ContinuousConfig(BaseModel):
    series_id: str = "v1_back_ratio_settle"
    builder_version: Literal["continuous_builder_v1"] = "continuous_builder_v1"
    strict_roll_ratio: bool = True
    allow_close_fallback: bool = True
    allowed_settle_statuses: list[str] = Field(default_factory=lambda: [
        "final",
        "preliminary",
        "close_fallback",
    ])
    blocked_settle_statuses: list[str] = Field(default_factory=lambda: ["missing"])
    max_abs_daily_return_warning: float = Field(default=0.20, ge=0.0)
    max_abs_daily_return_error: float = Field(default=0.50, ge=0.0)

    @model_validator(mode="after")
    def validate_thresholds(self) -> "ContinuousConfig":
        if self.max_abs_daily_return_error < self.max_abs_daily_return_warning:
            raise ValueError(
                "max_abs_daily_return_error must be >= max_abs_daily_return_warning"
            )
        return self


class FeatureHorizonsConfig(BaseModel):
    normalized_returns: list[int] = Field(default_factory=lambda: [1, 21, 63, 126, 252])

    @model_validator(mode="after")
    def validate_horizons(self) -> "FeatureHorizonsConfig":
        if not self.normalized_returns:
            raise ValueError("normalized_returns must not be empty")
        if any(horizon <= 0 for horizon in self.normalized_returns):
            raise ValueError("normalized_returns must be positive")
        return self


class FeatureVolatilityConfig(BaseModel):
    estimator: Literal["ewm_std"] = "ewm_std"
    span_days: int = Field(default=60, ge=2)
    min_periods: int = Field(default=20, ge=2)
    ratio_pairs: list[tuple[int, int]] = Field(default_factory=lambda: [(20, 60), (60, 252)])

    @model_validator(mode="after")
    def validate_pairs(self) -> "FeatureVolatilityConfig":
        if not self.ratio_pairs:
            raise ValueError("ratio_pairs must not be empty")
        for left, right in self.ratio_pairs:
            if left <= 0 or right <= 0 or left >= right:
                raise ValueError("ratio_pairs must contain positive increasing pairs")
        return self


class FeatureMacdConfig(BaseModel):
    method: Literal["log_price_ema_diff_zscore"] = "log_price_ema_diff_zscore"
    pairs: list[tuple[int, int]] = Field(default_factory=lambda: [(8, 24), (16, 48), (32, 96)])
    zscore_span_days: int = Field(default=252, ge=2)
    zscore_min_periods: int = Field(default=63, ge=2)

    @model_validator(mode="after")
    def validate_pairs(self) -> "FeatureMacdConfig":
        if not self.pairs:
            raise ValueError("macd pairs must not be empty")
        for fast, slow in self.pairs:
            if fast <= 0 or slow <= 0 or fast >= slow:
                raise ValueError("macd pairs must contain positive increasing spans")
        return self


class FeatureCpdConfig(BaseModel):
    builder_version: Literal["cpd_builder_v1"] = "cpd_builder_v1"
    method: Literal["two_sample_t_v1"] = "two_sample_t_v1"
    windows: list[int] = Field(default_factory=lambda: [21, 63])
    min_segment_days: int = Field(default=5, ge=1)
    min_segment_fraction: float = Field(default=0.25, gt=0.0, lt=0.5)
    input_return: Literal["vol_scaled_daily_return"] = "vol_scaled_daily_return"
    score_transform: Literal["one_minus_exp_half_t2"] = "one_minus_exp_half_t2"

    @model_validator(mode="after")
    def validate_windows(self) -> "FeatureCpdConfig":
        if sorted(self.windows) != self.windows:
            raise ValueError("cpd windows must be sorted")
        if len(set(self.windows)) != len(self.windows):
            raise ValueError("cpd windows must be unique")
        if any(window <= 0 for window in self.windows):
            raise ValueError("cpd windows must be positive")
        return self


class FeatureClippingConfig(BaseModel):
    normalized_return_abs_max: float = Field(default=20.0, gt=0.0)
    macd_abs_max: float = Field(default=20.0, gt=0.0)
    vol_ratio_min: float = Field(default=0.05, gt=0.0)
    vol_ratio_max: float = Field(default=20.0, gt=0.0)
    cpd_score_min: float = Field(default=0.0, ge=0.0, le=1.0)
    cpd_score_max: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_bounds(self) -> "FeatureClippingConfig":
        if self.vol_ratio_max < self.vol_ratio_min:
            raise ValueError("vol_ratio_max must be >= vol_ratio_min")
        if self.cpd_score_max < self.cpd_score_min:
            raise ValueError("cpd_score_max must be >= cpd_score_min")
        return self


class FeaturesConfig(BaseModel):
    feature_set_id: str = "features_v1"
    builder_version: Literal["features_builder_v1"] = "features_builder_v1"
    series_id: str = "v1_back_ratio_settle"
    price_column: Literal["adj_settle_price"] = "adj_settle_price"
    return_column: Literal["daily_return"] = "daily_return"
    annualization_factor: int = Field(default=252, ge=1)
    warmup_days: int = Field(default=252, ge=1)
    epsilon: float = Field(default=1.0e-12, gt=0.0)
    horizons: FeatureHorizonsConfig = Field(default_factory=FeatureHorizonsConfig)
    volatility: FeatureVolatilityConfig = Field(default_factory=FeatureVolatilityConfig)
    macd: FeatureMacdConfig = Field(default_factory=FeatureMacdConfig)
    cpd: FeatureCpdConfig = Field(default_factory=FeatureCpdConfig)
    clipping: FeatureClippingConfig = Field(default_factory=FeatureClippingConfig)


class SignalsConfig(BaseModel):
    output_dataset: str = "data/research/signals_daily"
    qa_artifact_dir: str = "artifacts/wp8"
    default_write_mode: Literal["overwrite_partition"] = "overwrite_partition"
    stable_sort_keys: list[str] = Field(default_factory=lambda: ["as_of_date", "root"])
    clip_min: float = Field(default=-1.0)
    clip_max: float = Field(default=1.0)

    @model_validator(mode="after")
    def validate_bounds(self) -> "SignalsConfig":
        if not self.stable_sort_keys:
            raise ValueError("stable_sort_keys must not be empty")
        if self.clip_max < self.clip_min:
            raise ValueError("clip_max must be >= clip_min")
        return self


class TsmomInvalidReasonsConfig(BaseModel):
    missing_required_feature: str = "missing_required_feature"
    nonfinite_required_feature: str = "nonfinite_required_feature"
    feature_incomplete: str = "feature_incomplete"
    warmup_not_ok: str = "warmup_not_ok"
    root_not_requested: str = "root_not_requested"


class TsmomStrategyConfig(BaseModel):
    strategy_id: str = "tsmom"
    model_id: str = "tsmom_v1"
    signal_version: str = "tsmom_signal_v1"
    formula_artifact_dir: str = "artifacts/strategies/tsmom_v1"
    feature_set_id: str = "features_v1"
    required_features: list[str] = Field(default_factory=lambda: ["ret_21", "ret_63", "ret_252"])
    horizons_days: list[int] = Field(default_factory=lambda: [21, 63, 252])
    weights: list[float] = Field(default_factory=lambda: [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0])
    sign_zero_policy: Literal["zero"] = "zero"
    allow_partial_horizons: bool = False
    require_feature_complete: bool = True
    require_warmup_status_ok: bool = True
    invalid_reasons: TsmomInvalidReasonsConfig = Field(default_factory=TsmomInvalidReasonsConfig)

    @model_validator(mode="after")
    def validate_strategy(self) -> "TsmomStrategyConfig":
        if not self.required_features:
            raise ValueError("required_features must not be empty")
        if len(self.required_features) != len(self.horizons_days):
            raise ValueError("required_features and horizons_days must have the same length")
        if len(self.required_features) != len(self.weights):
            raise ValueError("required_features and weights must have the same length")
        if len(set(self.required_features)) != len(self.required_features):
            raise ValueError("required_features must be unique")
        if any(horizon <= 0 for horizon in self.horizons_days):
            raise ValueError("horizons_days must be positive")
        if any(weight < 0 for weight in self.weights):
            raise ValueError("weights must be non-negative")
        if abs(sum(self.weights) - 1.0) > 1.0e-9:
            raise ValueError("weights must sum to 1.0")
        return self


class StrategiesConfig(BaseModel):
    tsmom: TsmomStrategyConfig = Field(default_factory=TsmomStrategyConfig)


class CpdLstmLabelsConfig(BaseModel):
    horizon_root_trading_days: int = Field(default=1, ge=1)
    return_source: Literal["continuous_daily.daily_return"] = "continuous_daily.daily_return"
    normalize_by: Literal["annualized_vol_60"] = "annualized_vol_60"
    normalized_return_clip_abs: float = Field(default=10.0, gt=0.0)
    min_annualized_vol: float = Field(default=0.01, gt=0.0)


class CpdLstmStandardizationConfig(BaseModel):
    enabled: bool = True
    fit_scope: Literal["train_only"] = "train_only"
    method: Literal["zscore"] = "zscore"
    min_std: float = Field(default=1.0e-6, gt=0.0)
    clip_abs_after_standardization: float = Field(default=10.0, gt=0.0)


class CpdLstmArchitectureConfig(BaseModel):
    input_size: int = Field(default=14, ge=1)
    hidden_size: int = Field(default=64, ge=1)
    num_layers: int = Field(default=1, ge=1)
    dropout_after_lstm: float = Field(default=0.20, ge=0.0, lt=1.0)
    head_hidden_size: int = Field(default=32, ge=1)
    output_activation: Literal["tanh"] = "tanh"
    output_clip_abs: float = Field(default=1.0, gt=0.0)


class CpdLstmTrainingConfig(BaseModel):
    optimizer: Literal["adamw"] = "adamw"
    learning_rate: float = Field(default=0.0003, gt=0.0)
    weight_decay: float = Field(default=0.0001, ge=0.0)
    max_epochs: int = Field(default=50, ge=1)
    min_epochs: int = Field(default=5, ge=1)
    early_stopping_patience: int = Field(default=8, ge=0)
    gradient_clip_norm: float = Field(default=1.0, gt=0.0)
    seed: int = 42
    deterministic_mode: Literal["strict", "warn", "off"] = "warn"
    device: str = "auto"
    dtype: Literal["float32"] = "float32"
    batch_mode: Literal["full_panel"] = "full_panel"
    validation_metric: Literal["sharpe_ex_cost"] = "sharpe_ex_cost"
    cost_bps_default: float = Field(default=2.0, ge=0.0)
    turnover_cost_multiplier: float = Field(default=1.0, ge=0.0)
    signal_l2_penalty: float = Field(default=1.0e-4, ge=0.0)
    turnover_l1_penalty: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def validate_epochs(self) -> "CpdLstmTrainingConfig":
        if self.max_epochs < self.min_epochs:
            raise ValueError("max_epochs must be >= min_epochs")
        return self


class CpdLstmSmokeConfig(BaseModel):
    max_epochs: int = Field(default=3, ge=1)
    min_epochs: int = Field(default=1, ge=1)
    roots: list[str] = Field(default_factory=lambda: ["ES", "NQ"])
    n_days: int = Field(default=180, ge=80)

    @model_validator(mode="after")
    def validate_smoke(self) -> "CpdLstmSmokeConfig":
        if self.max_epochs < self.min_epochs:
            raise ValueError("smoke.max_epochs must be >= smoke.min_epochs")
        if not self.roots:
            raise ValueError("smoke.roots must not be empty")
        return self


class CpdLstmModelConfig(BaseModel):
    model_family: Literal["cpd_lstm_v1"] = "cpd_lstm_v1"
    strategy_id: Literal["cpd_lstm"] = "cpd_lstm"
    feature_set_id: str = "features_v1"
    series_id: str = "v1_back_ratio_settle"
    sequence_length: int = Field(default=63, ge=2)
    annualization_factor: int = Field(default=252, ge=1)
    epsilon: float = Field(default=1.0e-12, gt=0.0)
    feature_order: list[str] = Field(default_factory=lambda: list(CPD_LSTM_FEATURE_ORDER))
    labels: CpdLstmLabelsConfig = Field(default_factory=CpdLstmLabelsConfig)
    standardization: CpdLstmStandardizationConfig = Field(default_factory=CpdLstmStandardizationConfig)
    architecture: CpdLstmArchitectureConfig = Field(default_factory=CpdLstmArchitectureConfig)
    training: CpdLstmTrainingConfig = Field(default_factory=CpdLstmTrainingConfig)
    smoke: CpdLstmSmokeConfig = Field(default_factory=CpdLstmSmokeConfig)

    @model_validator(mode="after")
    def validate_model(self) -> "CpdLstmModelConfig":
        if self.feature_order != CPD_LSTM_FEATURE_ORDER:
            raise ValueError("models.cpd_lstm.feature_order must match features_v1 order")
        if self.architecture.input_size != len(self.feature_order):
            raise ValueError("architecture.input_size must match feature_order length")
        return self


class ModelsConfig(BaseModel):
    cpd_lstm: CpdLstmModelConfig = Field(default_factory=CpdLstmModelConfig)


class AppConfig(BaseModel):
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    costs: CostsConfig = Field(default_factory=CostsConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    roll: RollConfig = Field(default_factory=RollConfig)
    continuous: ContinuousConfig = Field(default_factory=ContinuousConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    signals: SignalsConfig = Field(default_factory=SignalsConfig)
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)



class DatabentoClientConfig(BaseModel):
    dataset: str = "GLBX.MDP3"
    key_env: str = "DATABENTO_API_KEY"
    default_stype_in: Literal["parent"] = "parent"
    default_stype_out: Literal["raw_symbol"] = "raw_symbol"
    request_timeout_seconds: int = Field(default=300, ge=1)
    max_retries: int = Field(default=3, ge=0)
    retry_backoff_seconds: int = Field(default=5, ge=1)
    max_symbols_per_request: int = Field(default=20, ge=1)
    price_type: Literal["float"] = "float"
    pretty_ts: bool = True
    map_symbols: bool = True


class DatabentoRequestChunkingConfig(BaseModel):
    bootstrap: Literal["year"] = "year"
    incremental: Literal["month"] = "month"


class DatabentoRequestSchemaConfig(BaseModel):
    enabled: bool = True
    required: bool = False
    purpose: str | None = None


class DatabentoRequestsConfig(BaseModel):
    default_start_date: str = "2010-01-01"
    chunking: DatabentoRequestChunkingConfig = Field(default_factory=DatabentoRequestChunkingConfig)
    schemas: dict[str, DatabentoRequestSchemaConfig] = Field(default_factory=dict)


class DatabentoCostControlConfig(BaseModel):
    require_preflight: bool = True
    require_execute_flag_for_paid_pull: bool = True
    default_max_cost_usd: float = Field(default=5.0, ge=0.0)
    smoke_max_days: int = Field(default=10, ge=1)


class DatabentoRawStorageConfig(BaseModel):
    root: str = "data/raw/databento"
    write_mode: Literal["immutable"] = "immutable"
    compression: str = "zstd"


class DatabentoCuratedStorageConfig(BaseModel):
    contract_master: str = "data/curated/contract_master"
    contracts_daily: str = "data/curated/contracts_daily"


class DatabentoNormalizationConfig(BaseModel):
    keep_only_roots_from_instruments_yml: bool = True
    keep_only_outright_futures: bool = True
    settlement_priority: list[str] = Field(default_factory=lambda: [
        "final_settlement",
        "preliminary_settlement",
        "close_fallback",
    ])
    statistics_trade_date_field: str = "ts_ref"
    ohlcv_trade_date_field: str = "ts_event"
    unavailable_price_policy: Literal["mark_missing"] = "mark_missing"


class DatabentoQualityConfig(BaseModel):
    fail_on_empty_required_schema: bool = True
    fail_on_unknown_root: bool = True
    warn_on_missing_settlement: bool = True
    warn_on_zero_volume_lead_candidates: bool = True


class DatabentoIngestConfig(BaseModel):
    version: str
    vendor: str = "databento"
    client: DatabentoClientConfig = Field(default_factory=DatabentoClientConfig)
    requests: DatabentoRequestsConfig = Field(default_factory=DatabentoRequestsConfig)
    cost_control: DatabentoCostControlConfig = Field(default_factory=DatabentoCostControlConfig)
    raw_storage: DatabentoRawStorageConfig = Field(default_factory=DatabentoRawStorageConfig)
    curated_storage: DatabentoCuratedStorageConfig = Field(default_factory=DatabentoCuratedStorageConfig)
    normalization: DatabentoNormalizationConfig = Field(default_factory=DatabentoNormalizationConfig)
    quality: DatabentoQualityConfig = Field(default_factory=DatabentoQualityConfig)


class SchemaColumnConfig(BaseModel):
    name: str
    dtype: str
    nullable: bool = True


class TableSchemaConfig(BaseModel):
    columns: list[SchemaColumnConfig]

    @property
    def column_names(self) -> list[str]:
        return [column.name for column in self.columns]


class DataSchemaConfig(BaseModel):
    version: str
    tables: dict[str, TableSchemaConfig]


def _load_yaml_raw(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_yaml(path: str | Path) -> AppConfig:
    return AppConfig.model_validate(_load_yaml_raw(path))


def load_databento_ingest_yaml(path: str | Path) -> DatabentoIngestConfig:
    return DatabentoIngestConfig.model_validate(_load_yaml_raw(path))


def load_data_schema_yaml(path: str | Path) -> DataSchemaConfig:
    return DataSchemaConfig.model_validate(_load_yaml_raw(path))
