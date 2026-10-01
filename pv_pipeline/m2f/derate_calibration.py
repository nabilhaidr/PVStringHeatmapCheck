"""Kalibrasi ``m2f.dc_derate_per_wb`` dari banyak hari -- rasio harian vs langit dan debu.

Rancangan: ``docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md``.

``measured_ratio`` (sheet ``M2f_BaselineCalib``) ikut langit: ~1,0 saat
mendung, 0,81-0,94 saat cerah-kering. Satu konstanta per WB hanya sah bila
rasio tidak bergantung pada langit setelah efek debu dipisahkan. Model per WB::

    rasio = a + b (kt - kt_ref) + c (mulus - mulus_ref) + e hari_sejak_hujan

``e`` memisahkan debu (hari sejak hujan >= 5 mm) dari langit; tanpa kovariat
itu hari pasca-hujan, yang biasanya juga berawan, menyatukan keduanya.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

KOLOM_FIT = ("rasio", "kt", "mulus", "hari_sejak_hujan")


def hari_sejak_hujan(hujan: pd.Series, hari: pd.DatetimeIndex, *, ambang_mm: float = 5.0,
                     maks: int = 30) -> pd.Series:
    """Hari sejak hujan >= ``ambang_mm`` terakhir (pada atau sebelum hari itu), dibatasi ``maks``.

    ``hujan``: mm per hari, berindeks tanggal. Tanpa hujan pembersih sebelumnya -> ``maks``.
    """
    basah = pd.DatetimeIndex(hujan.index[hujan.to_numpy(dtype=float) >= ambang_mm]).normalize()
    hasil = []
    for h in pd.DatetimeIndex(hari).normalize():
        lalu = basah[basah <= h]
        hasil.append(maks if len(lalu) == 0 else min(maks, int((h - lalu.max()).days)))
    return pd.Series(hasil, index=pd.DatetimeIndex(hari), name="hari_sejak_hujan", dtype=int)


def kt_poa(poa: pd.Series, poa_cerah: pd.Series, elevasi: pd.Series, *, min_elev: float = 15.0) -> float:
    """Sigma POA / Sigma POA langit cerah pada sampel elevasi > ``min_elev`` yang keduanya terisi."""
    p, c, e = (np.asarray(x, dtype=float) for x in (poa, poa_cerah, elevasi))
    m = (e > min_elev) & np.isfinite(p) & np.isfinite(c)
    total = c[m].sum()
    return float(p[m].sum() / total) if m.any() and total > 0 else float("nan")


def porsi_kosong(poa: pd.Series, elevasi: pd.Series, *, min_elev: float = 15.0) -> float:
    """Pecahan sampel elevasi > ``min_elev`` yang POA-nya NaN."""
    p, e = np.asarray(poa, dtype=float), np.asarray(elevasi, dtype=float)
    siang = e > min_elev
    return float(np.isnan(p[siang]).mean()) if siang.any() else float("nan")
