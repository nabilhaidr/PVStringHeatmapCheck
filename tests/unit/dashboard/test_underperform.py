from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from pv_pipeline.dashboard.data.underperform import (
    analyze_inverter_strings,
    build_string_timeseries,
    summarize_pv_string_findings,
)


def test_summarize_pv_string_findings_keeps_only_targeted_strings():
    findings = pd.DataFrame({
        "source_date": [date(2026, 5, 14), date(2026, 5, 14), date(2026, 5, 14), date(2026, 5, 14)],
        "timestamp": [
            "2026-05-14T08:00:00",
            "2026-05-14T09:00:00",
            "2026-05-14T10:00:00",
            "2026-05-14T11:00:00",
        ],
        "wb_id": ["WB05", "WB05", "WB05", "WB05"],
        "inverter_id": ["WB05-INV01", "WB05-INV01", "WB05-INV02", "WB05-INV03"],
        "pv_string": ["PV3", "PV3", None, ""],
        "sub_module": ["M2b_peer_zscore", "M2b_peer_zscore", "M2e_inverter", "M2b_open_circuit"],
        "severity": ["HIGH", "CRITICAL", "CRITICAL", "HIGH"],
        "fault_type": ["high_R", "open_circuit", "inverter_down", "open_circuit"],
        "confidence": [80.0, 95.0, 100.0, 90.0],
    })

    summary = summarize_pv_string_findings(findings)

    assert len(summary) == 1
    row = summary.iloc[0]
    assert row["inverter_id"] == "WB05-INV01"
    assert row["pv_string"] == "PV3"
    assert row["finding_count"] == 2
    assert row["worst_severity"] == "CRITICAL"
    assert row["latest_timestamp"] == pd.Timestamp("2026-05-14T09:00:00")
    assert row["fault_types"] == "high_R; open_circuit"
    assert row["max_confidence"] == 95.0


def test_summarize_pv_string_findings_sorts_worst_severity_first():
    findings = pd.DataFrame({
        "source_date": [date(2026, 5, 14), date(2026, 5, 14)],
        "timestamp": ["2026-05-14T08:00:00", "2026-05-14T09:00:00"],
        "inverter_id": ["WB05-INV01", "WB05-INV02"],
        "pv_string": ["PV1", "PV2"],
        "sub_module": ["M2b_peer_zscore", "M2b_peer_zscore"],
        "severity": ["MEDIUM", "HIGH"],
    })

    summary = summarize_pv_string_findings(findings)

    assert summary["pv_string"].tolist() == ["PV2", "PV1"]
    assert summary["worst_severity"].tolist() == ["HIGH", "MEDIUM"]


def _baseline_df(include_current: bool = True) -> pd.DataFrame:
    rows = []
    for ts, pv1, pv2, pv3, current in [
        ("2026-05-14 08:00", 1.0, 2.0, 3.0, 4.0),
        ("2026-05-14 08:05", 2.0, 2.0, 2.0, 5.0),
        ("2026-05-14 08:10", 3.0, 2.0, 1.0, 6.0),
    ]:
        row = {
            "Inverter_ID": "WB05-INV01",
            "Start Time": ts,
            "PV1 Power(kW)": pv1,
            "PV2 Power(kW)": pv2,
            "PV3 Power(kW)": pv3,
        }
        if include_current:
            row["PV1 input current(A)"] = current
        rows.append(row)
    return pd.DataFrame(rows)


def test_build_string_timeseries_matches_cell3_per_timestamp_normalization():
    ts, message = build_string_timeseries(_baseline_df(), "WB05-INV01", "PV1")

    assert message == ""
    assert ts["pv_power_kw"].tolist() == [1.0, 2.0, 3.0]
    assert ts["sibling_median_power_kw"].tolist() == [2.5, 2.0, 1.5]
    assert ts["power_ratio_to_sibling"].tolist() == [0.4, 1.0, 2.0]
    assert ts["pv_current_a"].tolist() == [4.0, 5.0, 6.0]
    assert ts["cell3_norm"].iloc[0] == 0.0
    assert pd.isna(ts["cell3_norm"].iloc[1])
    assert ts["cell3_norm"].iloc[2] == 1.0


def test_build_string_timeseries_allows_power_only_baseline_csv():
    ts, message = build_string_timeseries(
        _baseline_df(include_current=False),
        "WB05-INV01",
        "PV1",
    )

    assert message == ""
    assert "pv_current_a" not in ts.columns
    assert ts["pv_power_kw"].tolist() == [1.0, 2.0, 3.0]


def test_build_string_timeseries_reports_all_nan_selected_pv():
    df = _baseline_df()
    df["PV2 Power(kW)"] = float("nan")

    ts, message = build_string_timeseries(df, "WB05-INV01", "PV2")

    assert not ts.empty
    assert ts["pv_power_kw"].isna().all()
    assert "PV2 Power(kW)" in message
    assert "no valid power samples" in message


def test_analyze_inverter_strings_returns_display_only_metrics():
    analysis = analyze_inverter_strings(_baseline_df(), "WB05-INV01")

    pv1 = analysis.loc[analysis["pv_string"] == "PV1"].iloc[0]
    assert pv1["n_samples"] == 3
    assert pv1["n_current_samples"] == 3
    assert pv1["median_power_kw"] == 2.0
    assert pv1["median_current_a"] == 5.0
    assert pv1["median_sibling_power_kw"] == 2.0
    assert pv1["median_power_ratio_to_sibling"] == 1.0
    assert pv1["p10_power_ratio_to_sibling"] == pytest.approx(0.52)
    assert pv1["low_norm_pct"] == 50.0
