from __future__ import annotations

from datetime import date
from io import BytesIO
from unittest.mock import Mock

from pv_pipeline.dashboard.data.gdrive import DriveArtifact, download_artifact, list_artifacts


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


def test_download_artifact_returns_bytesio_from_media_request():
    fake_files = Mock()
    fake_files.get_media.return_value = BytesIO(b"payload")
    fake_service = Mock()
    fake_service.files.return_value = fake_files

    out = download_artifact("abc", service=fake_service)

    assert out.getvalue() == b"payload"
    fake_files.get_media.assert_called_once_with(fileId="abc")
