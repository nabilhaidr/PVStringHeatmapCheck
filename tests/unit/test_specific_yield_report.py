"""Tests pv_pipeline.specific_yield_report (specific yield IEC 61724-1)."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from pv_pipeline.all_string_yield_report import AllStringYieldData
from pv_pipeline.specific_yield_report import (
    DETAIL_COLUMNS,
    SHEET_ORDER,
    build_specific_yield,
    build_specific_yield_output_path,
    modules_per_string,
    string_capacity_kwp,
    verify_specific_yield_workbook,
    write_specific_yield_workbook,
)


def _daily_row(pv_string, day, yield_kwh, valid=288):
    inverter_id, pv_label = pv_string.rsplit("-", 1)
    return {
        "date": day,
        "pv_string": pv_string,
        "inverter_id": inverter_id,
        "pv_label": pv_label,
        "string_yield_kwh": yield_kwh,
        "valid_power_samples": valid,
        "expected_samples": 288,
        "coverage_pct": valid / 288 * 100,
        "missing_power_samples": 288 - valid,
        "source_csv": f"{day:%Y%m%d}.csv",
        "status": "COMPLETE" if valid == 288 else "PARTIAL",
    }


@pytest.fixture
def report():
    """2 tanggal x 3 string: WB01 (fase 1), WB03 (fase 2), + slot kosong."""
    rows = []
    for day, factor in [(date(2026, 6, 1), 1.0), (date(2026, 6, 2), 0.5)]:
        rows.append(_daily_row("WB01-INV01-PV1", day, 60.0 * factor))
        rows.append(_daily_row("WB03-INV02-PV5", day, 65.0 * factor))
        rows.append(_daily_row("WB03-INV02-PV19", day, float("nan"), valid=0))
    daily = pd.DataFrame(rows)
    summary = daily.pivot(
        index="pv_string", columns="date", values="string_yield_kwh",
    ).reset_index()
    return AllStringYieldData(
        summary=summary,
        daily=daily,
        metadata={"start_date": "2026-06-01", "end_date": "2026-06-02"},
    )


class TestCapacity:
    def test_fase1_wb01_wb02_24_modul(self):
        assert modules_per_string("WB01-INV01-PV1") == 24
        assert modules_per_string("WB02-INV10-PV28") == 24
        # 24 x 625 Wp = 15.0 kWp
        assert string_capacity_kwp("WB01-INV01-PV1") == pytest.approx(15.0)

    def test_fase2_wb03_wb10_26_modul(self):
        assert modules_per_string("WB03-INV02-PV5") == 26
        assert modules_per_string("WB10-INV15-PV19") == 26
        # 26 x 625 Wp = 16.25 kWp
        assert string_capacity_kwp("WB10-INV15-PV19") == pytest.approx(16.25)

    def test_module_wp_override(self):
        assert string_capacity_kwp("WB01-INV01-PV1", 600.0) == pytest.approx(
            24 * 0.6
        )

    def test_format_tidak_dikenal_ditolak(self):
        with pytest.raises(ValueError, match="Unexpected pv_string"):
            modules_per_string("INV01-PV1")


class TestBuildSpecificYield:
    def test_pembagian_kapasitas_per_fase(self, report):
        out = build_specific_yield(report)
        d = out.daily.set_index(["pv_string", "date"])
        # Fase 1: 60 kWh / 15 kWp = 4.0 kWh/kWp
        assert d.loc[("WB01-INV01-PV1", date(2026, 6, 1)),
                     "specific_yield_kwh_per_kwp"] == pytest.approx(4.0)
        # Fase 2: 65 kWh / 16.25 kWp = 4.0 -> beda kapasitas ternormalkan
        assert d.loc[("WB03-INV02-PV5", date(2026, 6, 1)),
                     "specific_yield_kwh_per_kwp"] == pytest.approx(4.0)
        # Hari kedua setengahnya.
        assert d.loc[("WB01-INV01-PV1", date(2026, 6, 2)),
                     "specific_yield_kwh_per_kwp"] == pytest.approx(2.0)

    def test_kolom_dan_kapasitas_tercatat(self, report):
        out = build_specific_yield(report)
        assert list(out.daily.columns) == DETAIL_COLUMNS
        caps = out.daily.set_index("pv_string")["capacity_kwp"]
        assert caps.loc["WB01-INV01-PV1"].unique().tolist() == [15.0]
        assert caps.loc["WB03-INV02-PV5"].unique().tolist() == [16.25]

    def test_summary_pivot_string_x_tanggal(self, report):
        out = build_specific_yield(report)
        assert list(out.summary.columns) == [
            "pv_string", date(2026, 6, 1), date(2026, 6, 2),
        ]
        row = out.summary.set_index("pv_string").loc["WB03-INV02-PV5"]
        assert row[date(2026, 6, 1)] == pytest.approx(4.0)
        assert row[date(2026, 6, 2)] == pytest.approx(2.0)

    def test_yield_nan_tetap_nan(self, report):
        out = build_specific_yield(report)
        empty = out.daily[out.daily["pv_string"] == "WB03-INV02-PV19"]
        assert empty["specific_yield_kwh_per_kwp"].isna().all()

    def test_metadata_mendokumentasikan_rumus(self, report):
        meta = build_specific_yield(report).metadata
        assert meta["standard"] == "IEC 61724-1"
        assert meta["specific_yield_unit"].startswith("kWh/kWp/day")
        assert meta["module_wp"] == 625.0
        assert meta["modules_per_string_wb01_wb02"] == 24
        assert meta["modules_per_string_other_wb"] == 26
        assert meta["specific_yield_string_count"] == 3
        # Metadata sumber (rentang tanggal) diteruskan apa adanya.
        assert meta["start_date"] == "2026-06-01"

    def test_empty_pv_map_membuang_slot_kosong(self, report):
        out = build_specific_yield(
            report, empty_pv_map={"WB03-INV02": [19]},
        )
        assert "WB03-INV02-PV19" not in set(out.daily["pv_string"])
        assert "WB03-INV02-PV19" not in set(out.summary["pv_string"])
        assert out.metadata["excluded_empty_slot_strings"] == 1
        assert out.metadata["specific_yield_string_count"] == 2

    def test_empty_pv_map_kosong_tidak_membuang_apa_pun(self, report):
        out = build_specific_yield(report, empty_pv_map={"WB03-INV02": []})
        assert out.metadata["excluded_empty_slot_strings"] == 0
        assert len(out.daily) == len(report.daily)

    def test_daily_kosong_ditolak(self):
        empty = AllStringYieldData(
            summary=pd.DataFrame(),
            daily=pd.DataFrame(columns=["date", "pv_string"]),
            metadata={},
        )
        with pytest.raises(ValueError, match="nothing to normalise"):
            build_specific_yield(empty)


class TestWorkbook:
    def test_output_path_mengikuti_rentang(self, tmp_path):
        path = build_specific_yield_output_path(
            tmp_path, date(2026, 6, 1), date(2026, 6, 30),
        )
        assert path.name == "specific_yield_20260601_20260630.xlsx"

    def test_write_dan_verify_roundtrip(self, report, tmp_path):
        out = build_specific_yield(report)
        path = write_specific_yield_workbook(
            tmp_path / "specific_yield.xlsx", out,
        )
        verify_specific_yield_workbook(path)

        assert pd.ExcelFile(path).sheet_names == SHEET_ORDER
        recap = pd.read_excel(path, sheet_name=SHEET_ORDER[0])
        assert recap.columns[0] == "pv_string"
        assert len(recap) == 3
        detail = pd.read_excel(path, sheet_name=SHEET_ORDER[1])
        assert list(detail.columns) == DETAIL_COLUMNS
        value = detail.loc[
            detail["pv_string"] == "WB01-INV01-PV1",
            "specific_yield_kwh_per_kwp",
        ].iloc[0]
        assert value == pytest.approx(4.0)
        meta = pd.read_excel(path, sheet_name=SHEET_ORDER[2])
        assert set(meta.columns) == {"key", "value"}
        assert "specific_yield_formula" in set(meta["key"])
        # File sementara tidak tertinggal.
        assert not list(tmp_path.glob("*.tmp.xlsx"))

    def test_verify_menolak_file_hilang(self, tmp_path):
        with pytest.raises(RuntimeError, match="was not created"):
            verify_specific_yield_workbook(tmp_path / "tidak_ada.xlsx")
