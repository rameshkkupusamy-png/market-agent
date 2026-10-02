"""All tunable numbers, with the spec's defaults. config.yaml overrides any of them."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from pathlib import Path

import yaml


class SettingsError(Exception):
    """config.yaml has an unknown section or setting."""


@dataclass(frozen=True)
class StrategySettings:
    min_price: float = 10.0
    min_traded_value: float = 20_000_000
    sma_fast: int = 50
    sma_slow: int = 200
    breakout_days: int = 20
    volume_days: int = 20
    volume_ratio: float = 1.5
    atr_days: int = 14
    rank_days: int = 63
    earnings_buffer_days: int = 5
    stop_atr: float = 2.0
    target_atr: float = 4.0
    max_hold_days: int = 20
    max_new_per_day: int = 3


@dataclass(frozen=True)
class RiskSettings:
    starting_cash: float = 10_000
    risk_per_trade: float = 0.01
    max_position_pct: float = 0.10
    max_positions: int = 10
    max_per_sector: int = 3
    breaker_drawdown: float = 0.15
    commission: float = 1.0
    slippage: float = 0.001


@dataclass(frozen=True)
class DataSettings:
    db_path: str = "data/market.db"
    membership_csv: str = "data/sp500_membership.csv"
    sectors_csv: str = "data/sectors.csv"
    history_start: date = date(2014, 1, 1)  # a year of warm-up for the 200-day average
    benchmark: str = "SPY"
    max_daily_jump: float = 0.40


@dataclass(frozen=True)
class BacktestSettings:
    start: date = date(2015, 1, 1)
    tuning_end: date = date(2021, 12, 31)
    test_start: date = date(2022, 1, 1)


@dataclass(frozen=True)
class Settings:
    strategy: StrategySettings = field(default_factory=StrategySettings)
    risk: RiskSettings = field(default_factory=RiskSettings)
    data: DataSettings = field(default_factory=DataSettings)
    backtest: BacktestSettings = field(default_factory=BacktestSettings)

    def fingerprint(self) -> str:
        """Identifies the trading rules: changes when any strategy or risk number changes."""
        text = json.dumps(
            {"strategy": asdict(self.strategy), "risk": asdict(self.risk)},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(text.encode()).hexdigest()[:12]


SECTIONS = {
    "strategy": StrategySettings,
    "risk": RiskSettings,
    "data": DataSettings,
    "backtest": BacktestSettings,
}


def load_settings(path: Path | None) -> Settings:
    raw = {}
    if path is not None and path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for name in raw:
        if name not in SECTIONS:
            raise SettingsError(f"Unknown section {name} in {path}")
    parts = {}
    for name, cls in SECTIONS.items():
        values = raw.get(name) or {}
        allowed = {f.name for f in fields(cls)}
        for key in values:
            if key not in allowed:
                raise SettingsError(f"Unknown setting {name}.{key} in {path}")
        parts[name] = cls(**values)
    return Settings(**parts)
