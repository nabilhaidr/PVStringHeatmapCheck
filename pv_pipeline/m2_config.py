"""M2 thresholds + keyword mapping config loader.

Lifecycle terpisah dari ``string_config.py`` (yang urus EMPTY_PV_MAP):
- ``string_config`` = peta string fisik, jarang berubah.
- ``m2_config`` = threshold analitik, sering di-tune.
"""
from __future__ import annotations

import copy
import os
import warnings
from typing import Any, Dict


DEFAULT_M2_CONFIG: Dict[str, Any] = {
    "m2e": {
        "inverter_status_map": {
            "on_grid_keywords": ["grid connected", "on-grid", "on grid", "ongrid"],
            "down_keywords": ["shutdown", "fault", "stopped", "stop", "error"],
            "transitional_keywords": [
                "standby",
                "starting",
                "stopping",
                "initializing",
                "initialization",
                "detecting",
                "detection",
                "no sunlight",
            ],
        },
        "shutdown_time_detection": "auto",
        "empty_pv_map_path": "config/strings.yaml",
        "string_proxy": {
            "pstr_zero_threshold_kw": 0.1,
            "sibling_median_active_kw": 1.0,
            "min_active_siblings_pct": 50,
            "debounce_consecutive_steps": 20,
        },
        "severity_thresholds": {
            "critical_below": 90,
            "high_below": 95,
            "medium_below": 97,
            "info_below": 99,
            "emit_normal": False,
        },
        "output_dir": "outputs",
        "show_overlay": False,
    },
    # Sprint 3.1 - POA defaults (mirror config/m2_config.yaml -> poa).
    "poa": {
        "site_geometry_path": "config/site_geometry.yaml",
        "default_source": "auto",
        "emit_all_sources": True,
        "sources_to_emit": [
            "pyranometer_per_ws",
            "pyranometer_avg",
            "pvlib_clearsky_ineichen",
            "pvlib_clearsky_simplified_solis",
            "pvlib_clearsky_haurwitz",
        ],
        "auto_fallback_chain": [
            "pyranometer_per_ws",
            "pyranometer_avg",
            "pvlib_clearsky_ineichen",
        ],
        "transposition_model": "perez",  # Fase 2: Perez sebagai default
    },
    # Sprint 3.2 - Panel datasheet config (mirror config/m2_config.yaml -> panel).
    "panel": {
        "spec_path": "config/panel_spec.yaml",
    },
    # Sprint 4.A - M2b detector thresholds (POA-gated).
    "m2b": {
        "poa_threshold_wm2": 300.0,
        "poa_floor_wm2": 50.0,                # 2026-05-16 sunset fix
        "hour_cutoff_end": 18.0,              # 2026-05-16 sunset fix (hour_cutoff mode)
        "respect_inverter_shutdown": True,    # 2026-05-16 sunset fix
        "filter_mode": "solar_elevation",     # Fase 2: "solar_elevation" | "hour_cutoff"
        "solar_elevation_min_deg": 5.0,       # Fase 2: filter saat sun > 5 deg
        "z_threshold": 2.5,
        "voc_ratio_threshold": 0.95,
        "stat_method": "median",
        "pv_max": 28,
        "min_peer_strings": 3,
        "min_daylight_samples": 10,
    },
    "m2b_open_circuit": {
        # Wave 11 hotfix #10: raise 200 -> 500 W/m^2 untuk filter contour
        # shading false positives pada jam 06-08 dan 16-18. Detector hanya
        # qualifying saat strong sun. PVs di shaded zones di terrain
        # berbukit punya POA aktual lebih rendah dari WS pyranometer ->
        # ratio<0.05 muncul legitimate, bukan open circuit. Pakai 500 W/m^2
        # supaya cuma noon-window strong sun yang qualified.
        "poa_threshold_wm2": 700.0,
        "poa_floor_wm2": 50.0,                # 2026-05-16 sunset fix
        "hour_cutoff_end": 18.0,              # 2026-05-16 sunset fix (hour_cutoff mode)
        "respect_inverter_shutdown": True,    # 2026-05-16 sunset fix
        "filter_mode": "solar_elevation",     # Fase 2: "solar_elevation" | "hour_cutoff"
        "solar_elevation_min_deg": 5.0,       # Fase 2: filter saat sun > 5 deg
        "i_ratio_threshold": 0.05,
        "debounce_consecutive_steps": 20,
        "confidence_pct": 95.0,
        "pv_max": 28,
        "min_peer_strings": 3,
        "min_daylight_samples": 5,
    },
    "m2b_ground_fault": {
        "poa_threshold_wm2": 200.0,
        "poa_floor_wm2": 50.0,                # 2026-05-16 sunset fix
        "hour_cutoff_end": 18.0,              # 2026-05-16 sunset fix (hour_cutoff mode)
        "respect_inverter_shutdown": True,    # 2026-05-16 sunset fix
        "filter_mode": "solar_elevation",     # Fase 2: "solar_elevation" | "hour_cutoff"
        "solar_elevation_min_deg": 5.0,       # Fase 2: filter saat sun > 5 deg
        "v_to_ground_abs_threshold_v": 50.0,
        "adaptive_z_threshold": 3.0,
        "voc_ratio_threshold": 0.85,
        "i_high_z_threshold": 2.0,
        "pv_max": 28,
        "min_daylight_samples": 5,
    },
    # Sprint 3.3 - Baseline accumulator config path (mirror config/m2_config.yaml -> baseline).
    "baseline": {
        "config_path": "config/baseline.yaml",
    },
    # Wave 7 - IKN Generation data path (untuk PR analysis).
    "generation": {
        "site_geometry_path": "config/site_geometry.yaml",
        # Per-site installed DC capacity (kWp). Pakai untuk PR denominator.
        # Source: IKN Generation Detail1 sheet -> PV Capacity (kWp) = 71500.
        "capacity_kwp": 71500.0,
    },
    # Wave 9 - Hampel outlier preprocessing (A/B test feature flag).
    # Default OFF supaya backwards-compat. Detector check enabled flag di run().
    "preprocessing": {
        "enabled": True,                       # Set True untuk apply Hampel pre-detector
        "window": 15,                           # ~75 min @ 5-min sampling
        "max_deviation": 3.0,                   # 3-sigma MAD threshold
    },
    # Fase 3 Part 2 Task #4 - M2a Shading detector (Diurnal CV + PR-proxy).
    # Default OFF (opt-in). Detects whole-inverter uniform shading via
    # hour-of-day CV + PR-proxy joint-low signal + diurnal asymmetry
    # classification (shading_morning / shading_afternoon / shading_uniform).
    "m2a_shading": {
	"exclude_from_findings_sheet": True,
        "enabled": True,                       # Opt-in feature flag
        "poa_threshold_wm2": 100.0,             # Daylight gate (lower than M2b)
        "hour_range": [6.0, 18.0],              # Analysis window (sunrise..sunset)
        "cv_low_multiplier": 0.5,               # CV_h < 0.5 * median(CV) -> low
        "pr_low_multiplier": 0.85,              # PR_h < 0.85 * median(PR) -> low
        "min_samples_per_hour": 5,              # Skip hours with too few samples
        "min_hours_for_analysis": 4,            # Need >=4 hours for stable median
        "am_pm_split_hour": 12.0,               # Diurnal asymmetry boundary
        "asymmetry_threshold": 0.5,             # |N_am-N_pm|/total > 0.5 -> asymmetric
        "pv_max": 28,                           # PV string inventory upper bound
    },
    # Fase 3 Part 2 Task #5 - M2a Soiling SRR via rdtools (SKELETON).
    # Default OFF. BLOCKED on >=6 months data + BMKG precipitation (optional).
    # Detector gracefully emits "insufficient_data" finding sampai
    # baseline window >= min_days. Build baseline via
    # pv_pipeline.baseline.BaselineAccumulator.
    "m2a_soiling": {
        "enabled": False,                       # Opt-in feature flag
        "min_days": 90,                         # Minimum data window
        "recommended_days": 180,                # Recommended (6 months)
        "capacity_kwp": 71500.0,                # PLTS-IKN site total DC capacity
        "cleaning_cost_idr": 0.0,               # Placeholder -- user provides
        "electricity_tariff_idr_per_kwh": 1500.0,  # IKN PLTS PPA estimate
        "payback_threshold_days": 30.0,         # payback < this -> recommend cleaning
        "precipitation_path": "",               # Optional BMKG CSV/xlsx path
        "rdtools_reps": 1000,                   # Monte Carlo iterations
        "rdtools_confidence_level": 68.2,       # 1-sigma confidence
        "sample_freq_hours": 0.08333333,        # 5/60 = 5-min Huawei sampling
        "pv_max": 28,
    },
    # Fase 3 Part 2 Task #6 - M2a Low Irradiance Performance Check.
    # Default OFF (opt-in). Per inverter, regress PR_proxy vs POA dalam low band
    # (default 50-250 W/m^2); flag slope_low < threshold dengan minimum R^2.
    # Disambiguate via mid-band (300-800 W/m^2) regression:
    #   - low flagged + mid OK   -> "low_irradiance_underperform" (high Rs modules)
    #   - low flagged + mid flagged -> "general_underperform" (soiling-like)
    "m2a_low_irradiance": {
        "enabled": True,                       # Opt-in feature flag
        "poa_low_range": [50.0, 250.0],         # Low POA band (W/m^2)
        "poa_mid_range": [300.0, 800.0],        # Mid POA band for soiling cross-check
        "min_low_samples": 30,                  # Min samples di low band
        "min_mid_samples": 30,                  # Min samples di mid band
        "slope_threshold": 0.0,                 # slope_low < 0 -> flag
        "r_squared_min": 0.3,                   # Min fit quality untuk emit finding
        "hour_range": [6.0, 18.0],              # Analysis window (sunrise..sunset)
        "hour_cutoff_end": 18.0,                # Defensive sunset
        "solar_elevation_min_deg": 5.0,         # Fase 2 elevation filter
        "respect_inverter_shutdown": True,      # Honor shutdown column
        "pv_max": 28,                           # PV string inventory upper bound
    },
    # Fase 3 Part 2 Task #2 - M2IForest sklearn-based per-inverter anomaly detector.
    # Default OFF (opt-in) mirror Wave 9 pattern. Toggle enabled=True untuk engage.
    "m2_iforest": {
        "enabled": True,                       # Opt-in feature flag
        # 2026-05-23: M2_iforest findings TIDAK masuk Findings sheet utama +
        # tidak trigger Cell 7 auto-skip. Artifact sheets (AnomalyScores +
        # AnomalySummary) tetap di xlsx. Set False kalau mau iforest masuk
        # Findings sheet seperti detector lain.
        "exclude_from_findings_sheet": True,
        "contamination": 0.01,                  # 1% bottom flagged as anomaly
        "n_estimators": 100,                    # IsolationForest n_estimators
        "random_state": 42,                     # Reproducibility
        "min_daylight_samples": 30,             # Min samples per inverter to fit
        "poa_threshold_wm2": 50.0,              # Low gate (iforest learns broader daylight)
        "poa_floor_wm2": 50.0,                  # 2026-05-16 sunset fix
        "hour_cutoff_end": 18.0,                # Defensive sunset
        "solar_elevation_min_deg": 5.0,         # Fase 2 elevation filter
        "respect_inverter_shutdown": True,      # Honor shutdown column
        "pv_max": 28,                           # PV string inventory upper bound
        "include_r_string": True,               # Add R = V/I feature
        "include_sibling_dev": True,            # Add V_dev/I_dev (vs sibling median)
    },
    # Sprint 3.1 extension + Wave 6 (Tcell sources, SAPM fallback).
    "cell_temp": {
        "site_geometry_path": "config/site_geometry.yaml",
        "default_source": "auto",
        "emit_all_sources": True,
        "sources_to_emit": [
            "measured_per_ws",
            "measured_overall_avg",
            "sapm",                          # Wave 6: SAPM cell temp model
        ],
        "auto_fallback_chain": [
            "measured_per_ws",
            "measured_overall_avg",
            "sapm",                          # Wave 6: SAPM sebagai last fallback
        ],
    },
}


def _ensure_yaml() -> None:
    try:
        import yaml  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: PyYAML")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "PyYAML"])


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursive merge override into a copy of base. override wins."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_m2_config(path: str) -> Dict[str, Any]:
    """Load YAML config, merge atas DEFAULT_M2_CONFIG.

    Bila ``path`` tidak ada, return defaults + warning (tidak raise).
    """
    if not path or not os.path.exists(path):
        warnings.warn(
            f"[m2_config] {path!r} not found, using DEFAULT_M2_CONFIG",
            stacklevel=2,
        )
        return copy.deepcopy(DEFAULT_M2_CONFIG)

    _ensure_yaml()
    import yaml  # noqa: WPS433

    with open(path, "r", encoding="utf-8") as fp:
        user_cfg = yaml.safe_load(fp) or {}

    return _deep_merge(DEFAULT_M2_CONFIG, user_cfg)


if __name__ == "__main__":
    from pv_pipeline.m2_config import DEFAULT_M2_CONFIG, load_m2_config
    cfg = load_m2_config("nonexistent_path.yaml")
    assert cfg["m2e"]["severity_thresholds"]["critical_below"] == 90
    assert "grid connected" in cfg["m2e"]["inverter_status_map"]["on_grid_keywords"]
    assert cfg["m2e"]["string_proxy"]["pstr_zero_threshold_kw"] == 0.1
    # Sprint 3.1 + 3.2: poa & panel defaults
    assert cfg["poa"]["default_source"] == "auto"
    assert "pvlib_clearsky_ineichen" in cfg["poa"]["sources_to_emit"]
    assert cfg["poa"]["auto_fallback_chain"][0] == "pyranometer_per_ws"
    assert cfg["panel"]["spec_path"] == "config/panel_spec.yaml"
    # Sprint 3.1 extension: cell_temp defaults
    assert cfg["cell_temp"]["default_source"] == "auto"
    assert "measured_per_ws" in cfg["cell_temp"]["sources_to_emit"]
    assert cfg["cell_temp"]["auto_fallback_chain"][0] == "measured_per_ws"
    print("[m2_config] defaults smoke OK")
    # YAML round-trip test
    cfg2 = load_m2_config("config/m2_config.yaml")
    assert cfg2["m2e"]["severity_thresholds"]["critical_below"] == 90
    assert "grid connected" in cfg2["m2e"]["inverter_status_map"]["on_grid_keywords"]
    assert cfg2["poa"]["site_geometry_path"] == "config/site_geometry.yaml"
    assert cfg2["poa"]["emit_all_sources"] is True
    assert "pvlib_clearsky_simplified_solis" in cfg2["poa"]["sources_to_emit"]
    assert cfg2["panel"]["spec_path"] == "config/panel_spec.yaml"
    assert cfg2["cell_temp"]["default_source"] == "auto"
    assert "measured_overall_avg" in cfg2["cell_temp"]["sources_to_emit"]
    print("[m2_config] yaml round-trip OK")
