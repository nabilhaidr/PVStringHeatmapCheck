from datetime import date
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from pv_pipeline.string_yield_report import (
    DriveItem,
    SourceManifest,
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
