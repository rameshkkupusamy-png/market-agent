from datetime import date

import pytest

from helpers import settings_with
from market_agent.settings import Settings, SettingsError, load_settings


def test_defaults_match_the_spec():
    s = Settings()
    assert s.strategy.min_price == 10.0
    assert s.strategy.min_traded_value == 20_000_000
    assert s.strategy.volume_ratio == 1.5
    assert (s.strategy.stop_atr, s.strategy.target_atr) == (2.0, 4.0)
    assert s.strategy.max_hold_days == 20
    assert s.strategy.max_new_per_day == 3
    assert s.risk.starting_cash == 10_000
    assert s.risk.risk_per_trade == 0.01
    assert s.risk.max_position_pct == 0.10
    assert (s.risk.max_positions, s.risk.max_per_sector) == (10, 3)
    assert s.risk.breaker_drawdown == 0.15
    assert (s.risk.commission, s.risk.slippage) == (1.0, 0.001)
    assert s.backtest.tuning_end == date(2021, 12, 31)


def test_missing_file_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "none.yaml") == Settings()


def test_file_overrides_some_values(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("strategy:\n  volume_ratio: 2.0\nrisk:\n  max_positions: 5\n", "utf-8")
    s = load_settings(path)
    assert s.strategy.volume_ratio == 2.0
    assert s.risk.max_positions == 5
    assert s.strategy.stop_atr == 2.0


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("strategy:\n  volume_ratoi: 2\n", "Unknown setting strategy.volume_ratoi"),
        ("riks:\n  max_positions: 2\n", "Unknown section riks"),
    ],
)
def test_unknown_keys_are_rejected(tmp_path, text, message):
    path = tmp_path / "config.yaml"
    path.write_text(text, "utf-8")
    with pytest.raises(SettingsError, match=message):
        load_settings(path)


def test_fingerprint_changes_with_strategy_or_risk_only():
    base = Settings().fingerprint()
    assert len(base) == 12
    assert settings_with(strategy={"volume_ratio": 2.0}).fingerprint() != base
    assert settings_with(risk={"max_positions": 5}).fingerprint() != base
    assert settings_with(data={"db_path": "other.db"}).fingerprint() == base
