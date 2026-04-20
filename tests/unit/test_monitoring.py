from pathlib import Path

from cpdshadow.config import load_yaml
from cpdshadow.monitoring import (
    DataQualitySnapshot,
    ExecutionHealthSnapshot,
    ModelHealthSnapshot,
    StrategyHealthSnapshot,
    evaluate_monitoring_state,
    risk_health_from_portfolio_diagnostics,
    signal_saturation_fraction,
    cross_sectional_signal_std,
)
from cpdshadow.portfolio import build_target_positions
from cpdshadow.risk import SizingInput



def test_nominal_monitoring_state_runs_champion() -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    decision = evaluate_monitoring_state(
        config=cfg.monitoring,
        data_quality=DataQualitySnapshot(),
        model_health=ModelHealthSnapshot(),
        execution_health=ExecutionHealthSnapshot(),
        strategy_health=StrategyHealthSnapshot(),
    )
    assert decision.final_action == "run_cpd_lstm"
    assert decision.kill_switch_engaged is False
    assert decision.fallback_active is False
    assert decision.alerts == ()



def test_missing_settlement_triggers_hold() -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    decision = evaluate_monitoring_state(
        config=cfg.monitoring,
        data_quality=DataQualitySnapshot(missing_settlement_roots=("ES",)),
    )
    assert decision.final_action == "hold"
    assert decision.can_rebalance is False
    assert decision.kill_switch_engaged is True
    assert decision.alerts[0].code == "DQ_MISSING_SETTLEMENT"



def test_cpd_service_failure_triggers_fallback() -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    decision = evaluate_monitoring_state(
        config=cfg.monitoring,
        model_health=ModelHealthSnapshot(cpd_service_ok=False),
    )
    assert decision.final_action == "fallback_tsmom"
    assert decision.fallback_active is True
    assert any(alert.code == "MODEL_CPD_SERVICE_FAILURE" for alert in decision.alerts)



def test_broker_ambiguity_triggers_reduce_only() -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    decision = evaluate_monitoring_state(
        config=cfg.monitoring,
        execution_health=ExecutionHealthSnapshot(broker_state_known=False),
    )
    assert decision.final_action == "reduce_only"
    assert decision.reduce_only is True
    assert decision.kill_switch_engaged is True



def test_strategy_degradation_triggers_fallback() -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    decision = evaluate_monitoring_state(
        config=cfg.monitoring,
        strategy_health=StrategyHealthSnapshot(
            cpd_60d_net_sharpe=-0.40,
            tsmom_60d_net_sharpe=0.10,
            consecutive_cpd_underperformance_windows=2,
            reversal_edge_usd=-100.0,
        ),
    )
    assert decision.final_action == "fallback_tsmom"
    assert any(alert.code == "STRAT_REVERSAL_EDGE_LOST" for alert in decision.alerts)



def test_signal_health_helpers() -> None:
    signals = [0.99, -0.97, 0.10, -0.05]
    assert signal_saturation_fraction(signals) == 0.5
    assert cross_sectional_signal_std(signals) > 0.0



def test_risk_snapshot_from_portfolio_diagnostics() -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    inputs = [
        SizingInput(
            root="ES",
            asset_class="equity_index",
            signal=1.0,
            annualized_vol=0.20,
            lead_price=5000.0,
            quote_multiplier_to_usd_notional=50.0,
            initial_margin_per_contract_usd=15000.0,
            tick_value_usd=12.5,
            current_contracts=0,
        ),
        SizingInput(
            root="ZN",
            asset_class="rates",
            signal=1.0,
            annualized_vol=0.08,
            lead_price=110.0,
            quote_multiplier_to_usd_notional=1000.0,
            initial_margin_per_contract_usd=3500.0,
            tick_value_usd=15.625,
            current_contracts=0,
        ),
    ]
    result = build_target_positions(inputs, nav_usd=1_000_000.0, risk_config=cfg.risk, costs_config=cfg.costs)
    snapshot = risk_health_from_portfolio_diagnostics(
        result.diagnostics,
        nav_usd=1_000_000.0,
        realized_vol_20d=0.11,
        position_reconciliation_mismatch_count=0,
    )
    assert snapshot.margin_usage_fraction == result.diagnostics.total_initial_margin_usd / 1_000_000.0
    assert snapshot.realized_vol_20d == 0.11
