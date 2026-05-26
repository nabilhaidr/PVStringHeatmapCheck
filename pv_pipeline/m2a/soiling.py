"""M2a Soiling Detection via rdtools SRR (Fase 3 Part 2 Task #5, SKELETON).

NREL's rdtools.soiling_srr() implements the Stochastic Rate and Recovery (SRR)
method for estimating insolation-weighted soiling ratio from a daily PR time
series. Pairing dengan precipitation data (optional) memberi sinyal kuat
untuk cleaning event detection (rain wash vs manual cleaning).

Status (2026-05-23)
-------------------
SKELETON ONLY -- production engagement BLOCKED on:

  1. Data accumulation: rdtools recommends >=6 months daily PR untuk
     reliable SRR (paling tidak >=90 hari untuk diagnostic run).
     Current PLTS-IKN baseline window: build via
     pv_pipeline.baseline.BaselineAccumulator (Sprint 3.3 -- saves daily
     NORMAL data to baseline/{YYYY-MM-DD}.parquet).

  2. Precipitation data: optional tapi strongly recommended untuk
     cleaning event detection. Cek BMKG API atau stasiun terdekat
     untuk daily mm/day. Tanpa precipitation, SRR masih run tapi
     cleaning_events output kurang reliable (asumsi all cleaning
     manual = false positives di tropical monsoon).

Algorithm (when data sufficient)
--------------------------------
Per analysis window (typically rolling 6-12 month):

1. Aggregate combined_df ke daily PR series:
       energy_daily_kwh   = integrate("Active power(kW)" or sum PV{n} Power)
                            per day via riemann sum
       insolation_daily   = integrate(POA_W_per_m2 / 1000) per day
                            via riemann sum (yields kWh/m^2)
       pr_daily           = energy_daily_kwh / (insolation_daily * capacity_kwp)
                            per IEC 61724-1.

2. Data sufficiency gate:
       len(pr_daily.dropna()) >= min_days (default 90) -> proceed
       otherwise -> emit "insufficient_data" finding (severity INFO),
                    skip rdtools call.

3. Optional precipitation join (if precipitation_path config provided
   and file exists):
       precip_daily = load_precipitation(precipitation_path)
       align to pr_daily.index via reindex.

4. Call rdtools.soiling.soiling_srr(...):
       sr, sr_ci, calc_info = rdtools.soiling.soiling_srr(
           energy_normalized_daily = pr_daily,
           insolation_daily = insolation_daily,
           precipitation_daily = precip_daily,   # optional
           reps = 1000,                          # Monte Carlo iterations
           confidence_level = 68.2,              # 1-sigma
       )

5. Economic analysis (cleaning recommendation):
       avg_daily_kwh   = mean(energy_daily_kwh.tail(30))
       p_loss          = 1 - sr (insolation-weighted soiling fraction)
       daily_loss_idr  = avg_daily_kwh * tariff * p_loss
       payback_days    = cleaning_cost_idr / daily_loss_idr
       if payback_days < payback_threshold (default 30):
           recommend cleaning -> emit MEDIUM/HIGH finding.

6. Emit per analysis window:
       - M2Finding (severity per p_loss + payback economics).
       - artifacts["SoilingRatio"]      : daily SR time series + CI.
       - artifacts["CleaningEvents"]    : cleaning event dates from rdtools.
       - artifacts["EconomicAnalysis"]  : p_loss, daily_loss_idr,
                                           payback_days, recommend.

Default OFF (opt-in via config["m2a_soiling"]["enabled"]=True), mirror
Wave 9 / M2IForest / M2aShading / M2aLowIrradiance pattern.

Outputs
-------
    Findings : M2Finding per analysis run.
               fault_type in {"soiling_detected", "cleaning_recommended",
                              "insufficient_data", "insufficient_dependency",
                              "rdtools_error"}.
               severity   = CRITICAL/HIGH/MEDIUM/INFO by (p_loss,
                            payback_days) economics.
               value      = p_loss (0..1, fraction lost to soiling).
               threshold  = pr_low_threshold (decision boundary).
               evidence   = {soiling_ratio, sr_ci_lower, sr_ci_upper,
                             p_loss, n_days, n_cleaning_events,
                             precip_available, avg_daily_kwh,
                             daily_loss_idr, payback_days,
                             cleaning_cost_idr, electricity_tariff_idr}.

    artifacts["SoilingRatio"]     : per-day SR + 68% CI bounds.
    artifacts["CleaningEvents"]   : detected cleaning dates (rain or manual).
    artifacts["EconomicAnalysis"] : 1-row summary dengan payback + recommend.

References
----------
- rdtools.soiling docs: https://rdtools.readthedocs.io/en/stable/
- Deceglie et al. 2018 "Quantifying Soiling Loss Directly from PV Yield"
- IEC 61724-1:2021 (PR definition).
"""
from __future__ import annotations

import os
import warnings
from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from pv_pipeline.core import M2Finding, Severity, SubModule, load_empty_pv_map


# --- Defaults (mirror DEFAULT_M2_CONFIG["m2a_soiling"]) --------------------
DEFAULT_ENABLED: bool = False
DEFAULT_MIN_DAYS: int = 90                   # minimum data window
DEFAULT_RECOMMENDED_DAYS: int = 180          # recommended window (6 months)
DEFAULT_CAPACITY_KWP: float = 71500.0        # PLTS-IKN total DC capacity
DEFAULT_CLEANING_COST_IDR: float = 0.0       # placeholder; user provides
DEFAULT_ELECTRICITY_TARIFF_IDR: float = 1500.0  # IDR/kWh estimate (IKN PLTS PPA)
DEFAULT_PAYBACK_THRESHOLD_DAYS: float = 30.0
DEFAULT_PRECIPITATION_PATH: str = ""         # empty -> skip precipitation
DEFAULT_PV_MAX: int = 28
DEFAULT_HOUR_RANGE: Tuple[float, float] = (6.0, 18.0)
DEFAULT_RDTOOLS_REPS: int = 1000             # Monte Carlo reps
DEFAULT_RDTOOLS_CONFIDENCE: float = 68.2     # 1-sigma confidence level
DEFAULT_SAMPLE_FREQ_HOURS: float = 5.0 / 60.0  # 5-min Huawei sampling

# Huawei xlsx columns (preferred: explicit per-inverter active power).
ACTIVE_POWER_COL_CANDIDATES: List[str] = [
    "Active power(kW)",
    "Active Power(kW)",
    "active power(kW)",
]
PV_V_COL_TEMPLATE: str = "PV{pv} input voltage(V)"
PV_I_COL_TEMPLATE: str = "PV{pv} input current(A)"
PV_POWER_COL_TEMPLATE: str = "PV{pv} Power(kW)"


# --- Utilities ---------------------------------------------------------------
def _ensure_rdtools() -> bool:
    """Auto-install rdtools on first use (mirror ``_ensure_pvlib`` pattern).

    Returns True if rdtools is available, False if install fails (caller
    falls back to "insufficient_dependency" finding).
    """
    try:
        import rdtools  # noqa: F401
        return True
    except ImportError:  # pragma: no cover
        import subprocess
        import sys
        try:
            print("Installing rdtools for M2aSoiling detector...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "rdtools"],
                timeout=300,
            )
            import rdtools  # noqa: F401
            return True
        except Exception as exc:
            warnings.warn(
                f"[M2aSoiling] rdtools auto-install failed: {exc}. "
                "Install manually: pip install rdtools.",
                stacklevel=2,
            )
            return False


def _find_active_power_col(df: pd.DataFrame) -> Optional[str]:
    for cand in ACTIVE_POWER_COL_CANDIDATES:
        if cand in df.columns:
            return cand
    return None


def _normalize_pv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Wave 11 hotfix #11 mirror."""
    rename_map = {}
    for col in df.columns:
        if "Input Voltage" in col:
            rename_map[col] = col.replace("Input Voltage", "input voltage")
        elif "Input Current" in col:
            rename_map[col] = col.replace("Input Current", "input current")
    if rename_map:
        return df.rename(columns=rename_map)
    return df


def compute_inverter_power_per_timestamp(
    group: pd.DataFrame,
    pv_indices: List[int],
) -> np.ndarray:
    """Per-timestamp inverter power (kW). Prefer Active power column.

    Order of preference:
      1. "Active power(kW)" if present (Huawei direct).
      2. Sum of "PV{n} Power(kW)" across pv_indices.
      3. Sum of V*I/1000 across pv_indices (fallback).
    """
    n_ts = len(group)
    apc = _find_active_power_col(group)
    if apc is not None:
        return pd.to_numeric(group[apc], errors="coerce").to_numpy()

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


def aggregate_daily(
    timestamps: pd.DatetimeIndex,
    values: np.ndarray,
    *,
    freq_hours: float = DEFAULT_SAMPLE_FREQ_HOURS,
) -> pd.Series:
    """Riemann-sum integration: per-day sum(values * freq_hours).

    Skips NaN values. Returns pd.Series indexed by date (pd.Timestamp at
    midnight).
    """
    if len(timestamps) == 0:
        return pd.Series(dtype=float)
    s = pd.Series(values, index=timestamps).dropna()
    if s.empty:
        return pd.Series(dtype=float)
    daily = s.groupby(s.index.normalize()).sum() * freq_hours
    daily.index = pd.DatetimeIndex(daily.index)
    return daily


def compute_daily_pr_series(
    pr_energy_daily: pd.Series,
    insolation_daily_kwh_per_m2: pd.Series,
    capacity_kwp: float,
) -> pd.Series:
    """PR_daily = E_daily / (H_daily * capacity_kwp), per IEC 61724-1.

    Returns daily PR series aligned to common date index. NaN where
    insolation is too low or PR out of physical range (0..1.5).
    """
    aligned = pd.DataFrame({
        "energy": pr_energy_daily,
        "insolation": insolation_daily_kwh_per_m2,
    }).dropna()
    if aligned.empty or capacity_kwp <= 0:
        return pd.Series(dtype=float)
    pr = aligned["energy"] / (aligned["insolation"] * capacity_kwp)
    pr = pr[(pr >= 0.0) & (pr <= 1.5)]
    return pr


def _severity_from_economics(
    p_loss: float,
    payback_days: float,
    *,
    payback_threshold: float = DEFAULT_PAYBACK_THRESHOLD_DAYS,
) -> Severity:
    """Severity ladder from (p_loss, payback_days).

    - p_loss >= 0.10 AND payback < threshold/3 -> CRITICAL
    - p_loss >= 0.05 AND payback < threshold   -> HIGH
    - p_loss >= 0.02 AND payback < 2*threshold -> MEDIUM
    - else -> INFO
    """
    if not (np.isfinite(p_loss) and np.isfinite(payback_days)):
        return Severity.INFO
    if p_loss >= 0.10 and payback_days < payback_threshold / 3.0:
        return Severity.CRITICAL
    if p_loss >= 0.05 and payback_days < payback_threshold:
        return Severity.HIGH
    if p_loss >= 0.02 and payback_days < (2.0 * payback_threshold):
        return Severity.MEDIUM
    return Severity.INFO


def compute_cleaning_payback(
    p_loss: float,
    avg_daily_kwh: float,
    *,
    cleaning_cost_idr: float = DEFAULT_CLEANING_COST_IDR,
    electricity_tariff_idr: float = DEFAULT_ELECTRICITY_TARIFF_IDR,
) -> Tuple[float, float]:
    """Return (daily_loss_idr, payback_days).

    daily_loss_idr = avg_daily_kwh * tariff * p_loss
    payback_days   = cleaning_cost_idr / daily_loss_idr (inf if no loss or no cost)
    """
    daily_loss_idr = float(avg_daily_kwh * electricity_tariff_idr * p_loss)
    if daily_loss_idr <= 0 or cleaning_cost_idr <= 0:
        return daily_loss_idr, float("inf")
    return daily_loss_idr, float(cleaning_cost_idr / daily_loss_idr)


def _load_precipitation(path: str) -> Optional[pd.Series]:
    """Load daily precipitation from file (CSV/xlsx).

    Schema heuristic: first datetime-coercible column = date, first numeric
    column after = precipitation_mm. Returns None if path empty or invalid.
    """
    if not path or not os.path.exists(path):
        return None
    try:
        if path.endswith(".xlsx") or path.endswith(".xls"):
            df = pd.read_excel(path)
        else:
            df = pd.read_csv(path)
    except Exception as exc:
        warnings.warn(
            f"[M2aSoiling] precipitation load failed ({path}): {exc}",
            stacklevel=2,
        )
        return None
    date_col = None
    precip_col = None
    for col in df.columns:
        try:
            pd.to_datetime(df[col].iloc[:5], errors="raise")
            date_col = col
            break
        except Exception:
            continue
    if date_col is None or len(df.columns) < 2:
        warnings.warn(
            f"[M2aSoiling] precipitation file {path} lacks recognizable date column.",
            stacklevel=2,
        )
        return None
    for col in df.columns:
        if col == date_col:
            continue
        s_num = pd.to_numeric(df[col], errors="coerce")
        if s_num.notna().sum() > 0:
            precip_col = col
            break
    if precip_col is None:
        return None
    dates = pd.to_datetime(df[date_col], errors="coerce")
    precip = pd.to_numeric(df[precip_col], errors="coerce")
    out = pd.Series(precip.values, index=pd.DatetimeIndex(dates)).dropna()
    out.index = out.index.normalize()
    return out


class M2aSoiling(SubModule):
    """Soiling detector via rdtools SRR (Stochastic Rate and Recovery).

    Dependency injection (optional):
        prov = POAProvider.from_yaml(...)
        sm = M2aSoiling(poa=prov)
    """

    name: str = "M2a_soiling"

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

    def _build_daily_series(
        self,
        combined_df: pd.DataFrame,
        cfg: dict,
        empty_map: dict,
    ) -> Tuple[pd.Series, pd.Series]:
        """Aggregate all inverters into site-level daily energy + insolation.

        Returns (energy_daily_kwh, insolation_daily_kwh_per_m2).
        Both pd.Series indexed by normalized date.
        """
        pv_max = int(cfg.get("pv_max", DEFAULT_PV_MAX))
        freq_hours = float(cfg.get("sample_freq_hours", DEFAULT_SAMPLE_FREQ_HOURS))

        all_energy_per_ts: List[pd.Series] = []
        all_poa_per_ts: List[pd.Series] = []

        for inverter_id, group in combined_df.groupby("Inverter_ID"):
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

            p_inv = compute_inverter_power_per_timestamp(group_clean, pv_indices)
            all_energy_per_ts.append(pd.Series(p_inv, index=ts_clean))

            wb_parts = str(inverter_id).split("-")
            wb_id = wb_parts[0].upper() if wb_parts else str(inverter_id).upper()
            try:
                poa = self.poa.get_poa(ts_clean, wb_id, source="auto")
                all_poa_per_ts.append(pd.Series(
                    poa.reindex(ts_clean).fillna(0.0).values,
                    index=ts_clean,
                ))
            except Exception as exc:
                warnings.warn(
                    f"[M2aSoiling] POA query failed (wb={wb_id}): {exc}",
                    stacklevel=2,
                )
                continue

        if not all_energy_per_ts:
            return pd.Series(dtype=float), pd.Series(dtype=float)

        # Site-aggregate per timestamp: sum energy, mean POA (POA shared site).
        energy_concat = pd.concat(all_energy_per_ts).groupby(level=0).sum()
        poa_concat = pd.concat(all_poa_per_ts).groupby(level=0).mean()

        energy_daily = aggregate_daily(
            energy_concat.index, energy_concat.values, freq_hours=freq_hours,
        )
        poa_daily_kwh = aggregate_daily(
            poa_concat.index, poa_concat.values / 1000.0,
            freq_hours=freq_hours,
        )
        return energy_daily, poa_daily_kwh

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        cfg = config.get("m2a_soiling", {}) or {}
        enabled = bool(cfg.get("enabled", DEFAULT_ENABLED))
        if not enabled:
            return []

        min_days = int(cfg.get("min_days", DEFAULT_MIN_DAYS))
        recommended_days = int(cfg.get("recommended_days", DEFAULT_RECOMMENDED_DAYS))
        capacity_kwp = float(cfg.get("capacity_kwp", DEFAULT_CAPACITY_KWP))
        cleaning_cost_idr = float(cfg.get("cleaning_cost_idr", DEFAULT_CLEANING_COST_IDR))
        tariff_idr = float(cfg.get(
            "electricity_tariff_idr_per_kwh", DEFAULT_ELECTRICITY_TARIFF_IDR
        ))
        payback_thr = float(cfg.get(
            "payback_threshold_days", DEFAULT_PAYBACK_THRESHOLD_DAYS
        ))
        precip_path = str(cfg.get("precipitation_path", DEFAULT_PRECIPITATION_PATH))
        rdtools_reps = int(cfg.get("rdtools_reps", DEFAULT_RDTOOLS_REPS))
        rdtools_ci = float(cfg.get("rdtools_confidence_level", DEFAULT_RDTOOLS_CONFIDENCE))

        if "Inverter_ID" not in combined_df.columns or "Start Time" not in combined_df.columns:
            warnings.warn(
                "[M2aSoiling] missing 'Inverter_ID' or 'Start Time'; skipping.",
                stacklevel=2,
            )
            return []

        combined_df = _normalize_pv_columns(combined_df)
        empty_map = load_empty_pv_map(config)

        try:
            self._ensure_providers(config)
        except Exception as exc:
            warnings.warn(
                f"[M2aSoiling] POA provider init failed: "
                f"{exc.__class__.__name__}: {exc}. Skipping.",
                stacklevel=2,
            )
            return []

        # Build daily PR series.
        energy_daily, insolation_daily = self._build_daily_series(
            combined_df, cfg, empty_map,
        )
        pr_daily = compute_daily_pr_series(
            energy_daily, insolation_daily, capacity_kwp,
        )

        n_days = int(pr_daily.size)
        findings: List[M2Finding] = []

        # Data sufficiency gate -- skeleton's primary defensive behavior.
        if n_days < min_days:
            now_ts = (pr_daily.index.max() if n_days > 0 else datetime.utcnow())
            findings.append(M2Finding(
                timestamp=now_ts.to_pydatetime() if hasattr(now_ts, "to_pydatetime") else now_ts,
                inverter_id="SITE",
                pv_string=None,
                sub_module=self.name,
                severity=Severity.INFO,
                value=float(n_days),
                threshold=float(min_days),
                message=(
                    f"M2aSoiling skeleton: insufficient data window "
                    f"({n_days} days < min_days={min_days}). "
                    f"Recommend >={recommended_days} days untuk reliable SRR. "
                    f"Build baseline via pv_pipeline.baseline.BaselineAccumulator."
                ),
                fault_type="insufficient_data",
                confidence=100.0,
                evidence={
                    "n_days": n_days,
                    "min_days": min_days,
                    "recommended_days": recommended_days,
                    "baseline_action": "BaselineAccumulator.save() per day",
                },
            ))
            self.artifacts["EconomicAnalysis"] = pd.DataFrame([{
                "n_days_available": n_days,
                "min_days_required": min_days,
                "status": "insufficient_data",
                "soiling_ratio": float("nan"),
                "p_loss": float("nan"),
                "daily_loss_idr": float("nan"),
                "payback_days": float("nan"),
                "recommend_cleaning": False,
            }])
            return findings

        # Data sufficient -- attempt rdtools call.
        if not _ensure_rdtools():
            findings.append(M2Finding(
                timestamp=pr_daily.index.max().to_pydatetime(),
                inverter_id="SITE",
                pv_string=None,
                sub_module=self.name,
                severity=Severity.INFO,
                value=0.0,
                threshold=0.0,
                message=(
                    "M2aSoiling: rdtools unavailable (install failed). "
                    "Run `pip install rdtools` manually."
                ),
                fault_type="insufficient_dependency",
                confidence=100.0,
                evidence={"dependency": "rdtools"},
            ))
            return findings

        import rdtools  # noqa: WPS433
        from rdtools import soiling  # noqa: WPS433

        # Optional precipitation.
        precip_daily = _load_precipitation(precip_path)
        precip_aligned = (
            precip_daily.reindex(pr_daily.index).fillna(0.0)
            if precip_daily is not None else None
        )

        try:
            sr_result = soiling.soiling_srr(
                energy_normalized_daily=pr_daily,
                insolation_daily=insolation_daily.reindex(pr_daily.index),
                precipitation_daily=precip_aligned,
                reps=rdtools_reps,
                confidence_level=rdtools_ci,
            )
            if isinstance(sr_result, tuple) and len(sr_result) >= 3:
                sr, sr_ci, calc_info = sr_result[0], sr_result[1], sr_result[2]
            else:
                sr = getattr(sr_result, "soiling_ratio", float("nan"))
                sr_ci = (float("nan"), float("nan"))
                calc_info = {}
        except Exception as exc:
            warnings.warn(
                f"[M2aSoiling] rdtools.soiling_srr failed: "
                f"{exc.__class__.__name__}: {exc}",
                stacklevel=2,
            )
            findings.append(M2Finding(
                timestamp=pr_daily.index.max().to_pydatetime(),
                inverter_id="SITE",
                pv_string=None,
                sub_module=self.name,
                severity=Severity.INFO,
                value=0.0,
                threshold=0.0,
                message=f"M2aSoiling: rdtools call failed -- {exc}",
                fault_type="rdtools_error",
                confidence=100.0,
                evidence={"exception": str(exc)},
            ))
            return findings

        sr_val = float(sr) if np.isfinite(sr) else float("nan")
        p_loss = 1.0 - sr_val if np.isfinite(sr_val) else float("nan")
        ci_lower = (
            float(sr_ci[0])
            if isinstance(sr_ci, (tuple, list)) and len(sr_ci) >= 2
            else float("nan")
        )
        ci_upper = (
            float(sr_ci[1])
            if isinstance(sr_ci, (tuple, list)) and len(sr_ci) >= 2
            else float("nan")
        )

        avg_daily_kwh = float(energy_daily.tail(30).mean()) if not energy_daily.empty else 0.0
        daily_loss_idr, payback_days = compute_cleaning_payback(
            p_loss if np.isfinite(p_loss) else 0.0,
            avg_daily_kwh,
            cleaning_cost_idr=cleaning_cost_idr,
            electricity_tariff_idr=tariff_idr,
        )
        recommend = payback_days < payback_thr
        severity = _severity_from_economics(
            p_loss if np.isfinite(p_loss) else 0.0,
            payback_days,
            payback_threshold=payback_thr,
        )
        fault_type = "cleaning_recommended" if recommend else "soiling_detected"

        ts_finding = pr_daily.index.max().to_pydatetime()
        findings.append(M2Finding(
            timestamp=ts_finding,
            inverter_id="SITE",
            pv_string=None,
            sub_module=self.name,
            severity=severity,
            value=p_loss if np.isfinite(p_loss) else 0.0,
            threshold=0.0,
            message=(
                f"M2aSoiling SRR: sr={sr_val:.4f} (CI {ci_lower:.4f}..{ci_upper:.4f}); "
                f"p_loss={p_loss:.4f}; daily_loss={daily_loss_idr:.0f} IDR; "
                f"payback={payback_days:.1f}d "
                f"({'CLEAN NOW' if recommend else 'wait'})"
            ),
            fault_type=fault_type,
            confidence=float(50.0 + (sr_val * 50.0)) if np.isfinite(sr_val) else 50.0,
            evidence={
                "soiling_ratio": sr_val,
                "sr_ci_lower": ci_lower,
                "sr_ci_upper": ci_upper,
                "p_loss": p_loss,
                "n_days": n_days,
                "n_cleaning_events": (
                    len(calc_info.get("soiling_interval_summary", []))
                    if isinstance(calc_info, dict) else 0
                ),
                "precip_available": precip_aligned is not None,
                "avg_daily_kwh": avg_daily_kwh,
                "daily_loss_idr": daily_loss_idr,
                "payback_days": payback_days if np.isfinite(payback_days) else None,
                "cleaning_cost_idr": cleaning_cost_idr,
                "electricity_tariff_idr_per_kwh": tariff_idr,
            },
        ))

        # Artifact: SoilingRatio daily series (rdtools output).
        if isinstance(calc_info, dict) and "stochastic_soiling_profiles" in calc_info:
            try:
                profiles = calc_info["stochastic_soiling_profiles"]
                if isinstance(profiles, pd.DataFrame) and not profiles.empty:
                    self.artifacts["SoilingRatio"] = profiles.copy()
            except Exception:
                pass

        # Artifact: CleaningEvents from soiling_interval_summary.
        if isinstance(calc_info, dict) and "soiling_interval_summary" in calc_info:
            try:
                summary = calc_info["soiling_interval_summary"]
                if isinstance(summary, pd.DataFrame) and not summary.empty:
                    self.artifacts["CleaningEvents"] = summary.copy()
            except Exception:
                pass

        # Artifact: EconomicAnalysis (always emitted if SRR succeeded).
        self.artifacts["EconomicAnalysis"] = pd.DataFrame([{
            "n_days_available": n_days,
            "min_days_required": min_days,
            "status": "ok",
            "soiling_ratio": sr_val,
            "sr_ci_lower": ci_lower,
            "sr_ci_upper": ci_upper,
            "p_loss": p_loss,
            "avg_daily_kwh": avg_daily_kwh,
            "daily_loss_idr": daily_loss_idr,
            "payback_days": payback_days if np.isfinite(payback_days) else float("nan"),
            "cleaning_cost_idr": cleaning_cost_idr,
            "tariff_idr_per_kwh": tariff_idr,
            "recommend_cleaning": recommend,
            "severity": severity.value,
        }])

        return findings


if __name__ == "__main__":  # pragma: no cover
    # Smoke tests.
    ts = pd.date_range("2026-05-14 06:00", periods=12, freq="5min")
    s = aggregate_daily(ts, np.full(12, 100.0), freq_hours=5.0/60.0)
    print(f"[m2a.soiling] aggregate_daily smoke: {s.values}")

    loss, payback = compute_cleaning_payback(
        0.05, 50000.0,
        cleaning_cost_idr=10_000_000.0, electricity_tariff_idr=1500.0,
    )
    print(f"[m2a.soiling] payback smoke: loss={loss:.0f}, payback={payback:.2f} days")
    assert abs(payback - (10_000_000.0 / 3_750_000.0)) < 0.01

    assert _severity_from_economics(0.15, 5.0) == Severity.CRITICAL
    assert _severity_from_economics(0.06, 20.0) == Severity.HIGH
    assert _severity_from_economics(0.03, 50.0) == Severity.MEDIUM
    assert _severity_from_economics(0.01, 100.0) == Severity.INFO
    print("[m2a.soiling] severity smoke OK")
