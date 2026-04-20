from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator
import yaml


MicroStatus = Literal["approved", "restricted", "none"]
RollAnchor = Literal["last_trade_date", "first_notice_or_last_trade_date"]
AssetClass = Literal[
    "equity_index",
    "rates",
    "fx",
    "metals",
    "energy",
    "agriculture",
]


class MicroSubstitute(BaseModel):
    root: str | None = None
    available: bool = False
    ratio_to_standard: float | None = Field(default=None, gt=0)
    status: MicroStatus = "none"
    default_auto_substitute: bool = False
    note: str = ""

    @model_validator(mode="after")
    def validate_internal_consistency(self) -> "MicroSubstitute":
        if self.available and not self.root:
            raise ValueError("micro substitute marked available but root is missing")
        if not self.available and self.status != "none":
            raise ValueError("unavailable micro substitute must use status='none'")
        if self.status == "none" and self.available:
            raise ValueError("status='none' cannot be used when a micro substitute is available")
        if self.root is None and self.ratio_to_standard is not None:
            raise ValueError("ratio_to_standard requires a micro root")
        return self


class Instrument(BaseModel):
    root: str
    display_name: str
    exchange: Literal["CME", "CBOT", "COMEX", "NYMEX"]
    venue: str = "CME Globex"
    asset_class: AssetClass
    sub_asset_class: str
    currency: str = "USD"
    price_quote: str
    contract_unit_text: str
    quote_multiplier_to_usd_notional: float = Field(gt=0)
    min_price_increment: float = Field(gt=0)
    tick_value_usd: float = Field(gt=0)
    hard_roll_anchor: RollAnchor
    hard_roll_business_days_before: int = Field(ge=1)
    micro_substitute: MicroSubstitute = Field(default_factory=MicroSubstitute)

    @model_validator(mode="after")
    def validate_tick_value(self) -> "Instrument":
        expected = self.min_price_increment * self.quote_multiplier_to_usd_notional
        if abs(expected - self.tick_value_usd) > 1e-9:
            raise ValueError(
                f"tick_value_usd mismatch for {self.root}: expected {expected}, got {self.tick_value_usd}"
            )
        return self


class InstrumentPolicy(BaseModel):
    standard_contracts_are_primary: bool = True
    auto_micro_substitution_enabled: bool = False
    roll_primary_rule: str = "volume_lead_3day"
    roll_volume_field: str = "official_daily_volume"
    signal_price_priority: list[str] = Field(default_factory=list)
    continuous_method: str = "backward_ratio_adjusted"
    databento_dataset: str = "GLBX.MDP3"
    timezone: str = "America/Chicago"


class InstrumentMaster(BaseModel):
    version: str
    status: str
    effective_date: date
    description: str
    policy: InstrumentPolicy = Field(default_factory=InstrumentPolicy)
    hard_roll_defaults: dict[str, dict[str, str | int]] = Field(default_factory=dict)
    fields_guide: dict[str, object] = Field(default_factory=dict)
    instruments: list[Instrument] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_universe(self) -> "InstrumentMaster":
        roots = [instrument.root for instrument in self.instruments]
        if len(roots) != len(set(roots)):
            raise ValueError("instrument roots must be unique")
        return self


def load_instrument_master(path: str | Path) -> InstrumentMaster:
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return InstrumentMaster.model_validate(raw)
