"""Test pv_pipeline.m2_config: DEFAULT_M2_CONFIG + load_m2_config deep-merge."""
from __future__ import annotations

import pytest

from pv_pipeline.m2_config import DEFAULT_M2_CONFIG, load_m2_config


# ---------- DEFAULT_M2_CONFIG structure ----------


def test_default_has_all_top_level_sections():
    """All Sprint sections present di DEFAULT_M2_CONFIG."""
    required_sections = [
        "m2e",                # Phase 1
        "m2b",                # Sprint 4.A peer_zscore
        "m2b_open_circuit",   # Sprint 4.A
        "m2b_ground_fault",   # Sprint 4.A
        "poa",                # Sprint 3.1
        "panel",              # Sprint 3.2
        "cell_temp",          # Sprint 3.1 extension
        "baseline",           # Sprint 3.3
    ]
    for section in required_sections:
        assert section in DEFAULT_M2_CONFIG, f"missing section: {section}"


def test_m2e_severity_thresholds():
    """M2e severity thresholds match spec design doc."""
    sev = DEFAULT_M2_CONFIG["m2e"]["severity_thresholds"]
    assert sev["critical_below"] == 90
    assert sev["high_below"] == 95
    assert sev["medium_below"] == 97
    assert sev["info_below"] == 99


def test_m2b_sunset_fix_defaults():
    """Sunset fix defaults (2026-05-16) di 3 detector sections."""
    for section in ["m2b", "m2b_open_circuit", "m2b_ground_fault"]:
        cfg = DEFAULT_M2_CONFIG[section]
        assert cfg["poa_floor_wm2"] == 50.0, f"{section}: poa_floor mismatch"
        assert cfg["hour_cutoff_end"] == 18.0, f"{section}: hour_cutoff mismatch"
        assert cfg["respect_inverter_shutdown"] is True, f"{section}: respect_shutdown mismatch"


def test_m2b_peer_zscore_thresholds():
    """M2b peer_zscore spec 4.2.1 + 4.2.3."""
    cfg = DEFAULT_M2_CONFIG["m2b"]
    assert cfg["poa_threshold_wm2"] == 300.0  # per spec 4.2.1
    assert cfg["z_threshold"] == 2.5          # |z| > 2.5
    assert cfg["voc_ratio_threshold"] == 0.95


def test_m2b_open_circuit_thresholds():
    """M2b open_circuit spec 4.2.3."""
    cfg = DEFAULT_M2_CONFIG["m2b_open_circuit"]
    # Wave 11 hotfix #10 + tune: raised to 700 untuk filter contour
    # shading FPs (PLTS-IKN site contour-heavy, need strong sun gate).
    # debounce raised 2 -> 20 to require persistent (>=20 consecutive samples
    # ~1.7 hour) ratio<5% before flagging.
    assert cfg["poa_threshold_wm2"] == 700.0
    assert cfg["i_ratio_threshold"] == 0.05    # 5% per spec
    assert cfg["confidence_pct"] == 95.0       # per spec
    assert cfg["debounce_consecutive_steps"] == 20


def test_m2b_ground_fault_thresholds():
    """M2b ground_fault triple-signal thresholds."""
    cfg = DEFAULT_M2_CONFIG["m2b_ground_fault"]
    assert cfg["v_to_ground_abs_threshold_v"] == 50.0
    assert cfg["adaptive_z_threshold"] == 3.0
    assert cfg["voc_ratio_threshold"] == 0.85   # spec 4.2.3
    assert cfg["i_high_z_threshold"] == 2.0


def test_poa_section_multi_source():
    """POA multi-source comparison config."""
    cfg = DEFAULT_M2_CONFIG["poa"]
    assert cfg["default_source"] == "auto"
    assert cfg["emit_all_sources"] is True
    sources = cfg["sources_to_emit"]
    assert "pyranometer_per_ws" in sources
    assert "pvlib_clearsky_ineichen" in sources
    assert len(sources) == 5  # 2 pyranometer + 3 pvlib
    chain = cfg["auto_fallback_chain"]
    assert chain[0] == "pyranometer_per_ws"
    assert chain[-1] == "pvlib_clearsky_ineichen"


def test_poa_transposition_model_perez_default():
    """Fase 2: DEFAULT_M2_CONFIG['poa']['transposition_model'] == 'perez'."""
    assert DEFAULT_M2_CONFIG["poa"]["transposition_model"] == "perez"


def test_m2b_filter_mode_solar_elevation_default():
    """Fase 2 Wave 2: 3 detector sections default filter_mode='solar_elevation', min=5 deg."""
    for section in ["m2b", "m2b_open_circuit", "m2b_ground_fault"]:
        cfg = DEFAULT_M2_CONFIG[section]
        assert cfg["filter_mode"] == "solar_elevation", f"{section}: filter_mode mismatch"
        assert cfg["solar_elevation_min_deg"] == 5.0, f"{section}: elev_min mismatch"


def test_cell_temp_section():
    cfg = DEFAULT_M2_CONFIG["cell_temp"]
    assert cfg["default_source"] == "auto"
    assert "measured_per_ws" in cfg["sources_to_emit"]
    assert "measured_overall_avg" in cfg["sources_to_emit"]
    # Wave 6: SAPM fallback added to chain.
    assert "sapm" in cfg["sources_to_emit"]
    assert cfg["auto_fallback_chain"][-1] == "sapm"


def test_baseline_section():
    cfg = DEFAULT_M2_CONFIG["baseline"]
    assert cfg["config_path"] == "config/baseline.yaml"


# ---------- load_m2_config behavior ----------


def test_load_missing_path_returns_defaults():
    """Path tidak ada -> return copy of defaults + warning."""
    cfg = load_m2_config("nonexistent_path.yaml")
    assert cfg["m2e"]["severity_thresholds"]["critical_below"] == 90


def test_load_missing_path_returns_independent_copy():
    """Modify returned dict tidak affect DEFAULT_M2_CONFIG (deep copy)."""
    cfg1 = load_m2_config("nonexistent.yaml")
    cfg1["m2e"]["severity_thresholds"]["critical_below"] = 999
    cfg2 = load_m2_config("nonexistent.yaml")
    assert cfg2["m2e"]["severity_thresholds"]["critical_below"] == 90  # unaffected


def test_load_yaml_deep_merge(tmp_path):
    """User yaml override partial keys, defaults preserved untuk rest."""
    yaml_text = """\
m2e:
  severity_thresholds:
    critical_below: 85
m2b_open_circuit:
  poa_floor_wm2: 100.0
"""
    p = tmp_path / "user.yaml"
    p.write_text(yaml_text, encoding="utf-8")

    cfg = load_m2_config(str(p))
    # Override
    assert cfg["m2e"]["severity_thresholds"]["critical_below"] == 85
    assert cfg["m2b_open_circuit"]["poa_floor_wm2"] == 100.0
    # Defaults preserved
    assert cfg["m2e"]["severity_thresholds"]["high_below"] == 95
    assert cfg["m2b_open_circuit"]["hour_cutoff_end"] == 18.0  # sunset fix preserved
    assert cfg["m2b"]["z_threshold"] == 2.5                    # peer_zscore preserved


def test_load_empty_yaml_falls_back_to_defaults(tmp_path):
    """Yaml kosong/null tetap return defaults (no crash)."""
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    cfg = load_m2_config(str(p))
    assert cfg["m2e"]["severity_thresholds"]["critical_below"] == 90
