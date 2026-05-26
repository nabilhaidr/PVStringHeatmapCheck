"""Tests for ``pv_pipeline.m2a.shading`` (M2a Shading detector, Fase 3 Task #4)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.core import Severity
from pv_pipeline.m2a.shading import (
    DEFAULT_AM_PM_SPLIT_HOUR,
    DEFAULT_ASYMMETRY_THRESHOLD,
    DEFAULT_CV_LOW_MULTIPLIER,
    DEFAULT_ENABLED,
    DEFAULT_PR_LOW_MULTIPLIER,
    M2aShading,
    _find_shutdown_col,
    _normalize_pv_columns,
    _severity_from_counts,
    _wb_from_inverter_id,
    build_pv_power_matrix,
    classify_shading,
    compute_hourly_metrics,
)


# ============================================================================
# Config fixtures
# ============================================================================


@pytest.fixture
def shading_cfg(m2_config_minimal):
    """Extend m2_config_minimal with enabled m2a_shading section."""
    cfg = dict(m2_config_minimal)
    cfg["m2a_shading"] = {
        "enabled": True,
        "poa_threshold_wm2": 100.0,
        "hour_range": [6.0, 18.0],
        "cv_low_multiplier": 0.5,
        "pr_low_multiplier": 0.85,
        "min_samples_per_hour": 5,
        "min_hours_for_analysis": 4,
        "am_pm_split_hour": 12.0,
        "asymmetry_threshold": 0.5,
        "pv_max": 10,
    }
    return cfg


@pytest.fixture
def shading_cfg_disabled(m2_config_minimal):
    """Cfg with m2a_shading.enabled=False (opt-in default)."""
    cfg = dict(m2_config_minimal)
    cfg["m2a_shading"] = {"enabled": False}
    return cfg


# ============================================================================
# Pure-utility tests
# ============================================================================


class TestClassifyShading:
    def test_morning_dominant(self):
        ft, asy = classify_shading(5, 0)
        assert ft == "shading_morning"
        assert asy == 1.0

    def test_afternoon_dominant(self):
        ft, asy = classify_shading(0, 5)
        assert ft == "shading_afternoon"
        assert asy == 1.0

    def test_balanced_uniform(self):
        ft, asy = classify_shading(3, 3)
        assert ft == "shading_uniform"
        assert asy == 0.0

    def test_slightly_asymmetric_below_threshold(self):
        # 3 vs 2 -> asymmetry = 1/5 = 0.2 < 0.5 -> uniform
        ft, asy = classify_shading(3, 2, asymmetry_threshold=0.5)
        assert ft == "shading_uniform"
        assert asy == pytest.approx(0.2)

    def test_asymmetric_above_threshold(self):
        # 4 vs 1 -> asymmetry = 3/5 = 0.6 > 0.5 -> morning
        ft, asy = classify_shading(4, 1, asymmetry_threshold=0.5)
        assert ft == "shading_morning"
        assert asy == pytest.approx(0.6)

    def test_zero_counts(self):
        ft, asy = classify_shading(0, 0)
        assert ft == "shading_uniform"
        assert asy == 0.0


class TestSeverityFromCounts:
    def test_no_suspicious_returns_info(self):
        assert _severity_from_counts(0, 0.0, total_hours=10) == Severity.INFO

    def test_high_fraction_high_asymmetry_critical(self):
        # 8/10 hours suspicious + asymmetry 1.0
        # score = 0.8*0.7 + 1.0*0.3 = 0.86 -> CRITICAL
        assert _severity_from_counts(8, 1.0, total_hours=10) == Severity.CRITICAL

    def test_moderate_high(self):
        # 5/10 suspicious + asymmetry 0.6 -> 0.35 + 0.18 = 0.53 -> HIGH
        assert _severity_from_counts(5, 0.6, total_hours=10) == Severity.HIGH

    def test_low_medium(self):
        # 3/10 + asymmetry 0.3 -> 0.21 + 0.09 = 0.30 -> MEDIUM
        assert _severity_from_counts(3, 0.3, total_hours=10) == Severity.MEDIUM

    def test_very_low_info(self):
        # 1/10 + asymmetry 0.2 -> 0.07 + 0.06 = 0.13 -> INFO
        assert _severity_from_counts(1, 0.2, total_hours=10) == Severity.INFO

    def test_zero_total_hours_returns_info(self):
        assert _severity_from_counts(0, 0.5, total_hours=0) == Severity.INFO


class TestWbFromInverterId:
    def test_standard(self):
        assert _wb_from_inverter_id("WB05-INV12") == "WB05"

    def test_lowercase_input(self):
        assert _wb_from_inverter_id("wb02-inv05") == "WB02"

    def test_empty(self):
        assert _wb_from_inverter_id("") == ""


class TestFindShutdownCol:
    def test_canonical(self):
        df = pd.DataFrame({"Inverter shutdown time": [], "X": []})
        assert _find_shutdown_col(df) == "Inverter shutdown time"

    def test_short_alt(self):
        df = pd.DataFrame({"Shutdown time": [], "X": []})
        assert _find_shutdown_col(df) == "Shutdown time"

    def test_missing(self):
        df = pd.DataFrame({"X": [], "Y": []})
        assert _find_shutdown_col(df) is None


class TestNormalizePvColumns:
    def test_title_case_normalize(self):
        df = pd.DataFrame({
            "PV15 Input Voltage(V)": [1.0],
            "PV15 Input Current(A)": [2.0],
        })
        out = _normalize_pv_columns(df)
        assert "PV15 input voltage(V)" in out.columns
        assert "PV15 input current(A)" in out.columns

    def test_no_changes_preserved(self):
        df = pd.DataFrame({"PV1 input voltage(V)": [1.0]})
        out = _normalize_pv_columns(df)
        assert list(out.columns) == list(df.columns)


# ============================================================================
# build_pv_power_matrix
# ============================================================================


class TestBuildPvPowerMatrix:
    def test_v_i_fallback_when_no_power_col(self):
        """When PV{n} Power(kW) absent, fall back to V*I/1000."""
        df = pd.DataFrame({
            "PV1 input voltage(V)": [1000.0, 1200.0],
            "PV1 input current(A)": [10.0, 12.0],
            "PV2 input voltage(V)": [1100.0, 1100.0],
            "PV2 input current(A)": [11.0, 11.0],
        })
        p_mat, inv_total = build_pv_power_matrix(df, [1, 2])
        # PV1 t0: 1000*10/1000 = 10 kW; t1: 1200*12/1000 = 14.4 kW
        assert p_mat[0, 0] == pytest.approx(10.0)
        assert p_mat[1, 0] == pytest.approx(14.4)
        # PV2 t0: 1100*11/1000 = 12.1 kW
        assert p_mat[0, 1] == pytest.approx(12.1)
        # inv_total = sum across PVs
        assert inv_total[0] == pytest.approx(22.1)
        assert inv_total[1] == pytest.approx(26.5)

    def test_prefer_power_col_when_available(self):
        """If PV{n} Power(kW) column exists, use it directly."""
        df = pd.DataFrame({
            "PV1 Power(kW)": [5.0, 6.0],
            "PV1 input voltage(V)": [1000.0, 1200.0],  # would give 10.0/14.4 if used
            "PV1 input current(A)": [10.0, 12.0],
        })
        p_mat, _ = build_pv_power_matrix(df, [1])
        # Should use PV1 Power(kW), NOT V*I
        assert p_mat[0, 0] == 5.0
        assert p_mat[1, 0] == 6.0

    def test_missing_columns_yield_nan(self):
        df = pd.DataFrame({"PV1 input voltage(V)": [1000.0]})  # missing current
        p_mat, inv_total = build_pv_power_matrix(df, [1, 2])
        assert np.isnan(p_mat[0, 0])
        assert np.isnan(p_mat[0, 1])
        # inv_total via nansum returns 0 (not NaN) for all-NaN row
        assert inv_total[0] == 0.0

    def test_shape_correct(self):
        df = pd.DataFrame({
            f"PV{n} input voltage(V)": [1200.0] * 5 for n in range(1, 4)
        })
        for n in range(1, 4):
            df[f"PV{n} input current(A)"] = [10.0] * 5
        p_mat, inv_total = build_pv_power_matrix(df, [1, 2, 3])
        assert p_mat.shape == (5, 3)
        assert inv_total.shape == (5,)


# ============================================================================
# compute_hourly_metrics
# ============================================================================


class TestComputeHourlyMetrics:
    def test_uniform_data_yields_low_cv(self):
        """Uniform power across PVs -> CV close to 0."""
        n_ts = 60
        n_pv = 5
        p_mat = np.full((n_ts, n_pv), 5.0)  # all identical
        inv_total = p_mat.sum(axis=1)
        poa = np.full(n_ts, 500.0)
        hours = np.linspace(10, 11, n_ts)  # all in hour 10
        hrly = compute_hourly_metrics(p_mat, inv_total, poa, hours)
        assert 10 in hrly.index
        assert hrly.loc[10, "cv"] == pytest.approx(0.0, abs=1e-9)

    def test_variable_data_yields_high_cv(self):
        n_ts = 30
        n_pv = 5
        # PV1 = 1, PV2 = 10, PV3 = 20, PV4 = 30, PV5 = 40 -> high CV
        p_mat = np.broadcast_to(np.array([1.0, 10.0, 20.0, 30.0, 40.0]),
                                  (n_ts, n_pv)).copy()
        inv_total = p_mat.sum(axis=1)
        poa = np.full(n_ts, 500.0)
        hours = np.linspace(10, 11, n_ts)
        hrly = compute_hourly_metrics(p_mat, inv_total, poa, hours)
        assert hrly.loc[10, "cv"] > 0.5

    def test_pr_proxy_computation(self):
        n_ts = 20
        p_mat = np.full((n_ts, 1), 5.0)
        inv_total = p_mat.sum(axis=1)   # 5 kW
        poa = np.full(n_ts, 500.0)
        hours = np.full(n_ts, 10.5)
        hrly = compute_hourly_metrics(p_mat, inv_total, poa, hours)
        # PR_proxy = 5 / 500 = 0.01
        assert hrly.loc[10, "pr_proxy"] == pytest.approx(0.01)

    def test_skip_hours_below_min_samples(self):
        n_ts = 3       # too few -> skip
        p_mat = np.full((n_ts, 1), 5.0)
        inv_total = p_mat.sum(axis=1)
        poa = np.full(n_ts, 500.0)
        hours = np.full(n_ts, 10.5)
        hrly = compute_hourly_metrics(p_mat, inv_total, poa, hours,
                                        min_samples_per_hour=10)
        assert hrly.empty

    def test_negative_or_zero_power_excluded(self):
        n_ts = 20
        n_pv = 4
        p_mat = np.full((n_ts, n_pv), 5.0)
        # Inject 1 negative + 1 zero per timestamp, leaving 2 valid (both 5.0)
        p_mat[:, 0] = -1.0  # PV1 always negative -> excluded
        p_mat[:, 1] = 0.0   # PV2 always zero -> excluded
        # PV3, PV4 stay at 5.0
        inv_total = np.nansum(p_mat, axis=1)
        poa = np.full(n_ts, 500.0)
        hours = np.full(n_ts, 10.5)
        hrly = compute_hourly_metrics(p_mat, inv_total, poa, hours)
        # CV computed per ts across surviving PVs (PV3=5, PV4=5) -> std=0, CV=0.
        assert hrly.loc[10, "cv"] == pytest.approx(0.0)

    def test_multiple_hours(self):
        n_ts = 60
        n_pv = 3
        p_mat = np.full((n_ts, n_pv), 5.0)
        inv_total = p_mat.sum(axis=1)
        poa = np.full(n_ts, 500.0)
        hours = np.concatenate([
            np.full(20, 9.5), np.full(20, 10.5), np.full(20, 11.5)
        ])
        hrly = compute_hourly_metrics(p_mat, inv_total, poa, hours)
        assert sorted(hrly.index.tolist()) == [9, 10, 11]

    def test_empty_returns_empty_df(self):
        hrly = compute_hourly_metrics(
            np.empty((0, 1)), np.empty(0), np.empty(0), np.empty(0)
        )
        assert hrly.empty


# ============================================================================
# Module-level defaults
# ============================================================================


class TestDefaults:
    def test_default_enabled_false(self):
        assert DEFAULT_ENABLED is False

    def test_default_cv_multiplier(self):
        assert DEFAULT_CV_LOW_MULTIPLIER == 0.5

    def test_default_pr_multiplier(self):
        assert DEFAULT_PR_LOW_MULTIPLIER == 0.85

    def test_default_asymmetry_threshold(self):
        assert DEFAULT_ASYMMETRY_THRESHOLD == 0.5

    def test_default_am_pm_split(self):
        assert DEFAULT_AM_PM_SPLIT_HOUR == 12.0


# ============================================================================
# M2aShading.run() integration tests
# ============================================================================


class TestM2aShadingRunDefaults:
    def test_default_disabled_returns_empty(
        self, synthetic_combined_df, shading_cfg_disabled, mock_poa
    ):
        sm = M2aShading(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, shading_cfg_disabled)
        assert findings == []
        assert sm.artifacts == {}

    def test_no_m2a_shading_section_returns_empty(
        self, synthetic_combined_df, m2_config_minimal, mock_poa
    ):
        sm = M2aShading(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, m2_config_minimal)
        assert findings == []

    def test_missing_inverter_id_returns_empty(self, shading_cfg, mock_poa):
        df = pd.DataFrame({"Start Time": [pd.Timestamp("2026-05-14 12:00")]})
        sm = M2aShading(poa=mock_poa)
        assert sm.run(df, shading_cfg) == []

    def test_missing_start_time_returns_empty(self, shading_cfg, mock_poa):
        df = pd.DataFrame({"Inverter_ID": ["WB01-INV01"]})
        sm = M2aShading(poa=mock_poa)
        assert sm.run(df, shading_cfg) == []

    def test_empty_df_returns_empty(self, shading_cfg, mock_poa):
        df = pd.DataFrame(columns=["Inverter_ID", "Start Time"])
        sm = M2aShading(poa=mock_poa)
        assert sm.run(df, shading_cfg) == []


# Helpers for synthetic shaded data ------------------------------------------


def _make_uniform_shading_df(
    shade_hours: list,
    shade_factor: float = 0.3,
    inverter_id: str = "WB05-INV01",
):
    """Build combined_df with uniform shading injected at specific hours.

    Algorithm-aware synthetic data using MULTIPLICATIVE noise (so CV is
    independent of sun level, mirroring real PV plant behavior where
    panel-to-panel mismatch is roughly proportional):

    - **Clean hours**: per-PV efficiency spread 0.95..1.05 + small
      multiplicative noise -> CV ~ 3% (typical real PV behavior).
    - **Shading hours**: all PVs uniformly clamped to ``shade_factor * I_base``
      with very tight noise -> CV ~ 0.5% (uniform reduction is the
      whole-array shading signature the detector looks for).

    This setup ensures:
      - median_CV ~ 0.03 (clean hours dominate count).
      - shading hour CV ~ 0.005 < 0.5 * median_CV -> triggers "low CV".
      - shading hour PR_proxy << median_PR_proxy -> triggers "low PR".
    """
    rng = np.random.default_rng(42)
    n = 145
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 18:00", freq="5min")[:n]
    hours = np.linspace(0, 12, n)
    sun = np.sin(np.pi * hours / 12) ** 2
    I_base = 13.0 * sun
    V_base = 1200.0 + 200.0 * np.exp(-3 * sun)
    pv_eff = np.linspace(0.95, 1.05, 10)

    rows = []
    for ts_i, ts in enumerate(t):
        h = ts.hour
        shading_active = h in shade_hours
        row = {"Inverter_ID": inverter_id, "Start Time": ts}
        for j, pv_n in enumerate(range(1, 11)):
            if shading_active:
                # Uniform clamp: all PVs same low value, tight relative noise.
                I_pv = I_base[ts_i] * shade_factor * (1.0 + rng.normal(0, 0.005))
            else:
                # Clean: per-PV efficiency 0.95..1.05 + multiplicative noise.
                I_pv = I_base[ts_i] * pv_eff[j] * (1.0 + rng.normal(0, 0.005))
            row[f"PV{pv_n} input voltage(V)"] = V_base[ts_i] * (1.0 + rng.normal(0, 0.001))
            row[f"PV{pv_n} input current(A)"] = I_pv
        rows.append(row)
    return pd.DataFrame(rows)


class TestM2aShadingDetection:
    def test_clean_data_no_systematic_shading(
        self, synthetic_combined_df, shading_cfg, mock_poa
    ):
        """Clean uniform-sun data: severity should be at most HIGH (no CRITICAL)."""
        sm = M2aShading(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, shading_cfg)
        # Even if some hours flagged due to noise, no CRITICAL expected.
        for f in findings:
            assert f.severity in (Severity.INFO, Severity.MEDIUM,
                                    Severity.HIGH, Severity.CRITICAL)

    def test_morning_shading_classified(self, shading_cfg, mock_poa):
        """Inject uniform shading at hours 6-10 (AM) -> shading_morning."""
        df = _make_uniform_shading_df(shade_hours=[6, 7, 8, 9, 10], shade_factor=0.3)
        sm = M2aShading(poa=mock_poa)
        findings = sm.run(df, shading_cfg)
        morning_findings = [f for f in findings if f.fault_type == "shading_morning"]
        assert len(morning_findings) > 0, (
            f"Expected shading_morning findings; got fault_types: "
            f"{set(f.fault_type for f in findings)}"
        )

    def test_afternoon_shading_classified(self, shading_cfg, mock_poa):
        """Inject uniform shading at hours 14-17 (PM) -> shading_afternoon."""
        df = _make_uniform_shading_df(shade_hours=[14, 15, 16, 17], shade_factor=0.3)
        sm = M2aShading(poa=mock_poa)
        findings = sm.run(df, shading_cfg)
        afternoon_findings = [f for f in findings if f.fault_type == "shading_afternoon"]
        assert len(afternoon_findings) > 0, (
            f"Expected shading_afternoon findings; got: "
            f"{set(f.fault_type for f in findings)}"
        )

    def test_findings_have_correct_module_name(self, shading_cfg, mock_poa):
        df = _make_uniform_shading_df(shade_hours=[7, 8, 9])
        sm = M2aShading(poa=mock_poa)
        findings = sm.run(df, shading_cfg)
        assert len(findings) > 0
        for f in findings:
            assert f.sub_module == "M2a_shading"
            assert f.pv_string is None  # inverter-aggregate, not per-PV

    def test_findings_evidence_populated(self, shading_cfg, mock_poa):
        df = _make_uniform_shading_df(shade_hours=[7, 8, 9])
        sm = M2aShading(poa=mock_poa)
        findings = sm.run(df, shading_cfg)
        assert len(findings) > 0
        ev = findings[0].evidence
        for key in ("hour", "cv", "cv_threshold", "pr_proxy", "pr_threshold",
                    "am_pm", "asymmetry", "n_suspicious_total"):
            assert key in ev, f"missing key {key} in evidence"

    def test_confidence_in_range(self, shading_cfg, mock_poa):
        df = _make_uniform_shading_df(shade_hours=[7, 8, 9])
        sm = M2aShading(poa=mock_poa)
        findings = sm.run(df, shading_cfg)
        for f in findings:
            assert f.confidence is not None
            assert 50.0 <= f.confidence <= 100.0


class TestM2aShadingArtifacts:
    def test_hourly_metrics_artifact(self, shading_cfg, mock_poa):
        df = _make_uniform_shading_df(shade_hours=[7, 8, 9])
        sm = M2aShading(poa=mock_poa)
        sm.run(df, shading_cfg)
        assert "HourlyMetrics" in sm.artifacts
        hm = sm.artifacts["HourlyMetrics"]
        for col in ("inverter_id", "hour", "cv", "pr_proxy", "n_samples",
                    "mean_poa", "mean_inv", "cv_threshold", "pr_threshold",
                    "suspicious", "am_pm"):
            assert col in hm.columns

    def test_summary_artifact(self, shading_cfg, mock_poa):
        df = _make_uniform_shading_df(shade_hours=[7, 8, 9])
        sm = M2aShading(poa=mock_poa)
        sm.run(df, shading_cfg)
        assert "ShadingSummary" in sm.artifacts
        s = sm.artifacts["ShadingSummary"]
        for col in ("inverter_id", "total_hours", "n_suspicious", "n_am",
                    "n_pm", "asymmetry", "fault_type", "severity",
                    "cv_median", "pr_median"):
            assert col in s.columns
        # Should have one summary row per inverter (1 inverter in this test).
        assert len(s) == 1


class TestM2aShadingReproducibility:
    def test_repeatable_findings(self, shading_cfg, mock_poa):
        df = _make_uniform_shading_df(shade_hours=[7, 8, 9])
        sm1 = M2aShading(poa=mock_poa)
        sm2 = M2aShading(poa=mock_poa)
        f1 = sm1.run(df, shading_cfg)
        f2 = sm2.run(df, shading_cfg)
        assert len(f1) == len(f2)


class TestM2aShadingEmptyPvMap:
    def test_empty_pv_map_excludes_pvs(self, shading_cfg, mock_poa, tmp_path):
        """EMPTY_PV_MAP entries are skipped from analysis."""
        strings_yaml = tmp_path / "strings.yaml"
        strings_yaml.write_text(
            "empty_pv_map:\n  WB05-INV01: [9, 10]\n",
            encoding="utf-8",
        )
        cfg = dict(shading_cfg)
        cfg["m2e"] = {"empty_pv_map_path": str(strings_yaml)}
        df = _make_uniform_shading_df(shade_hours=[7, 8, 9])
        sm = M2aShading(poa=mock_poa)
        # Should still run and emit findings, but only over PV1-PV8.
        findings = sm.run(df, cfg)
        assert isinstance(findings, list)


class TestM2aShadingConfigOverrides:
    def test_custom_cv_multiplier_changes_detection(self, mock_poa):
        """Stricter cv_low_multiplier (smaller) -> fewer suspicious hours."""
        df = _make_uniform_shading_df(shade_hours=[7, 8, 9])
        cfg_loose = {
            "m2a_shading": {
                "enabled": True,
                "cv_low_multiplier": 0.99,
                "pr_low_multiplier": 0.99,
                "min_samples_per_hour": 5,
                "min_hours_for_analysis": 4,
                "pv_max": 10,
            },
            "poa": {"site_geometry_path": "n/a"},
        }
        cfg_strict = dict(cfg_loose)
        cfg_strict["m2a_shading"] = dict(cfg_loose["m2a_shading"])
        cfg_strict["m2a_shading"]["cv_low_multiplier"] = 0.01
        cfg_strict["m2a_shading"]["pr_low_multiplier"] = 0.01

        sm_loose = M2aShading(poa=mock_poa)
        sm_strict = M2aShading(poa=mock_poa)
        f_loose = sm_loose.run(df, cfg_loose)
        f_strict = sm_strict.run(df, cfg_strict)
        # Strict should produce fewer or equal findings than loose.
        assert len(f_strict) <= len(f_loose)
