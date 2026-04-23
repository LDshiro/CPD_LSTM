from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import pandas as pd

from cpdshadow.config import SignalsConfig, TsmomStrategyConfig
from cpdshadow.signals import (
    SIGNALS_DAILY_COLUMNS,
    SignalBuildRequest,
    SignalBuildResult,
    sort_signals_daily,
)


@dataclass(frozen=True)
class TsmomSignalStrategy:
    signals_config: SignalsConfig
    strategy_config: TsmomStrategyConfig

    @property
    def strategy_id(self) -> str:
        return self.strategy_config.strategy_id

    @property
    def model_id(self) -> str:
        return self.strategy_config.model_id

    @property
    def signal_version(self) -> str:
        return self.strategy_config.signal_version

    def build_signals(
        self, features: pd.DataFrame, request: SignalBuildRequest
    ) -> SignalBuildResult:
        if features.empty:
            return SignalBuildResult(signals=pd.DataFrame(columns=SIGNALS_DAILY_COLUMNS), qa={})

        working = features.copy()
        working["as_of_date"] = pd.to_datetime(working["as_of_date"]).dt.date
        rows: list[dict[str, object]] = []
        requested_roots = set(request.roots or ())

        for _, row in working.iterrows():
            root = str(row["root"])
            invalid_reason: str | None = None
            feature_hash = _nonempty_string_or_none(row.get("feature_hash"))

            if requested_roots and root not in requested_roots:
                invalid_reason = self.strategy_config.invalid_reasons.root_not_requested
            elif self.strategy_config.require_feature_complete and not bool(
                row.get("is_complete", False)
            ):
                invalid_reason = self.strategy_config.invalid_reasons.feature_incomplete
            elif (
                self.strategy_config.require_warmup_status_ok
                and str(row.get("warmup_status")) != "ok"
            ):
                invalid_reason = self.strategy_config.invalid_reasons.warmup_not_ok
            elif feature_hash is None:
                invalid_reason = self.strategy_config.invalid_reasons.missing_required_feature
            else:
                signal_raw = 0.0
                for feature_name, weight in zip(
                    self.strategy_config.required_features,
                    self.strategy_config.weights,
                    strict=True,
                ):
                    if feature_name not in working.columns:
                        invalid_reason = (
                            self.strategy_config.invalid_reasons.missing_required_feature
                        )
                        break
                    raw_value = row.get(feature_name)
                    if pd.isna(raw_value):
                        invalid_reason = (
                            self.strategy_config.invalid_reasons.missing_required_feature
                        )
                        break
                    numeric = float(raw_value)
                    if not isfinite(numeric):
                        invalid_reason = (
                            self.strategy_config.invalid_reasons.nonfinite_required_feature
                        )
                        break
                    signal_raw += _sign(numeric) * float(weight)

            if invalid_reason is None:
                clipped = _clip(
                    signal_raw,
                    clip_min=self.signals_config.clip_min,
                    clip_max=self.signals_config.clip_max,
                )
                rows.append(
                    {
                        "run_id": request.run_id,
                        "strategy_id": self.strategy_id,
                        "model_id": self.model_id,
                        "as_of_date": row["as_of_date"],
                        "root": root,
                        "signal_raw": float(signal_raw),
                        "signal_clipped": float(clipped),
                        "is_valid": True,
                        "invalid_reason": None,
                        "feature_hash": feature_hash,
                        "created_at_utc": request.created_at_utc,
                    }
                )
            else:
                rows.append(
                    {
                        "run_id": request.run_id,
                        "strategy_id": self.strategy_id,
                        "model_id": self.model_id,
                        "as_of_date": row["as_of_date"],
                        "root": root,
                        "signal_raw": None,
                        "signal_clipped": None,
                        "is_valid": False,
                        "invalid_reason": invalid_reason,
                        "feature_hash": feature_hash,
                        "created_at_utc": request.created_at_utc,
                    }
                )

        signals_daily = pd.DataFrame(rows, columns=SIGNALS_DAILY_COLUMNS)
        signals_daily = sort_signals_daily(
            signals_daily, sort_keys=self.signals_config.stable_sort_keys
        )
        return SignalBuildResult(signals=signals_daily, qa={})


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _clip(value: float, *, clip_min: float, clip_max: float) -> float:
    return max(clip_min, min(clip_max, value))


def _nonempty_string_or_none(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None
