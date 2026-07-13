from __future__ import annotations

from datetime import date
import importlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.string_yield_report import (
    DownloadedInputs,
    DriveItem,
    SourceManifest,
)


CSV_URL = (
    "https://drive.google.com/drive/folders/"
    "1f_KrPuqfZJTE5I9cVQiyp65QrHbkF3Iw?usp=sharing"
)


def _write_csv(path, frame):
    frame.to_csv(path, index=False)
    return path


def test_download_csv_inputs_inventories_one_folder_and_selects_dates(
    monkeypatch,
    tmp_path,
):
    report_module = importlib.import_module(
        "pv_pipeline.all_string_yield_report"
    )
    calls = []
    items = [
        DriveItem(
            "https://drive.google.com/uc?id=a",
            "nested/20260501.csv",
        ),
        DriveItem("https://drive.google.com/uc?id=b", "20260502.csv"),
        DriveItem("https://drive.google.com/uc?id=c", "20260503.csv"),
    ]

    def fake_inventory(url):
        calls.append(("inventory", url))
        return items

    def fake_download(manifest, destination):
        calls.append(("download", manifest, Path(destination)))
        return DownloadedInputs({}, {}, {})

    monkeypatch.setattr(
        report_module,
        "inventory_drive_folder",
        fake_inventory,
    )
    monkeypatch.setattr(
        report_module,
        "download_manifest",
        fake_download,
    )
    dates = pd.date_range("2026-05-01", "2026-05-02", freq="D")

    manifest, inputs = report_module.download_csv_inputs(
        CSV_URL,
        dates,
        tmp_path,
    )

    assert list(manifest.csv_by_date) == [
        date(2026, 5, 1),
        date(2026, 5, 2),
    ]
    assert manifest.poa_by_year == {}
    assert manifest.missing_poa_years == []
    assert manifest.url_poa == ""
    assert manifest.csv_inventory_count == 3
    assert inputs == DownloadedInputs({}, {}, {})
    assert [entry[0] for entry in calls] == ["inventory", "download"]


def test_daily_yield_builds_union_once_and_leaves_missing_values_blank(
    tmp_path,
):
    report_module = importlib.import_module(
        "pv_pipeline.all_string_yield_report"
    )
    stamps = pd.date_range(
        "2026-05-01 00:00",
        periods=12,
        freq="5min",
    )
    day_one = pd.concat([
        pd.DataFrame({
            "Start Time": stamps,
            "Inverter_ID": "WB02-INV10",
            "PV1 Power(kW)": 20.0,
            "PV2 Voltage(V)": np.nan,
            "PV2 Current(A)": np.nan,
        }),
        pd.DataFrame({
            "Start Time": stamps,
            "Inverter_ID": "WB02-INV02",
            "PV1 Power(kW)": 10.0,
            "PV2 Voltage(V)": 500.0,
            "PV2 Current(A)": 10.0,
        }),
    ], ignore_index=True)
    day_two = day_one.loc[
        day_one["Inverter_ID"] == "WB02-INV02"
    ].copy()
    day_two["Start Time"] = pd.date_range(
        "2026-05-02 00:00",
        periods=12,
        freq="5min",
    )
    paths = {
        date(2026, 5, 1): _write_csv(
            tmp_path / "20260501.csv",
            day_one,
        ),
        date(2026, 5, 2): _write_csv(
            tmp_path / "20260502.csv",
            day_two,
        ),
    }
    dates = pd.date_range("2026-05-01", "2026-05-03", freq="D")

    result = report_module.build_all_string_daily_yield(paths, dates)

    expected_strings = [
        "WB02-INV02-PV1",
        "WB02-INV02-PV2",
        "WB02-INV10-PV1",
    ]
    assert list(result.summary.columns) == ["date", *expected_strings]
    assert len(result.daily) == 9
    indexed = result.daily.set_index(["date", "pv_string"])
    assert indexed.loc[
        (date(2026, 5, 1), "WB02-INV02-PV1"),
        "string_yield_kwh",
    ] == pytest.approx(10.0)
    assert indexed.loc[
        (date(2026, 5, 1), "WB02-INV02-PV2"),
        "string_yield_kwh",
    ] == pytest.approx(5.0)
    assert indexed.loc[
        (date(2026, 5, 2), "WB02-INV10-PV1"),
        "status",
    ] == "NO_STRING_DATA"
    assert indexed.loc[
        (date(2026, 5, 3), "WB02-INV02-PV1"),
        "status",
    ] == "MISSING_CSV"
    assert pd.isna(result.summary.loc[2, "WB02-INV02-PV1"])


def test_direct_power_does_not_fall_back_when_values_are_empty(tmp_path):
    report_module = importlib.import_module(
        "pv_pipeline.all_string_yield_report"
    )
    day_one = pd.DataFrame({
        "Start Time": ["2026-05-01 00:00"],
        "Inverter_ID": ["WB01-INV01"],
        "PV1 Power(kW)": [4.0],
        "PV1 Voltage(V)": [500.0],
        "PV1 Current(A)": [10.0],
    })
    day_two = day_one.copy()
    day_two["Start Time"] = "2026-05-02 00:00"
    day_two["PV1 Power(kW)"] = np.nan
    paths = {
        date(2026, 5, 1): _write_csv(
            tmp_path / "20260501.csv",
            day_one,
        ),
        date(2026, 5, 2): _write_csv(
            tmp_path / "20260502.csv",
            day_two,
        ),
    }

    result = report_module.build_all_string_daily_yield(
        paths,
        pd.date_range("2026-05-01", "2026-05-02", freq="D"),
    )

    second = result.daily.loc[
        result.daily["date"] == date(2026, 5, 2)
    ].iloc[0]
    assert second["status"] == "NO_STRING_DATA"
    assert pd.isna(second["string_yield_kwh"])


def test_read_error_is_distinct_and_no_usable_range_fails_loudly(
    tmp_path,
):
    report_module = importlib.import_module(
        "pv_pipeline.all_string_yield_report"
    )
    valid = _write_csv(
        tmp_path / "20260501.csv",
        pd.DataFrame({
            "Start Time": ["2026-05-01 00:00"],
            "Inverter_ID": ["WB01-INV01"],
            "PV1 Power(kW)": [6.0],
        }),
    )
    broken = _write_csv(
        tmp_path / "20260502.csv",
        pd.DataFrame({"bad": [1]}),
    )
    dates = pd.date_range("2026-05-01", "2026-05-02", freq="D")

    result = report_module.build_all_string_daily_yield(
        {
            date(2026, 5, 1): valid,
            date(2026, 5, 2): broken,
        },
        dates,
    )

    assert result.daily.loc[
        result.daily["date"] == date(2026, 5, 2),
        "status",
    ].tolist() == ["CSV_READ_ERROR"]
    assert "20260502.csv" in result.metadata["csv_read_errors"]
    assert result.metadata["missing_csv_dates"] == []
    with pytest.raises(
        RuntimeError,
        match="No requested CSV could be read",
    ):
        report_module.build_all_string_daily_yield(
            {date(2026, 5, 2): broken},
            dates[1:],
        )


def test_quality_diagnostics_drop_off_grid_and_keep_negative_power(
    tmp_path,
):
    report_module = importlib.import_module(
        "pv_pipeline.all_string_yield_report"
    )
    frame = pd.DataFrame({
        "Start Time": [
            "2026-05-01 00:00:00",
            "2026-05-01 00:00:00",
            "2026-05-01 00:05:00",
            "2026-05-01 00:10:30",
            "2026-05-02 00:10:00",
        ],
        "Inverter_ID": "WB01-INV01",
        "PV1 Power(kW)": [5.0, 7.0, -3.0, 10.0, 99.0],
    })
    path = _write_csv(tmp_path / "20260501.csv", frame)

    result = report_module.build_all_string_daily_yield(
        {date(2026, 5, 1): path},
        pd.date_range("2026-05-01", periods=1),
    )

    row = result.daily.iloc[0]
    assert row["valid_power_samples"] == 2
    assert row["string_yield_kwh"] == pytest.approx(0.25)
    assert result.metadata["duplicate_rows"] == 1
    assert result.metadata["negative_power_samples"] == 1
    assert result.metadata["wrong_date_rows"]["20260501.csv"] == 1
    assert "Negative power samples were retained." in result.metadata[
        "warnings"
    ]
    assert (
        "Rows outside their YYYYMMDD.csv date were dropped."
        in result.metadata["warnings"]
    )


def test_complete_day_uses_manage_object_fallback(tmp_path):
    report_module = importlib.import_module(
        "pv_pipeline.all_string_yield_report"
    )
    path = _write_csv(
        tmp_path / "20260501.csv",
        pd.DataFrame({
            "Start Time": pd.date_range(
                "2026-05-01 00:00",
                periods=288,
                freq="5min",
            ),
            "ManageObject": "WB01-INV01",
            "PV1 Power(kW)": 1.0,
        }),
    )

    result = report_module.build_all_string_daily_yield(
        {date(2026, 5, 1): path},
        pd.date_range("2026-05-01", periods=1),
    )

    row = result.daily.iloc[0]
    assert row["pv_string"] == "WB01-INV01-PV1"
    assert row["valid_power_samples"] == 288
    assert row["status"] == "COMPLETE"


def test_metadata_preserves_inventory_and_download_diagnostics(tmp_path):
    report_module = importlib.import_module(
        "pv_pipeline.all_string_yield_report"
    )
    path = _write_csv(
        tmp_path / "20260501.csv",
        pd.DataFrame({
            "Start Time": ["2026-05-01 00:00"],
            "Inverter_ID": ["WB01-INV01"],
            "PV1 Power(kW)": [1.0],
        }),
    )
    missing_day = date(2026, 5, 2)
    manifest = SourceManifest(
        csv_by_date={
            date(2026, 5, 1): DriveItem("local", "20260501.csv"),
        },
        poa_by_year={},
        missing_csv_dates=[missing_day],
        missing_poa_years=[],
        url_csv="https://drive.google.com/drive/folders/csv-source",
        csv_inventory_count=472,
    )

    result = report_module.build_all_string_daily_yield(
        {date(2026, 5, 1): path},
        pd.date_range("2026-05-01", "2026-05-02", freq="D"),
        source_manifest=manifest,
        download_errors={"20260502.csv": "RuntimeError: failed"},
    )

    assert result.metadata["csv_inventory_count"] == 472
    assert result.metadata["inventory_missing_csv_dates"] == [
        "2026-05-02"
    ]
    assert result.metadata["download_errors"] == {
        "20260502.csv": "RuntimeError: failed"
    }


def test_no_valid_string_sample_fails_loudly(tmp_path):
    report_module = importlib.import_module(
        "pv_pipeline.all_string_yield_report"
    )
    path = _write_csv(
        tmp_path / "20260501.csv",
        pd.DataFrame({
            "Start Time": ["2026-05-01 00:00"],
            "Inverter_ID": ["WB01-INV01"],
            "PV1 Power(kW)": [np.nan],
        }),
    )

    with pytest.raises(
        RuntimeError,
        match="No valid PV string power sample",
    ):
        report_module.build_all_string_daily_yield(
            {date(2026, 5, 1): path},
            pd.date_range("2026-05-01", periods=1),
        )
