from datetime import date
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.string_yield_report import (
    DriveItem,
    SourceManifest,
    _extract_string_power,
    build_report_data,
    download_manifest,
    parse_date_range,
    parse_inventory_json,
    parse_string_selection,
    select_source_manifest,
    validate_drive_folder_url,
)


def test_parse_string_selection_normalizes_case_and_pv_number():
    got = parse_string_selection("wb05-inv01-pv03")
    assert (got.canonical, got.wb_id, got.inverter_id, got.pv_number, got.pv_label) == (
        "WB05-INV01-PV3",
        "WB05",
        "WB05-INV01",
        3,
        "PV3",
    )


@pytest.mark.parametrize("value", ["WB5-INV01-PV03", "WB05-INV01-PV0", "WB05-INV01-PV29"])
def test_parse_string_selection_rejects_invalid_format_and_range(value):
    with pytest.raises(ValueError):
        parse_string_selection(value)


def test_parse_date_range_is_inclusive_and_rejects_reverse():
    dates = parse_date_range("2026-12-31", "2027-01-02")
    assert [d.date() for d in dates] == [date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 2)]
    with pytest.raises(ValueError, match="START_DATE"):
        parse_date_range("2027-01-02", "2026-12-31")


def test_drive_url_and_inventory_json_contract():
    url = "https://drive.google.com/drive/folders/folder-id?usp=sharing"
    assert validate_drive_folder_url(url) == url
    items = parse_inventory_json('[{"url":"u1","path":"root/20260501.csv"}]')
    assert items == [DriveItem(url="u1", path="root/20260501.csv")]
    with pytest.raises(ValueError):
        validate_drive_folder_url("https://example.com/folder-id")
    with pytest.raises(ValueError):
        parse_inventory_json('[{"url":"u1"}]')


def test_select_inventory_matches_only_requested_csv_dates_and_poa_years():
    dates = parse_date_range("2026-12-31", "2027-01-01")
    csv_items = [
        DriveItem("csv-old", "root/20261230.csv"),
        DriveItem("csv-a", "root/20261231.csv"),
    ]
    poa_items = [
        DriveItem("poa-26", "root/POA PLTS IKN 2026.xlsx"),
        DriveItem("poa-27", "root/POA PLTS IKN 2027.xlsx"),
        DriveItem("other", "root/other.xlsx"),
    ]
    got = select_source_manifest(csv_items, poa_items, dates)
    assert got.csv_by_date == {date(2026, 12, 31): csv_items[1]}
    assert got.missing_csv_dates == [date(2027, 1, 1)]
    assert got.poa_by_year == {2026: poa_items[0], 2027: poa_items[1]}
    assert got.missing_poa_years == []


def test_select_inventory_rejects_duplicate_requested_basename():
    dates = parse_date_range("2026-05-01", "2026-05-01")
    csv_items = [
        DriveItem("csv-a", "root-a/20260501.csv"),
        DriveItem("csv-b", "root-b/20260501.csv"),
    ]

    with pytest.raises(ValueError, match="Duplicate requested basename"):
        select_source_manifest(csv_items, [], dates)


def test_download_manifest_downloads_only_selected_items(monkeypatch, tmp_path):
    calls = []

    def fake_download(*, url, output, quiet):
        calls.append((url, Path(output).name, quiet))
        Path(output).write_text("synthetic", encoding="utf-8")
        return output

    monkeypatch.setitem(sys.modules, "gdown", SimpleNamespace(download=fake_download))
    manifest = SourceManifest(
        csv_by_date={date(2026, 5, 1): DriveItem("csv-url", "root/20260501.csv")},
        poa_by_year={2026: DriveItem("poa-url", "root/POA PLTS IKN 2026.xlsx")},
        missing_csv_dates=[],
        missing_poa_years=[],
    )
    got = download_manifest(manifest, tmp_path)
    assert calls == [
        ("csv-url", "20260501.csv", False),
        ("poa-url", "POA PLTS IKN 2026.xlsx", False),
    ]
    assert got.download_errors == {}
    assert got.csv_by_date[date(2026, 5, 1)].is_file()
    assert got.poa_by_year[2026].is_file()


def _write_geometry(tmp_path, mapping="  WS-2: [WB05]"):
    path = tmp_path / "site_geometry.yaml"
    path.write_text(
        f"ws_to_wb:\n{mapping}\npyranometer:\n  sheet: POA PLTS IKN\n",
        encoding="utf-8",
    )
    return path


def _write_poa(path, timestamps, ws2, avg):
    frame = pd.DataFrame({
        "Date time": timestamps,
        **{
            f"POA Irradiance (W/m2) WS {number}":
                ws2 if number == 2 else np.full(len(timestamps), 100.0)
            for number in range(1, 6)
        },
        "Rata-rata WS 1 - WS 5": avg,
    })
    frame.to_excel(path, sheet_name="POA PLTS IKN", index=False)


def test_extract_power_prefers_explicit_power_column():
    df = pd.DataFrame({
        "Start Time": ["2026-05-01 00:00"],
        "Inverter_ID": ["wb05-inv01"],
        "PV3 Power(kW)": [7.5],
        "PV3 Input Voltage(V)": [1000],
        "PV3 Input Current(A)": [10],
    })

    series, diagnostics = _extract_string_power(
        df, parse_string_selection("WB05-INV01-PV03")
    )

    assert series.iloc[0] == pytest.approx(7.5)
    assert diagnostics["power_source"] == "direct"


def test_extract_power_falls_back_case_insensitively_and_deduplicates_mean():
    df = pd.DataFrame({
        "start time": ["2026-05-01 00:00", "2026-05-01 00:00", "2026-05-01 00:10"],
        "inverter_id": ["WB05-INV01"] * 3,
        "pv03 INPUT voltage(v)": [1000, 1000, 1000],
        "PV3 input CURRENT(a)": [8, 12, 10],
    })

    series, diagnostics = _extract_string_power(
        df, parse_string_selection("WB05-INV01-PV3")
    )

    assert list(series) == pytest.approx([10.0, 10.0])
    assert diagnostics["duplicate_rows"] == 1
    assert diagnostics["power_source"] == "voltage_current"


def test_daily_yield_uses_only_observed_slots_and_distinguishes_statuses(tmp_path):
    dates = parse_date_range("2026-05-01", "2026-05-03")
    csv1 = tmp_path / "20260501.csv"
    csv2 = tmp_path / "20260502.csv"
    pd.DataFrame({
        "Start Time": pd.date_range("2026-05-01", periods=12, freq="5min"),
        "Inverter_ID": "WB05-INV01",
        "PV3 Power(kW)": 10.0,
    }).to_csv(csv1, index=False)
    pd.DataFrame({
        "Start Time": ["2026-05-02 00:00"],
        "Inverter_ID": ["WB05-INV02"],
        "PV3 Power(kW)": [10.0],
    }).to_csv(csv2, index=False)

    report = build_report_data(
        {date(2026, 5, 1): csv1, date(2026, 5, 2): csv2},
        {},
        parse_string_selection("WB05-INV01-PV03"),
        dates,
        Path("config/site_geometry.yaml"),
    )

    assert len(report.five_minute) == 3 * 288
    assert report.five_minute["power_kw"].notna().sum() == 12
    assert report.daily["status"].tolist() == ["PARTIAL", "NO_STRING_DATA", "MISSING_CSV"]
    assert report.daily.loc[0, "string_yield_kwh"] == pytest.approx(10.0)
    assert report.daily.loc[1:, "string_yield_kwh"].isna().all()
    assert list(report.five_minute.columns) == [
        "timestamp", "inverter_id", "pv_string", "power_kw", "poa_wm2",
        "source_csv", "poa_source", "data_status",
    ]
    assert list(report.daily.columns) == [
        "date", "string_yield_kwh", "valid_power_samples", "expected_samples",
        "coverage_pct", "missing_power_samples", "poa_valid_samples", "source_csv", "status",
    ]


def test_complete_day_requires_exactly_288_valid_samples(tmp_path):
    csv_path = tmp_path / "20260501.csv"
    pd.DataFrame({
        "Start Time": pd.date_range("2026-05-01", periods=288, freq="5min"),
        "Inverter_ID": "WB05-INV01",
        "PV3 Power(kW)": 1.0,
    }).to_csv(csv_path, index=False)

    report = build_report_data(
        {date(2026, 5, 1): csv_path}, {},
        parse_string_selection("WB05-INV01-PV3"),
        parse_date_range("2026-05-01", "2026-05-01"),
        Path("config/site_geometry.yaml"),
    )

    assert report.daily.loc[0, "status"] == "COMPLETE"
    assert report.daily.loc[0, "coverage_pct"] == pytest.approx(100.0)


def test_wrong_file_date_rows_are_dropped_and_warned(tmp_path):
    csv_path = tmp_path / "20260501.csv"
    pd.DataFrame({
        "Start Time": ["2026-05-01 00:00", "2026-05-02 00:00"],
        "Inverter_ID": "WB05-INV01",
        "PV3 Power(kW)": [2.0, 99.0],
    }).to_csv(csv_path, index=False)

    report = build_report_data(
        {date(2026, 5, 1): csv_path}, {},
        parse_string_selection("WB05-INV01-PV3"),
        parse_date_range("2026-05-01", "2026-05-01"),
        Path("config/site_geometry.yaml"),
    )

    assert report.five_minute["power_kw"].notna().sum() == 1
    assert report.metadata["wrong_date_rows"] == {"20260501.csv": 1}
    assert "Rows outside their YYYYMMDD.csv date were dropped." in report.metadata["warnings"]


def test_negative_power_is_retained_and_warned(tmp_path):
    csv_path = tmp_path / "20260501.csv"
    pd.DataFrame({
        "Start Time": ["2026-05-01 00:00"],
        "Inverter_ID": ["WB05-INV01"],
        "PV3 Power(kW)": [-3.0],
    }).to_csv(csv_path, index=False)

    report = build_report_data(
        {date(2026, 5, 1): csv_path}, {},
        parse_string_selection("WB05-INV01-PV3"),
        parse_date_range("2026-05-01", "2026-05-01"),
        Path("config/site_geometry.yaml"),
    )

    assert report.five_minute.loc[0, "power_kw"] == pytest.approx(-3.0)
    assert report.metadata["negative_power_samples"] == 1
    assert "Negative power samples were retained." in report.metadata["warnings"]


def test_missing_five_minute_slot_stays_nan(tmp_path):
    csv_path = tmp_path / "20260501.csv"
    pd.DataFrame({
        "Start Time": ["2026-05-01 00:00", "2026-05-01 00:10"],
        "Inverter_ID": "WB05-INV01",
        "PV3 Power(kW)": [1.0, 1.0],
    }).to_csv(csv_path, index=False)

    report = build_report_data(
        {date(2026, 5, 1): csv_path}, {},
        parse_string_selection("WB05-INV01-PV3"),
        parse_date_range("2026-05-01", "2026-05-01"),
        Path("config/site_geometry.yaml"),
    )

    missing = report.five_minute.loc[
        report.five_minute["timestamp"] == pd.Timestamp("2026-05-01 00:05"), "power_kw"
    ]
    assert missing.isna().all()


def test_manage_object_fallback_normalizes_mixed_case_before_conversion():
    frame = pd.DataFrame({
        "Start Time": ["2026-05-01 00:00"],
        "manageobject": ["root/inv_a_234_ikn"],
        "PV3 Power(kW)": [4.0],
    })

    series, diagnostics = _extract_string_power(
        frame, parse_string_selection("WB02-INV34-PV3")
    )

    assert series.iloc[0] == pytest.approx(4.0)
    assert diagnostics["power_source"] == "direct"


def test_no_readable_csv_raises_loudly(tmp_path):
    with pytest.raises(RuntimeError, match="No requested CSV could be read"):
        build_report_data(
            {date(2026, 5, 1): tmp_path / "missing.csv"}, {},
            parse_string_selection("WB05-INV01-PV3"),
            parse_date_range("2026-05-01", "2026-05-01"),
            Path("config/site_geometry.yaml"),
        )


def test_no_valid_selected_string_sample_across_readable_csvs_raises(tmp_path):
    csv_path = tmp_path / "20260501.csv"
    pd.DataFrame({
        "Start Time": ["2026-05-01 00:00"],
        "Inverter_ID": ["WB05-INV02"],
        "PV3 Power(kW)": [4.0],
    }).to_csv(csv_path, index=False)

    with pytest.raises(RuntimeError, match="No valid power sample found for WB05-INV01-PV3"):
        build_report_data(
            {date(2026, 5, 1): csv_path}, {},
            parse_string_selection("WB05-INV01-PV3"),
            parse_date_range("2026-05-01", "2026-05-01"),
            Path("config/site_geometry.yaml"),
        )


def test_poa_uses_mapped_ws_and_labels_only_gap_fallback_as_avg(tmp_path):
    csv_path = tmp_path / "20260501.csv"
    poa_path = tmp_path / "POA PLTS IKN 2026.xlsx"
    pd.DataFrame({
        "Start Time": ["2026-05-01 00:00"],
        "Inverter_ID": ["WB05-INV01"],
        "PV3 Power(kW)": [4.0],
    }).to_csv(csv_path, index=False)
    _write_poa(
        poa_path,
        pd.date_range("2026-05-01", periods=2, freq="5min"),
        [500.0, np.nan],
        [600.0, 650.0],
    )
    manifest = SourceManifest(
        csv_by_date={}, poa_by_year={},
        missing_csv_dates=[date(2026, 5, 2)], missing_poa_years=[2027],
        url_csv="https://drive.google.com/drive/folders/csv",
        url_poa="https://drive.google.com/drive/folders/poa",
        csv_inventory_count=4, poa_inventory_count=2,
    )

    report = build_report_data(
        {date(2026, 5, 1): csv_path}, {2026: poa_path},
        parse_string_selection("WB05-INV01-PV3"),
        parse_date_range("2026-05-01", "2026-05-01"),
        _write_geometry(tmp_path),
        source_manifest=manifest,
        download_errors={"failed.csv": "HTTPError: denied"},
    )

    assert report.five_minute.loc[:1, "poa_wm2"].tolist() == pytest.approx([500.0, 650.0])
    assert report.five_minute.loc[:1, "poa_source"].tolist() == ["WS-2", "avg"]
    assert report.metadata["mapped_ws"] == "WS-2"
    assert report.metadata["poa_fallback_samples"] == 1
    assert report.metadata["loaded_poa_files"] == ["POA PLTS IKN 2026.xlsx"]
    assert report.metadata["source_url_csv"] == manifest.url_csv
    assert report.metadata["source_url_poa"] == manifest.url_poa
    assert report.metadata["csv_inventory_count"] == 4
    assert report.metadata["inventory_missing_csv_dates"] == ["2026-05-02"]
    assert report.metadata["download_errors"] == {"failed.csv": "HTTPError: denied"}


def test_unmapped_wb_does_not_receive_wholesale_average_poa(tmp_path):
    csv_path = tmp_path / "20260501.csv"
    poa_path = tmp_path / "POA PLTS IKN 2026.xlsx"
    pd.DataFrame({
        "Start Time": ["2026-05-01 00:00"],
        "Inverter_ID": ["WB06-INV01"],
        "PV3 Power(kW)": [4.0],
    }).to_csv(csv_path, index=False)
    _write_poa(poa_path, [pd.Timestamp("2026-05-01 00:00")], [np.nan], [700.0])

    report = build_report_data(
        {date(2026, 5, 1): csv_path}, {2026: poa_path},
        parse_string_selection("WB06-INV01-PV3"),
        parse_date_range("2026-05-01", "2026-05-01"),
        _write_geometry(tmp_path),
    )

    assert report.five_minute["poa_wm2"].isna().all()
    assert report.metadata["poa_fallback_samples"] == 0


def test_unreadable_poa_year_isolated_from_power_yield(tmp_path):
    csv_path = tmp_path / "20260501.csv"
    missing_poa = tmp_path / "POA PLTS IKN 2026.xlsx"
    pd.DataFrame({
        "Start Time": ["2026-05-01 00:00"],
        "Inverter_ID": ["WB05-INV01"],
        "PV3 Power(kW)": [6.0],
    }).to_csv(csv_path, index=False)

    report = build_report_data(
        {date(2026, 5, 1): csv_path}, {2026: missing_poa},
        parse_string_selection("WB05-INV01-PV3"),
        parse_date_range("2026-05-01", "2026-05-01"),
        _write_geometry(tmp_path),
    )

    assert report.daily.loc[0, "string_yield_kwh"] == pytest.approx(0.5)
    assert report.five_minute["poa_wm2"].isna().all()
    assert "POA PLTS IKN 2026.xlsx" in report.metadata["poa_read_errors"]
