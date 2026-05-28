"""Test pv_pipeline.baseline: BaselineAccumulator hybrid filter logic."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime

import pandas as pd
import pytest

from pv_pipeline.baseline import BaselineAccumulator, MaintenancePeriod


# ---------- MaintenancePeriod ----------


def test_maintenance_period_specific_inverter():
    p = MaintenancePeriod.from_dict({
        "start": "2026-05-14 10:00",
        "end": "2026-05-14 12:00",
        "affected": ["WB02-INV05"],
        "reason": "test",
    })
    assert not p.affect_all
    assert p.affected == ["WB02-INV05"]
    assert p.affect_wb_prefix == []
    assert p.matches(pd.Timestamp("2026-05-14 11:00"), "WB02-INV05")
    assert not p.matches(pd.Timestamp("2026-05-14 11:00"), "WB02-INV06")
    assert not p.matches(pd.Timestamp("2026-05-14 09:00"), "WB02-INV05")  # before window


def test_maintenance_period_wb_prefix_wildcard():
    p = MaintenancePeriod.from_dict({
        "start": "2026-05-14 10:00",
        "end": "2026-05-14 12:00",
        "affected": ["WB02"],
        "reason": "WB02 outage",
    })
    assert p.affect_wb_prefix == ["WB02"]
    assert p.matches(pd.Timestamp("2026-05-14 11:00"), "WB02-INV05")
    assert p.matches(pd.Timestamp("2026-05-14 11:00"), "WB02-INV99")
    assert not p.matches(pd.Timestamp("2026-05-14 11:00"), "WB05-INV01")


def test_maintenance_period_all():
    p = MaintenancePeriod.from_dict({
        "start": "2026-05-14",
        "end": "2026-05-14",
        "affected": "all",
        "reason": "full plant outage",
    })
    assert p.affect_all
    # Date-only end harus jadi 23:59:59
    assert p.end.hour == 23
    assert p.matches(pd.Timestamp("2026-05-14 15:00"), "WB02-INV05")
    assert p.matches(pd.Timestamp("2026-05-14 23:59:59"), "WB10-INV28")


# ---------- BaselineAccumulator filter logic ----------


@pytest.fixture
def df_simple():
    """Synthetic 3 inverters × 145 timestamps di 2026-05-14 daylight."""
    rows = []
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 18:00", freq="5min")[:145]
    for inv in ["WB05-INV01", "WB05-INV02", "WB02-INV05"]:
        for ts in t:
            rows.append({"Inverter_ID": inv, "Start Time": ts, "value": 1.0})
    return pd.DataFrame(rows)


class _MockFinding:
    def __init__(self, inv, sev, pv_string=None):
        self.inverter_id = inv
        self.pv_string = pv_string
        # Use mock severity-like object
        self.severity = type("X", (), {"value": sev})()


def test_filter_no_maintenance_no_findings(df_simple):
    """Tanpa maintenance + tanpa findings -> semua rows kept."""
    acc = BaselineAccumulator(base_dir="dummy", min_rows_per_inverter=30)
    filtered, summary = acc.filter_combined_df(df_simple, findings=None)
    assert summary.rows_total == 435
    assert summary.rows_kept == 435
    assert summary.rows_skipped_maintenance == 0
    assert summary.rows_skipped_findings == 0


def test_filter_maintenance_wb_prefix(df_simple):
    """WB02 maintenance 10:00-12:00 -> WB02-INV05 di window di-skip."""
    periods = [MaintenancePeriod.from_dict({
        "start": "2026-05-14 10:00",
        "end": "2026-05-14 12:00",
        "affected": ["WB02"],
        "reason": "test",
    })]
    acc = BaselineAccumulator(
        base_dir="dummy",
        min_rows_per_inverter=30,
        maintenance_periods=periods,
    )
    filtered, summary = acc.filter_combined_df(df_simple, findings=None)
    # 10:00-12:00 inclusive = 25 timestamps (10:00, 10:05, ..., 12:00)
    assert summary.rows_skipped_maintenance == 25
    assert summary.rows_kept == 435 - 25
    # Other inverters fully kept
    assert (filtered["Inverter_ID"] == "WB05-INV01").sum() == 145


def test_filter_findings_auto_skip_inverter_day(df_simple):
    """Finding HIGH untuk WB05-INV02 -> seluruh data inverter_day di-skip."""
    findings = [_MockFinding("WB05-INV02", "HIGH")]
    acc = BaselineAccumulator(base_dir="dummy", min_rows_per_inverter=30)
    filtered, summary = acc.filter_combined_df(df_simple, findings=findings)
    assert summary.rows_skipped_findings == 145
    assert "WB05-INV02" in summary.inverters_skipped_findings
    assert (filtered["Inverter_ID"] == "WB05-INV02").sum() == 0


def test_filter_medium_severity_NOT_auto_skipped(df_simple):
    """Severity MEDIUM (di bawah CRITICAL/HIGH default) tidak trigger auto-skip."""
    findings = [_MockFinding("WB05-INV02", "MEDIUM")]
    acc = BaselineAccumulator(base_dir="dummy", min_rows_per_inverter=30)
    filtered, summary = acc.filter_combined_df(df_simple, findings=findings)
    assert summary.rows_skipped_findings == 0
    assert (filtered["Inverter_ID"] == "WB05-INV02").sum() == 145


def test_save_daily_creates_parquet_and_csv(df_simple):
    """save_daily creates both parquet + csv di {base_dir}/{YYYY-MM}/{YYYY-MM-DD}.{ext}."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        acc = BaselineAccumulator(
            base_dir=os.path.join(td, "baseline"),
            output_formats=("parquet", "csv"),
            min_rows_per_inverter=30,
            overwrite=True,
        )
        result = acc.run(df_simple, "2026-05-14", findings=None)
        assert result["paths"]["parquet"] is not None
        assert result["paths"]["csv"] is not None
        assert os.path.exists(result["paths"]["parquet"])
        assert os.path.exists(result["paths"]["csv"])
        # Manifest dibuat
        assert os.path.exists(acc.manifest_path())


def test_min_rows_per_inverter_filter(df_simple):
    """Inverter dengan rows < min_rows_per_inverter setelah filter di-drop."""
    # Set min_rows tinggi (> total rows tersedia)
    acc = BaselineAccumulator(base_dir="dummy", min_rows_per_inverter=1000)
    filtered, summary = acc.filter_combined_df(df_simple, findings=None)
    assert summary.rows_kept == 0
    assert len(summary.inverters_skipped_min_rows) == 3


# ---------- 2026-05-23 per-PV scope (granular skip) ----------


@pytest.fixture
def df_pv():
    """Synthetic 2 inverters × 145 timestamps dengan PV1+PV2 V/I/Power cols."""
    rows = []
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 18:00", freq="5min")[:145]
    for inv in ["WB05-INV01", "WB05-INV02"]:
        for ts in t:
            rows.append({
                "Inverter_ID": inv,
                "Start Time": ts,
                "PV1 input voltage(V)": 1200.0,
                "PV1 input current(A)": 10.0,
                "PV1 Power(kW)": 12.0,
                "PV2 input voltage(V)": 1210.0,
                "PV2 input current(A)": 10.5,
                "PV2 Power(kW)": 12.7,
                "value": 1.0,
            })
    return pd.DataFrame(rows)


def test_default_skip_scope_is_pv_string():
    """Sejak 2026-05-23, default scope = pv_string (granular per-PV)."""
    from pv_pipeline.baseline import DEFAULT_SKIP_SCOPE
    assert DEFAULT_SKIP_SCOPE == "pv_string"


def test_invalid_skip_scope_warns_and_defaults():
    """Unknown scope -> warn + fallback to DEFAULT_SKIP_SCOPE."""
    import warnings as _w
    from pv_pipeline.baseline import DEFAULT_SKIP_SCOPE
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        acc = BaselineAccumulator(base_dir="dummy", skip_scope="bogus")
        assert acc.skip_scope == DEFAULT_SKIP_SCOPE
    assert any("skip_scope" in str(w.message).lower() for w in caught)


def test_pv_string_scope_nan_only_named_pv(df_pv):
    """Finding HIGH untuk WB05-INV01:PV1 -> NaN PV1 cols only; PV2 intact."""
    findings = [_MockFinding("WB05-INV01", "HIGH", pv_string="PV1")]
    acc = BaselineAccumulator(base_dir="dummy", min_rows_per_inverter=30)
    assert acc.skip_scope == "pv_string"  # new default
    filtered, summary = acc.filter_combined_df(df_pv, findings=findings)

    # No rows dropped (per-PV scope NaNs, not drops).
    assert summary.rows_skipped_findings == 0
    assert summary.inverters_skipped_findings == []
    # Per-PV NaN count = 145 (all WB05-INV01 rows touched)
    assert summary.rows_pv_nanned == 145
    assert summary.pv_strings_skipped_findings == ["WB05-INV01:PV1"]

    # WB05-INV01 retained (all 145 rows).
    inv01 = filtered[filtered["Inverter_ID"] == "WB05-INV01"]
    assert len(inv01) == 145
    # PV1 cols NaN'd
    assert inv01["PV1 input voltage(V)"].isna().all()
    assert inv01["PV1 input current(A)"].isna().all()
    assert inv01["PV1 Power(kW)"].isna().all()
    # PV2 cols intact
    assert inv01["PV2 input voltage(V)"].notna().all()
    assert (inv01["PV2 input voltage(V)"] == 1210.0).all()
    # WB05-INV02 fully intact
    inv02 = filtered[filtered["Inverter_ID"] == "WB05-INV02"]
    assert len(inv02) == 145
    assert inv02["PV1 input voltage(V)"].notna().all()


def test_pv_string_scope_inverter_level_finding_drops_whole_inverter(df_pv):
    """Finding HIGH dengan pv_string=None -> still drops whole inverter under pv_string scope."""
    findings = [_MockFinding("WB05-INV01", "HIGH", pv_string=None)]
    acc = BaselineAccumulator(base_dir="dummy", min_rows_per_inverter=30)
    filtered, summary = acc.filter_combined_df(df_pv, findings=findings)
    # Inverter-level finding -> drop whole inverter (no PV target).
    assert summary.rows_skipped_findings == 145
    assert summary.inverters_skipped_findings == ["WB05-INV01"]
    assert summary.rows_pv_nanned == 0
    assert (filtered["Inverter_ID"] == "WB05-INV01").sum() == 0


def test_pv_string_scope_mixed_findings(df_pv):
    """Mixed findings: per-PV + inverter-level under pv_string scope."""
    findings = [
        _MockFinding("WB05-INV01", "HIGH", pv_string="PV2"),       # per-PV
        _MockFinding("WB05-INV02", "CRITICAL", pv_string=None),    # inverter
    ]
    acc = BaselineAccumulator(base_dir="dummy", min_rows_per_inverter=30)
    filtered, summary = acc.filter_combined_df(df_pv, findings=findings)
    # INV01: PV2 NaN only, all rows kept.
    inv01 = filtered[filtered["Inverter_ID"] == "WB05-INV01"]
    assert len(inv01) == 145
    assert inv01["PV2 input voltage(V)"].isna().all()
    assert inv01["PV1 input voltage(V)"].notna().all()
    # INV02: fully dropped.
    assert (filtered["Inverter_ID"] == "WB05-INV02").sum() == 0
    assert "WB05-INV02" in summary.inverters_skipped_findings
    assert "WB05-INV01:PV2" in summary.pv_strings_skipped_findings


def test_legacy_inverter_day_scope_explicit(df_pv):
    """Explicit skip_scope='inverter_day' restores legacy behavior."""
    findings = [_MockFinding("WB05-INV01", "HIGH", pv_string="PV1")]
    acc = BaselineAccumulator(
        base_dir="dummy", min_rows_per_inverter=30, skip_scope="inverter_day",
    )
    filtered, summary = acc.filter_combined_df(df_pv, findings=findings)
    # Legacy: drop whole inverter even though finding is per-PV.
    assert summary.rows_skipped_findings == 145
    assert summary.inverters_skipped_findings == ["WB05-INV01"]
    assert summary.rows_pv_nanned == 0  # no NaN under legacy scope
    assert (filtered["Inverter_ID"] == "WB05-INV01").sum() == 0


def test_pv_string_scope_medium_severity_not_skipped(df_pv):
    """MEDIUM severity tetap tidak trigger NaN-out."""
    findings = [_MockFinding("WB05-INV01", "MEDIUM", pv_string="PV1")]
    acc = BaselineAccumulator(base_dir="dummy", min_rows_per_inverter=30)
    filtered, summary = acc.filter_combined_df(df_pv, findings=findings)
    assert summary.rows_pv_nanned == 0
    assert summary.pv_strings_skipped_findings == []
    # PV1 untouched.
    inv01 = filtered[filtered["Inverter_ID"] == "WB05-INV01"]
    assert inv01["PV1 input voltage(V)"].notna().all()


def test_pv_string_scope_handles_title_case_pv15():
    """NaN-out handles Title Case Huawei PV15-PV28 variants."""
    rows = []
    t = pd.date_range("2026-05-14 09:00", "2026-05-14 15:00", freq="5min")
    for ts in t:
        rows.append({
            "Inverter_ID": "WB05-INV01",
            "Start Time": ts,
            "PV15 Input Voltage(V)": 1200.0,    # Title Case (Huawei PV15-28 schema)
            "PV15 Input Current(A)": 10.0,
            "PV16 Input Voltage(V)": 1210.0,
            "PV16 Input Current(A)": 10.5,
        })
    df = pd.DataFrame(rows)
    findings = [_MockFinding("WB05-INV01", "HIGH", pv_string="PV15")]
    acc = BaselineAccumulator(base_dir="dummy", min_rows_per_inverter=10)
    filtered, summary = acc.filter_combined_df(df, findings=findings)
    # PV15 Title Case cols NaN'd
    assert filtered["PV15 Input Voltage(V)"].isna().all()
    assert filtered["PV15 Input Current(A)"].isna().all()
    # PV16 intact
    assert filtered["PV16 Input Voltage(V)"].notna().all()


def test_extract_pv_index_helper():
    """_extract_pv_index parses various pv_string formats."""
    from pv_pipeline.baseline import _extract_pv_index
    assert _extract_pv_index("PV1") == 1
    assert _extract_pv_index("PV15") == 15
    assert _extract_pv_index("pv28") == 28
    assert _extract_pv_index("PV 7") == 7    # whitespace tolerated
    assert _extract_pv_index("") is None
    assert _extract_pv_index("garbage") is None
    assert _extract_pv_index(None) is None


def test_manifest_includes_new_columns(df_pv):
    """Manifest CSV includes rows_pv_nanned + pv_strings_skipped_findings."""
    findings = [_MockFinding("WB05-INV01", "HIGH", pv_string="PV1")]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        acc = BaselineAccumulator(
            base_dir=os.path.join(td, "baseline"),
            output_formats=("csv",),
            min_rows_per_inverter=30,
            overwrite=True,
        )
        acc.run(df_pv, "2026-05-14", findings=findings)
        manifest = pd.read_csv(acc.manifest_path())
        assert "rows_pv_nanned" in manifest.columns
        assert "pv_strings_skipped_findings" in manifest.columns
        assert int(manifest["rows_pv_nanned"].iloc[0]) == 145
        assert "WB05-INV01:PV1" in str(manifest["pv_strings_skipped_findings"].iloc[0])


def test_manifest_includes_dashboard_artifact_placeholders(df_pv):
    """Baseline manifest exposes dashboard artifact names and empty Drive link slots."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        acc = BaselineAccumulator(
            base_dir=os.path.join(td, "baseline"),
            output_formats=("csv",),
            min_rows_per_inverter=30,
            overwrite=True,
        )
        acc.run(df_pv, "2026-05-14", findings=None)

        manifest = pd.read_csv(acc.manifest_path())
        row = manifest.iloc[0]
        expected_columns = [
            "baseline_csv_name",
            "baseline_csv_file_id",
            "baseline_csv_url",
            "findings_xlsx_name",
            "findings_xlsx_file_id",
            "findings_xlsx_url",
            "findings_jsonl_name",
            "findings_jsonl_file_id",
            "findings_jsonl_url",
        ]
        for column in expected_columns:
            assert column in manifest.columns

        assert row["baseline_csv_name"] == "2026-05-14.csv"
        assert row["findings_xlsx_name"] == "m2_findings_20260514.xlsx"
        assert row["findings_jsonl_name"] == "m2_findings_20260514.jsonl"
        for column in [
            "baseline_csv_file_id",
            "baseline_csv_url",
            "findings_xlsx_file_id",
            "findings_xlsx_url",
            "findings_jsonl_file_id",
            "findings_jsonl_url",
        ]:
            assert pd.isna(row[column]) or row[column] == ""


def test_manifest_schema_upgrade_keeps_existing_manifest_readable(df_pv):
    """Appending after a schema change rewrites old rows so pandas can read CSV."""
    old_row = {
        "date": "2026-05-14",
        "rows_total": 10,
        "rows_kept": 10,
        "rows_skipped_maintenance": 0,
        "rows_skipped_findings": 0,
        "rows_skipped_min_rows": 0,
        "rows_pv_nanned": 0,
        "inverters_total": 1,
        "inverters_kept": 1,
        "inverters_skipped_findings": "",
        "inverters_skipped_min_rows": "",
        "pv_strings_skipped_findings": "",
        "maintenance_matches": 0,
        "file_parquet": "",
        "file_csv": "baseline/2026-05/2026-05-14.csv",
        "saved_at": "2026-05-14T12:00:00",
    }

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        base_dir = os.path.join(td, "baseline")
        os.makedirs(base_dir, exist_ok=True)
        manifest_path = os.path.join(base_dir, "manifest.csv")
        pd.DataFrame([old_row]).to_csv(manifest_path, index=False)

        acc = BaselineAccumulator(
            base_dir=base_dir,
            output_formats=("csv",),
            min_rows_per_inverter=30,
            overwrite=True,
        )
        acc.run(df_pv, "2026-05-15", findings=None)

        manifest = pd.read_csv(manifest_path)
        assert len(manifest) == 2
        assert "baseline_csv_name" in manifest.columns
        assert pd.isna(manifest.loc[0, "baseline_csv_name"]) or manifest.loc[0, "baseline_csv_name"] == ""
        assert manifest.loc[1, "baseline_csv_name"] == "2026-05-15.csv"
