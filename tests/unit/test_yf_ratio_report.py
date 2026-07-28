"""Tests pv_pipeline.yf_ratio_report (rasio Yf tanpa POA).

Fokus test: cuaca HARUS tercoret pada rasio (itu alasan metode ini ada),
string mati tidak boleh mengotori ranking cleaning, dan referensi pre/post
harus memakai string yang TIDAK ikut dibersihkan.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.yf_ratio_report import (
    CLEANING_COLUMNS,
    CLEANING_SHEETS,
    SIBLING_COLUMNS,
    SIBLING_SHEETS,
    build_cleaning_impact_output_path,
    build_sibling_ratio,
    build_sibling_ratio_output_path,
    build_yf_cleaning_impact,
    cleaning_events_to_pv_string,
    load_specific_yield_long,
    split_pv_string,
    verify_cleaning_impact_workbook,
    verify_sibling_ratio_workbook,
    write_cleaning_impact_workbook,
    write_sibling_ratio_workbook,
)


# Cuaca sengaja dibuat liar (0.36x .. 1.14x) supaya kalau metode salah,
# hasilnya pasti meleset jauh dari faktor kotor yang sebenarnya.
WEATHER = [1.0, 0.42, 0.95, 1.12, 0.38, 0.88, 1.05, 0.55, 0.97, 1.10,
           0.45, 1.02, 0.91, 0.36, 1.08, 0.99, 0.62, 1.14, 0.87, 0.51]
BASE_YF = 4.2


def _long_frame(n_days=10, *, soil_factor=None, start="2026-06-01",
                coverage=None):
    """Long df sintetis: 2 inverter x 6 string, cuaca sama untuk semua.

    ``soil_factor``: dict {pv_string: fungsi(day_index) -> faktor} untuk
    string yang menyimpang; sisanya bersih (faktor 1.0).
    """
    soil_factor = soil_factor or {}
    coverage = coverage or {}
    days = pd.date_range(start, periods=n_days, freq="D")
    rows = []
    for index, day in enumerate(days):
        weather = WEATHER[index % len(WEATHER)]
        for inverter in ("WB01-INV01", "WB01-INV02"):
            for pv in range(1, 7):
                pv_string = f"{inverter}-PV{pv}"
                factor = soil_factor.get(pv_string, lambda _: 1.0)(index)
                rows.append({
                    "date": day,
                    "pv_string": pv_string,
                    "inverter_id": inverter,
                    "pv_label": f"PV{pv}",
                    "wb": 1,
                    "yf": BASE_YF * weather * factor,
                    "coverage_pct": coverage.get((pv_string, index), 48.0),
                })
    return pd.DataFrame(rows)


def _events(pv_strings, days):
    rows = []
    for pv_string in pv_strings:
        inverter_id, pv_label, wb = split_pv_string(pv_string)
        for day in days:
            rows.append({
                "date": pd.Timestamp(day),
                "inverter_id": inverter_id,
                "wb": wb,
                "inv": int(inverter_id[-2:]),
                "st": int(pv_label[2:]),
                "pv": int(pv_label[2:]),
                "mppt": None,
            })
    return pd.DataFrame(rows)


class TestSplitPvString:
    def test_parsing_normal(self):
        assert split_pv_string("WB01-INV01-PV3") == ("WB01-INV01", "PV3", 1)
        assert split_pv_string("WB10-INV15-PV28") == ("WB10-INV15", "PV28", 10)

    def test_format_tidak_dikenal_ditolak(self):
        with pytest.raises(ValueError, match="Unexpected pv_string"):
            split_pv_string("INV01-PV1")


class TestLoadSpecificYieldLong:
    def _workbook(self, tmp_path, sheet_name):
        path = tmp_path / "specific_yield.xlsx"
        if sheet_name == "Detail_Harian":
            pd.DataFrame({
                "date": [date(2026, 6, 1), date(2026, 6, 1)],
                "pv_string": ["WB01-INV01-PV1", "WB03-INV02-PV5"],
                "specific_yield_kwh_per_kwp": [4.0, 3.5],
                "coverage_pct": [48.0, 47.0],
            }).to_excel(path, sheet_name=sheet_name, index=False)
        else:
            pd.DataFrame({
                "pv_string": ["WB01-INV01-PV1", "WB03-INV02-PV5"],
                date(2026, 6, 1): [4.0, 3.5],
            }).to_excel(path, sheet_name=sheet_name, index=False)
        return path

    def test_baca_detail_harian(self, tmp_path):
        out = load_specific_yield_long(
            self._workbook(tmp_path, "Detail_Harian")
        )
        assert list(out.columns) == [
            "date", "pv_string", "inverter_id", "pv_label", "wb",
            "yf", "coverage_pct",
        ]
        assert out.loc[0, "inverter_id"] == "WB01-INV01"
        assert out.loc[1, "wb"] == 3
        assert out.loc[0, "yf"] == pytest.approx(4.0)

    def test_fallback_rekap_tanpa_coverage(self, tmp_path):
        out = load_specific_yield_long(
            self._workbook(tmp_path, "Rekap_SpecificYield")
        )
        assert len(out) == 2
        assert out["coverage_pct"].isna().all()

    def test_sheet_tidak_dikenal_ditolak(self, tmp_path):
        path = tmp_path / "lain.xlsx"
        pd.DataFrame({"a": [1]}).to_excel(path, sheet_name="Lain", index=False)
        with pytest.raises(ValueError, match="tidak punya sheet"):
            load_specific_yield_long(path)


class TestSiblingRatio:
    def test_cuaca_tercoret_pada_rasio(self):
        """Inti metode: string kotor 0.85x harus terbaca ~0.85 walau cuaca
        berayun 0.36x-1.14x sepanjang periode."""
        frame = _long_frame(
            n_days=10, soil_factor={"WB01-INV01-PV1": lambda _: 0.85},
        )
        out = build_sibling_ratio(frame).ranking.set_index("pv_string")
        assert out.loc["WB01-INV01-PV1", "ratio_vs_inverter"] == pytest.approx(
            0.85, abs=1e-9,
        )
        assert out.loc["WB01-INV01-PV1", "deficit_vs_inverter_pct"] == (
            pytest.approx(15.0, abs=1e-7)
        )
        # String bersih tetap ~1.0 (tidak ikut turun karena mendung).
        assert out.loc["WB01-INV02-PV3", "ratio_vs_inverter"] == pytest.approx(
            1.0, abs=1e-9,
        )

    def test_string_kotor_jadi_rank_satu(self):
        frame = _long_frame(
            n_days=10,
            soil_factor={
                "WB01-INV01-PV1": lambda _: 0.85,
                "WB01-INV02-PV2": lambda _: 0.93,
            },
        )
        ranking = build_sibling_ratio(frame).ranking
        assert ranking.loc[0, "pv_string"] == "WB01-INV01-PV1"
        assert ranking.loc[0, "rank"] == 1
        assert ranking.loc[0, "status"] == "CLEANING_CANDIDATE"
        assert ranking.loc[1, "pv_string"] == "WB01-INV02-PV2"

    def test_string_mati_diflag_dan_keluar_dari_ranking(self):
        """String ~0 output = mati/offline, bukan kotor -> jangan menempati
        prioritas cleaning teratas."""
        frame = _long_frame(
            n_days=10,
            soil_factor={
                "WB01-INV01-PV6": lambda _: 0.01,
                "WB01-INV01-PV1": lambda _: 0.85,
            },
        )
        ranking = build_sibling_ratio(frame).ranking.set_index("pv_string")
        assert ranking.loc["WB01-INV01-PV6", "status"] == "DEAD_OR_OFFLINE"
        assert pd.isna(ranking.loc["WB01-INV01-PV6", "rank"])
        assert ranking.loc["WB01-INV01-PV1", "rank"] == 1

    def test_rasio_wb_menangkap_soiling_se_inverter(self):
        """Seluruh INV01 kotor 0.9x: rasio vs tetangga se-inverter buta
        (semua turun bersama), tapi rasio vs WB menangkapnya."""
        frame = _long_frame(
            n_days=10,
            soil_factor={
                f"WB01-INV01-PV{pv}": (lambda _: 0.9) for pv in range(1, 7)
            },
        )
        out = build_sibling_ratio(frame).ranking.set_index("pv_string")
        row = out.loc["WB01-INV01-PV3"]
        assert row["ratio_vs_inverter"] == pytest.approx(1.0, abs=1e-9)
        assert row["ratio_vs_wb"] < 0.97

    def test_hari_cakupan_rendah_dibuang(self):
        """String-day dengan cakupan sampel jauh di bawah tetangganya =
        data bolong, bukan soiling -> harus dibuang."""
        frame = _long_frame(
            n_days=10,
            soil_factor={
                "WB01-INV01-PV1": lambda index: 0.5 if index == 3 else 1.0,
            },
            coverage={("WB01-INV01-PV1", 3): 20.0},
        )
        report = build_sibling_ratio(frame)
        row = report.ranking.set_index("pv_string").loc["WB01-INV01-PV1"]
        assert row["n_days"] == 9
        assert row["ratio_vs_inverter"] == pytest.approx(1.0, abs=1e-9)
        assert report.metadata["dropped_low_coverage_rows"] == 1

    def test_min_days_menyaring_string_data_tipis(self):
        frame = _long_frame(n_days=3)
        with pytest.raises(ValueError, match="5 hari"):
            build_sibling_ratio(frame, min_days=5)

    def test_min_siblings_menyaring_inverter_kecil(self):
        frame = _long_frame(n_days=10)
        with pytest.raises(ValueError, match="20 string"):
            build_sibling_ratio(frame, min_siblings=20)

    def test_daily_ratio_pivot_dan_metadata(self):
        report = build_sibling_ratio(_long_frame(n_days=10))
        assert report.daily_ratio.columns[0] == "pv_string"
        assert len(report.daily_ratio) == 12
        assert len(report.daily_ratio.columns) == 11  # pv_string + 10 hari
        assert report.metadata["day_count"] == 10
        assert report.metadata["string_count"] == 12
        assert "blind_spot" in report.metadata

    def test_long_df_kosong_ditolak(self):
        with pytest.raises(ValueError, match="kosong"):
            build_sibling_ratio(pd.DataFrame(columns=["date", "yf"]))


class TestCleaningEventsToPvString:
    def test_mapping_pv_dan_buang_tanpa_mapping(self):
        events = pd.DataFrame({
            "date": [pd.Timestamp("2026-06-10")] * 2,
            "inverter_id": ["WB01-INV01", "WB03-INV05"],
            "wb": [1, 3], "inv": [1, 5], "st": [1, 7],
            "pv": [1, np.nan],   # WB03 tanpa mapping ST->PV
            "mppt": [None, None],
        })
        out = cleaning_events_to_pv_string(events)
        assert list(out["pv_string"]) == ["WB01-INV01-PV1"]

    def test_events_kosong(self):
        out = cleaning_events_to_pv_string(pd.DataFrame())
        assert out.empty
        assert list(out.columns) == ["date", "pv_string", "inverter_id", "st"]


class TestYfCleaningImpact:
    def test_uplift_terdeteksi_dan_cuaca_tercoret(self):
        """PV1 kotor 0.85x sampai hari ke-10 lalu dibersihkan -> uplift
        harus ~17.6% ((1-0.85)/0.85), bukan angka acak ikut cuaca."""
        frame = _long_frame(
            n_days=20,
            soil_factor={
                "WB01-INV01-PV1": lambda index: 0.85 if index < 10 else 1.0,
            },
        )
        report = build_yf_cleaning_impact(
            frame, _events(["WB01-INV01-PV1"], ["2026-06-10"]), window_days=5,
        )
        row = report.impact.iloc[0]
        assert row["pv_string"] == "WB01-INV01-PV1"
        assert row["uplift_pct"] == pytest.approx(15.0 / 85.0 * 100, abs=0.5)
        assert row["soiling_loss_pct"] == pytest.approx(15.0, abs=0.5)
        assert row["status"] == "RECOVERED"
        assert row["reference_mode"] == "WB_UNCLEANED"
        assert row["n_reference_strings"] == 11

    def test_referensi_mengecualikan_string_yang_ikut_dibersihkan(self):
        """Kalau semua string dibersihkan bersamaan, tidak ada kontrol ->
        mode RAW_NO_REFERENCE (jujur), bukan diam-diam memakai referensi
        yang ikut bersih sehingga uplift tampak nol."""
        strings = [
            f"WB01-INV0{inv}-PV{pv}" for inv in (1, 2) for pv in range(1, 7)
        ]
        frame = _long_frame(
            n_days=20,
            soil_factor={
                name: (lambda index: 0.85 if index < 10 else 1.0)
                for name in strings
            },
        )
        report = build_yf_cleaning_impact(
            frame, _events(strings, ["2026-06-10"]),
            window_days=5, min_reference_strings=5,
        )
        assert set(report.impact["reference_mode"]) == {"RAW_NO_REFERENCE"}
        assert (report.impact["n_reference_strings"] == 0).all()

    def test_campaign_dipisah_oleh_gap(self):
        report = build_yf_cleaning_impact(
            _long_frame(n_days=20),
            _events(
                ["WB01-INV01-PV1"],
                ["2026-06-05", "2026-06-06", "2026-06-16"],
            ),
            window_days=4, gap_days=3, min_window_days=2,
        )
        assert len(report.impact) == 2
        starts = sorted(report.impact["cleaning_start"])
        assert starts[0] == pd.Timestamp("2026-06-05")
        assert starts[1] == pd.Timestamp("2026-06-16")

    def test_min_window_days_menyaring_jendela_tipis(self):
        with pytest.raises(ValueError, match="cukup di kedua sisi"):
            build_yf_cleaning_impact(
                _long_frame(n_days=20),
                _events(["WB01-INV01-PV1"], ["2026-06-01"]),
                window_days=5, min_window_days=3,
            )

    def test_rekap_campaign_dan_metadata(self):
        frame = _long_frame(
            n_days=20,
            soil_factor={
                "WB01-INV01-PV1": lambda index: 0.85 if index < 10 else 1.0,
                "WB01-INV01-PV2": lambda index: 0.90 if index < 10 else 1.0,
            },
        )
        report = build_yf_cleaning_impact(
            frame,
            _events(["WB01-INV01-PV1", "WB01-INV01-PV2"], ["2026-06-10"]),
            window_days=5,
        )
        assert list(report.campaigns.columns) == [
            "cleaning_start", "cleaning_end", "n_strings",
            "n_strings_evaluated", "median_uplift_pct",
            "median_soiling_loss_pct", "reference_mode",
        ]
        assert report.campaigns.loc[0, "n_strings_evaluated"] == 2
        assert report.campaigns.loc[0, "median_uplift_pct"] > 5
        assert report.metadata["cleaned_strings"] == 2
        assert report.metadata["evaluated_rows"] == 2
        assert report.metadata["campaign_count"] == 1

    def test_tanpa_event_termapping_ditolak(self):
        events = pd.DataFrame({
            "date": [pd.Timestamp("2026-06-05")],
            "inverter_id": ["WB03-INV05"],
            "wb": [3], "inv": [5], "st": [7], "pv": [np.nan], "mppt": [None],
        })
        with pytest.raises(ValueError, match="mapping ST->PV"):
            build_yf_cleaning_impact(_long_frame(n_days=10), events)


class TestWorkbooks:
    def test_output_path_mengikuti_rentang(self, tmp_path):
        assert build_sibling_ratio_output_path(
            tmp_path, date(2026, 6, 1), date(2026, 6, 30),
        ).name == "sibling_ratio_20260601_20260630.xlsx"
        assert build_cleaning_impact_output_path(
            tmp_path, date(2026, 6, 1), date(2026, 6, 30),
        ).name == "cleaning_impact_yf_20260601_20260630.xlsx"

    def test_sibling_workbook_roundtrip(self, tmp_path):
        report = build_sibling_ratio(
            _long_frame(
                n_days=10, soil_factor={"WB01-INV01-PV1": lambda _: 0.85},
            )
        )
        path = write_sibling_ratio_workbook(tmp_path / "sibling.xlsx", report)
        verify_sibling_ratio_workbook(path)
        assert pd.ExcelFile(path).sheet_names == SIBLING_SHEETS
        ranking = pd.read_excel(path, sheet_name=SIBLING_SHEETS[0])
        assert list(ranking.columns) == SIBLING_COLUMNS
        assert ranking.loc[0, "pv_string"] == "WB01-INV01-PV1"
        assert not list(tmp_path.glob("*.tmp.xlsx"))

    def test_cleaning_workbook_roundtrip(self, tmp_path):
        frame = _long_frame(
            n_days=20,
            soil_factor={
                "WB01-INV01-PV1": lambda index: 0.85 if index < 10 else 1.0,
            },
        )
        report = build_yf_cleaning_impact(
            frame, _events(["WB01-INV01-PV1"], ["2026-06-10"]), window_days=5,
        )
        path = write_cleaning_impact_workbook(
            tmp_path / "cleaning.xlsx", report,
        )
        verify_cleaning_impact_workbook(path)
        assert pd.ExcelFile(path).sheet_names == CLEANING_SHEETS
        impact = pd.read_excel(path, sheet_name=CLEANING_SHEETS[0])
        assert list(impact.columns) == CLEANING_COLUMNS
        assert not list(tmp_path.glob("*.tmp.xlsx"))

    def test_verify_menolak_file_hilang(self, tmp_path):
        with pytest.raises(RuntimeError, match="was not created"):
            verify_sibling_ratio_workbook(tmp_path / "tidak_ada.xlsx")
