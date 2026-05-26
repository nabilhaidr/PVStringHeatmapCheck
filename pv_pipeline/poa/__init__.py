"""POA (Plane-of-Array irradiance) provider package.

Public API
----------
- :class:`PyranometerLoader` -- baca xlsx pyranometer 5 WS + avg.
- :class:`PvlibClearSkyEstimator` -- 3 model clear-sky (Ineichen, Solis, Haurwitz).
- :class:`POAProvider` -- orchestrator multi-source dengan fallback chain.

Source identifier constants (untuk get_poa(source=...)):
    SOURCE_PYRANOMETER_PER_WS
    SOURCE_PYRANOMETER_AVG
    SOURCE_PVLIB_INEICHEN
    SOURCE_PVLIB_SOLIS
    SOURCE_PVLIB_HAURWITZ
    SOURCE_AUTO
"""
from __future__ import annotations

from .albedo_loader import (
    DEFAULT_ALBEDO_SHEET,
    DEFAULT_ALBEDO_TIMESTAMP_COL,
    DEFAULT_ALBEDO_VALUE_COL,
    DEFAULT_ALBEDO_TOLERANCE,
    AlbedoLoader,
)
from .loader import (
    COL_AVG,
    COL_PER_WS_FMT,
    COL_TIMESTAMP,
    DEFAULT_REINDEX_TOLERANCE,
    PyranometerLoader,
)
from .pvlib_estimator import (
    DEFAULT_ALBEDO,
    DEFAULT_TRANSPOSITION_MODEL,
    MODEL_HAURWITZ,
    MODEL_INEICHEN,
    MODEL_SOLIS,
    SUPPORTED_MODELS,
    PvlibClearSkyEstimator,
)
from .provider import (
    ALL_NON_AUTO_SOURCES,
    ALL_SOURCES,
    DEFAULT_AUTO_FALLBACK_CHAIN,
    POAProvider,
    SOURCE_AUTO,
    SOURCE_PVLIB_HAURWITZ,
    SOURCE_PVLIB_INEICHEN,
    SOURCE_PVLIB_SOLIS,
    SOURCE_PYRANOMETER_AVG,
    SOURCE_PYRANOMETER_PER_WS,
)

__all__ = [
    # Classes
    "PyranometerLoader",
    "PvlibClearSkyEstimator",
    "POAProvider",
    "AlbedoLoader",
    # Source identifiers
    "SOURCE_PYRANOMETER_PER_WS",
    "SOURCE_PYRANOMETER_AVG",
    "SOURCE_PVLIB_INEICHEN",
    "SOURCE_PVLIB_SOLIS",
    "SOURCE_PVLIB_HAURWITZ",
    "SOURCE_AUTO",
    # Source lists
    "ALL_SOURCES",
    "ALL_NON_AUTO_SOURCES",
    "DEFAULT_AUTO_FALLBACK_CHAIN",
    # pvlib model identifiers
    "MODEL_INEICHEN",
    "MODEL_SOLIS",
    "MODEL_HAURWITZ",
    "SUPPORTED_MODELS",
    # Defaults
    "DEFAULT_ALBEDO",
    "DEFAULT_TRANSPOSITION_MODEL",
    "DEFAULT_REINDEX_TOLERANCE",
    # Loader column-name constants (untuk testing/debug)
    "COL_TIMESTAMP",
    "COL_PER_WS_FMT",
    "COL_AVG",
    # AlbedoLoader constants
    "DEFAULT_ALBEDO_SHEET",
    "DEFAULT_ALBEDO_TIMESTAMP_COL",
    "DEFAULT_ALBEDO_VALUE_COL",
    "DEFAULT_ALBEDO_TOLERANCE",
]
