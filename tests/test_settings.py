from datetime import date
from typing import get_type_hints

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


def test_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(SettingsError, match="not found"):
        load_settings(tmp_path / "none.yaml", required=True)


def test_whole_numbers_give_the_same_fingerprint_as_the_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("strategy:\n  stop_atr: 2\nrisk:\n  starting_cash: 10000\n", "utf-8")
    s = load_settings(path)
    assert isinstance(s.strategy.stop_atr, float)
    assert s.fingerprint() == Settings().fingerprint()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("strategy:\n  stop_atr: two\n", "strategy.stop_atr"),
        ("risk:\n  max_positions: 2.5\n", "risk.max_positions"),
        ("backtest:\n  test_start: soon\n", "backtest.test_start"),
        ("strategy: 5\n", "Section strategy"),
        ("- strategy\n", "mapping"),
    ],
)
def test_bad_values_are_rejected(tmp_path, text, message):
    path = tmp_path / "config.yaml"
    path.write_text(text, "utf-8")
    with pytest.raises(SettingsError, match=message):
        load_settings(path)


def test_defaults_have_their_declared_types():
    for section in (Settings().strategy, Settings().risk, Settings().data, Settings().backtest):
        for name, kind in get_type_hints(type(section)).items():
            assert type(getattr(section, name)) is kind, name


def test_paper_and_ai_defaults_and_overrides(tmp_path):
    s = Settings()
    assert (s.paper.retry_hours, s.paper.max_missing_share, s.paper.delisted_after_days) == (
        2.0,
        0.05,
        5,
    )
    assert (s.ai.model, s.ai.effort, s.ai.monthly_cap) == ("claude-opus-5-5", "low", 5.0)
    path = tmp_path / "config.yaml"
    path.write_text("ai:\n  model: claude-sonnet-5-5\n  input_price: 2\n", encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.ai.model == "claude-sonnet-5-5"
    assert loaded.ai.input_price == 2.0


def test_ai_and_paper_settings_do_not_change_the_fingerprint():
    changed = settings_with(ai={"model": "claude-haiku-4-5"}, paper={"retry_hours": 1.0})
    assert changed.fingerprint() == Settings().fingerprint()
