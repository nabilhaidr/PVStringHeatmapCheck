"""M2b MPPT-partner ratio detector untuk PV string.

Desain (2026-06-12, disepakati user): peer scope = sibling string se-MPPT
(``mppt_map`` di config/strings.yaml), sinyal rasio arus -- BUKAN z-score.
Z-score tidak feasible dalam grup MPPT kecil: |z| maksimum = (N-1)/sqrt(N)
-> N=2: 0.71, N=4: 1.5, N=5: 1.79, semuanya di bawah z_threshold 2.5.

    ratio(t)  = I_string(t) / median(I_partner_se-MPPT(t))
    qualifying: ratio < ratio_threshold (default 0.85) saat daylight gate
                (POA > 300 W/m^2, floor 50, solar elevation > 5 deg)
    debounce  : >= 20 langkah 5-menit konsekutif (~100 menit)
    severity  : ratio_event_median < 0.20 CRITICAL | < 0.50 HIGH | else MEDIUM
    confidence: min(90, max(50, (1 - ratio_event_median) * 100))

String se-MPPT share voltage operating point yang sama, jadi perbandingan
arus partner adalah perbandingan paling apple-to-apple. min_partner_strings=1
supaya grup 2-string (WB01-02, SUN2000-215KTL-H0) tetap teranalisis.

Komplemen (bukan pengganti) detector inverter-wide:
- M2bOpenCircuit tetap inverter-wide (I_q95): menangkap MPPT-level fault
  (semua string satu MPPT mati bersamaan) yang ratio-to-partner lewatkan
  sebagian (ratio 0/0 -> 0 tetap flag, tapi degradasi SIMETRIS partial
  satu MPPT tidak terdeteksi di sini karena ratio ~ 1).
- M2bPeerZScore tetap inverter-wide untuk sinyal R_str + voc_ratio.

PV string yang tidak terdaftar di mppt_map atau WB tanpa mapping di-skip
(tanpa fallback inverter-wide -- itu wilayah detector lain).
"""
from __future__ import annotations

import warnings
from datetime import datetime
from typing import Dict, List

import numpy as np
import pandas as pd

from pv_pipeline.core import M2Finding, Severity, SubModule
from pv_pipeline.m2f.deficit import build_deficit_frame
from pv_pipeline.open_circuit import (
    _debounced_qualifying_mask,
    _find_shutdown_col,
    _wb_from_inverter_id,
    count_debounced_events,
)


DEFAULT_POA_THRESHOLD_WM2: float = 300.0   # moderate sun: arus stabil & proporsional
DEFAULT_POA_FLOOR_WM2: float = 50.0        # hard floor sunset/twilight (2026-05-16 fix)
DEFAULT_HOUR_CUTOFF_END: float = 18.0      # dipakai saat filter_mode="hour_cutoff"
DEFAULT_RESPECT_INVERTER_SHUTDOWN: bool = True
DEFAULT_FILTER_MODE: str = "solar_elevation"
DEFAULT_SOLAR_ELEV_MIN_DEG: float = 5.0
DEFAULT_RATIO_THRESHOLD: float = 0.85      # I_string < 85% median partner -> qualifying
DEFAULT_RATIO_HIGH: float = 0.50           # ratio_event_median < 0.50 -> HIGH
DEFAULT_RATIO_CRITICAL: float = 0.20       # ratio_event_median < 0.20 -> CRITICAL
DEFAULT_DEBOUNCE_STEPS: int = 20           # ~100 menit @ 5-min sampling
DEFAULT_MIN_PARTNER_STRINGS: int = 1       # grup 2-string (WB01-02) tetap dianalisis
DEFAULT_MPPT_MAP_PATH: str = "config/strings.yaml"


class M2bMpptRatio(SubModule):
    """Detector underperform string vs partner se-MPPT (current ratio)."""

    name: str = "M2b_mppt_ratio"

    def __init__(self, poa=None):
        super().__init__()
        self.poa = poa
        # m2f: deret waktu defisit per POA source/string, TIDAK masuk
        # self.artifacts -- itu channel Excel (M2Engine.write_xlsx_multi)
        # tanpa try/except, dan volume defisit (5 source x ribuan string x
        # ratusan timestamp) jauh melampaui limit baris sheet.
        self.deficit_frames: List[pd.DataFrame] = []

    def _ensure_poa(self, config: dict) -> None:
        if self.poa is None:
            from pv_pipeline.poa.provider import POAProvider
            geom_path = config.get("poa", {}).get("site_geometry_path", "config/site_geometry.yaml")
            self.poa = POAProvider.from_yaml(geom_path)

    def _resolve_sources(self, config: dict) -> List[str]:
        poa_cfg = config.get("poa", {})
        if poa_cfg.get("emit_all_sources", True):
            return list(poa_cfg.get("sources_to_emit", ["auto"]))
        return [poa_cfg.get("default_source", "auto")]

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        cfg = config.get("m2b_mppt_ratio", {}) or {}
        if not bool(cfg.get("enabled", True)):
            return []
        poa_threshold = float(cfg.get("poa_threshold_wm2", DEFAULT_POA_THRESHOLD_WM2))
        poa_floor = float(cfg.get("poa_floor_wm2", DEFAULT_POA_FLOOR_WM2))
        hour_cutoff_end = float(cfg.get("hour_cutoff_end", DEFAULT_HOUR_CUTOFF_END))
        respect_inverter_shutdown = bool(cfg.get(
            "respect_inverter_shutdown", DEFAULT_RESPECT_INVERTER_SHUTDOWN
        ))
        filter_mode = str(cfg.get("filter_mode", DEFAULT_FILTER_MODE)).lower()
        solar_elev_min_deg = float(cfg.get("solar_elevation_min_deg", DEFAULT_SOLAR_ELEV_MIN_DEG))
        ratio_threshold = float(cfg.get("ratio_threshold", DEFAULT_RATIO_THRESHOLD))
        ratio_high = float(cfg.get("ratio_high", DEFAULT_RATIO_HIGH))
        ratio_critical = float(cfg.get("ratio_critical", DEFAULT_RATIO_CRITICAL))
        debounce_steps = int(cfg.get("debounce_consecutive_steps", DEFAULT_DEBOUNCE_STEPS))
        pv_max = int(cfg.get("pv_max", 28))
        min_daylight_samples = int(cfg.get("min_daylight_samples", 5))
        min_partner_strings = int(cfg.get("min_partner_strings", DEFAULT_MIN_PARTNER_STRINGS))
        mppt_map_path = str(cfg.get("mppt_map_path", DEFAULT_MPPT_MAP_PATH))

        if "Inverter_ID" not in combined_df.columns or "Start Time" not in combined_df.columns:
            warnings.warn(
                "[M2bMpptRatio] missing 'Inverter_ID' or 'Start Time'; skipping.",
                stacklevel=2,
            )
            return []

        # Ground truth grouping sibling per MPPT (user-confirmed 2026-06-11).
        try:
            from pv_pipeline.string_config import get_mppt_groups
            mppt_groups = get_mppt_groups(mppt_map_path, pv_max_allowed=pv_max)
        except Exception as exc:
            warnings.warn(
                f"[M2bMpptRatio] mppt_map load failed ({mppt_map_path}): {exc}; "
                "detector skipped.",
                stacklevel=2,
            )
            return []
        if not mppt_groups:
            warnings.warn("[M2bMpptRatio] mppt_map kosong; detector skipped.", stacklevel=2)
            return []

        shutdown_col = _find_shutdown_col(combined_df) if respect_inverter_shutdown else None

        # Wave 9 pattern: optional Hampel preprocessing (A/B feature flag).
        prep_cfg = config.get("preprocessing", {}) or {}
        if bool(prep_cfg.get("enabled", False)):
            from pv_pipeline.preprocessing import apply_hampel_to_pv_dataframe
            combined_df, prep_audit = apply_hampel_to_pv_dataframe(
                combined_df, pv_max=pv_max,
                window=int(prep_cfg.get("window", 15)),
                max_deviation=float(prep_cfg.get("max_deviation", 3.0)),
            )
            if prep_audit:
                self.artifacts["PreprocessingAudit"] = pd.DataFrame(prep_audit)

        self._ensure_poa(config)
        sources = self._resolve_sources(config)

        # Wave 11 hotfix #11 pattern: normalize Title Case I cols (PV15-28).
        _rename_map = {}
        for _col in combined_df.columns:
            if "Input Current" in _col:
                _rename_map[_col] = _col.replace("Input Current", "input current")
        if _rename_map:
            combined_df = combined_df.rename(columns=_rename_map)

        from pv_pipeline.core import load_empty_pv_map as _load_emap
        empty_map = _load_emap(config)

        all_ts = pd.to_datetime(combined_df["Start Time"], errors="coerce")
        latest_ts = all_ts.max() if not all_ts.dropna().empty else datetime.utcnow()

        findings: List[M2Finding] = []
        artifact_rows: List[dict] = []
        deficit_rows: list = []

        for poa_source in sources:
            for inverter_id, group in combined_df.groupby("Inverter_ID"):
                wb_id = _wb_from_inverter_id(inverter_id)
                wb_groups: Dict[int, List[int]] = mppt_groups.get(wb_id, {})
                if not wb_groups:
                    continue
                empty_set = {
                    int(n) for n in empty_map.get(str(inverter_id).upper(), [])
                }
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
                        f"[M2bMpptRatio] POA query failed (wb={wb_id}, src={poa_source}): "
                        f"{exc}", stacklevel=2,
                    )
                    continue

                poa_aligned = poa.reindex(ts_clean).fillna(0.0)
                daylight_poa = (poa_aligned > poa_threshold) & (poa_aligned > poa_floor)

                effective_mode = filter_mode
                if filter_mode == "solar_elevation":
                    try:
                        elev = self.poa.get_solar_elevation(ts_clean)
                        elev_aligned = elev.reindex(ts_clean)
                        if elev_aligned.notna().any():
                            # Wave 11 hotfix #7 pattern: defensive AND hour_cutoff.
                            elev_mask = elev_aligned.fillna(-90.0).values > solar_elev_min_deg
                            hour_arr_se = ts_clean.hour + ts_clean.minute / 60.0
                            hour_mask_se = hour_arr_se < hour_cutoff_end
                            daylight_time = pd.Series(elev_mask & hour_mask_se, index=ts_clean)
                        else:
                            effective_mode = "hour_cutoff"
                            hour_arr = ts_clean.hour + ts_clean.minute / 60.0
                            daylight_time = pd.Series(hour_arr < hour_cutoff_end, index=ts_clean)
                    except Exception as exc:
                        warnings.warn(
                            f"[M2bMpptRatio] solar_elevation query failed (wb={wb_id}): {exc}. "
                            "Falling back ke hour_cutoff.",
                            stacklevel=2,
                        )
                        effective_mode = "hour_cutoff"
                        hour_arr = ts_clean.hour + ts_clean.minute / 60.0
                        daylight_time = pd.Series(hour_arr < hour_cutoff_end, index=ts_clean)
                else:
                    hour_arr = ts_clean.hour + ts_clean.minute / 60.0
                    daylight_time = pd.Series(hour_arr < hour_cutoff_end, index=ts_clean)

                daylight_shutdown = pd.Series(True, index=ts_clean)
                if respect_inverter_shutdown and shutdown_col is not None:
                    raw_shut = pd.to_datetime(group_clean[shutdown_col], errors="coerce")
                    valid_shut = raw_shut.dropna()
                    if not valid_shut.empty:
                        valid_shut = valid_shut[valid_shut.dt.year >= 2000]
                    if not valid_shut.empty:
                        proposed = pd.Series(ts_clean < valid_shut.min(), index=ts_clean)
                        if proposed.sum() > 0:
                            daylight_shutdown = proposed

                daylight_mask = pd.Series(
                    daylight_poa.values & daylight_time.values & daylight_shutdown.values,
                    index=ts_clean,
                )
                if daylight_mask.sum() < min_daylight_samples:
                    continue

                i_col_of = {
                    n: f"PV{n} input current(A)"
                    for n in range(1, pv_max + 1)
                    if f"PV{n} input current(A)" in group_clean.columns
                }
                if not i_col_of:
                    continue
                I_matrix = group_clean[list(i_col_of.values())].apply(
                    pd.to_numeric, errors="coerce"
                )

                for mppt_n, members in wb_groups.items():
                    present = [
                        n for n in members
                        if n not in empty_set and n in i_col_of
                    ]
                    for pv_n in members:
                        if pv_n in empty_set:
                            artifact_rows.append({
                                "poa_source": poa_source,
                                "inverter_id": str(inverter_id),
                                "wb_id": wb_id,
                                "pv_string": f"PV{pv_n}",
                                "mppt": int(mppt_n),
                                "status": "EMPTY",
                                "partner_strings": "",
                                "ratio_median_daylight": None,
                                "ratio_event_median": None,
                                "n_qualifying_steps": 0,
                                "n_debounced_events": 0,
                                "emitted_finding": False,
                                "daylight_samples": int(daylight_mask.sum()),
                            })
                            continue
                        if pv_n not in i_col_of:
                            continue
                        partners = [m for m in present if m != pv_n]
                        if len(partners) < min_partner_strings:
                            continue

                        I_string = I_matrix[i_col_of[pv_n]]
                        partner_median_ts = I_matrix[[i_col_of[m] for m in partners]].median(
                            axis=1, skipna=True,
                        )
                        ratio = I_string / partner_median_ts.clip(lower=0.01)
                        qualifying = (ratio < ratio_threshold) & daylight_mask
                        n_events, total_steps = count_debounced_events(qualifying, debounce_steps)

                        ratio_daylight = ratio[daylight_mask.values].dropna()
                        ratio_median_daylight = (
                            float(ratio_daylight.median()) if not ratio_daylight.empty else float("nan")
                        )
                        qual_ratio = ratio[qualifying.values].dropna()
                        ratio_event_median = (
                            float(qual_ratio.median()) if not qual_ratio.empty else float("nan")
                        )

                        qual_indices = qualifying[qualifying].index
                        last_qual_ts = qual_indices[-1] if len(qual_indices) > 0 else latest_ts

                        emitted = n_events > 0
                        if emitted:
                            if ratio_event_median < ratio_critical:
                                severity = Severity.CRITICAL
                            elif ratio_event_median < ratio_high:
                                severity = Severity.HIGH
                            else:
                                severity = Severity.MEDIUM
                            confidence = min(90.0, max(50.0, (1.0 - ratio_event_median) * 100.0))
                            findings.append(M2Finding(
                                timestamp=last_qual_ts.to_pydatetime() if hasattr(last_qual_ts, "to_pydatetime") else last_qual_ts,
                                inverter_id=str(inverter_id),
                                pv_string=f"PV{pv_n}",
                                sub_module=self.name,
                                severity=severity,
                                value=float(ratio_event_median),
                                threshold=float(ratio_threshold),
                                message=(
                                    f"MPPT-partner underperform PV{pv_n} (MPPT {mppt_n}): "
                                    f"I/median(partner)={ratio_event_median:.3f} "
                                    f"({n_events} debounced event(s), src={poa_source})"
                                ),
                                fault_type="mppt_partner_underperform",
                                confidence=float(confidence),
                                evidence={
                                    "poa_source": poa_source,
                                    "mppt": int(mppt_n),
                                    "partner_strings": [f"PV{m}" for m in partners],
                                    "ratio_event_median": ratio_event_median,
                                    "ratio_median_daylight": ratio_median_daylight,
                                    "n_qualified_events": int(n_events),
                                    "total_event_steps": int(total_steps),
                                    "debounce_steps": int(debounce_steps),
                                    "daylight_samples": int(daylight_mask.sum()),
                                    "ratio_threshold": ratio_threshold,
                                    "ratio_high": ratio_high,
                                    "ratio_critical": ratio_critical,
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

                        # m2f: counterfactual = median arus partner se-MPPT per
                        # timestamp (partner_median_ts, sudah dihitung di atas).
                        # flag_mask = qualifying yang SUDAH lolos debounce (bukan
                        # qualifying mentah) -- production debounce_steps=20
                        # (~100 menit), jadi dip 5-menit terisolasi di cloud edge
                        # tidak boleh diklaim kWh-nya walau n_events==0/emitted
                        # ==False. Konsisten dengan open_circuit.py.
                        v_col_mr = f"PV{pv_n} input voltage(V)"
                        if v_col_mr in group_clean.columns:
                            v_string_mr = pd.to_numeric(group_clean[v_col_mr], errors="coerce")
                        else:
                            v_string_mr = pd.Series(np.nan, index=ts_clean)
                        flag_mask_mr = _debounced_qualifying_mask(qualifying, debounce_steps)
                        deficit_rows.append(build_deficit_frame(
                            timestamps=ts_clean,
                            poa_source=poa_source,
                            inverter_id=str(inverter_id),
                            pv_string=f"PV{pv_n}",
                            actual_kw=(I_string * v_string_mr / 1000.0).to_numpy(),
                            counterfactual_kw=(partner_median_ts * v_string_mr / 1000.0).to_numpy(),
                            flagged=flag_mask_mr,
                        ))

                        artifact_rows.append({
                            "poa_source": poa_source,
                            "inverter_id": str(inverter_id),
                            "wb_id": wb_id,
                            "pv_string": f"PV{pv_n}",
                            "mppt": int(mppt_n),
                            "status": "mppt_partner_underperform" if emitted else "NORMAL",
                            "partner_strings": ", ".join(f"PV{m}" for m in partners),
                            "ratio_median_daylight": ratio_median_daylight,
                            "ratio_event_median": ratio_event_median if not np.isnan(ratio_event_median) else None,
                            "n_qualifying_steps": int(qualifying.sum()),
                            "n_debounced_events": int(n_events),
                            "emitted_finding": emitted,
                            "daylight_samples": int(daylight_mask.sum()),
                        })

        if artifact_rows:
            self.artifacts["StringStatus"] = pd.DataFrame(artifact_rows)

        self.deficit_frames.extend(deficit_rows)
        return findings


if __name__ == "__main__":
    # Smoke: PV2 degraded 45% vs partner PV1 dalam MPPT 2-string -> HIGH.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        yaml_path = Path(td) / "strings.yaml"
        yaml_path.write_text(
            "empty_pv_map: {}\n"
            "mppt_map:\n"
            "  SMOKE:\n"
            "    wbs: [WB01]\n"
            "    mppt:\n"
            "      1: [1, 2]\n",
            encoding="utf-8",
        )
        t = pd.date_range("2026-05-14 06:00", "2026-05-14 18:00", freq="5min")
        hours = t.hour + t.minute / 60.0
        sun = np.sin(np.pi * (hours - 6.0) / 12.0) ** 2
        df = pd.DataFrame({
            "Inverter_ID": "WB01-INV01",
            "Start Time": t,
            "PV1 input current(A)": 13.0 * sun,
            "PV2 input current(A)": 13.0 * sun * 0.45,
        })

        class _SmokePOA:
            def get_poa(self, timestamps, wb_id, source="auto"):
                idx = pd.DatetimeIndex(timestamps)
                h = idx.hour + idx.minute / 60.0
                return pd.Series(1000.0 * np.sin(np.pi * (h - 6.0) / 12.0) ** 2, index=idx)

        cfg = {
            "m2b_mppt_ratio": {
                "filter_mode": "hour_cutoff",
                "mppt_map_path": str(yaml_path),
                "pv_max": 2,
            },
            "m2e": {"empty_pv_map_path": str(yaml_path)},
            "poa": {"emit_all_sources": False, "default_source": "auto"},
        }
        sm = M2bMpptRatio(poa=_SmokePOA())
        out = sm.run(df, cfg)
        assert len(out) == 1 and out[0].pv_string == "PV2", out
        assert out[0].severity == Severity.HIGH
        assert "StringStatus" in sm.artifacts
        print(f"[mppt_ratio] smoke OK: {out[0].message}")
