from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

import pandas as pd


STRING_RE = re.compile(r"^(WB\d{2})-(INV\d{2})-PV(\d{1,2})$", re.I)


@dataclass(frozen=True)
class StringSelection:
    canonical: str
    wb_id: str
    inverter_id: str
    pv_number: int
    pv_label: str


@dataclass(frozen=True)
class DriveItem:
    url: str
    path: str


@dataclass
class SourceManifest:
    csv_by_date: dict[date, DriveItem]
    poa_by_year: dict[int, DriveItem]
    missing_csv_dates: list[date]
    missing_poa_years: list[int]
    url_csv: str = ""
    url_poa: str = ""
    csv_inventory_count: int = 0
    poa_inventory_count: int = 0


@dataclass
class DownloadedInputs:
    csv_by_date: dict[date, Path]
    poa_by_year: dict[int, Path]
    download_errors: dict[str, str]


def parse_string_selection(value: str) -> StringSelection:
    match = STRING_RE.fullmatch(str(value).strip())
    if not match:
        raise ValueError("PV_STRING must match WBxx-INVxx-PVxx.")
    wb_id, inv_part, pv_text = match.groups()
    pv_number = int(pv_text)
    if not 1 <= pv_number <= 28:
        raise ValueError("PV number must be in range 1..28.")
    wb_id, inv_part = wb_id.upper(), inv_part.upper()
    inverter_id = f"{wb_id}-{inv_part}"
    return StringSelection(
        canonical=f"{inverter_id}-PV{pv_number}",
        wb_id=wb_id,
        inverter_id=inverter_id,
        pv_number=pv_number,
        pv_label=f"PV{pv_number}",
    )


def parse_date_range(start: str, end: str) -> pd.DatetimeIndex:
    try:
        start_ts = pd.Timestamp(datetime.strptime(start, "%Y-%m-%d"))
        end_ts = pd.Timestamp(datetime.strptime(end, "%Y-%m-%d"))
    except (TypeError, ValueError) as exc:
        raise ValueError("START_DATE and END_DATE must use yyyy-mm-dd.") from exc
    if start_ts > end_ts:
        raise ValueError("START_DATE must be on or before END_DATE.")
    return pd.date_range(start_ts.normalize(), end_ts.normalize(), freq="D")


def validate_drive_folder_url(url: str) -> str:
    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https" or parsed.netloc != "drive.google.com" or "/drive/folders/" not in parsed.path:
        raise ValueError(f"Expected a public Google Drive folder URL, got {url!r}.")
    return str(url).strip()


def parse_inventory_json(payload: str) -> list[DriveItem]:
    raw = json.loads(payload)
    if not isinstance(raw, list):
        raise ValueError("gdown inventory must be a JSON array.")
    items = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("url") or not entry.get("path"):
            raise ValueError(f"Invalid gdown inventory entry: {entry!r}")
        items.append(DriveItem(url=str(entry["url"]), path=str(entry["path"])))
    return items


def inventory_drive_folder(folder_url: str) -> list[DriveItem]:
    folder_url = validate_drive_folder_url(folder_url)
    result = subprocess.run(
        [sys.executable, "-m", "gdown", folder_url, "--folder", "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return parse_inventory_json(result.stdout)


def _index_exact_basename(items: Sequence[DriveItem], targets: set[str]) -> dict[str, DriveItem]:
    found: dict[str, DriveItem] = {}
    for item in items:
        name = Path(item.path).name
        if name not in targets:
            continue
        if name in found:
            raise ValueError(f"Duplicate requested basename in Drive inventory: {name}")
        found[name] = item
    return found


def select_source_manifest(
    csv_items,
    poa_items,
    dates: pd.DatetimeIndex,
    *,
    url_csv="",
    url_poa="",
) -> SourceManifest:
    requested_dates = [ts.date() for ts in dates]
    csv_names = {d.strftime("%Y%m%d") + ".csv" for d in requested_dates}
    years = sorted({d.year for d in requested_dates})
    poa_names = {f"POA PLTS IKN {year}.xlsx" for year in years}
    csv_index = _index_exact_basename(csv_items, csv_names)
    poa_index = _index_exact_basename(poa_items, poa_names)
    csv_by_date = {
        d: csv_index[d.strftime("%Y%m%d") + ".csv"]
        for d in requested_dates
        if d.strftime("%Y%m%d") + ".csv" in csv_index
    }
    poa_by_year = {
        year: poa_index[f"POA PLTS IKN {year}.xlsx"]
        for year in years
        if f"POA PLTS IKN {year}.xlsx" in poa_index
    }
    return SourceManifest(
        csv_by_date=csv_by_date,
        poa_by_year=poa_by_year,
        missing_csv_dates=[d for d in requested_dates if d not in csv_by_date],
        missing_poa_years=[year for year in years if year not in poa_by_year],
        url_csv=url_csv,
        url_poa=url_poa,
        csv_inventory_count=len(csv_items),
        poa_inventory_count=len(poa_items),
    )


def download_manifest(manifest: SourceManifest, destination: Path) -> DownloadedInputs:
    import gdown

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    csv_paths: dict[date, Path] = {}
    poa_paths: dict[int, Path] = {}
    errors: dict[str, str] = {}
    for key, item in manifest.csv_by_date.items():
        path = destination / Path(item.path).name
        try:
            gdown.download(url=item.url, output=str(path), quiet=False)
            if not path.is_file():
                raise RuntimeError(f"gdown did not create {path.name}")
        except Exception as exc:
            errors[path.name] = f"{type(exc).__name__}: {exc}"
        else:
            csv_paths[key] = path
    for key, item in manifest.poa_by_year.items():
        path = destination / Path(item.path).name
        try:
            gdown.download(url=item.url, output=str(path), quiet=False)
            if not path.is_file():
                raise RuntimeError(f"gdown did not create {path.name}")
        except Exception as exc:
            errors[path.name] = f"{type(exc).__name__}: {exc}"
        else:
            poa_paths[key] = path
    return DownloadedInputs(csv_by_date=csv_paths, poa_by_year=poa_paths, download_errors=errors)


def download_report_inputs(url_csv, url_poa, dates, destination):
    csv_items = inventory_drive_folder(url_csv)
    poa_items = inventory_drive_folder(url_poa)
    manifest = select_source_manifest(
        csv_items,
        poa_items,
        dates,
        url_csv=url_csv,
        url_poa=url_poa,
    )
    return manifest, download_manifest(manifest, Path(destination))
