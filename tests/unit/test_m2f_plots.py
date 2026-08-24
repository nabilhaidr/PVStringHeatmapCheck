"""Tes grafik M2f: mengembalikan Figure, tidak menulis file, tahan input kosong."""
import matplotlib
matplotlib.use("Agg")

import pandas as pd
import pytest
from matplotlib.figure import Figure

from pv_pipeline.m2f.pareto import build_pareto_table
from pv_pipeline.m2f.plots import (
    build_loss_waterfall_figure,
    build_pareto_figure,
    build_waterfall_table,
)


ORDER = ["availability_outage", "dc_cable_fault", "soiling", "unexplained"]


def _totals(**kwargs):
    base = {
        "availability_outage": 0.0,
        "dc_cable_fault": 0.0,
        "soiling": 0.0,
        "microcrack": None,
        "bifacial_underperf": None,
        "unexplained": 0.0,
    }
    base.update(kwargs)
    return base


def test_waterfall_table_keeps_priority_order_not_magnitude_order():
    # WHY: urutan prioritas adalah inti metodenya. Kalau grafik mengurutkan
    # menurut besaran, pembaca kehilangan informasi kenapa soiling kecil
    # (karena fault sudah mengklaim lebih dulu).
    table = build_waterfall_table(
        _totals(availability_outage=1.0, dc_cable_fault=9.0, soiling=2.0),
        attribution_order=ORDER,
    )
    labels = table["label"].tolist()
    assert labels[0] == "E_expected"
    assert labels[-1] == "E_actual"
    assert labels[1:-1] == ORDER


def test_waterfall_marks_terminals_and_losses():
    table = build_waterfall_table(_totals(soiling=5.0), attribution_order=ORDER)
    kinds = table.set_index("label")["kind"]
    assert kinds["E_expected"] == "terminal"
    assert kinds["E_actual"] == "terminal"
    assert kinds["soiling"] == "loss"


def test_waterfall_marks_negative_residual_as_gain():
    # String melebihi ekspektasi -> batang naik, bukan dipaksa nol.
    table = build_waterfall_table(_totals(unexplained=-3.0), attribution_order=ORDER)
    assert table.set_index("label").loc["unexplained", "kind"] == "gain"


def test_waterfall_figure_returns_figure_without_writing_files(tmp_path):
    table = build_waterfall_table(
        _totals(dc_cable_fault=5.0, soiling=3.0), attribution_order=ORDER
    )
    fig = build_loss_waterfall_figure(table, scope="site", period_label="2026-05")
    assert isinstance(fig, Figure)
    assert not list(tmp_path.iterdir())


def test_pareto_figure_returns_figure():
    table = build_pareto_table(_totals(dc_cable_fault=60.0, soiling=40.0))
    fig = build_pareto_figure(table, scope="wb", period_label="WB03 2026-05")
    assert isinstance(fig, Figure)


def test_pareto_figure_reports_residual_share_in_title():
    # WHY: porsi unexplained adalah metrik kualitas model dan harus terlihat
    # tanpa membuka workbook.
    table = build_pareto_table(_totals(dc_cable_fault=30.0, unexplained=70.0))
    fig = build_pareto_figure(table, scope="site", period_label="2026-05")
    assert "70" in fig.axes[0].get_title()


def test_empty_input_returns_figure_instead_of_raising():
    # WHY: grafik dipanggil dari notebook batch; satu WB tanpa data tidak
    # boleh menghentikan seluruh run.
    empty = pd.DataFrame(columns=["label", "delta_kwh", "kind"])
    fig = build_loss_waterfall_figure(empty, scope="site", period_label="2026-05")
    assert isinstance(fig, Figure)

    empty_pareto = pd.DataFrame(
        columns=["category", "loss_kwh", "pct", "cum_pct", "actionable", "vital_few"]
    )
    fig2 = build_pareto_figure(empty_pareto, scope="site", period_label="2026-05")
    assert isinstance(fig2, Figure)
