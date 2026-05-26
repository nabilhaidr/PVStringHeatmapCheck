"""Time-series preprocessing helpers untuk PV string V/I (Fase 2 Wave 3).

Sumber: ``M2_PV_Performance_Master_Context.docx`` -- Physics Baseline preprocessing.
Backend: ``pvanalytics.quality.outliers.hampel`` (Hampel filter, MAD-based).

Functions
---------
- :func:`hampel_outlier_mask` -- Boolean mask (True = outlier) per Series.
- :func:`clean_with_hampel`  -- Replace outliers dengan ``replace_with`` (default NaN).
- :func:`clean_pv_string_columns` -- Convenience: apply to all ``PV{n} input X(...)``
                                    kolom di combined_df.

Design notes
------------
- Default ``window=15`` (di-tuned untuk PLTS-IKN 5-min sampling = ~75 min lookback).
  Cukup besar untuk smooth out passing-cloud noise, cukup kecil supaya sunrise/sunset
  ramp tidak ke-flag sebagai outlier.
- Default ``max_deviation=3.0`` (3-sigma MAD) -- konservatif, low false-positive.
- Tidak mengubah detector behavior. Wiring ke detector di-defer ke follow-up commit
  (perlu measure recall/precision delta dulu).
"""
from __future__ import annotations

import warnings
from typing import Dict, Optional

import numpy as np
import pandas as pd


DEFAULT_HAMPEL_WINDOW: int = 15
DEFAULT_HAMPEL_MAX_DEVIATION: float = 3.0


def _ensure_pvanalytics() -> None:
    """Pastikan pvanalytics tersedia (auto-install kalau belum).

    Mirror pattern ``_ensure_pvlib`` di ``pvlib_estimator.py``.
    """
    try:
        import pvanalytics  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: pvanalytics")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pvanalytics"])


def hampel_outlier_mask(
    series: pd.Series,
    *,
    window: int = DEFAULT_HAMPEL_WINDOW,
    max_deviation: float = DEFAULT_HAMPEL_MAX_DEVIATION,
) -> pd.Series:
    """Hampel outlier mask (True = outlier) untuk satu time-series.

    Wraps :func:`pvanalytics.quality.outliers.hampel`. Hampel = rolling-window
    MAD-based outlier detection, robust terhadap gradual trend (sunrise/sunset
    ramp tidak ke-flag).

    Parameters
    ----------
    series : pd.Series
        Time-indexed V atau I per PV string.
    window : int, default 15
        Rolling window size (samples). Di-tuned untuk 5-min PLTS-IKN data.
    max_deviation : float, default 3.0
        Threshold (in MAD-sigma). |value - rolling_median| / rolling_mad >
        threshold -> outlier.

    Returns
    -------
    pd.Series
        Boolean mask aligned ke input index. NaN positions di input -> False
        di output (pvanalytics convention).
    """
    if not isinstance(series, pd.Series):
        raise TypeError(f"hampel_outlier_mask expects pd.Series, got {type(series).__name__}")
    if series.empty:
        return pd.Series([], index=series.index, dtype=bool, name="hampel_outlier")

    _ensure_pvanalytics()
    from pvanalytics.quality.outliers import hampel

    mask = hampel(series, window=window, max_deviation=max_deviation)
    mask = pd.Series(mask.values, index=series.index, dtype=bool, name="hampel_outlier")
    return mask


def clean_with_hampel(
    series: pd.Series,
    *,
    window: int = DEFAULT_HAMPEL_WINDOW,
    max_deviation: float = DEFAULT_HAMPEL_MAX_DEVIATION,
    replace_with=np.nan,
) -> pd.Series:
    """Return copy of ``series`` dengan outlier-positions di-replace.

    Parameters
    ----------
    series : pd.Series
    window, max_deviation : sama seperti :func:`hampel_outlier_mask`.
    replace_with : scalar, default NaN
        Nilai pengganti untuk outlier.

    Returns
    -------
    pd.Series
        Same index/name sebagai input, outlier positions -> ``replace_with``.
    """
    mask = hampel_outlier_mask(series, window=window, max_deviation=max_deviation)
    cleaned = series.copy()
    cleaned[mask.fillna(False)] = replace_with
    return cleaned


def apply_hampel_to_pv_dataframe(
    df: pd.DataFrame,
    *,
    pv_max: int = 28,
    window: int = DEFAULT_HAMPEL_WINDOW,
    max_deviation: float = DEFAULT_HAMPEL_MAX_DEVIATION,
    columns: Optional[list] = None,
):
    """Wave 9: copy df, replace PV V/I columns dengan Hampel-cleaned versions.

    Detector wire: dipanggil di run() saat ``preprocessing.enabled=True`` untuk
    pre-screen outlier sensor glitch sebelum mask logic.

    Parameters
    ----------
    df : pd.DataFrame
        Combined_df (atau per-inverter group). Tidak di-mutate in-place.
    pv_max : int, default 28
    window, max_deviation : pass-through ke :func:`clean_with_hampel`.
    columns : list[str], optional
        Override list kolom (skip auto-discovery).

    Returns
    -------
    (cleaned_df, audit_records) : (pd.DataFrame, List[dict])
        cleaned_df: copy dari df dengan V/I cols replaced.
        audit_records: list of dict per column dengan ``{column, n_outliers,
        total_samples, pct_outliers}``. Empty kalau no col found.
    """
    if columns is None:
        columns = []
        for n in range(1, pv_max + 1):
            for suffix in ("input voltage(V)", "input current(A)"):
                col = f"PV{n} {suffix}"
                if col in df.columns:
                    columns.append(col)

    cleaned = df.copy()
    audit = []
    for col in columns:
        if col not in cleaned.columns:
            continue
        orig = pd.to_numeric(cleaned[col], errors="coerce")
        if orig.empty or orig.notna().sum() < 2:
            continue
        mask = hampel_outlier_mask(orig, window=window, max_deviation=max_deviation)
        n_out = int(mask.fillna(False).sum())
        total = int(orig.notna().sum())
        if n_out > 0:
            cleaned[col] = orig.where(~mask.fillna(False), np.nan)
        audit.append({
            "column": col,
            "n_outliers": n_out,
            "total_samples": total,
            "pct_outliers": (n_out / total * 100.0) if total > 0 else 0.0,
        })
    return cleaned, audit


def clean_pv_string_columns(
    df: pd.DataFrame,
    *,
    pv_max: int = 28,
    window: int = DEFAULT_HAMPEL_WINDOW,
    max_deviation: float = DEFAULT_HAMPEL_MAX_DEVIATION,
    columns: Optional[list] = None,
) -> Dict[str, pd.Series]:
    """Apply Hampel cleaning ke semua PV string V/I kolom di combined_df.

    Tidak modify ``df`` in-place. Return dict {col_name: cleaned_series} supaya
    caller bisa apply selectively.

    Parameters
    ----------
    df : pd.DataFrame
        Combined_df (mungkin ber-Index timestamp atau ber-kolom "Start Time").
    pv_max : int, default 28
        Loop PV1..PV{pv_max} untuk ``input voltage(V)`` + ``input current(A)``.
    window, max_deviation : pass-through ke :func:`clean_with_hampel`.
    columns : list[str], optional
        Override list kolom (skip auto-discovery PV columns).

    Returns
    -------
    Dict[str, pd.Series]
        ``{col_name: cleaned_series}``. Skip kolom yang tidak ada di df.
    """
    if columns is None:
        columns = []
        for n in range(1, pv_max + 1):
            for suffix in ("input voltage(V)", "input current(A)"):
                col = f"PV{n} {suffix}"
                if col in df.columns:
                    columns.append(col)

    out: Dict[str, pd.Series] = {}
    for col in columns:
        if col not in df.columns:
            warnings.warn(
                f"[preprocessing] column {col!r} not in df; skipping.",
                stacklevel=2,
            )
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        out[col] = clean_with_hampel(
            series, window=window, max_deviation=max_deviation,
        )
    return out


if __name__ == "__main__":
    rng = np.random.default_rng(2026)
    n = 200
    t = pd.date_range("2026-05-14 06:00", periods=n, freq="5min")
    base = 1200.0 + 50.0 * np.sin(np.linspace(0, np.pi, n)) + rng.normal(0, 5, n)
    series = pd.Series(base, index=t, name="PV5 input voltage(V)")

    spike_idx = [50, 100, 150]
    for i in spike_idx:
        series.iloc[i] += 200.0

    mask = hampel_outlier_mask(series, window=15, max_deviation=3.0)
    cleaned = clean_with_hampel(series, window=15, max_deviation=3.0)

    print(f"[preprocessing] synthetic V series (n={n}, 3 spikes injected at {spike_idx})")
    print(f"  hampel mask True count : {int(mask.sum())} (expected >= 3)")
    print(f"  cleaned NaN count      : {int(cleaned.isna().sum())} (expected >= 3)")
    for i in spike_idx:
        flagged = bool(mask.iloc[i])
        print(f"    idx {i} (V={series.iloc[i]:.1f}): flagged={flagged}")

    print("\n[preprocessing] smoke OK")
