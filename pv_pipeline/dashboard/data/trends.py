"""Pure daily-trend aggregations over concatenated M2 findings sheets.

Every function groups by ``source_date`` (the artifact day added by
``concat_findings_range``) and returns an empty DataFrame when the input is
empty or missing required columns.
"""

from __future__ import annotations

import pandas as pd


def findings_counts_per_day(findings_df: pd.DataFrame) -> pd.DataFrame:
    """Count findings per source_date, sub_module, and severity."""
    cols = ["source_date", "sub_module", "severity", "count"]
    required = {"source_date", "sub_module", "severity"}
    if findings_df is None or findings_df.empty or not required.issubset(findings_df.columns):
        return pd.DataFrame(columns=cols)
    return (
        findings_df.groupby(["source_date", "sub_module", "severity"])
        .size()
        .reset_index(name="count")
    )


def status_counts_per_day(df: pd.DataFrame, status_col: str = "status") -> pd.DataFrame:
    """Count rows and percent share per source_date and status."""
    cols = ["source_date", status_col, "count", "pct"]
    if df is None or df.empty or not {"source_date", status_col}.issubset(df.columns):
        return pd.DataFrame(columns=cols)
    counts = df.groupby(["source_date", status_col]).size().reset_index(name="count")
    totals = counts.groupby("source_date")["count"].transform("sum")
    counts["pct"] = (counts["count"] / totals * 100.0).round(2)
    return counts


def numeric_metric_per_day(
    df: pd.DataFrame,
    value_col: str,
    aggs: tuple[str, ...] = ("mean", "max", "median"),
) -> pd.DataFrame:
    """Aggregate a numeric column per source_date into one column per agg."""
    out_cols = ["source_date", *[f"{value_col}_{agg}" for agg in aggs]]
    if df is None or df.empty or not {"source_date", value_col}.issubset(df.columns):
        return pd.DataFrame(columns=out_cols)
    work = df[["source_date"]].copy()
    work[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    work = work.dropna(subset=[value_col])
    if work.empty:
        return pd.DataFrame(columns=out_cols)
    grouped = work.groupby("source_date")[value_col].agg(list(aggs))
    grouped.columns = [f"{value_col}_{agg}" for agg in aggs]
    return grouped.reset_index()
