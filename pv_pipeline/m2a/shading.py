"""M2a Shading Detection via Diurnal CV + PR-proxy (Fase 3 Part 2 Task #4).

Detects WHOLE-INVERTER uniform shading patterns (terrain shadow, building
shadow, fog/cloud band) by combining two signals per hour-of-day:

1. **CV per hour** -- coefficient of variation of per-PV power samples
   within that hour. Low CV = uniform underperformance across PV strings
   (consistent with whole-array shading). High CV = mixed, more
   consistent with partial-PV shading (better detected by M2b detectors).

2. **PR-proxy per hour** -- mean inverter power normalized by mean POA
   over the hour. Lower PR-proxy than the day's median = underperforming.

Combined rule:
    Suspicious hour := (CV_h < cv_low_mult * median_CV)
                   AND (PR_proxy_h < pr_low_mult * median_PR_proxy)

Confirmation via diurnal asymmetry
----------------------------------
Site PLTS-IKN is at latitude -0.99 (slightly south of equator), panels
all face NORTH (azimuth=0, tilt=10 deg). Under clear sky, normal
performance is roughly SYMMETRIC AM vs PM (no orientation-driven
asymmetry).

So if suspicious hours concentrate ASYMMETRICALLY in AM-only or PM-only,
it's consistent with terrain/building shadow (sun blocked from one side
of the day). If suspicious hours are symmetric (AM == PM), it's more
consistent with soiling or persistent cloud cover -- which is M2a-Soiling
territory (Task #5), not shading.

Classification:
    N_am = count(suspicious hours where h < am_pm_split_hour)
    N_pm = count(suspicious hours where h >= am_pm_split_hour)
    asymmetry = |N_am - N_pm| / max(N_am + N_pm, 1)
    if asymmetry < asymmetry_threshold:
        -> fault_type = "shading_uniform"   (likely soiling or cloud, weaker signal)
    elif N_am > N_pm:
        -> fault_type = "shading_morning"   (east-side terrain shadow)
    else:
        -> fault_type = "shading_afternoon" (west-side terrain shadow)

Note on partial shading (single-PV obstruction like a tree branch on one
string): detected by M2b peer_zscore (high-R) or M2b open_circuit, NOT
here. M2a operates at inverter aggregate level.

Note on moving shadow (e.g., construction crane): weak signal at hourly
aggregation level. Master Context flags this as requiring thermography
drone for ground-truth.

Default OFF (opt-in via config["m2a_shading"]["enabled"]=True), mirror
Wave 9 / M2IForest pattern.

Outputs
-------
    Findings : one M2Finding per suspicious hour per inverter.
               fault_type = "shading_morning" / "shading_afternoon" / "shading_uniform".
               severity   = CRITICAL/HIGH/MEDIUM/INFO based on
                            (suspicious_count, asymmetry_strength).
               value      = PR-proxy ratio at that hour.
               threshold  = PR-proxy threshold for "suspicious".
               evidence   = {hour, cv, cv_threshold, pr_proxy, pr_threshold,
                             n_samples, am_pm}.

    artifacts["HourlyMetrics"]   : per (inverter, hour) full table
                                   (cv, pr_proxy, suspicious flag, am_pm).
    artifacts["ShadingSummary"]  : per inverter (n_suspicious_hours,
                                   n_am, n_pm, asymmetry, fault_type).
"""
from __future__ import annotations

import warnings
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from pv_pipeline.core import M2Finding, Severity, SubModule, load_empty_pv_map


# --- Defaults (mirror DEFAULT_M2_CONFIG["m2a_shading"]) ---------------------
DEFAULT_ENABLED: bool = False
DEFAULT_POA_THRESHOLD_WM2: float = 100.0       # daylight gate (lower than M2b)
DEFAULT_HOUR_RANGE: Tuple[float, float] = (6.0, 18.0)
DEFAULT_CV_LOW_MULTIPLIER: float = 0.5         # CV_h < 0.5 * median(CV) -> low
DEFAULT_PR_LOW_MULTIPLIER: float = 0.85        # PR_h < 0.85 * median(PR) -> low
DEFAULT_MIN_SAMPLES_PER_HOUR: int = 5          # need enough for stable CV
DEFAULT_MIN_HOURS_FOR_ANALYSIS: int = 4        # need >=4 hours for median ref
DEFAULT_AM_PM_SPLIT_HOUR: float = 12.0
DEFAULT_ASYMMETRY_THRESHOLD: float = 0.5
DEFAULT_PV_MAX: int = 28

# Column templates (Huawei xlsx schema).
PV_V_COL_TEMPLATE: str = "PV{pv} input voltage(V)"
PV_I_COL_TEMPLATE: str = "PV{pv} input current(A)"
PV_POWER_COL_TEMPLATE: str = "PV{pv} Power(kW)"   # availability.py uses this

INVERTER_SHUTDOWN_COL_CANDIDATES: List[str] = [
    "Inverter shutdown time",
    "Shutdown time",
]


# --- Utilities ---------------------------------------------------------------
def _find_shutdown_col(df: pd.DataFrame) -> Optional[str]:
    for cand in INVERTER_SHUTDOWN_COL_CANDIDATES:
        if cand in df.columns:
            return cand
    return None


def _wb_from_inverter_id(inverter_id: str) -> str:
    """``WB05-INV12`` -> ``"WB05"``."""
    if not inverter_id:
        return ""
    parts = str(inverter_id).split("-")
    return parts[0].upper() if parts else str(inverter_id).upper()


def _normalize_pv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Wave 11 hotfix #11 mirror: normalize Title Case V/I cols to lowercase."""
    rename_map = {}
    for col in df.columns:
        if "Input Voltage" in col:
            rename_map[col] = col.replace("Input Voltage", "input voltage")
        elif "Input Current" in col:
            rename_map[col] = col.replace("Input Current", "input current")
    if rename_map:
        return df.rename(columns=rename_map)
    return df


def build_pv_power_matrix(
    group: pd.DataFrame,
    pv_indices: List[int],
) -> Tuple[np.ndarray, np.ndarray]:
    """Build per-PV per-timestamp power matrix (kW).

    Prefer ``PV{n} Power(kW)`` column when available (already kW). Else
    fall back to V * I / 1000 (W -> kW).

    Returns
    -------
    p_mat : np.ndarray, shape (N_ts, N_pv)
        Power per (timestamp, PV) in kW. NaN where source data missing.
    inv_total : np.ndarray, shape (N_ts,)
        Sum across PVs per timestamp (kW) -- inverter DC input proxy.
        nansum so partially-NaN rows still produce a value.
    """
    n_ts = len(group)
    n_pv = len(pv_indices)
    p_mat = np.full((n_ts, n_pv), np.nan, dtype=float)

    for j, pv in enumerate(pv_indices):
        p_col = PV_POWER_COL_TEMPLATE.format(pv=pv)
        if p_col in group.columns:
            p_mat[:, j] = pd.to_numeric(group[p_col], errors="coerce").to_numpy()
            continue
        # Fallback: V * I / 1000
        v_col = PV_V_COL_TEMPLATE.format(pv=pv)
        i_col = PV_I_COL_TEMPLATE.format(pv=pv)
        if v_col in group.columns and i_col in group.columns:
            v = pd.to_numeric(group[v_col], errors="coerce").to_numpy()
            i = pd.to_numeric(group[i_col], errors="coerce").to_numpy()
            p_mat[:, j] = (v * i) / 1000.0

    inv_total = np.nansum(p_mat, axis=1)
    return p_mat, inv_total


def compute_hourly_metrics(
    p_mat: np.ndarray,
    inv_total: np.ndarray,
    poa: np.ndarray,
    hours: np.ndarray,
    *,
    min_samples_per_hour: int = DEFAULT_MIN_SAMPLES_PER_HOUR,
) -> pd.DataFrame:
    """Aggregate per-hour CV + PR-proxy.

    Parameters
    ----------
    p_mat : ndarray, shape (N_ts, N_pv)
        Per-PV per-timestamp power (kW).
    inv_total : ndarray, shape (N_ts,)
        Per-timestamp inverter total power (kW).
    poa : ndarray, shape (N_ts,)
        Per-timestamp POA irradiance (W/m^2).
    hours : ndarray, shape (N_ts,)
        Hour-of-day (float, e.g. 9.5 for 09:30).
    min_samples_per_hour : int
        Skip hours with fewer per-PV samples (raises CV instability).

    Returns
    -------
    DataFrame indexed by hour (int 0..23) with columns:
        cv          : coefficient of variation of per-PV power within hour
        pr_proxy    : mean(inv_total) / mean(POA) within hour
        n_samples   : count of valid (PV, ts) samples
        mean_poa    : mean POA in hour
        mean_inv    : mean inverter power in hour
    """
    rows: List[dict] = []
    hour_ints = np.floor(hours).astype(int)

    for h in sorted(set(hour_ints.tolist())):
        if h < 0 or h > 23:
            continue
        mask_h = hour_ints == h
        if not mask_h.any():
            continue

        # Per-timestamp CV across sibling PVs, then median-aggregate over hour.
        # This isolates inter-PV (cross-string) variation from intra-hour
        # temporal variation (sun rising within the hour). Median is robust
        # to outliers (e.g., one bad PV timestamp).
        p_h = p_mat[mask_h, :]  # shape (N_ts_in_hour, N_pv)
        cv_per_ts: List[float] = []
        for row_idx in range(p_h.shape[0]):
            row_samples = p_h[row_idx, :]
            row_clean = row_samples[np.isfinite(row_samples) & (row_samples > 0)]
            if row_clean.size < 2:
                continue
            mean_row = np.mean(row_clean)
            if mean_row <= 0:
                continue
            cv_per_ts.append(float(np.std(row_clean) / mean_row))

        # Count of valid (PV, ts) samples in hour for diagnostic / gating.
        p_flat = p_h.flatten()
        n_valid_samples = int(((np.isfinite(p_flat)) & (p_flat > 0)).sum())
        if n_valid_samples < min_samples_per_hour:
            continue
        # CV requires >=2 sibling PVs per timestamp. Allow NaN when not
        # computable (e.g., single-PV test fixture); PR-proxy still emitted.
        cv = float(np.median(cv_per_ts)) if cv_per_ts else float("nan")

        # Inverter-level mean for PR proxy.
        inv_h = inv_total[mask_h]
        poa_h = poa[mask_h]
        inv_valid = inv_h[np.isfinite(inv_h)]
        poa_valid = poa_h[np.isfinite(poa_h) & (poa_h > 0)]
        if inv_valid.size == 0 or poa_valid.size == 0:
            pr_proxy = np.nan
            mean_poa = np.nan
            mean_inv = np.nan
        else:
            mean_inv = float(np.mean(inv_valid))
            mean_poa = float(np.mean(poa_valid))
            pr_proxy = mean_inv / max(mean_poa, 1e-6)

        rows.append({
            "hour": h,
            "cv": cv,
            "pr_proxy": pr_proxy,
            "n_samples": n_valid_samples,
            "mean_poa": mean_poa,
            "mean_inv": mean_inv,
        })

    if not rows:
        return pd.DataFrame(columns=["hour", "cv", "pr_proxy", "n_samples",
                                      "mean_poa", "mean_inv"]).set_index("hour")
    return pd.DataFrame(rows).set_index("hour")


def classify_shading(
    n_am: int,
    n_pm: int,
    *,
    asymmetry_threshold: float = DEFAULT_ASYMMETRY_THRESHOLD,
) -> Tuple[str, float]:
    """Classify shading type from AM/PM suspicious-hour counts.

    Returns
    -------
    fault_type : "shading_morning" | "shading_afternoon" | "shading_uniform"
    asymmetry  : |N_am - N_pm| / max(N_am + N_pm, 1), in [0, 1]
    """
    total = max(n_am + n_pm, 1)
    asymmetry = abs(n_am - n_pm) / total
    if asymmetry < asymmetry_threshold:
        return "shading_uniform", asymmetry
    return ("shading_morning" if n_am > n_pm else "shading_afternoon"), asymmetry


def _severity_from_counts(
    n_suspicious: int,
    asymmetry: float,
    *,
    total_hours: int,
) -> Severity:
    """Severity ladder by suspicious-hour fraction + asymmetry strength.

    Higher suspicious fraction + stronger asymmetry -> more confident shading.
    """
    if total_hours <= 0:
        return Severity.INFO
    frac = n_suspicious / total_hours
    # Combined score: high fraction AND high asymmetry both contribute.
    score = (frac * 0.7) + (asymmetry * 0.3)
    if score >= 0.6:
        return Severity.CRITICAL
    if score >= 0.4:
        return Severity.HIGH
    if score >= 0.2:
        return Severity.MEDIUM
    return Severity.INFO


class M2aShading(SubModule):
    """Whole-inverter shading detector via Diurnal CV + PR-proxy.

    Dependency injection (optional):
        prov = POAProvider.from_yaml(...)
        sm = M2aShading(poa=prov)
    """

    name: str = "M2a_shading"

    def __init__(self, poa=None):
        super().__init__()
        self.poa = poa

    def _ensure_providers(self, config: dict) -> None:
        if self.poa is None:
            from pv_pipeline.poa.provider import POAProvider
            geom_path = (
                config.get("poa", {})
                .get("site_geometry_path", "config/site_geometry.yaml")
            )
            self.poa = POAProvider.from_yaml(geom_path)

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        cfg = config.get("m2a_shading", {}) or {}
        enabled = bool(cfg.get("enabled", DEFAULT_ENABLED))
        if not enabled:
            return []

        poa_threshold = float(cfg.get("poa_threshold_wm2", DEFAULT_POA_THRESHOLD_WM2))
        hour_lo, hour_hi = cfg.get("hour_range", DEFAULT_HOUR_RANGE)
        hour_lo = float(hour_lo)
        hour_hi = float(hour_hi)
        cv_low_mult = float(cfg.get("cv_low_multiplier", DEFAULT_CV_LOW_MULTIPLIER))
        pr_low_mult = float(cfg.get("pr_low_multiplier", DEFAULT_PR_LOW_MULTIPLIER))
        min_samples_per_hour = int(cfg.get(
            "min_samples_per_hour", DEFAULT_MIN_SAMPLES_PER_HOUR
        ))
        min_hours = int(cfg.get(
            "min_hours_for_analysis", DEFAULT_MIN_HOURS_FOR_ANALYSIS
        ))
        am_pm_split = float(cfg.get("am_pm_split_hour", DEFAULT_AM_PM_SPLIT_HOUR))
        asymmetry_thr = float(cfg.get("asymmetry_threshold", DEFAULT_ASYMMETRY_THRESHOLD))
        pv_max = int(cfg.get("pv_max", DEFAULT_PV_MAX))

        if "Inverter_ID" not in combined_df.columns or "Start Time" not in combined_df.columns:
            warnings.warn(
                "[M2aShading] missing 'Inverter_ID' or 'Start Time'; skipping.",
                stacklevel=2,
            )
            return []

        combined_df = _normalize_pv_columns(combined_df)
        empty_map = load_empty_pv_map(config)

        try:
            self._ensure_providers(config)
        except Exception as exc:
            warnings.warn(
                f"[M2aShading] POA provider init failed: {exc.__class__.__name__}: {exc}. "
                "Detector requires POA; skipping.",
                stacklevel=2,
            )
            return []

        findings: List[M2Finding] = []
        hourly_rows: List[dict] = []
        summary_rows: List[dict] = []

        for inverter_id, group in combined_df.groupby("Inverter_ID"):
            wb_id = _wb_from_inverter_id(inverter_id)
            inv_empties = set(int(n) for n in empty_map.get(str(inverter_id).upper(), []))
            pv_indices = [n for n in range(1, pv_max + 1) if n not in inv_empties]
            if not pv_indices:
                continue

            timestamps = pd.to_datetime(group["Start Time"], errors="coerce")
            valid_idx = timestamps.notna()
            if valid_idx.sum() == 0:
                continue
            ts_clean = pd.DatetimeIndex(timestamps[valid_idx].values)
            group_clean = group.loc[valid_idx].copy()
            group_clean.index = ts_clean

            # Hour-of-day filter (daylight band).
            hours = ts_clean.hour + ts_clean.minute / 60.0
            mask_daylight = (hours >= hour_lo) & (hours < hour_hi)
            if mask_daylight.sum() == 0:
                continue
            ts_day = ts_clean[mask_daylight]
            group_day = group_clean.loc[ts_day]
            hours_day = hours[mask_daylight]

            # POA gate.
            try:
                poa_series = self.poa.get_poa(ts_day, wb_id, source="auto")
            except Exception as exc:
                warnings.warn(
                    f"[M2aShading] POA query failed (wb={wb_id}): "
                    f"{exc.__class__.__name__}: {exc}. Skipping inverter.",
                    stacklevel=2,
                )
                continue
            poa_aligned = poa_series.reindex(ts_day).fillna(0.0)
            mask_poa = poa_aligned.values > poa_threshold
            if mask_poa.sum() == 0:
                continue
            ts_qual = ts_day[mask_poa]
            group_qual = group_day.iloc[mask_poa]
            poa_qual = poa_aligned.values[mask_poa]
            hours_qual = hours_day[mask_poa]

            # Build power matrix + hourly metrics.
            p_mat, inv_total = build_pv_power_matrix(group_qual, pv_indices)
            hourly = compute_hourly_metrics(
                p_mat, inv_total, poa_qual, hours_qual,
                min_samples_per_hour=min_samples_per_hour,
            )
            if hourly.empty or len(hourly) < min_hours:
                continue

            # Reference thresholds (per inverter, this day).
            valid_cv = hourly["cv"].dropna()
            valid_pr = hourly["pr_proxy"].dropna()
            if valid_cv.empty or valid_pr.empty:
                continue
            cv_median = float(valid_cv.median())
            pr_median = float(valid_pr.median())
            cv_threshold = cv_low_mult * cv_median
            pr_threshold = pr_low_mult * pr_median

            # Suspicious hour mask.
            suspicious = (
                (hourly["cv"] < cv_threshold) & (hourly["pr_proxy"] < pr_threshold)
            ).fillna(False)
            n_suspicious = int(suspicious.sum())

            # AM/PM split.
            am_mask = pd.Series(hourly.index < am_pm_split, index=hourly.index)
            pm_mask = ~am_mask
            n_am = int((suspicious & am_mask).sum())
            n_pm = int((suspicious & pm_mask).sum())
            fault_type, asymmetry = classify_shading(
                n_am, n_pm, asymmetry_threshold=asymmetry_thr,
            )

            # Severity ladder.
            total_hours = len(hourly)
            severity = _severity_from_counts(
                n_suspicious, asymmetry, total_hours=total_hours,
            )

            # Hourly artifact rows (all hours, with suspicious flag).
            for h, row in hourly.iterrows():
                hourly_rows.append({
                    "inverter_id": inverter_id,
                    "hour": int(h),
                    "cv": float(row["cv"]) if pd.notna(row["cv"]) else float("nan"),
                    "pr_proxy": float(row["pr_proxy"]) if pd.notna(row["pr_proxy"]) else float("nan"),
                    "n_samples": int(row["n_samples"]),
                    "mean_poa": float(row["mean_poa"]) if pd.notna(row["mean_poa"]) else float("nan"),
                    "mean_inv": float(row["mean_inv"]) if pd.notna(row["mean_inv"]) else float("nan"),
                    "cv_threshold": cv_threshold,
                    "pr_threshold": pr_threshold,
                    "suspicious": bool(suspicious.loc[h]),
                    "am_pm": "AM" if h < am_pm_split else "PM",
                })

            # Summary row per inverter.
            summary_rows.append({
                "inverter_id": inverter_id,
                "total_hours": total_hours,
                "n_suspicious": n_suspicious,
                "n_am": n_am,
                "n_pm": n_pm,
                "asymmetry": asymmetry,
                "fault_type": fault_type if n_suspicious > 0 else "no_shading",
                "severity": severity.value if n_suspicious > 0 else "NORMAL",
                "cv_median": cv_median,
                "pr_median": pr_median,
                "cv_threshold": cv_threshold,
                "pr_threshold": pr_threshold,
            })

            # Emit findings (one per suspicious hour).
            if n_suspicious == 0:
                continue
            day_ts = ts_qual[0].normalize() if len(ts_qual) > 0 else datetime.utcnow()
            for h, row in hourly.iterrows():
                if not suspicious.loc[h]:
                    continue
                # Anchor finding timestamp at hour midpoint.
                ts_h = day_ts + pd.Timedelta(hours=int(h), minutes=30)
                findings.append(M2Finding(
                    timestamp=ts_h.to_pydatetime() if hasattr(ts_h, "to_pydatetime") else ts_h,
                    inverter_id=str(inverter_id),
                    pv_string=None,                  # inverter-aggregate
                    sub_module=self.name,
                    severity=severity,
                    value=float(row["pr_proxy"]),
                    threshold=pr_threshold,
                    message=(
                        f"Shading at h={h} ({fault_type}); "
                        f"cv={row['cv']:.3f} < {cv_threshold:.3f}, "
                        f"pr_proxy={row['pr_proxy']:.4f} < {pr_threshold:.4f}, "
                        f"asymmetry={asymmetry:.2f} (N_am={n_am}, N_pm={n_pm})"
                    ),
                    fault_type=fault_type,
                    confidence=float(50.0 + asymmetry * 50.0),  # 50..100
                    evidence={
                        "hour": int(h),
                        "cv": float(row["cv"]),
                        "cv_threshold": cv_threshold,
                        "cv_median_day": cv_median,
                        "pr_proxy": float(row["pr_proxy"]),
                        "pr_threshold": pr_threshold,
                        "pr_median_day": pr_median,
                        "n_samples_hour": int(row["n_samples"]),
                        "am_pm": "AM" if h < am_pm_split else "PM",
                        "n_suspicious_total": n_suspicious,
                        "n_am_total": n_am,
                        "n_pm_total": n_pm,
                        "asymmetry": asymmetry,
                    },
                ))

        if hourly_rows:
            self.artifacts["HourlyMetrics"] = pd.DataFrame(hourly_rows)
        if summary_rows:
            self.artifacts["ShadingSummary"] = pd.DataFrame(summary_rows)

        return findings


if __name__ == "__main__":  # pragma: no cover
    # Smoke test: classify_shading edge cases.
    assert classify_shading(0, 0) == ("shading_uniform", 0.0)
    assert classify_shading(3, 0)[0] == "shading_morning"
    assert classify_shading(0, 3)[0] == "shading_afternoon"
    assert classify_shading(2, 2)[0] == "shading_uniform"
    assert classify_shading(4, 1, asymmetry_threshold=0.5)[0] == "shading_morning"
    print("[m2a.shading] classify_shading smoke OK")

    # Smoke test: compute_hourly_metrics on synthetic uniform data.
    n_ts = 60
    n_pv = 5
    rng = np.random.default_rng(0)
    p_mat = 5.0 + rng.normal(0, 0.2, size=(n_ts, n_pv))
    inv_total = p_mat.sum(axis=1)
    poa = np.linspace(200, 800, n_ts)
    hours = np.linspace(9, 14, n_ts)
    hrly = compute_hourly_metrics(p_mat, inv_total, poa, hours)
    print(f"[m2a.shading] hourly metrics shape: {hrly.shape}, cols: {list(hrly.columns)}")
    print("[m2a.shading] smoke OK")
