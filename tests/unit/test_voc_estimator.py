"""Test pv_pipeline.voc_estimator: V@I≈0 median estimator."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.voc_estimator import estimate_voc_at_low_current, estimate_voc_per_string


def _build_sunrise_sunset_profile(n=145):
    """Synthesize V/I profile: sunrise (low I, high V) → noon (high I, low V) → sunset."""
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 18:00", freq="5min")[:n]
    hours = np.linspace(0, 12, n)
    sun = np.sin(np.pi * hours / 12) ** 2
    I = 10.0 * sun
    V = 600.0 + 50.0 * np.exp(-3 * sun)  # ~650V at sunrise/sunset, ~600V at noon
    return pd.Series(V, index=t), pd.Series(I, index=t)


def test_voc_estimate_normal():
    """Sunrise/sunset V (I<0.5A) median harus ~650V."""
    V, I = _build_sunrise_sunset_profile()
    voc = estimate_voc_at_low_current(V, I)
    assert 640.0 < voc < 660.0, f"Voc={voc} out of expected range 640-660"


def test_voc_too_few_samples_returns_nan():
    """min_samples=3 default; jika kurang -> NaN."""
    voc = estimate_voc_at_low_current(pd.Series([100.0]), pd.Series([10.0]))
    assert pd.isna(voc)


def test_voc_no_low_current_samples_returns_nan():
    """Kalau semua I > threshold (tidak ada sunrise/sunset), return NaN."""
    V = pd.Series([600.0] * 100)
    I = pd.Series([10.0] * 100)  # All above i_threshold=0.5
    voc = estimate_voc_at_low_current(V, I)
    assert pd.isna(voc)


def test_voc_filters_low_voltage_artifacts():
    """V < min_voc_v=10 dianggap no-data, di-buang."""
    V = pd.Series([0.0] * 10 + [600.0] * 10)
    I = pd.Series([0.1] * 20)  # All low current
    voc = estimate_voc_at_low_current(V, I, min_voc_v=10.0)
    # Hanya 10 sample valid (V=600), bukan 20. Median masih 600.
    assert voc == pytest.approx(600.0)


def test_voc_per_string_multiple_columns():
    """estimate_voc_per_string handles multiple PV channels + missing channels."""
    V, I = _build_sunrise_sunset_profile()
    df = pd.DataFrame({
        "PV1 input voltage(V)": V.values,
        "PV1 input current(A)": I.values,
        "PV5 input voltage(V)": (V.values + 5),
        "PV5 input current(A)": I.values,
        # PV10 sengaja missing
    })
    out = estimate_voc_per_string(df)
    assert 1 in out
    assert 5 in out
    assert 10 not in out
    assert abs(out[1] - 650) < 10
    assert abs(out[5] - 655) < 10
