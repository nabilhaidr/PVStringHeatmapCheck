"""Tes rekap_m2f: gabungan workbook M2f harian menjadi rekap bulanan.

Workbook sintetis ditulis dengan nama sheet persis seperti
``M2Engine.write_xlsx_multi`` menulisnya untuk M2fLossAttribution (nama
berprefiks > 31 karakter jatuh ke nama artifact polos).
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rekap_m2f import (
    build_daily_calib,
    build_monthly,
    discover_m2f_xlsx,
    load_day,
)

ORDER = [
    "curtailment", "availability_outage", "dc_cable_fault", "soiling",
    "unexplained",
]


def _write_day(
    folder: Path,
    day: str,
    *,
    e_expected: float,
    losses: dict,
    calib_sheet: str = "M2f_BaselineCalib",
    name: str = None,
):
    """Satu workbook harian; ``losses`` = {kategori: kWh} untuk satu string."""
    ymd = day.replace("-", "")
    path = folder / (name or f"m2f_loss_attribution_{ymd}.xlsx")
    per_string = pd.DataFrame([
        {"string_id": "WB03-INV01-PV3", "day": pd.Timestamp(day),
         "category": cat, "loss_kwh": kwh}
        for cat, kwh in losses.items()
    ])
    l_total = float(sum(losses.values()))
    closure = pd.DataFrame([{
        "string_id": "WB03-INV01-PV3", "day": pd.Timestamp(day),
        "l_total_kwh": l_total, "skipped_reason": None,
    }, {
        "string_id": "WB03-INV01-PV6", "day": pd.Timestamp(day),
        "l_total_kwh": np.nan, "skipped_reason": "poa_or_tcell_missing",
    }])
    waterfall = pd.DataFrame([
        {"label": "E_expected", "delta_kwh": e_expected, "kind": "terminal"},
        {"label": "E_actual", "delta_kwh": e_expected - l_total, "kind": "terminal"},
    ])
    calib = pd.DataFrame([{
        "wb_id": "WB03", "g_bifacial": 1.0, "dc_derate": 1.0,
        "measured_ratio": 0.9, "n_calib_string_days": 450,
        "n_strings": 450, "n_days": 1,
    }])
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"x": [1]}).to_excel(writer, sheet_name="Findings", index=False)
        waterfall.to_excel(writer, sheet_name="M2f_Waterfall", index=False)
        per_string.to_excel(writer, sheet_name="M2f_PerString", index=False)
        closure.to_excel(writer, sheet_name="M2f_Closure", index=False)
        calib.to_excel(writer, sheet_name=calib_sheet, index=False)
    return path


def _months(folder):
    days = [load_day(path) for _, path in discover_m2f_xlsx(str(folder))]
    return build_monthly(days, ORDER)


def test_month_sums_days_and_derives_actual_from_closure(tmp_path):
    # WHY: keputusan cleaning dan maintenance dibuat per bulan; Pareto satu
    # hari berisik. E_actual bulanan harus E_expected - L_total yang benar-
    # benar dinilai (Closure), bukan jumlah terminal yang bisa meleset bila
    # sebuah kategori tidak ada di attribution_order.
    _write_day(tmp_path, "2026-08-30", e_expected=100.0,
               losses={"availability_outage": 2.0, "unexplained": 8.0})
    _write_day(tmp_path, "2026-08-31", e_expected=120.0,
               losses={"availability_outage": 1.0, "unexplained": 9.0})
    month = _months(tmp_path)["2026-08"]
    assert month["n_days"] == 2
    assert month["e_expected_kwh"] == pytest.approx(220.0)
    assert month["l_total_kwh"] == pytest.approx(20.0)
    assert month["e_actual_kwh"] == pytest.approx(200.0)
    assert month["totals"]["availability_outage"] == pytest.approx(3.0)
    assert month["totals"]["unexplained"] == pytest.approx(17.0)


def test_category_never_measured_in_month_stays_none(tmp_path):
    # WHY: kontrak tiga-keadaan M2f -- kategori yang tidak pernah diukur di
    # hari mana pun bulan itu harus None, bukan 0.0 ("dicek, bersih"). Satu
    # hari terukur sudah cukup membuatnya terukur di level bulan.
    _write_day(tmp_path, "2026-08-30", e_expected=100.0,
               losses={"soiling": 3.0, "unexplained": 7.0})
    _write_day(tmp_path, "2026-08-31", e_expected=100.0,
               losses={"unexplained": 10.0})
    month = _months(tmp_path)["2026-08"]
    assert month["totals"]["soiling"] == pytest.approx(3.0)
    assert month["totals"]["dc_cable_fault"] is None
    assert month["totals"]["microcrack"] is None
    assert "dc_cable_fault" not in month["pareto"]["category"].tolist()
    assert "dc_cable_fault" not in month["waterfall"]["label"].tolist()


def test_days_are_split_by_calendar_month(tmp_path):
    _write_day(tmp_path, "2026-08-31", e_expected=100.0, losses={"unexplained": 5.0})
    _write_day(tmp_path, "2026-09-01", e_expected=90.0, losses={"unexplained": 4.0})
    months = _months(tmp_path)
    assert sorted(months) == ["2026-08", "2026-09"]
    assert months["2026-09"]["e_expected_kwh"] == pytest.approx(90.0)


def test_range_named_workbook_is_ignored_to_prevent_double_count(tmp_path):
    # WHY: run multi-hari menulis m2f_loss_attribution_YYYYMMDD-YYYYMMDD.xlsx.
    # Bila berkas itu berdampingan dengan berkas harian untuk hari yang sama,
    # menjumlah keduanya menggandakan rugi bulan itu.
    _write_day(tmp_path, "2026-08-31", e_expected=100.0, losses={"unexplained": 5.0})
    _write_day(tmp_path, "2026-08-31", e_expected=100.0, losses={"unexplained": 5.0},
               name="m2f_loss_attribution_20260830-20260831.xlsx")
    found = discover_m2f_xlsx(str(tmp_path))
    assert [day.strftime("%Y-%m-%d") for day, _ in found] == ["2026-08-31"]


def test_daily_calib_collects_measured_ratio_and_skips_old_workbooks(tmp_path):
    # WHY: measured_ratio harian adalah bahan menguji apakah dc_derate
    # mengikuti cuaca (temuan 2026-09-27). Workbook lama (M2f_BifacialCalib,
    # tanpa measured_ratio) tidak boleh menggagalkan rekap.
    _write_day(tmp_path, "2026-08-30", e_expected=100.0, losses={"unexplained": 5.0},
               calib_sheet="M2f_BifacialCalib")
    _write_day(tmp_path, "2026-08-31", e_expected=100.0, losses={"unexplained": 5.0})
    days = [load_day(path) for _, path in discover_m2f_xlsx(str(tmp_path))]
    calib = build_daily_calib(days)
    assert calib["date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-08-31"]
    assert calib["measured_ratio"].iloc[0] == pytest.approx(0.9)
