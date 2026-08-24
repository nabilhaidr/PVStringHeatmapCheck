"""Skema artefak deret waktu defisit, dipakai bersama oleh 4 detektor m2b.

Detektor m2b sudah menghitung arus aktual dan arus counterfactual (median
sibling / median partner MPPT) per timestamp, tetapi hanya menyimpan skor
akhirnya. M2f butuh deret waktunya untuk mengklaim energi ke ledger.
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd


TIMESERIES_DEFICIT_SHEET: str = "TimeseriesDeficit"

DEFICIT_COLUMNS: List[str] = [
    "timestamp",
    "inverter_id",
    "pv_string",
    "actual_kw",
    "counterfactual_kw",
    "flagged",
]


def build_deficit_frame(
    timestamps,
    inverter_id: str,
    pv_string: str,
    actual_kw: np.ndarray,
    counterfactual_kw: np.ndarray,
    flagged: np.ndarray,
) -> pd.DataFrame:
    """Rakit satu frame defisit dengan skema tetap ``DEFICIT_COLUMNS``."""
    idx = pd.DatetimeIndex(timestamps)
    frame = pd.DataFrame(
        {
            "timestamp": idx,
            "inverter_id": str(inverter_id),
            "pv_string": str(pv_string),
            "actual_kw": np.asarray(actual_kw, dtype=float),
            "counterfactual_kw": np.asarray(counterfactual_kw, dtype=float),
            "flagged": np.asarray(flagged, dtype=bool),
        }
    )
    return frame[DEFICIT_COLUMNS]


def deficit_to_kwh(frame: pd.DataFrame, *, freq_hours: float) -> pd.Series:
    """Defisit energi (kWh) per timestamp, hanya pada baris ``flagged``.

    Defisit negatif (string melampaui counterfactual) dipotong ke nol -- itu
    bukan rugi, dan bukan urusan kategori ini.
    """
    missing = [c for c in DEFICIT_COLUMNS if c not in frame.columns]
    if missing:
        raise KeyError(f"[m2f] frame defisit kehilangan kolom {missing}.")
    actual = pd.to_numeric(frame["actual_kw"], errors="coerce")
    counterfactual = pd.to_numeric(frame["counterfactual_kw"], errors="coerce")
    gap = (counterfactual - actual).fillna(0.0).clip(lower=0.0)
    gap = gap.where(frame["flagged"].astype(bool), 0.0)
    out = pd.Series(
        (gap * float(freq_hours)).to_numpy(dtype=float),
        index=pd.DatetimeIndex(frame["timestamp"]),
        name="deficit_kwh",
    )
    return out
