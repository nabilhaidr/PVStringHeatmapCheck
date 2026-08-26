"""Baseline energi M2f: E_expected dari POA+Tcell terukur, dan E_actual.

`E_expected` memakai ``physics.compute_p_expected_per_string`` yang hanya
memperhitungkan POA DEPAN, sedangkan modul Jinko JKM625N bifacial. Koefisien
``bifacial_gain`` per WB mengoreksi under-estimate itu; dikalibrasi dari string
sehat pada hari clear-sky (lihat :func:`calibrate_bifacial_gain`).
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from pv_pipeline.panel_spec import PanelSpec
from pv_pipeline.physics import compute_p_expected_per_string


# Sampling PLTS-IKN = 5 menit.
DEFAULT_FREQ_HOURS: float = 5.0 / 60.0


def compute_expected_energy_kwh(
    poa_wm2: pd.Series,
    tcell_c: pd.Series,
    panel_spec: PanelSpec,
    wb_id: str,
    *,
    bifacial_gain: float = 1.0,
    freq_hours: float = DEFAULT_FREQ_HOURS,
) -> pd.Series:
    """Energi harapan per timestamp (kWh) untuk satu PV string.

    ``compute_p_expected_per_string`` mengembalikan Watt per string; dibagi
    1000 menjadi kW, dikali ``freq_hours`` menjadi kWh.
    """
    p_watt = compute_p_expected_per_string(poa_wm2, tcell_c, panel_spec, wb_id)
    if not isinstance(p_watt, pd.Series):
        p_watt = pd.Series(p_watt, index=poa_wm2.index)
    kwh = (p_watt / 1000.0) * float(freq_hours) * float(bifacial_gain)
    kwh = kwh.fillna(0.0).clip(lower=0.0)
    kwh.name = "e_expected_kwh"
    return kwh


def compute_actual_energy_kwh(
    power_kw: pd.Series,
    *,
    freq_hours: float = DEFAULT_FREQ_HOURS,
) -> pd.Series:
    """Energi aktual per timestamp (kWh) dari daya DC string (kW).

    NaN diperlakukan sebagai 0 kWh: sampel hilang berarti energi tidak
    tercatat, dan selisihnya akan muncul sebagai rugi yang harus diatribusikan
    -- bukan disembunyikan.
    """
    kwh = pd.to_numeric(power_kw, errors="coerce").fillna(0.0) * float(freq_hours)
    kwh.name = "e_actual_kwh"
    return kwh


def calibrate_bifacial_gain(
    expected_kwh_per_string: pd.Series,
    actual_kwh_per_string: pd.Series,
    *,
    min_strings: int = 3,
) -> float:
    """Median rasio aktual/harapan pada string sehat di hari clear-sky.

    Parameters
    ----------
    expected_kwh_per_string, actual_kwh_per_string : pd.Series
        Total kWh per string, di-index oleh string_id. ``expected`` dihitung
        dengan ``bifacial_gain=1.0``.
    min_strings : int, default 3
        Jumlah string minimum. Di bawah ini kalibrasi ditolak -- gain dari
        satu-dua string adalah kebetulan, bukan kalibrasi.

    Notes
    -----
    String yang index-nya tidak overlap antara ``expected`` dan ``actual``
    di-drop oleh ``align(..., join="inner")`` sebelum kalibrasi dihitung.
    Drop ini dilaporkan lewat ``warnings.warn`` (jumlah per arah) dan
    diikutkan dalam pesan ``ValueError`` bila sampel jadi terlalu tipis --
    supaya tidak diam-diam mengecilkan sampel kalibrasi.
    """
    idx_expected = expected_kwh_per_string.index
    idx_actual = actual_kwh_per_string.index
    n_missing_in_actual = int(idx_expected.difference(idx_actual).size)
    n_missing_in_expected = int(idx_actual.difference(idx_expected).size)

    expected, actual = expected_kwh_per_string.align(
        actual_kwh_per_string, join="inner"
    )
    if n_missing_in_actual or n_missing_in_expected:
        # Dua arah dilaporkan terpisah karena masing-masing menuduh langkah
        # upstream yang berbeda: hilang dari actual = string belum/putus
        # tercatat di telemetri; hilang dari expected = string belum
        # dihitung di sisi POA/Tcell.
        warnings.warn(
            f"[m2f] kalibrasi bifacial: {n_missing_in_actual} string ada di "
            f"expected tapi hilang dari actual, {n_missing_in_expected} string "
            "ada di actual tapi hilang dari expected -- string ini di-drop "
            "sebelum kalibrasi (index tidak overlap).",
            stacklevel=2,
        )

    valid = expected > 0.0
    n_valid = int(valid.sum())
    if n_valid < min_strings:
        raise ValueError(
            f"[m2f] kalibrasi bifacial butuh minimal {min_strings} string dengan "
            f"expected > 0; hanya ada {n_valid} (setelah align: "
            f"{n_missing_in_actual} string hilang dari actual, "
            f"{n_missing_in_expected} string hilang dari expected)."
        )
    ratio = actual[valid] / expected[valid]
    return float(np.median(ratio.to_numpy(dtype=float)))
