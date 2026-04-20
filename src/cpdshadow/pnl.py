from __future__ import annotations

from cpdshadow.config import CostsConfig
from cpdshadow.portfolio import DailyPnlInput, PositionPnlAttribution, attribute_daily_pnl as attribute_portfolio_daily_pnl

__all__ = ["DailyPnlInput", "PositionPnlAttribution", "attribute_daily_pnl"]



def attribute_daily_pnl(costs_config: CostsConfig, inputs: DailyPnlInput) -> PositionPnlAttribution:
    """Compatibility wrapper for single-position PnL attribution.

    The canonical implementation lives in ``cpdshadow.portfolio.attribute_daily_pnl`` and
    operates on a list of positions. This wrapper preserves a single-position entry point
    for lightweight tests and utilities.
    """
    result = attribute_portfolio_daily_pnl([inputs], costs_config=costs_config)
    return result.positions[0]
