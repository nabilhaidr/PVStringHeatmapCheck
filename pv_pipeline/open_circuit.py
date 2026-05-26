"""M2b Open-Circuit detector untuk PV string.

Spec 4.2.3 (Master Context):
    Open circuit / blown fuse:
        I_string < 5% dari I_quartile95 saat POA > 200 W/m^2
        Confidence: 95%

Peer scope: sibling PV strings di SAMA inverter (PV1..PV28).
I_q95 dihitung per timestamp ACROSS sibling strings (bukan time-series q95 per string).

Multi-source comparison: detector loop di tiap source dari
``config.poa.sources_to_emit``, emit finding ber-tag ``poa_source``.

Debounce: ratio I_string/I_q95 < 0.05 harus terjadi >=N langkah konsekutif
(default 2) untuk dianggap genuine event (filter noise/glitch).
"""
from __future__ import annotations

import warnings
from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from pv_pipeline.core import M2Finding, Severity, SubModule


DEFAULT_POA_THRESHOLD_WM2: float = 200.0
DEFAULT_POA_FLOOR_WM2: float = 50.0       # hard floor: skip kalau POA < 50 (sunset/twilight)
DEFAULT_HOUR_CUTOFF_END: float = 18.0     # Dipakai saat filter_mode="hour_cutoff"
DEFAULT_RESPECT_INVERTER_SHUTDOWN: bool = True  # skip rows setelah Inverter shutdown time
DEFAULT_FILTER_MODE: str = "solar_elevation"    # Fase 2: "solar_elevation" | "hour_cutoff"
DEFAULT_SOLAR_ELEV_MIN_DEG: float = 5.0          # Fase 2: sun apparent_elevation threshold
DEFAULT_I_RATIO_THRESHOLD: float = 0.05  # 5% per spec
DEFAULT_DEBOUNCE_STEPS: int = 2
DEFAULT_CONFIDENCE_PCT: float = 95.0

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
    if not inverter_id:
        return ""
    parts = str(inverter_id).split("-")
    return parts[0].upper() if parts else str(inverter_id).upper()


def count_debounced_events(boolean_series: pd.Series, min_consecutive: int) -> Tuple[int, int]:
    """Count consecutive-True groups dengan length >= ``min_consecutive``.

    Returns
    -------
    (n_events, total_qualifying_steps)
        n_events: jumlah group debounced (event).
        total_qualifying_steps: total True steps di group-group itu.
    """
    if boolean_series is None or len(boolean_series) == 0:
        return 0, 0
    arr = boolean_series.astype(bool).to_numpy()
    n_events = 0
    total_steps = 0
    cur = 0
    for v in arr:
        if v:
            cur += 1
        else:
            if cur >= min_consecutive:
                n_events += 1
                total_steps += cur
            cur = 0
    # Tail group
    if cur >= min_consecutive:
        n_events += 1
        total_steps += cur
    return n_events, total_steps


class M2bOpenCircuit(SubModule):
    """Detector open-circuit / blown fuse: I_string << I_q95 fleet."""

    name: str = "M2b_open_circuit"

    def __init__(self, poa=None):
        super().__init__()
        self.poa = poa

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
        cfg = config.get("m2b_open_circuit", {}) or {}
        poa_threshold = float(cfg.get("poa_threshold_wm2", DEFAULT_POA_THRESHOLD_WM2))
        poa_floor = float(cfg.get("poa_floor_wm2", DEFAULT_POA_FLOOR_WM2))
        hour_cutoff_end = float(cfg.get("hour_cutoff_end", DEFAULT_HOUR_CUTOFF_END))
        respect_inverter_shutdown = bool(cfg.get(
            "respect_inverter_shutdown", DEFAULT_RESPECT_INVERTER_SHUTDOWN
        ))
        filter_mode = str(cfg.get("filter_mode", DEFAULT_FILTER_MODE)).lower()
        solar_elev_min_deg = float(cfg.get("solar_elevation_min_deg", DEFAULT_SOLAR_ELEV_MIN_DEG))
        i_ratio_threshold = float(cfg.get("i_ratio_threshold", DEFAULT_I_RATIO_THRESHOLD))
        debounce_steps = int(cfg.get("debounce_consecutive_steps", DEFAULT_DEBOUNCE_STEPS))
        confidence = float(cfg.get("confidence_pct", DEFAULT_CONFIDENCE_PCT))
        pv_max = int(cfg.get("pv_max", 28))
        min_daylight_samples = int(cfg.get("min_daylight_samples", 5))
        min_peer_strings = int(cfg.get("min_peer_strings", 3))

        # Find Inverter shutdown time column (kalau ada).
        shutdown_col = _find_shutdown_col(combined_df) if respect_inverter_shutdown else None

        if "Inverter_ID" not in combined_df.columns or "Start Time" not in combined_df.columns:
            warnings.warn(
                "[M2bOpenCircuit] missing 'Inverter_ID' or 'Start Time'; skipping.",
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

        self._ensure_poa(config)
        sources = self._resolve_sources(config)

        # Wave 11 hotfix #11: Huawei xlsx pakai mixed case:
        #   PV1-PV14: "PV{n} input voltage(V)" / "PV{n} input current(A)" (lowercase)
        #   PV15-PV28: "PV{n} Input Voltage(V)" / "PV{n} Input Current(A)" (Title Case)
        # Normalize ke lowercase canonical supaya analysis catch PV15-PV28.
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

        # Wave 11 hotfix #10: load empty_pv_map ONCE supaya main loop bisa
        # skip empty PV slots (PV slot kosong by design per strings.yaml).
        # Tanpa filter ini, kolom V/I dari empty slot dilaporkan oleh Huawei
        # sebagai 0 -> ratio=0/9.11=0 < threshold -> CRITICAL emitted (false
        # positive, e.g., 910 dari 1709 CRITICAL @ 2026-05-14 user run).
        from pv_pipeline.core import load_empty_pv_map as _load_emap_mainloop
        _empty_map_main = _load_emap_mainloop(config)

        all_ts = pd.to_datetime(combined_df["Start Time"], errors="coerce")
        latest_ts = all_ts.max() if not all_ts.dropna().empty else datetime.utcnow()

        for poa_source in sources:
            for inverter_id, group in combined_df.groupby("Inverter_ID"):
                wb_id = _wb_from_inverter_id(inverter_id)
                _inv_empties = _empty_map_main.get(str(inverter_id).upper(), [])
                _inv_empty_set = set(int(n) for n in _inv_empties)
                timestamps = pd.to_datetime(group["Start Time"], errors="coerce")
                valid_idx = timestamps.notna()
                if valid_idx.sum() < min_daylight_samples:
                    continue
                ts_clean = pd.DatetimeIndex(timestamps[valid_idx].values)
                group_clean = group.loc[valid_idx].copy()
                group_clean.index = ts_clean

                # POA gate (main threshold + hard floor untuk sunset/twilight)
                try:
                    poa = self.poa.get_poa(ts_clean, wb_id, source=poa_source)
                except Exception as exc:
                    warnings.warn(
                        f"[M2bOpenCircuit] POA query failed (wb={wb_id}, src={poa_source}): "
                        f"{exc}", stacklevel=2,
                    )
                    continue

                # Combine 3 daylight conditions (per user fix 2026-05-16):
                #   1. POA > poa_threshold (main) DAN POA > poa_floor (hard sunset floor)
                #   2. Hour-of-day < hour_cutoff_end (sunset cutoff hardcoded jam)
                #   3. Sebelum Inverter shutdown time (kalau column tersedia)
                poa_aligned = poa.reindex(ts_clean).fillna(0.0)
                daylight_poa = (poa_aligned > poa_threshold) & (poa_aligned > poa_floor)

                # Fase 2: solar_elevation filter (physical) atau hour_cutoff (heuristic).
                effective_mode = filter_mode
                if filter_mode == "solar_elevation":
                    try:
                        elev = self.poa.get_solar_elevation(ts_clean)
                        elev_aligned = elev.reindex(ts_clean)
                        if elev_aligned.notna().any():
                            # Wave 11 hotfix #7: defensive AND dengan hour_cutoff.
                            # Tanpa ini, IKN equator twilight 18:15 lolos filter
                            # -> false-positive CRITICAL open_circuit emit.
                            elev_mask = elev_aligned.fillna(-90.0).values > solar_elev_min_deg
                            hour_arr_se = ts_clean.hour + ts_clean.minute / 60.0
                            hour_mask_se = hour_arr_se < hour_cutoff_end
                            daylight_time = pd.Series(
                                elev_mask & hour_mask_se,
                                index=ts_clean,
                            )
                        else:
                            effective_mode = "hour_cutoff"
                            hour_arr = ts_clean.hour + ts_clean.minute / 60.0
                            daylight_time = pd.Series(hour_arr < hour_cutoff_end, index=ts_clean)
                    except Exception as exc:
                        warnings.warn(
                            f"[M2bOpenCircuit] solar_elevation query failed (wb={wb_id}): {exc}. "
                            f"Falling back ke hour_cutoff.",
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
                    # Per-inverter shutdown timestamp (biasanya seragam dalam 1 inverter,
                    # ambil min non-NaT supaya konservatif).
                    raw_shut = pd.to_datetime(group_clean[shutdown_col], errors="coerce")
                    valid_shut = raw_shut.dropna()
                    # Wave 11 hotfix #5: drop sentinel datetimes (year<2000).
                    if not valid_shut.empty:
                        valid_shut = valid_shut[valid_shut.dt.year >= 2000]
                    if not valid_shut.empty:
                        shutdown_ts = valid_shut.min()
                        proposed = pd.Series(ts_clean < shutdown_ts, index=ts_clean)
                        # Wave 11 hotfix #6: skip filter kalau proposed mask
                        # drop ALL ts (likely sentinel like "0:00:00" parsed
                        # as today midnight).
                        if proposed.sum() > 0:
                            daylight_shutdown = proposed

                daylight_mask = daylight_poa.values & daylight_time.values & daylight_shutdown.values
                daylight_mask = pd.Series(daylight_mask, index=ts_clean)
                if daylight_mask.sum() < min_daylight_samples:
                    continue

                # Identifikasi kolom I per PV string yang tersedia.
                i_cols = [
                    f"PV{n} input current(A)" for n in range(1, pv_max + 1)
                    if f"PV{n} input current(A)" in group_clean.columns
                ]
                if len(i_cols) < min_peer_strings:
                    continue

                I_matrix = group_clean[i_cols].apply(pd.to_numeric, errors="coerce")
                # I_q95 per-timestamp ACROSS sibling strings (per spec).
                I_q95_per_ts = I_matrix.quantile(0.95, axis=1)

                for i_col in i_cols:
                    pv_n_str = i_col.split(" ")[0]  # "PV5"
                    pv_n = int(pv_n_str[2:])
                    # Wave 11 hotfix #10: skip empty PV slots dari analisis.
                    if pv_n in _inv_empty_set:
                        continue
                    I_string = I_matrix[i_col]

                    # ratio = I_string / I_q95 (per timestamp)
                    ratio = I_string / I_q95_per_ts.clip(lower=0.01)
                    qualifying = (ratio < i_ratio_threshold) & daylight_mask
                    n_events, total_steps = count_debounced_events(qualifying, debounce_steps)

                    # Median stats (untuk artifact).
                    I_string_daylight = I_string[daylight_mask.values].dropna()
                    I_q95_daylight = I_q95_per_ts[daylight_mask.values].dropna()
                    i_string_median = float(I_string_daylight.median()) if not I_string_daylight.empty else float("nan")
                    i_q95_median = float(I_q95_daylight.median()) if not I_q95_daylight.empty else float("nan")
                    ratio_median = float((ratio[daylight_mask.values]).dropna().median()) if (ratio[daylight_mask.values]).dropna().size > 0 else float("nan")

                    # Wave 11 hotfix #9: use LAST qualifying timestamp untuk
                    # Finding.timestamp (bukan latest_ts). Tanpa ini, semua
                    # findings stamped @ data's last sample (e.g., 18:15) yang
                    # menyebabkan user salah baca seolah qualifying terjadi di
                    # twilight. Sebenarnya qualifying di noon (daylight_mask).
                    qual_indices = qualifying[qualifying].index
                    if len(qual_indices) > 0:
                        last_qual_ts = qual_indices[-1]
                    else:
                        last_qual_ts = latest_ts

                    emitted = n_events > 0
                    if emitted:
                        findings.append(M2Finding(
                            timestamp=last_qual_ts.to_pydatetime() if hasattr(last_qual_ts, "to_pydatetime") else last_qual_ts,
                            inverter_id=str(inverter_id),
                            pv_string=f"PV{pv_n}",
                            sub_module=self.name,
                            severity=Severity.CRITICAL,
                            value=float(ratio_median if not np.isnan(ratio_median) else 0.0),
                            threshold=float(i_ratio_threshold),
                            message=(
                                f"Open-circuit suspect PV{pv_n}: "
                                f"I/I_q95 median={ratio_median:.3f} "
                                f"({n_events} debounced event(s), src={poa_source})"
                            ),
                            fault_type="open_circuit",
                            confidence=confidence,
                            evidence={
                                "poa_source": poa_source,
                                "i_string_median": i_string_median,
                                "i_q95_median": i_q95_median,
                                "ratio_median": ratio_median,
                                "n_qualified_events": int(n_events),
                                "total_event_steps": int(total_steps),
                                "debounce_steps": int(debounce_steps),
                                "daylight_samples": int(daylight_mask.sum()),
                                "poa_threshold_wm2": poa_threshold,
                                "poa_floor_wm2": poa_floor,
                                "hour_cutoff_end": hour_cutoff_end,
                                "respect_inverter_shutdown": respect_inverter_shutdown,
                                "shutdown_col_used": shutdown_col,
                                "filter_mode": filter_mode,
                                "filter_mode_effective": effective_mode,
                                "solar_elevation_min_deg": solar_elev_min_deg,
                                "i_ratio_threshold": i_ratio_threshold,
                            },
                        ))

                    artifact_rows.append({
                        "poa_source": poa_source,
                        "inverter_id": str(inverter_id),
                        "wb_id": wb_id,
                        "pv_string": f"PV{pv_n}",
                        "status": "open_circuit" if emitted else "NORMAL",
                        "i_string_median_daylight": i_string_median,
                        "i_q95_median_daylight": i_q95_median,
                        "ratio_median_daylight": ratio_median,
                        "n_qualifying_steps": int(qualifying.sum()),
                        "n_debounced_events": int(n_events),
                        "total_event_steps": int(total_steps),
                        "emitted_finding": emitted,
                        "daylight_samples": int(daylight_mask.sum()),
                    })

        # Wave 11 hotfix #8 (revised by #9): top-up FULL PV inventory match
        # M2e_hybrid_AllStrings shape. Empty PVs emit dengan status="EMPTY".
        if artifact_rows:
            from pv_pipeline.core import load_empty_pv_map as _load_emap_oc
            empty_pv_map_oc = _load_emap_oc(config)
            existing_pairs_oc = {
                (r["inverter_id"], r["pv_string"]) for r in artifact_rows
            }
            for inv_id in combined_df["Inverter_ID"].dropna().unique():
                inv_str_oc = str(inv_id)
                empties_oc = set(empty_pv_map_oc.get(inv_str_oc.upper(), []))
                wb_id_oc = _wb_from_inverter_id(inv_id)
                for pv_n in range(1, pv_max + 1):
                    pv_label = f"PV{pv_n}"
                    if (inv_str_oc, pv_label) in existing_pairs_oc:
                        continue
                    if pv_n in empties_oc:
                        artifact_rows.append({
                            "poa_source": "n/a",
                            "inverter_id": inv_str_oc,
                            "wb_id": wb_id_oc,
                            "pv_string": pv_label,
                            "status": "EMPTY",
                            "note": "empty_pv_slot_per_strings_yaml",
                            "emitted_finding": False,
                        })
                    else:
                        artifact_rows.append({
                            "poa_source": "n/a",
                            "inverter_id": inv_str_oc,
                            "wb_id": wb_id_oc,
                            "pv_string": pv_label,
                            "status": "NORMAL",
                            "note": "topup_no_voltage_or_current_data_for_this_pv",
                            "emitted_finding": False,
                        })

        # Wave 11 hotfix #3: fan-out NORMAL placeholder kalau main loop empty
        # (sama pattern dengan peer_zscore + ground_fault) supaya StringStatus
        # sheet ALWAYS muncul di output xlsx walau semua inverter gagal data gate.
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
                    i_col = f"PV{pv_n} input current(A)"
                    if i_col not in combined_df.columns:
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
            # Wave 8: rename ke StringStatus + tambah status column (NORMAL | open_circuit).
            self.artifacts["StringStatus"] = pd.DataFrame(artifact_rows)
        return findings


if __name__ == "__main__":
    # Synthetic smoke: 10 strings, PV7 open-circuit (I ~0 di daylight).
    # PLUS sunset edge case: 18:00-19:00 semua I=0 + POA residual >50 (mock pyranometer lag) -
    # harus DI-SKIP karena hour_cutoff_end=18.0 (per user fix 2026-05-16).
    import sys
    sys.path.insert(0, ".claude/worktrees/modest-shockley-9c31f4")

    rng = np.random.default_rng(7)
    # Range full 06:00-20:00 (extend past sunset) untuk test cutoff.
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 20:00", freq="5min")
    n = len(t)
    rows = []
    inverter_shutdown_ts = pd.Timestamp("2026-05-14 18:25:00")  # shutdown jam 18:25
    for ts_i, ts in enumerate(t):
        # Sun profile: 06-18 normal arc, 18-20 dimming -> 0 by 18:30.
        hour_of_day = ts.hour + ts.minute / 60.0
        if 6.0 <= hour_of_day <= 18.0:
            sun = np.sin(np.pi * (hour_of_day - 6.0) / 12.0) ** 2
        else:
            sun = 0.0
        I_base_val = 13.0 * sun

        row = {
            "Inverter_ID": "WB05-INV05",
            "Start Time": ts,
            "Inverter shutdown time": inverter_shutdown_ts,
        }
        for pv_n in range(1, 11):
            if pv_n == 7:
                # PV7 broken open_circuit sepanjang hari (akan flag di daylight)
                row[f"PV{pv_n} input current(A)"] = 0.05 + rng.normal(0, 0.02)
            else:
                row[f"PV{pv_n} input current(A)"] = I_base_val + rng.normal(0, 0.1)
        rows.append(row)
    df = pd.DataFrame(rows)

    class MockPOA:
        """Mock pyranometer dengan sunset lag: POA masih > 100 di 18:00-18:30."""
        def get_poa(self, timestamps, wb_id, source="auto"):
            ts_idx = pd.DatetimeIndex(timestamps)
            poa_out = []
            for ts in ts_idx:
                hour = ts.hour + ts.minute / 60.0
                if 6.0 <= hour <= 18.0:
                    sun = np.sin(np.pi * (hour - 6.0) / 12.0) ** 2
                    poa_out.append(1000.0 * sun)
                elif 18.0 < hour <= 18.5:
                    # Sunset lag: pyranometer masih emit 100-300 W/m^2 (sensor lag)
                    poa_out.append(250.0)
                else:
                    poa_out.append(0.0)
            return pd.Series(poa_out, index=ts_idx)

    cfg = {
        "m2b_open_circuit": {
            "poa_threshold_wm2": 200.0,
            "poa_floor_wm2": 50.0,
            "hour_cutoff_end": 18.0,
            "respect_inverter_shutdown": True,
            "i_ratio_threshold": 0.05,
            "debounce_consecutive_steps": 2,
            "confidence_pct": 95.0,
            "pv_max": 10,
            "min_peer_strings": 3,
            "min_daylight_samples": 5,
        },
        "poa": {"emit_all_sources": False, "default_source": "auto",
                "site_geometry_path": "config/site_geometry.yaml"},
    }
    sm = M2bOpenCircuit(poa=MockPOA())
    findings = sm.run(df, cfg)
    print(f"[open_circuit] findings count: {len(findings)}")
    for f in findings:
        ev = f.evidence or {}
        print(f"  {f.inverter_id}/{f.pv_string}: ratio_median={f.value:.3f} "
              f"sev={f.severity.value} conf={f.confidence:.0f}%  "
              f"daylight_samples={ev.get('daylight_samples')}")

    # Verify PV7 (real open-circuit di daytime) STILL flagged.
    assert any(f.pv_string == "PV7" for f in findings), "PV7 should still be flagged (real open-circuit)"
    # Verify no PV1-6/8-10 false positives di sunset window (mereka I=0 di 18:00+).
    non_pv7 = [f for f in findings if f.pv_string != "PV7"]
    assert len(non_pv7) == 0, f"unexpected false positives: {[f.pv_string for f in non_pv7]}"
    print("\n[open_circuit] sunset fix smoke OK")
    print("  - PV7 (real open-circuit daytime): flagged")
    print("  - PV1-PV6, PV8-PV10 di sunset window 18:00+: NOT flagged (cutoff bekerja)")
