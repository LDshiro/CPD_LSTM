from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

from cpdshadow.config import RiskConfig
from cpdshadow.instruments import AssetClass


@dataclass(frozen=True)
class SizingInput:
    root: str
    asset_class: AssetClass
    signal: float
    annualized_vol: float
    lead_price: float
    quote_multiplier_to_usd_notional: float
    initial_margin_per_contract_usd: float
    tick_value_usd: float
    current_contracts: int = 0
    roll_contracts: int = 0

    def __post_init__(self) -> None:
        if not math.isfinite(self.signal):
            raise ValueError(f"non-finite signal for {self.root}")
        if self.annualized_vol < 0 or not math.isfinite(self.annualized_vol):
            raise ValueError(f"invalid annualized_vol for {self.root}")
        if self.lead_price < 0 or not math.isfinite(self.lead_price):
            raise ValueError(f"invalid lead_price for {self.root}")
        if self.quote_multiplier_to_usd_notional <= 0 or not math.isfinite(self.quote_multiplier_to_usd_notional):
            raise ValueError(f"invalid quote multiplier for {self.root}")
        if self.initial_margin_per_contract_usd < 0 or not math.isfinite(self.initial_margin_per_contract_usd):
            raise ValueError(f"invalid initial margin for {self.root}")
        if self.tick_value_usd < 0 or not math.isfinite(self.tick_value_usd):
            raise ValueError(f"invalid tick value for {self.root}")


@dataclass(frozen=True)
class RiskMetrics:
    annualized_dollar_risk_per_contract_usd: float
    position_annualized_dollar_risk_usd: float
    initial_margin_required_usd: float


@dataclass(frozen=True)
class ConstraintSummary:
    root_caps_triggered: tuple[str, ...]
    class_caps_triggered: tuple[str, ...]
    margin_soft_scale_factor: float
    margin_soft_cap_triggered: bool
    margin_hard_cap_pre_scale_breached: bool
    integer_reduction_steps: int



def round_half_away_from_zero(value: float) -> int:
    magnitude = math.floor(abs(value) + 0.5)
    return int(math.copysign(magnitude, value))



def clip_signal(signal: float) -> float:
    return max(-1.0, min(1.0, signal))



def annualized_dollar_risk_per_contract(state: SizingInput) -> float:
    return state.annualized_vol * state.lead_price * state.quote_multiplier_to_usd_notional



def compute_risk_metrics(state: SizingInput, contracts: float) -> RiskMetrics:
    ann_per_contract = annualized_dollar_risk_per_contract(state)
    return RiskMetrics(
        annualized_dollar_risk_per_contract_usd=ann_per_contract,
        position_annualized_dollar_risk_usd=abs(contracts) * ann_per_contract,
        initial_margin_required_usd=abs(contracts) * state.initial_margin_per_contract_usd,
    )



def active_states(inputs: Iterable[SizingInput], threshold: float) -> list[SizingInput]:
    result: list[SizingInput] = []
    for state in inputs:
        valid = state.annualized_vol > 0 and state.lead_price > 0
        if valid and abs(clip_signal(state.signal)) >= threshold:
            result.append(state)
    return result



def compute_raw_contract_targets(
    inputs: Iterable[SizingInput],
    nav_usd: float,
    risk_config: RiskConfig,
) -> tuple[dict[str, float], int, float, float]:
    active = active_states(inputs, risk_config.active_signal_threshold)
    active_count = len(active)
    if active_count == 0:
        return {}, 0, nav_usd * risk_config.target_annual_vol, 0.0

    total_risk_budget = nav_usd * risk_config.target_annual_vol
    per_asset_risk_budget = total_risk_budget / math.sqrt(active_count)

    raw_targets: dict[str, float] = {}
    for state in active:
        contract_ann_risk = annualized_dollar_risk_per_contract(state)
        if contract_ann_risk <= 0:
            raw_targets[state.root] = 0.0
            continue
        raw_targets[state.root] = (
            clip_signal(state.signal) * per_asset_risk_budget / contract_ann_risk
        )
    return raw_targets, active_count, total_risk_budget, per_asset_risk_budget



def apply_absolute_risk_caps(
    inputs: Iterable[SizingInput],
    contracts_by_root: dict[str, float],
    nav_usd: float,
    risk_config: RiskConfig,
) -> tuple[dict[str, float], ConstraintSummary]:
    states = {state.root: state for state in inputs}
    scaled = dict(contracts_by_root)
    root_hits: list[str] = []
    class_hits: list[str] = []

    root_cap_usd = nav_usd * risk_config.single_root_soft_cap_fraction_of_nav
    class_cap_usd = nav_usd * risk_config.asset_class_soft_cap_fraction_of_nav

    for root, contracts in list(scaled.items()):
        metric = compute_risk_metrics(states[root], contracts)
        if metric.position_annualized_dollar_risk_usd > root_cap_usd and metric.position_annualized_dollar_risk_usd > 0:
            factor = root_cap_usd / metric.position_annualized_dollar_risk_usd
            scaled[root] = contracts * factor
            root_hits.append(root)

    by_class: dict[AssetClass, list[str]] = {}
    for root, state in states.items():
        if root in scaled:
            by_class.setdefault(state.asset_class, []).append(root)

    for asset_class, roots in by_class.items():
        class_risk = sum(
            compute_risk_metrics(states[root], scaled.get(root, 0.0)).position_annualized_dollar_risk_usd
            for root in roots
        )
        if class_risk > class_cap_usd and class_risk > 0:
            factor = class_cap_usd / class_risk
            for root in roots:
                scaled[root] = scaled.get(root, 0.0) * factor
            class_hits.append(asset_class)

    summary = ConstraintSummary(
        root_caps_triggered=tuple(sorted(set(root_hits))),
        class_caps_triggered=tuple(sorted(set(class_hits))),
        margin_soft_scale_factor=1.0,
        margin_soft_cap_triggered=False,
        margin_hard_cap_pre_scale_breached=False,
        integer_reduction_steps=0,
    )
    return scaled, summary



def apply_margin_soft_cap(
    inputs: Iterable[SizingInput],
    contracts_by_root: dict[str, float],
    nav_usd: float,
    risk_config: RiskConfig,
    *,
    prior_summary: ConstraintSummary | None = None,
) -> tuple[dict[str, float], ConstraintSummary]:
    states = {state.root: state for state in inputs}
    required = sum(
        compute_risk_metrics(states[root], contracts).initial_margin_required_usd
        for root, contracts in contracts_by_root.items()
    )
    soft_cap_usd = nav_usd * risk_config.initial_margin_soft_cap_fraction_of_nav
    hard_cap_usd = nav_usd * risk_config.initial_margin_hard_cap_fraction_of_nav
    factor = 1.0
    scaled = dict(contracts_by_root)
    soft_triggered = required > soft_cap_usd and required > 0
    if soft_triggered:
        factor = soft_cap_usd / required
        for root in list(scaled):
            scaled[root] *= factor

    base = prior_summary or ConstraintSummary(
        root_caps_triggered=tuple(),
        class_caps_triggered=tuple(),
        margin_soft_scale_factor=1.0,
        margin_soft_cap_triggered=False,
        margin_hard_cap_pre_scale_breached=False,
        integer_reduction_steps=0,
    )
    summary = ConstraintSummary(
        root_caps_triggered=base.root_caps_triggered,
        class_caps_triggered=base.class_caps_triggered,
        margin_soft_scale_factor=factor,
        margin_soft_cap_triggered=soft_triggered,
        margin_hard_cap_pre_scale_breached=required > hard_cap_usd,
        integer_reduction_steps=base.integer_reduction_steps,
    )
    return scaled, summary



def post_rounding_reduce_to_constraints(
    inputs: Iterable[SizingInput],
    contracts_by_root: dict[str, int],
    nav_usd: float,
    risk_config: RiskConfig,
) -> tuple[dict[str, int], int]:
    states = {state.root: state for state in inputs}
    reduced = dict(contracts_by_root)
    root_cap_usd = nav_usd * risk_config.single_root_soft_cap_fraction_of_nav
    class_cap_usd = nav_usd * risk_config.asset_class_soft_cap_fraction_of_nav
    margin_soft_cap_usd = nav_usd * risk_config.initial_margin_soft_cap_fraction_of_nav
    steps = 0

    def total_margin() -> float:
        return sum(
            compute_risk_metrics(states[root], qty).initial_margin_required_usd
            for root, qty in reduced.items()
        )

    def root_risk(root: str) -> float:
        return compute_risk_metrics(states[root], reduced.get(root, 0)).position_annualized_dollar_risk_usd

    def class_risk(asset_class: AssetClass) -> float:
        return sum(
            root_risk(root)
            for root, state in states.items()
            if state.asset_class == asset_class and root in reduced
        )

    def reduce_one_contract(root: str) -> bool:
        nonlocal steps
        qty = reduced.get(root, 0)
        if qty == 0:
            return False
        reduced[root] = qty - int(math.copysign(1, qty))
        steps += 1
        return True

    for _ in range(risk_config.max_integer_reduction_steps):
        if total_margin() > margin_soft_cap_usd:
            root = max(reduced, key=lambda item: compute_risk_metrics(states[item], reduced[item]).initial_margin_required_usd)
            if not reduce_one_contract(root):
                break
            continue

        offending_root = next((root for root in reduced if root_risk(root) > root_cap_usd), None)
        if offending_root is not None:
            if not reduce_one_contract(offending_root):
                break
            continue

        offending_class = next(
            (
                asset_class
                for asset_class in {state.asset_class for state in states.values()}
                if class_risk(asset_class) > class_cap_usd
            ),
            None,
        )
        if offending_class is not None:
            roots = [root for root, state in states.items() if state.asset_class == offending_class]
            root = max(roots, key=root_risk)
            if not reduce_one_contract(root):
                break
            continue
        break

    return reduced, steps
