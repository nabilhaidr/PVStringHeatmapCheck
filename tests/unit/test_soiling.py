"""Tests for ``pv_pipeline.m2a.soiling`` (Fase 3 Task #5 SKELETON).

Skeleton-level tests: opt-in pattern, data-sufficiency gate, util
functions, economic helpers. NOT testing rdtools.soiling_srr call
itself (requires >=90 days data + heavy dep). When real data
accumulates, add integration tests separately.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.core import Severity
from pv_pipeline.m2a.soiling import (
    ACTIVE_POWER_COL_CANDIDATES,
    DEFAULT_CAPACITY_KWP,
    DEFAULT_CLEANING_COST_IDR,
    DEFAULT_ELECTRICITY_TARIFF_IDR,
    DEFAULT_ENABLED,
    DEFAULT_MIN_DAYS,
    DEFAULT_PAYBACK_THRESHOLD_DAYS,
    DEFAULT_RECOMMENDED_DAYS,
    M2aSoiling,
    _find_active_power_col,
    _load_precipitation,
    _normalize_pv_columns,
    _severity_from_economics,
    aggregate_daily,
    compute_cleaning_payback,
    compute_daily_pr_series,
    compute_inverter_power_per_timestamp,
)


# ============================================================================
# Config fixtures
# ============================================================================


@pytest.fixture
def soiling_cfg(m2_config_minimal):
    """Cfg with m2a_soiling.enabled=True + small min_days for testing."""
    cfg = dict(m2_config_minimal)
    cfg["m2a_soiling"] = {
        "enabled": True,
        "min_days": 7,                # tiny for synthetic test
        "recommended_days": 14,
        "capacity_kwp": 100.0,
        "cleaning_cost_idr": 10_000_000.0,
        "electricity_tariff_idr_per_kwh": 1500.0,
        "payback_threshold_days": 30.0,
        "precipitation_path": "",
        "rdtools_reps": 100,
        "rdtools_confidence_level": 68.2,
        "sample_freq_hours": 5.0 / 60.0,
        "pv_max": 10,
    }
    return cfg


@pytest.fixture
def soiling_cfg_disabled(m2_config_minimal):
    """Cfg with m2a_soiling.enabled=False (opt-in default)."""
    cfg = dict(m2_config_minimal)
    cfg["m2a_soiling"] = {"enabled": False}
    return cfg


# ============================================================================
# Pure-utility tests
# ============================================================================


class TestDefaults:
    def test_default_enabled_false(self):
        assert DEFAULT_ENABLED is False

    def test_default_min_days_90(self):
        assert DEFAULT_MIN_DAYS == 90

    def test_default_recommended_180(self):
        assert DEFAULT_RECOMMENDED_DAYS == 180

    def test_default_capacity_kwp(self):
        assert DEFAULT_CAPACITY_KWP == 71500.0

    def test_default_payback_30_days(self):
        assert DEFAULT_PAYBACK_THRESHOLD_DAYS == 30.0


class TestFindActivePowerCol:
    def test_canonical(self):
        df = pd.DataFrame({"Active power(kW)": [1.0]})
        assert _find_active_power_col(df) == "Active power(kW)"

    def test_title_case(self):
        df = pd.DataFrame({"Active Power(kW)": [1.0]})
        assert _find_active_power_col(df) == "Active Power(kW)"

    def test_missing(self):
        df = pd.DataFrame({"X": [1.0]})
        assert _find_active_power_col(df) is None

    def test_candidates_match_constant(self):
        assert "Active power(kW)" in ACTIVE_POWER_COL_CANDIDATES


class TestNormalizePvColumns:
    def test_title_case_normalized(self):
        df = pd.DataFrame({"PV15 Input Voltage(V)": [1.0]})
        out = _normalize_pv_columns(df)
        assert "PV15 input voltage(V)" in out.columns


class TestAggregateDaily:
    def test_basic_sum_with_freq(self):
        """12 samples * 5min = 1 hour, value=100 -> sum 100 (riemann)."""
        ts = pd.date_range("2026-05-14 06:00", periods=12, freq="5min")
        s = aggregate_daily(ts, np.full(12, 100.0), freq_hours=5.0/60.0)
        assert s.iloc[0] == pytest.approx(100.0)

    def test_multi_day_aggregation(self):
        """2 days of constant data -> 2 daily totals."""
        ts_idx = pd.DatetimeIndex(list(
            pd.date_range("2026-05-14 06:00", periods=12, freq="5min")
        ) + list(
            pd.date_range("2026-05-15 06:00", periods=12, freq="5min")
        ))
        s = aggregate_daily(ts_idx, np.full(24, 100.0), freq_hours=5.0/60.0)
        assert len(s) == 2
        assert s.iloc[0] == pytest.approx(100.0)
        assert s.iloc[1] == pytest.approx(100.0)

    def test_skip_nan(self):
        ts = pd.date_range("2026-05-14 06:00", periods=4, freq="5min")
        vals = np.array([100.0, np.nan, 100.0, 100.0])
        s = aggregate_daily(ts, vals, freq_hours=5.0/60.0)
        # 3 valid samples sum=300, * (5/60) = 25.
        assert s.iloc[0] == pytest.approx(25.0)

    def test_empty_returns_empty(self):
        s = aggregate_daily(pd.DatetimeIndex([]), np.array([]))
        assert s.empty

    def test_all_nan_returns_empty(self):
        ts = pd.date_range("2026-05-14 06:00", periods=4, freq="5min")
        s = aggregate_daily(ts, np.full(4, np.nan))
        assert s.empty


class TestComputeDailyPrSeries:
    def test_basic_pr_computation(self):
        """PR = energy / (insolation * capacity)."""
        dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])
        energy = pd.Series([1000.0, 2000.0, 3000.0], index=dates)
        insol = pd.Series([5.0, 6.0, 7.0], index=dates)
        capacity = 1000.0
        pr = compute_daily_pr_series(energy, insol, capacity)
        # PR_0 = 1000 / (5 * 1000) = 0.2
        assert pr.iloc[0] == pytest.approx(0.2)
        assert pr.iloc[1] == pytest.approx(2000.0 / (6.0 * 1000.0))
        assert pr.iloc[2] == pytest.approx(3000.0 / 7000.0)

    def test_pr_filter_unphysical(self):
        """PR > 1.5 or < 0 filtered out."""
        dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])
        # 1st PR=20 (filtered), 2nd PR=0.2 (kept), 3rd PR=-0.1 (filtered)
        energy = pd.Series([100000.0, 1000.0, -500.0], index=dates)
        insol = pd.Series([5.0, 5.0, 5.0], index=dates)
        pr = compute_daily_pr_series(energy, insol, 1000.0)
        assert len(pr) == 1
        assert pr.iloc[0] == pytest.approx(0.2)

    def test_zero_capacity_returns_empty(self):
        dates = pd.to_datetime(["2026-01-01"])
        energy = pd.Series([1000.0], index=dates)
        insol = pd.Series([5.0], index=dates)
        pr = compute_daily_pr_series(energy, insol, 0.0)
        assert pr.empty

    def test_misaligned_indices_intersected(self):
        """Mismatched dates intersect via inner join (dropna)."""
        e = pd.Series([1000.0, 2000.0],
                      index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
        i = pd.Series([5.0, 6.0],
                      index=pd.to_datetime(["2026-01-02", "2026-01-03"]))
        pr = compute_daily_pr_series(e, i, 1000.0)
        # Only 2026-01-02 in both. PR = 2000 / (5 * 1000) = 0.4
        assert len(pr) == 1
        assert pr.iloc[0] == pytest.approx(0.4)


class TestSeverityFromEconomics:
    def test_high_loss_short_payback_critical(self):
        # p_loss=0.15, payback=5 < 30/3=10 -> CRITICAL
        assert _severity_from_economics(0.15, 5.0) == Severity.CRITICAL

    def test_med_loss_short_payback_high(self):
        # p_loss=0.06, payback=20 < 30 (not <10) -> HIGH
        assert _severity_from_economics(0.06, 20.0) == Severity.HIGH

    def test_low_loss_med_payback_medium(self):
        # p_loss=0.03, payback=50 < 60 (2*30) -> MEDIUM
        assert _severity_from_economics(0.03, 50.0) == Severity.MEDIUM

    def test_tiny_loss_long_payback_info(self):
        assert _severity_from_economics(0.01, 100.0) == Severity.INFO

    def test_nan_loss_info(self):
        assert _severity_from_economics(float("nan"), 10.0) == Severity.INFO

    def test_inf_payback_info(self):
        assert _severity_from_economics(0.0, float("inf")) == Severity.INFO


class TestComputeCleaningPayback:
    def test_standard_calc(self):
        """daily_loss = avg_daily_kwh * tariff * p_loss = 50000 * 1500 * 0.05 = 3.75M."""
        loss, payback = compute_cleaning_payback(
            0.05, 50000.0,
            cleaning_cost_idr=10_000_000.0,
            electricity_tariff_idr=1500.0,
        )
        assert loss == pytest.approx(3_750_000.0)
        assert payback == pytest.approx(10_000_000.0 / 3_750_000.0)

    def test_zero_loss_inf_payback(self):
        loss, payback = compute_cleaning_payback(0.0, 50000.0,
                                                  cleaning_cost_idr=10_000_000.0)
        assert loss == 0.0
        assert payback == float("inf")

    def test_zero_cost_inf_payback(self):
        loss, payback = compute_cleaning_payback(0.05, 50000.0,
                                                  cleaning_cost_idr=0.0)
        assert payback == float("inf")


class TestComputeInverterPowerPerTimestamp:
    def test_prefer_active_power_col(self):
        df = pd.DataFrame({
            "Active power(kW)": [10.0, 12.0],
            "PV1 Power(kW)": [5.0, 6.0],
            "PV1 input voltage(V)": [1000.0, 1200.0],
            "PV1 input current(A)": [10.0, 12.0],
        })
        p = compute_inverter_power_per_timestamp(df, [1])
        # Should use Active power(kW), not sum of PVs
        assert p[0] == 10.0
        assert p[1] == 12.0

    def test_fallback_pv_power_kw(self):
        df = pd.DataFrame({
            "PV1 Power(kW)": [5.0, 6.0],
            "PV2 Power(kW)": [3.0, 4.0],
        })
        p = compute_inverter_power_per_timestamp(df, [1, 2])
        assert p[0] == pytest.approx(8.0)
        assert p[1] == pytest.approx(10.0)

    def test_fallback_v_i(self):
        df = pd.DataFrame({
            "PV1 input voltage(V)": [1000.0],
            "PV1 input current(A)": [10.0],
        })
        p = compute_inverter_power_per_timestamp(df, [1])
        # 1000 * 10 / 1000 = 10 kW
        assert p[0] == pytest.approx(10.0)


class TestLoadPrecipitation:
    def test_empty_path_returns_none(self):
        assert _load_precipitation("") is None

    def test_missing_file_returns_none(self):
        assert _load_precipitation("/nonexistent/path.csv") is None

    def test_basic_csv_load(self, tmp_path):
        csv = tmp_path / "precip.csv"
        csv.write_text(
            "date,precipitation_mm\n"
            "2026-05-14,5.0\n"
            "2026-05-15,0.0\n"
            "2026-05-16,12.3\n",
            encoding="utf-8",
        )
        s = _load_precipitation(str(csv))
        assert s is not None
        assert len(s) == 3
        assert s.iloc[0] == 5.0
        assert s.iloc[2] == 12.3


# ============================================================================
# M2aSoiling.run() integration tests (skeleton-level)
# ============================================================================


class TestM2aSoilingRunDefaults:
    def test_default_disabled_returns_empty(
        self, synthetic_combined_df, soiling_cfg_disabled, mock_poa,
    ):
        sm = M2aSoiling(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, soiling_cfg_disabled)
        assert findings == []
        assert sm.artifacts == {}

    def test_no_m2a_soiling_section_returns_empty(
        self, synthetic_combined_df, m2_config_minimal, mock_poa,
    ):
        sm = M2aSoiling(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, m2_config_minimal)
        assert findings == []

    def test_missing_inverter_id_returns_empty(self, soiling_cfg, mock_poa):
        df = pd.DataFrame({"Start Time": [pd.Timestamp("2026-05-14 12:00")]})
        sm = M2aSoiling(poa=mock_poa)
        assert sm.run(df, soiling_cfg) == []

    def test_missing_start_time_returns_empty(self, soiling_cfg, mock_poa):
        df = pd.DataFrame({"Inverter_ID": ["WB01-INV01"]})
        sm = M2aSoiling(poa=mock_poa)
        assert sm.run(df, soiling_cfg) == []

    def test_empty_df_emits_insufficient_data(self, soiling_cfg, mock_poa):
        """Empty df has 0 days -> graceful insufficient_data finding."""
        df = pd.DataFrame(columns=["Inverter_ID", "Start Time"])
        sm = M2aSoiling(poa=mock_poa)
        findings = sm.run(df, soiling_cfg)
        assert len(findings) == 1
        assert findings[0].fault_type == "insufficient_data"
        assert findings[0].evidence["n_days"] == 0


class TestM2aSoilingInsufficientData:
    def test_synthetic_one_day_emits_insufficient(
        self, synthetic_combined_df, soiling_cfg, mock_poa,
    ):
        """synthetic_combined_df has only 1 day -- should emit insufficient_data INFO."""
        sm = M2aSoiling(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, soiling_cfg)
        assert len(findings) == 1
        f = findings[0]
        assert f.fault_type == "insufficient_data"
        assert f.severity == Severity.INFO
        assert f.inverter_id == "SITE"
        assert "insufficient data window" in f.message.lower()
        assert "min_days" in f.evidence
        assert "recommended_days" in f.evidence

    def test_insufficient_emits_economic_analysis_artifact(
        self, synthetic_combined_df, soiling_cfg, mock_poa,
    ):
        sm = M2aSoiling(poa=mock_poa)
        sm.run(synthetic_combined_df, soiling_cfg)
        assert "EconomicAnalysis" in sm.artifacts
        ea = sm.artifacts["EconomicAnalysis"]
        assert len(ea) == 1
        assert ea["status"].iloc[0] == "insufficient_data"
        assert pd.isna(ea["soiling_ratio"].iloc[0])

    def test_evidence_includes_baseline_action_hint(
        self, synthetic_combined_df, soiling_cfg, mock_poa,
    ):
        sm = M2aSoiling(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, soiling_cfg)
        ev = findings[0].evidence
        assert "baseline_action" in ev
        assert "BaselineAccumulator" in ev["baseline_action"]


class TestM2aSoilingSiteScope:
    def test_finding_inverter_id_is_site(
        self, synthetic_combined_df, soiling_cfg, mock_poa,
    ):
        """M2a Soiling is site-level, not per-inverter."""
        sm = M2aSoiling(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, soiling_cfg)
        for f in findings:
            assert f.inverter_id == "SITE"
            assert f.pv_string is None


class TestM2aSoilingReproducibility:
    def test_same_input_same_output(
        self, synthetic_combined_df, soiling_cfg, mock_poa,
    ):
        sm1 = M2aSoiling(poa=mock_poa)
        sm2 = M2aSoiling(poa=mock_poa)
        f1 = sm1.run(synthetic_combined_df, soiling_cfg)
        f2 = sm2.run(synthetic_combined_df, soiling_cfg)
        assert len(f1) == len(f2)
        assert f1[0].fault_type == f2[0].fault_type
