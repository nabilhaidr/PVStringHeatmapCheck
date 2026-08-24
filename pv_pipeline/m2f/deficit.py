"""Skema artefak deret waktu defisit, dipakai bersama oleh 3 detektor m2b.

Detektor m2b sudah menghitung arus aktual dan arus counterfactual (median
sibling / median partner MPPT) per timestamp, tetapi hanya menyimpan skor
akhirnya. M2f butuh deret waktunya untuk mengklaim energi ke ledger.
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from pv_pipeline.m2f.baseline import DEFAULT_FREQ_HOURS


DEFICIT_COLUMNS: List[str] = [
    "poa_source",
    "timestamp",
    "inverter_id",
    "pv_string",
    "actual_kw",
    "counterfactual_kw",
    "flagged",
]


def build_deficit_frame(
    timestamps,
    poa_source: str,
    inverter_id: str,
    pv_string: str,
    actual_kw: np.ndarray,
    counterfactual_kw: np.ndarray,
    flagged: np.ndarray,
) -> pd.DataFrame:
    """Rakit satu frame defisit dengan skema tetap ``DEFICIT_COLUMNS``.

    ``poa_source`` wajib disertakan -- setiap detektor loop di 5 POA source
    dan flag mask-nya berbeda per source, jadi tanpa kolom ini
    ``(inverter_id, pv_string, timestamp)`` bukan key unik.
    """
    idx = pd.DatetimeIndex(timestamps)
    frame = pd.DataFrame(
        {
            "poa_source": str(poa_source),
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

    Timestamp yang TIDAK ``flagged`` dijamin 0.0 -- itu memang bukan kandidat
    rugi kategori ini. Timestamp yang ``flagged`` TAPI gapnya NaN (mis. kolom
    tegangan hilang upstream sehingga ``actual_kw``/``counterfactual_kw`` tak
    terhitung) dipertahankan sebagai NaN, BUKAN diisi 0.0 -- string itu tidak
    bisa dievaluasi sama sekali, dan mengisi 0.0 di sini akan melaporkan
    "dicek, aman" padahal sebenarnya "tidak bisa dicek". Batas yang benar-
    benar menegakkan ini ada di ``LossLedger.claim()``, yang me-raise pada
    NaN alih-alih menelannya.
    """
    missing = [c for c in DEFICIT_COLUMNS if c not in frame.columns]
    if missing:
        raise KeyError(f"[m2f] frame defisit kehilangan kolom {missing}.")
    actual = pd.to_numeric(frame["actual_kw"], errors="coerce")
    counterfactual = pd.to_numeric(frame["counterfactual_kw"], errors="coerce")
    gap = (counterfactual - actual).clip(lower=0.0)
    gap = gap.where(frame["flagged"].astype(bool), 0.0)
    out = pd.Series(
        (gap * float(freq_hours)).to_numpy(dtype=float),
        index=pd.DatetimeIndex(frame["timestamp"]),
        name="deficit_kwh",
    )
    return out


def reduce_deficit_frames(
    frames: List[pd.DataFrame],
    *,
    poa_source: str,
    index: pd.DatetimeIndex,
    freq_hours: float = DEFAULT_FREQ_HOURS,
) -> pd.Series:
    """Gabung frame defisit dari beberapa detektor jadi satu Series kWh.

    Production ``emit_all_sources: true`` dengan 5 POA source berarti ketiga
    detektor m2b bersama-sama menghasilkan hingga 15 frame per string (3
    detektor x 5 source). Menyatukan semuanya lewat ``.reindex()`` polos
    melempar ``ValueError: cannot reindex on an axis with duplicate labels``,
    dan ``groupby(level=0).sum()`` menghitung dua kali energi fisik yang sama
    saat dua detektor menandai string yang sama pada timestamp yang sama.

    Langkah: (1) buang baris yang bukan ``poa_source`` yang diminta -- sisa
    per detektor lalu punya satu baris per timestamp; (2) konversi tiap frame
    ke kWh lewat :func:`deficit_to_kwh`; (3) ambil MAKSIMUM elemen-per-elemen
    antar detektor -- dua detektor menandai fisik yang sama menjelaskan SATU
    rugi, bukan dua, jadi menjumlahkan akan melipatgandakannya; (4) reindex ke
    ``index`` (timeline penuh ledger), isi 0.0 untuk timestamp yang tak
    tercakup detektor manapun.
    """
    idx = pd.DatetimeIndex(index)
    per_detector: List[pd.Series] = []
    for frame in frames:
        subset = frame[frame["poa_source"] == poa_source]
        if subset.empty:
            continue
        per_detector.append(deficit_to_kwh(subset, freq_hours=freq_hours))

    if not per_detector:
        return pd.Series(0.0, index=idx, dtype=float, name="deficit_kwh")

    combined = pd.concat(per_detector, axis=1)
    # max(skipna=True): kalau SEMUA detektor NaN pada satu timestamp (mis.
    # kolom tegangan hilang di semua), hasilnya tetap NaN -- dipertahankan
    # (bukan di-fillna ke 0.0) supaya "tidak bisa dievaluasi" tidak menyamar
    # jadi "dicek, aman", sejalan dengan deficit_to_kwh di atas. reindex di
    # bawah hanya mengisi 0.0 pada timestamp yang SAMA SEKALI tak tercakup
    # detektor manapun (bukan pada NaN yang sudah ada).
    reduced = combined.max(axis=1, skipna=True)
    reduced = reduced.reindex(idx, fill_value=0.0)
    reduced.name = "deficit_kwh"
    return reduced.astype(float)
