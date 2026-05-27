from __future__ import annotations

from datetime import date
from io import BytesIO

import pandas as pd

from pv_pipeline.dashboard.data import cache
from pv_pipeline.dashboard.data.gdrive import DriveArtifact


def test_load_findings_day_prefers_xlsx_artifacts(monkeypatch):
    day = date(2026, 5, 14)
    xlsx_artifacts = {
        day: DriveArtifact(day, "xlsx-id", "m2_findings_20260514.xlsx", "findings")
    }
    jsonl_artifacts = {
        day: DriveArtifact(day, "jsonl-id", "m2_findings_20260514.jsonl", "findings_jsonl")
    }

    monkeypatch.setattr(cache, "download_artifact", lambda file_id: BytesIO(file_id.encode()))
    monkeypatch.setattr(
        cache,
        "load_findings_workbook",
        lambda _bio: {"Findings": pd.DataFrame({"source": ["xlsx"]}), "Artifact": pd.DataFrame({"x": [1]})},
    )
    monkeypatch.setattr(
        cache,
        "load_findings_jsonl",
        lambda _bio: {"Findings": pd.DataFrame({"source": ["jsonl"]})},
    )

    sheets, error = cache._load_findings_day(day, xlsx_artifacts, jsonl_artifacts)

    assert error == ""
    assert set(sheets) == {"Findings", "Artifact"}
    assert sheets["Findings"].loc[0, "source"] == "xlsx"


def test_load_findings_day_falls_back_to_jsonl_when_xlsx_missing(monkeypatch):
    day = date(2026, 5, 14)
    jsonl_artifacts = {
        day: DriveArtifact(day, "jsonl-id", "m2_findings_20260514.jsonl", "findings_jsonl")
    }

    monkeypatch.setattr(cache, "download_artifact", lambda _file_id: BytesIO(b"{}"))
    monkeypatch.setattr(
        cache,
        "load_findings_jsonl",
        lambda _bio: {"Findings": pd.DataFrame({"source": ["jsonl"]})},
    )

    sheets, error = cache._load_findings_day(day, {}, jsonl_artifacts)

    assert error == ""
    assert list(sheets) == ["Findings"]
    assert sheets["Findings"].loc[0, "source"] == "jsonl"


def test_load_findings_day_falls_back_to_jsonl_when_xlsx_fails(monkeypatch):
    day = date(2026, 5, 14)
    xlsx_artifacts = {
        day: DriveArtifact(day, "xlsx-id", "m2_findings_20260514.xlsx", "findings")
    }
    jsonl_artifacts = {
        day: DriveArtifact(day, "jsonl-id", "m2_findings_20260514.jsonl", "findings_jsonl")
    }

    monkeypatch.setattr(cache, "download_artifact", lambda _file_id: BytesIO(b"payload"))
    monkeypatch.setattr(cache, "load_findings_workbook", lambda _bio: (_ for _ in ()).throw(ValueError("bad xlsx")))
    monkeypatch.setattr(
        cache,
        "load_findings_jsonl",
        lambda _bio: {"Findings": pd.DataFrame({"source": ["jsonl"]})},
    )

    sheets, error = cache._load_findings_day(day, xlsx_artifacts, jsonl_artifacts)

    assert list(sheets) == ["Findings"]
    assert sheets["Findings"].loc[0, "source"] == "jsonl"
    assert "bad xlsx" in error
