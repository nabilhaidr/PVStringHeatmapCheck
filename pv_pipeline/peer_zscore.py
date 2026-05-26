"""M2b Peer Z-score detector untuk PV string apparent resistance.

Spec 4.2.1 (Master Context):
    mask = POA > 300
    R_str = V_string[mask] / I_string[mask].clip(lower=0.1)
    z_score = (R_str - R_str.median()) / R_str.std()

Spec 4.2.3 High-R rule:
    Emit fault_type="high_R" jika:
        |z| > 2.5  AND  voc_ratio > 0.95
    Confidence: min(90%, |z|/4 * 100%)

Peer scope: sibling PV strings di SAMA inverter (PV1..PV28 dari satu Inverter_ID).
Tidak cross-inverter karena: orientasi panel, MPPT controller, DC bus berbeda.

Multi-source comparison: detector loop di setiap source dari
``config.poa.sources_to_emit`` (default: 5 source), emit finding ber-tag ``poa_source``.

Dual-stat: hitung z_mean (vs mean) dan z_median (vs median) sebagai cross-check.
"""
from __future__ import annotations

import warnings
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from pv_pipeline.core import M2Finding, Severity, SubModule
from pv_pipeline.voc_estimator import estimate_voc_at_low_current


# Default thresholds (override via config["m2b"]).
DEFAULT_POA_THRESHOLD_WM2: float = 300.0
DEFAULT_POA_FLOOR_WM2: float = 50.0       # 2026-05-16 sunset fix
DEFAULT_HOUR_CUTOFF_END: float = 18.0     # 2026-05-16 sunset fix
DEFAULT_RESPECT_INVERTER_SHUTDOWN: bool = True  # 2026-05-16 sunset fix
DEFAULT_FILTER_MODE: str = "solar_elevation"    # Fase 2: "solar_elevation" | "hour_cutoff"
DEFAULT_SOLAR_ELEV_MIN_DEG: float = 5.0          # Fase 2: sun apparent_elevation threshold
DEFAULT_Z_THRESHOLD: float = 2.5
DEFAULT_VOC_RATIO_THRESHOLD: float = 0.95
DEFAULT_STAT_METHOD: str = "median"   # "mean" | "median" | "both"

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


def _wb_from_inverter_id(inverter_id: str) -> str:
    """``WB05-INV12`` -> ``"WB05"``. Fallback ke string asli kalau pattern aneh."""
    if not inverter_id:
        return ""
    parts = str(inverter_id).split("-")
    return parts[0].upper() if parts else str(inverter_id).upper()


class M2bPeerZScore(SubModule):
    """Detector R-string Z-score peer comparison (sibling strings same inverter).

    Dependency injection via constructor (preferred di notebook):
        prov = POAProvider.from_yaml(...)
        panel = PanelSpec.from_yaml(...)
        ct = CellTempProvider.from_geometry_yaml(...)
        sm = M2bPeerZScore(poa=prov, panel=panel, cell_temp=ct)

    Lazy fallback di run() saat tidak di-inject: construct dari config paths.
    """

    name: str = "M2b_peer_zscore"

    def __init__(
        self,
        poa=None,
        panel=None,
        cell_temp=None,
    ):
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

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        cfg = config.get("m2b", {}) or {}
        poa_threshold = float(cfg.get("poa_threshold_wm2", DEFAULT_POA_THRESHOLD_WM2))
        poa_floor = float(cfg.get("poa_floor_wm2", DEFAULT_POA_FLOOR_WM2))
        hour_cutoff_end = float(cfg.get("hour_cutoff_end", DEFAULT_HOUR_CUTOFF_END))
        respect_inverter_shutdown = bool(cfg.get(
            "respect_inverter_shutdown", DEFAULT_RESPECT_INVERTER_SHUTDOWN
        ))
        filter_mode = str(cfg.get("filter_mode", DEFAULT_FILTER_MODE)).lower()
        solar_elev_min_deg = float(cfg.get("solar_elevation_min_deg", DEFAULT_SOLAR_ELEV_MIN_DEG))
        z_threshold = float(cfg.get("z_threshold", DEFAULT_Z_THRESHOLD))
        voc_ratio_threshold = float(cfg.get("voc_ratio_threshold", DEFAULT_VOC_RATIO_THRESHOLD))
        stat_method = str(cfg.get("stat_method", DEFAULT_STAT_METHOD)).lower()
        pv_max = int(cfg.get("pv_max", 28))
        min_daylight_samples = int(cfg.get("min_daylight_samples", 10))
        min_peer_strings = int(cfg.get("min_peer_strings", 3))

        shutdown_col = _find_shutdown_col(combined_df) if respect_inverter_shutdown else None

        if "Inverter_ID" not in combined_df.columns or "Start Time" not in combined_df.columns:
            warnings.warn(
                "[M2bPeerZScore] missing 'Inverter_ID' or 'Start Time'; skipping.",
                stacklevel=2,
            )
            return []

        # Wave 9: optional Hampel preprocessing (A/B feature flag).
        prep_cfg = config.get("preprocessing", {}) or {}
        prep_enabled = bool(prep_cfg.get("enabled", False))
        if prep_enabled:
            from pv_pipeline.preprocessing import apply_hampel_to_pv_dataframe
            prep_window = int(prep_cfg.get("window", 15))
            prep_max_dev = float(prep_cfg.get("max_deviation", 3.0))
            combined_df, prep_audit = apply_hampel_to_pv_dataframe(
                combined_df, pv_max=pv_max,
                window=prep_window, max_deviation=prep_max_dev,
            )
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

        # Wave 11 hotfix #6: track WHY each iteration continues (for diagnostic
        # purposes). User sees these counts di GateFailureSummary artifact
        # sheet kalau fan-out kick in.
        gate_failures = {
            "g1_insufficient_daylight_samples": 0,
            "g2_poa_query_exception": 0,
            "g3_mask_poa_below_min_daylight": 0,
            "g4_shutdown_sentinel_skipped": 0,  # tidak fail, hanya info
            "g5_peer_strings_below_min": 0,
            "g6_rstr_std_too_small": 0,
            "total_iterations": 0,
        }

        # Wave 11 hotfix #10: load empty_pv_map ONCE supaya main loop skip
        # empty PV slots (false-positive prevention).
        from pv_pipeline.core import load_empty_pv_map as _load_emap_mainloop_pz
        _empty_map_main_pz = _load_emap_mainloop_pz(config)

        # Get latest timestamp untuk pakai sebagai Finding.timestamp.
        all_ts = pd.to_datetime(combined_df["Start Time"], errors="coerce")
        latest_ts = all_ts.max() if not all_ts.dropna().empty else datetime.utcnow()

        for poa_source in sources:
            for inverter_id, group in combined_df.groupby("Inverter_ID"):
                gate_failures["total_iterations"] += 1
                wb_id = _wb_from_inverter_id(inverter_id)
                _inv_empties_pz = _empty_map_main_pz.get(str(inverter_id).upper(), [])
                _inv_empty_set_pz = set(int(n) for n in _inv_empties_pz)
                timestamps = pd.to_datetime(group["Start Time"], errors="coerce")
                valid_idx = timestamps.notna()
                if valid_idx.sum() < min_daylight_samples:
                    gate_failures["g1_insufficient_daylight_samples"] += 1
                    continue
                ts_clean = pd.DatetimeIndex(timestamps[valid_idx].values)
                group_clean = group.loc[valid_idx].copy()
                group_clean.index = ts_clean

                # POA gate per spec 4.2.1 + sunset fix (2026-05-16):
                #   1. POA > poa_threshold (spec, default 300) AND POA > poa_floor (sanity)
                #   2. hour < hour_cutoff_end (sunset cutoff)
                #   3. Sebelum Inverter shutdown time (kalau column tersedia)
                try:
                    poa = self.poa.get_poa(ts_clean, wb_id, source=poa_source)
                except Exception as exc:
                    warnings.warn(
                        f"[M2bPeerZScore] POA query failed (wb={wb_id}, src={poa_source}): "
                        f"{exc.__class__.__name__}: {exc}",
                        stacklevel=2,
                    )
                    gate_failures["g2_poa_query_exception"] += 1
                    continue

                poa_aligned = poa.reindex(ts_clean).fillna(0.0)
                mask_poa_main = (poa_aligned > poa_threshold) & (poa_aligned > poa_floor)

                # Fase 2: solar_elevation filter (physical) atau hour_cutoff (heuristic).
                effective_mode = filter_mode
                if filter_mode == "solar_elevation":
                    try:
                        elev = self.poa.get_solar_elevation(ts_clean)
                        elev_aligned = elev.reindex(ts_clean)
                        if elev_aligned.notna().any():
                            # Wave 11 hotfix #7: defensive AND dengan hour_cutoff.
                            # Tanpa ini, di equator (IKN) elev masih > 5 deg sampai
                            # 18:15+ -> twilight false-positive CRITICAL.
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
                            f"[M2bPeerZScore] solar_elevation query failed (wb={wb_id}): "
                            f"{exc.__class__.__name__}: {exc}. Falling back ke hour_cutoff.",
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
                    # Wave 11 hotfix #5: drop sentinel datetimes (year<2000)
                    # yang artinya "never shutdown" -- tanpa filter ini,
                    # valid_shut.min() = 1970-01-01 -> mask_shutdown all False
                    # -> mask_poa.sum()=0 -> continue (artifact_rows tetap empty
                    # -> fan-out fallback kicks in).
                    if not valid_shut.empty:
                        valid_shut = valid_shut[valid_shut.dt.year >= 2000]
                    if not valid_shut.empty:
                        shutdown_ts = valid_shut.min()
                        proposed = pd.Series(ts_clean < shutdown_ts, index=ts_clean)
                        # Wave 11 hotfix #6: kalau shutdown_ts <= earliest ts
                        # (proposed mask drop ALL timestamps), itu sentinel
                        # (e.g., Huawei "0:00:00" parsed sebagai today midnight).
                        # Skip filter supaya tidak buang semua data legitimate.
                        if proposed.sum() > 0:
                            mask_shutdown = proposed
                        else:
                            gate_failures["g4_shutdown_sentinel_skipped"] += 1

                mask_poa = pd.Series(
                    mask_poa_main.values & mask_time.values & mask_shutdown.values,
                    index=ts_clean,
                )
                if mask_poa.sum() < min_daylight_samples:
                    gate_failures["g3_mask_poa_below_min_daylight"] += 1
                    continue

                # Mean cell temp untuk Voc_string_nominal calc.
                try:
                    tcell = self.cell_temp.get_tcell(ts_clean, wb_id)
                    tcell_daylight = tcell[mask_poa.values].dropna()
                    tcell_mean = float(tcell_daylight.mean()) if not tcell_daylight.empty else 25.0
                except Exception as exc:
                    warnings.warn(
                        f"[M2bPeerZScore] Tcell query failed (wb={wb_id}): {exc}",
                        stacklevel=2,
                    )
                    tcell_mean = 25.0

                voc_nominal_per_module = self.panel.voc_at_cell_temp(tcell_mean, base="stc")
                modules_per_string = self.panel.modules_per_string(wb_id)
                voc_string_nominal = voc_nominal_per_module * modules_per_string

                # Build per-string R_str (median across daylight timestamps).
                r_str_per_string: Dict[int, float] = {}
                voc_actual_per_string: Dict[int, float] = {}
                for pv_n in range(1, pv_max + 1):
                    # Wave 11 hotfix #10: skip empty PV slots dari analisis.
                    if pv_n in _inv_empty_set_pz:
                        continue
                    v_col = f"PV{pv_n} input voltage(V)"
                    i_col = f"PV{pv_n} input current(A)"
                    if v_col not in group_clean.columns or i_col not in group_clean.columns:
                        continue
                    V = pd.to_numeric(group_clean[v_col], errors="coerce")
                    I = pd.to_numeric(group_clean[i_col], errors="coerce")

                    # R_str(t) = V/I.clip(0.1) hanya di mask_poa
                    I_clip = I.clip(lower=0.1)
                    R_t = (V / I_clip).where(mask_poa.values)
                    R_valid = R_t.dropna()
                    if len(R_valid) < min_daylight_samples // 2:
                        continue
                    r_str_per_string[pv_n] = float(R_valid.median())

                    voc_actual_per_string[pv_n] = estimate_voc_at_low_current(V, I)

                if len(r_str_per_string) < min_peer_strings:
                    gate_failures["g5_peer_strings_below_min"] += 1
                    continue

                r_values = pd.Series(r_str_per_string)
                r_mean_fleet = float(r_values.mean())
                r_median_fleet = float(r_values.median())
                r_std_fleet = float(r_values.std())
                if not np.isfinite(r_std_fleet) or r_std_fleet < 1e-6:
                    gate_failures["g6_rstr_std_too_small"] += 1
                    continue

                # Emit per-string finding + artifact row.
                for pv_n, r_val in r_str_per_string.items():
                    z_mean = (r_val - r_mean_fleet) / r_std_fleet
                    z_median = (r_val - r_median_fleet) / r_std_fleet
                    # Pilih z_primary per stat_method (untuk severity/confidence).
                    if stat_method == "mean":
                        z_primary = z_mean
                    elif stat_method == "median":
                        z_primary = z_median
                    else:  # "both" -> ambil yang absolutely lebih besar
                        z_primary = z_mean if abs(z_mean) >= abs(z_median) else z_median

                    flagged_by_mean = abs(z_mean) > z_threshold
                    flagged_by_median = abs(z_median) > z_threshold
                    voc_actual = voc_actual_per_string.get(pv_n, float("nan"))
                    voc_ratio = (
                        voc_actual / voc_string_nominal
                        if (not np.isnan(voc_actual)) and voc_string_nominal > 0
                        else float("nan")
                    )
                    voc_ok = (not np.isnan(voc_ratio)) and voc_ratio > voc_ratio_threshold

                    flagged = flagged_by_mean or flagged_by_median
                    should_emit_per_spec = flagged and voc_ok

                    if should_emit_per_spec:
                        confidence = min(90.0, abs(z_primary) / 4.0 * 100.0)
                        severity = (
                            Severity.HIGH if abs(z_primary) > 3.5
                            else Severity.MEDIUM
                        )
                        # Wave 11 hotfix #9: use LAST daylight timestamp (bukan
                        # latest_ts). Z-score statistical agregat dari semua
                        # daylight samples; gunakan last daylight sample sebagai
                        # representative timestamp -- lebih meaningful drpd 18:15
                        # (data's last row, biasanya sunset).
                        _pz_daylight_idx = mask_poa[mask_poa].index
                        _pz_ts = _pz_daylight_idx[-1] if len(_pz_daylight_idx) > 0 else latest_ts
                        findings.append(M2Finding(
                            timestamp=_pz_ts.to_pydatetime() if hasattr(_pz_ts, "to_pydatetime") else _pz_ts,
                            inverter_id=str(inverter_id),
                            pv_string=f"PV{pv_n}",
                            sub_module=self.name,
                            severity=severity,
                            value=float(z_primary),
                            threshold=float(z_threshold),
                            message=(
                                f"High-R suspect PV{pv_n}: |z|={abs(z_primary):.2f} "
                                f"voc_ratio={voc_ratio:.3f} (src={poa_source})"
                            ),
                            fault_type="high_R",
                            confidence=float(confidence),
                            evidence={
                                "poa_source": poa_source,
                                "rstr": float(r_val),
                                "rstr_fleet_mean": r_mean_fleet,
                                "rstr_fleet_median": r_median_fleet,
                                "rstr_fleet_std": r_std_fleet,
                                "z_mean": float(z_mean),
                                "z_median": float(z_median),
                                "stat_method": stat_method,
                                "flagged_by_mean": bool(flagged_by_mean),
                                "flagged_by_median": bool(flagged_by_median),
                                "voc_actual_v": float(voc_actual) if not np.isnan(voc_actual) else None,
                                "voc_string_nominal_v": float(voc_string_nominal),
                                "voc_ratio": float(voc_ratio) if not np.isnan(voc_ratio) else None,
                                "cell_temp_c": float(tcell_mean),
                                "modules_per_string": int(modules_per_string),
                                "daylight_samples": int(mask_poa.sum()),
                                "poa_threshold_wm2": poa_threshold,
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
                        "pv_string": f"PV{pv_n}",
                        "status": "high_R" if should_emit_per_spec else "NORMAL",
                        "rstr": r_val,
                        "rstr_fleet_median": r_median_fleet,
                        "rstr_fleet_std": r_std_fleet,
                        "z_mean": z_mean,
                        "z_median": z_median,
                        "z_primary": z_primary,
                        "flagged_by_mean": flagged_by_mean,
                        "flagged_by_median": flagged_by_median,
                        "voc_actual_v": voc_actual if not np.isnan(voc_actual) else None,
                        "voc_string_nominal_v": voc_string_nominal,
                        "voc_ratio": voc_ratio if not np.isnan(voc_ratio) else None,
                        "voc_ok": voc_ok,
                        "emitted_finding": should_emit_per_spec,
                        "cell_temp_c": tcell_mean,
                        "daylight_samples": int(mask_poa.sum()),
                    })

        # Wave 11 hotfix #6: emit GateFailureSummary diagnostic kalau fan-out
        # akan kick in (artifact_rows empty). User bisa lihat di output xlsx
        # sheet M2b_peer_zscore_GateFailureSummary untuk tahu gate mana yang
        # filter semua iterasi.
        if not artifact_rows and gate_failures["total_iterations"] > 0:
            self.artifacts["GateFailureSummary"] = pd.DataFrame([gate_failures])

        # Wave 11 hotfix #8 (revised by #9): top-up FULL PV inventory PV1..pv_max
        # supaya StringStatus match M2e_hybrid_AllStrings shape (28 PVs uniform
        # per inverter). Empty PVs per strings.yaml emit dengan status="EMPTY",
        # non-empty PVs tanpa V/I data emit dengan status="NORMAL".
        if artifact_rows:
            from pv_pipeline.core import load_empty_pv_map as _load_emap_pz
            empty_pv_map_pz = _load_emap_pz(config)
            existing_pairs_pz = {
                (r["inverter_id"], r["pv_string"]) for r in artifact_rows
            }
            for inv_id in combined_df["Inverter_ID"].dropna().unique():
                inv_str_pz = str(inv_id)
                empties_pz = set(empty_pv_map_pz.get(inv_str_pz.upper(), []))
                wb_id_pz = _wb_from_inverter_id(inv_id)
                for pv_n in range(1, pv_max + 1):
                    pv_label = f"PV{pv_n}"
                    if (inv_str_pz, pv_label) in existing_pairs_pz:
                        continue
                    if pv_n in empties_pz:
                        # Wave 11 hotfix #9: emit EMPTY status untuk slot kosong
                        # per strings.yaml (match M2e_hybrid_AllStrings shape).
                        artifact_rows.append({
                            "poa_source": "n/a",
                            "inverter_id": inv_str_pz,
                            "wb_id": wb_id_pz,
                            "pv_string": pv_label,
                            "status": "EMPTY",
                            "note": "empty_pv_slot_per_strings_yaml",
                            "emitted_finding": False,
                        })
                    else:
                        artifact_rows.append({
                            "poa_source": "n/a",
                            "inverter_id": inv_str_pz,
                            "wb_id": wb_id_pz,
                            "pv_string": pv_label,
                            "status": "NORMAL",
                            "note": "topup_no_voltage_or_current_data_for_this_pv",
                            "emitted_finding": False,
                        })

        # Wave 11 hotfix #3: ensure StringStatus sheet ALWAYS emit dengan semua
        # (inverter, PV) pairs, supaya user lihat lengkap di output xlsx.
        # Kalau main loop kosong (semua inverter gagal POA gate / data quality),
        # fan-out NORMAL placeholder rows dengan note diagnostic supaya sheet
        # tidak hilang dari output.
        # Wave 11 hotfix #5: respect EMPTY_PV_MAP per inverter dari strings.yaml.
        if not artifact_rows:
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
                    artifact_rows.append({
                        "poa_source": "n/a",
                        "inverter_id": inv_id_str,
                        "wb_id": wb_id_fb,
                        "pv_string": f"PV{pv_n}",
                        "status": "NORMAL",
                        "note": "no_analysis_performed_check_data_quality_or_poa_gate",
                        "emitted_finding": False,
                    })

        if artifact_rows:
            # Wave 8: rename ke StringStatus + tambah status column (NORMAL | high_R).
            self.artifacts["StringStatus"] = pd.DataFrame(artifact_rows)
        return findings


if __name__ == "__main__":
    # Synthetic smoke test: 1 inverter dengan 10 PV strings (realistic Jinko 26-module string),
    # 1 string (PV3) punya R abnormal tinggi (I drops, V tetap -> high resistance signature).
    import sys
    sys.path.insert(0, ".claude/worktrees/modest-shockley-9c31f4")

    rng = np.random.default_rng(42)
    n = 145
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 18:00", freq="5min")[:n]
    hours = np.linspace(0, 12, n)
    sun = np.sin(np.pi * hours / 12) ** 2
    # Realistic 26-module Jinko string: Voc ~1400V, Vmp ~1200V, Imp ~13A.
    I_base = 13.0 * sun
    V_base = 1200.0 + 200.0 * np.exp(-3 * sun)  # ~1400V at sunrise/sunset, ~1200V at noon

    rows = []
    for ts_i, ts in enumerate(t):
        row = {"Inverter_ID": "WB05-INV05", "Start Time": ts}
        for pv_n in range(1, 11):
            if pv_n == 3:
                # High-R signature: V slightly elevated, I significantly dropped.
                row[f"PV{pv_n} input voltage(V)"] = V_base[ts_i] * 1.03 + rng.normal(0, 2)
                row[f"PV{pv_n} input current(A)"] = I_base[ts_i] * 0.40 + rng.normal(0, 0.05)
            else:
                row[f"PV{pv_n} input voltage(V)"] = V_base[ts_i] + rng.normal(0, 2)
                row[f"PV{pv_n} input current(A)"] = I_base[ts_i] + rng.normal(0, 0.05)
        rows.append(row)
    df = pd.DataFrame(rows)

    # Mock providers (tidak butuh real xlsx).
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
        "m2b": {"poa_threshold_wm2": 300.0, "z_threshold": 2.5, "voc_ratio_threshold": 0.95,
                "stat_method": "median", "pv_max": 10, "min_peer_strings": 3,
                "min_daylight_samples": 10},
        "poa": {"emit_all_sources": False, "default_source": "auto", "site_geometry_path": "config/site_geometry.yaml"},
        "panel": {"spec_path": "config/panel_spec.yaml"},
    }
    sm = M2bPeerZScore(poa=MockPOA(), panel=MockPanel(), cell_temp=MockTcell())
    findings = sm.run(df, cfg)
    print(f"[peer_zscore] findings count: {len(findings)}")
    for f in findings:
        print(f"  {f.inverter_id}/{f.pv_string}: |z|={abs(f.value):.2f} sev={f.severity.value} conf={f.confidence:.0f}%")
    art = sm.artifacts.get("StatComparison")
    if art is not None:
        print(f"\n[peer_zscore] StatComparison artifact ({len(art)} rows):")
        print(art[["pv_string", "rstr", "z_median", "voc_ratio", "voc_ok", "emitted_finding"]].round(2).to_string(index=False))
    assert any(f.pv_string == "PV3" for f in findings), "PV3 should be flagged (R abnormal high)"
    print("\n[peer_zscore] smoke OK (PV3 flagged sebagai high_R)")
