"""M2a analytical layer: site-level shading / soiling / low-light degradation.

Berbeda dari M2b (per-PV-string fault detection) dan M2e (inverter
availability), M2a fokus pada whole-array / whole-inverter degradation
yang bersifat sistemik (uniform):

- M2a Shading (Task #4) : terrain / building / cloud shadow patterns via
                          diurnal CV + PR-proxy time-of-day analysis.
- M2a Soiling (Task #5, SKELETON) : Soiling Ratio (SRR) via rdtools.
                                    Skeleton ready -- blocked on >=6 bulan
                                    data + BMKG precipitation. Detector
                                    gracefully emits "insufficient_data"
                                    finding sampai data sufficient.
- M2a Low Irradiance (Task #6) : PR vs POA slope di low-light range
                                 (50-250 W/m^2) -> modul underperform.

Default OFF semua (opt-in via config) supaya backwards-compat.
"""
from __future__ import annotations

from . import shading, low_irradiance, soiling  # noqa: F401

__all__ = ["shading", "low_irradiance", "soiling"]
