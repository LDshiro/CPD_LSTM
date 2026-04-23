from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from cpdshadow.config import CpdLstmModelConfig
from cpdshadow.ml.model import require_torch


@dataclass(frozen=True)
class PanelLossResult:
    loss: Any
    sharpe: float
    mean_pnl: float
    std_pnl: float
    mean_turnover: float


def sharpe_ex_cost_loss(
    *,
    signals: Any,
    y_norm: Any,
    dates: Sequence[object],
    roots: Sequence[str],
    annualized_vol: Any,
    config: CpdLstmModelConfig,
) -> PanelLossResult:
    torch = require_torch()
    if signals.numel() == 0:
        raise ValueError("cannot compute panel loss for an empty signal tensor")
    cost_bps = float(config.training.cost_bps_default)
    min_vol = float(config.labels.min_annualized_vol)
    annualization = float(config.annualization_factor)
    sigma_daily = torch.clamp(annualized_vol / (annualization**0.5), min=min_vol / (annualization**0.5))
    cost_norm = (cost_bps / 10000.0) / (sigma_daily + config.epsilon)

    turnover_items = []
    previous_by_root: dict[str, Any] = {}
    for idx, root in enumerate(roots):
        signal = signals[idx]
        previous = previous_by_root.get(root)
        if previous is None:
            previous = torch.zeros((), dtype=signals.dtype, device=signals.device)
        turnover_items.append(torch.abs(signal - previous))
        previous_by_root[root] = signal
    turnover = torch.stack(turnover_items)
    asset_pnl = signals * y_norm
    net_asset_pnl = asset_pnl - turnover * cost_norm * config.training.turnover_cost_multiplier

    portfolio_pnls = []
    for current_date in sorted(set(dates)):
        mask = torch.tensor(
            [item == current_date for item in dates],
            dtype=torch.bool,
            device=signals.device,
        )
        if bool(mask.any()):
            portfolio_pnls.append(net_asset_pnl[mask].mean())
    portfolio_pnl = torch.stack(portfolio_pnls)
    mean_pnl = portfolio_pnl.mean()
    std_pnl = portfolio_pnl.std(unbiased=False)
    sharpe = mean_pnl / (std_pnl + config.epsilon)
    l2 = config.training.signal_l2_penalty * torch.mean(signals * signals)
    turnover_penalty = config.training.turnover_l1_penalty * torch.mean(turnover)
    loss = -sharpe + l2 + turnover_penalty
    return PanelLossResult(
        loss=loss,
        sharpe=float(sharpe.detach().cpu().item()),
        mean_pnl=float(mean_pnl.detach().cpu().item()),
        std_pnl=float(std_pnl.detach().cpu().item()),
        mean_turnover=float(turnover.mean().detach().cpu().item()),
    )
