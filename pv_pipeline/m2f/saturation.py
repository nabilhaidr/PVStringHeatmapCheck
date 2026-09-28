"""Pemilah kekurangan daya di POA tinggi: plafon set point vs sensor vs steady-state.

Latar: rasio aktual/harapan M2f (``measured_ratio``) ~0,85 di hari cerah dan
~1,0 di hari mendung. Daya berhenti naik di atas ~800 W/m2 karena tiga
penyebab yang bercampur:

* plafon set point busbar -- pembatasan penyaluran jaringan distribusi
  eksternal 20 kV (curtailment, lihat :mod:`pv_pipeline.m2f.setpoint`):
  daya datar di plafon walau POA terus berubah;
* keterwakilan sensor -- pyranometer titik menangkap lonjakan/celah awan yang
  tidak diterima seluruh array (terlihat di sampel TIDAK stabil saja);
* steady-state -- kekurangan yang bertahan di sampel stabil di bawah plafon
  (kalibrasi sensor, spektrum, suhu).

Per inverter-hari: ``k`` = median(Pc / POA) pada sampel STABIL di pita
kalibrasi (Pc = daya DC terkoreksi suhu); prediksi = k x POA. Sampel stabil =
POA menyimpang < ``smooth_tol`` dari rata-rata kedua tetangganya, di sampel
itu dan kedua tetangganya (lereng pagi yang mulus tetap stabil; tepi awan
tidak). Sampel di plafon = riwayat set point (``cap_kw``) ATAU bentuk plateau
-- dua penanda yang sama dengan curtailment M2f.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from pv_pipeline.m2f.setpoint import capped_mask, plateau_mask

SATURATION_METRICS: List[str] = [
    "n_calib", "n_high", "n_high_stable", "stable_high_share", "at_ceiling", "ac_max_kw",
    "r_high_all", "r_high_stable", "r_high_stable_uncapped",
    "ceiling_loss_pct", "uncapped_high_loss_pct", "high_share_pct",
]


def _ratio(pc: np.ndarray, pred: np.ndarray, mask: np.ndarray, min_n: int) -> float:
    """Rasio energi sum(Pc) / sum(prediksi) -- berbobot energi, bukan median."""
    if mask.sum() < min_n:
        return np.nan
    return float(pc[mask].sum() / pred[mask].sum())


def saturation_metrics(
    poa, p_dc, p_ac, tcell, elev, *,
    gamma: float,
    cap_kw: Optional[np.ndarray] = None,
    pmax_kw: float = np.inf,
    high_min: float = 750.0,
    calib_range: Tuple[float, float] = (300.0, 700.0),
    calib_elev_min: float = 20.0,
    smooth_tol: float = 0.02,
    min_calib: int = 8,
    min_stable: int = 3,
) -> Dict[str, float]:
    """Metrik satu inverter-hari; array berurutan waktu dengan panjang sama.

    ``cap_kw`` = plafon set point per timestamp (``SetpointCaps.cap_kw``),
    ``pmax_kw`` = daya AC maks inverter; tanpa keduanya hanya penanda plateau
    yang dipakai. ``r_*`` = sum(Pc)/sum(k x POA) pada sampel POA tinggi
    (semua / stabil / stabil di bawah plafon). ``*_loss_pct`` = kekurangan
    sebagai % energi harapan harian sum(k x POA). NaN bila sampel stabil
    kalibrasi < ``min_calib``.
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
    ceiling = ok & plateau_mask(p_ac, poa, pmax_kw)
    if cap_kw is not None:
        ceiling |= ok & capped_mask(p_ac, cap_kw, pmax_kw)
    ac_ok = ok & np.isfinite(p_ac)
    expected = float(pred[ok].sum())

    out.update({
        "n_high": int(high.sum()),
        "n_high_stable": int(stable_high.sum()),
        "stable_high_share": float(stable_high.sum() / high.sum()) if high.any() else np.nan,
        "at_ceiling": float(ceiling.any()),
        "ac_max_kw": float(np.max(p_ac[ac_ok])) if ac_ok.any() else np.nan,
        "r_high_all": _ratio(pc, pred, high, min_stable),
        "r_high_stable": _ratio(pc, pred, stable_high, min_stable),
        "r_high_stable_uncapped": _ratio(pc, pred, stable_high & ~ceiling, min_stable),
        "ceiling_loss_pct": float((pred - pc)[ceiling].sum() / expected * 100.0),
        "uncapped_high_loss_pct": float((pred - pc)[high & ~ceiling].sum() / expected * 100.0),
        "high_share_pct": float(pred[high].sum() / expected * 100.0),
    })
    return out
