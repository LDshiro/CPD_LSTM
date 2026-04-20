from __future__ import annotations

from dataclasses import dataclass

from cpdshadow.config import CostsConfig
from cpdshadow.instruments import AssetClass


@dataclass(frozen=True)
class CostProfile:
    asset_class: AssetClass
    commission_per_contract_side_usd: float
    slippage_ticks_per_side: float
    roll_extra_ticks_per_contract_side: float


@dataclass(frozen=True)
class TradeCostBreakdown:
    quantity_contracts: float
    commission_usd: float
    slippage_usd: float
    roll_extra_usd: float
    total_usd: float



def resolve_cost_profile(asset_class: AssetClass, costs: CostsConfig) -> CostProfile:
    return CostProfile(
        asset_class=asset_class,
        commission_per_contract_side_usd=costs.commission_per_contract_side_usd.get(asset_class),
        slippage_ticks_per_side=costs.slippage_ticks_per_side.get(asset_class),
        roll_extra_ticks_per_contract_side=costs.roll_extra_ticks_per_contract_side.get(asset_class),
    )



def estimate_rebalance_cost_usd(
    quantity_contracts: float,
    tick_value_usd: float,
    profile: CostProfile,
) -> TradeCostBreakdown:
    quantity = abs(float(quantity_contracts))
    commission = quantity * profile.commission_per_contract_side_usd
    slippage = quantity * profile.slippage_ticks_per_side * tick_value_usd
    total = commission + slippage
    return TradeCostBreakdown(
        quantity_contracts=quantity,
        commission_usd=commission,
        slippage_usd=slippage,
        roll_extra_usd=0.0,
        total_usd=total,
    )



def estimate_roll_cost_usd(
    quantity_contracts: float,
    tick_value_usd: float,
    profile: CostProfile,
) -> TradeCostBreakdown:
    quantity = abs(float(quantity_contracts))
    commission = 2.0 * quantity * profile.commission_per_contract_side_usd
    slippage = 2.0 * quantity * profile.slippage_ticks_per_side * tick_value_usd
    roll_extra = 2.0 * quantity * profile.roll_extra_ticks_per_contract_side * tick_value_usd
    total = commission + slippage + roll_extra
    return TradeCostBreakdown(
        quantity_contracts=quantity,
        commission_usd=commission,
        slippage_usd=slippage,
        roll_extra_usd=roll_extra,
        total_usd=total,
    )
