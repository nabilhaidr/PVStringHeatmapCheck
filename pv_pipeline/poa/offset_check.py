"""Offset waktu POA terhadap telemetri inverter.

Lag dicari dari korelasi PERUBAHAN (diff) daya dengan perubahan POA: saat awan
lewat, keduanya melompat bersamaan bila stempel waktunya selaras. Lag positif
L = nilai POA berstempel ``t - L`` cocok dengan daya berstempel ``t``, yaitu
stempel POA L menit lebih awal; koreksinya MENAMBAHKAN L ke stempel POA.
"""
from __future__ import annotations

from typing import Iterable, List

import numpy as np
import pandas as pd

DEFAULT_LAGS_MIN = (-15, -10, -5, 0, 5, 10, 15)
DEFAULT_TOLERANCE = pd.Timedelta("2min")
OFFSET_COLUMNS: List[str] = ["ws", "best_lag_min", "corr_best", "corr_lag0", "n"]


def best_lag_by_ws(
    power_by_ws: pd.DataFrame,
    poa_by_ws: pd.DataFrame,
    *,
    lags_min: Iterable[int] = DEFAULT_LAGS_MIN,
    min_points: int = 20,
    tolerance: pd.Timedelta = DEFAULT_TOLERANCE,
) -> pd.DataFrame:
    """Lag terbaik per WS.

    ``power_by_ws``: index stempel telemetri, kolom WS (daya median WB-nya).
    ``poa_by_ws``: index stempel POA asli, kolom WS. WS dengan < ``min_points``
    pasangan diff sah pada lag 0 dilewati.
    """
    idx = pd.DatetimeIndex(power_by_ws.index)
    rows = []
    for ws in power_by_ws.columns.intersection(poa_by_ws.columns):
        d_power = power_by_ws[ws].diff()
        src = poa_by_ws[ws].dropna().sort_index()
        corr, count = {}, {}
        for lag in lags_min:
            shifted = src.reindex(idx - pd.Timedelta(minutes=lag), method="nearest", tolerance=tolerance)
            d_poa = pd.Series(shifted.to_numpy(), index=idx).diff()
            valid = d_power.notna() & d_poa.notna()
            if valid.sum() < min_points:
                continue
            c = float(np.corrcoef(d_power[valid], d_poa[valid])[0, 1])
            if np.isfinite(c):
                corr[lag], count[lag] = c, int(valid.sum())
        if 0 not in corr:
            continue
        best = max(corr, key=corr.get)
        rows.append({
            "ws": ws, "best_lag_min": int(best), "corr_best": corr[best],
            "corr_lag0": corr[0], "n": count[best],
        })
    return pd.DataFrame(rows, columns=OFFSET_COLUMNS)
