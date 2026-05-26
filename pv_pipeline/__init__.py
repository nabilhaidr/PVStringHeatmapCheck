"""PV string-level performance analysis pipeline.

Phase 0 modules (refactor)
--------------------------
- data_loader     : Google Drive download (gdown) + Excel ingestion utilities.
- transformations : ManageObject -> Inverter_ID, PV power computation, pivot helpers.
- string_config   : Load and sanitize EMPTY_PV_MAP from YAML.
- viz             : Per-inverter heatmap rendering (matplotlib + seaborn).

Phase 1 modules (M2e Hybrid Availability)
-----------------------------------------
- core         : Severity, M2Finding, SubModule, M2Engine skeleton.
- m2_config    : Load thresholds + status keyword mapping + poa/panel defaults.
- availability : M2eAvailability submodule (inverter-level + string-proxy).

Sprint 3 / Sprint 4 modules (POA + M2b detectors + baseline + ML skeleton)
-------------------------------------------------------------------------
- panel_spec    : Jinko JKM625N PanelSpec dataclass + Voc/string helpers.
- voc_estimator : estimate_voc_at_low_current (sunrise/sunset Voc).
- poa           : POAProvider orchestrator (PyranometerLoader, PvlibClearSkyEstimator,
                  AlbedoLoader). Multi-year support, solar_elevation passthrough.
- cell_temp     : CellTempProvider with SAPM model fallback (Wave 6).
- peer_zscore   : M2bPeerZScore detector (R-string Z-score with voc_ratio).
- open_circuit  : M2bOpenCircuit detector (I_string / I_q95 ratio + debounce).
- ground_fault  : M2bGroundFault detector (triple signal: absolute / adaptive / spec).
- baseline      : BaselineAccumulator with hybrid filter + maintenance windows.
- training_data : Sprint 4 LSTM-AE skeleton (sequence builder, blocked: needs >=3 mo baseline).
- lstm_ae       : Sprint 4 LSTM-AE PyTorch (blocked: needs trained model).

Fase 2 modules (Physics + multi-source + output enhancement)
------------------------------------------------------------
- physics       : Pmax/P_expected/Kt/DeltaP/PR/active_power_integration helpers.
- preprocessing : Hampel outlier filter (pvanalytics wrapper) + A/B helper.
- weather       : AmbientTempLoader / WindSpeedLoader / WindDirectionLoader (4-WS multi-year).
- generation    : GenerationLoader (IKN Generation Summary PV, daily energy + PAE).

Fase 2 wave history
-------------------
- Wave 1 (220bcfc) : Perez transposition default + physics base (Pmax/Kt/P_expected).
- Wave 2 (6cb38a9) : solar_elevation filter (replace hour_cutoff) + DeltaP helper.
- Wave 3 (cc126a8) : pvanalytics Hampel outlier preprocessing module.
- Wave 4 (135a645) : path migration to "raw data input/" + multi-year POA.
- Wave 5 (fcd0336) : weather loaders (Ambient + WindSpeed + WindDirection).
- Wave 6 (c9376c7) : SAPM Tcell fallback (Sandia thermal model).
- Wave 7 (c686760) : GenerationLoader + PR + active_power_integration.
- Wave 8 (01ce52f) : per-PV StringStatus sheets per detector.
- Wave 9 (05406d6) : Hampel preprocessing A/B wire (feature flag).
- Wave 10         : integration tests Cell 4 e2e + docs refresh.
- Wave 11         : notebook v1.5 (PR + curtailment cross-check) + GenerationLoader
                    deem_dispatch_kwh + curtailment_flag columns.

Note: worktree ini hanya punya subset Phase 0 files. Import Phase 0 dilakukan
secara defensive (warning, tidak fatal) supaya ``import pv_pipeline`` tetap
sukses meskipun beberapa modul Phase 0 belum disinkronkan dari main repo.
"""
from __future__ import annotations

import warnings as _warnings

__version__ = "0.20.0"  # Fase 3 Part 2 Task #5: M2a Soiling skeleton (rdtools SRR)


def _try_import(name: str):
    """Import sub-module dengan tangani ImportError (Phase 0 files mungkin missing)."""
    try:
        return __import__(f"pv_pipeline.{name}", fromlist=[name])
    except ImportError as exc:  # pragma: no cover
        _warnings.warn(
            f"[pv_pipeline] sub-module {name!r} not available: {exc}. "
            "Phase 0 files mungkin belum disinkronkan dari main repo.",
            stacklevel=2,
        )
        return None


# Phase 0 (best-effort, beberapa file mungkin missing di worktree ini).
data_loader = _try_import("data_loader")
transformations = _try_import("transformations")
string_config = _try_import("string_config")
viz = _try_import("viz")

# Phase 1 (M2e) - core/m2_config wajib, availability bisa fail kalau Phase 0
# string_config tidak tersedia (availability mengonsumsinya).
from . import core, m2_config  # noqa: E402
availability = _try_import("availability")

# Sprint 3.1 + 3.2 - POA + Panel datasheet + Tcell.
from . import panel_spec, poa, cell_temp  # noqa: E402

# Sprint 4.A - M2b detectors (POA-gated) + Voc_actual helper.
from . import voc_estimator, peer_zscore, open_circuit, ground_fault  # noqa: E402

# Sprint 3.3 - Baseline accumulator.
from . import baseline  # noqa: E402

# Sprint 4 - LSTM-AE skeleton (BLOCKED on >=3 months baseline accumulation).
from . import training_data, lstm_ae  # noqa: E402

# Fase 2 Wave 1 - Physics helpers (Pmax/Kt/P_expected per Master Context).
from . import physics  # noqa: E402

# Fase 2 Wave 3 - Preprocessing (Hampel outlier filter via pvanalytics).
from . import preprocessing  # noqa: E402

# Wave 5 - Weather loaders (Ambient/WindSpeed/WindDirection, 4-WS multi-year).
from . import weather  # noqa: E402

# Wave 7 - Generation loader (IKN Generation Summary PV) untuk PR analysis.
from . import generation  # noqa: E402

# Fase 3 Part 2 Task #2 - M2IForest sklearn-based per-inverter anomaly detector.
from . import iforest  # noqa: E402

# Fase 3 Part 2 Task #4 - M2a sub-package (shading, soiling, low-light).
from . import m2a  # noqa: E402

__all__ = [
    # Phase 0
    "data_loader",
    "transformations",
    "string_config",
    "viz",
    # Phase 1 (M2e)
    "core",
    "m2_config",
    "availability",
    # Sprint 3.1 + 3.2
    "panel_spec",
    "poa",
    "cell_temp",
    # Sprint 4.A - M2b detectors
    "voc_estimator",
    "peer_zscore",
    "open_circuit",
    "ground_fault",
    # Sprint 3.3
    "baseline",
    # Sprint 4 skeleton
    "training_data",
    "lstm_ae",
    # Fase 2 Wave 1
    "physics",
    # Fase 2 Wave 3
    "preprocessing",
    # Wave 5
    "weather",
    # Wave 7
    "generation",
    # Fase 3 Part 2 Task #2
    "iforest",
    # Fase 3 Part 2 Task #4
    "m2a",
]
