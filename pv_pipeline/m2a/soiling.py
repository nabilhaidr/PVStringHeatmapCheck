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

import json
import os
import re
import time
import warnings
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from pv_pipeline.core import M2Finding, Severity, SubModule, load_empty_pv_map
from pv_pipeline.m2a.cleaning_report import (
    build_st_to_pv,
    classify_cleaning_intervals,
    daily_cleaning_counts,
    load_cleaning_report,
    load_dc_cable_map,
)


# --- Defaults (mirror DEFAULT_M2_CONFIG["m2a_soiling"]) --------------------
DEFAULT_ENABLED: bool = False
DEFAULT_MIN_DAYS: int = 90                   # minimum data window
DEFAULT_RECOMMENDED_DAYS: int = 180          # recommended window (6 months)
DEFAULT_CAPACITY_KWP: float = 71500.0        # PLTS-IKN total DC capacity
DEFAULT_CLEANING_COST_IDR: float = 0.0       # placeholder; user provides
DEFAULT_ELECTRICITY_TARIFF_IDR: float = 1500.0  # IDR/kWh estimate (IKN PLTS PPA)
DEFAULT_PAYBACK_THRESHOLD_DAYS: float = 30.0
DEFAULT_PRECIPITATION_PATH: str = ""         # empty -> skip precipitation
DEFAULT_CLEANING_REPORT_PATH: str = ""       # empty -> skip manual cleaning join
DEFAULT_DC_CABLE_LIST_PATH: str = ""         # empty -> ST==PV identity semua WB
DEFAULT_CLEANING_MATCH_WINDOW_DAYS: int = 3
DEFAULT_CLEANING_PRECIP_THRESHOLD_MM: float = 1.0
# Mask availability M2e: inverter-day dengan uptime < ambang di-drop dari
# energi harian DAN kapasitasnya dikeluarkan dari penyebut PR hari itu,
# supaya SRR tidak membaca outage parsial sebagai soiling.
DEFAULT_AVAILABILITY_DIR: str = ""           # empty -> skip mask
DEFAULT_AVAILABILITY_MIN_UPTIME_PCT: float = 95.0
# Koreksi suhu PR (default OFF di kode; config produksi m2_config.yaml set
# true). gamma Pmax Jinko JKM625N = -0.29 %/C, referensi STC 25 C.
DEFAULT_TEMPERATURE_CORRECTION: bool = False
DEFAULT_TEMP_COEF_PMAX_PCT_PER_C: float = -0.29
DEFAULT_TEMP_REF_C: float = 25.0
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


def temp_correction_factor(
    tcell_c,
    gamma_pmax_pct_per_c: float = DEFAULT_TEMP_COEF_PMAX_PCT_PER_C,
    ref_c: float = DEFAULT_TEMP_REF_C,
):
    """Faktor koreksi suhu Pmax: CF = 1 + (gamma/100) * (Tcell - ref).

    gamma negatif (Pmax turun saat panas): CF < 1 saat Tcell > ref. Dipakai
    di penyebut PR agar depresi suhu dihilangkan -> PR flat lintas musim,
    soiling terisolasi. Menerima skalar/Series/array.
    """
    return 1.0 + (gamma_pmax_pct_per_c / 100.0) * (tcell_c - ref_c)


def compute_daily_pr_series(
    pr_energy_daily: pd.Series,
    insolation_daily_kwh_per_m2: pd.Series,
    capacity_kwp: float,
    temp_factor_daily: Optional[pd.Series] = None,
    capacity_factor_daily: Optional[pd.Series] = None,
) -> pd.Series:
    """PR_daily = E_daily / (H_daily * capacity_kwp * TempFactor * CapFactor),
    IEC 61724-1.

    ``temp_factor_daily`` (opsional) = faktor koreksi suhu ternormalisasi
    insolasi per hari; bila diberikan, PR jadi temperature-corrected.
    ``capacity_factor_daily`` (opsional) = fraksi kapasitas tersedia per hari
    (mask availability M2e: energi inverter down di-drop, kapasitasnya ikut
    keluar dari penyebut supaya PR tak bias). NaN di mana insolasi/faktor
    invalid atau PR di luar rentang fisik (0..1.5).
    """
    cols = {
        "energy": pr_energy_daily,
        "insolation": insolation_daily_kwh_per_m2,
    }
    if temp_factor_daily is not None:
        cols["temp_factor"] = temp_factor_daily
    if capacity_factor_daily is not None:
        cols["capacity_factor"] = capacity_factor_daily
    aligned = pd.DataFrame(cols).dropna()
    if aligned.empty or capacity_kwp <= 0:
        return pd.Series(dtype=float)
    denom = aligned["insolation"] * capacity_kwp
    if temp_factor_daily is not None:
        denom = denom * aligned["temp_factor"].replace(0.0, np.nan)
    if capacity_factor_daily is not None:
        denom = denom * aligned["capacity_factor"].replace(0.0, np.nan)
    pr = aligned["energy"] / denom
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


def reindex_daily_frequency(
    pr_daily: pd.Series,
    insolation_daily: pd.Series,
    precip_daily: Optional[pd.Series] = None,
) -> Tuple[pd.Series, pd.Series, Optional[pd.Series]]:
    """Reindex seri harian ke grid kontinu freq='D' untuk rdtools.

    ``rdtools.soiling_srr`` menolak index tanpa frekuensi harian eksplisit
    ("Daily performance metric series must have daily frequency"), sedangkan
    baseline punya hari bolong (outage/curtailment/day-filter) sehingga
    freq tidak bisa di-infer. Hari hilang menjadi NaN (ditoleransi SRR saat
    segmentasi interval); presipitasi hari hilang diisi 0.
    """
    full_idx = pd.date_range(
        pr_daily.index.min(), pr_daily.index.max(), freq="D",
    )
    pr_full = pr_daily.reindex(full_idx)
    insolation_full = insolation_daily.reindex(full_idx)
    precip_full = (
        precip_daily.reindex(full_idx).fillna(0.0)
        if precip_daily is not None else None
    )
    return pr_full, insolation_full, precip_full


def _ci_bounds(sr_ci) -> Tuple[float, float]:
    """Ekstrak (lower, upper) dari confidence interval rdtools soiling_srr.

    rdtools mengembalikan ``np.ndarray`` ``[lower, upper]``; membatasi ke
    tuple/list saja (bug lama) membuat CI selalu NaN. Terima sequence/array
    apa pun berpanjang >=2.
    """
    try:
        if sr_ci is None or len(sr_ci) < 2:
            return float("nan"), float("nan")
        return float(sr_ci[0]), float(sr_ci[1])
    except (TypeError, ValueError):
        return float("nan"), float("nan")


def summarize_soiling_profiles(
    profiles,
    confidence_level: float = DEFAULT_RDTOOLS_CONFIDENCE,
) -> Optional[pd.DataFrame]:
    """Ringkas stochastic_soiling_profiles rdtools -> df [date, sr_p50,
    sr_ci_lower, sr_ci_upper].

    rdtools mengembalikan LIST of pd.Series (satu per realisasi Monte
    Carlo) -- cek isinstance DataFrame lama membuat sheet SoilingRatio
    tidak pernah ter-emit. Gabung via pd.concat(axis=1) lalu ambil median
    + kuantil CI per hari (1000 kolom realisasi tidak praktis di Excel).
    Returns None bila input kosong/tak dikenal.
    """
    if isinstance(profiles, pd.DataFrame):
        prof_df = profiles
    elif isinstance(profiles, (list, tuple)) and len(profiles) > 0:
        try:
            prof_df = pd.concat(list(profiles), axis=1)
        except (TypeError, ValueError):
            return None
    else:
        return None
    if prof_df.empty:
        return None
    alpha = (100.0 - float(confidence_level)) / 2.0 / 100.0
    summary = pd.DataFrame({
        "sr_p50": prof_df.median(axis=1),
        "sr_ci_lower": prof_df.quantile(alpha, axis=1),
        "sr_ci_upper": prof_df.quantile(1.0 - alpha, axis=1),
    })
    summary.index.name = "date"
    return summary.reset_index()


MONTHLY_SOILING_COLUMNS: List[str] = [
    "month", "n_days", "sr_p50", "sr_ci_lower", "sr_ci_upper",
    "p_loss_pct", "energy_lost_kwh_est", "loss_idr_est",
]


def build_monthly_soiling_loss(
    profiles_df: Optional[pd.DataFrame],
    insolation_daily: pd.Series,
    energy_daily: Optional[pd.Series],
    tariff_idr_per_kwh: float,
    confidence_level: float = DEFAULT_RDTOOLS_CONFIDENCE,
) -> pd.DataFrame:
    """Breakdown soiling loss BULANAN dari profil SR harian Monte Carlo SRR.

    ``profiles_df`` = hasil pd.concat(stochastic_soiling_profiles, axis=1)
    (index tanggal harian, kolom = realisasi). SR bulanan = median harian
    antar realisasi, dirata-rata berbobot insolasi POA bulan itu (konsisten
    definisi insolation-weighted SR rdtools); CI dari kuantil antar
    realisasi dengan bobot yang sama. energy_lost_kwh_est =
    sum(E_d * (1/sr_d - 1)) -- estimasi energi hilang dihitung dari energi
    aktual (soiled) bulan itu.
    """
    empty = pd.DataFrame(columns=MONTHLY_SOILING_COLUMNS)
    if profiles_df is None or profiles_df.empty:
        return empty
    if insolation_daily is None or insolation_daily.empty:
        return empty

    alpha = (100.0 - float(confidence_level)) / 2.0 / 100.0
    idx = pd.DatetimeIndex(profiles_df.index).normalize()

    insol = insolation_daily.copy()
    insol.index = pd.DatetimeIndex(insol.index).normalize()
    insol = insol[~insol.index.duplicated()]
    if energy_daily is not None and not energy_daily.empty:
        energy = energy_daily.copy()
        energy.index = pd.DatetimeIndex(energy.index).normalize()
        energy = energy[~energy.index.duplicated()]
    else:
        energy = pd.Series(dtype=float)

    df = pd.DataFrame({
        "sr_p50": profiles_df.median(axis=1).values,
        "sr_lo": profiles_df.quantile(alpha, axis=1).values,
        "sr_hi": profiles_df.quantile(1.0 - alpha, axis=1).values,
        "insol": insol.reindex(idx).values,
        "energy": energy.reindex(idx).values,
    }, index=idx).dropna(subset=["sr_p50", "insol"])
    if df.empty:
        return empty

    rows = []
    for month, g in df.groupby(df.index.to_period("M")):
        w = float(g["insol"].sum())
        if w <= 0:
            continue
        sr_m = float((g["sr_p50"] * g["insol"]).sum() / w)
        lost = g["energy"] * (1.0 / g["sr_p50"].clip(lower=1e-6) - 1.0)
        lost_sum = float(lost.dropna().sum())
        rows.append({
            "month": str(month),
            "n_days": int(len(g)),
            "sr_p50": sr_m,
            "sr_ci_lower": float((g["sr_lo"] * g["insol"]).sum() / w),
            "sr_ci_upper": float((g["sr_hi"] * g["insol"]).sum() / w),
            "p_loss_pct": (1.0 - sr_m) * 100.0,
            "energy_lost_kwh_est": lost_sum,
            "loss_idr_est": lost_sum * float(tariff_idr_per_kwh),
        })
    return pd.DataFrame(rows, columns=MONTHLY_SOILING_COLUMNS)


CLEANING_IMPACT_COLUMNS: List[str] = [
    "date", "sr_before", "sr_after", "sr_gain_pp",
    "energy_recovered_kwh_per_day", "rupiah_per_day", "likely_cause",
]


def build_cleaning_impact(
    cleaning_events: pd.DataFrame,
    avg_daily_kwh: float,
    tariff_idr_per_kwh: float,
) -> pd.DataFrame:
    """Dampak tiap event cleaning (batas antar interval SRR): PR sebelum vs
    sesudah, kenaikan SR, energi & rupiah yang dipulihkan per hari.

    Satu event = transisi interval i-1 -> i (rdtools memotong deret di
    tiap cleaning). sr_before = ``inferred_end_loss`` interval sebelumnya
    (kotor, tepat sebelum dibersihkan); sr_after = ``inferred_start_loss``
    interval berikutnya (bersih, tepat sesudah). Keduanya soiling ratio
    (fraksi performa dipertahankan). Energi dipulihkan/hari =
    sr_gain * avg_daily_kwh (aproksimasi; avg_daily_kwh = actual soiled
    energy 30 hari terakhir).

    ``sr_gain_pp`` = sr_after - sr_before dalam POIN PERSEN -- selisih dua
    rasio, BUKAN perubahan relatif. Beda satuan dengan ``soiling_loss_pct``
    di sheet DirectCleaningImpact*; jangan dibandingkan langsung.
    """
    empty = pd.DataFrame(columns=CLEANING_IMPACT_COLUMNS)
    if cleaning_events is None or cleaning_events.empty:
        return empty
    if not {"start", "inferred_start_loss", "inferred_end_loss"} <= set(cleaning_events.columns):
        return empty

    df = cleaning_events.sort_values("start").reset_index(drop=True)
    rows = []
    for i in range(1, len(df)):
        prev, cur = df.iloc[i - 1], df.iloc[i]
        sr_before = float(prev["inferred_end_loss"])
        sr_after = float(cur["inferred_start_loss"])
        if not (np.isfinite(sr_before) and np.isfinite(sr_after)):
            continue
        sr_gain = sr_after - sr_before
        energy = sr_gain * float(avg_daily_kwh)
        rows.append({
            "date": pd.Timestamp(cur["start"]).normalize(),
            "sr_before": sr_before,
            "sr_after": sr_after,
            "sr_gain_pp": sr_gain * 100.0,
            "energy_recovered_kwh_per_day": energy,
            "rupiah_per_day": energy * float(tariff_idr_per_kwh),
            "likely_cause": cur.get("likely_cause", "unknown"),
        })
    return pd.DataFrame(rows, columns=CLEANING_IMPACT_COLUMNS)


DEFAULT_DIRECT_WINDOW_DAYS: int = 7      # jendela PR pre/post di sekitar campaign
DEFAULT_DIRECT_GAP_DAYS: int = 7         # gap antar tanggal -> campaign berbeda
DEFAULT_DIRECT_MIN_WINDOW_DAYS: int = 2  # min hari PR valid per sisi
DIRECT_CLEANING_IMPACT_COLUMNS: List[str] = [
    "cleaning_start", "cleaning_end", "n_strings_cleaned",
    "n_days_before", "n_days_after", "pr_before", "pr_after",
    "soiling_loss_pct", "energy_recovered_kwh_per_day", "rupiah_per_day",
]


def build_direct_cleaning_impact(
    pr_daily: pd.Series,
    manual_events: pd.DataFrame,
    avg_daily_kwh: float,
    tariff_idr_per_kwh: float,
    *,
    window_days: int = DEFAULT_DIRECT_WINDOW_DAYS,
    gap_days: int = DEFAULT_DIRECT_GAP_DAYS,
    min_window_days: int = DEFAULT_DIRECT_MIN_WINDOW_DAYS,
) -> pd.DataFrame:
    """Pre/post PR langsung di sekitar campaign cleaning manual -- TIDAK
    bergantung SRR (tetap terisi walau soiling_srr NoValidIntervalError).

    Campaign = klaster tanggal cleaning (rekap manual) dengan jarak antar
    tanggal <= gap_days. Untuk tiap campaign: pr_before = rata-rata PR harian
    window_days sebelum campaign mulai (kotor), pr_after = rata-rata
    window_days sesudah campaign selesai (bersih). PR sudah ternormalisasi
    irradiance sehingga variasi cuaca sebagian besar hilang; koreksi
    temperatur belum diterapkan (lihat catatan modul).

    soiling_loss_pct = (pr_after - pr_before)/pr_after * 100 -- fraksi output
    bersih yang hilang karena soiling tepat sebelum cleaning.
    energy_recovered_kwh_per_day = avg_daily_kwh * soiling_loss_fraction.
    """
    empty = pd.DataFrame(columns=DIRECT_CLEANING_IMPACT_COLUMNS)
    if pr_daily is None or pr_daily.empty:
        return empty
    if manual_events is None or manual_events.empty or "date" not in manual_events.columns:
        return empty

    pr = pr_daily.copy()
    pr.index = pd.DatetimeIndex(pr.index).normalize()
    pr = pr[~pr.index.duplicated()].sort_index()

    dates = pd.to_datetime(manual_events["date"], errors="coerce").dropna().dt.normalize()
    counts = dates.value_counts().sort_index()   # jumlah string per tanggal
    uniq = list(counts.index)
    if not uniq:
        return empty

    campaigns: List[List[pd.Timestamp]] = []
    cur = [uniq[0]]
    for d in uniq[1:]:
        if (d - cur[-1]).days <= gap_days:
            cur.append(d)
        else:
            campaigns.append(cur)
            cur = [d]
    campaigns.append(cur)

    win = pd.Timedelta(days=window_days)
    rows = []
    for camp in campaigns:
        start, end = camp[0], camp[-1]
        before = pr[(pr.index >= start - win) & (pr.index < start)]
        after = pr[(pr.index > end) & (pr.index <= end + win)]
        if len(before) < min_window_days or len(after) < min_window_days:
            continue
        pr_before = float(before.mean())
        pr_after = float(after.mean())
        if not (np.isfinite(pr_before) and np.isfinite(pr_after)) or pr_after <= 0:
            continue
        loss_frac = (pr_after - pr_before) / pr_after
        energy = float(avg_daily_kwh) * loss_frac
        rows.append({
            "cleaning_start": start,
            "cleaning_end": end,
            "n_strings_cleaned": int(counts.loc[camp].sum()),
            "n_days_before": int(len(before)),
            "n_days_after": int(len(after)),
            "pr_before": pr_before,
            "pr_after": pr_after,
            "soiling_loss_pct": loss_frac * 100.0,
            "energy_recovered_kwh_per_day": energy,
            "rupiah_per_day": energy * float(tariff_idr_per_kwh),
        })
    return pd.DataFrame(rows, columns=DIRECT_CLEANING_IMPACT_COLUMNS)


DEFAULT_MODULE_WP: float = 625.0
MODULES_PER_STRING_BY_WB = {1: 24, 2: 24}   # selain WB01/02 -> 26 modul/string


def _wb_number(inverter_id: str) -> Optional[int]:
    m = re.match(r"WB(\d+)", str(inverter_id).upper())
    return int(m.group(1)) if m else None


def _modules_per_string(wb: Optional[int]) -> int:
    return MODULES_PER_STRING_BY_WB.get(wb, 26)


def _string_capacity_kwp(inverter_id: str) -> float:
    """Kapasitas DC satu string (kWp): modul/string x 625 Wp."""
    return _modules_per_string(_wb_number(inverter_id)) * DEFAULT_MODULE_WP / 1000.0


def _inverter_capacity_kwp(
    inverter_id: str,
    empty_map: dict,
    pv_max: int = DEFAULT_PV_MAX,
) -> float:
    """Kapasitas DC satu inverter (kWp) = string aktif x kapasitas string.

    String aktif = pv_max - slot kosong by design (empty_pv_map strings.yaml).
    Sanity: WB01 18 string x 24 x 625 Wp = 270 kWp x 50 inverter = 13500 ~
    nameplate WB01.
    """
    empties = empty_map.get(str(inverter_id).upper(), []) or []
    n_strings = max(pv_max - len(empties), 0)
    return n_strings * _string_capacity_kwp(inverter_id)


DIRECT_PER_STRING_COLUMNS: List[str] = [
    "inverter_id", "pv", "st", "cleaning_start", "cleaning_end",
    "n_days_before", "n_days_after", "pr_before", "pr_after",
    "soiling_loss_pct", "rank_soiling_loss",
]


def build_direct_cleaning_impact_per_string(
    string_energy_daily: pd.DataFrame,
    insolation_daily: pd.Series,
    manual_events: pd.DataFrame,
    *,
    window_days: int = DEFAULT_DIRECT_WINDOW_DAYS,
    gap_days: int = DEFAULT_DIRECT_GAP_DAYS,
    min_window_days: int = DEFAULT_DIRECT_MIN_WINDOW_DAYS,
) -> pd.DataFrame:
    """Pre/post PR PER STRING di sekitar tanggal cleaning string itu sendiri
    (independen SRR). Menangkap soiling NON-UNIFORM yang tak terlihat di
    agregat: string ber-loss tinggi = paling kotor; loss ~0 = cleaning
    tak berdampak (kandidat masalah non-soiling).

    ``string_energy_daily``: long df kolom [date, Inverter_ID, pv,
    energy_kwh] (hasil loader per-string). PR string = (E_str / H_POA) /
    kapasitas_string; soiling_loss_pct = (pr_after - pr_before) / pr_after
    -- definisi yang SAMA dengan ``soiling_loss_pct`` di
    ``build_direct_cleaning_impact`` (level plant) dan di
    ``pv_pipeline.yf_ratio_report``. Koreksi suhu tidak diterapkan di level
    string (jendela pre/post berdekatan, drift suhu musiman ~batal).
    ``rank_soiling_loss`` = 1 untuk loss terbesar.
    """
    empty = pd.DataFrame(columns=DIRECT_PER_STRING_COLUMNS)
    if string_energy_daily is None or string_energy_daily.empty:
        return empty
    if manual_events is None or manual_events.empty:
        return empty
    if insolation_daily is None or insolation_daily.empty:
        return empty

    insol = insolation_daily.copy()
    insol.index = pd.DatetimeIndex(insol.index).normalize()
    insol = insol[~insol.index.duplicated()].replace(0.0, np.nan)

    se = string_energy_daily.copy()
    se["date"] = pd.to_datetime(se["date"], errors="coerce").dt.normalize()
    se = se.dropna(subset=["date"])
    se["y"] = se["energy_kwh"].to_numpy() / insol.reindex(se["date"]).to_numpy()
    se = se.dropna(subset=["y"])

    ev = manual_events.dropna(subset=["pv"]).copy()
    if ev.empty:
        return empty
    ev["pv"] = ev["pv"].astype(int)
    ev["date"] = pd.to_datetime(ev["date"], errors="coerce").dt.normalize()
    ev = ev.dropna(subset=["date"])

    win = pd.Timedelta(days=window_days)
    rows = []
    y_groups = {k: g.set_index("date")["y"].sort_index()
                for k, g in se.groupby(["Inverter_ID", "pv"])}
    for (inv_id, pv), g in ev.groupby(["inverter_id", "pv"]):
        y = y_groups.get((inv_id, int(pv)))
        if y is None or y.empty:
            continue
        cap = _string_capacity_kwp(inv_id)
        st = g["st"].iloc[0] if "st" in g.columns else None
        dates = sorted(g["date"].unique())
        campaigns: List[List[pd.Timestamp]] = []
        cur = [dates[0]]
        for d in dates[1:]:
            if (d - cur[-1]).days <= gap_days:
                cur.append(d)
            else:
                campaigns.append(cur)
                cur = [d]
        campaigns.append(cur)
        for camp in campaigns:
            start, end = camp[0], camp[-1]
            before = y[(y.index >= start - win) & (y.index < start)]
            after = y[(y.index > end) & (y.index <= end + win)]
            if len(before) < min_window_days or len(after) < min_window_days:
                continue
            y_b, y_a = float(before.mean()), float(after.mean())
            if not (np.isfinite(y_b) and np.isfinite(y_a)) or y_a <= 0:
                continue
            rows.append({
                "inverter_id": inv_id,
                "pv": int(pv),
                "st": st,
                "cleaning_start": start,
                "cleaning_end": end,
                "n_days_before": int(len(before)),
                "n_days_after": int(len(after)),
                "pr_before": y_b / cap,
                "pr_after": y_a / cap,
                "soiling_loss_pct": (y_a - y_b) / y_a * 100.0,
            })
    if not rows:
        return empty
    out = pd.DataFrame(rows)
    out["rank_soiling_loss"] = out["soiling_loss_pct"].rank(
        ascending=False, method="min",
    ).astype(int)
    return out.sort_values(
        "soiling_loss_pct", ascending=False, ignore_index=True,
    )[DIRECT_PER_STRING_COLUMNS]


DEFAULT_RECOMMENDATION_DEAD_RATIO: float = 0.10
RECOMMENDATION_COLUMNS: List[str] = [
    "inverter_id", "pv", "n_days", "pr_recent", "pr_inverter_median",
    "deficit_vs_siblings_pct", "inverter_p_loss_pct", "hist_soiling_loss_pct",
    "cable_vdrop_pct", "score", "status", "rank",
]


def build_cleaning_recommendation(
    string_energy_daily: pd.DataFrame,
    insolation_daily: pd.Series,
    per_inverter_srr: Optional[pd.DataFrame] = None,
    direct_per_string: Optional[pd.DataFrame] = None,
    *,
    cable_metrics: Optional[pd.DataFrame] = None,
    window_days: int = 30,
    min_days: int = 10,
    dead_ratio: float = DEFAULT_RECOMMENDATION_DEAD_RATIO,
) -> pd.DataFrame:
    """Rekomendasi area cleaning: satukan yield string terkini (basis
    heatmap) dengan ranking soiling inverter + uplift historis.

    Per string, jendela ``window_days`` terakhir data:
      pr_recent = (sum E_str / sum H_POA pada hari-hari string itu) /
                  kapasitas_string;
      deficit_vs_siblings_pct = (median PR inverter - pr_recent)/median*100
        -- menangkap soiling PARSIAL (string kotor tertinggal dari sibling
        se-inverter);
      inverter_p_loss_pct (join PerInverterSRR) -- soiling MERATA level
        inverter yang tak terlihat dari deficit sibling;
      hist_soiling_loss_pct (join DirectCleaningImpactPerString, mean per
        string) -- bukti historis string ini memang pulih saat dibersihkan.

    score = deficit_vs_siblings_pct + inverter_p_loss_pct (0 bila tak ada)
    ~ estimasi % energi string yang hilang; rank 1 = prioritas tertinggi.

    String dengan pr_recent < ``dead_ratio`` x median inverter ditandai
    ``DEAD_OR_OFFLINE`` dan DIKELUARKAN dari ranking (rank NA): defisitnya
    ~100% sehingga selalu menang skor, padahal itu kasus availability (M2e),
    bukan soiling -- membersihkannya tidak memulihkan apa pun. Ambang dan
    penamaan status mengikuti ``pv_pipeline.yf_ratio_report``.
    """
    empty = pd.DataFrame(columns=RECOMMENDATION_COLUMNS)
    if string_energy_daily is None or string_energy_daily.empty:
        return empty
    if insolation_daily is None or insolation_daily.empty:
        return empty

    se = string_energy_daily.copy()
    se["date"] = pd.to_datetime(se["date"], errors="coerce").dt.normalize()
    se = se.dropna(subset=["date"])
    if se.empty:
        return empty
    end = se["date"].max()
    se = se[se["date"] >= end - pd.Timedelta(days=window_days - 1)]

    insol = insolation_daily.copy()
    insol.index = pd.DatetimeIndex(insol.index).normalize()
    insol = insol[~insol.index.duplicated()].replace(0.0, np.nan)
    se["insol"] = insol.reindex(se["date"]).to_numpy()
    se = se.dropna(subset=["insol"])

    rows = []
    for (inv_id, pv), g in se.groupby(["Inverter_ID", "pv"]):
        n = int(g["date"].nunique())
        if n < min_days:
            continue
        h = float(g["insol"].sum())
        if h <= 0:
            continue
        pr = float(g["energy_kwh"].sum()) / h / _string_capacity_kwp(inv_id)
        rows.append({
            "inverter_id": str(inv_id), "pv": int(pv),
            "n_days": n, "pr_recent": pr,
        })
    if not rows:
        return empty

    out = pd.DataFrame(rows)
    med = out.groupby("inverter_id")["pr_recent"].transform("median")
    out["pr_inverter_median"] = med
    out["deficit_vs_siblings_pct"] = np.where(
        med > 0, (med - out["pr_recent"]) / med * 100.0, np.nan,
    )

    out["inverter_p_loss_pct"] = np.nan
    if (per_inverter_srr is not None and not per_inverter_srr.empty
            and {"inverter_id", "p_loss_pct"} <= set(per_inverter_srr.columns)):
        pl = per_inverter_srr.drop_duplicates("inverter_id").set_index(
            per_inverter_srr.drop_duplicates("inverter_id")["inverter_id"].astype(str)
        )["p_loss_pct"]
        out["inverter_p_loss_pct"] = out["inverter_id"].map(pl)

    out["hist_soiling_loss_pct"] = np.nan
    if (direct_per_string is not None and not direct_per_string.empty
            and {"inverter_id", "pv", "soiling_loss_pct"}
            <= set(direct_per_string.columns)):
        hist = (
            direct_per_string.groupby(["inverter_id", "pv"])["soiling_loss_pct"]
            .mean().rename("hist_soiling_loss_pct").reset_index()
        )
        hist["inverter_id"] = hist["inverter_id"].astype(str)
        hist["pv"] = hist["pv"].astype(int)
        out = out.drop(columns=["hist_soiling_loss_pct"]).merge(
            hist, on=["inverter_id", "pv"], how="left",
        )

    # Kolom BUKTI, bukan koreksi skor: defisit terhadap sibling bisa berasal
    # dari panel kotor ATAU dari kabel DC yang jauh lebih panjang (as-built
    # 11-202 m, 0,15-2,79%). Voltage drop tidak diterjemahkan lurus ke arus
    # terukur di terminal inverter karena MPPT bekerja di level array, jadi
    # angkanya ditampilkan apa adanya untuk dinilai analis. NA = string itu
    # tidak punya baris di cable list, bukan "kabelnya pendek".
    out["cable_vdrop_pct"] = np.nan
    if (cable_metrics is not None and not cable_metrics.empty
            and {"inverter_id", "pv", "vdrop_pct"} <= set(cable_metrics.columns)):
        cm = cable_metrics.drop_duplicates(["inverter_id", "pv"])[
            ["inverter_id", "pv", "vdrop_pct"]
        ].rename(columns={"vdrop_pct": "cable_vdrop_pct"})
        cm["inverter_id"] = cm["inverter_id"].astype(str)
        cm["pv"] = cm["pv"].astype(int)
        out = out.drop(columns=["cable_vdrop_pct"]).merge(
            cm, on=["inverter_id", "pv"], how="left",
        )

    out["score"] = (
        out["deficit_vs_siblings_pct"].fillna(0.0)
        + out["inverter_p_loss_pct"].fillna(0.0)
    )
    out["status"] = np.where(
        (med > 0) & (out["pr_recent"] < med * float(dead_ratio)),
        "DEAD_OR_OFFLINE",
        "NORMAL",
    )
    # DEAD_OR_OFFLINE bukan kandidat cleaning -> dikeluarkan dari ranking
    # supaya prioritas teratas benar-benar string kotor, bukan string mati.
    rank_basis = out["score"].where(out["status"] != "DEAD_OR_OFFLINE")
    out["rank"] = rank_basis.rank(
        ascending=False, method="min",
    ).astype("Int64")
    return out.sort_values(
        ["rank", "inverter_id", "pv"], na_position="last", ignore_index=True,
    )[RECOMMENDATION_COLUMNS]


def _parse_wb_filter(value) -> Optional[set]:
    """Normalisasi cfg ``wb_filter`` (mis. ["WB01", "wb02", 3]) -> {1, 2, 3}.

    Kosong/None -> None (tanpa filter). Dipakai saat analysis run per
    kelompok WB supaya cleaning events kelompok lain tidak ikut
    mengklasifikasi interval SRR kelompok ini.
    """
    if not value:
        return None
    out = set()
    for item in value:
        s = str(item).strip().upper()
        if s.startswith("WB"):
            s = s[2:]
        try:
            out.add(int(s))
        except ValueError:
            warnings.warn(
                f"[M2aSoiling] wb_filter entry tidak dikenal: {item!r}",
                stacklevel=2,
            )
    return out or None


def _load_manual_cleaning(
    cleaning_report_path: str,
    dc_cable_list_path: str,
    wb_filter: Optional[set] = None,
) -> Tuple[Optional[pd.DataFrame], Optional[pd.Series]]:
    """Load rekap cleaning manual (optional, non-fatal seperti presipitasi).

    Returns (events_df, daily_counts) atau (None, None) bila path kosong /
    file tidak ada / gagal parse. ``wb_filter`` membatasi events ke kelompok
    WB tertentu (nomor int).
    """
    if not cleaning_report_path or not os.path.exists(cleaning_report_path):
        return None, None
    st_to_pv = None
    if dc_cable_list_path:
        if os.path.exists(dc_cable_list_path):
            try:
                st_to_pv = build_st_to_pv(load_dc_cable_map(dc_cable_list_path))
            except Exception as exc:
                warnings.warn(
                    f"[M2aSoiling] DC cable list load failed "
                    f"({dc_cable_list_path}): {exc}. ST->PV mapping WB03-10 "
                    "tidak tersedia (pv=NaN).",
                    stacklevel=2,
                )
        else:
            warnings.warn(
                f"[M2aSoiling] dc_cable_list_path tidak ditemukan: "
                f"{dc_cable_list_path!r}.",
                stacklevel=2,
            )
    try:
        events = load_cleaning_report(cleaning_report_path, st_to_pv)
    except Exception as exc:
        warnings.warn(
            f"[M2aSoiling] cleaning report load failed "
            f"({cleaning_report_path}): {exc}",
            stacklevel=2,
        )
        return None, None
    if wb_filter is not None:
        events = events[events["wb"].isin(wb_filter)].reset_index(drop=True)
    if events.empty:
        return None, None
    return events, daily_cleaning_counts(events)


_M2E_FINDINGS_FILE_RE = re.compile(r"^m2_findings_(\d{8})\.(jsonl|xlsx)$", re.I)
# jsonl gagal (setelah retry) sebanyak ini TANGGAL BERUNTUN sementara xlsx
# jalan -> mount DriveFS sedang bermasalah dengan jsonl secara persisten;
# skip jsonl untuk tanggal berikutnya supaya tidak buang waktu retry ~470x.
_AVAILABILITY_JSONL_SKIP_AFTER = 5


def _parse_availability_jsonl(path: str, day: pd.Timestamp) -> List[dict]:
    """Parse satu m2_findings jsonl -> rows [date, inverter_id, uptime_pct]."""
    rows: List[dict] = []
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("sub_module") != "M2e_inverter":
                continue
            rows.append({
                "date": day,
                "inverter_id": str(rec.get("inverter_id")),
                "uptime_pct": float(rec.get("value")),
            })
    return rows


def _parse_availability_xlsx(path: str, day: pd.Timestamp) -> List[dict]:
    """Parse satu m2_findings xlsx (sheet Findings) -> rows."""
    df = pd.read_excel(path, sheet_name="Findings")
    if "sub_module" not in df.columns:
        return []
    sub = df[df["sub_module"] == "M2e_inverter"]
    return [{
        "date": day,
        "inverter_id": str(r["inverter_id"]),
        "uptime_pct": float(r["value"]),
    } for _, r in sub.iterrows()]


def _load_availability_uptime(
    dir_path: str,
    *,
    retries: int = 3,
    retry_delay_s: float = 2.0,
    min_coverage_warn: float = 0.9,
) -> Optional[pd.DataFrame]:
    """Load uptime inverter per hari dari output M2e daily notebook.

    File yang dikenali di ``dir_path`` (folder outputs Drive):
    ``m2_findings_{YYYYMMDD}.jsonl`` / ``.xlsx`` (hasil M2Engine.write_jsonl
    / write_xlsx_multi). Baris yang dipakai: ``sub_module == "M2e_inverter"``
    dengan ``value`` = uptime_pct. Catatan: M2e default hanya emit inverter
    NON-NORMAL (uptime di bawah ambang severity), jadi inverter yang tidak
    muncul dianggap available penuh.

    Ketangguhan terhadap DriveFS Colab (errno 107 "Transport endpoint is
    not connected" saat burst baca ratusan file kecil):
      1. Tiap file dibaca dengan ``retries`` percobaan berjeda
         ``retry_delay_s`` detik (errno 107 transien pulih dalam hitungan
         detik).
      2. Bila jsonl tetap gagal, fallback ke xlsx TANGGAL YANG SAMA
         (jsonl hanya prioritas kecepatan parse, isinya identik).
      3. Fail loud: satu baris ringkasan "X/N tanggal terbaca" selalu
         diprint, plus UserWarning keras bila cakupan < min_coverage_warn
         -- kejadian "mask diam-diam kosong" langsung terlihat, bukan
         terkubur di ratusan warning per file.
    jsonl yang gagal ``_AVAILABILITY_JSONL_SKIP_AFTER`` tanggal beruntun
    di-skip untuk sisa tanggal (pola kegagalan persisten).

    Returns df [date, inverter_id, uptime_pct]; df kosong bila semua
    tanggal terbaca tapi tak ada finding M2e_inverter; None bila folder
    tidak ada / tidak ada file dikenali / SEMUA tanggal gagal dibaca.
    """
    if not dir_path or not os.path.isdir(dir_path):
        return None
    by_date: Dict[str, Dict[str, str]] = {}
    for fn in sorted(os.listdir(dir_path)):
        m = _M2E_FINDINGS_FILE_RE.match(fn)
        if not m:
            continue
        date_str, ext = m.group(1), m.group(2).lower()
        by_date.setdefault(date_str, {})[ext] = os.path.join(dir_path, fn)
    if not by_date:
        return None

    rows: List[dict] = []
    n_ok = 0
    n_fallback = 0
    failed_dates: List[str] = []
    last_exc: Optional[Exception] = None
    jsonl_fail_streak = 0

    for date_str, paths in sorted(by_date.items()):
        day = pd.Timestamp(datetime.strptime(date_str, "%Y%m%d")).normalize()
        formats = []
        if "jsonl" in paths and jsonl_fail_streak < _AVAILABILITY_JSONL_SKIP_AFTER:
            formats.append(("jsonl", paths["jsonl"], _parse_availability_jsonl))
        if "xlsx" in paths:
            formats.append(("xlsx", paths["xlsx"], _parse_availability_xlsx))

        day_rows: Optional[List[dict]] = None
        jsonl_ok_here = False
        jsonl_failed_here = False
        for ext, path, parser in formats:
            got: Optional[List[dict]] = None
            for attempt in range(max(1, retries)):
                try:
                    got = parser(path, day)
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt < retries - 1 and retry_delay_s > 0:
                        time.sleep(retry_delay_s)
            if got is not None:
                day_rows = got
                if ext == "jsonl":
                    jsonl_ok_here = True
                elif "jsonl" in paths:
                    n_fallback += 1
                break
            if ext == "jsonl":
                jsonl_failed_here = True

        if jsonl_ok_here:
            jsonl_fail_streak = 0
        elif jsonl_failed_here:
            jsonl_fail_streak += 1

        if day_rows is None:
            failed_dates.append(date_str)
        else:
            n_ok += 1
            rows.extend(day_rows)

    n_total = len(by_date)
    summary = f"[M2aSoiling] availability: {n_ok}/{n_total} tanggal terbaca"
    if n_fallback:
        summary += f" ({n_fallback} via fallback xlsx)"
    if failed_dates:
        summary += f", {len(failed_dates)} gagal"
    print(summary)

    if n_ok < n_total * float(min_coverage_warn):
        warnings.warn(
            f"[M2aSoiling] MASK AVAILABILITY TIDAK LENGKAP: hanya "
            f"{n_ok}/{n_total} tanggal terbaca (contoh gagal: "
            f"{failed_dates[:5]}; error terakhir: {last_exc}). Outage "
            "parsial pada tanggal yang gagal TIDAK ter-mask -- hasil SRR "
            "bisa bias. Coba drive.mount(force_remount=True) atau salin "
            "folder outputs ke disk lokal Colab dulu.",
            stacklevel=2,
        )
    if n_ok == 0:
        return None
    return pd.DataFrame(rows, columns=["date", "inverter_id", "uptime_pct"])


def build_availability_mask(
    av_df: Optional[pd.DataFrame],
    min_uptime_pct: float = DEFAULT_AVAILABILITY_MIN_UPTIME_PCT,
) -> Dict[str, Set[pd.Timestamp]]:
    """{INVERTER_ID: set tanggal} dengan uptime < ambang (inverter-day
    yang harus di-mask dari deret PR)."""
    if av_df is None or av_df.empty:
        return {}
    uptime = pd.to_numeric(av_df["uptime_pct"], errors="coerce")
    low = av_df[uptime < float(min_uptime_pct)]
    mask: Dict[str, Set[pd.Timestamp]] = {}
    for inv, g in low.groupby(low["inverter_id"].astype(str).str.upper()):
        mask[str(inv)] = set(pd.to_datetime(g["date"]).dt.normalize())
    return mask


class M2aSoiling(SubModule):
    """Soiling detector via rdtools SRR (Stochastic Rate and Recovery).

    Dependency injection (optional):
        prov = POAProvider.from_yaml(...)
        sm = M2aSoiling(poa=prov)
    """

    name: str = "M2a_soiling"

    def __init__(self, poa=None, cell_temp=None, panel=None):
        super().__init__()
        self.poa = poa
        self.cell_temp = cell_temp
        self.panel = panel
        # Diisi oleh run(); dipakai runner/notebook untuk plotting & join.
        self.last_pr_daily = None
        self.last_calc_info = None
        self.last_insolation_daily = None
        self.last_soiling_profiles = None

    def _ensure_providers(self, config: dict) -> None:
        if self.poa is None:
            from pv_pipeline.poa.provider import POAProvider
            geom_path = (
                config.get("poa", {})
                .get("site_geometry_path", "config/site_geometry.yaml")
            )
            self.poa = POAProvider.from_yaml(geom_path)

        # Cell-temp + panel hanya untuk koreksi suhu (opsional, non-fatal):
        # kalau gagal init, koreksi dilewati (temp_factor=1).
        cfg = config.get("m2a_soiling", {}) or {}
        if not bool(cfg.get("temperature_correction", DEFAULT_TEMPERATURE_CORRECTION)):
            return
        geom_path = (
            config.get("poa", {})
            .get("site_geometry_path", "config/site_geometry.yaml")
        )
        if self.cell_temp is None:
            try:
                from pv_pipeline.cell_temp import CellTempProvider
                self.cell_temp = CellTempProvider.from_geometry_yaml(geom_path)
            except Exception as exc:
                warnings.warn(
                    f"[M2aSoiling] CellTempProvider init gagal ({exc}); "
                    "koreksi suhu dilewati (temp_factor=1).",
                    stacklevel=2,
                )
        if self.panel is None:
            try:
                from pv_pipeline.panel_spec import PanelSpec
                panel_path = config.get("panel", {}).get(
                    "spec_path", "config/panel_spec.yaml"
                )
                self.panel = PanelSpec.from_yaml(panel_path)
            except Exception as exc:
                warnings.warn(
                    f"[M2aSoiling] PanelSpec init gagal ({exc}); "
                    "koreksi suhu pakai gamma default.",
                    stacklevel=2,
                )

    def _panel_gamma(self, cfg: dict) -> float:
        """gamma Pmax (%/C): cfg override > panel spec > default."""
        if "temp_coef_pmax_pct_per_c" in cfg:
            return float(cfg["temp_coef_pmax_pct_per_c"])
        panel = getattr(self, "panel", None)
        tc = getattr(panel, "temp_coef", None)
        if tc is not None and getattr(tc, "pmax_pct_per_c", None) is not None:
            return float(tc.pmax_pct_per_c)
        return DEFAULT_TEMP_COEF_PMAX_PCT_PER_C

    def _build_daily_series(
        self,
        combined_df: pd.DataFrame,
        cfg: dict,
        empty_map: dict,
        avail_mask: Optional[Dict[str, Set[pd.Timestamp]]] = None,
    ) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
        """Aggregate all inverters into site-level daily energy + insolation
        + faktor koreksi suhu + faktor kapasitas tersedia.

        ``avail_mask`` ({INVERTER_ID: set tanggal}, dari build_availability_mask):
        inverter-day yang di-mask dibuang dari energi harian DAN kapasitas
        inverternya dikeluarkan dari penyebut hari itu (capacity_factor < 1)
        supaya outage parsial tidak menekan PR (terbaca soiling oleh SRR).

        Returns (energy_daily_kwh, insolation_daily_kwh_per_m2,
        temp_factor_daily, capacity_factor_daily). temp_factor_daily =
        insolation-weighted CF per hari (=1.0 bila koreksi off/cell-temp tak
        tersedia); capacity_factor_daily = fraksi kapasitas available (=1.0
        tanpa mask).
        """
        pv_max = int(cfg.get("pv_max", DEFAULT_PV_MAX))
        freq_hours = float(cfg.get("sample_freq_hours", DEFAULT_SAMPLE_FREQ_HOURS))
        temp_enabled = bool(cfg.get(
            "temperature_correction", DEFAULT_TEMPERATURE_CORRECTION
        )) and self.cell_temp is not None
        gamma = self._panel_gamma(cfg)
        ref_c = float(cfg.get("temp_ref_c", DEFAULT_TEMP_REF_C))
        collect_per_inverter = bool(cfg.get("per_inverter_srr", False))
        self._per_inverter_daily = {}
        self._n_avail_masked_days = 0
        avail_mask = avail_mask or {}

        all_energy_daily: List[pd.Series] = []
        all_poa_per_ts: List[pd.Series] = []
        all_poa_cf_per_ts: List[pd.Series] = []   # poa * CF (untuk temp_factor)
        total_cap_kwp = 0.0
        masked_cap_by_day: Dict[pd.Timestamp, float] = {}

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
            e_d = aggregate_daily(ts_clean, p_inv, freq_hours=freq_hours)
            cap_i = _inverter_capacity_kwp(inverter_id, empty_map, pv_max)
            total_cap_kwp += cap_i
            inv_masked = avail_mask.get(str(inverter_id).upper())
            if inv_masked:
                drop_idx = e_d.index.intersection(
                    pd.DatetimeIndex(sorted(inv_masked))
                )
                if len(drop_idx) > 0:
                    e_d = e_d.drop(drop_idx)
                    self._n_avail_masked_days += int(len(drop_idx))
                for d in inv_masked:
                    masked_cap_by_day[d] = masked_cap_by_day.get(d, 0.0) + cap_i
            if not e_d.empty:
                all_energy_daily.append(e_d)

            wb_parts = str(inverter_id).split("-")
            wb_id = wb_parts[0].upper() if wb_parts else str(inverter_id).upper()
            try:
                poa = self.poa.get_poa(ts_clean, wb_id, source="auto")
                poa_vals = poa.reindex(ts_clean).fillna(0.0).values
                all_poa_per_ts.append(pd.Series(poa_vals, index=ts_clean))
            except Exception as exc:
                warnings.warn(
                    f"[M2aSoiling] POA query failed (wb={wb_id}): {exc}",
                    stacklevel=2,
                )
                continue

            cf_vals = np.ones(len(ts_clean), dtype=float)
            if temp_enabled:
                try:
                    tcell = self.cell_temp.get_tcell(ts_clean, wb_id, source="auto")
                    tcell = pd.to_numeric(
                        tcell.reindex(ts_clean), errors="coerce"
                    ).to_numpy()
                    cf = temp_correction_factor(tcell, gamma, ref_c)
                    cf_vals = np.where(np.isfinite(cf), cf, 1.0)
                except Exception as exc:
                    warnings.warn(
                        f"[M2aSoiling] Tcell query failed (wb={wb_id}): {exc}; "
                        "CF=1 untuk inverter ini.",
                        stacklevel=2,
                    )
            all_poa_cf_per_ts.append(pd.Series(poa_vals * cf_vals, index=ts_clean))

            if collect_per_inverter:
                # e_d sudah ter-mask availability -> PerInverterSRR juga
                # bebas outage parsial.
                self._per_inverter_daily[str(inverter_id)] = (
                    e_d,
                    aggregate_daily(
                        ts_clean, poa_vals / 1000.0, freq_hours=freq_hours,
                    ),
                )

        if not all_energy_daily:
            empty = pd.Series(dtype=float)
            return empty, empty.copy(), empty.copy(), empty.copy()

        # Site-aggregate: sum energi harian per inverter (ter-mask); POA
        # mean per timestamp (POA shared site, tidak terpengaruh outage).
        energy_daily = pd.concat(all_energy_daily).groupby(level=0).sum()
        poa_concat = pd.concat(all_poa_per_ts).groupby(level=0).mean()

        poa_daily_kwh = aggregate_daily(
            poa_concat.index, poa_concat.values / 1000.0,
            freq_hours=freq_hours,
        )

        # capacity_factor_daily: fraksi kapasitas available per hari.
        capacity_factor_daily = pd.Series(1.0, index=poa_daily_kwh.index)
        if masked_cap_by_day and total_cap_kwp > 0:
            for d, kwp in masked_cap_by_day.items():
                dd = pd.Timestamp(d).normalize()
                if dd in capacity_factor_daily.index:
                    capacity_factor_daily.loc[dd] = max(
                        0.0, 1.0 - kwp / total_cap_kwp,
                    )

        # temp_factor_daily = insolation-weighted CF: sum(poa*CF)/sum(poa) per
        # hari (skala POA batal sebagai rasio). =1.0 kalau koreksi off.
        if temp_enabled and all_poa_cf_per_ts:
            poacf_sum = pd.concat(all_poa_cf_per_ts).groupby(level=0).sum()
            poa_sum = pd.concat(all_poa_per_ts).groupby(level=0).sum()
            poacf_daily = aggregate_daily(poacf_sum.index, poacf_sum.values, freq_hours=freq_hours)
            poaw_daily = aggregate_daily(poa_sum.index, poa_sum.values, freq_hours=freq_hours)
            temp_factor_daily = (poacf_daily / poaw_daily.replace(0.0, np.nan)).fillna(1.0)
        else:
            temp_factor_daily = pd.Series(1.0, index=poa_daily_kwh.index)
        return energy_daily, poa_daily_kwh, temp_factor_daily, capacity_factor_daily

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
        cleaning_report_path = str(cfg.get(
            "cleaning_report_path", DEFAULT_CLEANING_REPORT_PATH
        ))
        dc_cable_list_path = str(cfg.get(
            "dc_cable_list_path", DEFAULT_DC_CABLE_LIST_PATH
        ))
        cleaning_window_days = int(cfg.get(
            "cleaning_match_window_days", DEFAULT_CLEANING_MATCH_WINDOW_DAYS
        ))
        cleaning_precip_thr = float(cfg.get(
            "cleaning_precip_threshold_mm", DEFAULT_CLEANING_PRECIP_THRESHOLD_MM
        ))
        rdtools_reps = int(cfg.get("rdtools_reps", DEFAULT_RDTOOLS_REPS))
        rdtools_ci = float(cfg.get("rdtools_confidence_level", DEFAULT_RDTOOLS_CONFIDENCE))
        # Knob segmentasi interval SRR (default = default rdtools 3.2).
        # Penting di iklim monsoon + POA clearsky: PR harian berisik membuat
        # shift-detector melihat "cleaning" palsu -> interval pendek -> bisa
        # NoValidIntervalError. clean_criterion 'precip'/'precip_and_shift'
        # + precip_threshold (satuan mm, ikut data presipitasi kami) dan
        # min_interval_length lebih pendek membantu.
        rdtools_clean_criterion = str(cfg.get("rdtools_clean_criterion", "shift"))
        rdtools_precip_threshold = float(cfg.get("rdtools_precip_threshold", 0.01))
        rdtools_min_interval_length = int(cfg.get("rdtools_min_interval_length", 7))
        rdtools_day_scale = int(cfg.get("rdtools_day_scale", 13))

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

        # Mask availability M2e (opsional): inverter-day uptime < ambang
        # dibuang dari energi + kapasitas supaya outage parsial tidak
        # terbaca sebagai soiling oleh SRR.
        availability_dir = str(cfg.get("availability_dir", DEFAULT_AVAILABILITY_DIR))
        avail_min_uptime = float(cfg.get(
            "availability_min_uptime_pct", DEFAULT_AVAILABILITY_MIN_UPTIME_PCT
        ))
        avail_mask: Dict[str, Set[pd.Timestamp]] = {}
        if availability_dir:
            av_df = _load_availability_uptime(availability_dir)
            if av_df is None:
                warnings.warn(
                    f"[M2aSoiling] availability_dir {availability_dir!r} tidak "
                    "berisi m2_findings_*.jsonl/.xlsx yang bisa dibaca; "
                    "mask dilewati.",
                    stacklevel=2,
                )
            else:
                avail_mask = build_availability_mask(av_df, avail_min_uptime)
                masked_rows = av_df[
                    pd.to_numeric(av_df["uptime_pct"], errors="coerce")
                    < avail_min_uptime
                ]
                if not masked_rows.empty:
                    self.artifacts["AvailabilityMask"] = masked_rows.reset_index(
                        drop=True,
                    )

        # Build daily PR series (temperature-corrected bila diaktifkan).
        temp_correction = bool(cfg.get(
            "temperature_correction", DEFAULT_TEMPERATURE_CORRECTION
        ))
        (energy_daily, insolation_daily, temp_factor_daily,
         capacity_factor_daily) = self._build_daily_series(
            combined_df, cfg, empty_map, avail_mask=avail_mask,
        )
        # Diekspos untuk konsumen eksternal (runner: PR per-string memakai
        # insolation scope yang sama).
        self.last_insolation_daily = insolation_daily
        pr_daily = compute_daily_pr_series(
            energy_daily, insolation_daily, capacity_kwp,
            temp_factor_daily=temp_factor_daily if temp_correction else None,
            capacity_factor_daily=capacity_factor_daily if avail_mask else None,
        )
        self.last_pr_daily = pr_daily

        # Artifact: PRDaily -- deret harian mentah untuk plotting sawtooth
        # dari xlsx tanpa run ulang (emit juga saat insufficient_data).
        if not energy_daily.empty:
            pr_frame = pd.DataFrame({
                "energy_kwh": energy_daily,
                "insolation_kwh_per_m2": insolation_daily,
                "temp_factor": temp_factor_daily,
                "capacity_factor": capacity_factor_daily,
                "pr": pr_daily,
            })
            pr_frame.index.name = "date"
            self.artifacts["PRDaily"] = pr_frame.reset_index()
        # Temp_Loss = insolation-weighted (1 - CF) sepanjang periode, %.
        temp_loss_pct = float("nan")
        if temp_correction and not insolation_daily.empty:
            aligned_tf = pd.DataFrame({
                "insolation": insolation_daily,
                "temp_factor": temp_factor_daily,
            }).dropna()
            w = aligned_tf["insolation"].sum()
            if w > 0:
                tf_overall = float(
                    (aligned_tf["insolation"] * aligned_tf["temp_factor"]).sum() / w
                )
                temp_loss_pct = (1.0 - tf_overall) * 100.0

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

        # Optional precipitation. Seri untuk rdtools direindex ke grid harian
        # kontinu (freq='D') -- syarat keras soiling_srr.
        precip_daily = _load_precipitation(precip_path)
        pr_srr, insolation_srr, precip_aligned = reindex_daily_frequency(
            pr_daily, insolation_daily, precip_daily,
        )

        # Optional manual cleaning report (checklist per string per tanggal).
        manual_events, manual_daily = _load_manual_cleaning(
            cleaning_report_path,
            dc_cable_list_path,
            wb_filter=_parse_wb_filter(cfg.get("wb_filter")),
        )
        if manual_events is not None:
            self.artifacts["ManualCleaning"] = manual_events

        avg_daily_kwh = float(energy_daily.tail(30).mean()) if not energy_daily.empty else 0.0

        # Artifact: DirectCleaningImpact -- pre/post PR di sekitar campaign
        # cleaning manual, INDEPENDEN SRR (dibangun sebelum soiling_srr supaya
        # tetap ada walau SRR NoValidIntervalError).
        if manual_events is not None and not manual_events.empty:
            direct = build_direct_cleaning_impact(
                pr_daily, manual_events, avg_daily_kwh, tariff_idr,
                window_days=int(cfg.get("direct_impact_window_days", DEFAULT_DIRECT_WINDOW_DAYS)),
                gap_days=int(cfg.get("direct_impact_gap_days", DEFAULT_DIRECT_GAP_DAYS)),
            )
            if not direct.empty:
                self.artifacts["DirectCleaningImpact"] = direct

        # Artifact: PerInverterSRR (opt-in cfg per_inverter_srr; mahal:
        # ~194x soiling_srr). Ringkasan sr/p_loss per inverter -- interval
        # CleaningImpact per inverter TIDAK diemit (ledakan baris); uplift
        # granular pakai DirectCleaningImpactPerString. Ditempatkan sebelum
        # SRR agregat supaya tetap terisi walau agregat NoValidIntervalError.
        if getattr(self, "_per_inverter_daily", None):
            per_reps = int(cfg.get("per_inverter_reps", 200))
            inv_rows = []
            for inv_id, (e_d, i_d) in sorted(self._per_inverter_daily.items()):
                cap_inv = _inverter_capacity_kwp(
                    inv_id, empty_map, pv_max=int(cfg.get("pv_max", DEFAULT_PV_MAX)),
                )
                pr_inv = compute_daily_pr_series(
                    e_d, i_d, cap_inv,
                    temp_factor_daily=temp_factor_daily if temp_correction else None,
                )
                row = {
                    "inverter_id": inv_id,
                    "capacity_kwp": cap_inv,
                    "n_days": int(pr_inv.size),
                    "sr": float("nan"),
                    "sr_ci_lower": float("nan"),
                    "sr_ci_upper": float("nan"),
                    "p_loss_pct": float("nan"),
                    "status": "",
                }
                if pr_inv.size < min_days:
                    row["status"] = "insufficient_data"
                else:
                    pr_f, insol_f, precip_f = reindex_daily_frequency(
                        pr_inv, i_d, precip_daily,
                    )
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            res = soiling.soiling_srr(
                                energy_normalized_daily=pr_f,
                                insolation_daily=insol_f,
                                precipitation_daily=precip_f,
                                reps=per_reps,
                                confidence_level=rdtools_ci,
                                clean_criterion=rdtools_clean_criterion,
                                precip_threshold=rdtools_precip_threshold,
                                min_interval_length=rdtools_min_interval_length,
                                day_scale=rdtools_day_scale,
                            )
                        inv_sr = (
                            float(res[0])
                            if isinstance(res, tuple) and np.isfinite(res[0])
                            else float("nan")
                        )
                        lo, hi = _ci_bounds(
                            res[1] if isinstance(res, tuple) and len(res) > 1 else None
                        )
                        row.update({
                            "sr": inv_sr,
                            "sr_ci_lower": lo,
                            "sr_ci_upper": hi,
                            "p_loss_pct": (
                                (1.0 - inv_sr) * 100.0
                                if np.isfinite(inv_sr) else float("nan")
                            ),
                            "status": "ok",
                        })
                    except Exception as exc:
                        row["status"] = exc.__class__.__name__
                inv_rows.append(row)
            if inv_rows:
                per_inv = pd.DataFrame(inv_rows)
                per_inv["rank_p_loss"] = per_inv["p_loss_pct"].rank(
                    ascending=False, method="min",
                )
                self.artifacts["PerInverterSRR"] = per_inv.sort_values(
                    "p_loss_pct", ascending=False, na_position="last",
                    ignore_index=True,
                )

        try:
            sr_result = soiling.soiling_srr(
                energy_normalized_daily=pr_srr,
                insolation_daily=insolation_srr,
                precipitation_daily=precip_aligned,
                reps=rdtools_reps,
                confidence_level=rdtools_ci,
                clean_criterion=rdtools_clean_criterion,
                precip_threshold=rdtools_precip_threshold,
                min_interval_length=rdtools_min_interval_length,
                day_scale=rdtools_day_scale,
            )
            if isinstance(sr_result, tuple) and len(sr_result) >= 3:
                sr, sr_ci, calc_info = sr_result[0], sr_result[1], sr_result[2]
            else:
                sr = getattr(sr_result, "soiling_ratio", float("nan"))
                sr_ci = (float("nan"), float("nan"))
                calc_info = {}
            self.last_calc_info = calc_info if isinstance(calc_info, dict) else {}
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
        # sr_ci dari rdtools = np.ndarray [lower, upper]; jangan batasi ke
        # tuple/list saja (bikin CI selalu NaN).
        ci_lower, ci_upper = _ci_bounds(sr_ci)

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
                "manual_cleaning_available": manual_daily is not None,
                "n_manual_cleaning_days": (
                    int(manual_daily.size) if manual_daily is not None else 0
                ),
                "avg_daily_kwh": avg_daily_kwh,
                "daily_loss_idr": daily_loss_idr,
                "payback_days": payback_days if np.isfinite(payback_days) else None,
                "cleaning_cost_idr": cleaning_cost_idr,
                "electricity_tariff_idr_per_kwh": tariff_idr,
                "temperature_correction": temp_correction,
                "temp_loss_pct": temp_loss_pct if np.isfinite(temp_loss_pct) else None,
                "availability_mask_applied": bool(avail_mask),
                "availability_min_uptime_pct": avail_min_uptime if avail_mask else None,
                "n_availability_masked_inverter_days": int(
                    getattr(self, "_n_avail_masked_days", 0)
                ),
            },
        ))

        # Artifact: SoilingRatio daily series (rdtools output) -- lihat
        # summarize_soiling_profiles untuk kenapa bukan cek DataFrame.
        self.last_soiling_profiles = None
        if isinstance(calc_info, dict) and "stochastic_soiling_profiles" in calc_info:
            try:
                profiles = calc_info["stochastic_soiling_profiles"]
                if isinstance(profiles, pd.DataFrame) and not profiles.empty:
                    self.last_soiling_profiles = profiles
                elif isinstance(profiles, (list, tuple)) and len(profiles) > 0:
                    self.last_soiling_profiles = pd.concat(list(profiles), axis=1)
                summary = summarize_soiling_profiles(
                    self.last_soiling_profiles, rdtools_ci,
                )
                if summary is not None:
                    self.artifacts["SoilingRatio"] = summary
            except Exception:
                pass

        # Artifact: MonthlySoilingLoss -- breakdown bulanan dari profil SR
        # harian (insolation-weighted).
        if self.last_soiling_profiles is not None:
            monthly = build_monthly_soiling_loss(
                self.last_soiling_profiles, insolation_daily, energy_daily,
                tariff_idr, confidence_level=rdtools_ci,
            )
            if not monthly.empty:
                self.artifacts["MonthlySoilingLoss"] = monthly

        # Artifact: CleaningEvents from soiling_interval_summary, dianotasi
        # manual-vs-hujan dari rekap cleaning + presipitasi.
        if isinstance(calc_info, dict) and "soiling_interval_summary" in calc_info:
            try:
                summary = calc_info["soiling_interval_summary"]
                if isinstance(summary, pd.DataFrame) and not summary.empty:
                    self.artifacts["CleaningEvents"] = classify_cleaning_intervals(
                        summary,
                        manual_daily,
                        precip_daily,
                        window_days=cleaning_window_days,
                        precip_threshold_mm=cleaning_precip_thr,
                    )
            except Exception:
                pass

        # Artifact: CleaningImpact -- uplift eksplisit per event cleaning
        # (PR before/after, energi & rupiah dipulihkan/hari).
        ce = self.artifacts.get("CleaningEvents")
        if isinstance(ce, pd.DataFrame) and not ce.empty:
            self.artifacts["CleaningImpact"] = build_cleaning_impact(
                ce, avg_daily_kwh, tariff_idr,
            )

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
            "temperature_correction": temp_correction,
            "temp_loss_pct": temp_loss_pct,
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
