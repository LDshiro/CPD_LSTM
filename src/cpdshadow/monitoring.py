from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import pstdev
from typing import Iterable, Literal

from cpdshadow.config import MonitoringConfig
from cpdshadow.portfolio import PortfolioDiagnostics


AlertCategory = Literal["data_quality", "model_health", "risk", "execution", "strategy"]
AlertSeverity = Literal["info", "warning", "critical"]
ControlAction = Literal["run_cpd_lstm", "fallback_tsmom", "hold", "reduce_only"]

_ACTION_PRIORITY: dict[ControlAction, int] = {
    "run_cpd_lstm": 0,
    "fallback_tsmom": 1,
    "hold": 2,
    "reduce_only": 3,
}

_SEVERITY_PRIORITY: dict[AlertSeverity, int] = {
    "info": 0,
    "warning": 1,
    "critical": 2,
}


@dataclass(frozen=True)
class MonitoringAlert:
    code: str
    category: AlertCategory
    severity: AlertSeverity
    recommended_action: ControlAction
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DataQualitySnapshot:
    missing_settlement_roots: tuple[str, ...] = ()
    missing_volume_roots: tuple[str, ...] = ()
    contract_map_conflicts: tuple[str, ...] = ()
    stale_feature_roots: tuple[str, ...] = ()
    feature_missing_fraction: float = 0.0
    duplicate_feature_rows: int = 0


@dataclass(frozen=True)
class ModelHealthSnapshot:
    artifact_hash_match: bool = True
    cpd_service_ok: bool = True
    inference_ok: bool = True
    nonfinite_signal_roots: tuple[str, ...] = ()
    signal_saturation_fraction: float = 0.0
    cross_sectional_signal_std: float = 0.10
    feature_psi: float = 0.0


@dataclass(frozen=True)
class RiskHealthSnapshot:
    realized_vol_20d: float = 0.0
    margin_usage_fraction: float = 0.0
    margin_hard_cap_pre_scale_breached: bool = False
    position_reconciliation_mismatch_count: int = 0
    total_initial_margin_usd: float = 0.0


@dataclass(frozen=True)
class ExecutionHealthSnapshot:
    broker_state_known: bool = True
    api_disconnect: bool = False
    order_reject_count: int = 0
    fill_ratio: float = 1.0
    slippage_ratio_vs_modeled: float = 1.0


@dataclass(frozen=True)
class StrategyHealthSnapshot:
    cpd_60d_net_sharpe: float = 0.0
    tsmom_60d_net_sharpe: float = 0.0
    consecutive_cpd_underperformance_windows: int = 0
    reversal_edge_usd: float = 0.0


@dataclass(frozen=True)
class MonitoringDecision:
    final_action: ControlAction
    highest_severity: AlertSeverity
    alerts: tuple[MonitoringAlert, ...]
    kill_switch_engaged: bool
    fallback_active: bool
    can_rebalance: bool
    reduce_only: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "final_action": self.final_action,
            "highest_severity": self.highest_severity,
            "kill_switch_engaged": self.kill_switch_engaged,
            "fallback_active": self.fallback_active,
            "can_rebalance": self.can_rebalance,
            "reduce_only": self.reduce_only,
            "alerts": [asdict(alert) for alert in self.alerts],
        }



def _normalize_tuple(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))



def _alert(
    *,
    code: str,
    category: AlertCategory,
    severity: AlertSeverity,
    recommended_action: ControlAction,
    message: str,
    details: dict[str, object] | None = None,
) -> MonitoringAlert:
    return MonitoringAlert(
        code=code,
        category=category,
        severity=severity,
        recommended_action=recommended_action,
        message=message,
        details=details or {},
    )



def signal_saturation_fraction(signals: Iterable[float], *, abs_threshold: float = 0.95) -> float:
    values = [abs(float(signal)) for signal in signals]
    if not values:
        return 0.0
    saturated = sum(value >= abs_threshold for value in values)
    return saturated / len(values)



def cross_sectional_signal_std(signals: Iterable[float]) -> float:
    values = [float(signal) for signal in signals]
    if len(values) <= 1:
        return 0.0
    return float(pstdev(values))



def risk_health_from_portfolio_diagnostics(
    diagnostics: PortfolioDiagnostics,
    *,
    nav_usd: float,
    realized_vol_20d: float,
    position_reconciliation_mismatch_count: int = 0,
) -> RiskHealthSnapshot:
    if nav_usd <= 0:
        raise ValueError("nav_usd must be positive")
    return RiskHealthSnapshot(
        realized_vol_20d=realized_vol_20d,
        margin_usage_fraction=diagnostics.total_initial_margin_usd / nav_usd,
        margin_hard_cap_pre_scale_breached=diagnostics.margin_hard_cap_pre_scale_breached,
        position_reconciliation_mismatch_count=position_reconciliation_mismatch_count,
        total_initial_margin_usd=diagnostics.total_initial_margin_usd,
    )



def evaluate_data_quality(
    snapshot: DataQualitySnapshot,
    cfg: MonitoringConfig,
) -> list[MonitoringAlert]:
    alerts: list[MonitoringAlert] = []
    data_cfg = cfg.data_quality

    if len(snapshot.missing_settlement_roots) > data_cfg.max_missing_settlement_roots:
        alerts.append(_alert(
            code="DQ_MISSING_SETTLEMENT",
            category="data_quality",
            severity="critical",
            recommended_action="hold",
            message="Settlement data is missing for one or more roots; skip rebalance.",
            details={"roots": list(_normalize_tuple(snapshot.missing_settlement_roots))},
        ))
    if len(snapshot.missing_volume_roots) > data_cfg.max_missing_volume_roots:
        alerts.append(_alert(
            code="DQ_MISSING_VOLUME",
            category="data_quality",
            severity="critical",
            recommended_action="hold",
            message="Official daily volume is missing; roll logic is unsafe.",
            details={"roots": list(_normalize_tuple(snapshot.missing_volume_roots))},
        ))
    if len(snapshot.contract_map_conflicts) > data_cfg.max_contract_map_conflicts:
        alerts.append(_alert(
            code="DQ_CONTRACT_MAP_CONFLICT",
            category="data_quality",
            severity="critical",
            recommended_action="hold",
            message="Lead-contract mapping conflicts detected.",
            details={"roots": list(_normalize_tuple(snapshot.contract_map_conflicts))},
        ))
    if snapshot.duplicate_feature_rows > data_cfg.max_duplicate_feature_rows:
        alerts.append(_alert(
            code="DQ_DUPLICATE_FEATURE_ROWS",
            category="data_quality",
            severity="critical",
            recommended_action="hold",
            message="Duplicate feature rows detected; feature table is not trustworthy.",
            details={"duplicate_feature_rows": snapshot.duplicate_feature_rows},
        ))
    if len(snapshot.stale_feature_roots) > data_cfg.max_stale_feature_roots:
        alerts.append(_alert(
            code="DQ_STALE_FEATURES",
            category="data_quality",
            severity="critical",
            recommended_action="fallback_tsmom",
            message="Feature freshness failed for the champion model; use fallback strategy.",
            details={"roots": list(_normalize_tuple(snapshot.stale_feature_roots))},
        ))
    if snapshot.feature_missing_fraction > data_cfg.max_feature_missing_fraction:
        alerts.append(_alert(
            code="DQ_FEATURE_MISSINGNESS",
            category="data_quality",
            severity="critical",
            recommended_action="fallback_tsmom",
            message="Feature missingness exceeds the v1.0 tolerance for CPD-LSTM.",
            details={"feature_missing_fraction": snapshot.feature_missing_fraction},
        ))
    return alerts



def evaluate_model_health(
    snapshot: ModelHealthSnapshot,
    cfg: MonitoringConfig,
) -> list[MonitoringAlert]:
    alerts: list[MonitoringAlert] = []
    model_cfg = cfg.model_health

    if model_cfg.require_artifact_hash_match and not snapshot.artifact_hash_match:
        alerts.append(_alert(
            code="MODEL_ARTIFACT_MISMATCH",
            category="model_health",
            severity="critical",
            recommended_action="fallback_tsmom",
            message="Model artifact hash mismatch; champion model is not trusted.",
        ))
    if model_cfg.require_cpd_service_success and not snapshot.cpd_service_ok:
        alerts.append(_alert(
            code="MODEL_CPD_SERVICE_FAILURE",
            category="model_health",
            severity="critical",
            recommended_action="fallback_tsmom",
            message="CPD feature service failed; champion model cannot run safely.",
        ))
    if not snapshot.inference_ok:
        alerts.append(_alert(
            code="MODEL_INFERENCE_FAILURE",
            category="model_health",
            severity="critical",
            recommended_action="fallback_tsmom",
            message="Champion inference failed.",
        ))
    if len(snapshot.nonfinite_signal_roots) > model_cfg.max_nonfinite_signal_roots:
        alerts.append(_alert(
            code="MODEL_NONFINITE_SIGNALS",
            category="model_health",
            severity="critical",
            recommended_action="fallback_tsmom",
            message="Champion model produced non-finite signals.",
            details={"roots": list(_normalize_tuple(snapshot.nonfinite_signal_roots))},
        ))
    if snapshot.signal_saturation_fraction > model_cfg.max_signal_saturation_fraction:
        alerts.append(_alert(
            code="MODEL_SIGNAL_SATURATION",
            category="model_health",
            severity="critical",
            recommended_action="fallback_tsmom",
            message="Champion signal saturation exceeds the v1.0 threshold.",
            details={"signal_saturation_fraction": snapshot.signal_saturation_fraction},
        ))
    if snapshot.cross_sectional_signal_std < model_cfg.min_cross_sectional_signal_std:
        alerts.append(_alert(
            code="MODEL_LOW_SIGNAL_DISPERSION",
            category="model_health",
            severity="warning",
            recommended_action="fallback_tsmom",
            message="Cross-sectional signal dispersion is abnormally low.",
            details={"cross_sectional_signal_std": snapshot.cross_sectional_signal_std},
        ))
    if snapshot.feature_psi > model_cfg.max_feature_psi:
        alerts.append(_alert(
            code="MODEL_FEATURE_DRIFT",
            category="model_health",
            severity="warning",
            recommended_action="fallback_tsmom",
            message="Feature PSI exceeds the v1.0 drift tolerance.",
            details={"feature_psi": snapshot.feature_psi},
        ))
    return alerts



def evaluate_risk_health(
    snapshot: RiskHealthSnapshot,
    cfg: MonitoringConfig,
) -> list[MonitoringAlert]:
    alerts: list[MonitoringAlert] = []
    risk_cfg = cfg.risk_health

    if snapshot.margin_hard_cap_pre_scale_breached:
        alerts.append(_alert(
            code="RISK_MARGIN_HARD_BREACH",
            category="risk",
            severity="critical",
            recommended_action="reduce_only",
            message="Margin hard cap was breached before scaling; only risk-reducing orders are allowed.",
            details={"margin_usage_fraction": snapshot.margin_usage_fraction},
        ))
    if snapshot.position_reconciliation_mismatch_count > risk_cfg.max_position_reconciliation_mismatches:
        alerts.append(_alert(
            code="RISK_POSITION_MISMATCH",
            category="risk",
            severity="critical",
            recommended_action="reduce_only",
            message="Position reconciliation mismatch detected.",
            details={"position_reconciliation_mismatch_count": snapshot.position_reconciliation_mismatch_count},
        ))
    if snapshot.realized_vol_20d >= risk_cfg.realized_vol_reduce_only:
        alerts.append(_alert(
            code="RISK_REALIZED_VOL_REDUCE_ONLY",
            category="risk",
            severity="critical",
            recommended_action="reduce_only",
            message="20-day realized volatility exceeds the reduce-only threshold.",
            details={"realized_vol_20d": snapshot.realized_vol_20d},
        ))
    elif snapshot.realized_vol_20d >= risk_cfg.realized_vol_warning:
        alerts.append(_alert(
            code="RISK_REALIZED_VOL_WARNING",
            category="risk",
            severity="warning",
            recommended_action="run_cpd_lstm",
            message="20-day realized volatility is elevated.",
            details={"realized_vol_20d": snapshot.realized_vol_20d},
        ))

    if snapshot.margin_usage_fraction >= risk_cfg.margin_usage_reduce_only:
        alerts.append(_alert(
            code="RISK_MARGIN_USAGE_REDUCE_ONLY",
            category="risk",
            severity="critical",
            recommended_action="reduce_only",
            message="Margin usage exceeds the reduce-only threshold.",
            details={"margin_usage_fraction": snapshot.margin_usage_fraction},
        ))
    elif snapshot.margin_usage_fraction >= risk_cfg.margin_usage_warning:
        alerts.append(_alert(
            code="RISK_MARGIN_USAGE_WARNING",
            category="risk",
            severity="warning",
            recommended_action="run_cpd_lstm",
            message="Margin usage is elevated.",
            details={"margin_usage_fraction": snapshot.margin_usage_fraction},
        ))
    return alerts



def evaluate_execution_health(
    snapshot: ExecutionHealthSnapshot,
    cfg: MonitoringConfig,
) -> list[MonitoringAlert]:
    alerts: list[MonitoringAlert] = []
    execution_cfg = cfg.execution_health

    if (execution_cfg.require_broker_state_known and not snapshot.broker_state_known) or snapshot.api_disconnect:
        alerts.append(_alert(
            code="EXEC_BROKER_STATE_AMBIGUOUS",
            category="execution",
            severity="critical",
            recommended_action="reduce_only",
            message="Broker state is ambiguous or the session is disconnected.",
            details={
                "broker_state_known": snapshot.broker_state_known,
                "api_disconnect": snapshot.api_disconnect,
            },
        ))
    if snapshot.order_reject_count > execution_cfg.max_order_reject_count:
        alerts.append(_alert(
            code="EXEC_ORDER_REJECTS",
            category="execution",
            severity="critical",
            recommended_action="reduce_only",
            message="Order rejects exceed the allowed threshold.",
            details={"order_reject_count": snapshot.order_reject_count},
        ))
    if snapshot.slippage_ratio_vs_modeled >= execution_cfg.max_slippage_ratio_fallback:
        alerts.append(_alert(
            code="EXEC_SLIPPAGE_REGIME_BREAK",
            category="execution",
            severity="critical",
            recommended_action="fallback_tsmom",
            message="Observed slippage materially exceeds the modeled baseline.",
            details={"slippage_ratio_vs_modeled": snapshot.slippage_ratio_vs_modeled},
        ))
    elif snapshot.slippage_ratio_vs_modeled >= execution_cfg.max_slippage_ratio_warning:
        alerts.append(_alert(
            code="EXEC_SLIPPAGE_WARNING",
            category="execution",
            severity="warning",
            recommended_action="run_cpd_lstm",
            message="Observed slippage is elevated versus the modeled baseline.",
            details={"slippage_ratio_vs_modeled": snapshot.slippage_ratio_vs_modeled},
        ))
    if snapshot.fill_ratio < execution_cfg.min_fill_ratio_warning:
        alerts.append(_alert(
            code="EXEC_FILL_RATIO_WARNING",
            category="execution",
            severity="warning",
            recommended_action="run_cpd_lstm",
            message="Fill ratio fell below the monitoring threshold.",
            details={"fill_ratio": snapshot.fill_ratio},
        ))
    return alerts



def evaluate_strategy_health(
    snapshot: StrategyHealthSnapshot,
    cfg: MonitoringConfig,
) -> list[MonitoringAlert]:
    alerts: list[MonitoringAlert] = []
    strategy_cfg = cfg.strategy_health

    if (
        snapshot.cpd_60d_net_sharpe < strategy_cfg.min_cpd_60d_net_sharpe_before_review
        and snapshot.cpd_60d_net_sharpe < snapshot.tsmom_60d_net_sharpe
    ):
        alerts.append(_alert(
            code="STRAT_SHORT_HORIZON_UNDERPERFORMANCE",
            category="strategy",
            severity="warning",
            recommended_action="fallback_tsmom",
            message="Champion short-horizon Sharpe is below the review threshold and trailing TSMOM.",
            details={
                "cpd_60d_net_sharpe": snapshot.cpd_60d_net_sharpe,
                "tsmom_60d_net_sharpe": snapshot.tsmom_60d_net_sharpe,
            },
        ))
    if (
        snapshot.consecutive_cpd_underperformance_windows >= strategy_cfg.max_consecutive_cpd_underperformance_windows
        and snapshot.reversal_edge_usd <= strategy_cfg.min_reversal_edge_usd
    ):
        alerts.append(_alert(
            code="STRAT_REVERSAL_EDGE_LOST",
            category="strategy",
            severity="critical",
            recommended_action="fallback_tsmom",
            message="Champion no longer leads in the reversal bucket over repeated windows.",
            details={
                "consecutive_cpd_underperformance_windows": snapshot.consecutive_cpd_underperformance_windows,
                "reversal_edge_usd": snapshot.reversal_edge_usd,
            },
        ))
    return alerts



def _highest_severity(alerts: Iterable[MonitoringAlert]) -> AlertSeverity:
    highest = "info"
    for alert in alerts:
        if _SEVERITY_PRIORITY[alert.severity] > _SEVERITY_PRIORITY[highest]:
            highest = alert.severity
    return highest  # type: ignore[return-value]



def _final_action(alerts: Iterable[MonitoringAlert]) -> ControlAction:
    action: ControlAction = "run_cpd_lstm"
    for alert in alerts:
        if _ACTION_PRIORITY[alert.recommended_action] > _ACTION_PRIORITY[action]:
            action = alert.recommended_action
    return action



def evaluate_monitoring_state(
    *,
    config: MonitoringConfig,
    data_quality: DataQualitySnapshot | None = None,
    model_health: ModelHealthSnapshot | None = None,
    risk_health: RiskHealthSnapshot | None = None,
    execution_health: ExecutionHealthSnapshot | None = None,
    strategy_health: StrategyHealthSnapshot | None = None,
) -> MonitoringDecision:
    alerts: list[MonitoringAlert] = []
    if data_quality is not None:
        alerts.extend(evaluate_data_quality(data_quality, config))
    if model_health is not None:
        alerts.extend(evaluate_model_health(model_health, config))
    if risk_health is not None:
        alerts.extend(evaluate_risk_health(risk_health, config))
    if execution_health is not None:
        alerts.extend(evaluate_execution_health(execution_health, config))
    if strategy_health is not None:
        alerts.extend(evaluate_strategy_health(strategy_health, config))

    alerts_sorted = tuple(
        sorted(
            alerts,
            key=lambda alert: (
                -_ACTION_PRIORITY[alert.recommended_action],
                -_SEVERITY_PRIORITY[alert.severity],
                alert.category,
                alert.code,
            ),
        )
    )
    final_action = _final_action(alerts_sorted)
    highest_severity = _highest_severity(alerts_sorted)
    return MonitoringDecision(
        final_action=final_action,
        highest_severity=highest_severity,
        alerts=alerts_sorted,
        kill_switch_engaged=final_action in {"hold", "reduce_only"},
        fallback_active=final_action == "fallback_tsmom",
        can_rebalance=final_action in {"run_cpd_lstm", "fallback_tsmom"},
        reduce_only=final_action == "reduce_only",
    )
