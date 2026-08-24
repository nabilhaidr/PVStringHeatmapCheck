"""Grafik M2f: waterfall rugi dan diagram Pareto.

Fungsi mengembalikan ``matplotlib.figure.Figure`` dan TIDAK menulis file --
``savefig`` adalah tanggung jawab pemanggil (notebook). Pola sama dengan
``pv_pipeline/viz.py``.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.figure import Figure

from pv_pipeline.m2f.pareto import VITAL_FEW_THRESHOLD_PCT


WATERFALL_COLUMNS: List[str] = ["label", "delta_kwh", "kind"]

# Palet aman untuk buta warna (Okabe-Ito).
COLOR_TERMINAL = "#0072B2"
COLOR_LOSS = "#D55E00"
COLOR_GAIN = "#009E73"
COLOR_RESIDUAL = "#999999"
COLOR_VITAL = "#D55E00"
COLOR_TRIVIAL = "#56B4E9"
COLOR_CUM = "#0072B2"

_EMPTY_MESSAGE = "tidak ada data"


def build_waterfall_table(
    totals: Dict[str, Optional[float]],
    attribution_order: List[str],
) -> pd.DataFrame:
    """Susun tabel waterfall: terminal, kategori berurutan prioritas, terminal.

    Urutan mengikuti ``attribution_order``, BUKAN besaran. Urutan prioritas
    adalah inti metodenya dan harus terbaca dari grafik.

    ``delta_kwh`` pada baris ``E_expected`` berisi tinggi batang awal (total
    seluruh klaim + residual); pada baris ``E_actual`` berisi 0.0 karena
    tingginya dihitung sebagai sisa berjalan saat menggambar.
    """
    claimed = [
        float(totals[cat])
        for cat in attribution_order
        if totals.get(cat) is not None
    ]
    rows = [{
        "label": "E_expected",
        "delta_kwh": float(sum(claimed)),
        "kind": "terminal",
    }]
    for cat in attribution_order:
        val = totals.get(cat)
        if val is None:
            continue
        val = float(val)
        rows.append({
            "label": cat,
            "delta_kwh": val,
            "kind": "gain" if val < 0.0 else "loss",
        })
    rows.append({"label": "E_actual", "delta_kwh": 0.0, "kind": "terminal"})
    return pd.DataFrame(rows, columns=WATERFALL_COLUMNS)


def _empty_figure(title: str) -> Figure:
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.text(0.5, 0.5, _EMPTY_MESSAGE, ha="center", va="center", fontsize=14)
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    return fig


def build_loss_waterfall_figure(
    waterfall_df: pd.DataFrame,
    *,
    scope: str,
    period_label: str,
) -> Figure:
    """Waterfall dari E_expected ke E_actual, berurutan prioritas atribusi."""
    title = f"Waterfall rugi energi DC - {scope} - {period_label}"
    if waterfall_df is None or waterfall_df.empty:
        return _empty_figure(title)

    labels = waterfall_df["label"].tolist()
    deltas = waterfall_df["delta_kwh"].to_numpy(dtype=float)
    kinds = waterfall_df["kind"].tolist()

    e_expected = float(deltas[0])
    fig, ax = plt.subplots(figsize=(11, 6))

    running = e_expected
    for i, (label, delta, kind) in enumerate(zip(labels, deltas, kinds)):
        if kind == "terminal":
            height = e_expected if label == "E_expected" else running
            ax.bar(i, height, color=COLOR_TERMINAL)
            ax.text(i, height, f"{height:,.0f}", ha="center", va="bottom", fontsize=8)
            continue
        bottom = running - max(delta, 0.0)
        color = COLOR_RESIDUAL if label == "unexplained" else (
            COLOR_GAIN if kind == "gain" else COLOR_LOSS
        )
        hatch = "//" if label == "unexplained" else None
        ax.bar(i, abs(delta), bottom=bottom, color=color, hatch=hatch)
        pct = (delta / e_expected * 100.0) if e_expected else 0.0
        ax.text(
            i, bottom + abs(delta),
            f"{delta:,.0f}\n({pct:.1f}%)",
            ha="center", va="bottom", fontsize=8,
        )
        # Garis konektor supaya rantai pengurangan terbaca.
        ax.plot([i - 0.4, i + 0.4], [running, running], color="0.4", lw=0.8)
        running -= delta

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel("Energi (kWh)")
    ax.set_title(title)
    fig.text(
        0.01, 0.01,
        "microcrack dan bifacial_underperf belum ada instrumen; "
        "kontribusinya terlipat ke dalam unexplained.",
        fontsize=7, color="0.35",
    )
    fig.tight_layout()
    return fig


def build_pareto_figure(
    pareto_df: pd.DataFrame,
    *,
    scope: str,
    period_label: str,
) -> Figure:
    """Batang kWh menurun + garis kumulatif % + garis ambang 80%."""
    base_title = f"Pareto rugi energi DC - {scope} - {period_label}"
    if pareto_df is None or pareto_df.empty:
        return _empty_figure(base_title)

    residual = pareto_df.loc[pareto_df["category"] == "unexplained", "pct"]
    residual_pct = float(residual.iloc[0]) if len(residual) else 0.0
    title = f"{base_title} | unexplained {residual_pct:.0f}%"

    categories = pareto_df["category"].tolist()
    values = pareto_df["loss_kwh"].to_numpy(dtype=float)
    cum = pareto_df["cum_pct"].to_numpy(dtype=float)

    fig, ax = plt.subplots(figsize=(11, 6))
    colors = [
        COLOR_RESIDUAL if cat == "unexplained"
        else (COLOR_VITAL if vital else COLOR_TRIVIAL)
        for cat, vital in zip(categories, pareto_df["vital_few"].tolist())
    ]
    bars = ax.bar(range(len(categories)), values, color=colors)
    for bar, cat in zip(bars, categories):
        if cat == "unexplained":
            bar.set_hatch("//")

    ax.set_xticks(range(len(categories)))
    ax.set_xticklabels(categories, rotation=30, ha="right")
    ax.set_ylabel("Rugi energi (kWh)")

    ax2 = ax.twinx()
    ax2.plot(range(len(categories)), cum, color=COLOR_CUM, marker="o", lw=1.5)
    ax2.axhline(VITAL_FEW_THRESHOLD_PCT, color="0.4", ls="--", lw=1.0)
    ax2.set_ylabel("Kumulatif (%)")
    ax2.set_ylim(0, 105)

    n_vital = int(pareto_df["vital_few"].sum())
    ax.set_title(title)
    ax.annotate(
        f"{n_vital} kategori vital-few (dapat ditindak)",
        xy=(0.02, 0.94), xycoords="axes fraction", fontsize=9,
    )
    fig.tight_layout()
    return fig
