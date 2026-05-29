"""PV string underperform transforms for the Streamlit dashboard."""

from __future__ import annotations

import re
from typing import Iterable

import numpy as np
import pandas as pd


SEVERITY_RANK = {
    "NORMAL": 0,
    "INFO": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}

_PV_STRING_RE = re.compile(r"^PV\s*0*?(\d+)$", flags=re.I)
_PV_POWER_RE = re.compile(r"^PV\s*0*?(\d+)\s+Power\(kW\)$", flags=re.I)


def _severity_rank(value: object) -> int:
    return SEVERITY_RANK.get(str(value).strip().upper(), -1)


def _clean_unique(values: Iterable[object]) -> str:
    cleaned = sorted({
        str(value).strip()
        for value in values
        if pd.notna(value) and str(value).strip() and str(value).strip().lower() not in {"nan", "none"}
    })
    return "; ".join(cleaned)


def _extract_pv_index(pv_string: object) -> int | None:
    match = _PV_STRING_RE.match(str(pv_string).strip())
    if not match:
        return None
    return int(match.group(1))


def _wb_from_inverter(inverter_id: object) -> str:
    match = re.match(r"^(WB\d+)", str(inverter_id).upper())
    return match.group(1) if match else ""


def _find_pv_power_columns(df: pd.DataFrame, pv_max_allowed: int) -> dict[int, str]:
    cols: dict[int, str] = {}
    for col in df.columns:
        match = _PV_POWER_RE.match(str(col))
        if not match:
            continue
        pv_n = int(match.group(1))
        if pv_n <= pv_max_allowed:
            cols[pv_n] = str(col)
    return dict(sorted(cols.items()))


def _find_pv_col(df: pd.DataFrame, pv_n: int, metric: str) -> str | None:
    pattern = re.compile(
        rf"^PV\s*0*?{pv_n}\s+input\s+{metric}\({ 'A' if metric == 'current' else 'V' }\)$",
        flags=re.I,
    )
    for col in df.columns:
        if pattern.match(str(col)):
            return str(col)
    return None


def _valid_pv_string_mask(series: pd.Series) -> pd.Series:
    text = series.astype(str).str.strip()
    return series.notna() & text.ne("") & ~text.str.lower().isin({"nan", "none"})


def summarize_pv_string_findings(findings: pd.DataFrame) -> pd.DataFrame:
    """Summarize populated ``pv_string`` findings for dashboard triage."""
    if findings is None or findings.empty or "pv_string" not in findings.columns:
        return pd.DataFrame(columns=[
            "source_date", "wb_id", "inverter_id", "pv_string", "sub_module",
            "finding_count", "worst_severity", "latest_timestamp",
            "fault_types", "max_confidence",
        ])

    df = findings.copy()
    df = df.loc[_valid_pv_string_mask(df["pv_string"])].copy()
    if df.empty:
        return summarize_pv_string_findings(pd.DataFrame())

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    else:
        df["timestamp"] = pd.NaT
    if "source_date" not in df.columns:
        df["source_date"] = df["timestamp"].dt.date
    if "wb_id" not in df.columns and "inverter_id" in df.columns:
        df["wb_id"] = df["inverter_id"].map(_wb_from_inverter)

    for col in ["source_date", "wb_id", "inverter_id", "pv_string", "sub_module", "severity"]:
        if col not in df.columns:
            df[col] = ""
    if "fault_type" not in df.columns:
        df["fault_type"] = ""
    if "confidence" not in df.columns:
        df["confidence"] = np.nan

    df["severity"] = df["severity"].astype(str).str.upper()
    df["_severity_rank"] = df["severity"].map(_severity_rank)
    df["_confidence_num"] = pd.to_numeric(df["confidence"], errors="coerce")

    rows = []
    group_cols = ["source_date", "wb_id", "inverter_id", "pv_string", "sub_module"]
    for keys, sub in df.groupby(group_cols, dropna=False, sort=False):
        rank = sub["_severity_rank"]
        worst_idx = rank.idxmax()
        row = dict(zip(group_cols, keys))
        row.update({
            "finding_count": int(len(sub)),
            "worst_severity": str(sub.loc[worst_idx, "severity"]).upper(),
            "severity_rank": int(rank.max()),
            "latest_timestamp": sub["timestamp"].max(),
            "fault_types": _clean_unique(sub["fault_type"]),
            "max_confidence": float(sub["_confidence_num"].max())
            if sub["_confidence_num"].notna().any() else np.nan,
        })
        rows.append(row)

    out = pd.DataFrame(rows)
    return out.sort_values(
        ["severity_rank", "latest_timestamp", "inverter_id", "pv_string"],
        ascending=[False, False, True, True],
        kind="mergesort",
    ).reset_index(drop=True)


def _empty_indices_for_inverter(empty_pv_map: dict | None, inverter_id: str) -> set[int]:
    if not empty_pv_map:
        return set()
    try:
        return {int(n) for n in empty_pv_map.get(str(inverter_id).upper(), [])}
    except Exception:
        return set()


def _prepare_inverter_frame(
    df: pd.DataFrame,
    inverter_id: str,
    empty_pv_map: dict | None,
    pv_max_allowed: int,
) -> tuple[pd.DataFrame, dict[int, str]]:
    if df is None or df.empty or "Inverter_ID" not in df.columns or "Start Time" not in df.columns:
        return pd.DataFrame(), {}

    sub = df.loc[df["Inverter_ID"].astype(str).str.upper() == str(inverter_id).upper()].copy()
    if sub.empty:
        return pd.DataFrame(), {}
    sub["Start Time"] = pd.to_datetime(sub["Start Time"], errors="coerce")
    sub = sub.loc[sub["Start Time"].notna()].sort_values("Start Time").reset_index(drop=True)
    if sub.empty:
        return pd.DataFrame(), {}

    power_cols = _find_pv_power_columns(sub, pv_max_allowed)
    empty_indices = _empty_indices_for_inverter(empty_pv_map, inverter_id)
    for pv_n in empty_indices:
        power_cols.pop(pv_n, None)
    return sub, power_cols


def _cell3_normalized(power_matrix: pd.DataFrame) -> pd.DataFrame:
    positive = power_matrix.replace(0, np.nan)
    row_min = positive.min(axis=1, skipna=True)
    row_max = positive.max(axis=1, skipna=True)
    denom = row_max - row_min
    return positive.sub(row_min, axis=0).div(denom.replace(0, np.nan), axis=0)


def build_string_timeseries(
    df: pd.DataFrame,
    inverter_id: str,
    pv_string: str,
    *,
    empty_pv_map: dict | None = None,
    pv_max_allowed: int = 28,
) -> tuple[pd.DataFrame, str]:
    """Build one PV string baseline context using Cell-3-compatible metrics."""
    pv_n = _extract_pv_index(pv_string)
    if pv_n is None:
        return pd.DataFrame(), f"Invalid pv_string: {pv_string!r}"

    sub, power_cols = _prepare_inverter_frame(df, inverter_id, empty_pv_map, pv_max_allowed)
    if sub.empty:
        return pd.DataFrame(), f"No baseline rows for inverter {inverter_id}."
    if pv_n not in power_cols:
        return pd.DataFrame(), f"Baseline CSV does not contain PV{pv_n} Power(kW) for {inverter_id}."

    power_df = sub[list(power_cols.values())].apply(pd.to_numeric, errors="coerce").replace(0, np.nan)
    power_by_pv = power_df.rename(columns={col: f"PV{n}" for n, col in power_cols.items()})
    norm = _cell3_normalized(power_by_pv)

    pv_name = f"PV{pv_n}"
    sibling_cols = [col for col in power_by_pv.columns if col != pv_name]
    sibling_median = power_by_pv[sibling_cols].median(axis=1, skipna=True) if sibling_cols else pd.Series(np.nan, index=sub.index)
    pv_power = power_by_pv[pv_name]

    out = pd.DataFrame({
        "Start Time": sub["Start Time"],
        "pv_power_kw": pv_power,
        "sibling_median_power_kw": sibling_median,
        "power_ratio_to_sibling": pv_power / sibling_median.replace(0, np.nan),
        "cell3_norm": norm[pv_name],
    })

    current_col = _find_pv_col(sub, pv_n, "current")
    if current_col is not None:
        out["pv_current_a"] = pd.to_numeric(sub[current_col], errors="coerce")

    message = ""
    if out["pv_power_kw"].notna().sum() == 0:
        message = f"PV{pv_n} Power(kW) has no valid power samples in this baseline CSV."
    return out, message


def analyze_inverter_strings(
    df: pd.DataFrame,
    inverter_id: str,
    *,
    empty_pv_map: dict | None = None,
    pv_max_allowed: int = 28,
) -> pd.DataFrame:
    """Return display-only baseline metrics per PV string for one inverter."""
    sub, power_cols = _prepare_inverter_frame(df, inverter_id, empty_pv_map, pv_max_allowed)
    if sub.empty or not power_cols:
        return pd.DataFrame()

    rows = []
    for pv_n in power_cols:
        ts, _ = build_string_timeseries(
            sub,
            inverter_id,
            f"PV{pv_n}",
            empty_pv_map=empty_pv_map,
            pv_max_allowed=pv_max_allowed,
        )
        if ts.empty:
            continue
        valid_ratio = ts["power_ratio_to_sibling"].dropna()
        valid_norm = ts["cell3_norm"].dropna()
        row = {
            "inverter_id": str(inverter_id),
            "pv_string": f"PV{pv_n}",
            "median_power_kw": float(ts["pv_power_kw"].median()) if ts["pv_power_kw"].notna().any() else np.nan,
            "median_sibling_power_kw": float(ts["sibling_median_power_kw"].median())
            if ts["sibling_median_power_kw"].notna().any() else np.nan,
            "median_power_ratio_to_sibling": float(valid_ratio.median()) if not valid_ratio.empty else np.nan,
            "p10_power_ratio_to_sibling": float(valid_ratio.quantile(0.10)) if not valid_ratio.empty else np.nan,
            "low_norm_pct": float((valid_norm <= 0.25).mean() * 100.0) if not valid_norm.empty else np.nan,
            "n_samples": int(ts["pv_power_kw"].notna().sum()),
            "n_current_samples": 0,
            "median_current_a": np.nan,
        }
        if "pv_current_a" in ts.columns:
            row["n_current_samples"] = int(ts["pv_current_a"].notna().sum())
            if ts["pv_current_a"].notna().any():
                row["median_current_a"] = float(ts["pv_current_a"].median())
        rows.append(row)

    return pd.DataFrame(rows)
