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


_NAN = float("nan")


def fit_wb(tabel: pd.DataFrame) -> dict:
    """Regresi OLS ``rasio ~ 1 + (kt - kt_ref) + (mulus - mulus_ref) + hari_sejak_hujan`` satu WB.

    Galat baku dari (X'X)^-1 s^2 (tanpa statsmodels). ``rasio_bersih`` = median
    rasio yang dikoreksi ke hari baru hujan: kandidat derate.
    """
    t = tabel.dropna(subset=list(KOLOM_FIT))
    n = len(t)
    kt, mulus = t["kt"].to_numpy(float), t["mulus"].to_numpy(float)
    hasil = {"n": n, "kt_ref": float(np.median(kt)) if n else _NAN,
             "mulus_ref": float(np.median(mulus)) if n else _NAN,
             "iqr_kt": float(np.subtract(*np.percentile(kt, [75, 25]))) if n else _NAN,
             "iqr_mulus": float(np.subtract(*np.percentile(mulus, [75, 25]))) if n else _NAN}
    nama = ("a", "b", "c", "e")
    if n <= len(nama):
        hasil.update({k: _NAN for k in nama}, **{f"se_{k}": _NAN for k in nama}, **{f"t_{k}": _NAN for k in nama},
                     ayunan_langit=_NAN, rasio_bersih=_NAN)
        return hasil
    hsh = t["hari_sejak_hujan"].to_numpy(float)
    X = np.column_stack([np.ones(n), kt - hasil["kt_ref"], mulus - hasil["mulus_ref"], hsh])
    y = t["rasio"].to_numpy(float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    sisa = y - X @ beta
    s2 = float(sisa @ sisa) / (n - X.shape[1])
    se = np.sqrt(np.clip(np.diag(np.linalg.pinv(X.T @ X)) * s2, 0.0, None))
    for k, b, s in zip(nama, beta, se):
        hasil[k], hasil[f"se_{k}"] = float(b), float(s)
        hasil[f"t_{k}"] = float(b / s) if s > 0 else (0.0 if b == 0 else float(np.sign(b)) * np.inf)
    hasil["ayunan_langit"] = abs(hasil["b"]) * hasil["iqr_kt"] + abs(hasil["c"]) * hasil["iqr_mulus"]
    hasil["rasio_bersih"] = float(np.median(y - hasil["e"] * hsh))
    return hasil


def putuskan(fit: dict, *, ambang_ayunan: float = 0.03, ambang_t: float = 2.0, min_hari: int = 20) -> dict:
    """Konstanta per WB atau model langit -- aturan tertulis di spesifikasi."""
    if fit["n"] < min_hari:
        return {"keputusan": "data_kurang", "nilai": _NAN, "alasan": f"{fit['n']} hari sah < {min_hari}"}
    if fit["e"] > 0 and fit["t_e"] >= ambang_t:
        return {"keputusan": "tercampur", "nilai": _NAN,
                "alasan": f"rasio naik seiring debu (e = {fit['e']:.4f}, t = {fit['t_e']:.1f})"}
    if fit["ayunan_langit"] < ambang_ayunan or (abs(fit["t_b"]) < ambang_t and abs(fit["t_c"]) < ambang_t):
        return {"keputusan": "konstanta", "nilai": float(fit["rasio_bersih"]),
                "alasan": f"ayunan langit {fit['ayunan_langit']:.3f}; t_b {fit['t_b']:.1f}, t_c {fit['t_c']:.1f}"}
    return {"keputusan": "model_langit", "nilai": _NAN,
            "alasan": f"ayunan langit {fit['ayunan_langit']:.3f} >= {ambang_ayunan}; "
                      f"t_b {fit['t_b']:.1f}, t_c {fit['t_c']:.1f}"}


def validasi(tabel: pd.DataFrame, fits: dict, derate: dict) -> pd.DataFrame:
    """Sisa median ``1 - (rasio - e hari_sejak_hujan)/derate`` per WB dan tercile kt.

    Konstanta yang benar: beda median antar-tercile <= 0,03.
    """
    nama = np.array(["rendah", "sedang", "tinggi"])
    baris = []
    for wb, d in derate.items():
        t = tabel[tabel["wb_id"] == wb].dropna(subset=list(KOLOM_FIT))
        if t.empty or not np.isfinite(d):
            continue
        sisa = 1.0 - (t["rasio"] - fits[wb]["e"] * t["hari_sejak_hujan"]) / d
        kode = pd.qcut(t["kt"], 3, labels=False, duplicates="drop")
        label = kode.map(lambda i: nama[int(i)] if kode.max() == 2 else f"q{int(i)}")
        for lab, g in sisa.groupby(label):
            baris.append({"wb_id": wb, "tercile_kt": lab, "n": int(len(g)), "sisa_median": float(g.median())})
    v = pd.DataFrame(baris, columns=["wb_id", "tercile_kt", "n", "sisa_median"])
    v["beda_antar_tercile"] = v.groupby("wb_id")["sisa_median"].transform(lambda s: s.max() - s.min())
    return v
