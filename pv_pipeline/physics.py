"""Physics helpers untuk PV performance baseline (Fase 2).

Sumber formulasi: ``M2_PV_Performance_Master_Context.docx`` -- Fase 2 Physics Baseline.

Functions
---------
- :func:`compute_pmax_per_module` -- Pmax(POA, Tcell) per module (W), linear temp coef.
- :func:`compute_p_expected_per_string` -- P_expected per string (W) = per-module x n_modules.
- :func:`compute_kt` -- Clearness Index Kt = POA_measured / POA_clearsky.
- :func:`compute_delta_power` -- DeltaP_ratio = (P_actual / P_expected) - 1.
- :func:`compute_active_power_integration_kwh` -- Integrate P(kW) over time -> E(kWh).
- :func:`compute_pr` -- Performance Ratio per IEC 61724-1.

Semua function accept scalar atau array-like (Series/ndarray) input. Output
mempertahankan tipe input (Series in -> Series out, scalar in -> float out).

Catatan
-------
- STC reference: G = 1000 W/m^2, T_cell = 25 C.
- Linear temp coef pakai ``panel_spec.temp_coef.pmax_pct_per_c`` (Jinko JKM625N: -0.30 %/C).
- Performance Ratio (PR) helper di-defer (butuh energy integration dari Huawei xlsx
  ``Active power(kW)`` -- spec belum di-confirm).
"""
from __future__ import annotations

from typing import Union

import numpy as np
import pandas as pd

from .panel_spec import PanelSpec


# Standard Test Conditions (IEC 61215).
G_STC_WM2: float = 1000.0
T_STC_C: float = 25.0

# Default minimum POA (W/m^2) for Kt computation. Below this, ratio is meaningless
# (numerator + denominator both near zero -> noise). Caller dapat override.
KT_MIN_POA_WM2: float = 1.0

# Default minimum expected power (W) for DeltaP computation. Below this, ratio is
# meaningless (night / sunset). Caller dapat override.
DELTA_P_MIN_EXPECTED_W: float = 1.0

# Default minimum POA irradiation (kWh/m^2) untuk PR computation. Below this,
# Performance Ratio tidak meaningful (insufficient sun). Caller dapat override.
PR_MIN_POA_KWH_PER_M2: float = 0.01


ArrayLike = Union[float, np.ndarray, pd.Series]


def compute_pmax_per_module(
    poa_wm2: ArrayLike,
    tcell_c: ArrayLike,
    panel_spec: PanelSpec,
) -> ArrayLike:
    """Pmax per module (W) untuk kondisi (POA, Tcell).

    Formula (linear temp coef, IEC 61215 convention):
        P = Pmax_STC * (POA / G_STC) * (1 + tc_pmax/100 * (Tcell - T_STC))

    Parameters
    ----------
    poa_wm2 : float or array-like
        POA irradiance (W/m^2). Boleh scalar, ndarray, atau pd.Series.
    tcell_c : float or array-like
        Cell temperature (C). Broadcast-compatible dengan poa_wm2.
    panel_spec : PanelSpec
        Datasheet panel (PanelSpec.stc.pmax_w + PanelSpec.temp_coef.pmax_pct_per_c).

    Returns
    -------
    Same type as inputs (broadcast). Pmax per single module (W).
    """
    pmax_stc_w = panel_spec.stc.pmax_w
    tc_pmax_frac = panel_spec.temp_coef.pmax_pct_per_c / 100.0
    poa_factor = poa_wm2 / G_STC_WM2
    temp_factor = 1.0 + tc_pmax_frac * (tcell_c - T_STC_C)
    return pmax_stc_w * poa_factor * temp_factor


def compute_p_expected_per_string(
    poa_wm2: ArrayLike,
    tcell_c: ArrayLike,
    panel_spec: PanelSpec,
    wb_id: str,
) -> ArrayLike:
    """P_expected per PV string (W) untuk (POA, Tcell, WB).

    P_string = compute_pmax_per_module(POA, Tcell) * modules_per_string(wb_id).

    Parameters
    ----------
    poa_wm2, tcell_c : float or array-like
        Sama seperti :func:`compute_pmax_per_module`.
    panel_spec : PanelSpec
    wb_id : str
        Workblock identifier (mis. ``"WB01"``). Untuk lookup modules_per_string.

    Returns
    -------
    P_expected per string (W). Untuk system-level, kalikan dengan jumlah strings
    per inverter (downstream caller responsibility -- bukan dari PanelSpec).
    """
    n_modules = panel_spec.modules_per_string(wb_id)
    return compute_pmax_per_module(poa_wm2, tcell_c, panel_spec) * n_modules


def compute_kt(
    poa_measured: ArrayLike,
    poa_clearsky: ArrayLike,
    *,
    min_poa_wm2: float = KT_MIN_POA_WM2,
) -> ArrayLike:
    """Clearness Index Kt = POA_measured / POA_clearsky.

    Kt < 1 -> cloudy/hazy, Kt ~ 1 -> clear-sky, Kt > 1 -> cloud enhancement edge
    (jarang, biasanya artifact). Untuk pre-screen "clear-sky day" filter.

    Parameters
    ----------
    poa_measured : float or array-like
        Pyranometer reading (W/m^2).
    poa_clearsky : float or array-like
        pvlib estimator output (W/m^2). Broadcast-compatible.
    min_poa_wm2 : float, default KT_MIN_POA_WM2
        Posisi dengan ``poa_clearsky < min_poa_wm2`` -> Kt = NaN (avoid div-near-zero
        di sunrise/sunset/night).

    Returns
    -------
    Kt sebagai Series (kalau salah satu input Series), ndarray, atau float scalar.
    NaN di posisi clearsky < min_poa_wm2.
    """
    if isinstance(poa_measured, pd.Series) or isinstance(poa_clearsky, pd.Series):
        if isinstance(poa_measured, pd.Series) and isinstance(poa_clearsky, pd.Series):
            measured, clearsky = poa_measured.align(poa_clearsky, join="outer")
        else:
            measured = (
                poa_measured
                if isinstance(poa_measured, pd.Series)
                else pd.Series(poa_measured)
            )
            clearsky = (
                poa_clearsky
                if isinstance(poa_clearsky, pd.Series)
                else pd.Series(poa_clearsky)
            )
        safe = clearsky.where(clearsky >= min_poa_wm2, np.nan)
        kt = measured / safe
        kt.name = "kt"
        return kt

    measured_arr = np.asarray(poa_measured, dtype="float64")
    clearsky_arr = np.asarray(poa_clearsky, dtype="float64")
    safe_arr = np.where(clearsky_arr >= min_poa_wm2, clearsky_arr, np.nan)
    kt_arr = measured_arr / safe_arr
    if measured_arr.ndim == 0 and clearsky_arr.ndim == 0:
        return float(kt_arr)
    return kt_arr


def compute_delta_power(
    p_actual: ArrayLike,
    p_expected: ArrayLike,
    *,
    min_expected_w: float = DELTA_P_MIN_EXPECTED_W,
) -> ArrayLike:
    """DeltaP_ratio = (P_actual / P_expected) - 1.

    Interpretasi:
    - DeltaP_ratio ~ 0   -> performing as expected.
    - DeltaP_ratio < 0   -> under-performing (soiling / shading / fault).
    - DeltaP_ratio > 0   -> over-performing (sensor cal drift, cloud-edge enhancement,
                             atau P_expected formula underestimate).

    Parameters
    ----------
    p_actual : float or array-like
        Measured power (W). Dari Huawei ``Active power(kW)`` * 1000.
    p_expected : float or array-like
        P_expected dari :func:`compute_p_expected_per_string` * n_strings_per_inverter
        (atau aggregat lain yang konsisten dengan p_actual unit).
    min_expected_w : float, default DELTA_P_MIN_EXPECTED_W
        Posisi dengan ``p_expected < min_expected_w`` -> NaN (night/sunset).

    Returns
    -------
    Sama tipe dengan input. NaN di posisi p_expected < min_expected_w.
    """
    if isinstance(p_actual, pd.Series) or isinstance(p_expected, pd.Series):
        if isinstance(p_actual, pd.Series) and isinstance(p_expected, pd.Series):
            actual, expected = p_actual.align(p_expected, join="outer")
        else:
            actual = (
                p_actual
                if isinstance(p_actual, pd.Series)
                else pd.Series(p_actual)
            )
            expected = (
                p_expected
                if isinstance(p_expected, pd.Series)
                else pd.Series(p_expected)
            )
        safe = expected.where(expected >= min_expected_w, np.nan)
        delta = (actual / safe) - 1.0
        delta.name = "delta_power_ratio"
        return delta

    actual_arr = np.asarray(p_actual, dtype="float64")
    expected_arr = np.asarray(p_expected, dtype="float64")
    safe_arr = np.where(expected_arr >= min_expected_w, expected_arr, np.nan)
    delta_arr = (actual_arr / safe_arr) - 1.0
    if actual_arr.ndim == 0 and expected_arr.ndim == 0:
        return float(delta_arr)
    return delta_arr


def compute_active_power_integration_kwh(
    power_kw: pd.Series,
    *,
    freq_hours: Optional[float] = None,
) -> float:
    """Integrate active power (kW) time-series -> energy (kWh).

    Riemann sum: ``E = sum(P_kw * dt_h)``. Untuk 5-min sampling (PLTS-IKN Huawei),
    dt = 5/60 = 0.0833 h.

    Parameters
    ----------
    power_kw : pd.Series
        Active power (kW), indexed by datetime (preferred) atau plain. NaN
        di-skip dalam sum.
    freq_hours : float, optional
        Explicit interval (hours) per sample. Bila None, auto-detect dari
        median dt di index (butuh DatetimeIndex). Wajib spesifik kalau
        index bukan DatetimeIndex atau samples tidak uniform.

    Returns
    -------
    float
        Total energy (kWh). 0.0 kalau series kosong atau semua NaN.

    Notes
    -----
    Cross-check vs ``GenerationLoader.get_period_total('total_kwh')`` untuk
    validasi: integrasi power harus close dengan daily energy STS sums.
    """
    if not isinstance(power_kw, pd.Series):
        raise TypeError(
            f"compute_active_power_integration_kwh expects pd.Series, "
            f"got {type(power_kw).__name__}"
        )
    if power_kw.empty:
        return 0.0

    if freq_hours is None:
        if not isinstance(power_kw.index, pd.DatetimeIndex):
            raise ValueError(
                "freq_hours required when power_kw.index is not DatetimeIndex."
            )
        # Median dt dalam jam.
        dt = power_kw.index.to_series().diff().dropna()
        if dt.empty:
            raise ValueError("Cannot infer freq_hours from single-sample series.")
        freq_hours = float(dt.median().total_seconds() / 3600.0)

    p_clean = power_kw.dropna()
    return float(p_clean.sum() * freq_hours)


def compute_pr(
    energy_actual_kwh: ArrayLike,
    poa_kwh_per_m2: ArrayLike,
    capacity_kwp: float,
    *,
    min_poa_kwh_per_m2: float = PR_MIN_POA_KWH_PER_M2,
) -> ArrayLike:
    """Performance Ratio per IEC 61724-1.

    PR = E_actual / E_nominal
       = E_actual_kwh / (POA_kwh_per_m2 * capacity_kwp / G_STC)
       = E_actual_kwh / (POA_kwh_per_m2 * capacity_kwp)
         (karena G_STC = 1 kW/m^2 -> capacity_kwp / G_STC = capacity_kwp numerically)

    Interpretasi:
    - PR ~ 0.75-0.85 : performing as expected (typical tropical PLTS)
    - PR < 0.70      : underperforming (soiling/shading/curtailment)
    - PR > 0.90      : suspiciously high (cal drift / shorter period / cold)

    Parameters
    ----------
    energy_actual_kwh : float or array-like
        Measured energy (kWh) over period. Dari Huawei ``Active power(kW)`` × dt
        integration ATAU dari ``IKN Generation Summary (PV)`` total_kwh.
    poa_kwh_per_m2 : float or array-like
        POA irradiation (kWh/m^2) over same period. Dari pyranometer integration
        ATAU pvlib clear-sky estimate * dt.
    capacity_kwp : float
        Installed DC capacity (kWp). Untuk PLTS-IKN site total ~71,500 kWp.
        Untuk per-WB analysis: ~7,150 kWp per WB (site/10).
    min_poa_kwh_per_m2 : float, default PR_MIN_POA_KWH_PER_M2
        Posisi dengan POA < min_poa -> NaN (avoid div-near-zero saat night
        atau very short period).

    Returns
    -------
    Sama tipe dengan input (Series in -> Series out, scalar in -> float out).
    """
    if isinstance(energy_actual_kwh, pd.Series) or isinstance(poa_kwh_per_m2, pd.Series):
        if isinstance(energy_actual_kwh, pd.Series) and isinstance(poa_kwh_per_m2, pd.Series):
            actual, poa = energy_actual_kwh.align(poa_kwh_per_m2, join="outer")
        else:
            actual = (
                energy_actual_kwh
                if isinstance(energy_actual_kwh, pd.Series)
                else pd.Series(energy_actual_kwh)
            )
            poa = (
                poa_kwh_per_m2
                if isinstance(poa_kwh_per_m2, pd.Series)
                else pd.Series(poa_kwh_per_m2)
            )
        safe_poa = poa.where(poa >= min_poa_kwh_per_m2, np.nan)
        pr = actual / (safe_poa * float(capacity_kwp))
        pr.name = "performance_ratio"
        return pr

    actual_arr = np.asarray(energy_actual_kwh, dtype="float64")
    poa_arr = np.asarray(poa_kwh_per_m2, dtype="float64")
    safe_poa = np.where(poa_arr >= min_poa_kwh_per_m2, poa_arr, np.nan)
    pr_arr = actual_arr / (safe_poa * float(capacity_kwp))
    if actual_arr.ndim == 0 and poa_arr.ndim == 0:
        return float(pr_arr)
    return pr_arr


if __name__ == "__main__":
    import sys

    yaml_path = sys.argv[1] if len(sys.argv) > 1 else "config/panel_spec.yaml"
    spec = PanelSpec.from_yaml(yaml_path)

    print(f"[physics] panel = {spec.panel_model}")
    print(f"  Pmax_STC = {spec.stc.pmax_w} W  tc_pmax = {spec.temp_coef.pmax_pct_per_c} %/C")

    scenarios = [
        ("STC reference", 1000.0, 25.0),
        ("Cold morning  ", 600.0, 20.0),
        ("Hot noon      ", 1000.0, 55.0),
        ("Sunset twilght", 50.0, 30.0),
    ]
    for label, poa, t in scenarios:
        per_mod = compute_pmax_per_module(poa, t, spec)
        per_str_wb01 = compute_p_expected_per_string(poa, t, spec, "WB01")
        per_str_wb05 = compute_p_expected_per_string(poa, t, spec, "WB05")
        print(
            f"  {label}  POA={poa:>6.1f}  Tcell={t:>4.1f}C  ->  "
            f"P/mod={per_mod:>6.1f}W  P_string_WB01(n=24)={per_str_wb01:>7.1f}W  "
            f"P_string_WB05(n=26)={per_str_wb05:>7.1f}W"
        )

    measured = pd.Series([800.0, 950.0, 100.0, 0.0], index=pd.RangeIndex(4))
    clearsky = pd.Series([1000.0, 1000.0, 500.0, 0.0], index=pd.RangeIndex(4))
    kt = compute_kt(measured, clearsky)
    print(f"\n[physics] Kt smoke (measured/clearsky):")
    for i in range(len(measured)):
        print(f"  measured={measured.iloc[i]:>6.1f}  clearsky={clearsky.iloc[i]:>6.1f}  Kt={kt.iloc[i]}")
    print("\n[physics] smoke OK")
