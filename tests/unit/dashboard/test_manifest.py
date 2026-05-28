from __future__ import annotations

from datetime import date

import pandas as pd

from pv_pipeline.dashboard.data.manifest import (
    dashboard_artifact_names,
    enrich_dashboard_manifest,
)


def test_dashboard_artifact_names_match_pipeline_outputs():
    names = dashboard_artifact_names(date(2026, 5, 14))

    assert names == {
        "baseline_csv_name": "2026-05-14.csv",
        "findings_xlsx_name": "m2_findings_20260514.xlsx",
        "findings_jsonl_name": "m2_findings_20260514.jsonl",
    }


def test_enrich_dashboard_manifest_adds_expected_names_ids_and_drive_links():
    manifest = pd.DataFrame(
        {
            "date": ["2026-05-14"],
            "file_csv": ["baseline/2026-05/2026-05-14.csv"],
            "rows_kept": [123],
        }
    )
    drive_files = pd.DataFrame(
        {
            "name": [
                "2026-05-14.csv",
                "m2_findings_20260514.xlsx",
                "m2_findings_20260514.jsonl",
            ],
            "file_id": ["csv-id", "xlsx-id", "jsonl-id"],
        }
    )

    enriched = enrich_dashboard_manifest(manifest, drive_files)
    row = enriched.iloc[0]

    assert row["rows_kept"] == 123
    assert row["baseline_csv_name"] == "2026-05-14.csv"
    assert row["baseline_csv_file_id"] == "csv-id"
    assert row["baseline_csv_url"] == "https://drive.google.com/file/d/csv-id/view"
    assert row["findings_xlsx_name"] == "m2_findings_20260514.xlsx"
    assert row["findings_xlsx_file_id"] == "xlsx-id"
    assert row["findings_xlsx_url"] == "https://drive.google.com/file/d/xlsx-id/view"
    assert row["findings_jsonl_name"] == "m2_findings_20260514.jsonl"
    assert row["findings_jsonl_file_id"] == "jsonl-id"
    assert row["findings_jsonl_url"] == "https://drive.google.com/file/d/jsonl-id/view"


def test_enrich_dashboard_manifest_preserves_manual_links_by_default():
    manifest = pd.DataFrame(
        {
            "date": ["2026-05-14"],
            "baseline_csv_file_id": ["manual-csv-id"],
            "baseline_csv_url": ["https://drive.google.com/file/d/manual-csv-id/view"],
        }
    )
    drive_files = pd.DataFrame({"name": ["2026-05-14.csv"], "file_id": ["new-csv-id"]})

    enriched = enrich_dashboard_manifest(manifest, drive_files)

    assert enriched.loc[0, "baseline_csv_file_id"] == "manual-csv-id"
    assert enriched.loc[0, "baseline_csv_url"].endswith("/manual-csv-id/view")


def test_enrich_dashboard_manifest_leaves_missing_drive_files_blank():
    manifest = pd.DataFrame({"date": ["2026-05-14"]})
    drive_files = pd.DataFrame({"name": ["2026-05-14.csv"], "file_id": ["csv-id"]})

    enriched = enrich_dashboard_manifest(manifest, drive_files)

    assert enriched.loc[0, "baseline_csv_file_id"] == "csv-id"
    assert enriched.loc[0, "findings_xlsx_file_id"] == ""
    assert enriched.loc[0, "findings_jsonl_file_id"] == ""
