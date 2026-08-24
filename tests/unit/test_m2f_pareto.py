"""Tes tabel Pareto: urutan, kumulatif, dan penandaan vital-few."""
import pandas as pd
import pytest

from pv_pipeline.m2f.pareto import PARETO_COLUMNS, build_pareto_table


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


def test_schema_and_descending_order():
    table = build_pareto_table(
        _totals(availability_outage=10.0, dc_cable_fault=50.0, soiling=40.0)
    )
    assert list(table.columns) == PARETO_COLUMNS
    assert table["category"].tolist()[:3] == [
        "dc_cable_fault", "soiling", "availability_outage",
    ]


def test_cumulative_reaches_100_percent():
    table = build_pareto_table(
        _totals(availability_outage=25.0, dc_cable_fault=50.0, soiling=25.0)
    )
    assert table["cum_pct"].iloc[-1] == pytest.approx(100.0)


def test_locked_categories_are_excluded_from_table():
    # WHY: None berarti belum terukur. Menampilkannya sebagai batang 0 kWh
    # akan terbaca "sudah dicek, aman" -- justru kebalikan dari maksudnya.
    table = build_pareto_table(_totals(dc_cable_fault=10.0))
    assert "microcrack" not in table["category"].tolist()
    assert "bifacial_underperf" not in table["category"].tolist()


def test_unexplained_is_present_but_not_actionable():
    # WHY: menyembunyikan residual akan menutupi sinyal bahwa atribusinya lemah.
    table = build_pareto_table(_totals(dc_cable_fault=10.0, unexplained=90.0))
    row = table.set_index("category").loc["unexplained"]
    assert row["loss_kwh"] == pytest.approx(90.0)
    assert bool(row["actionable"]) is False
    assert bool(row["vital_few"]) is False


def test_vital_few_covers_categories_up_to_80_percent():
    table = build_pareto_table(
        _totals(dc_cable_fault=60.0, soiling=25.0, availability_outage=15.0)
    ).set_index("category")
    assert bool(table.loc["dc_cable_fault", "vital_few"]) is True
    assert bool(table.loc["soiling", "vital_few"]) is True
    assert bool(table.loc["availability_outage", "vital_few"]) is False


def test_all_zero_losses_give_zero_pct_not_nan():
    table = build_pareto_table(_totals())
    assert table["pct"].notna().all()
    assert table["pct"].sum() == pytest.approx(0.0)


def test_negative_residual_does_not_break_percentages():
    # String melebihi ekspektasi -> residual negatif. Persentase dihitung
    # terhadap total rugi POSITIF supaya tetap terbaca.
    table = build_pareto_table(_totals(dc_cable_fault=10.0, unexplained=-4.0))
    assert table["pct"].notna().all()
    assert table.set_index("category").loc["dc_cable_fault", "pct"] == pytest.approx(100.0)
