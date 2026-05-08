"""PV string-level performance analysis pipeline.

Phase 0 modules (refactor)
--------------------------
- data_loader : Google Drive download (gdown) + Excel ingestion utilities.
- transformations : ManageObject -> Inverter_ID, PV power computation, pivot helpers.
- string_config : Load and sanitize EMPTY_PV_MAP from YAML.
- viz : Per-inverter heatmap rendering (matplotlib + seaborn).

Phase 1 modules (M2e Hybrid Availability)
-----------------------------------------
- core : Severity, M2Finding, SubModule, M2Engine skeleton.
- m2_config : Load thresholds + status keyword mapping.
- availability : M2eAvailability submodule (inverter-level + string-proxy).
"""

__version__ = "0.2.0"

from . import (
    data_loader,
    transformations,
    string_config,
    viz,
    core,
    m2_config,
    availability,
)

__all__ = [
    "data_loader", "transformations", "string_config", "viz",
    "core", "m2_config", "availability",
]
