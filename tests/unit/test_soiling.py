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
    reindex_daily_frequency,
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


# ============================================================================
# reindex_daily_frequency (syarat keras rdtools.soiling_srr)
# ============================================================================


def test_reindex_daily_frequency_fills_gaps_for_rdtools():
    """rdtools menolak index tanpa freq harian ("Daily performance metric
    series must have daily frequency"). Baseline punya hari bolong, jadi
    seri harus direindex ke grid kontinu: hari hilang NaN, presipitasi 0."""
    idx = pd.DatetimeIndex(["2025-01-01", "2025-01-02", "2025-01-05"])
    pr = pd.Series([0.8, 0.81, 0.79], index=idx)
    insol = pd.Series([5.0, 5.1, 4.9], index=idx)
    precip = pd.Series([12.0], index=pd.DatetimeIndex(["2025-01-03"]))

    pr_f, insol_f, precip_f = reindex_daily_frequency(pr, insol, precip)

    # Grid kontinu 5 hari dengan freq eksplisit 'D' -- ini yang dicek rdtools.
    assert len(pr_f) == 5
    assert pr_f.index.freq is not None and pr_f.index.freqstr == "D"
    assert insol_f.index.equals(pr_f.index)
    # Hari bolong (03-04 Jan) = NaN di PR/insolation, 0.0 di presipitasi.
    assert np.isnan(pr_f[pd.Timestamp("2025-01-03")])
    assert np.isnan(insol_f[pd.Timestamp("2025-01-04")])
    assert precip_f[pd.Timestamp("2025-01-03")] == 12.0
    assert precip_f[pd.Timestamp("2025-01-04")] == 0.0
    # Nilai hari yang ada tidak berubah.
    assert pr_f[pd.Timestamp("2025-01-05")] == 0.79


def test_reindex_daily_frequency_without_precipitation():
    idx = pd.DatetimeIndex(["2025-01-01", "2025-01-03"])
    pr = pd.Series([0.8, 0.79], index=idx)
    insol = pd.Series([5.0, 4.9], index=idx)

    pr_f, insol_f, precip_f = reindex_daily_frequency(pr, insol, None)

    assert precip_f is None
    assert len(pr_f) == 3 and pr_f.index.freqstr == "D"


# ============================================================================
# _ci_bounds (rdtools sr_ci = np.ndarray; regresi CI selalu NaN)
# ============================================================================


def test_ci_bounds_accepts_numpy_array():
    """rdtools soiling_srr mengembalikan CI sebagai np.ndarray. Ekstraksi
    lama membatasi ke tuple/list -> CI selalu NaN di laporan. Harus menerima
    array (dan list/tuple), serta aman untuk None/kosong."""
    from pv_pipeline.m2a.soiling import _ci_bounds

    lo, hi = _ci_bounds(np.array([0.95, 0.99]))
    assert (lo, hi) == (0.95, 0.99)

    assert _ci_bounds([0.90, 0.94]) == (0.90, 0.94)
    assert _ci_bounds((0.88, 0.92)) == (0.88, 0.92)

    lo, hi = _ci_bounds(None)
    assert np.isnan(lo) and np.isnan(hi)
    lo, hi = _ci_bounds(np.array([0.9]))  # len < 2
    assert np.isnan(lo) and np.isnan(hi)


# ============================================================================
# build_cleaning_impact (sheet CleaningImpact: uplift eksplisit per event)
# ============================================================================


def test_build_cleaning_impact_computes_uplift_and_rupiah():
    """Tiap event = batas antar interval: sr_before = end interval sebelumnya
    (kotor), sr_after = start interval berikutnya (bersih). Energi & rupiah
    dipulihkan/hari = uplift * avg_daily_kwh * tarif."""
    from pv_pipeline.m2a.soiling import build_cleaning_impact, CLEANING_IMPACT_COLUMNS

    ce = pd.DataFrame({
        "start": pd.to_datetime(["2026-01-01", "2026-01-15", "2026-02-01"]),
        "inferred_start_loss": [1.00, 0.98, 0.99],
        "inferred_end_loss": [0.90, 0.92, 0.97],
        "likely_cause": ["unknown", "manual", "rain"],
    })

    impact = build_cleaning_impact(ce, avg_daily_kwh=100_000.0, tariff_idr_per_kwh=1500.0)

    assert list(impact.columns) == CLEANING_IMPACT_COLUMNS
    assert len(impact) == 2  # 3 interval -> 2 batas cleaning
    ev = impact.iloc[0]
    assert ev["date"] == pd.Timestamp("2026-01-15")
    assert ev["sr_before"] == pytest.approx(0.90)   # end interval 0
    assert ev["sr_after"] == pytest.approx(0.98)    # start interval 1
    assert ev["uplift_pct"] == pytest.approx(8.0)
    assert ev["energy_recovered_kwh_per_day"] == pytest.approx(0.08 * 100_000.0)
    assert ev["rupiah_per_day"] == pytest.approx(8000.0 * 1500.0)
    assert ev["likely_cause"] == "manual"


def test_build_cleaning_impact_empty_input_returns_typed_empty():
    from pv_pipeline.m2a.soiling import build_cleaning_impact, CLEANING_IMPACT_COLUMNS

    out = build_cleaning_impact(pd.DataFrame(), 100.0, 1500.0)
    assert list(out.columns) == CLEANING_IMPACT_COLUMNS
    assert out.empty


# ============================================================================
# build_direct_cleaning_impact (pre/post PR manual, INDEPENDEN SRR)
# ============================================================================


def test_build_direct_cleaning_impact_pre_post_and_campaign_grouping():
    """PR sawtooth: kotor ~0.75 sebelum cleaning, bersih ~0.90 sesudah.
    Dua tanggal cleaning berjarak <= gap_days -> satu campaign. Loss =
    (after-before)/after; energi = avg_daily_kwh * loss_fraction."""
    from pv_pipeline.m2a.soiling import build_direct_cleaning_impact

    idx = pd.date_range("2026-03-01", "2026-03-28", freq="D")
    vals = [0.75] * 9 + [0.80, 0.82] + [0.90] * 17
    pr = pd.Series(vals, index=idx)
    manual = pd.DataFrame({
        "date": pd.to_datetime(["2026-03-10", "2026-03-11"]),
        "inverter_id": ["WB03-INV01", "WB03-INV01"],
        "wb": [3, 3],
    })

    out = build_direct_cleaning_impact(
        pr, manual, avg_daily_kwh=100_000.0, tariff_idr_per_kwh=1500.0,
        window_days=7, gap_days=7, min_window_days=2,
    )

    assert len(out) == 1
    ev = out.iloc[0]
    assert ev["cleaning_start"] == pd.Timestamp("2026-03-10")
    assert ev["cleaning_end"] == pd.Timestamp("2026-03-11")
    assert ev["n_strings_cleaned"] == 2
    assert ev["pr_before"] == pytest.approx(0.75)
    assert ev["pr_after"] == pytest.approx(0.90)
    loss = (0.90 - 0.75) / 0.90
    assert ev["soiling_loss_pct"] == pytest.approx(loss * 100.0)
    assert ev["energy_recovered_kwh_per_day"] == pytest.approx(100_000.0 * loss)
    assert ev["rupiah_per_day"] == pytest.approx(100_000.0 * loss * 1500.0)


def test_build_direct_cleaning_impact_skips_campaign_without_enough_pr_days():
    from pv_pipeline.m2a.soiling import (
        build_direct_cleaning_impact, DIRECT_CLEANING_IMPACT_COLUMNS,
    )

    pr = pd.Series([0.8, 0.9, 0.9],
                   index=pd.to_datetime(["2026-03-09", "2026-03-12", "2026-03-13"]))
    manual = pd.DataFrame({"date": pd.to_datetime(["2026-03-10"])})

    out = build_direct_cleaning_impact(pr, manual, 100.0, 1500.0, min_window_days=2)
    assert list(out.columns) == DIRECT_CLEANING_IMPACT_COLUMNS
    assert out.empty


def test_build_direct_cleaning_impact_empty_inputs():
    from pv_pipeline.m2a.soiling import build_direct_cleaning_impact

    assert build_direct_cleaning_impact(pd.Series(dtype=float), pd.DataFrame(), 1.0, 1.0).empty


# ============================================================================
# Temperature correction (temp_correction_factor + PR temp-corrected)
# ============================================================================


def test_temp_correction_factor_reduces_below_one_when_hot():
    from pv_pipeline.m2a.soiling import temp_correction_factor
    import numpy as np

    # gamma=-0.29 %/C, ref 25. Tcell=45 -> CF = 1 + (-0.29/100)*20 = 0.942.
    assert temp_correction_factor(45.0, -0.29, 25.0) == pytest.approx(0.942)
    assert temp_correction_factor(25.0, -0.29, 25.0) == pytest.approx(1.0)
    cf = temp_correction_factor(np.array([25.0, 45.0]), -0.29, 25.0)
    assert cf[1] == pytest.approx(0.942)


def test_compute_daily_pr_series_temp_factor_raises_corrected_pr():
    """PR temp-corrected = E/(H*cap*CF). CF<1 (panas) -> PR naik & flat."""
    dates = pd.to_datetime(["2026-01-01", "2026-01-02"])
    energy = pd.Series([930.0, 930.0], index=dates)   # energi sama
    insol = pd.Series([5.0, 5.0], index=dates)
    tf = pd.Series([1.0, 0.93], index=dates)          # hari-2 lebih panas

    raw = compute_daily_pr_series(energy, insol, 1000.0)
    corr = compute_daily_pr_series(energy, insol, 1000.0, temp_factor_daily=tf)

    # Tanpa koreksi: PR hari-2 = hari-1 (energi & insol sama).
    assert raw.iloc[0] == pytest.approx(raw.iloc[1])
    # Dengan koreksi: hari-2 (panas) di-scale naik -> lebih tinggi dari hari-1.
    assert corr.iloc[1] == pytest.approx(raw.iloc[1] / 0.93)
    assert corr.iloc[1] > corr.iloc[0]


# ============================================================================
# Per-string DirectCleaningImpact + kapasitas per inverter
# ============================================================================


def test_inverter_capacity_kwp_from_empty_map():
    """WB01 18 string aktif x 24 modul x 625 Wp = 270 kWp (x50 inv = 13500 ~
    nameplate WB01). WB05 tanpa slot kosong = 28 x 26 x 625 Wp = 455 kWp."""
    from pv_pipeline.m2a.soiling import _inverter_capacity_kwp, _string_capacity_kwp

    empty_map = {"WB01-INV01": list(range(19, 29))}  # 10 slot kosong
    assert _inverter_capacity_kwp("WB01-INV01", empty_map) == pytest.approx(270.0)
    assert _inverter_capacity_kwp("WB05-INV01", {}) == pytest.approx(455.0)
    assert _string_capacity_kwp("WB01-INV01") == pytest.approx(15.0)
    assert _string_capacity_kwp("WB03-INV02") == pytest.approx(16.25)


def test_build_direct_cleaning_impact_per_string_ranks_dirtiest_first():
    """Dua string dibersihkan pada campaign yang sama; PV10 lebih kotor
    (uplift besar) harus rank 1. pr = (E/H)/cap_string; event pv=NaN skip."""
    from pv_pipeline.m2a.soiling import build_direct_cleaning_impact_per_string

    idx = pd.date_range("2026-03-01", "2026-03-25", freq="D")
    insol = pd.Series(5.0, index=idx)
    cap = 16.25  # WB03: 26 modul x 625 Wp

    def energy(pr):
        return pr * 5.0 * cap

    rows = []
    for d in idx:
        dirty = d < pd.Timestamp("2026-03-10")
        clean = d > pd.Timestamp("2026-03-11")
        if not (dirty or clean):
            continue
        rows.append({"date": d, "Inverter_ID": "WB03-INV01", "pv": 10,
                     "energy_kwh": energy(0.72 if dirty else 0.90)})
        rows.append({"date": d, "Inverter_ID": "WB03-INV01", "pv": 11,
                     "energy_kwh": energy(0.87 if dirty else 0.90)})
    string_daily = pd.DataFrame(rows)

    manual = pd.DataFrame({
        "date": pd.to_datetime(["2026-03-10", "2026-03-11",
                                "2026-03-10", "2026-03-10"]),
        "inverter_id": ["WB03-INV01"] * 3 + ["WB03-INV13"],
        "pv": [10, 10, 11, np.nan],   # baris NaN = string tanpa mapping -> skip
        "st": [5, 5, 6, 1],
        "wb": [3, 3, 3, 3],
    })

    out = build_direct_cleaning_impact_per_string(
        string_daily, insol, manual, window_days=7, gap_days=7, min_window_days=2,
    )

    assert len(out) == 2
    top = out.iloc[0]
    assert (top["inverter_id"], top["pv"], top["st"]) == ("WB03-INV01", 10, 5)
    assert top["rank_uplift"] == 1
    assert top["cleaning_start"] == pd.Timestamp("2026-03-10")
    assert top["cleaning_end"] == pd.Timestamp("2026-03-11")
    assert top["pr_before"] == pytest.approx(0.72)
    assert top["pr_after"] == pytest.approx(0.90)
    assert top["uplift_pct"] == pytest.approx((0.90 - 0.72) / 0.90 * 100.0)
    second = out.iloc[1]
    assert second["pv"] == 11 and second["rank_uplift"] == 2
    assert second["uplift_pct"] == pytest.approx((0.90 - 0.87) / 0.90 * 100.0)


def test_build_direct_cleaning_impact_per_string_empty_inputs():
    from pv_pipeline.m2a.soiling import (
        build_direct_cleaning_impact_per_string, DIRECT_PER_STRING_COLUMNS,
    )

    out = build_direct_cleaning_impact_per_string(
        pd.DataFrame(), pd.Series(dtype=float), pd.DataFrame(),
    )
    assert list(out.columns) == DIRECT_PER_STRING_COLUMNS
    assert out.empty


# ============================================================================
# summarize_soiling_profiles (fix export SoilingRatio)
# ============================================================================


def test_summarize_soiling_profiles_accepts_list_of_series():
    """rdtools mengembalikan stochastic_soiling_profiles sebagai LIST of
    Series; cek isinstance DataFrame lama membuat sheet SoilingRatio tidak
    pernah ter-emit."""
    from pv_pipeline.m2a.soiling import summarize_soiling_profiles

    idx = pd.date_range("2026-01-01", periods=5, freq="D")
    base = np.linspace(1.0, 0.9, 5)
    profiles = [pd.Series(base + off, index=idx) for off in (-0.01, 0.0, 0.01)]
    out = summarize_soiling_profiles(profiles, confidence_level=68.2)
    assert out is not None
    assert list(out.columns) == ["date", "sr_p50", "sr_ci_lower", "sr_ci_upper"]
    assert len(out) == 5
    assert out["sr_p50"].iloc[0] == pytest.approx(1.0)
    assert (out["sr_ci_lower"] <= out["sr_p50"]).all()
    assert (out["sr_p50"] <= out["sr_ci_upper"]).all()


def test_summarize_soiling_profiles_invalid_inputs_return_none():
    from pv_pipeline.m2a.soiling import summarize_soiling_profiles

    assert summarize_soiling_profiles(None) is None
    assert summarize_soiling_profiles([]) is None
    assert summarize_soiling_profiles(pd.DataFrame()) is None


# ============================================================================
# capacity_factor_daily (mask availability M2e di penyebut PR)
# ============================================================================


def test_compute_daily_pr_series_capacity_factor_unbiases_outage():
    """Separuh fleet down: tanpa capacity_factor PR anjlok 50% (terbaca
    soiling oleh SRR); dengan factor 0.5 PR kembali ke nilai sebenarnya."""
    idx = pd.date_range("2026-01-01", periods=3, freq="D")
    energy = pd.Series([100.0, 50.0, 100.0], index=idx)
    insol = pd.Series([5.0, 5.0, 5.0], index=idx)
    cap = 25.0  # PR normal = 100/(5*25) = 0.8

    pr_naive = compute_daily_pr_series(energy, insol, cap)
    assert pr_naive.iloc[1] == pytest.approx(0.4)

    cf = pd.Series([1.0, 0.5, 1.0], index=idx)
    pr = compute_daily_pr_series(energy, insol, cap, capacity_factor_daily=cf)
    assert pr.iloc[0] == pytest.approx(0.8)
    assert pr.iloc[1] == pytest.approx(0.8)


def test_compute_daily_pr_series_capacity_factor_zero_drops_day():
    """Seluruh fleet ter-mask (factor 0) -> hari itu NaN/hilang, bukan inf."""
    idx = pd.date_range("2026-01-01", periods=2, freq="D")
    pr = compute_daily_pr_series(
        pd.Series([100.0, 10.0], index=idx),
        pd.Series([5.0, 5.0], index=idx),
        25.0,
        capacity_factor_daily=pd.Series([1.0, 0.0], index=idx),
    )
    assert pd.Timestamp("2026-01-02") not in pr.index


# ============================================================================
# _load_availability_uptime + build_availability_mask (M2e join)
# ============================================================================


def _write_m2e_findings_xlsx(path, rows):
    with pd.ExcelWriter(path) as xw:
        pd.DataFrame(rows).to_excel(xw, sheet_name="Findings", index=False)


def test_load_availability_uptime_reads_xlsx_and_jsonl(tmp_path):
    import json

    from pv_pipeline.m2a.soiling import _load_availability_uptime

    _write_m2e_findings_xlsx(tmp_path / "m2_findings_20260501.xlsx", [
        {"sub_module": "M2e_inverter", "inverter_id": "WB01-INV01",
         "value": 40.0},
        {"sub_module": "M2e_string", "inverter_id": "WB01-INV01",
         "value": 10.0},
    ])
    (tmp_path / "m2_findings_20260502.jsonl").write_text(
        "\n".join([
            json.dumps({"sub_module": "M2e_inverter",
                        "inverter_id": "WB02-INV03", "value": 91.5}),
            json.dumps({"sub_module": "M2b_intermittent",
                        "inverter_id": "WB02-INV03", "value": 1.0}),
        ]) + "\n",
        encoding="utf-8",
    )

    av = _load_availability_uptime(str(tmp_path))
    assert av is not None
    assert list(av.columns) == ["date", "inverter_id", "uptime_pct"]
    assert len(av) == 2  # baris non-M2e_inverter diabaikan
    assert set(av["inverter_id"]) == {"WB01-INV01", "WB02-INV03"}
    assert set(av["date"]) == {
        pd.Timestamp("2026-05-01"), pd.Timestamp("2026-05-02"),
    }


def test_load_availability_uptime_prefers_jsonl_over_xlsx(tmp_path):
    import json

    from pv_pipeline.m2a.soiling import _load_availability_uptime

    _write_m2e_findings_xlsx(tmp_path / "m2_findings_20260501.xlsx", [
        {"sub_module": "M2e_inverter", "inverter_id": "FROM-XLSX",
         "value": 50.0},
    ])
    (tmp_path / "m2_findings_20260501.jsonl").write_text(
        json.dumps({"sub_module": "M2e_inverter",
                    "inverter_id": "FROM-JSONL", "value": 60.0}) + "\n",
        encoding="utf-8",
    )

    av = _load_availability_uptime(str(tmp_path))
    assert list(av["inverter_id"]) == ["FROM-JSONL"]


def test_load_availability_uptime_missing_dir_returns_none(tmp_path):
    from pv_pipeline.m2a.soiling import _load_availability_uptime

    assert _load_availability_uptime("") is None
    assert _load_availability_uptime(str(tmp_path / "nope")) is None
    assert _load_availability_uptime(str(tmp_path)) is None  # dir kosong


def test_build_availability_mask_threshold_and_uppercase():
    from pv_pipeline.m2a.soiling import build_availability_mask

    av = pd.DataFrame({
        "date": [pd.Timestamp("2026-05-01")] * 2,
        "inverter_id": ["wb01-inv01", "WB01-INV02"],
        "uptime_pct": [80.0, 97.0],
    })
    mask = build_availability_mask(av, min_uptime_pct=95.0)
    assert set(mask) == {"WB01-INV01"}  # 97% >= ambang tidak di-mask
    assert mask["WB01-INV01"] == {pd.Timestamp("2026-05-01")}
    assert build_availability_mask(None) == {}
    assert build_availability_mask(pd.DataFrame()) == {}


class TestM2aSoilingAvailabilityMask:
    def test_full_mask_removes_all_days(
        self, synthetic_combined_df, soiling_cfg, mock_poa, tmp_path,
    ):
        """Semua inverter di-mask pada satu-satunya hari data -> n_days=0."""
        _write_m2e_findings_xlsx(tmp_path / "m2_findings_20260514.xlsx", [
            {"sub_module": "M2e_inverter", "inverter_id": inv, "value": 10.0}
            for inv in ["WB05-INV01", "WB05-INV02", "WB02-INV05"]
        ])
        cfg = dict(soiling_cfg)
        cfg["m2a_soiling"] = dict(cfg["m2a_soiling"])
        cfg["m2a_soiling"]["availability_dir"] = str(tmp_path)

        sm = M2aSoiling(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, cfg)
        assert findings[0].fault_type == "insufficient_data"
        assert findings[0].evidence["n_days"] == 0
        assert "AvailabilityMask" in sm.artifacts
        assert len(sm.artifacts["AvailabilityMask"]) == 3

    def test_partial_mask_scales_capacity_factor(
        self, synthetic_combined_df, soiling_cfg, mock_poa, tmp_path,
    ):
        """Satu inverter di-mask -> energinya hilang dari agregat DAN
        capacity_factor < 1 di hari itu (PR tidak bias)."""
        sm_ref = M2aSoiling(poa=mock_poa)
        sm_ref.run(synthetic_combined_df, soiling_cfg)
        e_ref = sm_ref.artifacts["PRDaily"]["energy_kwh"].iloc[0]

        _write_m2e_findings_xlsx(tmp_path / "m2_findings_20260514.xlsx", [
            {"sub_module": "M2e_inverter", "inverter_id": "WB05-INV01",
             "value": 10.0},
        ])
        cfg = dict(soiling_cfg)
        cfg["m2a_soiling"] = dict(cfg["m2a_soiling"])
        cfg["m2a_soiling"]["availability_dir"] = str(tmp_path)

        sm = M2aSoiling(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, cfg)
        prd = sm.artifacts["PRDaily"]
        assert prd["energy_kwh"].iloc[0] < e_ref
        assert 0.0 < prd["capacity_factor"].iloc[0] < 1.0
        assert findings[0].fault_type == "insufficient_data"
        assert sm._n_avail_masked_days == 1


# ============================================================================
# PRDaily artifact + last_pr_daily (bahan plotting sawtooth)
# ============================================================================


def test_run_emits_prdaily_artifact_and_last_pr_daily(
    synthetic_combined_df, soiling_cfg, mock_poa,
):
    """PRDaily harus ter-emit walau insufficient_data supaya deret PR bisa
    diplot dari xlsx tanpa run ulang."""
    sm = M2aSoiling(poa=mock_poa)
    sm.run(synthetic_combined_df, soiling_cfg)
    assert "PRDaily" in sm.artifacts
    prd = sm.artifacts["PRDaily"]
    assert {"date", "energy_kwh", "insolation_kwh_per_m2", "temp_factor",
            "capacity_factor", "pr"} <= set(prd.columns)
    assert len(prd) == 1  # fixture = 1 hari
    assert prd["energy_kwh"].iloc[0] > 0
    # Fixture sintetis menghasilkan PR di luar rentang fisik (difilter) --
    # last_pr_daily tetap terisi (Series, boleh kosong), bukan None.
    assert sm.last_pr_daily is not None


# ============================================================================
# build_monthly_soiling_loss (breakdown bulanan dari profil SR harian)
# ============================================================================


def test_build_monthly_soiling_loss_insolation_weighted():
    from pv_pipeline.m2a.soiling import (
        MONTHLY_SOILING_COLUMNS, build_monthly_soiling_loss,
    )

    idx = pd.date_range("2026-01-30", periods=4, freq="D")  # 2 Jan + 2 Feb
    profiles = pd.DataFrame(
        {0: [1.0, 0.9, 0.8, 0.8], 1: [1.0, 0.9, 0.8, 0.8]}, index=idx,
    )
    insol = pd.Series([5.0, 5.0, 4.0, 4.0], index=idx)
    energy = pd.Series([100.0, 90.0, 80.0, 80.0], index=idx)

    out = build_monthly_soiling_loss(profiles, insol, energy, 1500.0)
    assert list(out.columns) == MONTHLY_SOILING_COLUMNS
    assert list(out["month"]) == ["2026-01", "2026-02"]

    jan = out.iloc[0]
    assert jan["n_days"] == 2
    assert jan["sr_p50"] == pytest.approx((1.0 * 5 + 0.9 * 5) / 10)
    assert jan["p_loss_pct"] == pytest.approx(5.0)

    feb = out.iloc[1]
    assert feb["sr_p50"] == pytest.approx(0.8)
    # Energi hilang Feb: 2 hari x 80*(1/0.8 - 1) = 2 x 20 kWh.
    assert feb["energy_lost_kwh_est"] == pytest.approx(40.0)
    assert feb["loss_idr_est"] == pytest.approx(40.0 * 1500.0)


def test_build_monthly_soiling_loss_empty_inputs():
    from pv_pipeline.m2a.soiling import (
        MONTHLY_SOILING_COLUMNS, build_monthly_soiling_loss,
    )

    out = build_monthly_soiling_loss(None, pd.Series(dtype=float), None, 1500.0)
    assert list(out.columns) == MONTHLY_SOILING_COLUMNS
    assert out.empty


# ============================================================================
# build_cleaning_recommendation (heatmap string + ranking p_loss/uplift)
# ============================================================================


def test_build_cleaning_recommendation_ranks_dirty_string_first():
    from pv_pipeline.m2a.soiling import (
        RECOMMENDATION_COLUMNS, build_cleaning_recommendation,
    )

    days = pd.date_range("2026-04-01", periods=20, freq="D")
    insol = pd.Series(5.0, index=days)
    rows = []
    for d in days:
        for pv, e in [(1, 50.0), (2, 50.0), (3, 30.0)]:  # PV3 kotor parsial
            rows.append({"date": d, "Inverter_ID": "WB01-INV01",
                         "pv": pv, "energy_kwh": e})
    sd = pd.DataFrame(rows)
    per_inv = pd.DataFrame([{"inverter_id": "WB01-INV01", "p_loss_pct": 3.0}])
    dps = pd.DataFrame([
        {"inverter_id": "WB01-INV01", "pv": 3, "uplift_pct": 6.0},
    ])

    out = build_cleaning_recommendation(
        sd, insol, per_inv, dps, window_days=30, min_days=10,
    )
    assert list(out.columns) == RECOMMENDATION_COLUMNS
    top = out.iloc[0]
    assert top["pv"] == 3 and top["rank"] == 1
    # deficit = (50-30)/50 = 40% terhadap median sibling.
    assert top["deficit_vs_siblings_pct"] == pytest.approx(40.0)
    assert top["inverter_p_loss_pct"] == pytest.approx(3.0)
    assert top["hist_uplift_pct"] == pytest.approx(6.0)
    assert top["score"] == pytest.approx(43.0)
    # Sibling normal: deficit ~ negatif kecil, tidak diprioritaskan.
    assert (out[out["pv"] != 3]["rank"] > 1).all()


def test_build_cleaning_recommendation_min_days_and_empty():
    from pv_pipeline.m2a.soiling import (
        RECOMMENDATION_COLUMNS, build_cleaning_recommendation,
    )

    days = pd.date_range("2026-04-01", periods=3, freq="D")  # < min_days
    sd = pd.DataFrame([
        {"date": d, "Inverter_ID": "WB01-INV01", "pv": 1, "energy_kwh": 50.0}
        for d in days
    ])
    out = build_cleaning_recommendation(
        sd, pd.Series(5.0, index=days), window_days=30, min_days=10,
    )
    assert out.empty

    out2 = build_cleaning_recommendation(
        pd.DataFrame(), pd.Series(dtype=float),
    )
    assert list(out2.columns) == RECOMMENDATION_COLUMNS
    assert out2.empty
