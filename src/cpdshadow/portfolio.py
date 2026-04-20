from __future__ import annotations

from dataclasses import dataclass

from cpdshadow.config import CostsConfig, RiskConfig
from cpdshadow.costs import (
    estimate_rebalance_cost_usd,
    estimate_roll_cost_usd,
    resolve_cost_profile,
)
from cpdshadow.instruments import AssetClass
from cpdshadow.risk import (
    ConstraintSummary,
    SizingInput,
    active_states,
    apply_absolute_risk_caps,
    apply_margin_soft_cap,
    annualized_dollar_risk_per_contract,
    compute_raw_contract_targets,
    compute_risk_metrics,
    post_rounding_reduce_to_constraints,
    round_half_away_from_zero,
)


@dataclass(frozen=True)
class TargetPosition:
    root: str
    asset_class: AssetClass
    signal: float
    current_contracts: int
    raw_contracts: float
    capped_contracts: float
    final_contracts: int
    delta_contracts: int
    roll_contracts: int
    annualized_dollar_risk_per_contract_usd: float
    annualized_dollar_risk_target_usd: float
    initial_margin_required_usd: float
    estimated_rebalance_cost_usd: float
    estimated_roll_cost_usd: float
    estimated_total_trade_cost_usd: float


@dataclass(frozen=True)
class PortfolioDiagnostics:
    active_count: int
    risk_budget_total_usd_annualized: float
    raw_per_asset_risk_budget_usd_annualized: float
    total_annualized_dollar_risk_usd: float
    total_initial_margin_usd: float
    root_caps_triggered: tuple[str, ...]
    class_caps_triggered: tuple[str, ...]
    margin_soft_scale_factor: float
    margin_soft_cap_triggered: bool
    margin_hard_cap_pre_scale_breached: bool
    integer_reduction_steps: int


@dataclass(frozen=True)
class PortfolioSizingResult:
    nav_usd: float
    positions: list[TargetPosition]
    diagnostics: PortfolioDiagnostics


@dataclass(frozen=True)
class DailyPnlInput:
    root: str
    asset_class: AssetClass
    previous_contracts: int
    previous_price: float
    current_price: float
    quote_multiplier_to_usd_notional: float
    tick_value_usd: float
    delta_contracts: int = 0
    roll_contracts: int = 0


@dataclass(frozen=True)
class PositionPnlAttribution:
    root: str
    gross_mark_to_market_pnl_usd: float
    rebalance_cost_usd: float
    roll_cost_usd: float
    net_pnl_usd: float


@dataclass(frozen=True)
class PortfolioPnlAttribution:
    positions: list[PositionPnlAttribution]
    total_gross_pnl_usd: float
    total_trade_cost_usd: float
    total_net_pnl_usd: float



def _merge_constraint_summaries(*summaries: ConstraintSummary) -> ConstraintSummary:
    root_hits: set[str] = set()
    class_hits: set[str] = set()
    margin_soft_scale_factor = 1.0
    margin_soft_cap_triggered = False
    margin_hard_cap_pre_scale_breached = False
    integer_reduction_steps = 0
    for summary in summaries:
        root_hits.update(summary.root_caps_triggered)
        class_hits.update(summary.class_caps_triggered)
        margin_soft_scale_factor *= summary.margin_soft_scale_factor
        margin_soft_cap_triggered = margin_soft_cap_triggered or summary.margin_soft_cap_triggered
        margin_hard_cap_pre_scale_breached = (
            margin_hard_cap_pre_scale_breached or summary.margin_hard_cap_pre_scale_breached
        )
        integer_reduction_steps += summary.integer_reduction_steps
    return ConstraintSummary(
        root_caps_triggered=tuple(sorted(root_hits)),
        class_caps_triggered=tuple(sorted(class_hits)),
        margin_soft_scale_factor=margin_soft_scale_factor,
        margin_soft_cap_triggered=margin_soft_cap_triggered,
        margin_hard_cap_pre_scale_breached=margin_hard_cap_pre_scale_breached,
        integer_reduction_steps=integer_reduction_steps,
    )



def build_target_positions(
    inputs: list[SizingInput],
    nav_usd: float,
    risk_config: RiskConfig,
    costs_config: CostsConfig,
) -> PortfolioSizingResult:
    if nav_usd <= 0:
        raise ValueError("nav_usd must be positive")

    states_by_root = {state.root: state for state in inputs}
    raw_targets, active_count, total_risk_budget, per_asset_budget = compute_raw_contract_targets(
        inputs=inputs,
        nav_usd=nav_usd,
        risk_config=risk_config,
    )
    capped_targets, cap_summary = apply_absolute_risk_caps(
        inputs=inputs,
        contracts_by_root=raw_targets,
        nav_usd=nav_usd,
        risk_config=risk_config,
    )
    scaled_targets, margin_summary = apply_margin_soft_cap(
        inputs=inputs,
        contracts_by_root=capped_targets,
        nav_usd=nav_usd,
        risk_config=risk_config,
        prior_summary=cap_summary,
    )

    rounded_targets = {
        root: round_half_away_from_zero(contracts)
        for root, contracts in scaled_targets.items()
    }
    integer_steps = 0
    if risk_config.post_rounding_recheck:
        rounded_targets, integer_steps = post_rounding_reduce_to_constraints(
            inputs=inputs,
            contracts_by_root=rounded_targets,
            nav_usd=nav_usd,
            risk_config=risk_config,
        )

    combined_summary = _merge_constraint_summaries(
        cap_summary,
        margin_summary,
        ConstraintSummary(
            root_caps_triggered=tuple(),
            class_caps_triggered=tuple(),
            margin_soft_scale_factor=1.0,
            margin_soft_cap_triggered=False,
            margin_hard_cap_pre_scale_breached=False,
            integer_reduction_steps=integer_steps,
        ),
    )

    positions: list[TargetPosition] = []
    total_ann_risk = 0.0
    total_margin = 0.0
    for state in inputs:
        raw_contracts = raw_targets.get(state.root, 0.0)
        capped_contracts = scaled_targets.get(state.root, 0.0)
        final_contracts = rounded_targets.get(state.root, 0)
        metrics = compute_risk_metrics(state, final_contracts)
        total_ann_risk += metrics.position_annualized_dollar_risk_usd
        total_margin += metrics.initial_margin_required_usd

        profile = resolve_cost_profile(state.asset_class, costs_config)
        delta_contracts = final_contracts - state.current_contracts
        rebalance_cost = estimate_rebalance_cost_usd(
            quantity_contracts=abs(delta_contracts),
            tick_value_usd=state.tick_value_usd,
            profile=profile,
        )
        roll_cost = estimate_roll_cost_usd(
            quantity_contracts=abs(state.roll_contracts),
            tick_value_usd=state.tick_value_usd,
            profile=profile,
        )

        positions.append(
            TargetPosition(
                root=state.root,
                asset_class=state.asset_class,
                signal=state.signal,
                current_contracts=state.current_contracts,
                raw_contracts=raw_contracts,
                capped_contracts=capped_contracts,
                final_contracts=final_contracts,
                delta_contracts=delta_contracts,
                roll_contracts=state.roll_contracts,
                annualized_dollar_risk_per_contract_usd=annualized_dollar_risk_per_contract(state),
                annualized_dollar_risk_target_usd=metrics.position_annualized_dollar_risk_usd,
                initial_margin_required_usd=metrics.initial_margin_required_usd,
                estimated_rebalance_cost_usd=rebalance_cost.total_usd,
                estimated_roll_cost_usd=roll_cost.total_usd,
                estimated_total_trade_cost_usd=rebalance_cost.total_usd + roll_cost.total_usd,
            )
        )

    diagnostics = PortfolioDiagnostics(
        active_count=active_count,
        risk_budget_total_usd_annualized=total_risk_budget,
        raw_per_asset_risk_budget_usd_annualized=per_asset_budget,
        total_annualized_dollar_risk_usd=total_ann_risk,
        total_initial_margin_usd=total_margin,
        root_caps_triggered=combined_summary.root_caps_triggered,
        class_caps_triggered=combined_summary.class_caps_triggered,
        margin_soft_scale_factor=combined_summary.margin_soft_scale_factor,
        margin_soft_cap_triggered=combined_summary.margin_soft_cap_triggered,
        margin_hard_cap_pre_scale_breached=combined_summary.margin_hard_cap_pre_scale_breached,
        integer_reduction_steps=combined_summary.integer_reduction_steps,
    )
    return PortfolioSizingResult(nav_usd=nav_usd, positions=positions, diagnostics=diagnostics)



def attribute_daily_pnl(
    inputs: list[DailyPnlInput],
    costs_config: CostsConfig,
) -> PortfolioPnlAttribution:
    positions: list[PositionPnlAttribution] = []
    total_gross = 0.0
    total_cost = 0.0

    for item in inputs:
        gross = (
            item.previous_contracts
            * item.quote_multiplier_to_usd_notional
            * (item.current_price - item.previous_price)
        )
        profile = resolve_cost_profile(item.asset_class, costs_config)
        rebalance_cost = estimate_rebalance_cost_usd(
            quantity_contracts=abs(item.delta_contracts),
            tick_value_usd=item.tick_value_usd,
            profile=profile,
        ).total_usd
        roll_cost = estimate_roll_cost_usd(
            quantity_contracts=abs(item.roll_contracts),
            tick_value_usd=item.tick_value_usd,
            profile=profile,
        ).total_usd
        net = gross - rebalance_cost - roll_cost
        total_gross += gross
        total_cost += rebalance_cost + roll_cost
        positions.append(
            PositionPnlAttribution(
                root=item.root,
                gross_mark_to_market_pnl_usd=gross,
                rebalance_cost_usd=rebalance_cost,
                roll_cost_usd=roll_cost,
                net_pnl_usd=net,
            )
        )

    return PortfolioPnlAttribution(
        positions=positions,
        total_gross_pnl_usd=total_gross,
        total_trade_cost_usd=total_cost,
        total_net_pnl_usd=total_gross - total_cost,
    )
