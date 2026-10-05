"""Formatting shared by the dashboard pages: percentages, plain dates and value charts."""

from __future__ import annotations

import altair as alt
import pandas as pd

DATE_COLUMNS = {"day", "start", "end"}


def as_percent(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Fractions as percentages with one decimal, the column renamed '<name> (%)'."""
    out = frame.copy()
    present = [c for c in columns if c in out.columns]
    for column in present:
        out[column] = (pd.to_numeric(out[column], errors="coerce") * 100).round(1)
    return out.rename(columns={c: f"{c} (%)" for c in present})


def dates_only(frame: pd.DataFrame) -> pd.DataFrame:
    """Day columns as plain dates, so tables don't print a midnight time on every row."""
    out = frame.copy()
    for column in out.columns:
        if column in DATE_COLUMNS or str(column).endswith("_day"):
            out[column] = pd.to_datetime(out[column], errors="coerce").dt.date
    return out


def value_chart(curves: pd.DataFrame) -> alt.Chart:
    """US$ value over time, one line per column. The y axis fits the values instead of
    starting at zero, so a move of a few hundred dollars on 10,000 stays visible; points
    mark each day, so even a single day shows."""
    long = (
        curves.rename_axis("day").reset_index().melt("day", var_name="series", value_name="value")
    )
    return (
        alt.Chart(long)
        .mark_line(point=True)
        .encode(
            x=alt.X("day:T", title=None),
            y=alt.Y(
                "value:Q",
                title="US$",
                scale=alt.Scale(zero=False),
                axis=alt.Axis(format=",.0f"),
            ),
            color=alt.Color("series:N", title=None),
            tooltip=["day:T", "series:N", alt.Tooltip("value:Q", format=",.0f")],
        )
    )
