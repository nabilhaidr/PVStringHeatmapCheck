from __future__ import annotations

import re

import pandas as pd


def wb_from_inverter(inverter_id: object) -> str:
    match = re.match(r"^(WB\d+)", str(inverter_id).upper())
    return match.group(1) if match else ""


def normalize_findings_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "severity" in out.columns:
        out["severity"] = out["severity"].astype(str).str.upper()
    if "sub_module" in out.columns:
        out["sub_module"] = out["sub_module"].astype(str)
    if "inverter_id" in out.columns:
        out["inverter_id"] = out["inverter_id"].astype(str)
        out["wb_id"] = out["inverter_id"].map(wb_from_inverter)
    if "timestamp" in out.columns:
        out["timestamp"] = pd.to_datetime(out["timestamp"], errors="coerce")
    return out
