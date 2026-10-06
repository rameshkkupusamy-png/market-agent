"""All tunable numbers, with the spec's defaults. config.yaml overrides any of them."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, get_type_hints

import yaml


class SettingsError(Exception):
    """config.yaml is missing, or has an unknown section or setting or a wrongly typed value."""


@dataclass(frozen=True)
class StrategySettings:
    min_price: float = 10.0
    min_traded_value: float = 20_000_000.0
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
    starting_cash: float = 10_000.0
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
    # A ticker that left the index and never had data is tried again only this often.
    missing_recheck_days: int = 30


@dataclass(frozen=True)
class BacktestSettings:
    start: date = date(2015, 1, 1)
    tuning_end: date = date(2021, 12, 31)
    test_start: date = date(2022, 1, 1)


@dataclass(frozen=True)
class PaperSettings:
    retry_hours: float = 2.0  # how long run-daily waits for the latest day's data
    retry_every_minutes: float = 15.0
    max_missing_share: float = 0.05  # more eligible tickers failing than this: no trading
    delisted_after_days: int = 5  # sessions without a price before a held share is closed
    settle_minutes: float = 60.0  # a session is processed only this long after its close


@dataclass(frozen=True)
class AiSettings:
    model: str = "claude-opus-5-5"
    effort: str = "low"  # "" leaves it out (Claude Haiku 4.5 rejects it)
    max_tokens: int = 4000
    monthly_cap: float = 5.0  # US$
    input_price: float = 4.0  # US$ per million input tokens (claude-opus-5-5)
    output_price: float = 20.0  # US$ per million output tokens
    max_headlines: int = 15
    news_days: int = 7
    max_reviews_per_day: int = 6  # top-ranked candidates reviewed; the rest count as approved


@dataclass(frozen=True)
class Settings:
    strategy: StrategySettings = field(default_factory=StrategySettings)
    risk: RiskSettings = field(default_factory=RiskSettings)
    data: DataSettings = field(default_factory=DataSettings)
    backtest: BacktestSettings = field(default_factory=BacktestSettings)
    paper: PaperSettings = field(default_factory=PaperSettings)
    ai: AiSettings = field(default_factory=AiSettings)

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
    "paper": PaperSettings,
    "ai": AiSettings,
}


TYPE_NAMES = {float: "a number", int: "a whole number", str: "text", date: "a date (YYYY-MM-DD)"}


def _coerce(where: str, value: Any, kind: type) -> Any:
    """Give each value its field's type, so `2` and `2.0` make the same fingerprint."""
    if kind in (float, int) and isinstance(value, bool):
        pass
    elif kind is float and isinstance(value, int | float):
        return float(value)
    elif kind is int and isinstance(value, int):
        return value
    elif kind is str and isinstance(value, str):
        return value
    elif kind is date and isinstance(value, datetime):
        return value.date()
    elif kind is date and isinstance(value, date):
        return value
    elif kind is date and isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise SettingsError(f"{where} must be {TYPE_NAMES[kind]}, got {value!r}")


def load_settings(path: Path | None, required: bool = False) -> Settings:
    """`required`: the path was given explicitly, so a missing file is an error."""
    raw: Any = {}
    if path is not None and path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    elif path is not None and required:
        raise SettingsError(f"Settings file {path} not found")
    if not isinstance(raw, dict):
        raise SettingsError(f"{path} must be a mapping of sections")
    for name in raw:
        if name not in SECTIONS:
            raise SettingsError(f"Unknown section {name} in {path}")
    parts = {}
    for name, cls in SECTIONS.items():
        values = raw.get(name) or {}
        if not isinstance(values, dict):
            raise SettingsError(f"Section {name} in {path} must be a mapping of settings")
        types = get_type_hints(cls)
        for key in values:
            if key not in types:
                raise SettingsError(f"Unknown setting {name}.{key} in {path}")
        parts[name] = cls(
            **{key: _coerce(f"{name}.{key}", v, types[key]) for key, v in values.items()}
        )
    return Settings(**parts)
