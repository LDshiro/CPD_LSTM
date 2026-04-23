from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from cpdshadow.config import AppConfig
from cpdshadow.instruments import InstrumentMaster
from cpdshadow.portfolio import build_target_positions
from cpdshadow.risk import SizingInput


@dataclass(frozen=True)
class EvaluationInputs:
    signals: pd.DataFrame
    features: pd.DataFrame
    continuous: pd.DataFrame
    app_config: AppConfig
    instrument_master: InstrumentMaster
    nav_usd: float
    initial_margin_fraction_of_notional: float


def evaluate_targets_and_pnl(inputs: EvaluationInputs) -> tuple[pd.DataFrame, pd.DataFrame]:
    signals = _prepare_signals(inputs.signals)
    if signals.empty:
        return (
            pd.DataFrame(columns=_TARGET_COLUMNS),
            pd.DataFrame(columns=_PNL_COLUMNS),
        )
    features = _feature_lookup(inputs.features)
    prices = _price_lookup(inputs.continuous)
    instruments = {item.root: item for item in inputs.instrument_master.instruments}
    previous_contracts: dict[tuple[str, str, str], int] = {}
    target_rows: list[dict[str, object]] = []
    pnl_rows: list[dict[str, object]] = []

    group_keys = ["run_id", "fold_id", "strategy_id", "model_id", "as_of_date"]
    for key, group in signals.groupby(group_keys, sort=True):
        run_id, fold_id, strategy_id, model_id, as_of_date = key
        sizing_inputs: list[SizingInput] = []
        row_by_root: dict[str, pd.Series] = {}
        for _, signal_row in group.iterrows():
            if not bool(signal_row.get("is_valid", False)):
                continue
            root = str(signal_row["root"])
            feature_row = features.get((root, as_of_date))
            price_info = prices.get((root, as_of_date))
            instrument = instruments.get(root)
            if feature_row is None or price_info is None or instrument is None:
                continue
            annualized_vol = float(feature_row["annualized_vol_60"])
            price = float(price_info["adj_settle_price"])
            notional = abs(price * instrument.quote_multiplier_to_usd_notional)
            margin = notional * inputs.initial_margin_fraction_of_notional
            position_key = (strategy_id, model_id, root)
            sizing_inputs.append(
                SizingInput(
                    root=root,
                    asset_class=instrument.asset_class,
                    signal=float(signal_row["signal_clipped"]),
                    annualized_vol=annualized_vol,
                    lead_price=price,
                    quote_multiplier_to_usd_notional=instrument.quote_multiplier_to_usd_notional,
                    initial_margin_per_contract_usd=margin,
                    tick_value_usd=instrument.tick_value_usd,
                    current_contracts=previous_contracts.get(position_key, 0),
                )
            )
            row_by_root[root] = signal_row
        if not sizing_inputs:
            continue
        result = build_target_positions(
            sizing_inputs,
            nav_usd=inputs.nav_usd,
            risk_config=inputs.app_config.risk,
            costs_config=inputs.app_config.costs,
        )
        for position in result.positions:
            signal_row = row_by_root[position.root]
            feature_row = features[(position.root, as_of_date)]
            price_info = prices[(position.root, as_of_date)]
            instrument = instruments[position.root]
            next_info = price_info.get("next")
            previous_key = (strategy_id, model_id, position.root)
            previous_target = previous_contracts.get(previous_key, 0)
            previous_contracts[previous_key] = int(position.final_contracts)
            target_rows.append({
                "run_id": run_id,
                "fold_id": fold_id,
                "strategy_id": strategy_id,
                "model_id": model_id,
                "as_of_date": as_of_date,
                "root": position.root,
                "signal": float(signal_row["signal_clipped"]),
                "annualized_vol_60": float(feature_row["annualized_vol_60"]),
                "price_for_sizing": float(price_info["adj_settle_price"]),
                "multiplier": float(instrument.quote_multiplier_to_usd_notional),
                "target_contracts": int(position.final_contracts),
                "previous_target_contracts": int(previous_target),
                "turnover_contracts": int(abs(position.final_contracts - previous_target)),
                "ex_ante_annualized_dollar_risk": float(
                    position.annualized_dollar_risk_target_usd
                ),
                "modeled_rebalance_cost_usd": float(position.estimated_rebalance_cost_usd),
                "quality_flags": [],
            })
            if next_info is None:
                continue
            gross = (
                int(position.final_contracts)
                * instrument.quote_multiplier_to_usd_notional
                * (float(next_info["adj_settle_price"]) - float(price_info["adj_settle_price"]))
            )
            cost = float(position.estimated_rebalance_cost_usd)
            net = gross - cost
            pnl_rows.append({
                "run_id": run_id,
                "fold_id": fold_id,
                "strategy_id": strategy_id,
                "model_id": model_id,
                "as_of_date": as_of_date,
                "next_date": next_info["as_of_date"],
                "root": position.root,
                "target_contracts": int(position.final_contracts),
                "adj_settle_t": float(price_info["adj_settle_price"]),
                "adj_settle_next": float(next_info["adj_settle_price"]),
                "raw_return_next": float(next_info["daily_return"]),
                "gross_pnl_usd": gross,
                "modeled_cost_usd": cost,
                "net_pnl_usd": net,
                "nav_usd": float(inputs.nav_usd),
                "net_return": net / float(inputs.nav_usd),
            })

    targets = pd.DataFrame(target_rows)
    pnl = pd.DataFrame(pnl_rows)
    if not pnl.empty:
        pnl = pd.concat([pnl, _portfolio_rows(pnl)], ignore_index=True)
        pnl = pnl.sort_values(
            ["run_id", "fold_id", "strategy_id", "model_id", "as_of_date", "root"],
            kind="stable",
        ).reset_index(drop=True)
    if not targets.empty:
        targets = targets.sort_values(
            ["run_id", "fold_id", "strategy_id", "model_id", "as_of_date", "root"],
            kind="stable",
        ).reset_index(drop=True)
    return targets, pnl


def _prepare_signals(signals: pd.DataFrame) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame(columns=_SIGNAL_COLUMNS)
    working = signals.copy()
    working["as_of_date"] = pd.to_datetime(working["as_of_date"]).dt.date
    return working.sort_values(
        ["run_id", "fold_id", "strategy_id", "model_id", "as_of_date", "root"],
        kind="stable",
    ).reset_index(drop=True)


def _feature_lookup(features: pd.DataFrame) -> dict[tuple[str, date], pd.Series]:
    working = features.copy()
    working["as_of_date"] = pd.to_datetime(working["as_of_date"]).dt.date
    return {(str(row["root"]), row["as_of_date"]): row for _, row in working.iterrows()}


def _price_lookup(continuous: pd.DataFrame) -> dict[tuple[str, date], dict[str, object]]:
    working = continuous.copy()
    working["as_of_date"] = pd.to_datetime(working["as_of_date"]).dt.date
    working = working.sort_values(["root", "as_of_date"], kind="stable")
    lookup: dict[tuple[str, date], dict[str, object]] = {}
    for root, group in working.groupby("root", sort=True):
        rows = [row.to_dict() for _, row in group.iterrows()]
        for idx, row in enumerate(rows):
            row["next"] = rows[idx + 1] if idx + 1 < len(rows) else None
            lookup[(str(root), row["as_of_date"])] = row
    return lookup


def _portfolio_rows(pnl: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    keys = ["run_id", "fold_id", "strategy_id", "model_id", "as_of_date"]
    for key, group in pnl.groupby(keys, sort=True):
        run_id, fold_id, strategy_id, model_id, as_of_date = key
        gross = float(pd.to_numeric(group["gross_pnl_usd"], errors="coerce").sum())
        cost = float(pd.to_numeric(group["modeled_cost_usd"], errors="coerce").sum())
        net = float(pd.to_numeric(group["net_pnl_usd"], errors="coerce").sum())
        rows.append({
            "run_id": run_id,
            "fold_id": fold_id,
            "strategy_id": strategy_id,
            "model_id": model_id,
            "as_of_date": as_of_date,
            "next_date": group["next_date"].max(),
            "root": "__PORTFOLIO__",
            "target_contracts": None,
            "adj_settle_t": None,
            "adj_settle_next": None,
            "raw_return_next": None,
            "gross_pnl_usd": gross,
            "modeled_cost_usd": cost,
            "net_pnl_usd": net,
            "nav_usd": float(group["nav_usd"].iloc[0]),
            "net_return": net / float(group["nav_usd"].iloc[0]),
        })
    return pd.DataFrame(rows)


_SIGNAL_COLUMNS = [
    "run_id",
    "fold_id",
    "strategy_id",
    "model_id",
    "as_of_date",
    "root",
    "signal_clipped",
    "is_valid",
]

_TARGET_COLUMNS = [
    "run_id",
    "fold_id",
    "strategy_id",
    "model_id",
    "as_of_date",
    "root",
    "signal",
    "annualized_vol_60",
    "price_for_sizing",
    "multiplier",
    "target_contracts",
    "previous_target_contracts",
    "turnover_contracts",
    "ex_ante_annualized_dollar_risk",
    "modeled_rebalance_cost_usd",
    "quality_flags",
]

_PNL_COLUMNS = [
    "run_id",
    "fold_id",
    "strategy_id",
    "model_id",
    "as_of_date",
    "next_date",
    "root",
    "target_contracts",
    "adj_settle_t",
    "adj_settle_next",
    "raw_return_next",
    "gross_pnl_usd",
    "modeled_cost_usd",
    "net_pnl_usd",
    "nav_usd",
    "net_return",
]
