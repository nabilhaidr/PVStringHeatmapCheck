"""Tabel Pareto dari total ledger: urut menurun, kumulatif, vital-few."""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd


PARETO_COLUMNS: List[str] = [
    "category",
    "loss_kwh",
    "pct",
    "cum_pct",
    "actionable",
    "vital_few",
]

VITAL_FEW_THRESHOLD_PCT: float = 80.0

# `unexplained` bukan target tindakan -- ia metrik kualitas atribusi.
# `curtailment` adalah keputusan grid/plant controller, bukan maintenance;
# ikut dikumulatifkan, ia mendorong rugi yang bisa diperbaiki keluar dari
# vital-few.
# `grid_export_limit` (plafon set point normal, batas jaringan 20 kV) sama.
NON_ACTIONABLE: List[str] = ["unexplained", "curtailment", "grid_export_limit"]


def build_pareto_table(totals: Dict[str, Optional[float]]) -> pd.DataFrame:
    """Susun tabel Pareto dari peta kategori -> kWh.

    Kategori bernilai ``None`` (terkunci, belum ada instrumen) dibuang: batang
    0 kWh akan terbaca "sudah dicek, aman", justru kebalikan dari maksudnya.

    Persentase dihitung terhadap total rugi POSITIF, supaya residual negatif
    (string melebihi ekspektasi) tidak membuat pembagi mengecil atau nol.
    """
    rows = [
        {"category": cat, "loss_kwh": float(val)}
        for cat, val in totals.items()
        if val is not None
    ]
    table = pd.DataFrame(rows, columns=["category", "loss_kwh"])
    # kind="stable": quicksort default pandas tidak stabil, jadi kategori
    # yang seri (loss_kwh sama) bisa bertukar urutan antar run -- ambang
    # vital-few dan urutan chart harus deterministik.
    table = table.sort_values(
        "loss_kwh", ascending=False, kind="stable"
    ).reset_index(drop=True)

    denom = float(table.loc[table["loss_kwh"] > 0.0, "loss_kwh"].sum())
    if denom <= 0.0:
        table["pct"] = 0.0
    else:
        table["pct"] = table["loss_kwh"] / denom * 100.0

    table["actionable"] = ~table["category"].isin(NON_ACTIONABLE)
    # cum_pct dan ambang 80% dihitung hanya atas baris actionable: di v1
    # `unexplained` menyerap shading, low-irradiance, microcrack, bifacial
    # dan ground-fault sekaligus, jadi residual > 80% adalah keadaan YANG
    # DIHARAPKAN, bukan yang patologis. Mengumulasikan seluruh baris
    # (termasuk unexplained) membuat ambang 80% habis oleh unexplained
    # sendiri sebelum kategori actionable manapun sempat dipertimbangkan --
    # vital_few jadi kosong permanen. Penyebutnya juga rugi actionable
    # positif, bukan total rugi: kalau tidak, porsi non-actionable > 20%
    # membuat kumulatif tak pernah mencapai 80% dan SEMUA kategori actionable
    # jadi vital-few (2026-09-28). Baris non-actionable tidak menambah apa
    # pun ke kumulatif, jadi cum_pct-nya sama dengan baris actionable
    # terakhir sebelum dia -- tetap monoton untuk chart.
    actionable_loss = table["loss_kwh"].where(
        table["actionable"] & (table["loss_kwh"] > 0.0), 0.0,
    )
    actionable_denom = float(actionable_loss.sum())
    if actionable_denom <= 0.0:
        table["cum_pct"] = 0.0
    else:
        table["cum_pct"] = actionable_loss.cumsum() / actionable_denom * 100.0

    # Vital few = kategori actionable yang dapat ditindak sampai kumulatif
    # (actionable-only) menembus 80%. Baris pertama yang menembus ambang ikut
    # masuk (konvensi Pareto).
    crossed = table["cum_pct"] >= VITAL_FEW_THRESHOLD_PCT
    first_crossing = crossed.idxmax() if crossed.any() else len(table) - 1
    within = table.index <= first_crossing
    table["vital_few"] = within & table["actionable"] & (table["loss_kwh"] > 0.0)

    return table[PARETO_COLUMNS]
