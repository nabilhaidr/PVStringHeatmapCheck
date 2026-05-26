"""M2a Low-Irradiance Performance Check (Fase 3 Part 2 Task #6).

Detects modules underperforming specifically at LOW POA range (50-250 W/m^2)
via linear regression of PR-proxy vs POA. Disambiguates low-light
underperformance from uniform soiling by comparing low-range slope vs
mid-range slope.

Algorithm
---------
Per inverter, per analysis window (typically 1 day):

1. Filter daylight samples (POA gate + solar_elevation + shutdown).
2. Compute per-sample PR_proxy = P_inv_kw / POA_W_per_m2 (relative metric,
   no per-inverter capacity needed -- consistent dengan M2aShading).
3. Split samples into two POA bands:
       low_band  = [poa_low_min, poa_low_max]   default [50, 250]  W/m^2
       mid_band  = [poa_mid_min, poa_mid_max]   default [300, 800] W/m^2
4. Per band, fit linear regression:
       PR_proxy = a + b * POA
   Compute slope (b), intercept (a), R^2.
5. Flag rule:
       slope_low < slope_threshold (default 0.0 -> negative slope flag)
       AND r_squared_low >= r_squared_min (default 0.3)
   Means PR-proxy decreases or stays flat as POA rises di low range --
   signature of high series-resistance modules / low-light underperformance.

Classification (disambiguation vs soiling)
------------------------------------------
   - slope_low < threshold AND slope_mid >= threshold:
       fault_type = "low_irradiance_underperform"
       Low-light-specific issue. Module Rs high -> poor low-light response,
       but mid-range PR normal. Action: thermography drone scan.
   - slope_low < threshold AND slope_mid < threshold:
       fault_type = "general_underperform"
       Both bands underperform -> uniform PR drop. Likely soiling, sensor
       calibration drift, or DC cable degradation. M2a Soiling (Task #5)
       will give better signal once >=6 mo data + precipitation available.
   - slope_low >= threshold (or insufficient samples):
       no finding.

Default OFF (opt-in via config["m2a_low_irradiance"]["enabled"]=True),
mirror Wave 9 / M2IForest / M2aShading pattern.

Outputs
-------
    Findings : one M2Finding per flagged inverter.
               fault_type = "low_irradiance_underperform"
                          or "general_underperform".
               severity   = CRITICAL/HIGH/MEDIUM/INFO by |slope_low|
                            magnitude + R^2.
               value      = slope_low (negative = bad).
               threshold  = slope_threshold (decision boundary).
               evidence   = {slope_low, intercept_low, r_squared_low,
                             n_low_samples, slope_mid, intercept_mid,
                             r_squared_mid, n_mid_samples, classification}.

    artifacts["LowIrradianceFit"] : per inverter regression results.
    artifacts["LowIrradianceSummary"] : aggregate counts + thresholds.
"""
from __future__ import annotations

import warnings
from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from pv_pipeline.core import M2Finding, Severity, SubModule, load_empty_pv_map


# --- Defaults (mirror DEFAULT_M2_CONFIG["m2a_low_irradiance"]) -------------
DEFAULT_ENABLED: bool = False
DEFAULT_POA_LOW_RANGE: Tuple[float, float] = (50.0, 250.0)
DEFAULT_POA_MID_RANGE: Tuple[float, float] = (300.0, 800.0)
DEFAULT_MIN_LOW_SAMPLES: int = 30
DEFAULT_MIN_MID_SAMPLES: int = 30
DEFAULT_SLOPE_THRESHOLD: float = 0.0       # slope < 0 -> flag
DEFAULT_R_SQUARED_MIN: float = 0.3          # min fit quality
DEFAULT_HOUR_RANGE: Tuple[float, float] = (6.0, 18.0)
DEFAULT_HOUR_CUTOFF_END: float = 18.0
DEFAULT_SOLAR_ELEV_MIN_DEG: float = 5.0
DEFAULT_RESPECT_INVERTER_SHUTDOWN: bool = True
DEFAULT_PV_MAX: int = 28

# Column templates (Huawei xlsx schema). Same as M2aShading.
PV_V_COL_TEMPLATE: str = "PV{pv} input voltage(V)"
PV_I_COL_TEMPLATE: str = "PV{pv} input current(A)"
PV_POWER_COL_TEMPLATE: str = "PV{pv} Power(kW)"

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
    if not inverter_id:
        return ""
    parts = str(inverter_id).split("-")
    return parts[0].upper() if parts else str(inverter_id).upper()


def _normalize_pv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Wave 11 hotfix #11 mirror: Title Case -> lowercase canonical."""
    rename_map = {}
    for col in df.columns:
        if "Input Voltage" in col:
            rename_map[col] = col.replace("Input Voltage", "input voltage")
        elif "Input Current" in col:
            rename_map[col] = col.replace("Input Current", "input current")
    if rename_map:
        return df.rename(columns=rename_map)
    return df


def build_inverter_power_series(
    group: pd.DataFrame,
    pv_indices: List[int],
) -> np.ndarray:
    """Per-timestamp inverter total power (kW), nansum across PVs.

    Prefer ``PV{n} Power(kW)`` column when available; else V*I/1000 fallback.
    Mirrors M2aShading.build_pv_power_matrix but only returns the sum.
    """
    n_ts = len(group)
    n_pv = len(pv_indices)
    p_mat = np.full((n_ts, n_pv), np.nan, dtype=float)

    for j, pv in enumerate(pv_indices):
        p_col = PV_POWER_COL_TEMPLATE.format(pv=pv)
        if p_col in group.columns:
            p_mat[:, j] = pd.to_numeric(group[p_col], errors="coerce").to_numpy()
            continue
        v_col = PV_V_COL_TEMPLATE.format(pv=pv)
        i_col = PV_I_COL_TEMPLATE.format(pv=pv)
        if v_col in group.columns and i_col in group.columns:
            v = pd.to_numeric(group[v_col], errors="coerce").to_numpy()
            i = pd.to_numeric(group[i_col], errors="coerce").to_numpy()
            p_mat[:, j] = (v * i) / 1000.0

    return np.nansum(p_mat, axis=1)


def linear_regression_slope(
    x: np.ndarray,
    y: np.ndarray,
) -> Tuple[float, float, float, int]:
    """OLS linear regression y = a + b*x. Returns (slope, intercept, r_squared, n).

    Returns NaN slope/intercept/r2 if fewer than 2 finite samples or
    zero-variance x.
    """
    mask = np.isfinite(x) & np.isfinite(y)
    x = x[mask]
    y = y[mask]
    n = int(x.size)
    if n < 2:
        return float("nan"), float("nan"), float("nan"), n
    x_mean = float(np.mean(x))
    y_mean = float(np.mean(y))
    x_var = float(np.sum((x - x_mean) ** 2))
    if x_var <= 0:
        return float("nan"), float("nan"), float("nan"), n
    slope = float(np.sum((x - x_mean) * (y - y_mean)) / x_var)
    intercept = y_mean - slope * x_mean
    y_pred = intercept + slope * x
    ss_res = float(np.sum((y - y_pred) ** 2))
    ss_tot = float(np.sum((y - y_mean) ** 2))
    if ss_tot <= 0:
        r_squared = 1.0 if ss_res == 0 else 0.0
    else:
        r_squared = float(1.0 - (ss_res / ss_tot))
    return slope, float(intercept), r_squared, n


def classify_underperformance(
    slope_low: float,
    slope_mid: float,
    *,
    slope_threshold: float = DEFAULT_SLOPE_THRESHOLD,
) -> str:
    """Disambiguate low-light underperformance vs general (soiling).

    - low slope flagged + mid slope OK -> "low_irradiance_underperform"
    - low slope flagged + mid slope flagged -> "general_underperform"
    - low slope OK or NaN -> "normal"
    """
    low_flagged = np.isfinite(slope_low) and slope_low < slope_threshold
    mid_flagged = np.isfinite(slope_mid) and slope_mid < slope_threshold
    if low_flagged and not mid_flagged:
        return "low_irradiance_underperform"
    if low_flagged and mid_flagged:
        return "general_underperform"
    return "normal"


def _severity_from_slope(
    slope_low: float,
    r_squared_low: float,
    *,
    slope_threshold: float = DEFAULT_SLOPE_THRESHOLD,
) -> Severity:
    """Severity ladder by how far below threshold + fit quality."""
    if not np.isfinite(slope_low) or slope_low >= slope_threshold:
        return Severity.INFO
    delta = abs(slope_low - slope_threshold)
    fit_strength = max(0.0, min(1.0, r_squared_low)) if np.isfinite(r_squared_low) else 0.0
    score = delta * fit_strength
    if score >= 0.0008:
        return Severity.CRITICAL
    if score >= 0.0004:
        return Severity.HIGH
    if score >= 0.0001:
        return Severity.MEDIUM
    return Severity.INFO


class M2aLowIrradiance(SubModule):
    """Low-irradiance performance detector via PR-proxy slope in low POA range.

    Dependency injection (optional):
        prov = POAProvider.from_yaml(...)
        sm = M2aLowIrradiance(poa=prov)
    """

    name: str = "M2a_low_irradiance"

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

    def _build_gate_mask(
        self,
        group_clean: pd.DataFrame,
        ts_clean: pd.DatetimeIndex,
        wb_id: str,
        cfg: dict,
        shutdown_col: Optional[str],
    ) -> Tuple[pd.Series, np.ndarray]:
        """Compute daylight gate mask + return POA values aligned to ts_clean."""
        hour_cutoff_end = float(cfg.get("hour_cutoff_end", DEFAULT_HOUR_CUTOFF_END))
        solar_elev_min = float(cfg.get("solar_elevation_min_deg", DEFAULT_SOLAR_ELEV_MIN_DEG))
        respect_shutdown = bool(cfg.get(
            "respect_inverter_shutdown", DEFAULT_RESPECT_INVERTER_SHUTDOWN
        ))

        try:
            poa_series = self.poa.get_poa(ts_clean, wb_id, source="auto")
        except Exception as exc:
            warnings.warn(
                f"[M2aLowIrradiance] POA query failed (wb={wb_id}): "
                f"{exc.__class__.__name__}: {exc}. Skipping inverter.",
                stacklevel=2,
            )
            return pd.Series(False, index=ts_clean), np.zeros(len(ts_clean))

        poa_aligned = poa_series.reindex(ts_clean).fillna(0.0)
        mask_daylight = poa_aligned > 0.0

        try:
            elev = self.poa.get_solar_elevation(ts_clean)
            elev_aligned = elev.reindex(ts_clean)
            if elev_aligned.notna().any():
                elev_mask = elev_aligned.fillna(-90.0).values > solar_elev_min
                hour_arr = ts_clean.hour + ts_clean.minute / 60.0
                hour_mask = hour_arr < hour_cutoff_end
                mask_time = pd.Series(elev_mask & hour_mask, index=ts_clean)
            else:
                hour_arr = ts_clean.hour + ts_clean.minute / 60.0
                mask_time = pd.Series(hour_arr < hour_cutoff_end, index=ts_clean)
        except Exception:
            hour_arr = ts_clean.hour + ts_clean.minute / 60.0
            mask_time = pd.Series(hour_arr < hour_cutoff_end, index=ts_clean)

        mask_shutdown = pd.Series(True, index=ts_clean)
        if respect_shutdown and shutdown_col is not None and shutdown_col in group_clean.columns:
            raw_shut = pd.to_datetime(group_clean[shutdown_col], errors="coerce")
            valid_shut = raw_shut.dropna()
            if not valid_shut.empty:
                valid_shut = valid_shut[valid_shut.dt.year >= 2000]
            if not valid_shut.empty:
                shutdown_ts = valid_shut.min()
                proposed = pd.Series(ts_clean < shutdown_ts, index=ts_clean)
                if proposed.sum() > 0:
                    mask_shutdown = proposed

        full_mask = mask_daylight & mask_time & mask_shutdown
        return full_mask, poa_aligned.values

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        cfg = config.get("m2a_low_irradiance", {}) or {}
        enabled = bool(cfg.get("enabled", DEFAULT_ENABLED))
        if not enabled:
            return []

        poa_low_min, poa_low_max = cfg.get("poa_low_range", DEFAULT_POA_LOW_RANGE)
        poa_low_min = float(poa_low_min)
        poa_low_max = float(poa_low_max)
        poa_mid_min, poa_mid_max = cfg.get("poa_mid_range", DEFAULT_POA_MID_RANGE)
        poa_mid_min = float(poa_mid_min)
        poa_mid_max = float(poa_mid_max)
        min_low_samples = int(cfg.get("min_low_samples", DEFAULT_MIN_LOW_SAMPLES))
        min_mid_samples = int(cfg.get("min_mid_samples", DEFAULT_MIN_MID_SAMPLES))
        slope_threshold = float(cfg.get("slope_threshold", DEFAULT_SLOPE_THRESHOLD))
        r_squared_min = float(cfg.get("r_squared_min", DEFAULT_R_SQUARED_MIN))
        hour_lo, hour_hi = cfg.get("hour_range", DEFAULT_HOUR_RANGE)
        hour_lo = float(hour_lo)
        hour_hi = float(hour_hi)
        pv_max = int(cfg.get("pv_max", DEFAULT_PV_MAX))

        if "Inverter_ID" not in combined_df.columns or "Start Time" not in combined_df.columns:
            warnings.warn(
                "[M2aLowIrradiance] missing 'Inverter_ID' or 'Start Time'; skipping.",
                stacklevel=2,
            )
            return []

        combined_df = _normalize_pv_columns(combined_df)
        shutdown_col = _find_shutdown_col(combined_df)
        empty_map = load_empty_pv_map(config)

        try:
            self._ensure_providers(config)
        except Exception as exc:
            warnings.warn(
                f"[M2aLowIrradiance] POA provider init failed: "
                f"{exc.__class__.__name__}: {exc}. Skipping.",
                stacklevel=2,
            )
            return []

        findings: List[M2Finding] = []
        fit_rows: List[dict] = []
        summary_counts = {"normal": 0, "low_irradiance_underperform": 0,
                          "general_underperform": 0, "skipped": 0}

        for inverter_id, group in combined_df.groupby("Inverter_ID"):
            wb_id = _wb_from_inverter_id(inverter_id)
            inv_empties = set(int(n) for n in empty_map.get(str(inverter_id).upper(), []))
            pv_indices = [n for n in range(1, pv_max + 1) if n not in inv_empties]
            if not pv_indices:
                summary_counts["skipped"] += 1
                continue

            timestamps = pd.to_datetime(group["Start Time"], errors="coerce")
            valid_idx = timestamps.notna()
            if valid_idx.sum() == 0:
                summary_counts["skipped"] += 1
                continue
            ts_clean = pd.DatetimeIndex(timestamps[valid_idx].values)
            group_clean = group.loc[valid_idx].copy()
            group_clean.index = ts_clean

            hours = ts_clean.hour + ts_clean.minute / 60.0
            mask_hour = (hours >= hour_lo) & (hours < hour_hi)
            if mask_hour.sum() == 0:
                summary_counts["skipped"] += 1
                continue
            ts_h = ts_clean[mask_hour]
            group_h = group_clean.loc[ts_h]

            mask_gate, poa_values = self._build_gate_mask(
                group_h, ts_h, wb_id, cfg, shutdown_col,
            )
            mask_gate_arr = mask_gate.values
            if mask_gate_arr.sum() == 0:
                summary_counts["skipped"] += 1
                continue
            ts_qual = ts_h[mask_gate_arr]
            group_qual = group_h.iloc[mask_gate_arr]
            poa_qual = poa_values[mask_gate_arr]

            p_inv = build_inverter_power_series(group_qual, pv_indices)
            if not np.any(np.isfinite(p_inv) & (p_inv > 0)):
                summary_counts["skipped"] += 1
                continue

            with np.errstate(divide="ignore", invalid="ignore"):
                pr_proxy = np.where(poa_qual > 0, p_inv / poa_qual, np.nan)

            low_mask = (poa_qual >= poa_low_min) & (poa_qual <= poa_low_max)
            mid_mask = (poa_qual >= poa_mid_min) & (poa_qual <= poa_mid_max)

            slope_low, intercept_low, r2_low, n_low = linear_regression_slope(
                poa_qual[low_mask], pr_proxy[low_mask],
            )
            slope_mid, intercept_mid, r2_mid, n_mid = linear_regression_slope(
                poa_qual[mid_mask], pr_proxy[mid_mask],
            )

            if n_low < min_low_samples:
                fit_rows.append({
                    "inverter_id": inverter_id,
                    "n_low_samples": n_low,
                    "n_mid_samples": n_mid,
                    "slope_low": slope_low,
                    "intercept_low": intercept_low,
                    "r_squared_low": r2_low,
                    "slope_mid": slope_mid,
                    "intercept_mid": intercept_mid,
                    "r_squared_mid": r2_mid,
                    "classification": "insufficient_data",
                    "severity": "NORMAL",
                })
                summary_counts["skipped"] += 1
                continue

            classification = classify_underperformance(
                slope_low, slope_mid, slope_threshold=slope_threshold,
            )
            severity = _severity_from_slope(
                slope_low, r2_low, slope_threshold=slope_threshold,
            )

            summary_counts[classification] = summary_counts.get(classification, 0) + 1

            fit_rows.append({
                "inverter_id": inverter_id,
                "n_low_samples": n_low,
                "n_mid_samples": n_mid,
                "slope_low": slope_low,
                "intercept_low": intercept_low,
                "r_squared_low": r2_low,
                "slope_mid": slope_mid,
                "intercept_mid": intercept_mid,
                "r_squared_mid": r2_mid,
                "slope_threshold": slope_threshold,
                "r_squared_min": r_squared_min,
                "classification": classification,
                "severity": severity.value,
            })

            if classification == "normal":
                continue
            if not np.isfinite(r2_low) or r2_low < r_squared_min:
                continue

            day_ts = ts_qual[0].normalize() if len(ts_qual) > 0 else datetime.utcnow()
            ts_finding = day_ts + pd.Timedelta(hours=12)

            findings.append(M2Finding(
                timestamp=ts_finding.to_pydatetime() if hasattr(ts_finding, "to_pydatetime") else ts_finding,
                inverter_id=str(inverter_id),
                pv_string=None,
                sub_module=self.name,
                severity=severity,
                value=slope_low,
                threshold=slope_threshold,
                message=(
                    f"Low-irradiance underperformance ({classification}); "
                    f"slope_low={slope_low:.5f} < {slope_threshold} "
                    f"(r2={r2_low:.3f}, n={n_low}); "
                    f"slope_mid={slope_mid:.5f} (r2={r2_mid:.3f}, n={n_mid})"
                ),
                fault_type=classification,
                confidence=float(50.0 + (r2_low * 50.0)) if np.isfinite(r2_low) else 50.0,
                evidence={
                    "slope_low": slope_low,
                    "intercept_low": intercept_low,
                    "r_squared_low": r2_low,
                    "n_low_samples": n_low,
                    "poa_low_min": poa_low_min,
                    "poa_low_max": poa_low_max,
                    "slope_mid": slope_mid,
                    "intercept_mid": intercept_mid,
                    "r_squared_mid": r2_mid,
                    "n_mid_samples": n_mid,
                    "poa_mid_min": poa_mid_min,
                    "poa_mid_max": poa_mid_max,
                    "slope_threshold": slope_threshold,
                    "classification": classification,
                },
            ))

        if fit_rows:
            self.artifacts["LowIrradianceFit"] = pd.DataFrame(fit_rows)
        if summary_counts:
            self.artifacts["LowIrradianceSummary"] = pd.DataFrame([summary_counts])

        return findings


if __name__ == "__main__":  # pragma: no cover
    assert classify_underperformance(-0.001, 0.0001) == "low_irradiance_underperform"
    assert classify_underperformance(-0.001, -0.001) == "general_underperform"
    assert classify_underperformance(0.001, 0.001) == "normal"
    assert classify_underperformance(float("nan"), 0.001) == "normal"
    print("[m2a.low_irradiance] classify smoke OK")

    x = np.linspace(50, 250, 100)
    rng = np.random.default_rng(0)
    y = 0.0005 * x + 0.05 + rng.normal(0, 0.005, 100)
    slope, intercept, r2, n = linear_regression_slope(x, y)
    assert abs(slope - 0.0005) < 0.0001, f"slope={slope}"
    assert r2 > 0.9, f"r2={r2}"
    assert n == 100
    print(f"[m2a.low_irradiance] regression smoke OK (slope={slope:.5f}, r2={r2:.3f})")
