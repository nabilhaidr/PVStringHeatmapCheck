"""Test pv_pipeline.open_circuit: M2bOpenCircuit POA-gated detector.

Sunset/shutdown false-positive fix (2026-05-16):
- POA floor 50 W/m^2 (skip kalau di bawah sunset/twilight)
- Hour cutoff 18:00 (skip setelah sunset di IKN)
- Respect Inverter shutdown time (skip rows setelah inverter shutdown)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.open_circuit import M2bOpenCircuit


@pytest.fixture
def df_sunset_scenario():
    """Synthetic 1 day 06:00-20:00 dengan 1 inverter, 10 PV strings.

    - PV7: real open-circuit (I~0 sepanjang daylight).
    - Setelah 18:00: ALL strings I=0 (inverter shutdown).
    - Pyranometer mock: POA 250 W/m^2 di 18:00-18:30 (sensor lag).
    """
    rng = np.random.default_rng(7)
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 20:00", freq="5min")
    inv_shutdown = pd.Timestamp("2026-05-14 18:25:00")
    rows = []
    for ts in t:
        hour = ts.hour + ts.minute / 60.0
        sun = np.sin(np.pi * (hour - 6.0) / 12.0) ** 2 if 6.0 <= hour <= 18.0 else 0.0
        I_base = 13.0 * sun
        row = {
            "Inverter_ID": "WB05-INV05",
            "Start Time": ts,
            "Inverter shutdown time": inv_shutdown,
        }
        for pv_n in range(1, 11):
            if pv_n == 7:
                row[f"PV{pv_n} input current(A)"] = 0.05 + rng.normal(0, 0.02)
            else:
                row[f"PV{pv_n} input current(A)"] = I_base + rng.normal(0, 0.1)
        rows.append(row)
    return pd.DataFrame(rows)


class _MockPOAWithSunsetLag:
    """POA dengan sensor lag: 250 W/m^2 di 18:00-18:30 (kondisi yang trigger
    false positive sebelum sunset fix)."""

    def get_poa(self, timestamps, wb_id, source="auto"):
        ts_idx = pd.DatetimeIndex(timestamps)
        poa_out = []
        for ts in ts_idx:
            hour = ts.hour + ts.minute / 60.0
            if 6.0 <= hour <= 18.0:
                sun = np.sin(np.pi * (hour - 6.0) / 12.0) ** 2
                poa_out.append(1000.0 * sun)
            elif 18.0 < hour <= 18.5:
                poa_out.append(250.0)  # SUNSET LAG (>200 threshold)
            else:
                poa_out.append(0.0)
        return pd.Series(poa_out, index=ts_idx)


@pytest.fixture
def cfg_sunset_fix():
    """Config dengan sunset fix aktif (defaults Sprint 4.A 2026-05-16, hour_cutoff mode)."""
    return {
        "m2b_open_circuit": {
            "poa_threshold_wm2": 200.0,
            "poa_floor_wm2": 50.0,
            "hour_cutoff_end": 18.0,
            "respect_inverter_shutdown": True,
            "filter_mode": "hour_cutoff",  # Force legacy mode untuk sunset-fix tests
            "i_ratio_threshold": 0.05,
            "debounce_consecutive_steps": 2,
            "confidence_pct": 95.0,
            "pv_max": 10,
            "min_peer_strings": 3,
            "min_daylight_samples": 5,
        },
        "poa": {
            "emit_all_sources": False,
            "default_source": "auto",
            "site_geometry_path": "config/site_geometry.yaml",
        },
    }


def test_real_open_circuit_pv7_still_flagged(df_sunset_scenario, cfg_sunset_fix):
    """PV7 real open-circuit sepanjang daylight harus tetap di-flag."""
    sm = M2bOpenCircuit(poa=_MockPOAWithSunsetLag())
    findings = sm.run(df_sunset_scenario, cfg_sunset_fix)
    assert any(f.pv_string == "PV7" for f in findings), "PV7 (real fault) harus flagged"
    pv7 = next(f for f in findings if f.pv_string == "PV7")
    assert pv7.fault_type == "open_circuit"
    assert pv7.confidence == 95.0


def test_sunset_strings_not_false_positive(df_sunset_scenario, cfg_sunset_fix):
    """PV1-PV6, PV8-PV10 I=0 di 18:00+ TIDAK harus flagged (sunset cutoff bekerja)."""
    sm = M2bOpenCircuit(poa=_MockPOAWithSunsetLag())
    findings = sm.run(df_sunset_scenario, cfg_sunset_fix)
    non_pv7 = [f for f in findings if f.pv_string != "PV7"]
    assert len(non_pv7) == 0, (
        f"Sunset cutoff TIDAK bekerja: false positives di {[f.pv_string for f in non_pv7]}"
    )


def test_disabled_sunset_filters_regress_to_false_positives(df_sunset_scenario):
    """Sanity check: kalau sunset filters DI-DISABLE (hour_cutoff=24, no shutdown,
    poa_floor=0), behavior regress ke pre-fix dan emit false positives.

    Test ini memastikan sunset filters BENAR aktif by default.
    """
    cfg_no_fix = {
        "m2b_open_circuit": {
            "poa_threshold_wm2": 200.0,
            "poa_floor_wm2": 0.0,              # disabled
            "hour_cutoff_end": 24.0,           # disabled
            "respect_inverter_shutdown": False, # disabled
            "filter_mode": "hour_cutoff",      # Force legacy mode (24.0 = no cutoff)
            "i_ratio_threshold": 0.05,
            "debounce_consecutive_steps": 2,
            "confidence_pct": 95.0,
            "pv_max": 10,
            "min_peer_strings": 3,
            "min_daylight_samples": 5,
        },
        "poa": {"emit_all_sources": False, "default_source": "auto",
                "site_geometry_path": "config/site_geometry.yaml"},
    }
    sm = M2bOpenCircuit(poa=_MockPOAWithSunsetLag())
    findings = sm.run(df_sunset_scenario, cfg_no_fix)
    # Tanpa filter, semua 10 strings di 18:00-18:30 (POA=250, I=0) jadi false positive.
    # Plus PV7 daylight (real). Expected: > 1 finding.
    assert len(findings) > 1, (
        f"Tanpa sunset filter harus regress false positive, dapat {len(findings)} findings"
    )


def test_evidence_includes_new_filter_fields(df_sunset_scenario, cfg_sunset_fix):
    """Finding evidence harus include 4 new fields (audit trail filter aktif)."""
    sm = M2bOpenCircuit(poa=_MockPOAWithSunsetLag())
    findings = sm.run(df_sunset_scenario, cfg_sunset_fix)
    assert findings, "PV7 finding tidak terdeteksi"
    ev = findings[0].evidence
    assert ev["poa_floor_wm2"] == 50.0
    assert ev["hour_cutoff_end"] == 18.0
    assert ev["respect_inverter_shutdown"] is True
    assert ev["shutdown_col_used"] == "Inverter shutdown time"


# ---------- Wave 8: StringStatus artifact ----------


def test_string_status_artifact_emitted(df_sunset_scenario, cfg_sunset_fix):
    """Wave 8/11: artifact 'StringStatus' di-emit dengan status NORMAL | open_circuit | EMPTY."""
    sm = M2bOpenCircuit(poa=_MockPOAWithSunsetLag())
    sm.run(df_sunset_scenario, cfg_sunset_fix)
    assert "StringStatus" in sm.artifacts
    df = sm.artifacts["StringStatus"]
    assert "status" in df.columns
    assert set(df["status"].unique()).issubset({"NORMAL", "open_circuit", "EMPTY"})
    pv7 = df[df["pv_string"] == "PV7"]
    assert not pv7.empty
    assert (pv7["status"] == "open_circuit").any()
    pv1 = df[df["pv_string"] == "PV1"]
    assert (pv1["status"] == "NORMAL").all()


# ---------- Fase 2 Wave 2: solar_elevation filter ----------


class _MockPOAWithSolarElev:
    """Same POA + sun-curve elevation untuk solar_elevation mode test."""

    def get_poa(self, timestamps, wb_id, source="auto"):
        ts_idx = pd.DatetimeIndex(timestamps)
        out = []
        for ts in ts_idx:
            hour = ts.hour + ts.minute / 60.0
            if 6.0 <= hour <= 18.0:
                sun = np.sin(np.pi * (hour - 6.0) / 12.0) ** 2
                out.append(1000.0 * sun)
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


def test_solar_elevation_filter_evidence(df_sunset_scenario):
    """filter_mode='solar_elevation' -> evidence ber-filter_mode_effective='solar_elevation'."""
    cfg = {
        "m2b_open_circuit": {
            "poa_threshold_wm2": 200.0,
            "poa_floor_wm2": 50.0,
            "respect_inverter_shutdown": True,
            "filter_mode": "solar_elevation",
            "solar_elevation_min_deg": 5.0,
            "i_ratio_threshold": 0.05,
            "debounce_consecutive_steps": 2,
            "confidence_pct": 95.0,
            "pv_max": 10,
            "min_peer_strings": 3,
            "min_daylight_samples": 5,
        },
        "poa": {"emit_all_sources": False, "default_source": "auto",
                "site_geometry_path": "config/site_geometry.yaml"},
    }
    sm = M2bOpenCircuit(poa=_MockPOAWithSolarElev())
    findings = sm.run(df_sunset_scenario, cfg)
    assert findings, "PV7 (real fault) harus tetap terdeteksi via solar_elevation mode"
    ev = findings[0].evidence
    assert ev["filter_mode"] == "solar_elevation"
    assert ev["filter_mode_effective"] == "solar_elevation"
    assert ev["solar_elevation_min_deg"] == 5.0


# ---------- Wave 11 hotfix #3: fan-out NORMAL placeholder ----------


def test_string_status_emitted_even_when_poa_fails(synthetic_combined_df_with_outlier):
    """Wave 11 hotfix #3: fan-out StringStatus dengan NORMAL placeholder kalau
    POA query gagal pada semua inverter."""
    from pv_pipeline.open_circuit import M2bOpenCircuit

    class _FailingPOA:
        def get_poa(self, timestamps, wb_id, source="auto"):
            raise RuntimeError("synthetic POA failure")

        def get_solar_elevation(self, timestamps):
            return pd.Series([-45.0] * len(timestamps), index=timestamps)

    cfg = {
        "m2b_open_circuit": {
            "pv_max": 10,  # match synthetic fixture
            "filter_mode": "solar_elevation",
            "solar_elevation_min_deg": 5.0,
        },
        "poa": {"emit_all_sources": False, "default_source": "auto"},
    }
    sm = M2bOpenCircuit(poa=_FailingPOA())
    sm.run(synthetic_combined_df_with_outlier, cfg)
    assert "StringStatus" in sm.artifacts
    df = sm.artifacts["StringStatus"]
    assert not df.empty
    assert (df["status"] == "NORMAL").all()
    assert "note" in df.columns
    inv_in_df = set(synthetic_combined_df_with_outlier["Inverter_ID"].dropna().unique())
    assert set(df["inverter_id"].astype(str).unique()) == inv_in_df
