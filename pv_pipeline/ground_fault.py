"""M2b Ground-Fault detector untuk PV string (partial ground fault).

Spec 4.2.3 (Master Context):
    Ground fault (partial):
        I_string tinggi abnormal AND Voc turun signifikan (< 0.85)
        Butuh insulation resistance test untuk konfirmasi (tidak tersedia di SCADA)

Implementasi triple-signal cross-check:
    1. "absolute"   : |V_to_ground| > 50 V (threshold konservatif)
    2. "adaptive"   : V_to_ground inverter berbeda > 3 sigma dari fleet median
    3. "spec_4.2.3" : voc_ratio < 0.85 AND I_string z-peer > 2.0

Confidence assignment:
    spec + (absolute OR adaptive)  -> 90%
    spec only                       -> 80%
    absolute + adaptive             -> 80%
    absolute only                   -> 70%
    adaptive only                   -> 60%

Inverter-level finding (bukan per-string), karena V_to_ground diukur di inverter
DC bus. Tapi spec 4.2.3 mengindikasi worst PV string via voc_ratio terendah.

Multi-source comparison: detector loop di tiap source dari
``config.poa.sources_to_emit``, emit finding ber-tag ``poa_source``.
"""
from __future__ import annotations

import warnings
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from pv_pipeline.core import M2Finding, Severity, SubModule
from pv_pipeline.voc_estimator import estimate_voc_at_low_current


DEFAULT_POA_THRESHOLD_WM2: float = 200.0
DEFAULT_POA_FLOOR_WM2: float = 50.0       # 2026-05-16 sunset fix
DEFAULT_HOUR_CUTOFF_END: float = 18.0     # Dipakai saat filter_mode="hour_cutoff"
DEFAULT_RESPECT_INVERTER_SHUTDOWN: bool = True  # 2026-05-16 sunset fix
DEFAULT_FILTER_MODE: str = "solar_elevation"    # Fase 2: "solar_elevation" | "hour_cutoff"
DEFAULT_SOLAR_ELEV_MIN_DEG: float = 5.0          # Fase 2: sun apparent_elevation threshold
DEFAULT_V_TO_GROUND_ABS_V: float = 50.0
DEFAULT_ADAPTIVE_Z_THRESHOLD: float = 3.0
DEFAULT_VOC_RATIO_THRESHOLD: float = 0.85
DEFAULT_I_HIGH_Z_THRESHOLD: float = 2.0

# Column name candidates untuk Inverter shutdown time (Huawei xlsx).
INVERTER_SHUTDOWN_COL_CANDIDATES: List[str] = [
    "Inverter shutdown time",
    "Shutdown time",
]


def _find_shutdown_col(df: pd.DataFrame) -> Optional[str]:
    for cand in INVERTER_SHUTDOWN_COL_CANDIDATES:
        if cand in df.columns:
            return cand
    return None


# Voltage-to-ground column candidates (en-dash U+2013 vs ASCII hyphen).
V_GROUND_COLUMN_CANDIDATES: List[str] = [
    "Voltage between PV– and the ground(V)",  # en-dash (Huawei xlsx default)
    "Voltage between PV- and the ground(V)",       # ASCII hyphen fallback
    "Voltage between PV and the ground(V)",        # no dash
]


def _wb_from_inverter_id(inverter_id: str) -> str:
    if not inverter_id:
        return ""
    parts = str(inverter_id).split("-")
    return parts[0].upper() if parts else str(inverter_id).upper()


def _find_v_ground_column(df: pd.DataFrame) -> Optional[str]:
    for cand in V_GROUND_COLUMN_CANDIDATES:
        if cand in df.columns:
            return cand
    # Loose search: any column berisi "ground" + "Voltage"
    for col in df.columns:
        if "ground" in str(col).lower() and "voltage" in str(col).lower():
            return col
    return None


class M2bGroundFault(SubModule):
    """Detector ground-fault (partial) via triple-signal cross-check."""

    name: str = "M2b_ground_fault"

    def __init__(self, poa=None, panel=None, cell_temp=None):
        super().__init__()
        self.poa = poa
        self.panel = panel
        self.cell_temp = cell_temp

    def _ensure_providers(self, config: dict) -> None:
        if self.poa is None:
            from pv_pipeline.poa.provider import POAProvider
            geom_path = config.get("poa", {}).get("site_geometry_path", "config/site_geometry.yaml")
            self.poa = POAProvider.from_yaml(geom_path)
        if self.panel is None:
            from pv_pipeline.panel_spec import PanelSpec
            panel_path = config.get("panel", {}).get("spec_path", "config/panel_spec.yaml")
            self.panel = PanelSpec.from_yaml(panel_path)
        if self.cell_temp is None:
            from pv_pipeline.cell_temp import CellTempProvider
            geom_path = config.get("poa", {}).get("site_geometry_path", "config/site_geometry.yaml")
            self.cell_temp = CellTempProvider.from_geometry_yaml(geom_path)

    def _resolve_sources(self, config: dict) -> List[str]:
        poa_cfg = config.get("poa", {})
        if poa_cfg.get("emit_all_sources", True):
            return list(poa_cfg.get("sources_to_emit", ["auto"]))
        return [poa_cfg.get("default_source", "auto")]

    @staticmethod
    def _confidence_for(triggered: List[str]) -> float:
        has_spec = "spec_4.2.3" in triggered
        has_abs = "absolute" in triggered
        has_adp = "adaptive" in triggered
        if has_spec and (has_abs or has_adp):
            return 90.0
        if has_spec:
            return 80.0
        if has_abs and has_adp:
            return 80.0
        if has_abs:
            return 70.0
        if has_adp:
            return 60.0
        return 0.0

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        cfg = config.get("m2b_ground_fault", {}) or {}
        poa_threshold = float(cfg.get("poa_threshold_wm2", DEFAULT_POA_THRESHOLD_WM2))
        poa_floor = float(cfg.get("poa_floor_wm2", DEFAULT_POA_FLOOR_WM2))
        hour_cutoff_end = float(cfg.get("hour_cutoff_end", DEFAULT_HOUR_CUTOFF_END))
        respect_inverter_shutdown = bool(cfg.get(
            "respect_inverter_shutdown", DEFAULT_RESPECT_INVERTER_SHUTDOWN
        ))
        filter_mode = str(cfg.get("filter_mode", DEFAULT_FILTER_MODE)).lower()
        solar_elev_min_deg = float(cfg.get("solar_elevation_min_deg", DEFAULT_SOLAR_ELEV_MIN_DEG))
        v_abs_threshold = float(cfg.get("v_to_ground_abs_threshold_v", DEFAULT_V_TO_GROUND_ABS_V))
        adaptive_z_threshold = float(cfg.get("adaptive_z_threshold", DEFAULT_ADAPTIVE_Z_THRESHOLD))
        voc_ratio_threshold = float(cfg.get("voc_ratio_threshold", DEFAULT_VOC_RATIO_THRESHOLD))
        i_high_z_threshold = float(cfg.get("i_high_z_threshold", DEFAULT_I_HIGH_Z_THRESHOLD))
        pv_max = int(cfg.get("pv_max", 28))
        min_daylight_samples = int(cfg.get("min_daylight_samples", 5))

        shutdown_col = _find_shutdown_col(combined_df) if respect_inverter_shutdown else None

        if "Inverter_ID" not in combined_df.columns or "Start Time" not in combined_df.columns:
            warnings.warn(
                "[M2bGroundFault] missing 'Inverter_ID' or 'Start Time'; skipping.",
                stacklevel=2,
            )
            return []

        v_gnd_col = _find_v_ground_column(combined_df)
        if v_gnd_col is None:
            warnings.warn(
                "[M2bGroundFault] no 'Voltage between PV- and the ground(V)' column found; skipping.",
                stacklevel=2,
            )
            return []

        # Wave 9: optional Hampel preprocessing (V/I per-PV + V_to_ground).
        prep_cfg = config.get("preprocessing", {}) or {}
        prep_enabled = bool(prep_cfg.get("enabled", False))
        if prep_enabled:
            from pv_pipeline.preprocessing import (
                apply_hampel_to_pv_dataframe,
                clean_with_hampel,
            )
            prep_window = int(prep_cfg.get("window", 15))
            prep_max_dev = float(prep_cfg.get("max_deviation", 3.0))
            combined_df, prep_audit = apply_hampel_to_pv_dataframe(
                combined_df, pv_max=pv_max,
                window=prep_window, max_deviation=prep_max_dev,
            )
            # Clean V_to_ground column juga (ground_fault-specific).
            v_gnd_orig = pd.to_numeric(combined_df[v_gnd_col], errors="coerce")
            if v_gnd_orig.notna().sum() >= 2:
                v_gnd_cleaned = clean_with_hampel(
                    v_gnd_orig, window=prep_window, max_deviation=prep_max_dev,
                )
                n_v_out = int(v_gnd_orig.notna().sum() - v_gnd_cleaned.notna().sum())
                combined_df = combined_df.copy()
                combined_df[v_gnd_col] = v_gnd_cleaned
                prep_audit.append({
                    "column": v_gnd_col,
                    "n_outliers": n_v_out,
                    "total_samples": int(v_gnd_orig.notna().sum()),
                    "pct_outliers": (
                        n_v_out / max(int(v_gnd_orig.notna().sum()), 1) * 100.0
                    ),
                })
            if prep_audit:
                self.artifacts["PreprocessingAudit"] = pd.DataFrame(prep_audit)

        self._ensure_providers(config)
        sources = self._resolve_sources(config)

        # Wave 11 hotfix #11: normalize Title Case V/I cols (PV15-28) ke
        # lowercase canonical supaya analysis catch all PV1-PV28.
        _rename_map = {}
        for _col in combined_df.columns:
            if "Input Voltage" in _col:
                _rename_map[_col] = _col.replace("Input Voltage", "input voltage")
            elif "Input Current" in _col:
                _rename_map[_col] = _col.replace("Input Current", "input current")
        if _rename_map:
            combined_df = combined_df.rename(columns=_rename_map)

        findings: List[M2Finding] = []
        artifact_rows: List[dict] = []
        # Wave 8: per-PV-string status (NORMAL atau ground_fault).
        string_status_rows: List[dict] = []

        all_ts = pd.to_datetime(combined_df["Start Time"], errors="coerce")
        latest_ts = all_ts.max() if not all_ts.dropna().empty else datetime.utcnow()

        # Wave 11 hotfix #10: load empty_pv_map ONCE supaya main loop skip
        # empty PV slots (false-positive prevention).
        from pv_pipeline.core import load_empty_pv_map as _load_emap_mainloop_gf
        _empty_map_main_gf = _load_emap_mainloop_gf(config)

        # Fleet-wide V_to_ground statistics (across semua inverter, daylight-agnostic untuk simplicity).
        v_gnd_all = pd.to_numeric(combined_df[v_gnd_col], errors="coerce").dropna()
        if v_gnd_all.empty:
            return []
        v_gnd_fleet_median = float(v_gnd_all.median())
        v_gnd_fleet_std = float(v_gnd_all.std()) if v_gnd_all.std() > 0 else 1.0

        for poa_source in sources:
            for inverter_id, group in combined_df.groupby("Inverter_ID"):
                wb_id = _wb_from_inverter_id(inverter_id)
                _inv_empties_gf = _empty_map_main_gf.get(str(inverter_id).upper(), [])
                _inv_empty_set_gf = set(int(n) for n in _inv_empties_gf)
                timestamps = pd.to_datetime(group["Start Time"], errors="coerce")
                valid_idx = timestamps.notna()
                if valid_idx.sum() < min_daylight_samples:
                    continue
                ts_clean = pd.DatetimeIndex(timestamps[valid_idx].values)
                group_clean = group.loc[valid_idx].copy()
                group_clean.index = ts_clean

                try:
                    poa = self.poa.get_poa(ts_clean, wb_id, source=poa_source)
                except Exception as exc:
                    warnings.warn(
                        f"[M2bGroundFault] POA query failed (wb={wb_id}, src={poa_source}): {exc}",
                        stacklevel=2,
                    )
                    continue

                # Sunset fix (2026-05-16) + Fase 2 solar_elevation filter.
                poa_aligned = poa.reindex(ts_clean).fillna(0.0)
                mask_poa_main = (poa_aligned > poa_threshold) & (poa_aligned > poa_floor)

                effective_mode = filter_mode
                if filter_mode == "solar_elevation":
                    try:
                        elev = self.poa.get_solar_elevation(ts_clean)
                        elev_aligned = elev.reindex(ts_clean)
                        if elev_aligned.notna().any():
                            # Wave 11 hotfix #7: defensive AND dengan hour_cutoff.
                            # Tanpa ini, IKN equator twilight 18:15 lolos filter
                            # -> false-positive ground_fault emit.
                            elev_mask = elev_aligned.fillna(-90.0).values > solar_elev_min_deg
                            hour_arr_se = ts_clean.hour + ts_clean.minute / 60.0
                            hour_mask_se = hour_arr_se < hour_cutoff_end
                            mask_time = pd.Series(
                                elev_mask & hour_mask_se,
                                index=ts_clean,
                            )
                        else:
                            effective_mode = "hour_cutoff"
                            hour_arr = ts_clean.hour + ts_clean.minute / 60.0
                            mask_time = pd.Series(hour_arr < hour_cutoff_end, index=ts_clean)
                    except Exception as exc:
                        warnings.warn(
                            f"[M2bGroundFault] solar_elevation query failed (wb={wb_id}): {exc}. "
                            f"Falling back ke hour_cutoff.",
                            stacklevel=2,
                        )
                        effective_mode = "hour_cutoff"
                        hour_arr = ts_clean.hour + ts_clean.minute / 60.0
                        mask_time = pd.Series(hour_arr < hour_cutoff_end, index=ts_clean)
                else:
                    hour_arr = ts_clean.hour + ts_clean.minute / 60.0
                    mask_time = pd.Series(hour_arr < hour_cutoff_end, index=ts_clean)

                mask_shutdown = pd.Series(True, index=ts_clean)
                if respect_inverter_shutdown and shutdown_col is not None:
                    raw_shut = pd.to_datetime(group_clean[shutdown_col], errors="coerce")
                    valid_shut = raw_shut.dropna()
                    # Wave 11 hotfix #5: drop sentinel datetimes (year<2000).
                    if not valid_shut.empty:
                        valid_shut = valid_shut[valid_shut.dt.year >= 2000]
                    if not valid_shut.empty:
                        shutdown_ts = valid_shut.min()
                        proposed = pd.Series(ts_clean < shutdown_ts, index=ts_clean)
                        # Wave 11 hotfix #6: skip filter kalau proposed mask
                        # drop ALL ts (likely sentinel).
                        if proposed.sum() > 0:
                            mask_shutdown = proposed
                daylight_mask = pd.Series(
                    mask_poa_main.values & mask_time.values & mask_shutdown.values,
                    index=ts_clean,
                )
                if daylight_mask.sum() < min_daylight_samples:
                    continue

                # V_to_ground statistics di inverter ini, daylight only.
                v_gnd_series = pd.to_numeric(group_clean[v_gnd_col], errors="coerce")
                v_gnd_daylight = v_gnd_series[daylight_mask.values].dropna()
                if len(v_gnd_daylight) < 3:
                    continue
                v_gnd_max_abs = float(v_gnd_daylight.abs().max())
                v_gnd_median = float(v_gnd_daylight.median())
                v_gnd_adaptive_z = float(abs(v_gnd_median - v_gnd_fleet_median) / max(v_gnd_fleet_std, 0.01))

                flagged_absolute = v_gnd_max_abs > v_abs_threshold
                flagged_adaptive = v_gnd_adaptive_z > adaptive_z_threshold

                # Spec 4.2.3 check: per-string voc_ratio dan I_z.
                try:
                    tcell = self.cell_temp.get_tcell(ts_clean, wb_id)
                    tcell_daylight = tcell[daylight_mask.values].dropna()
                    tcell_mean = float(tcell_daylight.mean()) if not tcell_daylight.empty else 25.0
                except Exception:
                    tcell_mean = 25.0

                voc_per_module = self.panel.voc_at_cell_temp(tcell_mean, base="stc")
                modules_per_string = self.panel.modules_per_string(wb_id)
                voc_string_nominal = voc_per_module * modules_per_string

                # Iterasi sibling strings untuk hitung peer I_z + voc_ratio.
                i_cols = [
                    f"PV{n} input current(A)" for n in range(1, pv_max + 1)
                    if f"PV{n} input current(A)" in group_clean.columns
                ]
                if len(i_cols) < 3:
                    spec_flags: List[Dict] = []
                    worst_voc_ratio = float("nan")
                    worst_pv_string: Optional[int] = None
                    worst_i_z = float("nan")
                else:
                    I_matrix = group_clean[i_cols].apply(pd.to_numeric, errors="coerce")
                    spec_flags = []
                    worst_voc_ratio = float("nan")
                    worst_pv_string = None
                    worst_i_z = float("nan")

                    for i_col in i_cols:
                        pv_n = int(i_col.split(" ")[0][2:])
                        v_col = f"PV{pv_n} input voltage(V)"
                        if v_col not in group_clean.columns:
                            continue

                        I_string = I_matrix[i_col]
                        V_string = pd.to_numeric(group_clean[v_col], errors="coerce")

                        voc_actual = estimate_voc_at_low_current(V_string, I_string)
                        if np.isnan(voc_actual) or voc_string_nominal <= 0:
                            continue
                        voc_ratio = voc_actual / voc_string_nominal

                        # Peer I comparison: I string vs median of siblings di daylight.
                        peer_cols = [c for c in i_cols if c != i_col]
                        peer_median_ts = I_matrix[peer_cols].median(axis=1)
                        I_string_daylight = I_string[daylight_mask.values].dropna()
                        peer_daylight = peer_median_ts[daylight_mask.values].dropna()
                        if len(I_string_daylight) < 3 or len(peer_daylight) < 3:
                            continue
                        peer_std = float(peer_daylight.std()) if peer_daylight.std() > 0 else 0.01
                        i_z = (float(I_string_daylight.median()) - float(peer_daylight.median())) / peer_std

                        # Track string dengan voc_ratio terendah (worst).
                        if pd.isna(worst_voc_ratio) or voc_ratio < worst_voc_ratio:
                            worst_voc_ratio = voc_ratio
                            worst_pv_string = pv_n
                            worst_i_z = i_z

                        if voc_ratio < voc_ratio_threshold and i_z > i_high_z_threshold:
                            spec_flags.append({"pv_n": pv_n, "voc_ratio": voc_ratio, "i_z": i_z})

                flagged_spec = len(spec_flags) > 0
                triggered: List[str] = []
                if flagged_absolute:
                    triggered.append("absolute")
                if flagged_adaptive:
                    triggered.append("adaptive")
                if flagged_spec:
                    triggered.append("spec_4.2.3")

                # Wave 8: emit per-PV StringStatus rows untuk inverter ini (regardless triggered).
                status_str = "ground_fault" if triggered else "NORMAL"
                trig_str = "+".join(triggered) if triggered else ""
                conf_pct = self._confidence_for(triggered) if triggered else 0.0
                for pv_n in range(1, pv_max + 1):
                    # Wave 11 hotfix #10: skip empty PV slots dari analisis.
                    if pv_n in _inv_empty_set_gf:
                        continue
                    v_col = f"PV{pv_n} input voltage(V)"
                    if v_col not in group_clean.columns:
                        continue
                    pv_label = f"PV{pv_n}"
                    string_status_rows.append({
                        "poa_source": poa_source,
                        "inverter_id": str(inverter_id),
                        "wb_id": wb_id,
                        "pv_string": pv_label,
                        "status": status_str,
                        "is_worst_string": (worst_pv_string == pv_n) if worst_pv_string is not None else False,
                        "v_gnd_median_daylight": v_gnd_median,
                        "v_gnd_max_abs_daylight": v_gnd_max_abs,
                        "adaptive_z": v_gnd_adaptive_z,
                        "triggered_by": trig_str,
                        "confidence_pct": conf_pct,
                        "cell_temp_c": tcell_mean,
                        "daylight_samples": int(daylight_mask.sum()),
                    })

                if not triggered:
                    continue

                confidence = self._confidence_for(triggered)
                severity = Severity.CRITICAL if confidence >= 80 else Severity.HIGH
                triggered_by = "+".join(triggered)

                # Wave 11 hotfix #9: use LAST daylight timestamp (bukan latest_ts).
                _gf_daylight_idx = daylight_mask[daylight_mask].index
                _gf_ts = _gf_daylight_idx[-1] if len(_gf_daylight_idx) > 0 else latest_ts
                findings.append(M2Finding(
                    timestamp=_gf_ts.to_pydatetime() if hasattr(_gf_ts, "to_pydatetime") else _gf_ts,
                    inverter_id=str(inverter_id),
                    pv_string=(f"PV{worst_pv_string}" if worst_pv_string is not None else None),
                    sub_module=self.name,
                    severity=severity,
                    value=float(abs(v_gnd_median)),
                    threshold=float(v_abs_threshold),
                    message=(
                        f"Ground-fault suspect ({triggered_by}): "
                        f"|V_gnd_median|={abs(v_gnd_median):.1f}V worst voc_ratio={worst_voc_ratio:.3f} "
                        f"(src={poa_source})"
                    ),
                    fault_type="ground_fault",
                    confidence=confidence,
                    evidence={
                        "poa_source": poa_source,
                        "triggered_by": triggered_by,
                        "v_pv_ground_median": v_gnd_median,
                        "v_pv_ground_max_abs": v_gnd_max_abs,
                        "deviation_from_fleet_median": v_gnd_median - v_gnd_fleet_median,
                        "adaptive_z": v_gnd_adaptive_z,
                        "v_abs_threshold": v_abs_threshold,
                        "adaptive_z_threshold": adaptive_z_threshold,
                        "n_absolute_flags": 1 if flagged_absolute else 0,
                        "n_adaptive_flags": 1 if flagged_adaptive else 0,
                        "n_spec_flags": len(spec_flags),
                        "spec_flag_details": spec_flags,
                        "worst_pv_string": worst_pv_string,
                        "worst_voc_ratio": (float(worst_voc_ratio) if not pd.isna(worst_voc_ratio) else None),
                        "worst_i_z": (float(worst_i_z) if not pd.isna(worst_i_z) else None),
                        "voc_string_nominal_v": voc_string_nominal,
                        "cell_temp_c": tcell_mean,
                        "modules_per_string": modules_per_string,
                        "daylight_samples": int(daylight_mask.sum()),
                        "poa_floor_wm2": poa_floor,
                        "hour_cutoff_end": hour_cutoff_end,
                        "respect_inverter_shutdown": respect_inverter_shutdown,
                        "shutdown_col_used": shutdown_col,
                        "filter_mode": filter_mode,
                        "filter_mode_effective": effective_mode,
                        "solar_elevation_min_deg": solar_elev_min_deg,
                    },
                ))

                artifact_rows.append({
                    "poa_source": poa_source,
                    "inverter_id": str(inverter_id),
                    "wb_id": wb_id,
                    "v_gnd_median_daylight": v_gnd_median,
                    "v_gnd_max_abs_daylight": v_gnd_max_abs,
                    "adaptive_z": v_gnd_adaptive_z,
                    "flagged_absolute": flagged_absolute,
                    "flagged_adaptive": flagged_adaptive,
                    "flagged_spec": flagged_spec,
                    "worst_pv_string": worst_pv_string,
                    "worst_voc_ratio": (float(worst_voc_ratio) if not pd.isna(worst_voc_ratio) else None),
                    "worst_i_z": (float(worst_i_z) if not pd.isna(worst_i_z) else None),
                    "triggered_by": triggered_by,
                    "confidence_pct": confidence,
                    "cell_temp_c": tcell_mean,
                    "daylight_samples": int(daylight_mask.sum()),
                })

        # Wave 11 hotfix #8 (revised by #9): top-up FULL PV inventory match
        # M2e_hybrid_AllStrings shape. Empty PVs emit dengan status="EMPTY".
        if string_status_rows:
            from pv_pipeline.core import load_empty_pv_map as _load_emap_gf
            empty_pv_map_gf = _load_emap_gf(config)
            existing_pairs_gf = {
                (r["inverter_id"], r["pv_string"]) for r in string_status_rows
            }
            for inv_id in combined_df["Inverter_ID"].dropna().unique():
                inv_str_gf = str(inv_id)
                empties_gf = set(empty_pv_map_gf.get(inv_str_gf.upper(), []))
                wb_id_gf = _wb_from_inverter_id(inv_id)
                for pv_n in range(1, pv_max + 1):
                    pv_label = f"PV{pv_n}"
                    if (inv_str_gf, pv_label) in existing_pairs_gf:
                        continue
                    if pv_n in empties_gf:
                        string_status_rows.append({
                            "poa_source": "n/a",
                            "inverter_id": inv_str_gf,
                            "wb_id": wb_id_gf,
                            "pv_string": pv_label,
                            "status": "EMPTY",
                            "is_worst_string": False,
                            "note": "empty_pv_slot_per_strings_yaml",
                        })
                    else:
                        string_status_rows.append({
                            "poa_source": "n/a",
                            "inverter_id": inv_str_gf,
                            "wb_id": wb_id_gf,
                            "pv_string": pv_label,
                            "status": "NORMAL",
                            "is_worst_string": False,
                            "note": "topup_no_voltage_or_current_data_for_this_pv",
                        })

        # Wave 11 hotfix #3: fan-out NORMAL placeholder kalau string_status_rows
        # kosong (sama pattern dengan peer_zscore + open_circuit). InverterEvents
        # tetap conditional (per-inverter, flagged-only — Wave 8 design).
        # Wave 11 hotfix #5: respect EMPTY_PV_MAP per inverter dari strings.yaml.
        if not string_status_rows:
            from pv_pipeline.core import load_empty_pv_map
            empty_pv_map = load_empty_pv_map(config)
            for inv_id in combined_df["Inverter_ID"].dropna().unique():
                inv_id_str = str(inv_id)
                empties = set(empty_pv_map.get(inv_id_str.upper(), []))
                wb_id_fb = _wb_from_inverter_id(inv_id)
                for pv_n in range(1, pv_max + 1):
                    if pv_n in empties:
                        continue  # PV slot kosong by design (strings.yaml)
                    v_col = f"PV{pv_n} input voltage(V)"
                    if v_col not in combined_df.columns:
                        continue
                    string_status_rows.append({
                        "poa_source": "n/a",
                        "inverter_id": inv_id_str,
                        "wb_id": wb_id_fb,
                        "pv_string": f"PV{pv_n}",
                        "status": "NORMAL",
                        "is_worst_string": False,
                        "note": "no_analysis_performed_check_data_quality_or_poa_gate",
                    })

        if artifact_rows:
            # Wave 8: rename ke InverterEvents (per-inverter, hanya flagged).
            self.artifacts["InverterEvents"] = pd.DataFrame(artifact_rows)
        if string_status_rows:
            # Wave 8: per-PV-string status sheet (NORMAL | ground_fault).
            self.artifacts["StringStatus"] = pd.DataFrame(string_status_rows)
        return findings


if __name__ == "__main__":
    # Synthetic smoke: 3 inverters, INV02 elevated V_to_ground (absolute trigger).
    import sys
    sys.path.insert(0, ".claude/worktrees/modest-shockley-9c31f4")

    rng = np.random.default_rng(13)
    n = 145
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 18:00", freq="5min")[:n]
    hours = np.linspace(0, 12, n)
    sun = np.sin(np.pi * hours / 12) ** 2
    I_base = 13.0 * sun
    V_base = 1200.0 + 200.0 * np.exp(-3 * sun)

    rows = []
    for inv_id in ["WB05-INV01", "WB05-INV02", "WB05-INV03"]:
        for ts_i, ts in enumerate(t):
            row = {"Inverter_ID": inv_id, "Start Time": ts}
            for pv_n in range(1, 11):
                row[f"PV{pv_n} input voltage(V)"] = V_base[ts_i] + rng.normal(0, 5)
                row[f"PV{pv_n} input current(A)"] = I_base[ts_i] + rng.normal(0, 0.1)
            if inv_id == "WB05-INV02":
                # Elevated V_to_ground (absolute trigger) - simulasi ground leak.
                row["Voltage between PV– and the ground(V)"] = -80.0 + rng.normal(0, 3)
            else:
                row["Voltage between PV– and the ground(V)"] = rng.normal(0, 5)  # normal ~0V
            rows.append(row)
    df = pd.DataFrame(rows)

    class MockPOA:
        def get_poa(self, timestamps, wb_id, source="auto"):
            hrs = (pd.DatetimeIndex(timestamps).hour - 6) + (pd.DatetimeIndex(timestamps).minute / 60)
            poa = np.where((hrs >= 0) & (hrs <= 12), 1000.0 * np.sin(np.pi * hrs / 12) ** 2, 0.0)
            return pd.Series(poa, index=pd.DatetimeIndex(timestamps))

    class MockPanel:
        def voc_at_cell_temp(self, t, base="stc"):
            return 55.72
        def modules_per_string(self, wb_id):
            return 26

    class MockTcell:
        def get_tcell(self, timestamps, wb_id):
            return pd.Series([30.0] * len(timestamps), index=pd.DatetimeIndex(timestamps))

    cfg = {
        "m2b_ground_fault": {"poa_threshold_wm2": 200.0, "v_to_ground_abs_threshold_v": 50.0,
                              "adaptive_z_threshold": 3.0, "voc_ratio_threshold": 0.85,
                              "i_high_z_threshold": 2.0, "pv_max": 10, "min_daylight_samples": 5},
        "poa": {"emit_all_sources": False, "default_source": "auto",
                "site_geometry_path": "config/site_geometry.yaml"},
        "panel": {"spec_path": "config/panel_spec.yaml"},
    }
    sm = M2bGroundFault(poa=MockPOA(), panel=MockPanel(), cell_temp=MockTcell())
    findings = sm.run(df, cfg)
    print(f"[ground_fault] findings count: {len(findings)}")
    for f in findings:
        ev = f.evidence or {}
        print(f"  {f.inverter_id}: |V_gnd|={f.value:.1f}V "
              f"trig={ev.get('triggered_by')} sev={f.severity.value} conf={f.confidence:.0f}%")
    art = sm.artifacts.get("GroundFaultEvents")
    if art is not None:
        print(f"\n[ground_fault] GroundFaultEvents artifact ({len(art)} rows):")
        print(art[["inverter_id", "v_gnd_median_daylight", "v_gnd_max_abs_daylight",
                   "adaptive_z", "triggered_by", "confidence_pct"]].round(2).to_string(index=False))
    assert any(f.inverter_id == "WB05-INV02" for f in findings), "INV02 should be flagged"
    print("\n[ground_fault] smoke OK (INV02 flagged sebagai ground_fault)")
