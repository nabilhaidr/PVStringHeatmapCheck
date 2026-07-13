from __future__ import annotations

from datetime import date
import importlib
from pathlib import Path

import pandas as pd

from pv_pipeline.string_yield_report import DownloadedInputs, DriveItem


CSV_URL = (
    "https://drive.google.com/drive/folders/"
    "1f_KrPuqfZJTE5I9cVQiyp65QrHbkF3Iw?usp=sharing"
)


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
