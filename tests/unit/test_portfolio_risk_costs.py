from math import isclose
from pathlib import Path

from cpdshadow.config import CostsConfig, RiskConfig, load_yaml
from cpdshadow.costs import (
    estimate_rebalance_cost_usd,
    estimate_roll_cost_usd,
    resolve_cost_profile,
)
from cpdshadow.portfolio import DailyPnlInput, attribute_daily_pnl, build_target_positions
from cpdshadow.risk import SizingInput, round_half_away_from_zero



def test_round_half_away_from_zero() -> None:
    assert round_half_away_from_zero(1.50) == 2
    assert round_half_away_from_zero(1.49) == 1
    assert round_half_away_from_zero(-1.50) == -2
    assert round_half_away_from_zero(-1.49) == -1



def test_rebalance_and_roll_costs() -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    profile = resolve_cost_profile("equity_index", cfg.costs)
    rebalance = estimate_rebalance_cost_usd(2, 12.5, profile)
    roll = estimate_roll_cost_usd(3, 12.5, profile)

    assert isclose(rebalance.total_usd, 15.5)
    assert isclose(roll.total_usd, 65.25)



def test_build_target_positions_basic() -> None:
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
    positions = {position.root: position for position in result.positions}

    assert result.diagnostics.active_count == 2
    assert positions["ES"].final_contracts == 1
    assert positions["ZN"].final_contracts == 8
    assert result.diagnostics.total_initial_margin_usd == 43_000.0



def test_root_and_class_caps_can_clip_positions() -> None:
    inputs = [
        SizingInput(
            root="CL",
            asset_class="energy",
            signal=1.0,
            annualized_vol=0.25,
            lead_price=80.0,
            quote_multiplier_to_usd_notional=1000.0,
            initial_margin_per_contract_usd=12000.0,
            tick_value_usd=10.0,
            current_contracts=0,
        ),
        SizingInput(
            root="NG",
            asset_class="energy",
            signal=1.0,
            annualized_vol=0.60,
            lead_price=3.0,
            quote_multiplier_to_usd_notional=10000.0,
            initial_margin_per_contract_usd=9000.0,
            tick_value_usd=10.0,
            current_contracts=0,
        ),
    ]
    risk = RiskConfig(
        target_annual_vol=0.20,
        vol_lookback_days=60,
        active_signal_threshold=0.05,
        single_root_soft_cap_fraction_of_nav=0.03,
        asset_class_soft_cap_fraction_of_nav=0.05,
        initial_margin_soft_cap_fraction_of_nav=0.80,
        initial_margin_hard_cap_fraction_of_nav=0.90,
        contract_rounding="half_away_from_zero",
        post_rounding_recheck=True,
        max_integer_reduction_steps=500,
    )
    costs = CostsConfig()
    result = build_target_positions(inputs, nav_usd=1_000_000.0, risk_config=risk, costs_config=costs)
    positions = {position.root: position for position in result.positions}
    total_energy_risk = sum(position.annualized_dollar_risk_target_usd for position in result.positions)

    assert "CL" in result.diagnostics.root_caps_triggered or "NG" in result.diagnostics.root_caps_triggered
    assert "energy" in result.diagnostics.class_caps_triggered
    assert total_energy_risk <= 50_000.0 + 1e-9
    assert positions["CL"].final_contracts <= 1



def test_margin_soft_cap_scales_down_targets() -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    risk = cfg.risk.model_copy(update={
        "initial_margin_soft_cap_fraction_of_nav": 0.03,
        "initial_margin_hard_cap_fraction_of_nav": 0.04,
    })
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
        ),
        SizingInput(
            root="NQ",
            asset_class="equity_index",
            signal=1.0,
            annualized_vol=0.25,
            lead_price=19000.0,
            quote_multiplier_to_usd_notional=20.0,
            initial_margin_per_contract_usd=22000.0,
            tick_value_usd=5.0,
        ),
    ]
    result = build_target_positions(inputs, nav_usd=1_000_000.0, risk_config=risk, costs_config=cfg.costs)

    assert result.diagnostics.margin_soft_cap_triggered is True
    assert result.diagnostics.total_initial_margin_usd <= 30_000.0



def test_daily_pnl_attribution() -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    pnl = attribute_daily_pnl(
        [
            DailyPnlInput(
                root="ES",
                asset_class="equity_index",
                previous_contracts=2,
                previous_price=5000.0,
                current_price=5010.0,
                quote_multiplier_to_usd_notional=50.0,
                tick_value_usd=12.5,
                delta_contracts=1,
                roll_contracts=0,
            )
        ],
        costs_config=cfg.costs,
    )
    assert isclose(pnl.total_gross_pnl_usd, 1000.0)
    assert isclose(pnl.total_trade_cost_usd, 7.75)
    assert isclose(pnl.total_net_pnl_usd, 992.25)
