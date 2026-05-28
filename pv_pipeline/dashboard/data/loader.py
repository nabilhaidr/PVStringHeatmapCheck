"""Load dashboard artifacts from M2 xlsx outputs and baseline CSV files."""

from __future__ import annotations

import re
from datetime import date, datetime
from io import BytesIO
from typing import Dict, Mapping

import pandas as pd


_FINDINGS_RE = re.compile(r"^m2_findings_(\d{8})\.xlsx$", re.IGNORECASE)
_BASELINE_CSV_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.csv$", re.IGNORECASE)
_PV_POWER_RE = re.compile(r"^PV\d+\s+Power\(kW\)$", re.IGNORECASE)


def parse_findings_date(filename: str) -> date | None:
    """Return date from ``m2_findings_YYYYMMDD.xlsx`` or None for other names."""
    match = _FINDINGS_RE.match(str(filename).strip())
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def parse_baseline_csv_date(filename: str) -> date | None:
    """Return date from baseline ``YYYY-MM-DD.csv`` names or None."""
    match = _BASELINE_CSV_RE.match(str(filename).strip())
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def load_findings_workbook(bytes_io: BytesIO) -> Dict[str, pd.DataFrame]:
    """Read all sheets from an M2 findings xlsx workbook."""
    bytes_io.seek(0)
    with pd.ExcelFile(bytes_io, engine="openpyxl") as workbook:
        return {
            sheet_name: pd.read_excel(workbook, sheet_name=sheet_name)
            for sheet_name in workbook.sheet_names
        }


def concat_findings_range(
    per_day: Mapping[date, Mapping[str, pd.DataFrame]],
) -> Dict[str, pd.DataFrame]:
    """Concatenate per-day workbook sheets and add ``source_date``."""
    grouped: Dict[str, list[pd.DataFrame]] = {}
    for source_day in sorted(per_day):
        workbook = per_day[source_day]
        for sheet_name, df in workbook.items():
            if df is None:
                continue
            sheet_df = df.copy()
            sheet_df["source_date"] = source_day
            grouped.setdefault(sheet_name, []).append(sheet_df)

    return {
        sheet_name: pd.concat(frames, ignore_index=True)
        for sheet_name, frames in grouped.items()
        if frames
    }


def load_baseline_csv_day(bytes_io: BytesIO) -> pd.DataFrame:
    """Read one baseline CSV day for the Heatmap page.

    The baseline CSV is the filtered NORMAL daily snapshot. It must contain the
    columns used by ``pv_pipeline.viz.plot_single_inv_heatmap``.
    """
    bytes_io.seek(0)
    df = pd.read_csv(bytes_io)
    required = {"Inverter_ID", "Start Time"}
    missing = sorted(required.difference(df.columns))
    if missing:
        raise ValueError(f"Baseline CSV missing required columns: {', '.join(missing)}")

    pv_power_cols = [col for col in df.columns if _PV_POWER_RE.match(str(col))]
    if not pv_power_cols:
        raise ValueError("Baseline CSV must contain PVn Power(kW) columns")

    out = df.copy()
    out["Start Time"] = pd.to_datetime(out["Start Time"], errors="coerce")
    if "WB" not in out.columns:
        out["WB"] = (
            out["Inverter_ID"]
            .astype(str)
            .str.upper()
            .str.extract(r"^(WB\d+)", expand=False)
        )
    return out
