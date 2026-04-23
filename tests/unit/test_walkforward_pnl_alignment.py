from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from cpdshadow.config import load_yaml
from cpdshadow.instruments import load_instrument_master
from cpdshadow.research.pnl_eval import EvaluationInputs, evaluate_targets_and_pnl


def test_signal_at_t_earns_next_observed_adjusted_return() -> None:
    cfg = load_yaml(Path("config/settings.base.yml"))
    instruments = load_instrument_master(Path("config/instruments.yml"))
    dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    signals = pd.DataFrame(
        [
            {
                "run_id": "wf_test",
                "fold_id": "fold_1",
                "strategy_id": "tsmom",
                "model_id": "tsmom_v1",
                "as_of_date": dates[0],
                "root": "ES",
                "signal_clipped": 1.0,
                "is_valid": True,
            },
            {
                "run_id": "wf_test",
                "fold_id": "fold_1",
                "strategy_id": "tsmom",
                "model_id": "tsmom_v1",
                "as_of_date": dates[1],
                "root": "ES",
                "signal_clipped": 1.0,
                "is_valid": True,
            },
        ]
    )
    features = pd.DataFrame(
        [
            {"as_of_date": item, "root": "ES", "annualized_vol_60": 0.10}
            for item in dates
        ]
    )
    continuous = pd.DataFrame(
        [
            {
                "as_of_date": dates[0],
                "root": "ES",
                "adj_settle_price": 100.0,
                "daily_return": None,
            },
            {
                "as_of_date": dates[1],
                "root": "ES",
                "adj_settle_price": 110.0,
                "daily_return": 0.10,
            },
            {
                "as_of_date": dates[2],
                "root": "ES",
                "adj_settle_price": 121.0,
                "daily_return": 0.10,
            },
        ]
    )

    targets, pnl = evaluate_targets_and_pnl(
        EvaluationInputs(
            signals=signals,
            features=features,
            continuous=continuous,
            app_config=cfg,
            instrument_master=instruments,
            nav_usd=1_000_000.0,
            initial_margin_fraction_of_notional=0.10,
        )
    )

    es_pnl = pnl[pnl["root"] == "ES"].sort_values("as_of_date")
    assert list(es_pnl["adj_settle_t"]) == [100.0, 110.0]
    assert list(es_pnl["adj_settle_next"]) == [110.0, 121.0]
    assert list(es_pnl["next_date"]) == [dates[1], dates[2]]
    assert len(targets) == 2

