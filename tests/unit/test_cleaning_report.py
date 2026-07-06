"""Tests pv_pipeline.m2a.cleaning_report (loader rekap cleaning + ST->PV).

Kenapa penting:
- Nomor String di checklist cleaning != nomor PV Huawei untuk WB03-WB10
  (hanya ~12% kebetulan sama). Salah mapping = cleaning event dikaitkan ke
  string yang salah.
- Baris DC cable lintas-inverter (typo desain, mis. WB03INV13ST01 ->
  INV12PV5) harus di-skip, bukan dipetakan diam-diam.
- Klasifikasi interval SRR manual-vs-hujan adalah alasan utama data rekap
  cleaning ini dipakai: tanpa itu semua recovery dianggap cleaning manual.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from pv_pipeline.m2a.cleaning_report import (
    build_st_to_pv,
    classify_cleaning_intervals,
    daily_cleaning_counts,
    load_cleaning_report,
    parse_dc_cable_frame,
)


# --- DC cable mapping ---------------------------------------------------------


def test_parse_dc_cable_frame_dedupes_polarity_and_extracts_mapping():
    frame = pd.DataFrame({
        "src": ["WB03INV01ST01+", "WB03INV01ST01-", "WB05INV19ST27+", "banner"],
        "dst": ["WB03INV01M3PV10", "WB03INV01M3PV10", "WB05INV19M6PV28", "x"],
    })

    m = parse_dc_cable_frame(frame)

    assert len(m) == 2  # +/- jadi satu
    assert m.set_index(["wb", "inv", "st"]).loc[(3, 1, 1), "pv"] == 10
    assert m.set_index(["wb", "inv", "st"]).loc[(5, 19, 27), "mppt"] == 6


def test_parse_dc_cable_frame_skips_cross_inverter_rows_with_warning():
    frame = pd.DataFrame({
        "src": ["WB03INV13ST01+", "WB03INV13ST02+"],
        "dst": ["WB03INV12M2PV5", "WB03INV13M2PV6"],  # baris 1 = typo desain
    })

    with pytest.warns(UserWarning, match="di-skip"):
        m = parse_dc_cable_frame(frame)

    assert len(m) == 1
    assert m.iloc[0]["st"] == 2


# --- Cleaning report loader -----------------------------------------------------


def _write_cleaning_xlsx(path):
    """Layout mirip file asli: 3 baris banner, header row, blok inverter."""
    d1, d2, d3 = datetime(2026, 3, 1), datetime(2026, 3, 2), datetime(2026, 3, 3)
    sts1 = pd.DataFrame([
        ["JADWAL CLEANING", None, None, None, None, None],
        [None, None, None, None, None, None],
        [None, None, None, "MARET 2026", None, None],
        ["Zone", "Inverter", "String", d1, d2, d3],
        ["STS1", "INV-101", "ST01", True, None, None],
        [None, None, "ST02", None, "TRUE", None],
        [None, "INV-102", "ST01", None, None, True],
    ])
    sts3 = pd.DataFrame([
        ["JADWAL CLEANING", None, None, None, None],
        [None, None, None, None, None],
        [None, None, None, None, None],
        ["Zone", "Inverter", "String", d1, d2],
        ["STS3", "INV-315", "ST01", True, None],
        [None, None, "ST02", True, None],
    ])
    with pd.ExcelWriter(path) as writer:
        sts1.to_excel(writer, sheet_name="STS1 (2025)", header=False, index=False)
        sts3.to_excel(writer, sheet_name="STS3", header=False, index=False)
        pd.DataFrame([["bukan checklist"]]).to_excel(
            writer, sheet_name="Rekap", header=False, index=False,
        )
    return str(path)


def test_load_cleaning_report_identity_wb01_and_mapped_wb03(tmp_path):
    path = _write_cleaning_xlsx(tmp_path / "cleaning.xlsx")
    st_to_pv = {(3, 15, 1): (10, 3)}  # WB03-INV15 ST01 -> PV10 (MPPT 3)

    with pytest.warns(UserWarning, match="tanpa mapping"):
        events = load_cleaning_report(path, st_to_pv)

    # WB01: identity ST==PV; inverter ffill; TRUE bool maupun string "TRUE".
    wb01 = events[events["wb"] == 1]
    assert len(wb01) == 3
    assert wb01["inverter_id"].tolist() == [
        "WB01-INV01", "WB01-INV01", "WB01-INV02",
    ]
    assert wb01["pv"].tolist() == [1, 2, 1]
    assert wb01["date"].tolist() == [
        pd.Timestamp("2026-03-01"), pd.Timestamp("2026-03-02"),
        pd.Timestamp("2026-03-03"),
    ]

    # WB03: ST01 termap ke PV10; ST02 tak ada mapping -> pv NaN (warned).
    wb03 = events[events["wb"] == 3].set_index("st")
    assert wb03.loc[1, "inverter_id"] == "WB03-INV15"
    assert wb03.loc[1, "pv"] == 10
    assert wb03.loc[1, "mppt"] == 3
    assert pd.isna(wb03.loc[2, "pv"])


def test_build_st_to_pv_and_daily_counts(tmp_path):
    cable = pd.DataFrame(
        [{"wb": 3, "inv": 15, "st": 1, "mppt": 3, "pv": 10}]
    )
    assert build_st_to_pv(cable) == {(3, 15, 1): (10, 3)}

    path = _write_cleaning_xlsx(tmp_path / "cleaning.xlsx")
    with pytest.warns(UserWarning):
        events = load_cleaning_report(path, build_st_to_pv(cable))
    counts = daily_cleaning_counts(events)

    # 2026-03-01: WB01 ST01 + WB03 ST01 + WB03 ST02 = 3 string.
    assert counts[pd.Timestamp("2026-03-01")] == 3.0
    assert counts[pd.Timestamp("2026-03-03")] == 1.0


# --- wb_filter (analysis run per kelompok WB) -----------------------------------


def test_parse_wb_filter_normalizes_labels():
    from pv_pipeline.m2a.soiling import _parse_wb_filter

    assert _parse_wb_filter(["WB01", "wb02", 3]) == {1, 2, 3}
    assert _parse_wb_filter([]) is None
    assert _parse_wb_filter(None) is None


def test_load_manual_cleaning_wb_filter_limits_events(tmp_path):
    from pv_pipeline.m2a.soiling import _load_manual_cleaning

    path = _write_cleaning_xlsx(tmp_path / "cleaning.xlsx")

    with pytest.warns(UserWarning):
        events, daily = _load_manual_cleaning(path, "", wb_filter={1})

    # Hanya WB01: events WB03 tidak boleh mengklasifikasi interval kelompok WB01.
    assert set(events["wb"]) == {1}
    assert daily[pd.Timestamp("2026-03-01")] == 1.0  # tanpa filter = 3 string


# --- Klasifikasi interval SRR ---------------------------------------------------


def test_classify_cleaning_intervals_manual_rain_unknown():
    intervals = pd.DataFrame({
        "start": pd.to_datetime([
            "2026-03-01",  # manual saja
            "2026-04-01",  # hujan saja
            "2026-05-01",  # manual + hujan
            "2026-06-01",  # tidak ada -> unknown
        ]),
    })
    manual = pd.Series(
        [12.0, 5.0],
        index=pd.DatetimeIndex(["2026-03-02", "2026-05-01"]),
    )
    precip = pd.Series(
        [8.0, 3.0, 10.0, 0.4],
        index=pd.DatetimeIndex(
            ["2026-03-20", "2026-04-02", "2026-05-02", "2026-06-01"]
        ),
    )

    out = classify_cleaning_intervals(
        intervals, manual, precip, window_days=3, precip_threshold_mm=1.0,
    )

    assert out["likely_cause"].tolist() == [
        "manual", "rain", "manual+rain", "unknown",
    ]
    # 0.4 mm di bawah threshold 1.0 -> bukan rain.
    assert out.loc[3, "precip_mm_window"] == pytest.approx(0.4)
    assert out.loc[0, "manual_strings_cleaned_window"] == 12.0
