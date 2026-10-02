"""Which tickers could be traded on a given day: historical S&P 500 membership.

Using today's members for past years would hide every company that later failed
(survivorship bias), so membership is looked up by date.
"""

from __future__ import annotations

import csv
import re
from bisect import bisect_right
from pathlib import Path

import pandas as pd

REMOVED_SUFFIX = re.compile(r"-\d{6}$")  # some datasets mark removed tickers as ABC-201912


def clean_ticker(raw: str) -> str:
    return REMOVED_SUFFIX.sub("", raw.strip().upper())


class Universe:
    def __init__(self, snapshots: list[tuple[pd.Timestamp, frozenset[str]]]):
        self._snapshots = sorted(snapshots, key=lambda item: item[0])
        self._days = [day for day, _ in self._snapshots]

    @classmethod
    def from_csv(cls, path: Path) -> Universe:
        frame = pd.read_csv(path)
        snapshots = [
            (
                pd.Timestamp(row["date"]),
                frozenset(clean_ticker(t) for t in str(row["tickers"]).split(",") if t.strip()),
            )
            for _, row in frame.iterrows()
        ]
        return cls(snapshots)

    def members(self, day: pd.Timestamp) -> frozenset[str]:
        index = bisect_right(self._days, day) - 1
        return self._snapshots[index][1] if index >= 0 else frozenset()

    def tickers_between(self, start: pd.Timestamp, end: pd.Timestamp) -> set[str]:
        tickers = set(self.members(start))
        for day, members in self._snapshots:
            if start < day <= end:
                tickers |= members
        return tickers


def load_sectors(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["ticker"]: row["sector"] for row in csv.DictReader(handle)}


def write_sectors(path: Path, sectors: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "sector"])
        for ticker in sorted(sectors):
            writer.writerow([ticker, sectors[ticker]])
