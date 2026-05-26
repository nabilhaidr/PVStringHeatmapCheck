"""Weather dataset loaders untuk PLTS-IKN (Fase 2 Wave 5).

Module ini menyediakan 3 loader untuk variabel cuaca yang dipakai sebagai
input model SAPM cell-temperature (Wave 6) dan PR analysis (Wave 7):

- :class:`AmbientTempLoader`  -- ambient air temperature (C), 4 WS.
- :class:`WindSpeedLoader`    -- wind speed (m/s), 4 WS.
- :class:`WindDirectionLoader` -- wind direction (deg, 0=N, 0-360), 4 WS.

Ketiganya share xlsx layout yang sama (sheet "<Variable> PLTS IKN" + kolom
"Date time" + 4 per-WS values + 1 rata-rata) dan WB mapping yang sama
(ws_to_wb_weather di site_geometry.yaml: 4 WS, WB01/02 piggyback ke WS-4).
"""
from __future__ import annotations

from .loader import (
    AmbientTempLoader,
    WeatherLoaderBase,
    WindDirectionLoader,
    WindSpeedLoader,
)

__all__ = [
    "AmbientTempLoader",
    "WindSpeedLoader",
    "WindDirectionLoader",
    "WeatherLoaderBase",
]
