"""Tests for ``pv_pipeline.m2a.low_irradiance`` (Fase 3 Task #6)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.core import Severity
from pv_pipeline.m2a.low_irradiance import (
    DEFAULT_ENABLED,
    DEFAULT_POA_LOW_RANGE,
    DEFAULT_POA_MID_RANGE,
    DEFAULT_R_SQUARED_MIN,
    DEFAULT_SLOPE_THRESHOLD,
    M2aLowIrradiance,
    _find_shutdown_col,
    _normalize_pv_columns,
    _severity_from_slope,
    _wb_from_inverter_id,
    build_inverter_power_series,
    classify_underperformance,
    linear_regression_slope,
)


# ============================================================================
# Config fixtures
# ============================================================================


@pytest.fixture
def low_irr_cfg(m2_config_minimal):
    """Extend m2_config_minimal with enabled m2a_low_irradiance section."""
    cfg = dict(m2_config_minimal)
    cfg["m2a_low_irradiance"] = {
        "enabled": True,
        "poa_low_range": [50.0, 250.0],
        "poa_mid_range": [300.0, 800.0],
        "min_low_samples": 10,        # lower for synthetic data
        "min_mid_samples": 10,
        "slope_threshold": 0.0,
        "r_squared_min": 0.3,
        "hour_range": [6.0, 18.0],
        "hour_cutoff_end": 18.0,
        "solar_elevation_min_deg": 5.0,
        "respect_inverter_shutdown": False,
        "pv_max": 10,
    }
    return cfg


@pytest.fixture
def low_irr_cfg_disabled(m2_config_minimal):
    """cfg with m2a_low_irradiance.enabled=False (opt-in default)."""
    cfg = dict(m2_config_minimal)
    cfg["m2a_low_irradiance"] = {"enabled": False}
    return cfg


# ============================================================================
# Pure-utility tests
# ============================================================================


class TestClassifyUnderperformance:
    def test_low_only_flagged(self):
        assert classify_underperformance(-0.001, 0.001) == "low_irradiance_underperform"

    def test_both_flagged_general(self):
        assert classify_underperformance(-0.001, -0.001) == "general_underperform"

    def test_neither_flagged(self):
        assert classify_underperformance(0.001, 0.001) == "normal"

    def test_low_only_at_threshold(self):
        # slope_low exactly at threshold -> not flagged
        assert classify_underperformance(0.0, 0.001) == "normal"

    def test_nan_low_slope_is_normal(self):
        assert classify_underperformance(float("nan"), 0.001) == "normal"

    def test_custom_threshold(self):
        assert classify_underperformance(
            0.0001, 0.001, slope_threshold=0.0005,
        ) == "low_irradiance_underperform"


class TestSeverityFromSlope:
    def test_above_threshold_info(self):
        assert _severity_from_slope(0.001, r_squared_low=0.8) == Severity.INFO

    def test_at_threshold_info(self):
        assert _severity_from_slope(0.0, r_squared_low=0.8) == Severity.INFO

    def test_nan_slope_info(self):
        assert _severity_from_slope(float("nan"), r_squared_low=0.8) == Severity.INFO

    def test_negative_slope_strong_fit_critical(self):
        # delta=0.001, r2=1.0 -> score=0.001 -> CRITICAL (>=0.0008)
        assert _severity_from_slope(-0.001, r_squared_low=1.0) == Severity.CRITICAL

    def test_negative_slope_medium_fit_high(self):
        # delta=0.0008, r2=0.6 -> score=0.00048 -> HIGH (>=0.0004)
        assert _severity_from_slope(-0.0008, r_squared_low=0.6) == Severity.HIGH

    def test_small_neg_slope_medium(self):
        # delta=0.0002, r2=0.8 -> score=0.00016 -> MEDIUM (>=0.0001)
        assert _severity_from_slope(-0.0002, r_squared_low=0.8) == Severity.MEDIUM

    def test_tiny_neg_slope_info(self):
        # delta=0.00005, r2=0.8 -> score=0.00004 -> INFO
        assert _severity_from_slope(-0.00005, r_squared_low=0.8) == Severity.INFO

    def test_low_r2_downgrades(self):
        # delta=0.001 but r2=0.0 -> score=0 -> INFO
        assert _severity_from_slope(-0.001, r_squared_low=0.0) == Severity.INFO


class TestLinearRegressionSlope:
    def test_perfect_linear(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = 2.0 * x + 3.0
        slope, intercept, r2, n = linear_regression_slope(x, y)
        assert slope == pytest.approx(2.0)
        assert intercept == pytest.approx(3.0)
        assert r2 == pytest.approx(1.0)
        assert n == 5

    def test_noisy_linear(self):
        rng = np.random.default_rng(42)
        x = np.linspace(0, 100, 100)
        y = 0.5 * x + 10 + rng.normal(0, 0.5, 100)
        slope, intercept, r2, n = linear_regression_slope(x, y)
        assert abs(slope - 0.5) < 0.05
        assert r2 > 0.99

    def test_zero_variance_x_returns_nan(self):
        x = np.array([5.0, 5.0, 5.0])
        y = np.array([1.0, 2.0, 3.0])
        slope, intercept, r2, n = linear_regression_slope(x, y)
        assert np.isnan(slope)
        assert np.isnan(intercept)
        assert n == 3

    def test_single_sample_returns_nan(self):
        slope, intercept, r2, n = linear_regression_slope(
            np.array([1.0]), np.array([2.0]),
        )
        assert np.isnan(slope)
        assert n == 1

    def test_empty_returns_nan(self):
        slope, intercept, r2, n = linear_regression_slope(
            np.array([]), np.array([]),
        )
        assert np.isnan(slope)
        assert n == 0

    def test_nan_filtered_out(self):
        x = np.array([1.0, 2.0, np.nan, 4.0, 5.0])
        y = np.array([2.0, 4.0, 8.0, 8.0, 10.0])
        slope, _, _, n = linear_regression_slope(x, y)
        assert n == 4  # NaN row dropped
        assert slope == pytest.approx(2.0)

    def test_constant_y_zero_slope(self):
        x = np.array([1.0, 2.0, 3.0, 4.0])
        y = np.array([5.0, 5.0, 5.0, 5.0])
        slope, intercept, r2, n = linear_regression_slope(x, y)
        assert slope == pytest.approx(0.0)
        assert intercept == pytest.approx(5.0)
        # ss_tot = 0, ss_res = 0 -> r2 = 1.0 (perfect fit on flat line)
        assert r2 == 1.0


class TestWbFromInverterId:
    def test_standard(self):
        assert _wb_from_inverter_id("WB05-INV12") == "WB05"

    def test_lowercase(self):
        assert _wb_from_inverter_id("wb02-inv05") == "WB02"

    def test_empty(self):
        assert _wb_from_inverter_id("") == ""


class TestFindShutdownCol:
    def test_canonical(self):
        df = pd.DataFrame({"Inverter shutdown time": []})
        assert _find_shutdown_col(df) == "Inverter shutdown time"

    def test_missing(self):
        df = pd.DataFrame({"X": []})
        assert _find_shutdown_col(df) is None


class TestNormalizePvColumns:
    def test_title_case_to_lowercase(self):
        df = pd.DataFrame({"PV15 Input Voltage(V)": [1.0]})
        out = _normalize_pv_columns(df)
        assert "PV15 input voltage(V)" in out.columns


# ============================================================================
# build_inverter_power_series
# ============================================================================


class TestBuildInverterPowerSeries:
    def test_v_i_fallback(self):
        df = pd.DataFrame({
            "PV1 input voltage(V)": [1000.0, 1200.0],
            "PV1 input current(A)": [10.0, 12.0],
            "PV2 input voltage(V)": [1100.0, 1100.0],
            "PV2 input current(A)": [11.0, 11.0],
        })
        p_inv = build_inverter_power_series(df, [1, 2])
        # PV1+PV2 t0: 10 + 12.1 = 22.1 kW
        # PV1+PV2 t1: 14.4 + 12.1 = 26.5 kW
        assert p_inv[0] == pytest.approx(22.1)
        assert p_inv[1] == pytest.approx(26.5)

    def test_prefer_power_col(self):
        df = pd.DataFrame({
            "PV1 Power(kW)": [5.0, 6.0],
            "PV1 input voltage(V)": [1000.0, 1200.0],
            "PV1 input current(A)": [10.0, 12.0],
        })
        p_inv = build_inverter_power_series(df, [1])
        assert p_inv[0] == 5.0
        assert p_inv[1] == 6.0

    def test_missing_returns_nan_sum_zero(self):
        df = pd.DataFrame({"X": [1.0, 2.0]})
        p_inv = build_inverter_power_series(df, [1])
        # All NaN -> nansum -> 0.0
        assert p_inv[0] == 0.0


# ============================================================================
# Module-level defaults
# ============================================================================


class TestDefaults:
    def test_default_enabled_false(self):
        assert DEFAULT_ENABLED is False

    def test_default_poa_low_range(self):
        assert DEFAULT_POA_LOW_RANGE == (50.0, 250.0)

    def test_default_poa_mid_range(self):
        assert DEFAULT_POA_MID_RANGE == (300.0, 800.0)

    def test_default_slope_threshold(self):
        assert DEFAULT_SLOPE_THRESHOLD == 0.0

    def test_default_r_squared_min(self):
        assert DEFAULT_R_SQUARED_MIN == 0.3


# ============================================================================
# Synthetic-data helpers
# ============================================================================


def _make_inverter_df(inverter_id: str, rs_high: bool = False, seed: int = 42):
    """Build combined_df for a single inverter spanning full daylight.

    rs_high=True simulates HIGH series resistance: at low POA, current
    output is depressed disproportionately. Resulting PR_proxy slope in
    low band becomes NEGATIVE (PR decreases as POA rises in low range).

    rs_high=False is HEALTHY: linear I vs POA -> PR_proxy roughly flat
    in low band, so slope >= 0.
    """
    rng = np.random.default_rng(seed)
    n = 145
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 18:00", freq="5min")[:n]
    hours = np.linspace(0, 12, n)
    sun = np.sin(np.pi * hours / 12) ** 2
    poa_proxy = 1000.0 * sun

    rows = []
    for ts_i, ts in enumerate(t):
        row = {"Inverter_ID": inverter_id, "Start Time": ts}
        for pv_n in range(1, 6):
            if rs_high:
                if poa_proxy[ts_i] < 250:
                    # Sub-linear penalty at low POA (mimics high Rs)
                    I_factor = (poa_proxy[ts_i] / 1000.0) ** 0.7
                    I_pv = I_factor * 13.0 + rng.normal(0, 0.05)
                else:
                    I_pv = (poa_proxy[ts_i] / 1000.0) * 13.0 + rng.normal(0, 0.05)
            else:
                I_pv = (poa_proxy[ts_i] / 1000.0) * 13.0 + rng.normal(0, 0.05)
            V_pv = (1200.0 + 200.0 * np.exp(-3 * sun[ts_i])) * (
                1.0 + rng.normal(0, 0.001)
            )
            row[f"PV{pv_n} input voltage(V)"] = V_pv
            row[f"PV{pv_n} input current(A)"] = I_pv
        rows.append(row)
    return pd.DataFrame(rows)


# ============================================================================
# M2aLowIrradiance.run() integration tests
# ============================================================================


class TestM2aLowIrradianceRunDefaults:
    def test_default_disabled_returns_empty(
        self, synthetic_combined_df, low_irr_cfg_disabled, mock_poa
    ):
        sm = M2aLowIrradiance(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, low_irr_cfg_disabled)
        assert findings == []
        assert sm.artifacts == {}

    def test_no_m2a_low_irradiance_section_returns_empty(
        self, synthetic_combined_df, m2_config_minimal, mock_poa
    ):
        sm = M2aLowIrradiance(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, m2_config_minimal)
        assert findings == []

    def test_missing_inverter_id_returns_empty(self, low_irr_cfg, mock_poa):
        df = pd.DataFrame({"Start Time": [pd.Timestamp("2026-05-14 12:00")]})
        sm = M2aLowIrradiance(poa=mock_poa)
        assert sm.run(df, low_irr_cfg) == []

    def test_missing_start_time_returns_empty(self, low_irr_cfg, mock_poa):
        df = pd.DataFrame({"Inverter_ID": ["WB01-INV01"]})
        sm = M2aLowIrradiance(poa=mock_poa)
        assert sm.run(df, low_irr_cfg) == []

    def test_empty_df_returns_empty(self, low_irr_cfg, mock_poa):
        df = pd.DataFrame(columns=["Inverter_ID", "Start Time"])
        sm = M2aLowIrradiance(poa=mock_poa)
        assert sm.run(df, low_irr_cfg) == []


class TestM2aLowIrradianceBasic:
    def test_artifact_fit_always_emitted(self, low_irr_cfg, mock_poa):
        """LowIrradianceFit artifact should always emit even when no findings."""
        df = _make_inverter_df("WB05-INV01", rs_high=False)
        sm = M2aLowIrradiance(poa=mock_poa)
        sm.run(df, low_irr_cfg)
        assert "LowIrradianceFit" in sm.artifacts
        fit = sm.artifacts["LowIrradianceFit"]
        for col in ("inverter_id", "n_low_samples", "n_mid_samples",
                    "slope_low", "slope_mid", "r_squared_low",
                    "r_squared_mid", "classification", "severity"):
            assert col in fit.columns

    def test_artifact_summary_emitted(self, low_irr_cfg, mock_poa):
        df = _make_inverter_df("WB05-INV01", rs_high=False)
        sm = M2aLowIrradiance(poa=mock_poa)
        sm.run(df, low_irr_cfg)
        assert "LowIrradianceSummary" in sm.artifacts
        summary = sm.artifacts["LowIrradianceSummary"]
        assert len(summary) == 1
        for col in ("normal", "low_irradiance_underperform",
                    "general_underperform", "skipped"):
            assert col in summary.columns

    def test_reproducible(self, low_irr_cfg, mock_poa):
        """Same input -> same output."""
        df = _make_inverter_df("WB05-INV01", rs_high=False)
        sm1 = M2aLowIrradiance(poa=mock_poa)
        sm2 = M2aLowIrradiance(poa=mock_poa)
        f1 = sm1.run(df, low_irr_cfg)
        f2 = sm2.run(df, low_irr_cfg)
        assert len(f1) == len(f2)


class TestM2aLowIrradianceConfigOverrides:
    def test_higher_threshold_flags_more(self, mock_poa):
        """Raising slope_threshold flags more inverters."""
        df = _make_inverter_df("WB05-INV01", rs_high=False, seed=42)

        cfg_strict = {
            "m2a_low_irradiance": {
                "enabled": True,
                "poa_low_range": [50.0, 250.0],
                "poa_mid_range": [300.0, 800.0],
                "min_low_samples": 5,
                "min_mid_samples": 5,
                "slope_threshold": 0.0,
                "r_squared_min": 0.0,
                "hour_range": [6.0, 18.0],
                "respect_inverter_shutdown": False,
                "pv_max": 10,
            },
            "poa": {"site_geometry_path": "n/a"},
        }
        cfg_loose = dict(cfg_strict)
        cfg_loose["m2a_low_irradiance"] = dict(cfg_strict["m2a_low_irradiance"])
        cfg_loose["m2a_low_irradiance"]["slope_threshold"] = 1.0  # flag ANY slope < 1.0

        sm_s = M2aLowIrradiance(poa=mock_poa)
        sm_l = M2aLowIrradiance(poa=mock_poa)
        f_strict = sm_s.run(df, cfg_strict)
        f_loose = sm_l.run(df, cfg_loose)
        assert len(f_loose) >= len(f_strict)

    def test_insufficient_samples_skipped(self, low_irr_cfg, mock_poa):
        """Inverter dengan < min_low_samples skipped."""
        ts = pd.date_range("2026-05-14 12:00", periods=3, freq="5min")
        rows = []
        for t in ts:
            row = {"Inverter_ID": "WB01-INV01", "Start Time": t}
            for pv in range(1, 6):
                row[f"PV{pv} input voltage(V)"] = 1200.0
                row[f"PV{pv} input current(A)"] = 10.0
            rows.append(row)
        df = pd.DataFrame(rows)
        cfg = dict(low_irr_cfg)
        cfg["m2a_low_irradiance"] = dict(low_irr_cfg["m2a_low_irradiance"])
        cfg["m2a_low_irradiance"]["min_low_samples"] = 100
        sm = M2aLowIrradiance(poa=mock_poa)
        findings = sm.run(df, cfg)
        assert findings == []
        if "LowIrradianceSummary" in sm.artifacts:
            assert sm.artifacts["LowIrradianceSummary"]["skipped"].iloc[0] >= 1


class TestM2aLowIrradianceMultipleInverters:
    def test_multi_inverter_processed(
        self, synthetic_combined_df, low_irr_cfg, mock_poa,
    ):
        """Fixture has 3 inverters -- all should get artifact rows."""
        sm = M2aLowIrradiance(poa=mock_poa)
        sm.run(synthetic_combined_df, low_irr_cfg)
        if "LowIrradianceFit" in sm.artifacts:
            fit = sm.artifacts["LowIrradianceFit"]
            # All 3 inverters should appear (even if skipped due to insufficient
            # low-band samples).
            assert fit["inverter_id"].nunique() == 3
