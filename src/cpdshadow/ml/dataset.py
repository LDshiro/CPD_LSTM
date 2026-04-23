from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite, sqrt
from typing import Any, Sequence

import numpy as np
import pandas as pd

from cpdshadow.config import CpdLstmModelConfig
from cpdshadow.features import FEATURE_VECTOR_ORDER


class CpdLstmDataError(ValueError):
    pass


@dataclass(frozen=True)
class Standardizer:
    feature_order: tuple[str, ...]
    mean: tuple[float, ...]
    std: tuple[float, ...]
    min_std: float
    clip_abs: float
    method: str = "zscore"
    fit_scope: str = "train_only"

    @classmethod
    def fit(
        cls,
        values: np.ndarray,
        *,
        feature_order: Sequence[str],
        min_std: float,
        clip_abs: float,
    ) -> "Standardizer":
        if values.ndim != 3:
            raise CpdLstmDataError("standardizer input must be [sample, sequence, feature]")
        flattened = values.reshape(-1, values.shape[-1])
        if flattened.shape[0] == 0:
            raise CpdLstmDataError("cannot fit standardizer on an empty tensor")
        mean = np.nanmean(flattened, axis=0).astype(float)
        std = np.nanstd(flattened, axis=0).astype(float)
        std = np.where(np.isfinite(std) & (std >= min_std), std, min_std)
        if not np.isfinite(mean).all() or not np.isfinite(std).all():
            raise CpdLstmDataError("standardizer fit produced non-finite values")
        return cls(
            feature_order=tuple(feature_order),
            mean=tuple(float(item) for item in mean),
            std=tuple(float(item) for item in std),
            min_std=float(min_std),
            clip_abs=float(clip_abs),
        )

    def transform(self, values: np.ndarray) -> np.ndarray:
        if values.ndim != 3:
            raise CpdLstmDataError("standardizer input must be [sample, sequence, feature]")
        mean = np.asarray(self.mean, dtype=np.float32)
        std = np.asarray(self.std, dtype=np.float32)
        transformed = (values.astype(np.float32) - mean) / std
        return np.clip(transformed, -self.clip_abs, self.clip_abs).astype(np.float32)

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "fit_scope": self.fit_scope,
            "feature_order": list(self.feature_order),
            "mean": list(self.mean),
            "std": list(self.std),
            "min_std": self.min_std,
            "clip_abs_after_standardization": self.clip_abs,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Standardizer":
        return cls(
            feature_order=tuple(str(item) for item in payload["feature_order"]),
            mean=tuple(float(item) for item in payload["mean"]),
            std=tuple(float(item) for item in payload["std"]),
            min_std=float(payload["min_std"]),
            clip_abs=float(payload["clip_abs_after_standardization"]),
            method=str(payload.get("method", "zscore")),
            fit_scope=str(payload.get("fit_scope", "train_only")),
        )


@dataclass(frozen=True)
class SequencePanel:
    x: np.ndarray
    roots: tuple[str, ...]
    dates: tuple[date, ...]
    feature_hashes: tuple[str, ...]
    annualized_vol: np.ndarray
    y_norm: np.ndarray | None = None
    raw_next_return: np.ndarray | None = None
    next_dates: tuple[date | None, ...] | None = None

    @property
    def sample_count(self) -> int:
        return int(self.x.shape[0])


@dataclass(frozen=True)
class TrainingPanels:
    train: SequencePanel
    validation: SequencePanel
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class InferenceSequences:
    panel: SequencePanel
    audit_rows: pd.DataFrame


def validate_feature_order(config: CpdLstmModelConfig) -> None:
    expected = list(FEATURE_VECTOR_ORDER)
    if list(config.feature_order) != expected:
        raise CpdLstmDataError("CPD-LSTM feature order must match WP7 features_v1")


def build_training_panels(
    *,
    features_daily: pd.DataFrame,
    continuous_daily: pd.DataFrame,
    config: CpdLstmModelConfig,
    snapshot_id: str,
    feature_set_id: str,
    series_id: str,
    roots: Sequence[str] | None,
    train_start: date,
    train_end: date,
    val_start: date,
    val_end: date,
) -> TrainingPanels:
    validate_feature_order(config)
    if val_start <= train_end and train_start <= val_end:
        raise CpdLstmDataError("train and validation date windows must not overlap")
    features = prepare_features(
        features_daily=features_daily,
        config=config,
        snapshot_id=snapshot_id,
        feature_set_id=feature_set_id,
        series_id=series_id,
        roots=roots,
        end_date=val_end,
    )
    labels = prepare_continuous_labels(
        continuous_daily=continuous_daily,
        snapshot_id=snapshot_id,
        series_id=series_id,
        roots=roots,
    )
    label_lookup = _label_lookup(labels)
    train_records: list[_SampleRecord] = []
    val_records: list[_SampleRecord] = []
    warnings: list[str] = []

    for root, root_features in features.groupby("root", sort=True):
        group = root_features.sort_values("as_of_date", kind="stable").reset_index(drop=True)
        for idx, row in group.iterrows():
            as_of_date = row["as_of_date"]
            if train_start <= as_of_date <= train_end:
                split = "train"
            elif val_start <= as_of_date <= val_end:
                split = "validation"
            else:
                continue
            record = _build_training_record(
                root=str(root),
                idx=int(idx),
                group=group,
                labels=label_lookup,
                config=config,
            )
            if record is None:
                warnings.append(f"skipped_{split}_sample:{root}:{as_of_date}")
                continue
            if split == "train":
                train_records.append(record)
            else:
                val_records.append(record)

    train_panel = _records_to_panel(train_records, include_labels=True)
    validation_panel = _records_to_panel(val_records, include_labels=True)
    if train_panel.sample_count == 0:
        raise CpdLstmDataError("no valid CPD-LSTM training samples")
    if validation_panel.sample_count == 0:
        raise CpdLstmDataError("no valid CPD-LSTM validation samples")
    return TrainingPanels(train=train_panel, validation=validation_panel, warnings=tuple(warnings))


def build_inference_sequences(
    *,
    features_daily: pd.DataFrame,
    config: CpdLstmModelConfig,
    snapshot_id: str,
    feature_set_id: str,
    series_id: str,
    roots: Sequence[str] | None,
    start_date: date,
    end_date: date,
) -> InferenceSequences:
    validate_feature_order(config)
    features = prepare_features(
        features_daily=features_daily,
        config=config,
        snapshot_id=snapshot_id,
        feature_set_id=feature_set_id,
        series_id=series_id,
        roots=roots,
        end_date=end_date,
    )
    records: list[_SampleRecord] = []
    audit_rows: list[dict[str, object]] = []
    for root, root_features in features.groupby("root", sort=True):
        group = root_features.sort_values("as_of_date", kind="stable").reset_index(drop=True)
        for idx, row in group.iterrows():
            as_of_date = row["as_of_date"]
            if as_of_date < start_date or as_of_date > end_date:
                continue
            invalid_reason = _feature_row_invalid_reason(row)
            if invalid_reason is None:
                sequence = _sequence_values(group, int(idx), config)
                if sequence is None:
                    invalid_reason = "missing_sequence"
            else:
                sequence = None
            is_valid = invalid_reason is None and sequence is not None
            audit_rows.append({
                "as_of_date": as_of_date,
                "root": str(root),
                "feature_hash": _string_or_none(row.get("feature_hash")),
                "is_valid_sequence": is_valid,
                "invalid_reason": invalid_reason,
            })
            if is_valid and sequence is not None:
                records.append(
                    _SampleRecord(
                        x=sequence,
                        root=str(root),
                        as_of_date=as_of_date,
                        feature_hash=str(row["feature_hash"]),
                        annualized_vol=float(row["annualized_vol_60"]),
                        y_norm=None,
                        raw_next_return=None,
                        next_date=None,
                    )
                )
    return InferenceSequences(
        panel=_records_to_panel(records, include_labels=False),
        audit_rows=pd.DataFrame(
            audit_rows,
            columns=[
                "as_of_date",
                "root",
                "feature_hash",
                "is_valid_sequence",
                "invalid_reason",
            ],
        ),
    )


def prepare_features(
    *,
    features_daily: pd.DataFrame,
    config: CpdLstmModelConfig,
    snapshot_id: str,
    feature_set_id: str,
    series_id: str,
    roots: Sequence[str] | None,
    end_date: date,
) -> pd.DataFrame:
    if features_daily.empty:
        return pd.DataFrame(columns=list(_FEATURE_COLUMNS))
    missing = [column for column in _FEATURE_COLUMNS if column not in features_daily.columns]
    if missing:
        raise CpdLstmDataError(f"features_daily missing required columns: {missing}")
    selected = features_daily.copy()
    selected["as_of_date"] = pd.to_datetime(selected["as_of_date"]).dt.date
    selected = selected[
        (selected["feature_set_id"].astype(str) == feature_set_id)
        & (selected["series_id"].astype(str) == series_id)
        & (selected["snapshot_id"].astype(str) == snapshot_id)
        & (selected["as_of_date"] <= end_date)
    ]
    if roots is not None:
        root_set = {str(root) for root in roots}
        selected = selected[selected["root"].astype(str).isin(root_set)]
    selected = selected.sort_values(["root", "as_of_date"], kind="stable").reset_index(drop=True)
    return selected


def prepare_continuous_labels(
    *,
    continuous_daily: pd.DataFrame,
    snapshot_id: str,
    series_id: str,
    roots: Sequence[str] | None,
) -> pd.DataFrame:
    required = [
        "series_id",
        "as_of_date",
        "root",
        "daily_return",
        "is_usable_for_signal",
        "snapshot_id",
    ]
    if continuous_daily.empty:
        return pd.DataFrame(columns=required)
    missing = [column for column in required if column not in continuous_daily.columns]
    if missing:
        raise CpdLstmDataError(f"continuous_daily missing required columns: {missing}")
    selected = continuous_daily.copy()
    selected["as_of_date"] = pd.to_datetime(selected["as_of_date"]).dt.date
    selected = selected[
        (selected["series_id"].astype(str) == series_id)
        & (selected["snapshot_id"].astype(str) == snapshot_id)
        & selected["is_usable_for_signal"].fillna(False).astype(bool)
    ]
    if roots is not None:
        root_set = {str(root) for root in roots}
        selected = selected[selected["root"].astype(str).isin(root_set)]
    selected["daily_return"] = pd.to_numeric(selected["daily_return"], errors="coerce")
    selected = selected[selected["daily_return"].map(_finite)]
    return selected.sort_values(["root", "as_of_date"], kind="stable").reset_index(drop=True)


@dataclass(frozen=True)
class _SampleRecord:
    x: np.ndarray
    root: str
    as_of_date: date
    feature_hash: str
    annualized_vol: float
    y_norm: float | None
    raw_next_return: float | None
    next_date: date | None


def _build_training_record(
    *,
    root: str,
    idx: int,
    group: pd.DataFrame,
    labels: dict[tuple[str, date], float],
    config: CpdLstmModelConfig,
) -> _SampleRecord | None:
    row = group.iloc[idx]
    if _feature_row_invalid_reason(row) is not None:
        return None
    sequence = _sequence_values(group, idx, config)
    if sequence is None:
        return None
    if idx + config.labels.horizon_root_trading_days >= len(group):
        return None
    next_idx = idx + config.labels.horizon_root_trading_days
    next_date = group.iloc[next_idx]["as_of_date"]
    raw_return = labels.get((root, next_date))
    if raw_return is None or not _finite(raw_return):
        return None
    annualized_vol = float(row["annualized_vol_60"])
    if not _finite(annualized_vol) or annualized_vol < config.labels.min_annualized_vol:
        return None
    sigma_daily = max(
        annualized_vol / sqrt(config.annualization_factor),
        config.labels.min_annualized_vol / sqrt(config.annualization_factor),
    )
    y_norm = float(raw_return) / sigma_daily
    y_norm = float(np.clip(y_norm, -config.labels.normalized_return_clip_abs, config.labels.normalized_return_clip_abs))
    return _SampleRecord(
        x=sequence,
        root=root,
        as_of_date=row["as_of_date"],
        feature_hash=str(row["feature_hash"]),
        annualized_vol=annualized_vol,
        y_norm=y_norm,
        raw_next_return=float(raw_return),
        next_date=next_date,
    )


def _sequence_values(
    group: pd.DataFrame,
    idx: int,
    config: CpdLstmModelConfig,
) -> np.ndarray | None:
    start_idx = idx - config.sequence_length + 1
    if start_idx < 0:
        return None
    sequence_rows = group.iloc[start_idx : idx + 1]
    valid_rows = (
        sequence_rows["is_complete"].fillna(False).astype(bool)
        & (sequence_rows["warmup_status"].astype(str) == "ok")
        & sequence_rows["feature_hash"].map(lambda value: _string_or_none(value) is not None)
    )
    if not bool(valid_rows.all()):
        return None
    values = sequence_rows[list(config.feature_order)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float32)
    if not np.isfinite(values).all():
        return None
    return values


def _feature_row_invalid_reason(row: pd.Series) -> str | None:
    if not bool(row.get("is_complete", False)):
        return "feature_incomplete"
    if str(row.get("warmup_status")) != "ok":
        return "warmup_not_ok"
    if _string_or_none(row.get("feature_hash")) is None:
        return "missing_feature_hash"
    annualized_vol = _float_or_none(row.get("annualized_vol_60"))
    if annualized_vol is None or annualized_vol <= 0:
        return "invalid_annualized_vol"
    return None


def _records_to_panel(records: Sequence[_SampleRecord], *, include_labels: bool) -> SequencePanel:
    if records:
        x = np.stack([record.x for record in records]).astype(np.float32)
    else:
        x = np.empty((0, 0, len(FEATURE_VECTOR_ORDER)), dtype=np.float32)
    y_norm = (
        np.asarray([record.y_norm for record in records], dtype=np.float32)
        if include_labels
        else None
    )
    raw_next_return = (
        np.asarray([record.raw_next_return for record in records], dtype=np.float32)
        if include_labels
        else None
    )
    return SequencePanel(
        x=x,
        roots=tuple(record.root for record in records),
        dates=tuple(record.as_of_date for record in records),
        feature_hashes=tuple(record.feature_hash for record in records),
        annualized_vol=np.asarray([record.annualized_vol for record in records], dtype=np.float32),
        y_norm=y_norm,
        raw_next_return=raw_next_return,
        next_dates=tuple(record.next_date for record in records) if include_labels else None,
    )


def _label_lookup(labels: pd.DataFrame) -> dict[tuple[str, date], float]:
    return {
        (str(row["root"]), row["as_of_date"]): float(row["daily_return"])
        for _, row in labels.iterrows()
    }


def _float_or_none(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    numeric = float(value)
    return numeric if isfinite(numeric) else None


def _string_or_none(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _finite(value: object) -> bool:
    try:
        return isfinite(float(value))
    except (TypeError, ValueError):
        return False


_FEATURE_COLUMNS = (
    "feature_set_id",
    "as_of_date",
    "root",
    "series_id",
    *FEATURE_VECTOR_ORDER,
    "annualized_vol_60",
    "is_complete",
    "warmup_status",
    "feature_hash",
    "snapshot_id",
)
