"""Protects the test period from being used for tuning (spec section 6)."""

from __future__ import annotations

from market_agent.store import Store


class GuardError(Exception):
    """The test period may not be run again with the same settings."""


def check_test_period(store: Store, fingerprint: str) -> list[str]:
    runs = store.backtest_runs("test")
    for run in runs:
        if run["fingerprint"] == fingerprint:
            raise GuardError(
                f"The test period was already run with these exact settings (run {run['id']} on "
                f"{run['created_at']}): total return {run['metrics'].get('total_return')}. "
                "Running it again would give the same answer."
            )
    if not runs:
        return []
    times = "time" if len(runs) == 1 else "times"
    return [
        f"The test period has been run {len(runs)} {times} before with other settings. "
        "Changing settings after seeing a test result is tuning on the test period, "
        "so treat this result with suspicion."
    ]
