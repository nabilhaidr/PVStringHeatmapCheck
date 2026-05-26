"""POA orchestrator yang menggabungkan pyranometer + pvlib clear-sky.

Komposisi:
- :class:`pv_pipeline.poa.loader.PyranometerLoader` -> sumber pengukuran (per-WS, avg).
- :class:`pv_pipeline.poa.pvlib_estimator.PvlibClearSkyEstimator` -> sumber estimasi
  (Ineichen, Simplified Solis, Haurwitz).

Public API:
- ``POAProvider.from_yaml(geometry_path)`` -> instance siap pakai.
- ``get_poa(timestamps, wb_id, source)`` -> ``pd.Series`` untuk satu sumber.
- ``get_poa_all_sources(timestamps, wb_id)`` -> ``pd.DataFrame`` 6 kolom (5 sumber
  + ``auto``) sebagai perbandingan side-by-side.

Sources (string identifier):
    pyranometer_per_ws
    pyranometer_avg
    pvlib_clearsky_ineichen
    pvlib_clearsky_simplified_solis
    pvlib_clearsky_haurwitz
    auto                         (fallback chain per-timestamp)

Auto fallback chain (default, config override via config/m2_config.yaml -> poa.auto_fallback_chain):
    pyranometer_per_ws -> pyranometer_avg -> pvlib_clearsky_ineichen
"""
from __future__ import annotations

import os
import warnings
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

from .loader import PyranometerLoader
from .pvlib_estimator import (
    MODEL_HAURWITZ,
    MODEL_INEICHEN,
    MODEL_SOLIS,
    PvlibClearSkyEstimator,
    SUPPORTED_MODELS as PVLIB_SUPPORTED_MODELS,
)


# Source identifiers (juga dipakai di config/m2_config.yaml -> poa.sources_to_emit).
SOURCE_PYRANOMETER_PER_WS: str = "pyranometer_per_ws"
SOURCE_PYRANOMETER_AVG: str = "pyranometer_avg"
SOURCE_PVLIB_INEICHEN: str = f"pvlib_clearsky_{MODEL_INEICHEN}"
SOURCE_PVLIB_SOLIS: str = f"pvlib_clearsky_{MODEL_SOLIS}"
SOURCE_PVLIB_HAURWITZ: str = f"pvlib_clearsky_{MODEL_HAURWITZ}"
SOURCE_AUTO: str = "auto"

ALL_NON_AUTO_SOURCES: List[str] = [
    SOURCE_PYRANOMETER_PER_WS,
    SOURCE_PYRANOMETER_AVG,
    SOURCE_PVLIB_INEICHEN,
    SOURCE_PVLIB_SOLIS,
    SOURCE_PVLIB_HAURWITZ,
]

ALL_SOURCES: List[str] = ALL_NON_AUTO_SOURCES + [SOURCE_AUTO]

# Default fallback chain untuk source="auto".
DEFAULT_AUTO_FALLBACK_CHAIN: List[str] = [
    SOURCE_PYRANOMETER_PER_WS,
    SOURCE_PYRANOMETER_AVG,
    SOURCE_PVLIB_INEICHEN,
]


def _ensure_yaml() -> None:
    try:
        import yaml  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: PyYAML")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "PyYAML"])


class POAProvider:
    """Orchestrator: menggabungkan PyranometerLoader + PvlibClearSkyEstimator.

    Saat ada source pyranometer yang gagal (xlsx tidak ada / WB tidak ke-mapping),
    provider tetap berfungsi -- source pyranometer akan return Series all-NaN.
    Source pvlib selalu tersedia kalau geometry yaml valid.

    Parameters
    ----------
    pyranometer : PyranometerLoader, optional
        Bila None, pyranometer-* sources akan return all-NaN.
    pvlib_estimator : PvlibClearSkyEstimator, optional
        Bila None, pvlib_clearsky_* sources akan return all-NaN.
    auto_fallback_chain : list of str
        Urutan source untuk source="auto" (default per timestamp).
    """

    def __init__(
        self,
        pyranometer: Optional[PyranometerLoader] = None,
        pvlib_estimator: Optional[PvlibClearSkyEstimator] = None,
        auto_fallback_chain: Optional[List[str]] = None,
    ):
        self.pyranometer: Optional[PyranometerLoader] = pyranometer
        self.pvlib: Optional[PvlibClearSkyEstimator] = pvlib_estimator
        self.auto_fallback_chain: List[str] = list(
            auto_fallback_chain or DEFAULT_AUTO_FALLBACK_CHAIN
        )
        # Validasi: auto chain tidak boleh berisi "auto" (infinite loop).
        if SOURCE_AUTO in self.auto_fallback_chain:
            raise ValueError(
                f"auto_fallback_chain tidak boleh berisi 'auto' (akan rekursi). "
                f"Got: {self.auto_fallback_chain}"
            )
        for src in self.auto_fallback_chain:
            if src not in ALL_NON_AUTO_SOURCES:
                warnings.warn(
                    f"[POAProvider] auto_fallback_chain berisi source unknown: {src!r}",
                    stacklevel=2,
                )

    # ---------- Factory ----------

    @property
    def albedo_loader(self):
        """Convenience accessor untuk AlbedoLoader (kalau di-wire ke pvlib_estimator).

        Returns ``None`` kalau pvlib disabled atau albedo_provider tidak di-set.
        Berguna untuk notebook: ``prov.albedo_loader.get_albedo(timestamps)`` untuk
        plot dynamic albedo terpisah dari POA.
        """
        if self.pvlib is None:
            return None
        return getattr(self.pvlib, "albedo_provider", None)

    @classmethod
    def from_yaml(
        cls,
        geometry_path: str,
        *,
        auto_fallback_chain: Optional[List[str]] = None,
        skip_pyranometer: bool = False,
        skip_pvlib: bool = False,
    ) -> "POAProvider":
        """Bangun provider lengkap dari ``config/site_geometry.yaml``.

        Albedo loader (NSRDB TMY xlsx) auto-loaded saat ``skip_pvlib=False`` dan
        yaml punya ``albedo.xlsx_path``. Akses via ``prov.albedo_loader``.
        """
        if not os.path.exists(geometry_path):
            raise FileNotFoundError(
                f"[POAProvider] geometry yaml {geometry_path!r} not found."
            )

        pyr = None
        if not skip_pyranometer:
            try:
                pyr = PyranometerLoader.from_geometry_yaml(geometry_path)
            except FileNotFoundError as e:
                warnings.warn(
                    f"[POAProvider] pyranometer xlsx tidak ditemukan: {e}. "
                    "Source pyranometer_* akan all-NaN.",
                    stacklevel=2,
                )

        est = None
        if not skip_pvlib:
            est = PvlibClearSkyEstimator.from_geometry_yaml(geometry_path)

        return cls(
            pyranometer=pyr,
            pvlib_estimator=est,
            auto_fallback_chain=auto_fallback_chain,
        )

    # ---------- Single-source query ----------

    def get_poa(
        self,
        timestamps,
        wb_id: str,
        source: str = SOURCE_AUTO,
    ) -> pd.Series:
        """POA Series (W/m^2) untuk satu source identifier.

        ``source="auto"`` akan apply fallback chain per timestamp.
        """
        idx = pd.DatetimeIndex(timestamps)
        if idx.tz is not None:
            idx = idx.tz_localize(None)

        if source == SOURCE_AUTO:
            return self._resolve_auto(idx, wb_id)

        if source not in ALL_NON_AUTO_SOURCES:
            raise ValueError(
                f"[POAProvider] unsupported source={source!r}. "
                f"Supported: {ALL_SOURCES}"
            )

        return self._resolve_single(idx, wb_id, source=source)

    # ---------- Solar position passthrough (Fase 2 Wave 2) ----------

    def get_solar_elevation(self, timestamps) -> pd.Series:
        """Apparent solar elevation (deg) dari pvlib estimator (proxy).

        Untuk filter daylight di detector (Fase 2): ``elevation > 5`` -> matahari
        di atas horizon dengan sun-glare margin. Replace heuristic
        ``hour_cutoff_end`` di detector mask.

        Returns
        -------
        pd.Series
            Indexed by naive timestamps (sama convention dengan get_poa). All-NaN
            kalau pvlib disabled (``skip_pvlib=True`` saat construct).
        """
        idx = pd.DatetimeIndex(timestamps)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        if self.pvlib is None:
            return pd.Series(np.nan, index=idx, dtype="float64", name="solar_elevation_deg")
        return self.pvlib.get_solar_elevation(idx)

    def get_poa_all_sources(
        self,
        timestamps,
        wb_id: str,
        *,
        include_auto: bool = True,
    ) -> pd.DataFrame:
        """Side-by-side semua source untuk perbandingan.

        Returns
        -------
        pd.DataFrame
            Index = timestamps (naive). Columns = 5 source explicit + (optional) "auto".
        """
        idx = pd.DatetimeIndex(timestamps)
        if idx.tz is not None:
            idx = idx.tz_localize(None)

        out: Dict[str, pd.Series] = {}
        for src in ALL_NON_AUTO_SOURCES:
            out[src] = self._resolve_single(idx, wb_id, source=src)
        if include_auto:
            out[SOURCE_AUTO] = self._resolve_auto(idx, wb_id)

        df = pd.DataFrame(out)
        df.index.name = "timestamp"
        return df

    # ---------- Internal resolution ----------

    def _resolve_single(
        self,
        idx: pd.DatetimeIndex,
        wb_id: str,
        *,
        source: str,
    ) -> pd.Series:
        """Hitung satu source (tanpa fallback)."""
        if source == SOURCE_PYRANOMETER_PER_WS:
            if self.pyranometer is None:
                return self._nan_series(idx, source)
            s = self.pyranometer.get_per_ws(idx, wb_id)
        elif source == SOURCE_PYRANOMETER_AVG:
            if self.pyranometer is None:
                return self._nan_series(idx, source)
            s = self.pyranometer.get_avg(idx)
        elif source == SOURCE_PVLIB_INEICHEN:
            if self.pvlib is None:
                return self._nan_series(idx, source)
            s = self.pvlib.estimate(idx, model=MODEL_INEICHEN)
        elif source == SOURCE_PVLIB_SOLIS:
            if self.pvlib is None:
                return self._nan_series(idx, source)
            s = self.pvlib.estimate(idx, model=MODEL_SOLIS)
        elif source == SOURCE_PVLIB_HAURWITZ:
            if self.pvlib is None:
                return self._nan_series(idx, source)
            s = self.pvlib.estimate(idx, model=MODEL_HAURWITZ)
        else:
            raise ValueError(f"[POAProvider] internal: unknown source {source!r}")

        s = s.reindex(idx)
        s.name = source
        return s

    def _resolve_auto(
        self,
        idx: pd.DatetimeIndex,
        wb_id: str,
    ) -> pd.Series:
        """Fallback chain per timestamp: pakai source pertama yang non-NaN."""
        result = pd.Series(np.nan, index=idx, dtype="float64", name=SOURCE_AUTO)
        for src in self.auto_fallback_chain:
            if result.notna().all():
                break
            candidate = self._resolve_single(idx, wb_id, source=src)
            # Isi hanya posisi yang masih NaN.
            fill_mask = result.isna() & candidate.notna()
            result = result.where(~fill_mask, candidate)
        return result

    @staticmethod
    def _nan_series(idx: pd.DatetimeIndex, source: str) -> pd.Series:
        return pd.Series(np.nan, index=idx, dtype="float64", name=source)


if __name__ == "__main__":
    # Smoke: load provider, query 1 hari (2026-05-07 06:00-18:00 setiap jam).
    import sys

    geom = sys.argv[1] if len(sys.argv) > 1 else "config/site_geometry.yaml"
    prov = POAProvider.from_yaml(geom)
    print(f"[POAProvider] pyranometer={'YES' if prov.pyranometer else 'NO'}  "
          f"pvlib={'YES' if prov.pvlib else 'NO'}")
    print(f"  auto_fallback_chain = {prov.auto_fallback_chain}")

    ts = pd.date_range("2026-05-07 06:00", "2026-05-07 18:00", freq="1h")
    for wb in ["WB01", "WB05", "WB10"]:
        df = prov.get_poa_all_sources(ts, wb)
        print(f"\n  WB={wb}  (POA W/m^2 per source):")
        print(df.round(1).to_string())
    print("\n[POAProvider] smoke OK")
