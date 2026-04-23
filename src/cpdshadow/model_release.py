from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from pydantic import BaseModel, Field, model_validator

from cpdshadow.ids import canonical_json_bytes, file_sha256, stable_sha256_hex


DecisionState = Literal[
    "shadow_candidate",
    "defer_research",
    "reject_candidate",
    "tsmom_only",
]
GateStatus = Literal["pass", "fail", "warn", "na"]

ALLOWED_DECISIONS = {
    "shadow_candidate",
    "defer_research",
    "reject_candidate",
    "tsmom_only",
}
REQUIRED_MODEL_FILES = [
    "model.pt",
    "config.json",
    "feature_order.json",
    "standardizer.json",
    "metrics.json",
    "train_manifest.json",
    "sha256sums.txt",
]
REQUIRED_RELEASE_FILES = [
    "manifest.json",
    "promotion_decision.json",
    "gate_results.json",
    "metrics_summary.json",
    "artifact_hashes.json",
    "input_manifest.json",
    "model_card.md",
    "release_report.md",
]


class ModelReleaseError(ValueError):
    pass


class ModelReleaseHardGatesConfig(BaseModel):
    min_full_oos_net_sharpe: float = 0.90
    min_recent_8q_net_sharpe: float = 0.60
    max_drawdown_vs_tsmom_multiple: float = Field(default=1.50, gt=0.0)
    max_missing_feature_rate: float = Field(default=0.01, ge=0.0, le=1.0)
    require_common_oos_universe: bool = True
    require_no_lookahead_checks: bool = True
    require_model_artifact_hashes: bool = True
    require_feature_set_match: bool = True
    require_signal_contract_match: bool = True


class ModelReleaseReversalGatesConfig(BaseModel):
    min_event_count: int = Field(default=20, ge=0)
    require_5d_pnl_ge_tsmom: bool = True
    require_20d_pnl_ge_tsmom: bool = True


class ModelReleaseWarningGatesConfig(BaseModel):
    max_turnover_vs_tsmom_multiple: float = Field(default=2.50, ge=0.0)
    max_avg_modeled_cost_bps: float = Field(default=3.00, ge=0.0)
    min_oos_quarters: int = Field(default=8, ge=0)
    min_positive_oos_window_rate: float = Field(default=0.50, ge=0.0, le=1.0)


class ModelReleaseDecisionPolicyConfig(BaseModel):
    any_hard_fail: DecisionState = "reject_candidate"
    insufficient_reversal_events: DecisionState = "defer_research"
    hard_pass_with_reversal_pass: DecisionState = "shadow_candidate"
    hard_pass_reversal_mixed: DecisionState = "defer_research"
    hard_fail_but_tsmom_valid: DecisionState = "tsmom_only"


class ModelReleaseGatesConfig(BaseModel):
    version: str = "model_release_gates_v1"
    status_outputs: list[DecisionState]
    hard_gates: ModelReleaseHardGatesConfig = Field(
        default_factory=ModelReleaseHardGatesConfig
    )
    reversal_gates: ModelReleaseReversalGatesConfig = Field(
        default_factory=ModelReleaseReversalGatesConfig
    )
    warning_gates: ModelReleaseWarningGatesConfig = Field(
        default_factory=ModelReleaseWarningGatesConfig
    )
    decision_policy: ModelReleaseDecisionPolicyConfig = Field(
        default_factory=ModelReleaseDecisionPolicyConfig
    )

    @model_validator(mode="after")
    def validate_outputs(self) -> "ModelReleaseGatesConfig":
        if set(self.status_outputs) != ALLOWED_DECISIONS:
            raise ValueError("model release status_outputs must match allowed WP11 decisions")
        return self


@dataclass(frozen=True)
class PromotionEvidence:
    cpd_lstm: dict[str, object]
    tsmom: dict[str, object]
    reversal_bucket: dict[str, object]
    data_quality: dict[str, object]
    structural: dict[str, object]
    model_metadata: dict[str, object]
    source_paths: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return {
            "cpd_lstm": self.cpd_lstm,
            "tsmom": self.tsmom,
            "reversal_bucket": self.reversal_bucket,
            "data_quality": self.data_quality,
            "structural": self.structural,
            "model_metadata": self.model_metadata,
            "source_paths": self.source_paths,
        }


@dataclass(frozen=True)
class GateResult:
    gate_id: str
    severity: str
    status: GateStatus
    observed: object
    threshold: object
    message: str

    def to_dict(self) -> dict[str, object]:
        return {
            "gate_id": self.gate_id,
            "severity": self.severity,
            "status": self.status,
            "observed": self.observed,
            "threshold": self.threshold,
            "message": self.message,
        }


@dataclass(frozen=True)
class PromotionDecision:
    decision: DecisionState
    hard_gate_status: str
    reversal_gate_status: str
    warning_count: int
    failure_count: int
    summary: str
    recommended_next_step: str

    def to_dict(self, *, release_id: str, created_at_utc: datetime) -> dict[str, object]:
        return {
            "schema_version": "promotion_decision_v1",
            "release_id": release_id,
            "decision": self.decision,
            "human_approval_required": True,
            "summary": self.summary,
            "hard_gate_status": self.hard_gate_status,
            "reversal_gate_status": self.reversal_gate_status,
            "warning_count": self.warning_count,
            "failure_count": self.failure_count,
            "recommended_next_step": self.recommended_next_step,
            "fallback_strategy_id": "tsmom",
            "created_at_utc": _iso_utc(created_at_utc),
        }


def load_model_release_gates(path: str | Path) -> ModelReleaseGatesConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    payload = raw.get("model_release", raw)
    return ModelReleaseGatesConfig.model_validate(payload)


def load_promotion_evidence(
    *,
    walkforward_dir: str | Path,
    candidate_model_dir: str | Path,
    gates_config: ModelReleaseGatesConfig,
    feature_set_id: str = "features_v1",
    strategy_id: str = "cpd_lstm",
    baseline_strategy_id: str = "tsmom",
) -> PromotionEvidence:
    wf_dir = Path(walkforward_dir)
    model_dir = Path(candidate_model_dir)
    aggregate = _read_table(wf_dir, "aggregate_metrics")
    fold_metrics = _read_table(wf_dir, "fold_metrics")
    reversal = _read_table(wf_dir, "reversal_bucket_metrics")
    signals = _read_table(wf_dir, "oos_signals_daily")
    windows = _read_table(wf_dir, "walkforward_windows")
    manifest = _read_json_optional(wf_dir / "manifest.json")
    gates = _read_json_optional(wf_dir / "gates.json")
    if aggregate.empty:
        raise ModelReleaseError("missing or empty WP10 aggregate metrics")
    if signals.empty:
        raise ModelReleaseError("missing or empty WP10 OOS signals")
    metadata, artifact_hashes = _load_model_metadata(model_dir)
    cpd_row = _strategy_row(aggregate, strategy_id)
    tsmom_row = _strategy_row(aggregate, baseline_strategy_id)
    if not cpd_row:
        raise ModelReleaseError(f"aggregate metrics missing strategy {strategy_id}")
    if not tsmom_row:
        raise ModelReleaseError(f"aggregate metrics missing strategy {baseline_strategy_id}")
    structural = _structural_evidence(
        signals=signals,
        windows=windows,
        gates=gates,
        expected_feature_set_id=feature_set_id,
        candidate_metadata=metadata,
        artifact_hashes=artifact_hashes,
        strategy_id=strategy_id,
        baseline_strategy_id=baseline_strategy_id,
        require_model_artifact_hashes=gates_config.hard_gates.require_model_artifact_hashes,
    )
    data_quality = _data_quality_evidence(
        signals=signals,
        strategy_id=strategy_id,
    )
    return PromotionEvidence(
        cpd_lstm={
            "strategy_id": strategy_id,
            "model_id": cpd_row.get("model_id") or metadata.get("model_id"),
            "net_sharpe": _float_or_none(cpd_row.get("sharpe")),
            "recent_8q_net_sharpe": _float_or_none(
                cpd_row.get("last_8_quarters_sharpe")
            ),
            "max_drawdown": _float_or_none(cpd_row.get("max_drawdown")),
            "annualized_return": _float_or_none(cpd_row.get("ann_return")),
            "realized_vol": _float_or_none(cpd_row.get("ann_vol")),
            "turnover": _float_or_none(cpd_row.get("avg_daily_turnover_contracts")),
            "avg_modeled_cost_bps": _cost_bps(cpd_row),
            "positive_oos_window_rate": _positive_window_rate(fold_metrics, strategy_id),
            "oos_quarters": _n_folds(fold_metrics, strategy_id),
        },
        tsmom={
            "strategy_id": baseline_strategy_id,
            "model_id": tsmom_row.get("model_id"),
            "net_sharpe": _float_or_none(tsmom_row.get("sharpe")),
            "recent_8q_net_sharpe": _float_or_none(
                tsmom_row.get("last_8_quarters_sharpe")
            ),
            "max_drawdown": _float_or_none(tsmom_row.get("max_drawdown")),
            "annualized_return": _float_or_none(tsmom_row.get("ann_return")),
            "realized_vol": _float_or_none(tsmom_row.get("ann_vol")),
            "turnover": _float_or_none(tsmom_row.get("avg_daily_turnover_contracts")),
            "is_valid": _strategy_valid(aggregate, baseline_strategy_id),
        },
        reversal_bucket=_reversal_evidence(
            reversal=reversal,
            min_event_count=gates_config.reversal_gates.min_event_count,
        ),
        data_quality=data_quality,
        structural=structural,
        model_metadata=metadata | {"artifact_hashes": artifact_hashes},
        source_paths={
            "walkforward_dir": wf_dir.as_posix(),
            "candidate_model_dir": model_dir.as_posix(),
            "walkforward_manifest_hash": stable_sha256_hex(manifest),
        },
    )


def evaluate_promotion_gates(
    evidence: PromotionEvidence,
    config: ModelReleaseGatesConfig,
) -> list[GateResult]:
    cpd = evidence.cpd_lstm
    tsmom = evidence.tsmom
    reversal = evidence.reversal_bucket
    data_quality = evidence.data_quality
    structural = evidence.structural
    hard = config.hard_gates
    warnings = config.warning_gates
    results = [
        _min_gate(
            "min_full_oos_net_sharpe",
            cpd.get("net_sharpe"),
            hard.min_full_oos_net_sharpe,
            "hard",
        ),
        _min_gate(
            "min_recent_8q_net_sharpe",
            cpd.get("recent_8q_net_sharpe"),
            hard.min_recent_8q_net_sharpe,
            "hard",
        ),
        _max_gate(
            "max_drawdown_vs_tsmom_multiple",
            _drawdown_multiple(cpd.get("max_drawdown"), tsmom.get("max_drawdown")),
            hard.max_drawdown_vs_tsmom_multiple,
            "hard",
        ),
        _max_gate(
            "max_missing_feature_rate",
            data_quality.get("missing_feature_rate"),
            hard.max_missing_feature_rate,
            "hard",
        ),
        _bool_gate(
            "require_common_oos_universe",
            structural.get("common_oos_universe"),
            hard.require_common_oos_universe,
            "hard",
        ),
        _bool_gate(
            "require_no_lookahead_checks",
            structural.get("no_lookahead_checks_passed"),
            hard.require_no_lookahead_checks,
            "hard",
        ),
        _bool_gate(
            "require_model_artifact_hashes",
            structural.get("model_artifact_hashes_present"),
            hard.require_model_artifact_hashes,
            "hard",
        ),
        _bool_gate(
            "require_feature_set_match",
            structural.get("feature_set_match"),
            hard.require_feature_set_match,
            "hard",
        ),
        _bool_gate(
            "require_signal_contract_match",
            structural.get("signal_contract_match"),
            hard.require_signal_contract_match,
            "hard",
        ),
        _min_gate(
            "min_reversal_event_count",
            reversal.get("event_count"),
            config.reversal_gates.min_event_count,
            "reversal",
        ),
        _min_gate(
            "require_5d_pnl_ge_tsmom",
            reversal.get("mean_diff_5d"),
            0.0 if config.reversal_gates.require_5d_pnl_ge_tsmom else None,
            "reversal",
        ),
        _min_gate(
            "require_20d_pnl_ge_tsmom",
            reversal.get("mean_diff_20d"),
            0.0 if config.reversal_gates.require_20d_pnl_ge_tsmom else None,
            "reversal",
        ),
        _max_gate(
            "max_turnover_vs_tsmom_multiple",
            _safe_ratio(cpd.get("turnover"), tsmom.get("turnover")),
            warnings.max_turnover_vs_tsmom_multiple,
            "warning",
        ),
        _max_gate(
            "max_avg_modeled_cost_bps",
            cpd.get("avg_modeled_cost_bps"),
            warnings.max_avg_modeled_cost_bps,
            "warning",
        ),
        _min_gate(
            "min_oos_quarters",
            cpd.get("oos_quarters"),
            warnings.min_oos_quarters,
            "warning",
        ),
        _min_gate(
            "min_positive_oos_window_rate",
            cpd.get("positive_oos_window_rate"),
            warnings.min_positive_oos_window_rate,
            "warning",
        ),
    ]
    return results


def decide_promotion(
    evidence: PromotionEvidence,
    gate_results: list[GateResult],
    config: ModelReleaseGatesConfig,
) -> PromotionDecision:
    structural_fail = any(
        result.severity == "hard"
        and result.status == "fail"
        and result.gate_id
        in {
            "require_common_oos_universe",
            "require_no_lookahead_checks",
            "require_model_artifact_hashes",
            "require_feature_set_match",
            "require_signal_contract_match",
        }
        for result in gate_results
    )
    hard_fail = any(
        result.severity == "hard" and result.status == "fail" for result in gate_results
    )
    warning_count = sum(result.status == "warn" for result in gate_results)
    failure_count = sum(result.status == "fail" for result in gate_results)
    reversal_events = next(
        result for result in gate_results if result.gate_id == "min_reversal_event_count"
    )
    reversal_checks = [
        result for result in gate_results if result.severity == "reversal"
    ]
    reversal_underperformance = any(
        result.gate_id != "min_reversal_event_count" and result.status == "fail"
        for result in reversal_checks
    )
    if structural_fail:
        decision: DecisionState = config.decision_policy.any_hard_fail
        summary = "Structural or artifact gates failed; candidate cannot be packaged for Shadow."
        next_step = "Fix evidence lineage or artifact integrity before reconsidering."
    elif hard_fail:
        decision = (
            config.decision_policy.hard_fail_but_tsmom_valid
            if bool(evidence.tsmom.get("is_valid"))
            else config.decision_policy.any_hard_fail
        )
        summary = "Performance hard gates failed; CPD-LSTM is not a Shadow candidate."
        next_step = "Use TSMOM fallback or return to research."
    elif reversal_events.status == "fail":
        decision = config.decision_policy.insufficient_reversal_events
        summary = "Core gates passed, but reversal evidence is insufficient."
        next_step = "Collect more OOS reversal evidence before freezing the candidate."
    elif reversal_underperformance:
        decision = config.decision_policy.hard_pass_reversal_mixed
        summary = "Core gates passed, but reversal bucket performance is mixed."
        next_step = "Defer candidate and analyze reversal behavior."
    else:
        decision = config.decision_policy.hard_pass_with_reversal_pass
        summary = "Core and reversal gates passed for Shadow candidate review."
        next_step = "Prepare human review; do not approve paper or live trading in WP11."
    hard_gate_status = "fail" if hard_fail else "pass"
    reversal_gate_status = _reversal_status(reversal_events, reversal_underperformance)
    return PromotionDecision(
        decision=decision,
        hard_gate_status=hard_gate_status,
        reversal_gate_status=reversal_gate_status,
        warning_count=warning_count,
        failure_count=failure_count,
        summary=summary,
        recommended_next_step=next_step,
    )


def package_model_release(
    *,
    walkforward_dir: str | Path,
    candidate_model_dir: str | Path,
    gates_path: str | Path,
    output_dir: str | Path,
    release_id: str | None = None,
    created_at_utc: datetime | None = None,
    feature_set_id: str = "features_v1",
    strategy_id: str = "cpd_lstm",
    baseline_strategy_id: str = "tsmom",
    settings_path: str | Path | None = None,
    data_schema_path: str | Path | None = None,
    copy_artifacts: bool = False,
    write_alias: bool = False,
) -> dict[str, object]:
    gates_config = load_model_release_gates(gates_path)
    created_at = created_at_utc or datetime.now(UTC)
    evidence = load_promotion_evidence(
        walkforward_dir=walkforward_dir,
        candidate_model_dir=candidate_model_dir,
        gates_config=gates_config,
        feature_set_id=feature_set_id,
        strategy_id=strategy_id,
        baseline_strategy_id=baseline_strategy_id,
    )
    gate_results = evaluate_promotion_gates(evidence, gates_config)
    decision = decide_promotion(evidence, gate_results, gates_config)
    effective_release_id = release_id or _make_release_id(evidence, created_at)
    release_dir = Path(output_dir) / effective_release_id
    if release_dir.exists():
        shutil.rmtree(release_dir)
    release_dir.mkdir(parents=True, exist_ok=True)
    package = _release_payloads(
        evidence=evidence,
        gate_results=gate_results,
        decision=decision,
        gates_config=gates_config,
        release_id=effective_release_id,
        created_at_utc=created_at,
        gates_path=Path(gates_path),
        settings_path=Path(settings_path) if settings_path is not None else None,
        data_schema_path=Path(data_schema_path) if data_schema_path is not None else None,
    )
    _write_release_files(release_dir, package)
    if copy_artifacts:
        _copy_small_artifacts(Path(candidate_model_dir), release_dir / "model_artifact")
    if write_alias and decision.decision == "shadow_candidate":
        alias_dir = release_dir / "aliases"
        alias_dir.mkdir(parents=True, exist_ok=True)
        _write_json(alias_dir / "shadow_candidate.json", package["manifest"])
    return {
        "release_id": effective_release_id,
        "release_dir": release_dir.as_posix(),
        "decision": decision.decision,
        "human_approval_required": True,
        "qa": qa_model_release(release_dir),
    }


def evaluate_model_release(
    *,
    walkforward_dir: str | Path,
    candidate_model_dir: str | Path,
    gates_path: str | Path,
    feature_set_id: str = "features_v1",
    strategy_id: str = "cpd_lstm",
    baseline_strategy_id: str = "tsmom",
) -> dict[str, object]:
    gates_config = load_model_release_gates(gates_path)
    evidence = load_promotion_evidence(
        walkforward_dir=walkforward_dir,
        candidate_model_dir=candidate_model_dir,
        gates_config=gates_config,
        feature_set_id=feature_set_id,
        strategy_id=strategy_id,
        baseline_strategy_id=baseline_strategy_id,
    )
    gate_results = evaluate_promotion_gates(evidence, gates_config)
    decision = decide_promotion(evidence, gate_results, gates_config)
    return {
        "evidence": evidence.to_dict(),
        "gate_results": [result.to_dict() for result in gate_results],
        "decision": {
            "decision": decision.decision,
            "hard_gate_status": decision.hard_gate_status,
            "reversal_gate_status": decision.reversal_gate_status,
            "warning_count": decision.warning_count,
            "failure_count": decision.failure_count,
            "summary": decision.summary,
            "recommended_next_step": decision.recommended_next_step,
        },
    }


def qa_model_release(release_dir: str | Path) -> dict[str, object]:
    root = Path(release_dir)
    issues: list[dict[str, object]] = []
    for filename in REQUIRED_RELEASE_FILES:
        if not (root / filename).exists():
            issues.append({"severity": "error", "code": "missing_release_file", "file": filename})
    parsed: dict[str, dict[str, object]] = {}
    for filename in [
        "manifest.json",
        "promotion_decision.json",
        "gate_results.json",
        "artifact_hashes.json",
    ]:
        path = root / filename
        if not path.exists():
            continue
        try:
            parsed[filename] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            issues.append({
                "severity": "error",
                "code": "invalid_json",
                "file": filename,
                "message": str(exc),
            })
    manifest = parsed.get("manifest.json", {})
    decision = parsed.get("promotion_decision.json", {})
    gate_payload = parsed.get("gate_results.json", {})
    if manifest.get("release_id") != decision.get("release_id"):
        issues.append({"severity": "error", "code": "release_id_mismatch"})
    if decision.get("decision") not in ALLOWED_DECISIONS:
        issues.append({"severity": "error", "code": "invalid_decision"})
    if decision.get("human_approval_required") is not True:
        issues.append({"severity": "error", "code": "human_approval_not_required"})
    if any(decision.get(key) is True for key in ["live_approved", "paper_approved"]):
        issues.append({"severity": "error", "code": "forbidden_live_or_paper_approval"})
    gate_results = gate_payload.get("gate_results", [])
    configured_hard = {
        "min_full_oos_net_sharpe",
        "min_recent_8q_net_sharpe",
        "max_drawdown_vs_tsmom_multiple",
        "max_missing_feature_rate",
        "require_common_oos_universe",
        "require_no_lookahead_checks",
        "require_model_artifact_hashes",
        "require_feature_set_match",
        "require_signal_contract_match",
    }
    present_hard = {
        str(item.get("gate_id"))
        for item in gate_results
        if isinstance(item, dict) and item.get("severity") == "hard"
    }
    if not configured_hard.issubset(present_hard):
        issues.append({"severity": "error", "code": "missing_hard_gate_results"})
    if decision.get("decision") == "shadow_candidate":
        if decision.get("hard_gate_status") != "pass" or decision.get("reversal_gate_status") != "pass":
            issues.append({"severity": "error", "code": "shadow_candidate_gate_mismatch"})
    elif (root / "release_report.md").exists():
        report = (root / "release_report.md").read_text(encoding="utf-8")
        if "Recommended next step" not in report:
            issues.append({"severity": "error", "code": "missing_non_candidate_explanation"})
    report = {
        "release_dir": root.as_posix(),
        "has_errors": any(issue["severity"] == "error" for issue in issues),
        "issues": issues,
    }
    return report


def _release_payloads(
    *,
    evidence: PromotionEvidence,
    gate_results: list[GateResult],
    decision: PromotionDecision,
    gates_config: ModelReleaseGatesConfig,
    release_id: str,
    created_at_utc: datetime,
    gates_path: Path,
    settings_path: Path | None,
    data_schema_path: Path | None,
) -> dict[str, object]:
    gate_dicts = [result.to_dict() for result in gate_results]
    artifact_hashes = evidence.model_metadata.get("artifact_hashes", {})
    metrics_summary = evidence.to_dict()
    config_hashes = {
        "gates_config_sha256": file_sha256(gates_path),
        "settings_sha256": file_sha256(settings_path) if settings_path and settings_path.exists() else None,
        "data_schema_sha256": (
            file_sha256(data_schema_path)
            if data_schema_path and data_schema_path.exists()
            else None
        ),
    }
    decision_payload = decision.to_dict(
        release_id=release_id,
        created_at_utc=created_at_utc,
    )
    manifest = {
        "schema_version": "model_rc_manifest_v1",
        "release_id": release_id,
        "created_at_utc": _iso_utc(created_at_utc),
        "status": decision.decision,
        "human_approval_required": True,
        "strategy_id": "cpd_lstm",
        "model_family": evidence.model_metadata.get("model_family"),
        "model_id": evidence.model_metadata.get("model_id"),
        "feature_set_id": evidence.model_metadata.get("feature_set_id"),
        "signal_contract_version": "signals_daily_v1",
        "walkforward_eval_id": evidence.source_paths.get("walkforward_manifest_hash"),
        "candidate_model_dir": evidence.source_paths["candidate_model_dir"],
        "walkforward_dir": evidence.source_paths["walkforward_dir"],
        "config_hash": stable_sha256_hex(config_hashes),
        "feature_schema_hash": config_hashes["data_schema_sha256"],
        "artifact_hashes_file": "artifact_hashes.json",
        "gate_results_file": "gate_results.json",
        "promotion_decision_file": "promotion_decision.json",
    }
    input_manifest = {
        "release_id": release_id,
        "evidence_sources": evidence.source_paths,
        "config_hashes": config_hashes,
        "gates_version": gates_config.version,
        "derived_data_quality": evidence.data_quality,
        "derived_structural_checks": evidence.structural,
    }
    return {
        "manifest": manifest,
        "promotion_decision": decision_payload,
        "gate_results": {"release_id": release_id, "gate_results": gate_dicts},
        "metrics_summary": metrics_summary,
        "artifact_hashes": {
            "release_id": release_id,
            "candidate_model_dir": evidence.source_paths["candidate_model_dir"],
            "files": artifact_hashes,
            "manifest_sha256": stable_sha256_hex(artifact_hashes),
        },
        "input_manifest": input_manifest,
        "model_card": _model_card(
            release_id=release_id,
            decision=decision,
            evidence=evidence,
            artifact_hashes=artifact_hashes,
        ),
        "release_report": _release_report(
            release_id=release_id,
            decision=decision,
            gate_results=gate_results,
            evidence=evidence,
        ),
    }


def _write_release_files(release_dir: Path, package: dict[str, object]) -> None:
    _write_json(release_dir / "manifest.json", package["manifest"])
    _write_json(release_dir / "promotion_decision.json", package["promotion_decision"])
    _write_json(release_dir / "gate_results.json", package["gate_results"])
    _write_json(release_dir / "metrics_summary.json", package["metrics_summary"])
    _write_json(release_dir / "artifact_hashes.json", package["artifact_hashes"])
    _write_json(release_dir / "input_manifest.json", package["input_manifest"])
    (release_dir / "model_card.md").write_text(
        str(package["model_card"]),
        encoding="utf-8",
    )
    (release_dir / "release_report.md").write_text(
        str(package["release_report"]),
        encoding="utf-8",
    )


def _read_table(root: Path, stem: str) -> pd.DataFrame:
    parquet = root / f"{stem}.parquet"
    if parquet.exists():
        return pd.read_parquet(parquet)
    json_path = root / f"{stem}.json"
    if json_path.exists():
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict):
            if "rows" in payload and isinstance(payload["rows"], list):
                return pd.DataFrame(payload["rows"])
            return pd.DataFrame([payload])
    return pd.DataFrame()


def _read_json_optional(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_model_metadata(model_dir: Path) -> tuple[dict[str, object], dict[str, str]]:
    missing = [name for name in REQUIRED_MODEL_FILES if not (model_dir / name).exists()]
    if missing:
        raise ModelReleaseError(f"candidate model artifact missing required files: {missing}")
    config = _read_json_optional(model_dir / "config.json")
    train_manifest = _read_json_optional(model_dir / "train_manifest.json")
    metrics = _read_json_optional(model_dir / "metrics.json")
    hashes = {
        path.name: file_sha256(path)
        for path in sorted(model_dir.iterdir(), key=lambda item: item.name)
        if path.is_file()
    }
    model_id = (
        train_manifest.get("model_id")
        or config.get("model_id")
        or metrics.get("model_id")
        or model_dir.name
    )
    return (
        {
            "model_id": model_id,
            "model_family": config.get("model_family", "cpd_lstm_v1"),
            "strategy_id": config.get("strategy_id", "cpd_lstm"),
            "feature_set_id": config.get("feature_set_id", train_manifest.get("feature_set_id")),
            "artifact_sha256": train_manifest.get("artifact_sha256"),
            "training_run_id": train_manifest.get("training_run_id"),
        },
        hashes,
    )


def _structural_evidence(
    *,
    signals: pd.DataFrame,
    windows: pd.DataFrame,
    gates: dict[str, object],
    expected_feature_set_id: str,
    candidate_metadata: dict[str, object],
    artifact_hashes: dict[str, str],
    strategy_id: str,
    baseline_strategy_id: str,
    require_model_artifact_hashes: bool,
) -> dict[str, object]:
    common = _common_universe(signals, strategy_id, baseline_strategy_id)
    no_lookahead = _windows_non_overlapping(windows) and _wp10_gates_structural_pass(gates)
    signal_columns = {
        "run_id",
        "strategy_id",
        "model_id",
        "as_of_date",
        "root",
        "signal_raw",
        "signal_clipped",
        "is_valid",
        "invalid_reason",
        "feature_hash",
        "created_at_utc",
    }
    return {
        "common_oos_universe": common,
        "no_lookahead_checks_passed": no_lookahead,
        "model_artifact_hashes_present": (
            all(name in artifact_hashes for name in REQUIRED_MODEL_FILES)
            if require_model_artifact_hashes
            else True
        ),
        "feature_set_match": candidate_metadata.get("feature_set_id") == expected_feature_set_id,
        "signal_contract_match": signal_columns.issubset(set(signals.columns)),
    }


def _data_quality_evidence(*, signals: pd.DataFrame, strategy_id: str) -> dict[str, object]:
    if not {"strategy_id", "is_valid"}.issubset(signals.columns):
        return {
            "missing_feature_rate": 1.0,
            "oos_root_date_coverage": 0.0,
            "derived_from": "missing_oos_signal_validity_columns",
        }
    selected = signals[signals["strategy_id"].astype(str) == strategy_id]
    total = len(selected)
    invalid = int((~selected["is_valid"].fillna(False).astype(bool)).sum()) if total else 0
    return {
        "missing_feature_rate": invalid / total if total else 1.0,
        "oos_root_date_coverage": 1.0 - (invalid / total if total else 1.0),
        "derived_from": "oos_signals_daily.is_valid",
    }


def _reversal_evidence(*, reversal: pd.DataFrame, min_event_count: int) -> dict[str, object]:
    if reversal.empty:
        return {"event_count": 0, "mean_diff_5d": None, "mean_diff_20d": None}
    if "strategy_id" not in reversal.columns:
        return {"event_count": 0, "mean_diff_5d": None, "mean_diff_20d": None}
    comparison = reversal[reversal["strategy_id"].astype(str) == "cpd_lstm_minus_tsmom"]
    event_values = (
        pd.to_numeric(comparison["event_count"], errors="coerce").dropna()
        if not comparison.empty and "event_count" in comparison.columns
        else pd.Series(dtype="float64")
    )
    event_count = int(event_values.max()) if not event_values.empty else 0
    return {
        "event_count": event_count,
        "mean_diff_5d": _horizon_mean(comparison, 5),
        "mean_diff_20d": _horizon_mean(comparison, 20),
    }


def _horizon_mean(df: pd.DataFrame, horizon: int) -> float | None:
    if df.empty or "horizon_days" not in df.columns:
        return None
    selected = df[pd.to_numeric(df["horizon_days"], errors="coerce") == horizon]
    if selected.empty or "mean_diff_cpd_minus_tsmom" not in selected.columns:
        return None
    values = pd.to_numeric(selected["mean_diff_cpd_minus_tsmom"], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else None


def _strategy_row(df: pd.DataFrame, strategy_id: str) -> dict[str, object]:
    if df.empty or "strategy_id" not in df.columns:
        return {}
    selected = df[df["strategy_id"].astype(str) == strategy_id]
    return selected.iloc[0].to_dict() if not selected.empty else {}


def _strategy_valid(df: pd.DataFrame, strategy_id: str) -> bool:
    row = _strategy_row(df, strategy_id)
    return row != {} and _float_or_none(row.get("sharpe")) is not None


def _positive_window_rate(df: pd.DataFrame, strategy_id: str) -> float | None:
    if df.empty or "strategy_id" not in df.columns or "net_return" not in df.columns:
        return None
    selected = df[df["strategy_id"].astype(str) == strategy_id]
    values = pd.to_numeric(selected["net_return"], errors="coerce").dropna()
    return float((values > 0).mean()) if not values.empty else None


def _n_folds(df: pd.DataFrame, strategy_id: str) -> int:
    if df.empty or "strategy_id" not in df.columns or "fold_id" not in df.columns:
        return 0
    return int(df[df["strategy_id"].astype(str) == strategy_id]["fold_id"].nunique())


def _cost_bps(row: dict[str, object]) -> float | None:
    cost = _float_or_none(row.get("cost_to_gross_pnl"))
    return None if cost is None else cost * 10_000.0


def _common_universe(signals: pd.DataFrame, strategy_id: str, baseline_strategy_id: str) -> bool:
    required = {"is_valid", "strategy_id", "as_of_date", "root"}
    if not required.issubset(signals.columns):
        return False
    valid = signals[signals["is_valid"].fillna(False).astype(bool)].copy()
    if valid.empty:
        return False
    cpd = valid[valid["strategy_id"].astype(str) == strategy_id]
    baseline = valid[valid["strategy_id"].astype(str) == baseline_strategy_id]
    cpd_keys = set(zip(cpd["as_of_date"].astype(str), cpd["root"].astype(str), strict=False))
    baseline_keys = set(
        zip(baseline["as_of_date"].astype(str), baseline["root"].astype(str), strict=False)
    )
    return bool(cpd_keys) and cpd_keys == baseline_keys


def _windows_non_overlapping(windows: pd.DataFrame) -> bool:
    if windows.empty:
        return False
    required = {"train_end", "val_start", "val_end", "oos_start", "status"}
    if not required.issubset(windows.columns):
        return False
    for _, row in windows.iterrows():
        if str(row.get("status")) in {"skipped", "evaluated"}:
            continue
        if not (
            pd.Timestamp(row["train_end"])
            < pd.Timestamp(row["val_start"])
            <= pd.Timestamp(row["val_end"])
            < pd.Timestamp(row["oos_start"])
        ):
            return False
    return True


def _wp10_gates_structural_pass(gates: dict[str, object]) -> bool:
    status = gates.get("overall_status")
    return status in {"pass", "warning", "fail"} or gates == {}


def _min_gate(
    gate_id: str,
    observed: object,
    threshold: object,
    severity: str,
) -> GateResult:
    if threshold is None:
        return GateResult(gate_id, severity, "na", observed, threshold, "Gate disabled.")
    value = _float_or_none(observed)
    limit = _float_or_none(threshold)
    if value is None or limit is None:
        status: GateStatus = "warn" if severity == "warning" else "fail"
        return GateResult(gate_id, severity, status, observed, threshold, "Observed value unavailable.")
    status: GateStatus = "pass" if value >= limit else "fail"
    if severity == "warning" and status == "fail":
        status = "warn"
    return GateResult(gate_id, severity, status, value, limit, "Minimum threshold check.")


def _max_gate(
    gate_id: str,
    observed: object,
    threshold: object,
    severity: str,
) -> GateResult:
    value = _float_or_none(observed)
    limit = _float_or_none(threshold)
    if value is None or limit is None:
        status: GateStatus = "warn" if severity == "warning" else "fail"
        return GateResult(gate_id, severity, status, observed, threshold, "Observed value unavailable.")
    status: GateStatus = "pass" if value <= limit else "fail"
    if severity == "warning" and status == "fail":
        status = "warn"
    return GateResult(gate_id, severity, status, value, limit, "Maximum threshold check.")


def _bool_gate(
    gate_id: str,
    observed: object,
    required: bool,
    severity: str,
) -> GateResult:
    if not required:
        return GateResult(gate_id, severity, "na", observed, required, "Gate disabled.")
    status: GateStatus = "pass" if bool(observed) else "fail"
    return GateResult(gate_id, severity, status, bool(observed), True, "Boolean requirement.")


def _drawdown_multiple(cpd: object, tsmom: object) -> float | None:
    cpd_value = _float_or_none(cpd)
    tsmom_value = _float_or_none(tsmom)
    if cpd_value is None or tsmom_value is None:
        return None
    denominator = abs(tsmom_value) if tsmom_value != 0 else 1.0e-12
    return abs(cpd_value) / denominator


def _safe_ratio(numerator: object, denominator: object) -> float | None:
    left = _float_or_none(numerator)
    right = _float_or_none(denominator)
    if left is None or right is None or right == 0:
        return None
    return abs(left) / abs(right)


def _float_or_none(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if pd.notna(result) else None


def _reversal_status(events_gate: GateResult, underperformance: bool) -> str:
    if events_gate.status == "fail":
        return "insufficient"
    if underperformance:
        return "mixed"
    return "pass"


def _write_json(path: Path, payload: object) -> None:
    path.write_bytes(canonical_json_bytes(payload))


def _copy_small_artifacts(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_MODEL_FILES:
        source_file = source / name
        if source_file.exists() and source_file.stat().st_size < 5_000_000:
            shutil.copy2(source_file, target / name)


def _make_release_id(evidence: PromotionEvidence, created_at: datetime) -> str:
    token = stable_sha256_hex({"evidence": evidence.to_dict(), "created_at": _iso_utc(created_at)})[:12]
    return f"rc_{created_at.strftime('%Y%m%d')}_{token}"


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _model_card(
    *,
    release_id: str,
    decision: PromotionDecision,
    evidence: PromotionEvidence,
    artifact_hashes: object,
) -> str:
    return "\n".join(
        [
            f"# Model RC {release_id}",
            "",
            f"- Decision: `{decision.decision}`",
            "- Intended use: Shadow candidate review only.",
            "- Non-use: not approved for paper or live trading.",
            "- Human approval required: true",
            f"- Candidate model: `{evidence.source_paths['candidate_model_dir']}`",
            f"- Model hash manifest: `{stable_sha256_hex(artifact_hashes)}`",
            f"- Feature set: `{evidence.model_metadata.get('feature_set_id')}`",
            f"- Full OOS Sharpe: {evidence.cpd_lstm.get('net_sharpe')}",
            f"- TSMOM OOS Sharpe: {evidence.tsmom.get('net_sharpe')}",
            f"- Reversal 5d diff: {evidence.reversal_bucket.get('mean_diff_5d')}",
            f"- Reversal 20d diff: {evidence.reversal_bucket.get('mean_diff_20d')}",
            "",
            "Backtest evidence is not a guarantee of future performance.",
            "TSMOM remains the required fallback strategy.",
        ]
    )


def _release_report(
    *,
    release_id: str,
    decision: PromotionDecision,
    gate_results: list[GateResult],
    evidence: PromotionEvidence,
) -> str:
    lines = [
        f"# WP11 Release Report {release_id}",
        "",
        f"- Decision: `{decision.decision}`",
        f"- Summary: {decision.summary}",
        f"- Recommended next step: {decision.recommended_next_step}",
        "- Human approval required: true",
        "",
        "## Evidence",
        "",
        f"- CPD-LSTM Sharpe: {evidence.cpd_lstm.get('net_sharpe')}",
        f"- Recent 8Q Sharpe: {evidence.cpd_lstm.get('recent_8q_net_sharpe')}",
        f"- TSMOM Sharpe: {evidence.tsmom.get('net_sharpe')}",
        f"- Reversal events: {evidence.reversal_bucket.get('event_count')}",
        "",
        "## Gate Results",
        "",
    ]
    for result in gate_results:
        lines.append(
            f"- `{result.status}` `{result.severity}` `{result.gate_id}`: "
            f"{result.observed} vs {result.threshold}"
        )
    return "\n".join(lines)
