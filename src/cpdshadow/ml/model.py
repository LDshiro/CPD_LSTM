from __future__ import annotations

from typing import Any

from cpdshadow.config import CpdLstmArchitectureConfig, CpdLstmModelConfig

try:  # Keep this module importable when the optional ML extra is absent.
    import torch as _torch
    from torch import nn as _nn
except ImportError:  # pragma: no cover - depends on local optional dependency state
    _torch = None
    _nn = None


def require_torch() -> Any:
    if _torch is None:  # pragma: no cover - exercised only without optional dep
        raise RuntimeError(
            "PyTorch is required for CPD-LSTM commands. Install the optional ML extra, "
            "for example: python -m pip install -e .[ml]"
        )
    return _torch


if _nn is not None:

    class CpdLstmNet(_nn.Module):  # type: ignore[misc]
        def __init__(self, architecture: CpdLstmArchitectureConfig) -> None:
            super().__init__()
            self.architecture = architecture
            self.lstm = _nn.LSTM(
                input_size=architecture.input_size,
                hidden_size=architecture.hidden_size,
                num_layers=architecture.num_layers,
                batch_first=True,
            )
            self.dropout = _nn.Dropout(p=architecture.dropout_after_lstm)
            self.head = _nn.Sequential(
                _nn.Linear(architecture.hidden_size, architecture.head_hidden_size),
                _nn.ReLU(),
                _nn.Linear(architecture.head_hidden_size, 1),
                _nn.Tanh(),
            )

        def forward(self, x: Any) -> Any:
            _, (hidden, _) = self.lstm(x)
            last_hidden = hidden[-1]
            return self.head(self.dropout(last_hidden)).squeeze(-1)

else:

    class CpdLstmNet:  # type: ignore[no-redef]
        def __init__(self, architecture: CpdLstmArchitectureConfig) -> None:
            raise RuntimeError(
                "PyTorch is required for CPD-LSTM commands. Install the optional ML extra."
            )


def create_model(config: CpdLstmModelConfig) -> CpdLstmNet:
    require_torch()
    return CpdLstmNet(config.architecture)


def set_global_seed(seed: int) -> None:
    import random

    import numpy as np

    torch = require_torch()
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def resolve_device(device: str) -> Any:
    torch = require_torch()
    if device == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(device)


def set_torch_determinism(mode: str) -> None:
    torch = require_torch()
    if mode == "off":
        return
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    if mode == "strict":
        torch.use_deterministic_algorithms(True)
    elif mode == "warn":
        torch.use_deterministic_algorithms(True, warn_only=True)
    else:
        raise ValueError(f"unknown deterministic mode: {mode}")
