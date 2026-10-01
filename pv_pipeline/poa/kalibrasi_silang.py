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


KOLOM_BULANAN = ["ws", "bulan", "n", "median", "iqr", "alasan"]


def gain_bulanan(rasio: pd.DataFrame, *, min_sampel: int = 200) -> pd.DataFrame:
    """Median rasio per WS per bulan; < ``min_sampel`` sampel -> NaN, alasan "data tipis"."""
    baris = []
    for ws in rasio.columns:
        s = rasio[ws].dropna()
        for bulan, g in s.groupby(s.index.to_period("M")):
            cukup = len(g) >= min_sampel
            baris.append({"ws": ws, "bulan": str(bulan), "n": int(len(g)),
                          "median": float(g.median()) if cukup else np.nan,
                          "iqr": float(g.quantile(0.75) - g.quantile(0.25)) if cukup else np.nan,
                          "alasan": "" if cukup else "data tipis"})
    return pd.DataFrame(baris, columns=KOLOM_BULANAN)


def profil_jam(rasio: pd.DataFrame) -> pd.DataFrame:
    """Median rasio per jam / median rasio seluruh sampel WS itu di bulan itu."""
    baris = []
    for ws in rasio.columns:
        s = rasio[ws].dropna()
        for bulan, g in s.groupby(s.index.to_period("M")):
            acuan = g.median()
            for jam, h in g.groupby(g.index.hour):
                baris.append({"ws": ws, "bulan": str(bulan), "jam": int(jam), "n": int(len(h)),
                              "profil": float(h.median() / acuan)})
    return pd.DataFrame(baris, columns=["ws", "bulan", "jam", "n", "profil"])


def penghalang(profil: pd.DataFrame, *, ambang: float = 0.10, min_bulan: int = 3) -> pd.DataFrame:
    """(WS, jam) yang profilnya < 1 - ``ambang`` pada >= ``min_bulan`` bulan."""
    turun = profil[profil["profil"] < 1.0 - ambang]
    hit = turun.groupby(["ws", "jam"])["bulan"].nunique().reset_index(name="n_bulan")
    return hit[hit["n_bulan"] >= min_bulan].reset_index(drop=True)


def gain_relatif(rasio: pd.DataFrame, jam_penghalang: pd.DataFrame, *, min_sampel: int = 200,
                 ambang_geser: float = 0.05) -> pd.DataFrame:
    """Median rasio selama rentang tanpa jam penghalang; ``bergeser`` bila median bulanan berayun > ambang."""
    baris = []
    for ws in rasio.columns:
        s = rasio[ws].dropna()
        buang = set(jam_penghalang.loc[jam_penghalang["ws"] == ws, "jam"]) if len(jam_penghalang) else set()
        s = s[~s.index.hour.isin(sorted(buang))]
        bulanan = gain_bulanan(s.to_frame(ws), min_sampel=min_sampel)["median"].dropna()
        ayunan = float(bulanan.max() - bulanan.min()) if len(bulanan) else np.nan
        baris.append({"ws": ws, "gain": float(s.median()) if len(s) >= min_sampel else np.nan,
                      "n": int(len(s)), "ayunan_bulanan": ayunan,
                      "bergeser": bool(np.isfinite(ayunan) and ayunan > ambang_geser)})
    return pd.DataFrame(baris, columns=["ws", "gain", "n", "ayunan_bulanan", "bergeser"])
