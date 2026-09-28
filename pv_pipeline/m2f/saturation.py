"""Pemilah kekurangan daya di POA tinggi: clipping AC vs sensor vs steady-state.

Latar: rasio aktual/harapan M2f (``measured_ratio``) ~0,85 di hari cerah dan
~1,0 di hari mendung. Uji lokal 2026-09-28 menemukan daya berhenti naik di
atas ~750 W/m2, tetapi hari "cerah" itu penuh lonjakan tepi awan, sehingga
tiga penyebab bercampur:

* clipping AC -- daya datar di plafon inverter walau POA terus berubah;
* keterwakilan sensor -- pyranometer titik menangkap lonjakan/celah awan yang
  tidak diterima seluruh array (terlihat di sampel TIDAK stabil saja);
* steady-state -- kekurangan yang bertahan di sampel stabil tanpa clipping
  (kalibrasi sensor, spektrum, suhu).

Per inverter-hari: ``k`` = median(Pc / POA) pada sampel STABIL di pita
kalibrasi (Pc = daya DC terkoreksi suhu); prediksi = k x POA. Sampel stabil =
POA menyimpang < ``smooth_tol`` dari rata-rata kedua tetangganya, di sampel
itu dan kedua tetangganya (lereng pagi yang mulus tetap stabil; tepi awan
tidak). Clipping = >= ``min_plateau`` sampel POA tinggi dengan daya AC dalam
``ceiling_frac`` dari maksimum harian SEMENTARA POA di sampel itu menyebar >=
``min_plateau_poa_span`` -- puncak kurva cerah (daya ikut POA) tidak lolos.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

SATURATION_METRICS: List[str] = [
    "n_calib", "n_high", "n_high_stable", "stable_high_share", "clipping", "ac_max_kw",
    "r_high_all", "r_high_stable", "r_high_stable_unclipped",
    "clip_loss_pct", "nonclip_high_loss_pct", "high_share_pct",
]


def _ratio(pc: np.ndarray, pred: np.ndarray, mask: np.ndarray, min_n: int) -> float:
    """Rasio energi sum(Pc) / sum(prediksi) -- berbobot energi, bukan median."""
    if mask.sum() < min_n:
        return np.nan
    return float(pc[mask].sum() / pred[mask].sum())


def saturation_metrics(
    poa, p_dc, p_ac, tcell, elev, *,
    gamma: float,
    high_min: float = 750.0,
    calib_range: Tuple[float, float] = (300.0, 700.0),
    calib_elev_min: float = 20.0,
    smooth_tol: float = 0.02,
    ceiling_frac: float = 0.99,
    min_plateau: int = 6,
    min_plateau_poa_span: float = 0.05,
    min_calib: int = 8,
    min_stable: int = 3,
) -> Dict[str, float]:
    """Metrik satu inverter-hari; array berurutan waktu dengan panjang sama.

    ``r_*`` = sum(Pc)/sum(k x POA) pada sampel POA tinggi (semua / stabil /
    stabil tanpa clipping). ``*_loss_pct`` = kekurangan sebagai % energi
    harapan harian sum(k x POA). NaN bila sampel stabil kalibrasi < ``min_calib``.
    """
    poa, p_dc, p_ac, tcell, elev = (
        np.asarray(a, dtype=float) for a in (poa, p_dc, p_ac, tcell, elev)
    )
    pc = p_dc / (1.0 + gamma * (tcell - 25.0))
    ok = np.isfinite(pc) & np.isfinite(poa) & (poa > 20.0)

    s = pd.Series(poa)
    deviation = (s - (s.shift(1) + s.shift(-1)) / 2.0).abs() / s
    smooth = deviation < smooth_tol
    stable = (smooth & smooth.shift(1, fill_value=False)
              & smooth.shift(-1, fill_value=False)).to_numpy()

    calib = (ok & stable & (poa >= calib_range[0]) & (poa <= calib_range[1])
             & (elev >= calib_elev_min))
    out: Dict[str, float] = dict.fromkeys(SATURATION_METRICS, np.nan)
    out["n_calib"] = int(calib.sum())
    if calib.sum() < min_calib:
        return out

    pred = float(np.median(pc[calib] / poa[calib])) * poa
    high = ok & (poa > high_min)
    stable_high = high & stable
    ac_ok = ok & np.isfinite(p_ac)
    ac_max = float(np.max(p_ac[ac_ok])) if ac_ok.any() else np.nan
    near = high & np.isfinite(p_ac) & (p_ac >= ceiling_frac * ac_max)
    span = (np.ptp(poa[near]) / np.median(poa[near])) if near.any() else 0.0
    clipping = bool(near.sum() >= min_plateau and span >= min_plateau_poa_span)
    ceiling = near if clipping else np.zeros_like(near)
    expected = float(pred[ok].sum())

    out.update({
        "n_high": int(high.sum()),
        "n_high_stable": int(stable_high.sum()),
        "stable_high_share": float(stable_high.sum() / high.sum()) if high.any() else np.nan,
        "clipping": float(clipping),
        "ac_max_kw": ac_max,
        "r_high_all": _ratio(pc, pred, high, min_stable),
        "r_high_stable": _ratio(pc, pred, stable_high, min_stable),
        "r_high_stable_unclipped": _ratio(pc, pred, stable_high & ~ceiling, min_stable),
        "clip_loss_pct": float((pred - pc)[ceiling].sum() / expected * 100.0),
        "nonclip_high_loss_pct": float((pred - pc)[high & ~ceiling].sum() / expected * 100.0),
        "high_share_pct": float(pred[high].sum() / expected * 100.0),
    })
    return out
