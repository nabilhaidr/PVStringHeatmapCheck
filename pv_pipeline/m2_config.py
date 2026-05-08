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
        "string_proxy": {
            "pstr_zero_threshold_kw": 0.1,
            "sibling_median_active_kw": 1.0,
            "min_active_siblings_pct": 50,
            "debounce_consecutive_steps": 2,
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
    print("[m2_config] defaults smoke OK")
