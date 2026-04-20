from pathlib import Path

from cpdshadow.config import load_yaml
from cpdshadow.portfolio import DailyPnlInput
from cpdshadow.pnl import attribute_daily_pnl
from cpdshadow.risk import SizingInput



def test_single_position_pnl_wrapper() -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    breakdown = attribute_daily_pnl(
        cfg.costs,
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
        ),
    )
    assert breakdown.gross_mark_to_market_pnl_usd == 1000.0
    assert breakdown.net_pnl_usd < breakdown.gross_mark_to_market_pnl_usd



def test_sizing_input_rejects_invalid_multiplier() -> None:
    try:
        SizingInput(
            root="ES",
            asset_class="equity_index",
            signal=1.0,
            annualized_vol=0.20,
            lead_price=5000.0,
            quote_multiplier_to_usd_notional=0.0,
            initial_margin_per_contract_usd=15000.0,
            tick_value_usd=12.5,
        )
    except ValueError as exc:
        assert "quote multiplier" in str(exc)
    else:
        raise AssertionError("SizingInput should reject non-positive multiplier")
