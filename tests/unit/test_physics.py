"""Test pv_pipeline.physics: Pmax/P_expected/Kt helpers (Fase 2 Wave 1)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.physics import (
    DELTA_P_MIN_EXPECTED_W,
    G_STC_WM2,
    KT_MIN_POA_WM2,
    PR_MIN_POA_KWH_PER_M2,
    T_STC_C,
    compute_active_power_integration_kwh,
    compute_delta_power,
    compute_kt,
    compute_p_expected_per_string,
    compute_pmax_per_module,
    compute_pr,
)


# ---------- Mock PanelSpec (struct-only; tidak butuh yaml) ----------


class _MockTempCoef:
    def __init__(self, pmax_pct_per_c: float):
        self.pmax_pct_per_c = pmax_pct_per_c


class _MockStcParams:
    def __init__(self, pmax_w: float):
        self.pmax_w = pmax_w


class _MockPanelSpec:
    """Jinko JKM625N-like spec (Pmax_STC=625W, tc_pmax=-0.30 %/C)."""

    def __init__(self, pmax_stc_w: float = 625.0, tc_pmax_pct_per_c: float = -0.30):
        self.stc = _MockStcParams(pmax_stc_w)
        self.temp_coef = _MockTempCoef(tc_pmax_pct_per_c)
        self._strings_per_wb = {"WB01": 24, "WB02": 24}  # rest -> 26

    def modules_per_string(self, wb_id: str) -> int:
        return self._strings_per_wb.get(str(wb_id).upper(), 26)


@pytest.fixture
def panel():
    return _MockPanelSpec()


# ---------- Module-level constants ----------


def test_stc_constants():
    """STC reference per IEC 61215."""
    assert G_STC_WM2 == 1000.0
    assert T_STC_C == 25.0


def test_kt_min_poa_default():
    """Kt default min POA threshold ~1 W/m^2 (avoid div-near-zero)."""
    assert KT_MIN_POA_WM2 == 1.0


# ---------- compute_pmax_per_module ----------


def test_pmax_at_stc_returns_pmax_stc(panel):
    """P(POA=1000, Tcell=25) == Pmax_STC (definisi)."""
    p = compute_pmax_per_module(1000.0, 25.0, panel)
    assert p == pytest.approx(625.0, abs=1e-9)


def test_pmax_scales_linearly_with_poa(panel):
    """POA 500 @ STC temp -> P = 0.5 * Pmax_STC."""
    p = compute_pmax_per_module(500.0, 25.0, panel)
    assert p == pytest.approx(312.5, abs=1e-9)


def test_pmax_drops_with_hot_temp(panel):
    """Tcell=55 C @ STC POA -> P = Pmax_STC * (1 + (-0.003)*30) = 625 * 0.91 = 568.75."""
    p = compute_pmax_per_module(1000.0, 55.0, panel)
    assert p == pytest.approx(625.0 * (1 + (-0.30 / 100.0) * 30.0), abs=1e-9)
    assert p < 625.0


def test_pmax_rises_with_cold_temp(panel):
    """Tcell=15 C @ STC POA -> P > Pmax_STC (cold morning bonus)."""
    p = compute_pmax_per_module(1000.0, 15.0, panel)
    assert p > 625.0
    assert p == pytest.approx(625.0 * (1 + (-0.30 / 100.0) * (-10.0)), abs=1e-9)


def test_pmax_zero_poa_returns_zero(panel):
    """Night -> P = 0 regardless of Tcell."""
    assert compute_pmax_per_module(0.0, 25.0, panel) == pytest.approx(0.0)
    assert compute_pmax_per_module(0.0, 50.0, panel) == pytest.approx(0.0)


def test_pmax_accepts_numpy_array(panel):
    """Array input -> array output, element-wise."""
    poa = np.array([500.0, 1000.0])
    tcell = np.array([25.0, 55.0])
    p = compute_pmax_per_module(poa, tcell, panel)
    assert isinstance(p, np.ndarray)
    assert p.shape == (2,)
    assert p[0] == pytest.approx(312.5)
    assert p[1] == pytest.approx(625.0 * 0.91, abs=1e-3)


def test_pmax_accepts_pandas_series(panel):
    """Series input -> Series output, index preserved."""
    idx = pd.date_range("2026-05-14 10:00", periods=3, freq="1h")
    poa = pd.Series([500.0, 1000.0, 750.0], index=idx)
    tcell = pd.Series([25.0, 25.0, 30.0], index=idx)
    p = compute_pmax_per_module(poa, tcell, panel)
    assert isinstance(p, pd.Series)
    assert len(p) == 3
    assert p.index.equals(idx)
    assert p.iloc[0] == pytest.approx(312.5)
    assert p.iloc[1] == pytest.approx(625.0)


# ---------- compute_p_expected_per_string ----------


def test_p_expected_per_string_wb01_uses_24_modules(panel):
    """WB01 has 24 modules/string -> P_string = P_module * 24."""
    p_str = compute_p_expected_per_string(1000.0, 25.0, panel, "WB01")
    assert p_str == pytest.approx(625.0 * 24, abs=1e-9)


def test_p_expected_per_string_wb05_uses_26_modules(panel):
    """WB05 (not in override map) -> default 26 modules/string."""
    p_str = compute_p_expected_per_string(1000.0, 25.0, panel, "WB05")
    assert p_str == pytest.approx(625.0 * 26, abs=1e-9)


def test_p_expected_per_string_case_insensitive_wb(panel):
    """WB lookup case-insensitive."""
    p_lower = compute_p_expected_per_string(1000.0, 25.0, panel, "wb01")
    p_upper = compute_p_expected_per_string(1000.0, 25.0, panel, "WB01")
    assert p_lower == pytest.approx(p_upper)


def test_p_expected_per_string_series_input(panel):
    """Series input -> Series output preserving index."""
    idx = pd.date_range("2026-05-14 12:00", periods=2, freq="1h")
    poa = pd.Series([1000.0, 800.0], index=idx)
    tcell = pd.Series([25.0, 30.0], index=idx)
    p_str = compute_p_expected_per_string(poa, tcell, panel, "WB01")
    assert isinstance(p_str, pd.Series)
    assert len(p_str) == 2
    assert p_str.iloc[0] == pytest.approx(625.0 * 24)


# ---------- compute_kt ----------


def test_kt_scalar_clear_sky():
    """Kt(measured=1000, clearsky=1000) == 1.0."""
    kt = compute_kt(1000.0, 1000.0)
    assert kt == pytest.approx(1.0)
    assert isinstance(kt, float)


def test_kt_scalar_cloudy():
    """Kt(measured=500, clearsky=1000) == 0.5 (50% cloud cover proxy)."""
    kt = compute_kt(500.0, 1000.0)
    assert kt == pytest.approx(0.5)


def test_kt_scalar_below_min_poa_returns_nan():
    """Clearsky 0 (night) -> Kt NaN (no div-near-zero)."""
    kt = compute_kt(0.0, 0.0)
    assert np.isnan(kt)


def test_kt_series_returns_series_with_name():
    """Series in -> Series out, name='kt'."""
    measured = pd.Series([800.0, 950.0, 100.0, 0.0])
    clearsky = pd.Series([1000.0, 1000.0, 500.0, 0.0])
    kt = compute_kt(measured, clearsky)
    assert isinstance(kt, pd.Series)
    assert kt.name == "kt"
    assert len(kt) == 4
    assert kt.iloc[0] == pytest.approx(0.8)
    assert kt.iloc[1] == pytest.approx(0.95)
    assert kt.iloc[2] == pytest.approx(0.2)
    assert np.isnan(kt.iloc[3])


def test_kt_ndarray_input_returns_ndarray():
    """ndarray in -> ndarray out."""
    measured = np.array([800.0, 0.0])
    clearsky = np.array([1000.0, 0.0])
    kt = compute_kt(measured, clearsky)
    assert isinstance(kt, np.ndarray)
    assert kt[0] == pytest.approx(0.8)
    assert np.isnan(kt[1])


def test_kt_custom_min_poa_threshold():
    """min_poa_wm2 override: clearsky 50 < min=100 -> NaN."""
    kt = compute_kt(40.0, 50.0, min_poa_wm2=100.0)
    assert np.isnan(kt)


def test_kt_series_preserves_index():
    """Series Kt output index matches input index."""
    idx = pd.date_range("2026-05-14 12:00", periods=3, freq="15min")
    measured = pd.Series([900.0, 950.0, 970.0], index=idx)
    clearsky = pd.Series([1000.0, 1000.0, 1000.0], index=idx)
    kt = compute_kt(measured, clearsky)
    assert kt.index.equals(idx)


# ---------- compute_delta_power (Wave 2) ----------


def test_delta_p_min_expected_default():
    """DeltaP default min expected ~ 1 W (avoid div-near-zero)."""
    assert DELTA_P_MIN_EXPECTED_W == 1.0


def test_delta_power_zero_when_actual_equals_expected():
    """P_actual == P_expected -> DeltaP_ratio = 0."""
    d = compute_delta_power(15000.0, 15000.0)
    assert d == pytest.approx(0.0)
    assert isinstance(d, float)


def test_delta_power_negative_under_performing():
    """P_actual < P_expected -> DeltaP_ratio < 0 (soiling/shading proxy)."""
    d = compute_delta_power(12000.0, 15000.0)
    assert d == pytest.approx((12000.0 / 15000.0) - 1.0)  # = -0.20
    assert d < 0


def test_delta_power_positive_over_performing():
    """P_actual > P_expected -> DeltaP_ratio > 0 (cal drift / cloud edge)."""
    d = compute_delta_power(16000.0, 15000.0)
    assert d == pytest.approx((16000.0 / 15000.0) - 1.0)  # = +0.067
    assert d > 0


def test_delta_power_nan_when_expected_below_min():
    """P_expected < min (night) -> NaN."""
    d = compute_delta_power(0.0, 0.0)
    assert np.isnan(d)


def test_delta_power_series_returns_series_with_name():
    """Series in -> Series out, name='delta_power_ratio'."""
    actual = pd.Series([14000.0, 15000.0, 16000.0, 0.0])
    expected = pd.Series([15000.0, 15000.0, 15000.0, 0.0])
    d = compute_delta_power(actual, expected)
    assert isinstance(d, pd.Series)
    assert d.name == "delta_power_ratio"
    assert d.iloc[0] == pytest.approx(-1.0 / 15)
    assert d.iloc[1] == pytest.approx(0.0)
    assert d.iloc[2] == pytest.approx(1.0 / 15)
    assert np.isnan(d.iloc[3])


def test_delta_power_ndarray_input_returns_ndarray():
    """ndarray in -> ndarray out."""
    actual = np.array([14000.0, 0.0])
    expected = np.array([15000.0, 0.0])
    d = compute_delta_power(actual, expected)
    assert isinstance(d, np.ndarray)
    assert d[0] == pytest.approx(-1.0 / 15)
    assert np.isnan(d[1])


# ---------- compute_active_power_integration_kwh (Wave 7) ----------


def test_power_integration_5min_uniform():
    """P=10 kW constant for 12 samples @ 5min = 1 hour total = 10 kWh."""
    idx = pd.date_range("2026-05-14 10:00", periods=12, freq="5min")
    p = pd.Series([10.0] * 12, index=idx)
    e = compute_active_power_integration_kwh(p)
    assert e == pytest.approx(10.0, abs=0.01)


def test_power_integration_explicit_freq_overrides_index():
    """freq_hours kwarg overrides auto-detection."""
    p = pd.Series([100.0, 100.0, 100.0])  # no DatetimeIndex
    e = compute_active_power_integration_kwh(p, freq_hours=1.0)
    assert e == pytest.approx(300.0)


def test_power_integration_skips_nan():
    """NaN samples not counted."""
    idx = pd.date_range("2026-05-14 10:00", periods=6, freq="5min")
    p = pd.Series([10.0, np.nan, 10.0, np.nan, 10.0, 10.0], index=idx)
    e = compute_active_power_integration_kwh(p)
    # 4 valid samples * 10 kW * 5/60 h = 4 * 0.833 = 3.33 kWh
    assert e == pytest.approx(4 * 10.0 * (5.0 / 60.0), abs=0.01)


def test_power_integration_empty_series_returns_zero():
    e = compute_active_power_integration_kwh(pd.Series([], dtype="float64"))
    assert e == 0.0


def test_power_integration_non_datetimeindex_no_freq_raises():
    """Plain index + no freq_hours -> ValueError."""
    with pytest.raises(ValueError, match="freq_hours"):
        compute_active_power_integration_kwh(pd.Series([1.0, 2.0]))


def test_power_integration_raises_on_non_series():
    with pytest.raises(TypeError, match="pd.Series"):
        compute_active_power_integration_kwh([1.0, 2.0])  # type: ignore[arg-type]


# ---------- compute_pr (Wave 7) ----------


def test_pr_default_min_poa_constant():
    """PR default min POA ~ 0.01 kWh/m^2."""
    assert PR_MIN_POA_KWH_PER_M2 == 0.01


def test_pr_textbook_value():
    """E_actual=400000 kWh, POA=5.6 kWh/m^2, cap=71500 kWp -> PR ~1.0."""
    # PR = 400_000 / (5.6 * 71_500) = 400_000 / 400_400 = 0.999
    pr = compute_pr(400_000.0, 5.6, 71500.0)
    assert isinstance(pr, float)
    assert pr == pytest.approx(400_000.0 / (5.6 * 71500.0), abs=1e-6)


def test_pr_underperforming():
    """Real-world tropical PR ~ 0.78 (curtailment + soiling)."""
    pr = compute_pr(312_000.0, 5.6, 71500.0)
    assert pr == pytest.approx(0.78, abs=0.01)


def test_pr_nan_when_poa_below_min():
    """POA = 0 (night) -> NaN."""
    pr = compute_pr(0.0, 0.0, 71500.0)
    assert np.isnan(pr)


def test_pr_series_returns_series_with_name():
    """Series in -> Series out, name='performance_ratio'."""
    e = pd.Series([400_000.0, 312_000.0, 0.0])
    poa = pd.Series([5.6, 5.6, 0.0])
    pr = compute_pr(e, poa, 71500.0)
    assert isinstance(pr, pd.Series)
    assert pr.name == "performance_ratio"
    assert pr.iloc[0] == pytest.approx(400_000.0 / (5.6 * 71500.0), abs=1e-6)
    assert pr.iloc[1] == pytest.approx(0.78, abs=0.01)
    assert np.isnan(pr.iloc[2])


def test_pr_ndarray_input_returns_ndarray():
    e = np.array([400_000.0, 0.0])
    poa = np.array([5.6, 0.0])
    pr = compute_pr(e, poa, 71500.0)
    assert isinstance(pr, np.ndarray)
    assert pr[0] == pytest.approx(400_000.0 / (5.6 * 71500.0), abs=1e-6)
    assert np.isnan(pr[1])


def test_pr_custom_min_poa_threshold():
    """Override min_poa_kwh_per_m2."""
    pr = compute_pr(100.0, 0.05, 71500.0, min_poa_kwh_per_m2=0.10)
    assert np.isnan(pr)
