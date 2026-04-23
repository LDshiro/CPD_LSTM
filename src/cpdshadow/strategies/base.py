from __future__ import annotations

from typing import Protocol

import pandas as pd

from cpdshadow.signals import SignalBuildRequest, SignalBuildResult


class SignalStrategy(Protocol):
    strategy_id: str
    model_id: str
    signal_version: str

    def build_signals(
        self, features: pd.DataFrame, request: SignalBuildRequest
    ) -> SignalBuildResult: ...
