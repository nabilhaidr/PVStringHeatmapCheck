"""Google Drive access for dashboard artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO
from typing import Any, Dict, Literal
from urllib.request import urlopen

from pv_pipeline.dashboard.data.loader import (
    parse_baseline_csv_date,
    parse_findings_date,
    parse_findings_jsonl_date,
)


ArtifactKind = Literal["findings", "findings_jsonl", "baseline_csv"]
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
PUBLIC_SOURCE_COLUMNS = {
    "findings": (
        "findings_xlsx_file_id",
        "findings_xlsx_url",
        "findings_xlsx_drive_url",
        "findings_xlsx_drive_link",
        "m2_findings_xlsx_file_id",
        "m2_findings_xlsx_url",
        "m2_findings_xlsx_drive_url",
        "m2_findings_xlsx_drive_link",
        "xlsx_file_id",
        "xlsx_url",
        "xlsx_drive_url",
        "xlsx_drive_link",
        "file_xlsx",
        "file_xlsx_url",
    ),
    "findings_jsonl": (
        "findings_jsonl_file_id",
        "findings_jsonl_url",
        "findings_jsonl_drive_url",
        "findings_jsonl_drive_link",
        "m2_findings_jsonl_file_id",
        "m2_findings_jsonl_url",
        "m2_findings_jsonl_drive_url",
        "m2_findings_jsonl_drive_link",
        "jsonl_file_id",
        "jsonl_url",
        "jsonl_drive_url",
        "jsonl_drive_link",
        "file_jsonl",
        "file_jsonl_url",
    ),
    "baseline_csv": (
        "baseline_csv_file_id",
        "baseline_csv_url",
        "baseline_csv_drive_url",
        "baseline_csv_drive_link",
        "baseline_file_id",
        "baseline_url",
        "baseline_drive_url",
        "baseline_drive_link",
        "file_csv_id",
        "file_csv_url",
        "file_csv_drive_url",
        "file_csv_drive_link",
        "csv_file_id",
        "csv_url",
        "csv_drive_url",
        "csv_drive_link",
        "file_csv",
    ),
}
PUBLIC_NAME_COLUMNS = {
    "findings": ("findings_xlsx_name", "findings_xlsx_path", "file_xlsx"),
    "findings_jsonl": ("findings_jsonl_name", "findings_jsonl_path", "file_jsonl"),
    "baseline_csv": ("baseline_csv_name", "baseline_csv_path", "file_csv"),
}
DRIVE_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,}$")


@dataclass(frozen=True)
class DriveArtifact:
    date: date
    file_id: str
    name: str
    kind: ArtifactKind


def _streamlit_secrets() -> Any:
    import streamlit as st  # noqa: WPS433

    return st.secrets


def _gdrive_secrets() -> Any:
    return _streamlit_secrets()["gdrive"]


def _public_manifest_secrets() -> dict:
    return _as_dict(_as_dict(_streamlit_secrets()).get("gdrive_public", {}))


def _drive_client(service_account_json: str | None = None):
    """Build a Google Drive API client from service account JSON."""
    import json

    from google.oauth2 import service_account  # noqa: WPS433
    from googleapiclient.discovery import build  # noqa: WPS433

    if service_account_json is None:
        service_account_json = _gdrive_secrets()["service_account_json"]
    info = json.loads(service_account_json)
    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=scopes,
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return dict(value)
    except Exception:
        return {}


def _public_manifest_location(cfg: dict | None = None) -> str:
    cfg = _public_manifest_secrets() if cfg is None else cfg
    return str(cfg.get("manifest_csv_url") or cfg.get("manifest_csv_path") or "").strip()


def _config_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    return default


def _service_account_enabled(cfg: Any | None = None) -> bool:
    cfg = _as_dict(_gdrive_secrets()) if cfg is None else _as_dict(cfg)
    if "use_service_account" in cfg:
        return _config_bool(cfg.get("use_service_account"), default=True)
    return _config_bool(cfg.get("service_account_enabled"), default=True)


def _service_account_enabled_from_secrets() -> bool:
    try:
        return _service_account_enabled(_gdrive_secrets())
    except Exception:
        return True


def _has_service_account_config() -> bool:
    try:
        cfg = _as_dict(_gdrive_secrets())
    except Exception:
        return False
    if not _service_account_enabled(cfg):
        return False
    return bool(
        cfg.get("service_account_json")
        and (cfg.get("folder_id") or cfg.get("findings_folder_id") or cfg.get("baseline_folder_id"))
    )


def _clean_manifest_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "nat", "none"}:
        return ""
    return text


def _manifest_row(row: Any) -> dict[str, str]:
    return {str(key).strip().lower(): _clean_manifest_cell(value) for key, value in row.items()}


def _first_manifest_value(row: dict[str, str], columns: tuple[str, ...]) -> str:
    for column in columns:
        value = row.get(column)
        if value:
            return value
    return ""


def _parse_manifest_date(row: dict[str, str]) -> date | None:
    raw = _first_manifest_value(row, ("date", "source_date", "day"))
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw[:10] if fmt == "%Y-%m-%d" else raw, fmt).date()
        except ValueError:
            continue
    return None


def _extract_drive_file_id(source: str) -> str:
    for pattern in (r"/d/([^/?#]+)", r"[?&]id=([^&#]+)"):
        match = re.search(pattern, source)
        if match:
            return match.group(1)
    return ""


def _public_download_url(source: str) -> str:
    value = _clean_manifest_cell(source)
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        file_id = _extract_drive_file_id(value)
        if file_id:
            return f"https://drive.google.com/uc?export=download&id={file_id}"
        return value
    if "/" in value or "\\" in value or "." in value:
        return ""
    if DRIVE_FILE_ID_RE.match(value):
        return f"https://drive.google.com/uc?export=download&id={value}"
    return ""


def _artifact_name(kind: ArtifactKind, day: date, row: dict[str, str]) -> str:
    name = _first_manifest_value(row, PUBLIC_NAME_COLUMNS[kind])
    if name:
        return name
    if kind == "findings":
        return f"m2_findings_{day:%Y%m%d}.xlsx"
    if kind == "findings_jsonl":
        return f"m2_findings_{day:%Y%m%d}.jsonl"
    return f"{day:%Y-%m-%d}.csv"


def _read_public_manifest(cfg: dict) -> Any:
    import pandas as pd  # noqa: WPS433

    location = _public_manifest_location(cfg)
    if not location:
        raise KeyError("gdrive_public must define manifest_csv_url or manifest_csv_path.")
    return pd.read_csv(location)


def _list_public_manifest_artifacts(
    kind: ArtifactKind,
    cfg: dict | None = None,
) -> Dict[date, DriveArtifact]:
    cfg = _public_manifest_secrets() if cfg is None else cfg
    manifest = _read_public_manifest(cfg)
    artifacts: Dict[date, DriveArtifact] = {}
    for _index, raw_row in manifest.iterrows():
        row = _manifest_row(raw_row)
        parsed = _parse_manifest_date(row)
        if parsed is None:
            continue
        source_value = _first_manifest_value(row, PUBLIC_SOURCE_COLUMNS[kind])
        source = _public_download_url(source_value)
        name = _artifact_name(kind, parsed, row)
        if kind != "baseline_csv" and not source:
            continue
        artifact = DriveArtifact(date=parsed, file_id=source, name=name, kind=kind)
        existing = artifacts.get(parsed)
        if existing is None or (not existing.file_id and artifact.file_id):
            artifacts[parsed] = artifact
    return dict(sorted(artifacts.items()))


def _resolve_folder_id(
    kind: ArtifactKind,
    secrets: Any,
    folder_id: str | None = None,
) -> str:
    """Resolve per-kind Drive folder with ``folder_id`` backward compatibility."""
    if folder_id is not None:
        return str(folder_id)
    cfg = _as_dict(secrets)
    if kind in {"findings", "findings_jsonl"}:
        folder = cfg.get("findings_folder_id") or cfg.get("folder_id")
    elif kind == "baseline_csv":
        folder = cfg.get("baseline_folder_id") or cfg.get("folder_id")
    else:
        folder = cfg.get("folder_id")
    if not folder:
        raise KeyError(
            "GDrive secrets must define folder_id or per-kind "
            "findings_folder_id / baseline_folder_id."
        )
    return str(folder)


def _folder_id(kind: ArtifactKind, folder_id: str | None = None) -> str:
    return _resolve_folder_id(kind, _gdrive_secrets(), folder_id=folder_id)


def _list_children(service: Any, folder: str) -> list[dict]:
    files = []
    page_token = None
    while True:
        request = service.files().list(
            q=f"'{folder}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token,
            pageSize=1000,
        )
        response = request.execute()
        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files


def _month_subfolders(items: list[dict]) -> list[str]:
    month_re = re.compile(r"^\d{4}-\d{2}$")
    return [
        str(item["id"])
        for item in items
        if item.get("mimeType") == FOLDER_MIME_TYPE
        and month_re.match(str(item.get("name", "")))
    ]


def list_artifacts(
    kind: ArtifactKind,
    *,
    service: Any | None = None,
    folder_id: str | None = None,
) -> Dict[date, DriveArtifact]:
    """List dashboard artifacts from Drive, keyed by parsed date."""
    if kind not in {"findings", "findings_jsonl", "baseline_csv"}:
        raise ValueError(f"Unsupported artifact kind: {kind!r}")
    if service is None and folder_id is None:
        public_cfg = _public_manifest_secrets()
        if _public_manifest_location(public_cfg):
            try:
                return _list_public_manifest_artifacts(kind, public_cfg)
            except Exception:
                if not _has_service_account_config():
                    raise
        if not _service_account_enabled_from_secrets():
            raise KeyError(
                "Public manifest mode is required because [gdrive].use_service_account=false. "
                "Configure [gdrive_public].manifest_csv_url or manifest_csv_path."
            )
    service = service or _drive_client()
    folder = _folder_id(kind, folder_id)
    parser = {
        "findings": parse_findings_date,
        "findings_jsonl": parse_findings_jsonl_date,
        "baseline_csv": parse_baseline_csv_date,
    }[kind]

    artifacts: Dict[date, DriveArtifact] = {}
    root_items = _list_children(service, folder)
    scan_items = list(root_items)
    if kind == "baseline_csv":
        for subfolder in _month_subfolders(root_items):
            scan_items.extend(_list_children(service, subfolder))

    for item in scan_items:
        if item.get("mimeType") != FOLDER_MIME_TYPE:
            parsed = parser(item.get("name", ""))
            if parsed is None:
                continue
            artifacts[parsed] = DriveArtifact(
                date=parsed,
                file_id=str(item["id"]),
                name=str(item["name"]),
                kind=kind,
            )
    return dict(sorted(artifacts.items()))


def download_artifact(file_id: str, *, service: Any | None = None) -> BytesIO:
    """Download one Drive artifact into memory."""
    if not file_id:
        raise ValueError("Manifest row must define a downloadable public URL or Drive file ID.")
    if service is None and file_id.startswith(("http://", "https://")):
        with urlopen(file_id) as response:
            return BytesIO(response.read())
    service = service or _drive_client()
    request = service.files().get_media(fileId=file_id)
    if hasattr(request, "getvalue"):
        return BytesIO(request.getvalue())
    if hasattr(request, "read"):
        return BytesIO(request.read())

    from googleapiclient.http import MediaIoBaseDownload  # noqa: WPS433

    buffer = BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    buffer.seek(0)
    return buffer
