"""Kalibrasi silang POA antar-stasiun cuaca -- bias amplitudo sensor dan jam penghalang.

Rancangan: ``docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md``.

Tidak ada sensor POA "benar" di lokasi, jadi bias tiap WS diukur terhadap tiga
acuan: median stasiun lain (sampel yang stabil di semua stasiun yang
dibandingkan, supaya bayangan awan lokal tak terbaca sebagai bias), POA langit
cerah pvlib, dan larik itu sendiri lewat ``measured_ratio`` M2f. Faktor koreksi
hanya diusulkan bila >= 2 acuan sepakat.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

POA_MAKS = 1400.0


def sampel_stabil(poa: pd.DataFrame, *, toleransi: float = 0.02, poa_min: float = 300.0,
                  jam: tuple = ("09:00", "15:00")) -> pd.DataFrame:
    """Benar bila POA mulus di sampel itu dan kedua tetangganya, > ``poa_min``, dalam jendela ``jam``.

    "Mulus" = |POA - rata-rata kedua tetangga| / POA < ``toleransi`` -- definisi
    yang sama dengan ``pv_pipeline.m2f.saturation``. POA < 0 atau > 1400 = galat.
    """
    p = poa.where((poa >= 0.0) & (poa <= POA_MAKS))
    dev = (p - (p.shift(1) + p.shift(-1)) / 2.0).abs() / p
    mulus = dev < toleransi
    stabil = mulus & mulus.shift(1, fill_value=False) & mulus.shift(-1, fill_value=False)
    dalam = np.zeros(len(p), dtype=bool)
    dalam[p.index.indexer_between_time(*jam)] = True
    jendela = pd.DataFrame(np.repeat(dalam[:, None], p.shape[1], axis=1), index=p.index, columns=p.columns)
    return stabil & (p > poa_min) & jendela


def rasio_ke_median(poa: pd.DataFrame, stabil: pd.DataFrame, *, min_pembanding: int = 2) -> pd.DataFrame:
    """POA_WS / median POA stasiun LAIN yang stabil di sampel yang sama.

    NaN bila WS itu tak stabil atau pembandingnya < ``min_pembanding``. WS
    yang kosong tidak diisi rata-rata situs: isi rata-rata menarik rasio ke 1.
    """
    p = poa.where(stabil)
    hasil = {}
    for ws in p.columns:
        lain = p.drop(columns=ws)
        hasil[ws] = (p[ws] / lain.median(axis=1, skipna=True)).where(lain.notna().sum(axis=1) >= min_pembanding)
    return pd.DataFrame(hasil, index=p.index)
