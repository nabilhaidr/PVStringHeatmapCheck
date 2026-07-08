from __future__ import annotations

from datetime import date

import pandas as pd

from pv_pipeline.dashboard.data.trends import (
    findings_counts_per_day,
    numeric_metric_per_day,
    status_counts_per_day,
)


def test_findings_counts_per_day_groups_by_day_submodule_severity():
    df = pd.DataFrame({
        "source_date": [date(2026, 5, 1), date(2026, 5, 1), date(2026, 5, 2)],
        "sub_module": ["M2a_soiling", "M2a_soiling", "M2a_soiling"],
        "severity": ["HIGH", "HIGH", "HIGH"],
    })
    out = findings_counts_per_day(df)
    day1 = out[out["source_date"] == date(2026, 5, 1)]
    assert int(day1["count"].iloc[0]) == 2
    day2 = out[out["source_date"] == date(2026, 5, 2)]
    assert int(day2["count"].iloc[0]) == 1


def test_findings_counts_per_day_missing_columns_returns_empty():
    out = findings_counts_per_day(pd.DataFrame({"foo": [1]}))
    assert out.empty
    assert list(out.columns) == ["source_date", "sub_module", "severity", "count"]


def test_status_counts_per_day_computes_count_and_pct():
    df = pd.DataFrame({
        "source_date": [date(2026, 5, 1)] * 4,
        "status": ["NORMAL", "NORMAL", "NORMAL", "high_R"],
    })
    out = status_counts_per_day(df)
    normal = out[out["status"] == "NORMAL"]
    assert int(normal["count"].iloc[0]) == 3
    assert float(normal["pct"].iloc[0]) == 75.0


def test_status_counts_per_day_missing_column_returns_empty():
    out = status_counts_per_day(pd.DataFrame({"source_date": [date(2026, 5, 1)]}))
    assert out.empty


def test_numeric_metric_per_day_aggregates_mean_max_median():
    df = pd.DataFrame({
        "source_date": [date(2026, 5, 1), date(2026, 5, 1), date(2026, 5, 2)],
        "flagged_pct": [10.0, 20.0, 5.0],
    })
    out = numeric_metric_per_day(df, "flagged_pct")
    day1 = out[out["source_date"] == date(2026, 5, 1)].iloc[0]
    assert day1["flagged_pct_mean"] == 15.0
    assert day1["flagged_pct_max"] == 20.0
    assert day1["flagged_pct_median"] == 15.0


def test_numeric_metric_per_day_coerces_and_drops_non_numeric():
    df = pd.DataFrame({
        "source_date": [date(2026, 5, 1), date(2026, 5, 1)],
        "ratio_median_daylight": [1.0, None],
    })
    out = numeric_metric_per_day(df, "ratio_median_daylight", aggs=("median",))
    assert out["ratio_median_daylight_median"].iloc[0] == 1.0


def test_numeric_metric_per_day_missing_column_returns_empty():
    out = numeric_metric_per_day(
        pd.DataFrame({"source_date": [date(2026, 5, 1)]}), "flagged_pct",
    )
    assert out.empty


def test_window_errors_group_by_source_date_not_internal_date():
    # Rule 9 intent: trend must key off the artifact day (source_date),
    # NOT the WindowErrors internal `date` column. They differ here on purpose.
    df = pd.DataFrame({
        "source_date": [date(2026, 5, 1), date(2026, 5, 1)],
        "date": [pd.Timestamp("2026-04-30"), pd.Timestamp("2026-04-30")],
        "error_ratio": [0.5, 1.5],
    })
    out = numeric_metric_per_day(df, "error_ratio", aggs=("max",))
    assert list(out["source_date"]) == [date(2026, 5, 1)]
    assert out["error_ratio_max"].iloc[0] == 1.5
