from pathlib import Path

from cpdshadow.config import load_data_schema_yaml, load_databento_ingest_yaml, load_yaml



def test_load_base_config() -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    assert cfg.runtime.timezone == "Asia/Tokyo"
    assert cfg.risk.target_annual_vol == 0.10
    assert cfg.costs.roll_extra_ticks_per_contract_side.energy == 0.75
    assert cfg.monitoring.model_health.max_feature_psi == 0.20


def test_load_wp4_ingest_config_and_schema() -> None:
    ingest_cfg = load_databento_ingest_yaml(Path("config/databento.ingest.yml"))
    schema_cfg = load_data_schema_yaml(Path("config/data_schema.yml"))
    assert ingest_cfg.client.dataset == "GLBX.MDP3"
    assert ingest_cfg.requests.schemas["definition"].required is True
    assert "contracts_daily" in schema_cfg.tables
