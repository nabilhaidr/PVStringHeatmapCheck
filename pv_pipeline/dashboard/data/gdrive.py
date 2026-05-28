"""Google Drive access for dashboard artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO
from typing import Any, Dict, Literal

from pv_pipeline.dashboard.data.loader import (
    parse_baseline_csv_date,
    parse_findings_date,
)


ArtifactKind = Literal["findings", "baseline_csv"]


@dataclass(frozen=True)
class DriveArtifact:
    date: date
    file_id: str
    name: str
    kind: ArtifactKind


def _streamlit_secrets() -> Any:
    import streamlit as st  # noqa: WPS433

    return st.secrets


def _drive_client(service_account_json: str | None = None):
    """Build a Google Drive API client from service account JSON."""
    import json

    from google.oauth2 import service_account  # noqa: WPS433
    from googleapiclient.discovery import build  # noqa: WPS433

    if service_account_json is None:
        service_account_json = _streamlit_secrets()["gdrive"]["service_account_json"]
    info = json.loads(service_account_json)
    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=scopes,
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _folder_id(folder_id: str | None = None) -> str:
    if folder_id is not None:
        return str(folder_id)
    return str(_streamlit_secrets()["gdrive"]["folder_id"])


def list_artifacts(
    kind: ArtifactKind,
    *,
    service: Any | None = None,
    folder_id: str | None = None,
) -> Dict[date, DriveArtifact]:
    """List dashboard artifacts from Drive, keyed by parsed date."""
    if kind not in {"findings", "baseline_csv"}:
        raise ValueError(f"Unsupported artifact kind: {kind!r}")
    service = service or _drive_client()
    folder = _folder_id(folder_id)
    parser = parse_findings_date if kind == "findings" else parse_baseline_csv_date

    artifacts: Dict[date, DriveArtifact] = {}
    page_token = None
    while True:
        request = service.files().list(
            q=f"'{folder}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token,
            pageSize=1000,
        )
        response = request.execute()
        for item in response.get("files", []):
            parsed = parser(item.get("name", ""))
            if parsed is None:
                continue
            artifacts[parsed] = DriveArtifact(
                date=parsed,
                file_id=str(item["id"]),
                name=str(item["name"]),
                kind=kind,
            )
        page_token = response.get("nextPageToken")
        if not page_token:
            break
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
