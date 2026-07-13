from __future__ import annotations

from pathlib import Path

import pandas as pd

from pv_pipeline.string_yield_report import (
    DownloadedInputs,
    SourceManifest,
    download_manifest,
    inventory_drive_folder,
    select_source_manifest,
    validate_drive_folder_url,
)


def download_csv_inputs(
    url_csv: str,
    dates: pd.DatetimeIndex,
    destination: Path,
) -> tuple[SourceManifest, DownloadedInputs]:
    url_csv = validate_drive_folder_url(url_csv)
    csv_items = inventory_drive_folder(url_csv)
    manifest = select_source_manifest(
        csv_items,
        [],
        dates,
        url_csv=url_csv,
        include_poa=False,
    )
    return manifest, download_manifest(manifest, Path(destination))
