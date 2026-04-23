from pathlib import Path

from cpdshadow.config import load_data_schema_yaml, load_databento_ingest_yaml, load_yaml


def test_load_base_config() -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    assert cfg.runtime.timezone == "Asia/Tokyo"
    assert cfg.risk.target_annual_vol == 0.10
    assert cfg.costs.roll_extra_ticks_per_contract_side.energy == 0.75
    assert cfg.monitoring.model_health.max_feature_psi == 0.20
    assert cfg.roll.policy_version == "volume3_hardroll_v1"
    assert cfg.roll.effective_lag_trading_days == 1
    assert cfg.continuous.series_id == "v1_back_ratio_settle"
    assert cfg.continuous.builder_version == "continuous_builder_v1"
    assert cfg.features.feature_set_id == "features_v1"
    assert cfg.features.cpd.method == "two_sample_t_v1"
    assert cfg.signals.output_dataset == "data/research/signals_daily"
    assert cfg.strategies.tsmom.model_id == "tsmom_v1"


def test_load_wp4_ingest_config_and_schema() -> None:
    ingest_cfg = load_databento_ingest_yaml(Path("config/databento.ingest.yml"))
    schema_cfg = load_data_schema_yaml(Path("config/data_schema.yml"))
    assert ingest_cfg.client.dataset == "GLBX.MDP3"
    assert ingest_cfg.requests.schemas["definition"].required is True
    assert "contracts_daily" in schema_cfg.tables
    assert "lead_map" in schema_cfg.tables
    assert "roll_events" in schema_cfg.tables
    assert "continuous_daily" in schema_cfg.tables
    assert "cpd_daily" in schema_cfg.tables
    assert "features_daily" in schema_cfg.tables
    assert "signals_daily" in schema_cfg.tables
