from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
import yaml

from cpdshadow.instruments import AssetClass


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


class AppConfig(BaseModel):
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    costs: CostsConfig = Field(default_factory=CostsConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    roll: RollConfig = Field(default_factory=RollConfig)



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
