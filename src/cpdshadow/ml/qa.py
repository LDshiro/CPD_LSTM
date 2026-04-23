from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Sequence


@dataclass(frozen=True)
class MlQaIssue:
    code: str
    severity: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TrainQaReport:
    training_run_id: str
    model_id: str
    strategy_id: str
    summary: dict[str, object]
    issues: tuple[MlQaIssue, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    def with_additional_issues(self, issues: Sequence[MlQaIssue]) -> "TrainQaReport":
        merged = tuple(sorted([*self.issues, *issues], key=lambda item: (item.severity, item.code)))
        summary = dict(self.summary)
        summary["fatal_error_count"] = sum(issue.severity == "error" for issue in merged)
        summary["warning_count"] = sum(issue.severity == "warning" for issue in merged)
        return TrainQaReport(
            training_run_id=self.training_run_id,
            model_id=self.model_id,
            strategy_id=self.strategy_id,
            summary=summary,
            issues=merged,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "training_run_id": self.training_run_id,
            "model_id": self.model_id,
            "strategy_id": self.strategy_id,
            "has_errors": self.has_errors,
            "summary": self.summary,
            "issues": [asdict(issue) for issue in self.issues],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# WP9 CPD-LSTM Training QA {self.training_run_id}",
            "",
            f"- Model: `{self.model_id}`",
            f"- Strategy: `{self.strategy_id}`",
            f"- Fatal errors: {self.summary.get('fatal_error_count', 0)}",
            f"- Warnings: {self.summary.get('warning_count', 0)}",
            f"- Train samples: {self.summary.get('n_train_samples', 0)}",
            f"- Validation samples: {self.summary.get('n_val_samples', 0)}",
            f"- Best validation Sharpe ex-cost: {self.summary.get('val_sharpe_ex_cost_best')}",
            "",
            "## Issues",
            "",
        ]
        if not self.issues:
            lines.append("- None")
        else:
            for issue in self.issues:
                details = f" {issue.details}" if issue.details else ""
                lines.append(f"- `{issue.severity}` `{issue.code}`: {issue.message}{details}")
        return "\n".join(lines)
