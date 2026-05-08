"""M2eAvailability submodule: hybrid inverter-level + string-proxy availability.

Spec: docs/superpowers/specs/2026-05-08-m2e-hybrid-availability-design.md
"""
from __future__ import annotations

import re
import warnings
from datetime import datetime
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from pv_pipeline.core import Severity, M2Finding, SubModule


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
    if ratio_event >= 0.80:
        return "EVENT", sd_dt, st_dt

    sd_num = pd.to_numeric(sd_clean, errors="coerce")
    st_num = pd.to_numeric(st_clean, errors="coerce")
    ratio_num = (sd_num.notna().sum() + st_num.notna().sum()) / (2 * n)
    if ratio_num >= 0.80:
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


_PV_NUM_RE = re.compile(r"PV\s*0*?(\d+)\s+Power\(kW\)", flags=re.I)


def _label_to_pv_name(label: str) -> str:
    """`PV3 Power(kW)` -> `PV3` ; fallback ke label asli."""
    m = _PV_NUM_RE.search(label)
    return f"PV{int(m.group(1))}" if m else label


def _compute_string_proxy(
    df: "pd.DataFrame",
    pv_cols: list,
    cfg: dict,
    interval_minutes: float,
) -> list:
    """Detect string proxy-down events.

    `df` harus berisi: Inverter_ID, Start Time, _status_class, kolom-kolom di pv_cols.
    """
    sp = cfg.get("string_proxy", {})
    th = cfg.get("severity_thresholds", {})
    emit_normal = bool(th.get("emit_normal", False))
    p_zero = float(sp.get("pstr_zero_threshold_kw", 0.1))
    sib_med_th = float(sp.get("sibling_median_active_kw", 1.0))
    min_act_pct = float(sp.get("min_active_siblings_pct", 50))
    debounce = int(sp.get("debounce_consecutive_steps", 2))

    daylight_ts = set(
        df.loc[df["_status_class"] == "ON", "Start Time"].dropna().unique()
    )
    if not daylight_ts:
        return []

    findings: list = []
    n_pv_total = len(pv_cols)

    for inv_id, sub in df.groupby("Inverter_ID", sort=True):
        sub = sub.sort_values("Start Time").reset_index(drop=True)
        on_mask = (sub["_status_class"] == "ON") & sub["Start Time"].isin(daylight_ts)
        sub_on = sub[on_mask]
        if sub_on.empty:
            continue

        daylight_minutes_inv = float(len(sub_on)) * float(interval_minutes)

        pv_vals = sub_on[pv_cols].astype(float)
        sib_median = pv_vals.median(axis=1, skipna=True)
        active_count = (pv_vals > p_zero).sum(axis=1)
        active_pct = 100.0 * active_count / max(n_pv_total, 1)
        qualified = (sib_median >= sib_med_th) & (active_pct >= min_act_pct)

        for pv_col in pv_cols:
            pv_name = _label_to_pv_name(pv_col)
            pv_series = pv_vals[pv_col]
            cand = qualified & (pv_series < p_zero)
            if not cand.any():
                continue

            cand_arr = cand.to_numpy()
            run_lens = []
            run_start_idx = None
            for i, v in enumerate(cand_arr):
                if v:
                    if run_start_idx is None:
                        run_start_idx = i
                else:
                    if run_start_idx is not None:
                        run_lens.append((run_start_idx, i - 1))
                        run_start_idx = None
            if run_start_idx is not None:
                run_lens.append((run_start_idx, len(cand_arr) - 1))

            qualified_runs = [(a, b) for (a, b) in run_lens if (b - a + 1) >= debounce]
            if not qualified_runs:
                continue

            event_minutes_total = sum(
                (b - a + 1) * float(interval_minutes) for (a, b) in qualified_runs
            )
            string_uptime_pct = max(
                0.0,
                100.0 - 100.0 * event_minutes_total / max(daylight_minutes_inv, 1e-9),
            )
            sev, threshold = _severity_for_uptime(string_uptime_pct, th)
            if sev == Severity.NORMAL and not emit_normal:
                continue

            ts_first = sub_on["Start Time"].iloc[qualified_runs[0][0]]
            ts_last = sub_on["Start Time"].iloc[qualified_runs[-1][1]]
            ts_dt = ts_first.to_pydatetime() if hasattr(ts_first, "to_pydatetime") else ts_first

            findings.append(M2Finding(
                timestamp=ts_dt if isinstance(ts_dt, datetime) else datetime.utcnow(),
                inverter_id=str(inv_id),
                pv_string=pv_name,
                sub_module="M2e_string_proxy",
                severity=sev,
                value=round(string_uptime_pct, 4),
                threshold=threshold,
                message=(
                    f"{inv_id} {pv_name} proxy-down "
                    f"{event_minutes_total:.0f}min/{daylight_minutes_inv:.0f}min daylight "
                    f"({string_uptime_pct:.2f}% uptime)"
                ),
                extra={
                    "event_minutes": round(event_minutes_total, 2),
                    "daylight_minutes": round(daylight_minutes_inv, 2),
                    "n_events": len(qualified_runs),
                    "first_event_ts": ts_first.isoformat() if hasattr(ts_first, "isoformat") else str(ts_first),
                    "last_event_ts": ts_last.isoformat() if hasattr(ts_last, "isoformat") else str(ts_last),
                },
            ))

    return findings


_PV_POWER_COL_RE = re.compile(r"PV\s*0*?(\d+)\s+Power\(kW\)", flags=re.I)


def _detect_pv_power_cols(df: "pd.DataFrame", pv_max_allowed: int = 28) -> list:
    cols = [c for c in df.columns if _PV_POWER_COL_RE.search(str(c))]
    out = []
    for c in cols:
        m = _PV_POWER_COL_RE.search(str(c))
        if m and int(m.group(1)) <= pv_max_allowed:
            out.append(c)
    out.sort(key=lambda x: int(_PV_POWER_COL_RE.search(x).group(1)))
    return out


def _estimate_interval_minutes(start_times: "pd.Series") -> float:
    s = pd.to_datetime(start_times, errors="coerce").dropna().sort_values()
    if len(s) < 2:
        return 5.0
    diffs = s.diff().dropna().dt.total_seconds() / 60.0
    if len(diffs) == 0:
        return 5.0
    med = float(diffs.median())
    if med <= 0 or med != med:
        return 5.0
    return med


class M2eAvailability(SubModule):
    """Hybrid M2e: inverter-level + string-proxy availability."""
    name = "M2e_hybrid"

    def run(self, combined_df: "pd.DataFrame", config: dict) -> list:
        cfg = (config or {}).get("m2e", {}) or {}

        required = ["Inverter_ID", "Start Time", "Inverter status"]
        missing = [c for c in required if c not in combined_df.columns]
        if missing:
            warnings.warn(f"[M2e] missing required columns {missing}; skipping submodule")
            return []

        df = combined_df.copy()
        df["Start Time"] = pd.to_datetime(df["Start Time"], errors="coerce")

        keymap = cfg.get("inverter_status_map", {}) or {}
        df["_status_class"] = df["Inverter status"].apply(lambda s: _classify_status(s, keymap))
        unknown_mask = df["_status_class"] == "UNKNOWN"
        if unknown_mask.any():
            unk = df.loc[unknown_mask, "Inverter status"].astype(str).value_counts().head(20)
            warnings.warn(f"[M2e] UNKNOWN status values (top 20):\n{unk.to_string()}")

        force = (cfg.get("shutdown_time_detection") or "auto")
        sd_col = df["Inverter shutdown time"] if "Inverter shutdown time" in df.columns else None
        st_col = df["Inverter startup time"] if "Inverter startup time" in df.columns else None
        if sd_col is None or st_col is None:
            mode, sd_parsed, st_parsed = "STATUS_ONLY", None, None
        else:
            mode, sd_parsed, st_parsed = _detect_shutdown_time_mode(sd_col, st_col, force=force)
        print(f"[M2e] shutdown_time mode = {mode}")

        interval_min = _estimate_interval_minutes(df["Start Time"])
        inv_findings = _compute_inverter_availability(
            df, mode=mode, cfg=cfg, interval_minutes=interval_min,
            sd_parsed=sd_parsed, st_parsed=st_parsed,
        )

        pv_cols = _detect_pv_power_cols(df, pv_max_allowed=28)
        if not pv_cols:
            warnings.warn("[M2e] no PV power columns found; skipping string-proxy")
            str_findings = []
        else:
            str_findings = _compute_string_proxy(
                df, pv_cols=pv_cols, cfg=cfg, interval_minutes=interval_min,
            )

        return inv_findings + str_findings


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

    s_dt = pd.Series(["2026/05/06 18:26:40", "2026/05/06 18:21:02", "2026/05/06 18:30:00", "2026/05/06 18:25:01"])
    s_st = pd.Series(["2026/05/06 06:08:46", "2026/05/06 06:08:48", "2026/05/06 06:09:30", "2026/05/06 06:09:03"])
    mode, sd_dt, st_dt = _detect_shutdown_time_mode(s_dt, s_st, force=None)
    assert mode == "EVENT", f"expected EVENT got {mode}"
    assert sd_dt.notna().sum() == 4
    assert st_dt.notna().sum() == 4

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

    # _compute_string_proxy test
    from pv_pipeline.availability import _compute_string_proxy

    times = list(pd.date_range("2026-05-07 10:00", periods=6, freq="5min"))
    rows = []
    for t in times:
        rows.append({"Inverter_ID": "WB02-INV01", "Start Time": t, "_status_class": "ON",
                     "PV1 Power(kW)": 0.0, "PV2 Power(kW)": 5.0,
                     "PV3 Power(kW)": 5.5, "PV4 Power(kW)": 4.8})
    for t in times:
        rows.append({"Inverter_ID": "WB02-INV02", "Start Time": t, "_status_class": "ON",
                     "PV1 Power(kW)": 5.0, "PV2 Power(kW)": 5.0,
                     "PV3 Power(kW)": 5.5, "PV4 Power(kW)": 4.8})
    df_test = pd.DataFrame(rows)
    pv_cols = ["PV1 Power(kW)", "PV2 Power(kW)", "PV3 Power(kW)", "PV4 Power(kW)"]

    cfg = {
        "string_proxy": {
            "pstr_zero_threshold_kw": 0.1,
            "sibling_median_active_kw": 1.0,
            "min_active_siblings_pct": 50,
            "debounce_consecutive_steps": 2,
        },
        "severity_thresholds": {"critical_below": 90, "high_below": 95,
                                "medium_below": 97, "info_below": 99,
                                "emit_normal": False},
    }
    findings = _compute_string_proxy(df_test, pv_cols=pv_cols, cfg=cfg, interval_minutes=5)
    assert len(findings) == 1, f"expected 1 string-proxy finding, got {len(findings)}"
    f0 = findings[0]
    assert f0.inverter_id == "WB02-INV01"
    assert f0.pv_string == "PV1"
    assert f0.severity.value in {"CRITICAL", "HIGH", "MEDIUM", "INFO"}
    assert f0.extra["n_events"] >= 1
    print("[availability] _compute_string_proxy smoke OK")

    # M2eAvailability.run() end-to-end smoke
    from pv_pipeline.availability import M2eAvailability

    times = list(pd.date_range("2026-05-07 10:00", periods=6, freq="5min"))
    rows = []
    for t in times:
        rows.append({"ManageObject": "x/Inv_A_201_IKN", "Inverter_ID": "WB02-INV01",
                     "Start Time": t, "Inverter status": "Grid connected",
                     "Inverter shutdown time": "-", "Inverter startup time": "-",
                     "PV1 Power(kW)": 0.0, "PV2 Power(kW)": 5.0,
                     "PV3 Power(kW)": 5.5, "PV4 Power(kW)": 4.8})
    for t in times:
        rows.append({"ManageObject": "x/Inv_A_202_IKN", "Inverter_ID": "WB02-INV02",
                     "Start Time": t, "Inverter status": "Grid connected",
                     "Inverter shutdown time": "-", "Inverter startup time": "-",
                     "PV1 Power(kW)": 5.0, "PV2 Power(kW)": 5.0,
                     "PV3 Power(kW)": 5.5, "PV4 Power(kW)": 4.8})
    df_e2e = pd.DataFrame(rows)

    from pv_pipeline.m2_config import DEFAULT_M2_CONFIG
    sm = M2eAvailability()
    res = sm.run(df_e2e, DEFAULT_M2_CONFIG)
    assert isinstance(res, list)
    sub_string = [f for f in res if f.sub_module == "M2e_string_proxy"]
    assert len(sub_string) == 1
    assert sub_string[0].pv_string == "PV1"
    print("[availability] M2eAvailability.run() smoke OK")
