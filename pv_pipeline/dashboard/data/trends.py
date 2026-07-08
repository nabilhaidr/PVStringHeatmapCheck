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


def soiling_ratio_per_day(econ_df: pd.DataFrame) -> pd.DataFrame:
    """Return soiling ratio and confidence interval columns per source_date."""
    cols = ["source_date", "soiling_ratio", "sr_ci_lower", "sr_ci_upper", "recommend_cleaning"]
    if (
        econ_df is None
        or econ_df.empty
        or not {"source_date", "soiling_ratio"}.issubset(econ_df.columns)
    ):
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame({"source_date": econ_df["source_date"].to_numpy()})
    for col in ["soiling_ratio", "sr_ci_lower", "sr_ci_upper"]:
        out[col] = (
            pd.to_numeric(econ_df[col], errors="coerce").to_numpy()
            if col in econ_df.columns
            else pd.NA
        )
    out["recommend_cleaning"] = (
        econ_df["recommend_cleaning"].to_numpy()
        if "recommend_cleaning" in econ_df.columns
        else pd.NA
    )
    return out.reset_index(drop=True)


def wide_counts_per_day(df: pd.DataFrame, count_cols: list[str]) -> pd.DataFrame:
    """Melt wide count columns into classification rows per source_date."""
    cols = ["source_date", "classification", "count"]
    if df is None or df.empty or "source_date" not in df.columns:
        return pd.DataFrame(columns=cols)
    present = [col for col in count_cols if col in df.columns]
    if not present:
        return pd.DataFrame(columns=cols)
    long = df.melt(
        id_vars="source_date",
        value_vars=present,
        var_name="classification",
        value_name="count",
    )
    long["count"] = pd.to_numeric(long["count"], errors="coerce").fillna(0)
    return long.groupby(["source_date", "classification"], as_index=False)["count"].sum()
