"""M2eAvailability submodule: hybrid inverter-level + string-proxy availability.

Spec: docs/superpowers/specs/2026-05-08-m2e-hybrid-availability-design.md
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd


def _classify_status(status: Optional[str], keymap: dict) -> str:
    """Map raw status string -> {"ON","DOWN","TRANSITIONAL","UNKNOWN"}.

    Match strategy: lowercase + substring.
    Priority: down > on > transitional (down menang bila ada keyword tabrakan).
    """
    if status is None:
        return "UNKNOWN"
    try:
        s = str(status).strip().lower()
    except Exception:
        return "UNKNOWN"
    if not s:
        return "UNKNOWN"

    for kw in keymap.get("down_keywords", []) or []:
        if kw and kw.lower() in s:
            return "DOWN"
    for kw in keymap.get("on_grid_keywords", []) or []:
        if kw and kw.lower() in s:
            return "ON"
    for kw in keymap.get("transitional_keywords", []) or []:
        if kw and kw.lower() in s:
            return "TRANSITIONAL"
    return "UNKNOWN"


_SENTINEL_NULLS = {"-", "", "nan", "NaN", "None"}


def _replace_sentinels(s: "pd.Series") -> "pd.Series":
    """Ganti literal sentinel ('-', '', 'nan', dst.) dengan NaN."""
    if s is None:
        return s
    return s.where(~s.astype(str).str.strip().isin(_SENTINEL_NULLS), other=np.nan)


def _detect_shutdown_time_mode(
    shutdown_series: "pd.Series",
    startup_series: "pd.Series",
    force: Optional[str] = None,
) -> Tuple[str, "pd.Series", "pd.Series"]:
    """Auto-detect dtype mode for shutdown/startup time columns.

    Returns (mode, shutdown_parsed, startup_parsed) where mode in
    {"EVENT","CUMULATIVE","STATUS_ONLY"}.

    `force`:
        - None / "auto": auto-detect.
        - "event" / "cumulative" / "status_only": skip detection.
    """
    if force and force.lower() != "auto":
        f = force.lower()
        if f == "event":
            sd = pd.to_datetime(_replace_sentinels(shutdown_series), errors="coerce")
            st = pd.to_datetime(_replace_sentinels(startup_series), errors="coerce")
            return "EVENT", sd, st
        if f == "cumulative":
            sd = pd.to_numeric(_replace_sentinels(shutdown_series), errors="coerce")
            st = pd.to_numeric(_replace_sentinels(startup_series), errors="coerce")
            return "CUMULATIVE", sd, st
        if f == "status_only":
            return "STATUS_ONLY", shutdown_series, startup_series

    sd_clean = _replace_sentinels(shutdown_series) if shutdown_series is not None else None
    st_clean = _replace_sentinels(startup_series) if startup_series is not None else None
    if sd_clean is None or st_clean is None:
        return "STATUS_ONLY", shutdown_series, startup_series

    n = len(sd_clean)
    if n == 0:
        return "STATUS_ONLY", shutdown_series, startup_series

    sd_dt = pd.to_datetime(sd_clean, errors="coerce")
    st_dt = pd.to_datetime(st_clean, errors="coerce")
    ratio_event = (sd_dt.notna().sum() + st_dt.notna().sum()) / (2 * n)
    if ratio_event >= 0.70:
        return "EVENT", sd_dt, st_dt

    sd_num = pd.to_numeric(sd_clean, errors="coerce")
    st_num = pd.to_numeric(st_clean, errors="coerce")
    ratio_num = (sd_num.notna().sum() + st_num.notna().sum()) / (2 * n)
    if ratio_num >= 0.70:
        return "CUMULATIVE", sd_num, st_num

    return "STATUS_ONLY", shutdown_series, startup_series


if __name__ == "__main__":
    from pv_pipeline.availability import _classify_status

    keymap = {
        "on_grid_keywords": ["grid connected", "on-grid"],
        "down_keywords": ["shutdown", "fault"],
        "transitional_keywords": ["standby", "no sunlight"],
    }
    assert _classify_status("Grid connected", keymap) == "ON"
    assert _classify_status("Grid connected : power limited", keymap) == "ON"
    assert _classify_status("Standby :  no sunlight", keymap) == "TRANSITIONAL"
    assert _classify_status("Shutdown: command", keymap) == "DOWN"
    assert _classify_status("Mystery State", keymap) == "UNKNOWN"
    assert _classify_status(None, keymap) == "UNKNOWN"
    print("[availability] _classify_status smoke OK")

    # _detect_shutdown_time_mode test
    import pandas as pd
    from pv_pipeline.availability import _detect_shutdown_time_mode

    s_dt = pd.Series(["2026/05/06 18:26:40", "2026/05/06 18:21:02", "-", "2026/05/06 18:25:01"])
    s_st = pd.Series(["2026/05/06 06:08:46", "2026/05/06 06:08:48", "-", "2026/05/06 06:09:03"])
    mode, sd_dt, st_dt = _detect_shutdown_time_mode(s_dt, s_st, force=None)
    assert mode == "EVENT", f"expected EVENT got {mode}"
    assert sd_dt.notna().sum() == 3
    assert st_dt.notna().sum() == 3

    s_empty = pd.Series([None, None, None])
    mode2, _, _ = _detect_shutdown_time_mode(s_empty, s_empty, force=None)
    assert mode2 == "STATUS_ONLY"

    mode3, _, _ = _detect_shutdown_time_mode(s_dt, s_st, force="status_only")
    assert mode3 == "STATUS_ONLY"
    print("[availability] _detect_shutdown_time_mode smoke OK")
