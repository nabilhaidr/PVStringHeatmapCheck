from __future__ import annotations

from datetime import date
from io import BytesIO
from unittest.mock import Mock

import pytest

from pv_pipeline.dashboard.data.gdrive import (
    DriveArtifact,
    _resolve_folder_id,
    download_artifact,
    list_artifacts,
)


class _FakeRequest:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeFiles:
    def __init__(self):
        self.list_kwargs = None

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        return _FakeRequest({
            "files": [
                {"id": "xlsx-id", "name": "m2_findings_20260514.xlsx", "mimeType": "application/vnd.ms-excel"},
                {"id": "jsonl-id", "name": "m2_findings_20260514.jsonl", "mimeType": "application/json"},
                {"id": "csv-id", "name": "2026-05-14.csv", "mimeType": "text/csv"},
                {"id": "skip-id", "name": "manifest.csv", "mimeType": "text/csv"},
            ],
            "nextPageToken": None,
        })


class _FakeService:
    def __init__(self):
        self.files_resource = _FakeFiles()

    def files(self):
        return self.files_resource


def test_list_artifacts_filters_findings_by_filename():
    fake_service = _FakeService()

    artifacts = list_artifacts("findings", service=fake_service, folder_id="folder-1")

    assert artifacts == {
        date(2026, 5, 14): DriveArtifact(
            date=date(2026, 5, 14),
            file_id="xlsx-id",
            name="m2_findings_20260514.xlsx",
            kind="findings",
        )
    }
    assert "'folder-1' in parents" in fake_service.files_resource.list_kwargs["q"]


def test_list_artifacts_filters_baseline_csv_by_filename():
    artifacts = list_artifacts("baseline_csv", service=_FakeService(), folder_id="folder-1")

    assert list(artifacts) == [date(2026, 5, 14)]
    assert artifacts[date(2026, 5, 14)].file_id == "csv-id"


def test_list_artifacts_filters_findings_jsonl_by_filename():
    artifacts = list_artifacts("findings_jsonl", service=_FakeService(), folder_id="folder-1")

    assert list(artifacts) == [date(2026, 5, 14)]
    assert artifacts[date(2026, 5, 14)].file_id == "jsonl-id"


def test_resolve_folder_id_supports_separate_findings_and_baseline_folders():
    secrets = {
        "folder_id": "shared",
        "findings_folder_id": "findings-folder",
        "baseline_folder_id": "baseline-folder",
    }

    assert _resolve_folder_id("findings", secrets) == "findings-folder"
    assert _resolve_folder_id("findings_jsonl", secrets) == "findings-folder"
    assert _resolve_folder_id("baseline_csv", secrets) == "baseline-folder"


class _FakeNestedFiles:
    def __init__(self):
        self.queries = []

    def list(self, **kwargs):
        self.queries.append(kwargs["q"])
        if "'baseline-root' in parents" in kwargs["q"]:
            return _FakeRequest({
                "files": [
                    {
                        "id": "month-folder",
                        "name": "2026-05",
                        "mimeType": "application/vnd.google-apps.folder",
                    }
                ],
                "nextPageToken": None,
            })
        if "'month-folder' in parents" in kwargs["q"]:
            return _FakeRequest({
                "files": [
                    {"id": "csv-id", "name": "2026-05-14.csv", "mimeType": "text/csv"},
                    {"id": "skip-id", "name": "manifest.csv", "mimeType": "text/csv"},
                ],
                "nextPageToken": None,
            })
        return _FakeRequest({"files": [], "nextPageToken": None})


class _FakeNestedService:
    def __init__(self):
        self.files_resource = _FakeNestedFiles()

    def files(self):
        return self.files_resource


def test_list_artifacts_finds_baseline_csv_inside_month_subfolders():
    fake_service = _FakeNestedService()

    artifacts = list_artifacts("baseline_csv", service=fake_service, folder_id="baseline-root")

    assert artifacts[date(2026, 5, 14)].file_id == "csv-id"
    assert any("'baseline-root' in parents" in q for q in fake_service.files_resource.queries)
    assert any("'month-folder' in parents" in q for q in fake_service.files_resource.queries)


def test_download_artifact_returns_bytesio_from_media_request():
    fake_files = Mock()
    fake_files.get_media.return_value = BytesIO(b"payload")
    fake_service = Mock()
    fake_service.files.return_value = fake_files

    out = download_artifact("abc", service=fake_service)

    assert out.getvalue() == b"payload"
    fake_files.get_media.assert_called_once_with(fileId="abc")


def test_list_artifacts_uses_public_manifest_with_manual_findings_columns(
    monkeypatch,
    tmp_path,
):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        "\n".join([
            "date,file_csv,baseline_csv_file_id,findings_xlsx_url,findings_jsonl_file_id",
            (
                "2026-05-14,baseline/2026-05/2026-05-14.csv,"
                "baseline-id,https://drive.google.com/file/d/xlsx-id/view,jsonl-id"
            ),
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "pv_pipeline.dashboard.data.gdrive._streamlit_secrets",
        lambda: {"gdrive_public": {"manifest_csv_path": str(manifest)}},
    )

    xlsx = list_artifacts("findings")
    jsonl = list_artifacts("findings_jsonl")
    baseline = list_artifacts("baseline_csv")

    assert xlsx == {
        date(2026, 5, 14): DriveArtifact(
            date=date(2026, 5, 14),
            file_id="https://drive.google.com/uc?export=download&id=xlsx-id",
            name="m2_findings_20260514.xlsx",
            kind="findings",
        )
    }
    assert jsonl[date(2026, 5, 14)].file_id.endswith("id=jsonl-id")
    assert baseline[date(2026, 5, 14)].name == "baseline/2026-05/2026-05-14.csv"
    assert baseline[date(2026, 5, 14)].file_id.endswith("id=baseline-id")


def test_public_manifest_accepts_drive_link_alias_columns(
    monkeypatch,
    tmp_path,
):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        "\n".join([
            "date,baseline_csv_name,baseline_csv_drive_link,findings_xlsx_drive_link,findings_jsonl_drive_link",
            (
                "2026-05-14,2026-05-14.csv,"
                "https://drive.google.com/file/d/csv-id/view,"
                "https://drive.google.com/file/d/xlsx-id/view,"
                "https://drive.google.com/file/d/jsonl-id/view"
            ),
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "pv_pipeline.dashboard.data.gdrive._streamlit_secrets",
        lambda: {"gdrive_public": {"manifest_csv_path": str(manifest)}},
    )

    assert list_artifacts("baseline_csv")[date(2026, 5, 14)].file_id.endswith("id=csv-id")
    assert list_artifacts("findings")[date(2026, 5, 14)].file_id.endswith("id=xlsx-id")
    assert list_artifacts("findings_jsonl")[date(2026, 5, 14)].file_id.endswith("id=jsonl-id")


def test_public_manifest_can_keep_existing_file_csv_path_without_download_source(
    monkeypatch,
    tmp_path,
):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        "date,file_csv\n2026-05-14,baseline/2026-05/2026-05-14.csv\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "pv_pipeline.dashboard.data.gdrive._streamlit_secrets",
        lambda: {"gdrive_public": {"manifest_csv_path": str(manifest)}},
    )

    artifacts = list_artifacts("baseline_csv")

    assert artifacts[date(2026, 5, 14)] == DriveArtifact(
        date=date(2026, 5, 14),
        file_id="",
        name="baseline/2026-05/2026-05-14.csv",
        kind="baseline_csv",
    )
    with pytest.raises(ValueError, match="downloadable public URL or Drive file ID"):
        download_artifact("")


def test_download_artifact_reads_public_url_without_service(monkeypatch):
    class _Response:
        def __enter__(self):
            return BytesIO(b"public-payload")

        def __exit__(self, *_args):
            return False

    urlopen = Mock(return_value=_Response())
    monkeypatch.setattr("pv_pipeline.dashboard.data.gdrive.urlopen", urlopen)

    out = download_artifact("https://drive.google.com/uc?export=download&id=abc")

    assert out.getvalue() == b"public-payload"
    urlopen.assert_called_once()


def test_list_artifacts_requires_public_manifest_when_service_account_disabled(
    monkeypatch,
):
    monkeypatch.setattr(
        "pv_pipeline.dashboard.data.gdrive._streamlit_secrets",
        lambda: {
            "gdrive": {
                "use_service_account": False,
                "findings_folder_id": "folder-id",
            }
        },
    )

    with pytest.raises(KeyError, match="use_service_account=false"):
        list_artifacts("findings")


def test_public_manifest_failure_does_not_fallback_to_disabled_service_account(
    monkeypatch,
    tmp_path,
):
    missing_manifest = tmp_path / "missing.csv"
    monkeypatch.setattr(
        "pv_pipeline.dashboard.data.gdrive._streamlit_secrets",
        lambda: {
            "gdrive_public": {"manifest_csv_path": str(missing_manifest)},
            "gdrive": {
                "use_service_account": False,
                "findings_folder_id": "folder-id",
                "service_account_json": "{}",
            },
        },
    )

    with pytest.raises(FileNotFoundError):
        list_artifacts("findings")
