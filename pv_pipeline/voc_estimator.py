"""Voc_actual estimator: estimate open-circuit voltage dari V saat I -> 0.

Spec 4.2.3 (M2b High-R + Ground Fault rules) butuh:
    voc_ratio = voc_actual / voc_string_nominal

Voc_string_nominal datang dari datasheet (PanelSpec.voc_string_nominal).
Voc_actual perlu di-estimate dari measurement: ambil V saat I mendekati 0,
yaitu sunrise/sunset window (panel terbuka rangkaian secara natural).

Strategi:
- Filter samples dengan ``I < i_threshold_a`` (default 0.5 A).
- Optional: filter V > min_voc_v supaya tidak ambil zero-volt false negatives.
- Return median V dari samples valid; NaN bila samples < min_samples.

Convention:
- Input V_series, I_series adalah ``pd.Series`` per (Inverter, PV string).
- I dalam Ampere, V dalam Volt (per kolom Huawei xlsx ``PVx input current(A)``
  dan ``PVx input voltage(V)``).
- Output: float Voc_actual (Volt) atau ``float("nan")``.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


DEFAULT_I_THRESHOLD_A: float = 0.5
DEFAULT_MIN_VOC_V: float = 10.0
DEFAULT_MIN_SAMPLES: int = 3


def estimate_voc_at_low_current(
    V_series: pd.Series,
    I_series: pd.Series,
    *,
    i_threshold_a: float = DEFAULT_I_THRESHOLD_A,
    min_voc_v: float = DEFAULT_MIN_VOC_V,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> float:
    """Voc_actual = median V saat I < i_threshold AND V > min_voc.

    Parameters
    ----------
    V_series, I_series : pd.Series
        Sejajar by index (timestamps). Boleh punya NaN gaps.
    i_threshold_a : float, default 0.5
        Threshold arus untuk dianggap "open circuit" (sunrise/sunset).
    min_voc_v : float, default 10
        Buang sample dengan V terlalu rendah (kemungkinan no-data zero readings).
    min_samples : int, default 3
        Minimum samples valid; kalau kurang, return NaN.

    Returns
    -------
    float
        Voc_actual estimate dalam Volt, atau NaN.
    """
    if V_series is None or I_series is None:
        return float("nan")
    if len(V_series) == 0 or len(I_series) == 0:
        return float("nan")

    # Align by index (handle case kalau Series dari DataFrame berbeda).
    V = pd.to_numeric(V_series, errors="coerce")
    I = pd.to_numeric(I_series, errors="coerce")
    aligned = pd.DataFrame({"V": V, "I": I}).dropna()

    if aligned.empty:
        return float("nan")

    mask = (aligned["I"].abs() < i_threshold_a) & (aligned["V"] > min_voc_v)
    samples = aligned.loc[mask, "V"]

    if len(samples) < min_samples:
        return float("nan")
    return float(samples.median())


def estimate_voc_per_string(
    df_inverter: pd.DataFrame,
    pv_max: int = 28,
    *,
    i_threshold_a: float = DEFAULT_I_THRESHOLD_A,
    min_voc_v: float = DEFAULT_MIN_VOC_V,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict:
    """Per-PV-string Voc_actual estimate untuk satu inverter.

    Iterasi PV1..PV{pv_max}, panggil ``estimate_voc_at_low_current``,
    kembalikan dict ``{pv_n: voc_actual_v}``.

    Parameters
    ----------
    df_inverter : pd.DataFrame
        DataFrame untuk satu inverter (sudah di-groupby Inverter_ID).
        Expected kolom: ``PV{n} input voltage(V)`` dan ``PV{n} input current(A)``.
    pv_max : int, default 28
        Inverter Huawei max PV channels (default 28).
    """
    out: dict = {}
    for pv_n in range(1, pv_max + 1):
        v_col = f"PV{pv_n} input voltage(V)"
        i_col = f"PV{pv_n} input current(A)"
        if v_col not in df_inverter.columns or i_col not in df_inverter.columns:
            continue
        voc = estimate_voc_at_low_current(
            df_inverter[v_col],
            df_inverter[i_col],
            i_threshold_a=i_threshold_a,
            min_voc_v=min_voc_v,
            min_samples=min_samples,
        )
        out[pv_n] = voc
    return out


if __name__ == "__main__":
    # Synthetic smoke test.
    import numpy as np
    rng = np.random.default_rng(42)

    # Synthesize 1-day sunrise-to-sunset profile @ 5-min interval (145 samples).
    n = 145
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 18:00", freq="5min")[:n]
    # Sun position proxy: bell curve peaks at noon
    hours = np.linspace(0, 12, n)
    sun = np.sin(np.pi * hours / 12) ** 2  # 0 at sunrise/sunset, 1 at noon
    I = 10.0 * sun + rng.normal(0, 0.1, n)      # peak 10A
    V = 600.0 + 50.0 * np.exp(-3 * sun)         # 650V at sunrise/sunset, 600V at noon
    V_series = pd.Series(V, index=t)
    I_series = pd.Series(I, index=t)

    voc = estimate_voc_at_low_current(V_series, I_series)
    print(f"[voc_estimator] synthetic: V@sunrise/sunset (I<0.5A) median = {voc:.1f} V (expected ~650)")
    assert 640 < voc < 660, f"voc out of expected range: {voc}"

    # Test min_samples=NaN path
    bad = estimate_voc_at_low_current(pd.Series([1.0]), pd.Series([10.0]))
    assert pd.isna(bad), "should be NaN with min_samples=3"

    # estimate_voc_per_string smoke
    df = pd.DataFrame({
        "PV1 input voltage(V)": V_series.values,
        "PV1 input current(A)": I_series.values,
        "PV5 input voltage(V)": (V_series.values + 5),
        "PV5 input current(A)": I_series.values,
        # PV10 missing (skip)
    })
    out = estimate_voc_per_string(df)
    print(f"[voc_estimator] per-string: {out}")
    assert 1 in out and 5 in out and 10 not in out
    assert abs(out[1] - 650) < 10
    assert abs(out[5] - 655) < 10
    print("[voc_estimator] smoke OK")
