"""Google Drive access for dashboard artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import Any, Dict, Literal

from pv_pipeline.dashboard.data.loader import (
    parse_baseline_csv_date,
    parse_findings_date,
    parse_findings_jsonl_date,
)


ArtifactKind = Literal["findings", "findings_jsonl", "baseline_csv"]
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


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
