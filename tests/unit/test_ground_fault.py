"""Test pv_pipeline.ground_fault: M2bGroundFault triple-signal detector.

Sunset/shutdown false-positive fix (2026-05-16): same 3 mask conditions as
M2bOpenCircuit (poa_floor, hour_cutoff_end, respect_inverter_shutdown).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.ground_fault import M2bGroundFault


# ---------- Fixtures ----------


@pytest.fixture
def df_ground_fault_normal():
    """1 inverter, V_to_ground normal (~0V), no fault. PV3 dengan small R bump
    untuk make sure detector tidak emit di kondisi normal."""
    rng = np.random.default_rng(13)
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 18:00", freq="5min")
    rows = []
    for ts in t:
        hour = ts.hour + ts.minute / 60.0
        sun = np.sin(np.pi * (hour - 6.0) / 12.0) ** 2 if 6.0 <= hour <= 18.0 else 0.0
        I_base = 13.0 * sun
        V_base = 1200.0 + 200.0 * np.exp(-3 * sun)
        row = {"Inverter_ID": "WB05-INV05", "Start Time": ts,
               "Voltage between PV– and the ground(V)": rng.normal(0, 5),
               "Inverter shutdown time": pd.NaT}
        for pv_n in range(1, 11):
            row[f"PV{pv_n} input voltage(V)"] = V_base + rng.normal(0, 5)
            row[f"PV{pv_n} input current(A)"] = I_base + rng.normal(0, 0.1)
        rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def df_ground_fault_sunset_noise():
    """Sunset scenario: V_to_ground = -80V hanya di 18:00-19:00 (sensor noise pada
    DC bus yang sudah shutdown). Sebelum sunset normal (~0V).

    Tanpa sunset fix: absolute trigger fire (|V|>50). Dengan fix: skip karena cutoff.
    """
    rng = np.random.default_rng(7)
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 20:00", freq="5min")
    inv_shutdown = pd.Timestamp("2026-05-14 18:00:00")
    rows = []
    for ts in t:
        hour = ts.hour + ts.minute / 60.0
        sun = np.sin(np.pi * (hour - 6.0) / 12.0) ** 2 if 6.0 <= hour <= 18.0 else 0.0
        I_base = 13.0 * sun
        V_base = 1200.0 + 200.0 * np.exp(-3 * sun)

        if hour < 18.0:
            v_gnd = rng.normal(0, 5)
        else:
            v_gnd = -80.0 + rng.normal(0, 3)  # absolute trigger

        row = {
            "Inverter_ID": "WB05-INV02",
            "Start Time": ts,
            "Voltage between PV– and the ground(V)": v_gnd,
            "Inverter shutdown time": inv_shutdown,
        }
        for pv_n in range(1, 11):
            row[f"PV{pv_n} input voltage(V)"] = V_base + rng.normal(0, 5)
            if hour < 18.0:
                row[f"PV{pv_n} input current(A)"] = I_base + rng.normal(0, 0.1)
            else:
                row[f"PV{pv_n} input current(A)"] = 0.05 + rng.normal(0, 0.02)
        rows.append(row)
    return pd.DataFrame(rows)


class _MockPOAWithSunsetLag:
    def get_poa(self, timestamps, wb_id, source="auto"):
        ts_idx = pd.DatetimeIndex(timestamps)
        out = []
        for ts in ts_idx:
            hour = ts.hour + ts.minute / 60.0
            if 6.0 <= hour <= 18.0:
                out.append(1000.0 * np.sin(np.pi * (hour - 6.0) / 12.0) ** 2)
            elif 18.0 < hour <= 18.5:
                out.append(250.0)  # sensor lag, lolos poa_threshold=200
            else:
                out.append(0.0)
        return pd.Series(out, index=ts_idx)


@pytest.fixture
def cfg_with_sunset_fix():
    return {
        "m2b_ground_fault": {
            "poa_threshold_wm2": 200.0,
            "poa_floor_wm2": 50.0,
            "hour_cutoff_end": 18.0,
            "respect_inverter_shutdown": True,
            "filter_mode": "hour_cutoff",  # Force legacy mode untuk sunset-fix tests
            "v_to_ground_abs_threshold_v": 50.0,
            "adaptive_z_threshold": 3.0,
            "voc_ratio_threshold": 0.85,
            "i_high_z_threshold": 2.0,
            "pv_max": 10,
            "min_daylight_samples": 5,
        },
        "poa": {"emit_all_sources": False, "default_source": "auto",
                "site_geometry_path": "config/site_geometry.yaml"},
    }


# ---------- Tests ----------


def test_no_false_positive_normal_inverter(df_ground_fault_normal, mock_panel,
                                            mock_cell_temp, cfg_with_sunset_fix):
    """Normal V_to_ground (~0V) tidak emit ground_fault finding."""
    sm = M2bGroundFault(poa=_MockPOAWithSunsetLag(), panel=mock_panel,
                        cell_temp=mock_cell_temp)
    findings = sm.run(df_ground_fault_normal, cfg_with_sunset_fix)
    assert len(findings) == 0, f"Normal inverter false positive: {findings}"


def test_sunset_v_to_ground_noise_filtered(df_ground_fault_sunset_noise, mock_panel,
                                            mock_cell_temp, cfg_with_sunset_fix):
    """V_to_ground = -80V HANYA di sunset (>=18:00) -> sunset fix harus filter.

    Tanpa fix: absolute threshold (|V|>50) fire. Dengan fix: skip karena
    hour_cutoff_end=18.0 + respect_inverter_shutdown=True.
    """
    sm = M2bGroundFault(poa=_MockPOAWithSunsetLag(), panel=mock_panel,
                        cell_temp=mock_cell_temp)
    findings = sm.run(df_ground_fault_sunset_noise, cfg_with_sunset_fix)
    assert len(findings) == 0, (
        f"Sunset noise tidak ke-filter: {len(findings)} findings emitted "
        f"({[f.evidence.get('triggered_by') for f in findings]})"
    )


def test_disabled_sunset_filters_regress(df_ground_fault_sunset_noise, mock_panel,
                                          mock_cell_temp):
    """Sanity check: tanpa sunset fix, V_to_ground -80V di sunset trigger absolute."""
    cfg_no_fix = {
        "m2b_ground_fault": {
            "poa_threshold_wm2": 200.0,
            "poa_floor_wm2": 0.0,                # disabled
            "hour_cutoff_end": 24.0,             # disabled
            "respect_inverter_shutdown": False,  # disabled
            "v_to_ground_abs_threshold_v": 50.0,
            "adaptive_z_threshold": 3.0,
            "voc_ratio_threshold": 0.85,
            "i_high_z_threshold": 2.0,
            "pv_max": 10,
            "min_daylight_samples": 5,
        },
        "poa": {"emit_all_sources": False, "default_source": "auto",
                "site_geometry_path": "config/site_geometry.yaml"},
    }
    sm = M2bGroundFault(poa=_MockPOAWithSunsetLag(), panel=mock_panel,
                        cell_temp=mock_cell_temp)
    findings = sm.run(df_ground_fault_sunset_noise, cfg_no_fix)
    assert len(findings) > 0, "Tanpa sunset fix, sunset noise harus regress false positive"


def test_evidence_includes_new_filter_fields(df_ground_fault_sunset_noise, mock_panel,
                                              mock_cell_temp):
    """Saat finding emit (kondisi disabled), evidence harus include audit fields."""
    cfg_no_fix = {
        "m2b_ground_fault": {
            "poa_threshold_wm2": 200.0,
            "poa_floor_wm2": 0.0,
            "hour_cutoff_end": 24.0,
            "respect_inverter_shutdown": False,
            "v_to_ground_abs_threshold_v": 50.0,
            "adaptive_z_threshold": 3.0,
            "voc_ratio_threshold": 0.85,
            "i_high_z_threshold": 2.0,
            "pv_max": 10,
            "min_daylight_samples": 5,
        },
        "poa": {"emit_all_sources": False, "default_source": "auto",
                "site_geometry_path": "config/site_geometry.yaml"},
    }
    sm = M2bGroundFault(poa=_MockPOAWithSunsetLag(), panel=mock_panel,
                        cell_temp=mock_cell_temp)
    findings = sm.run(df_ground_fault_sunset_noise, cfg_no_fix)
    assert findings, "Need finding untuk verify evidence fields"
    ev = findings[0].evidence
    assert ev["poa_floor_wm2"] == 0.0
    assert ev["hour_cutoff_end"] == 24.0
    assert ev["respect_inverter_shutdown"] is False


# ---------- Fase 2 Wave 2: solar_elevation filter ----------


class _MockPOAWithSolarElev:
    """POA + sun-curve elevation untuk solar_elevation mode test."""

    def get_poa(self, timestamps, wb_id, source="auto"):
        ts_idx = pd.DatetimeIndex(timestamps)
        out = []
        for ts in ts_idx:
            hour = ts.hour + ts.minute / 60.0
            if 6.0 <= hour <= 18.0:
                out.append(1000.0 * np.sin(np.pi * (hour - 6.0) / 12.0) ** 2)
            elif 18.0 < hour <= 18.5:
                out.append(250.0)
            else:
                out.append(0.0)
        return pd.Series(out, index=ts_idx)

    def get_solar_elevation(self, timestamps):
        ts_idx = pd.DatetimeIndex(timestamps)
        hrs = (ts_idx.hour - 6) + (ts_idx.minute / 60.0)
        elev = np.where(
            (hrs >= 0) & (hrs <= 12),
            85.0 * np.sin(np.pi * hrs / 12),
            -45.0,
        )
        return pd.Series(elev, index=ts_idx, name="solar_elevation_deg")


def test_solar_elevation_filter_evidence(df_ground_fault_sunset_noise, mock_panel,
                                          mock_cell_temp):
    """filter_mode='solar_elevation' -> evidence include filter_mode_effective='solar_elevation'."""
    cfg = {
        "m2b_ground_fault": {
            "poa_threshold_wm2": 200.0,
            "poa_floor_wm2": 0.0,
            "respect_inverter_shutdown": False,
            "filter_mode": "solar_elevation",
            "solar_elevation_min_deg": 5.0,
            "v_to_ground_abs_threshold_v": 50.0,
            "adaptive_z_threshold": 3.0,
            "voc_ratio_threshold": 0.85,
            "i_high_z_threshold": 2.0,
            "pv_max": 10,
            "min_daylight_samples": 5,
        },
        "poa": {"emit_all_sources": False, "default_source": "auto",
                "site_geometry_path": "config/site_geometry.yaml"},
    }
    sm = M2bGroundFault(poa=_MockPOAWithSolarElev(), panel=mock_panel,
                        cell_temp=mock_cell_temp)
    findings = sm.run(df_ground_fault_sunset_noise, cfg)
    if findings:
        ev = findings[0].evidence
        assert ev["filter_mode"] == "solar_elevation"
        assert ev["filter_mode_effective"] == "solar_elevation"
        assert ev["solar_elevation_min_deg"] == 5.0


# ---------- Wave 8: StringStatus per-PV artifact ----------


def test_string_status_artifact_per_pv_emitted(
    df_ground_fault_sunset_noise, mock_panel, mock_cell_temp,
):
    """Wave 8: ground_fault fan-out per-PV row dengan status column.

    Pakai cfg_no_fix (sunset filter OFF) supaya beberapa inverter trigger,
    sehingga ada baseline NORMAL + flagged ground_fault rows.
    """
    cfg = {
        "m2b_ground_fault": {
            "poa_threshold_wm2": 200.0,
            "poa_floor_wm2": 0.0,
            "hour_cutoff_end": 24.0,
            "respect_inverter_shutdown": False,
            "filter_mode": "hour_cutoff",
            "v_to_ground_abs_threshold_v": 50.0,
            "adaptive_z_threshold": 3.0,
            "voc_ratio_threshold": 0.85,
            "i_high_z_threshold": 2.0,
            "pv_max": 10,
            "min_daylight_samples": 5,
        },
        "poa": {"emit_all_sources": False, "default_source": "auto",
                "site_geometry_path": "config/site_geometry.yaml"},
    }
    sm = M2bGroundFault(poa=_MockPOAWithSunsetLag(), panel=mock_panel,
                        cell_temp=mock_cell_temp)
    sm.run(df_ground_fault_sunset_noise, cfg)
    assert "StringStatus" in sm.artifacts
    df = sm.artifacts["StringStatus"]
    # Must contain per-PV rows.
    assert "pv_string" in df.columns
    assert "status" in df.columns
    assert set(df["status"].unique()).issubset({"NORMAL", "ground_fault", "EMPTY"})
    # is_worst_string only True untuk worst PV di inverter flagged.
    assert "is_worst_string" in df.columns


# ---------- Wave 11 hotfix #3: fan-out NORMAL placeholder ----------


def test_string_status_emitted_even_when_poa_fails(synthetic_combined_df_with_outlier):
    """Wave 11 hotfix #3: fan-out StringStatus dengan NORMAL placeholder kalau
    POA query gagal pada semua inverter. InverterEvents tetap conditional."""
    class _FailingPOA:
        def get_poa(self, timestamps, wb_id, source="auto"):
            raise RuntimeError("synthetic POA failure")

        def get_solar_elevation(self, timestamps):
            return pd.Series([-45.0] * len(timestamps), index=timestamps)

    class _MockPanel:
        def voc_string(self, wb_id, t_cell_c=25.0):
            return 1448.7  # 26 modules * 55.72 Voc_STC

    class _MockTcell:
        def get_tcell(self, timestamps, wb_id, source="auto"):
            return pd.Series([30.0] * len(timestamps), index=timestamps)

    cfg = {
        "m2b_ground_fault": {
            "pv_max": 10,
            "filter_mode": "solar_elevation",
            "solar_elevation_min_deg": 5.0,
        },
        "poa": {"emit_all_sources": False, "default_source": "auto"},
    }
    sm = M2bGroundFault(poa=_FailingPOA(), panel=_MockPanel(), cell_temp=_MockTcell())
    sm.run(synthetic_combined_df_with_outlier, cfg)
    assert "StringStatus" in sm.artifacts
    df = sm.artifacts["StringStatus"]
    assert not df.empty
    assert (df["status"] == "NORMAL").all()
    assert "note" in df.columns
    # InverterEvents stays absent (no triggers).
    assert "InverterEvents" not in sm.artifacts
