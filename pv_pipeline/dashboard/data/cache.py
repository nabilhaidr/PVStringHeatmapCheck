"""Streamlit cache wrappers for dashboard data loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List

import pandas as pd

from pv_pipeline.dashboard.data.gdrive import download_artifact, list_artifacts
from pv_pipeline.dashboard.data.loader import (
    concat_findings_range,
    load_baseline_csv_day,
    load_findings_jsonl,
    load_findings_workbook,
)


@dataclass(frozen=True)
class LoadResult:
    sheets: Dict[str, pd.DataFrame]
    available_dates: List[date] = field(default_factory=list)
    missing_dates: List[date] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CsvLoadResult:
    dataframe: pd.DataFrame
    available_dates: List[date] = field(default_factory=list)
    missing: bool = False
    error: str = ""


def _each_day(start: date, end: date) -> list[date]:
    if end < start:
        start, end = end, start
    days = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _cache_data(func):
    import streamlit as st  # noqa: WPS433

    return st.cache_data(show_spinner=False)(func)


def _load_findings_day(
    day: date,
    xlsx_artifacts: Dict[date, object],
    jsonl_artifacts: Dict[date, object],
) -> tuple[Dict[str, pd.DataFrame], str]:
    """Load xlsx for artifacts, falling back to JSONL Findings when needed."""
    xlsx_artifact = xlsx_artifacts.get(day)
    jsonl_artifact = jsonl_artifacts.get(day)
    if xlsx_artifact is not None:
        try:
            return load_findings_workbook(download_artifact(xlsx_artifact.file_id)), ""
        except Exception as exc:
            if jsonl_artifact is None:
                return {}, f"{xlsx_artifact.name}: {exc}"
            try:
                return (
                    load_findings_jsonl(download_artifact(jsonl_artifact.file_id)),
                    f"{xlsx_artifact.name}: {exc}; using {jsonl_artifact.name} fallback",
                )
            except Exception as fallback_exc:
                return {}, f"{xlsx_artifact.name}: {exc}; {jsonl_artifact.name}: {fallback_exc}"

    if jsonl_artifact is not None:
        try:
            return load_findings_jsonl(download_artifact(jsonl_artifact.file_id)), ""
        except Exception as exc:
            return {}, f"{jsonl_artifact.name}: {exc}"
    return {}, ""


@_cache_data
def cached_findings_range(start: date, end: date) -> LoadResult:
    """Load and concatenate findings xlsx files across a date range."""
    artifacts = list_artifacts("findings")
    jsonl_artifacts = list_artifacts("findings_jsonl")
    available = sorted(set(artifacts).union(jsonl_artifacts))
    per_day = {}
    missing = []
    errors = []
    for day in _each_day(start, end):
        sheets, error = _load_findings_day(day, artifacts, jsonl_artifacts)
        if error:
            errors.append(error)
        if not sheets:
            missing.append(day)
            continue
        per_day[day] = sheets
    return LoadResult(
        sheets=concat_findings_range(per_day),
        available_dates=available,
        missing_dates=missing,
        errors=errors,
    )


@_cache_data
def cached_baseline_csv_day(day: date) -> CsvLoadResult:
    """Load a single baseline CSV day for Heatmap."""
    artifacts = list_artifacts("baseline_csv")
    artifact = artifacts.get(day)
    if artifact is None:
        return CsvLoadResult(
            dataframe=pd.DataFrame(),
            available_dates=list(artifacts),
            missing=True,
        )
    try:
        return CsvLoadResult(
            dataframe=load_baseline_csv_day(download_artifact(artifact.file_id)),
            available_dates=list(artifacts),
        )
    except Exception as exc:  # pragma: no cover - UI path
        return CsvLoadResult(
            dataframe=pd.DataFrame(),
            available_dates=list(artifacts),
            error=f"{artifact.name}: {exc}",
        )


def clear_dashboard_cache() -> None:
    import streamlit as st  # noqa: WPS433

    st.cache_data.clear()
