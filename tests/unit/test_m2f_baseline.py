"""Tes baseline M2f: konversi daya->energi dan kalibrasi gain bifacial."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.baseline import (
    DEFAULT_FREQ_HOURS,
    calibrate_bifacial_gain,
    compute_actual_energy_kwh,
    compute_expected_energy_kwh,
)
from pv_pipeline.panel_spec import PanelSpec


@pytest.fixture
def spec():
    return PanelSpec.from_yaml("config/panel_spec.yaml")


def test_expected_energy_at_stc_matches_nameplate(spec):
    # Pada STC (1000 W/m2, 25 C), 26 modul x 625 W = 16,25 kW.
    # Satu interval 5 menit -> 16,25 * (5/60) kWh.
    idx = pd.date_range("2026-05-13 12:00", periods=1, freq="5min")
    poa = pd.Series([1000.0], index=idx)
    tcell = pd.Series([25.0], index=idx)
    out = compute_expected_energy_kwh(poa, tcell, spec, "WB03")
    assert out.iloc[0] == pytest.approx(16.25 * DEFAULT_FREQ_HOURS)


def test_expected_energy_uses_per_wb_module_count(spec):
    # WB01 = 24 modul, WB03 = 26 modul. Rasio harus 24/26.
    idx = pd.date_range("2026-05-13 12:00", periods=1, freq="5min")
    poa = pd.Series([1000.0], index=idx)
    tcell = pd.Series([25.0], index=idx)
    wb01 = compute_expected_energy_kwh(poa, tcell, spec, "WB01").iloc[0]
    wb03 = compute_expected_energy_kwh(poa, tcell, spec, "WB03").iloc[0]
    assert wb01 / wb03 == pytest.approx(24.0 / 26.0)


def test_bifacial_gain_scales_expected_linearly(spec):
    idx = pd.date_range("2026-05-13 12:00", periods=1, freq="5min")
    poa = pd.Series([800.0], index=idx)
    tcell = pd.Series([45.0], index=idx)
    base = compute_expected_energy_kwh(poa, tcell, spec, "WB03", bifacial_gain=1.0)
    lifted = compute_expected_energy_kwh(poa, tcell, spec, "WB03", bifacial_gain=1.05)
    assert lifted.iloc[0] == pytest.approx(base.iloc[0] * 1.05)


def test_actual_energy_is_riemann_sum():
    idx = pd.date_range("2026-05-13 12:00", periods=3, freq="5min")
    power = pd.Series([12.0, 12.0, 6.0], index=idx)
    out = compute_actual_energy_kwh(power)
    assert out.sum() == pytest.approx(30.0 * DEFAULT_FREQ_HOURS)


def test_actual_energy_treats_nan_as_zero():
    # WHY: sampel hilang berarti energi tidak tercatat. Selisihnya harus
    # muncul sebagai rugi yang diatribusikan, bukan disembunyikan.
    idx = pd.date_range("2026-05-13 12:00", periods=3, freq="5min")
    power = pd.Series([12.0, np.nan, 6.0], index=idx)
    out = compute_actual_energy_kwh(power)
    assert not out.isna().any(), "NaN tidak boleh muncul di output; harus diisi 0.0"
    assert out.iloc[1] == pytest.approx(0.0), "NaN pada posisi tengah harus menjadi 0.0"
    assert out.sum() == pytest.approx(18.0 * DEFAULT_FREQ_HOURS)


def test_calibrate_bifacial_gain_is_median_ratio():
    # WHY: E_expected memakai POA depan saja, sedangkan modulnya bifacial.
    # Tanpa kalibrasi, string sehat tampak "rugi" negatif dan seluruh
    # waterfall bias.
    expected = pd.Series([100.0, 100.0, 100.0], index=["a", "b", "c"])
    actual = pd.Series([104.0, 106.0, 108.0], index=["a", "b", "c"])
    assert calibrate_bifacial_gain(expected, actual) == pytest.approx(1.06)


def test_calibrate_bifacial_gain_ignores_zero_expected():
    expected = pd.Series([100.0, 0.0, 100.0], index=["a", "b", "c"])
    actual = pd.Series([105.0, 50.0, 105.0], index=["a", "b", "c"])
    assert calibrate_bifacial_gain(expected, actual, min_strings=2) == pytest.approx(1.05)


def test_calibrate_bifacial_gain_refuses_thin_sample():
    # WHY: gain dari 1-2 string bukan kalibrasi, itu kebetulan.
    expected = pd.Series([100.0, 100.0], index=["a", "b"])
    actual = pd.Series([105.0, 105.0], index=["a", "b"])
    with pytest.raises(ValueError, match="minimal 3 string"):
        calibrate_bifacial_gain(expected, actual, min_strings=3)


def test_expected_energy_fillna_guards_against_nan_poa_tcell(spec):
    # WHY: NaN POA atau Tcell tidak boleh menghasilkan NaN di output.
    # LossLedger.\_\_init\_\_ menolak NaN dalam e_expected; guard harus
    # mengganti NaN menjadi 0.0 agar ledger tidak rusak.
    idx = pd.date_range("2026-05-13 12:00", periods=3, freq="5min")
    poa = pd.Series([800.0, np.nan, 600.0], index=idx)
    tcell = pd.Series([40.0, 40.0, np.nan], index=idx)
    out_poa = compute_expected_energy_kwh(poa, tcell, spec, "WB03")
    out_tcell = compute_expected_energy_kwh(poa, tcell, spec, "WB03")
    assert not out_poa.isna().any(), "NaN POA harus diisi 0.0, bukan dibiarkan NaN"
    assert not out_tcell.isna().any(), "NaN Tcell harus diisi 0.0, bukan dibiarkan NaN"
    assert out_poa.iloc[1] == pytest.approx(0.0)
    assert out_tcell.iloc[2] == pytest.approx(0.0)


def test_expected_energy_clips_negative_poa(spec):
    # WHY: POA negatif tidak mungkin fisik; harus di-clip ke 0.0 agar
    # tidak menghasilkan energi negatif di ledger.
    idx = pd.date_range("2026-05-13 12:00", periods=2, freq="5min")
    poa = pd.Series([-100.0, 800.0], index=idx)
    tcell = pd.Series([30.0, 40.0], index=idx)
    out = compute_expected_energy_kwh(poa, tcell, spec, "WB03")
    assert not out.isna().any(), "output tidak boleh ada NaN"
    assert (out >= 0.0).all(), "semua energi harus >= 0.0; tidak boleh negatif"
    assert out.iloc[0] == pytest.approx(0.0), "POA negatif harus di-clip menjadi 0.0"
