"""Jembatan submodule m2b/m2a yang sudah dijalankan -> ``config["m2f"]``.

``M2Engine.run_all`` (pv_pipeline/core.py) menjalankan tiap submodule dengan
``config`` yang SAMA -- tidak ada jalur untuk satu submodule meneruskan
hasilnya ke submodule lain. M2f bukan detektor independen seperti itu: ia
MENGONSUMSI keluaran tiga detektor m2b (``deficit_frames``) dan M2aSoiling
(``MonthlySoilingLoss``). ``collect_m2f_inputs`` mengisi celah itu -- dipanggil
notebook di antara submodule m2b/m2a selesai `run()` dan ``M2fLossAttribution``
mulai `run()`.

Kontrak tiga-keadaan yang WAJIB dijaga (lihat pv_pipeline/m2f/report.py dan
Task 9 "Kendala WAJIB" #2/#3): "terukur" (nilai nyata) harus tetap beda dari
"dijalankan, nihil" (0.0) dan dari "tidak pernah dijalankan" (None/absen).
Fungsi ini TIDAK PERNAH mengubah kondisi ketiga menjadi kondisi kedua --
lihat catatan di tiap helper di bawah.
"""
from __future__ import annotations

from typing import Iterable, List

import pandas as pd

from pv_pipeline.core import SubModule


def _collect_deficit_frames(submodules: Iterable[SubModule]) -> List[pd.DataFrame]:
    """Gabungkan ``self.deficit_frames`` dari tiap submodule yang punya isi.

    ``getattr(sm, "deficit_frames", None)`` -- BUKAN nama submodule -- yang
    menentukan ikut atau tidak. Tiga detektor m2b (peer_zscore, open_circuit,
    mppt_ratio) set atribut ini; ``ground_fault`` sengaja tidak, dan tidak
    boleh di-special-case balik ke sini. Submodule dengan atribut kosong
    (``[]``, mis. detektor jalan tapi tidak ada string yang lolos gate POA)
    diperlakukan sama seperti submodule yang atributnya sama sekali tidak
    ada: tidak menyumbang apa pun ke daftar gabungan.
    """
    out: List[pd.DataFrame] = []
    for sm in submodules:
        frames = getattr(sm, "deficit_frames", None)
        if frames:
            out.extend(frames)
    return out


def _format_month(value) -> str:
    """Normalisasi satu nilai kolom ``month`` ke ``"YYYY-MM"``.

    ``build_monthly_soiling_loss`` (pv_pipeline/m2a/soiling.py) menulis
    ``str(month)`` dari ``pd.Period(freq="M")`` hasil
    ``df.index.to_period("M")`` -- itu SUDAH persis "YYYY-MM", format yang
    dicari ``M2fLossAttribution.run()`` lewat ``day.strftime("%Y-%m")``.
    Helper ini tetap menangani ``Period``/``Timestamp`` mentah secara
    eksplisit (bukan cuma ``str()``) supaya tidak diam-diam salah format
    kalau skema ``MonthlySoilingLoss`` berubah di kemudian hari.
    """
    if isinstance(value, (pd.Period, pd.Timestamp)):
        return value.strftime("%Y-%m")
    return str(value)


def _collect_p_loss_by_month(submodules: Iterable[SubModule]) -> dict:
    """Kumpulkan ``p_loss_by_month`` dari artifact ``MonthlySoilingLoss``.

    ``p_loss_pct`` adalah PERSEN (0..100); ``claim_soiling`` (m2f/estimators.py)
    mewajibkan ``p_loss`` berupa FRAKSI di [0, 1] dan me-raise ValueError di
    luar rentang itu -- jadi WAJIB dibagi 100 di sini, persis seperti komentar
    ``config/m2_config.yaml`` -> ``m2f.p_loss_by_month``.

    Bulan dengan ``p_loss_pct`` NaN dilewati (tidak masuk dict): itu artinya
    bulan itu tidak punya cakupan insolasi yang cukup untuk SRR (lihat
    ``build_monthly_soiling_loss``, guard ``w <= 0`` dan ``dropna``), bukan
    "soiling nol". Menyertakannya sebagai 0.0 akan melaporkan "sudah dicek,
    aman" untuk bulan yang sebenarnya tidak pernah dihitung.
    """
    out: dict = {}
    for sm in submodules:
        monthly = (getattr(sm, "artifacts", None) or {}).get("MonthlySoilingLoss")
        if monthly is None or monthly.empty:
            continue
        for month_value, p_loss_pct in zip(monthly["month"], monthly["p_loss_pct"]):
            if pd.isna(p_loss_pct):
                continue
            out[_format_month(month_value)] = float(p_loss_pct) / 100.0
    return out


def collect_m2f_inputs(submodules: Iterable[SubModule], config: dict) -> None:
    """Isi ``config["m2f"]["deficit_frames"]``/``["p_loss_by_month"]`` in-place.

    Dipanggil setelah ketiga detektor m2b dan M2aSoiling selesai `run()`,
    sebelum ``M2fLossAttribution().run(combined_df, config)``.

    Kontrak "tidak pernah diukur" != "diukur, aman" (Task 9 Kendala WAJIB
    #2/#3) ditegakkan di sini, bukan diandaikan sudah benar di ``report.py``:
    - ``deficit_frames`` diisi ``None`` (BUKAN ``[]``) kalau tidak ada satu
      pun submodule yang menyumbang frame -- ``report.py`` memang
      memperlakukan ``None`` dan ``[]`` sama hari ini (lihat
      ``_index_deficit_frames``), tapi kontraknya eksplisit di sini supaya
      tidak bergantung pada kebetulan implementasi itu.
    - ``p_loss_by_month`` hanya berisi bulan yang genuinely punya data SRR;
      key yang absen (bukan ``0.0``) berarti "belum pernah dihitung".
    """
    deficit_frames = _collect_deficit_frames(submodules)
    p_loss_by_month = _collect_p_loss_by_month(submodules)

    m2f_cfg = config.setdefault("m2f", {})
    m2f_cfg["deficit_frames"] = deficit_frames if deficit_frames else None
    m2f_cfg["p_loss_by_month"] = p_loss_by_month
