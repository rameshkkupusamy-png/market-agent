"""Protects the test period from being used for tuning (spec section 6)."""

from __future__ import annotations

from market_agent.store import Store


class GuardError(Exception):
    """The test period may not be run again with the same settings."""


def check_test_period(store: Store, fingerprint: str) -> list[str]:
    """Runs covering the test period are the "test" and "full" periods."""
    runs = sorted(
        store.backtest_runs("test") + store.backtest_runs("full"), key=lambda run: run["id"]
    )
    for run in runs:
        if run["fingerprint"] == fingerprint:
            total = run["metrics"].get("total_return")
            shown = f"{100 * total:+.1f}%" if isinstance(total, int | float) else "unknown"
            raise GuardError(
                f"These settings already ran the test period (run {run['id']} on "
                f"{run['created_at']}): total return {shown}. "
                "The test period was already run with these exact settings. Changing the "
                "settings allows a new run, but that counts as tuning on the test period."
            )
    if not runs:
        return []
    times = "time" if len(runs) == 1 else "times"
    return [
        f"The test period has been run {len(runs)} {times} before with other settings. "
        "Changing settings after seeing a test result is tuning on the test period, "
        "so treat this result with suspicion."
    ]
