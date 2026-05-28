"""Helpers for building dashboard-ready public manifests."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import pandas as pd


_DRIVE_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,}$")
_DRIVE_ID_PATTERNS = (r"/d/([^/?#]+)", r"[?&]id=([^&#]+)")
_DATE_COLUMNS = ("date", "source_date", "day")
_ID_COLUMNS = ("file_id", "id", "drive_file_id")
_URL_COLUMNS = ("url", "web_view_link", "webviewlink", "drive_url", "drive_link")
_ARTIFACTS = (
    ("baseline_csv", "baseline_csv_name"),
    ("findings_xlsx", "findings_xlsx_name"),
    ("findings_jsonl", "findings_jsonl_name"),
)


def dashboard_artifact_names(day: date) -> dict[str, str]:
    """Return the expected dashboard artifact filenames for one date."""
    return {
        "baseline_csv_name": f"{day:%Y-%m-%d}.csv",
        "findings_xlsx_name": f"m2_findings_{day:%Y%m%d}.xlsx",
        "findings_jsonl_name": f"m2_findings_{day:%Y%m%d}.jsonl",
    }


def drive_view_url(file_id: str) -> str:
    """Build a human-readable Google Drive file URL."""
    file_id = _clean_cell(file_id)
    if not file_id:
        return ""
    return f"https://drive.google.com/file/d/{file_id}/view"


def enrich_dashboard_manifest(
    manifest_df: pd.DataFrame,
    drive_files_df: pd.DataFrame,
    *,
    overwrite: bool = False,
) -> pd.DataFrame:
    """Add dashboard artifact name, file ID, and link columns to a manifest.

    ``drive_files_df`` should contain one row per Drive file with at least
    ``name`` and either ``file_id``/``id`` or a public Drive ``url``.
    Existing non-empty manual values are preserved unless ``overwrite=True``.
    """
    out = manifest_df.copy()
    drive_files = _drive_files_by_name(drive_files_df)
    _ensure_columns(out)

    for index, raw_row in out.iterrows():
        day = _parse_manifest_date(_normalise_row(raw_row))
        if day is None:
            continue

        names = dashboard_artifact_names(day)
        for kind, name_col in _ARTIFACTS:
            expected_name = names[name_col]
            _set_if_allowed(out, index, name_col, expected_name, overwrite)

            file = drive_files.get(expected_name, {})
            file_id = file.get("file_id", "")
            url = file.get("url", "") or drive_view_url(file_id)
            _set_if_allowed(out, index, f"{kind}_file_id", file_id, overwrite)
            _set_if_allowed(out, index, f"{kind}_url", url, overwrite)

    return out


def _ensure_columns(df: pd.DataFrame) -> None:
    for kind, name_col in _ARTIFACTS:
        for column in (name_col, f"{kind}_file_id", f"{kind}_url"):
            if column not in df.columns:
                df[column] = ""


def _set_if_allowed(
    df: pd.DataFrame,
    index: Any,
    column: str,
    value: str,
    overwrite: bool,
) -> None:
    if not value:
        return
    current = _clean_cell(df.at[index, column])
    if overwrite or not current:
        df.at[index, column] = value


def _drive_files_by_name(df: pd.DataFrame) -> dict[str, dict[str, str]]:
    files: dict[str, dict[str, str]] = {}
    if df.empty:
        return files

    for _index, raw_row in df.iterrows():
        row = _normalise_row(raw_row)
        name = row.get("name", "")
        if not name:
            continue
        url = _first(row, _URL_COLUMNS)
        file_id = _first(row, _ID_COLUMNS) or _extract_drive_file_id(url)
        if file_id and not _DRIVE_FILE_ID_RE.match(file_id):
            file_id = _extract_drive_file_id(file_id)
        files[name] = {
            "file_id": file_id,
            "url": url or drive_view_url(file_id),
        }
    return files


def _parse_manifest_date(row: dict[str, str]) -> date | None:
    raw = _first(row, _DATE_COLUMNS)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            value = raw[:10] if fmt == "%Y-%m-%d" else raw
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _normalise_row(row: Any) -> dict[str, str]:
    return {str(key).strip().lower(): _clean_cell(value) for key, value in row.items()}


def _first(row: dict[str, str], columns: tuple[str, ...]) -> str:
    for column in columns:
        value = row.get(column)
        if value:
            return value
    return ""


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "nat", "none"}:
        return ""
    return text


def _extract_drive_file_id(source: str) -> str:
    for pattern in _DRIVE_ID_PATTERNS:
        match = re.search(pattern, source)
        if match:
            return match.group(1)
    return ""
