"""M2eAvailability submodule: hybrid inverter-level + string-proxy availability.

Spec: docs/superpowers/specs/2026-05-08-m2e-hybrid-availability-design.md
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from pv_pipeline.core import Severity, M2Finding


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


def _severity_for_uptime(uptime_pct: float, thresholds: dict) -> Tuple[Severity, float]:
    """Map uptime_pct -> (Severity, threshold_breached). NaN -> (NORMAL, NaN)."""
    if uptime_pct is None:
        return Severity.NORMAL, float("nan")
    try:
        v = float(uptime_pct)
    except Exception:
        return Severity.NORMAL, float("nan")
    if v != v:  # NaN
        return Severity.NORMAL, float("nan")

    if v < float(thresholds.get("critical_below", 90)):
        return Severity.CRITICAL, float(thresholds.get("critical_below", 90))
    if v < float(thresholds.get("high_below", 95)):
        return Severity.HIGH, float(thresholds.get("high_below", 95))
    if v < float(thresholds.get("medium_below", 97)):
        return Severity.MEDIUM, float(thresholds.get("medium_below", 97))
    if v < float(thresholds.get("info_below", 99)):
        return Severity.INFO, float(thresholds.get("info_below", 99))
    return Severity.NORMAL, float(thresholds.get("info_below", 99))


def _compute_inverter_availability(
    df: "pd.DataFrame",
    mode: str,
    cfg: dict,
    interval_minutes: float,
    sd_parsed: Optional["pd.Series"] = None,
    st_parsed: Optional["pd.Series"] = None,
) -> list:
    """Compute per-inverter daily uptime% + emit M2Finding(s).

    `df` harus berisi kolom: Inverter_ID, Start Time, _status_class.
    Untuk mode EVENT, opsional pass `sd_parsed`/`st_parsed` (datetime series
    aligned to df index) untuk hitung downtime_minutes lebih akurat;
    fallback selalu n_down x interval_minutes.
    """
    th = cfg.get("severity_thresholds", {})
    emit_normal = bool(th.get("emit_normal", False))

    findings = []
    for inv_id, sub in df.groupby("Inverter_ID", sort=True):
        n_on = int((sub["_status_class"] == "ON").sum())
        n_down = int((sub["_status_class"] == "DOWN").sum())
        denom = n_on + n_down

        if denom == 0:
            continue

        uptime_pct = 100.0 * n_on / denom

        downtime_min = float(n_down) * float(interval_minutes)
        if mode == "EVENT" and sd_parsed is not None and st_parsed is not None:
            try:
                sd_sub = sd_parsed.loc[sub.index]
                st_sub = st_parsed.loc[sub.index]
                ev_down = (sd_sub > st_sub) & sd_sub.notna() & st_sub.notna()
                if ev_down.any():
                    down_rows = sub.loc[ev_down, "Start Time"]
                    if len(down_rows) >= 2:
                        delta_min = (down_rows.max() - down_rows.min()).total_seconds() / 60.0
                        downtime_min = float(max(downtime_min, delta_min))
            except Exception:
                pass

        sev, threshold = _severity_for_uptime(uptime_pct, th)
        if sev == Severity.NORMAL and not emit_normal:
            continue

        ts_min = sub["Start Time"].min()
        ts_dt = ts_min.to_pydatetime() if hasattr(ts_min, "to_pydatetime") else ts_min

        findings.append(M2Finding(
            timestamp=ts_dt if isinstance(ts_dt, datetime) else datetime.utcnow(),
            inverter_id=str(inv_id),
            pv_string=None,
            sub_module="M2e_inverter",
            severity=sev,
            value=round(uptime_pct, 4),
            threshold=threshold,
            message=f"inverter uptime {uptime_pct:.2f}% (n_on={n_on}, n_down={n_down})",
            extra={
                "n_on": n_on,
                "n_down": n_down,
                "downtime_minutes": round(downtime_min, 2),
                "mode": mode,
            },
        ))

    return findings


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

    # _severity_for_uptime test
    from pv_pipeline.availability import _severity_for_uptime
    from pv_pipeline.core import Severity

    th = {"critical_below": 90, "high_below": 95, "medium_below": 97, "info_below": 99}
    assert _severity_for_uptime(85.0, th) == (Severity.CRITICAL, 90)
    assert _severity_for_uptime(92.0, th) == (Severity.HIGH, 95)
    assert _severity_for_uptime(96.5, th) == (Severity.MEDIUM, 97)
    assert _severity_for_uptime(98.0, th) == (Severity.INFO, 99)
    assert _severity_for_uptime(99.5, th) == (Severity.NORMAL, 99)
    assert _severity_for_uptime(float("nan"), th)[0] == Severity.NORMAL
    print("[availability] _severity_for_uptime smoke OK")

    # _compute_inverter_availability test
    from pv_pipeline.availability import _compute_inverter_availability

    df_test = pd.DataFrame({
        "Inverter_ID": ["WB02-INV01"]*12 + ["WB02-INV02"]*12,
        "Start Time": list(pd.date_range("2026-05-07 06:00", periods=12, freq="5min"))*2,
        "_status_class": (
            ["ON"]*8 + ["DOWN"]*4   # INV01 -> uptime 8/12 = 66.67% -> CRITICAL
            + ["ON"]*12              # INV02 -> uptime 100% -> NORMAL (skipped)
        ),
    })
    cfg = {
        "severity_thresholds": {"critical_below": 90, "high_below": 95,
                                "medium_below": 97, "info_below": 99,
                                "emit_normal": False},
    }
    findings = _compute_inverter_availability(df_test, mode="STATUS_ONLY", cfg=cfg,
                                              interval_minutes=5)
    assert len(findings) == 1, f"expected 1 finding, got {len(findings)}"
    f0 = findings[0]
    assert f0.inverter_id == "WB02-INV01"
    assert f0.severity.value == "CRITICAL"
    assert abs(f0.value - 66.6667) < 0.01
    assert f0.extra["downtime_minutes"] == 20
    print("[availability] _compute_inverter_availability smoke OK")
