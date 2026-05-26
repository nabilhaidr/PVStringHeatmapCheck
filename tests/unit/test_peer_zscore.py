"""Test pv_pipeline.peer_zscore: M2bPeerZScore POA-gated detector.

Pakai fixtures dari conftest.py: synthetic_combined_df_with_outlier (PV3 high_R signature)
+ mock_poa + mock_panel + mock_cell_temp.
"""
from __future__ import annotations

import pandas as pd

from pv_pipeline.peer_zscore import M2bPeerZScore


def test_high_R_outlier_flagged(
    synthetic_combined_df_with_outlier,
    mock_poa,
    mock_panel,
    mock_cell_temp,
    m2_config_minimal,
):
    """PV3 di WB05-INV01 R abnormal -> harus emit fault_type=high_R."""
    sm = M2bPeerZScore(poa=mock_poa, panel=mock_panel, cell_temp=mock_cell_temp)
    findings = sm.run(synthetic_combined_df_with_outlier, m2_config_minimal)
    pv3_findings = [
        f for f in findings
        if f.inverter_id == "WB05-INV01" and f.pv_string == "PV3"
    ]
    assert len(pv3_findings) > 0, "PV3 di WB05-INV01 harus terdeteksi high_R"
    f = pv3_findings[0]
    assert f.fault_type == "high_R"
    assert abs(f.value) > 2.5  # |z| > z_threshold
    assert f.confidence is not None and f.confidence > 0
    assert f.evidence is not None
    assert f.evidence["voc_ratio"] is None or f.evidence["voc_ratio"] > 0.95


def test_normal_strings_not_flagged(
    synthetic_combined_df,
    mock_poa,
    mock_panel,
    mock_cell_temp,
    m2_config_minimal,
):
    """Synthetic data tanpa outlier tidak emit finding (atau very few false positives)."""
    sm = M2bPeerZScore(poa=mock_poa, panel=mock_panel, cell_temp=mock_cell_temp)
    findings = sm.run(synthetic_combined_df, m2_config_minimal)
    # Allow up to 0-2 false positive per inverter karena noise random di synthetic data.
    inv_counts = {}
    for f in findings:
        inv_counts[f.inverter_id] = inv_counts.get(f.inverter_id, 0) + 1
    for inv, n in inv_counts.items():
        assert n <= 2, f"Inverter {inv} ada {n} false positive (lebih dari toleransi)"


def test_artifacts_stat_comparison_created(
    synthetic_combined_df_with_outlier,
    mock_poa,
    mock_panel,
    mock_cell_temp,
    m2_config_minimal,
):
    """SubModule.artifacts['StringStatus'] harus terisi setelah run (Wave 8 rename)."""
    sm = M2bPeerZScore(poa=mock_poa, panel=mock_panel, cell_temp=mock_cell_temp)
    sm.run(synthetic_combined_df_with_outlier, m2_config_minimal)
    assert "StringStatus" in sm.artifacts
    df = sm.artifacts["StringStatus"]
    assert not df.empty
    assert "z_median" in df.columns
    assert "voc_ratio" in df.columns
    assert "emitted_finding" in df.columns
    # Wave 8/11: status column (NORMAL, high_R, atau EMPTY per row).
    assert "status" in df.columns
    assert set(df["status"].unique()).issubset({"NORMAL", "high_R", "EMPTY"})
    # PV3 di WB05-INV01 punya high_R signature di synthetic_combined_df_with_outlier.
    pv3_rows = df[(df["inverter_id"] == "WB05-INV01") & (df["pv_string"] == "PV3")]
    if not pv3_rows.empty:
        assert (pv3_rows["status"] == "high_R").any()


# ---------- Sunset/shutdown filter (2026-05-16) ----------


def test_sunset_filter_evidence_fields_present(
    synthetic_combined_df_with_outlier,
    mock_poa,
    mock_panel,
    mock_cell_temp,
    m2_config_minimal,
):
    """Findings evidence harus include 4 sunset filter audit fields."""
    # Tambah default sunset config (yang tidak ada di m2_config_minimal)
    cfg = dict(m2_config_minimal)
    cfg["m2b"] = dict(cfg["m2b"])
    cfg["m2b"]["poa_floor_wm2"] = 50.0
    cfg["m2b"]["hour_cutoff_end"] = 18.0
    cfg["m2b"]["respect_inverter_shutdown"] = True

    sm = M2bPeerZScore(poa=mock_poa, panel=mock_panel, cell_temp=mock_cell_temp)
    findings = sm.run(synthetic_combined_df_with_outlier, cfg)
    assert findings, "PV3 high_R harus terdeteksi"
    ev = findings[0].evidence
    assert ev["poa_floor_wm2"] == 50.0
    assert ev["hour_cutoff_end"] == 18.0
    assert ev["respect_inverter_shutdown"] is True


def test_sunset_hour_cutoff_filters_evening(
    mock_panel,
    mock_cell_temp,
    m2_config_minimal,
):
    """Hour cutoff = 18.0: data setelah 18:00 di-skip."""
    import numpy as np
    import pandas as pd

    # Buat synthetic: 1 inverter dengan I=0 sepanjang 18:00-20:00 (sunset)
    # tapi POA pyranometer masih 250 (lag) -> tanpa cutoff bisa trigger high_R.
    rng = np.random.default_rng(99)
    t = pd.date_range("2026-05-14 17:00", "2026-05-14 20:00", freq="5min")
    rows = []
    for ts in t:
        hour = ts.hour + ts.minute / 60.0
        if hour < 18.0:
            I_base = 5.0
        else:
            I_base = 0.05  # All strings drop ~0 setelah sunset
        row = {"Inverter_ID": "WB05-INV01", "Start Time": ts,
               "Inverter shutdown time": pd.NaT}  # no shutdown time
        for pv_n in range(1, 11):
            row[f"PV{pv_n} input voltage(V)"] = 1200.0 + rng.normal(0, 2)
            if pv_n == 3:
                # PV3 R abnormal sepanjang waktu (V tinggi, I rendah)
                row[f"PV{pv_n} input current(A)"] = I_base * 0.4 + rng.normal(0, 0.02)
            else:
                row[f"PV{pv_n} input current(A)"] = I_base + rng.normal(0, 0.05)
        rows.append(row)
    df = pd.DataFrame(rows)

    class _MockPOAWithLag:
        def get_poa(self, timestamps, wb_id, source="auto"):
            ts_idx = pd.DatetimeIndex(timestamps)
            out = []
            for ts in ts_idx:
                hour = ts.hour + ts.minute / 60.0
                if hour < 18.0:
                    out.append(450.0)  # > poa_threshold 300
                else:
                    out.append(250.0)  # < 300 tapi > floor 50 (sunset lag)
            return pd.Series(out, index=ts_idx)

    cfg = dict(m2_config_minimal)
    cfg["m2b"] = dict(cfg["m2b"])
    cfg["m2b"]["poa_floor_wm2"] = 50.0
    cfg["m2b"]["hour_cutoff_end"] = 18.0  # ACTIVE
    cfg["m2b"]["respect_inverter_shutdown"] = True
    cfg["m2b"]["filter_mode"] = "hour_cutoff"  # Force legacy mode untuk test ini
    cfg["m2b"]["min_daylight_samples"] = 5

    sm = M2bPeerZScore(poa=_MockPOAWithLag(), panel=mock_panel, cell_temp=mock_cell_temp)
    findings = sm.run(df, cfg)
    # Cutoff aktif -> hanya samples 17:00-18:00 yang valid (12 samples).
    # PV3 R abnormal tetap detected karena data daytime valid.
    if findings:
        ev = findings[0].evidence
        # daylight_samples harus < 25 (kalau cutoff bekerja); kalau tanpa cutoff > 30.
        assert ev["daylight_samples"] < 25, (
            f"hour_cutoff_end TIDAK bekerja: daylight_samples={ev['daylight_samples']} "
            f"(expected <25 dengan cutoff aktif)"
        )


# ---------- Fase 2 Wave 2: solar_elevation filter ----------


def test_solar_elevation_filter_emits_evidence(
    synthetic_combined_df_with_outlier,
    mock_poa,
    mock_panel,
    mock_cell_temp,
    m2_config_minimal,
):
    """filter_mode='solar_elevation' -> evidence include filter_mode_effective + min_deg."""
    cfg = dict(m2_config_minimal)
    cfg["m2b"] = dict(cfg["m2b"])
    cfg["m2b"]["filter_mode"] = "solar_elevation"
    cfg["m2b"]["solar_elevation_min_deg"] = 5.0

    sm = M2bPeerZScore(poa=mock_poa, panel=mock_panel, cell_temp=mock_cell_temp)
    findings = sm.run(synthetic_combined_df_with_outlier, cfg)
    assert findings, "PV3 high_R harus terdeteksi via solar_elevation mode"
    ev = findings[0].evidence
    assert ev["filter_mode"] == "solar_elevation"
    assert ev["filter_mode_effective"] == "solar_elevation"
    assert ev["solar_elevation_min_deg"] == 5.0


# ---------- Wave 9: preprocessing.enabled flag ----------


def test_preprocessing_enabled_emits_audit_artifact(
    synthetic_combined_df_with_outlier,
    mock_poa,
    mock_panel,
    mock_cell_temp,
    m2_config_minimal,
):
    """preprocessing.enabled=True -> PreprocessingAudit artifact ber-isi."""
    cfg = dict(m2_config_minimal)
    cfg["preprocessing"] = {"enabled": True, "window": 15, "max_deviation": 3.0}

    sm = M2bPeerZScore(poa=mock_poa, panel=mock_panel, cell_temp=mock_cell_temp)
    sm.run(synthetic_combined_df_with_outlier, cfg)
    assert "PreprocessingAudit" in sm.artifacts
    audit = sm.artifacts["PreprocessingAudit"]
    assert not audit.empty
    assert "column" in audit.columns
    assert "n_outliers" in audit.columns
    assert "total_samples" in audit.columns
    assert "pct_outliers" in audit.columns


def test_preprocessing_disabled_default_no_audit(
    synthetic_combined_df_with_outlier,
    mock_poa,
    mock_panel,
    mock_cell_temp,
    m2_config_minimal,
):
    """Default behavior (no preprocessing config) -> no PreprocessingAudit artifact."""
    sm = M2bPeerZScore(poa=mock_poa, panel=mock_panel, cell_temp=mock_cell_temp)
    sm.run(synthetic_combined_df_with_outlier, m2_config_minimal)
    assert "PreprocessingAudit" not in sm.artifacts


# ---------- Wave 11 hotfix #3: fan-out NORMAL placeholder ----------


def test_string_status_emitted_even_when_poa_fails(
    synthetic_combined_df_with_outlier,
    mock_panel,
    mock_cell_temp,
    m2_config_minimal,
):
    """Wave 11 hotfix #3: kalau POA query fail untuk semua inverter, StringStatus
    tetap emit dengan fan-out NORMAL placeholder per (inverter, PV) pair.

    Mirrors user's main-repo scenario di mana alias-namespace POAProvider gagal
    pada setiap inverter -> tanpa fan-out, sheet hilang dari output xlsx.
    """
    class _FailingPOA:
        def get_poa(self, timestamps, wb_id, source="auto"):
            raise RuntimeError("synthetic POA failure")

        def get_solar_elevation(self, timestamps):
            return pd.Series([-45.0] * len(timestamps), index=timestamps)

    sm = M2bPeerZScore(poa=_FailingPOA(), panel=mock_panel, cell_temp=mock_cell_temp)
    sm.run(synthetic_combined_df_with_outlier, m2_config_minimal)
    # StringStatus tetap muncul (fan-out fallback).
    assert "StringStatus" in sm.artifacts
    df = sm.artifacts["StringStatus"]
    assert not df.empty
    # Semua rows NORMAL dengan note diagnostic.
    assert (df["status"] == "NORMAL").all()
    assert "note" in df.columns
    assert (df["note"] == "no_analysis_performed_check_data_quality_or_poa_gate").all()
    # Cover semua inverter di combined_df.
    inv_in_df = set(synthetic_combined_df_with_outlier["Inverter_ID"].dropna().unique())
    inv_in_artifact = set(df["inverter_id"].astype(str).unique())
    assert inv_in_df == inv_in_artifact
