"""Clear-sky POA estimator pakai pvlib.

Tanggung jawab:
- Init ``pvlib.location.Location`` dari ``site_geometry.yaml``.
- Compute clear-sky GHI/DNI/DHI pakai 3 model:
    * ``ineichen``           (default, butuh Linke turbidity dari pvlib data table)
    * ``simplified_solis``   (algoritma fast, akurat untuk kondisi cerah)
    * ``haurwitz``           (paling sederhana, hanya GHI -> butuh decomposition)
- Transpose ke POA (tilted plane) via Hay-Davies (default) atau isotropic.
- Public method ``estimate(timestamps, model)`` -> ``pd.Series`` POA (W/m^2).
- ``estimate_all_models(timestamps)`` -> DataFrame 3 kolom (untuk perbandingan).

Timezone convention:
- Input timestamps boleh tz-aware atau naive. Naive akan dilocalize ke
  ``site.timezone`` (Asia/Makassar = WITA = UTC+8).
- Output Series berindex *naive* supaya konsisten dengan ``PyranometerLoader``
  dan notebook v1.4 (yang juga naive WITA).

Catatan model:
- ``haurwitz`` hanya menghasilkan ``ghi``. Untuk transposisi ke POA kita perlu
  ``dni`` + ``dhi``. Decompose pakai ``pvlib.irradiance.erbs(ghi, zenith)``.
- Albedo default = 0.20 (typical grass/dirt). User akan kasih nilai aktual nanti.
"""
from __future__ import annotations

import os
import warnings
from typing import Any, Callable, Iterable, List, Optional, Union

import numpy as np
import pandas as pd


DEFAULT_ALBEDO: float = 0.20
# Fase 2: Perez sebagai default (sesuai M2_PV_Performance_Master_Context).
# Perez butuh dni_extra + airmass -- sudah di-pass di _transpose_to_poa.
DEFAULT_TRANSPOSITION_MODEL: str = "perez"

# Type alias: albedo_provider boleh berupa:
#  - None / float: static value (pakai self.albedo).
#  - AlbedoLoader: object dengan ``get_albedo(timestamps) -> pd.Series``.
#  - Callable[[DatetimeIndex], pd.Series]: lambda atau function langsung.
AlbedoProviderType = Optional[Union[Callable[..., Any], object]]

# Model identifier yang publik dipakai di POAProvider.sources.
MODEL_INEICHEN: str = "ineichen"
MODEL_SOLIS: str = "simplified_solis"
MODEL_HAURWITZ: str = "haurwitz"
SUPPORTED_MODELS: List[str] = [MODEL_INEICHEN, MODEL_SOLIS, MODEL_HAURWITZ]


def _ensure_yaml() -> None:
    try:
        import yaml  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: PyYAML")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "PyYAML"])


def _ensure_pvlib() -> None:
    """Pastikan pvlib tersedia (auto-install kalau belum)."""
    try:
        import pvlib  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: pvlib")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pvlib"])


class PvlibClearSkyEstimator:
    """Stateful estimator: init Location sekali, panggil estimate banyak kali.

    Parameters
    ----------
    latitude, longitude : float
        Site coordinates dalam decimal degrees (signed).
    elevation_m : float
        Site elevation (meter di atas permukaan laut).
    timezone : str
        IANA timezone (mis. ``"Asia/Makassar"``).
    tilt_deg : float
        Panel tilt dari horizontal (0 = flat, 90 = vertical).
    azimuth_deg : float
        Panel azimuth (pvlib convention: 0 = North, 90 = East, 180 = South).
    albedo : float, default 0.20
        Ground reflectance (0..1).
    transposition_model : {"haydavies","isotropic","perez"}, default "perez"
        Model transposisi GHI -> POA. Perez = paling akurat untuk tropical clear-sky,
        butuh dni_extra + airmass (sudah di-handle internal).
    """

    def __init__(
        self,
        latitude: float,
        longitude: float,
        elevation_m: float,
        timezone: str,
        tilt_deg: float,
        azimuth_deg: float,
        albedo: float = DEFAULT_ALBEDO,
        transposition_model: str = DEFAULT_TRANSPOSITION_MODEL,
        albedo_provider: AlbedoProviderType = None,
    ):
        _ensure_pvlib()
        import pvlib  # noqa: WPS433

        self.latitude: float = float(latitude)
        self.longitude: float = float(longitude)
        self.elevation_m: float = float(elevation_m)
        self.timezone: str = str(timezone)
        self.tilt_deg: float = float(tilt_deg)
        self.azimuth_deg: float = float(azimuth_deg)
        self.albedo: float = float(albedo)  # static fallback
        self.transposition_model: str = str(transposition_model)
        self.albedo_provider: AlbedoProviderType = albedo_provider

        self.location = pvlib.location.Location(
            latitude=self.latitude,
            longitude=self.longitude,
            tz=self.timezone,
            altitude=self.elevation_m,
            name="PLTS-IKN",
        )

    # ---------- IO helpers ----------

    @classmethod
    def from_geometry_yaml(
        cls,
        geometry_path: str,
        *,
        default_albedo: float = DEFAULT_ALBEDO,
        transposition_model: str = DEFAULT_TRANSPOSITION_MODEL,
        load_albedo_provider: bool = True,
    ) -> "PvlibClearSkyEstimator":
        """Load dari ``config/site_geometry.yaml``.

        Albedo precedence (high -> low):
            1. ``albedo.xlsx_path`` -> auto-construct AlbedoLoader (dinamic per timestamp).
            2. ``site.albedo_pct`` -> static value (heuristic divisi-100 kalau >1).
            3. ``default_albedo`` (parameter ke method ini).

        ``load_albedo_provider=False`` paksa skip AlbedoLoader walaupun yaml
        punya path (testing convenience).
        """
        if not os.path.exists(geometry_path):
            raise FileNotFoundError(
                f"[pvlib_estimator] geometry yaml {geometry_path!r} not found."
            )

        _ensure_yaml()
        import yaml  # noqa: WPS433

        with open(geometry_path, "r", encoding="utf-8") as fp:
            cfg = yaml.safe_load(fp) or {}

        site = cfg.get("site") or {}
        panels = cfg.get("panels") or {}

        if "latitude" not in site or "longitude" not in site:
            raise KeyError(
                f"[pvlib_estimator] {geometry_path!r} missing site.latitude / site.longitude."
            )

        # Static albedo fallback (untuk timestamps yang gak ke-match di provider).
        albedo_pct = site.get("albedo_pct")
        if albedo_pct is None:
            albedo = default_albedo
        else:
            albedo_val = float(albedo_pct)
            albedo = albedo_val / 100.0 if albedo_val > 1.0 else albedo_val

        # Dynamic albedo provider (kalau yaml punya albedo.xlsx_path).
        albedo_provider = None
        if load_albedo_provider:
            try:
                from .albedo_loader import AlbedoLoader

                albedo_provider = AlbedoLoader.from_geometry_yaml(geometry_path)
            except FileNotFoundError as exc:
                warnings.warn(
                    f"[pvlib_estimator] AlbedoLoader file missing: {exc}. "
                    f"Falling back ke static albedo={albedo}.",
                    stacklevel=2,
                )
                albedo_provider = None
            except Exception as exc:  # pragma: no cover (defensive)
                warnings.warn(
                    f"[pvlib_estimator] AlbedoLoader init failed ({exc.__class__.__name__}): "
                    f"{exc}. Falling back ke static albedo={albedo}.",
                    stacklevel=2,
                )
                albedo_provider = None

        return cls(
            latitude=float(site["latitude"]),
            longitude=float(site["longitude"]),
            elevation_m=float(site.get("elevation_m", 0.0)),
            timezone=str(site.get("timezone", "UTC")),
            tilt_deg=float(panels.get("tilt_deg", 0.0)),
            azimuth_deg=float(panels.get("azimuth_deg", 180.0)),
            albedo=albedo,
            transposition_model=transposition_model,
            albedo_provider=albedo_provider,
        )

    # ---------- Public estimate ----------

    def estimate(
        self,
        timestamps,
        model: str = MODEL_INEICHEN,
    ) -> pd.Series:
        """POA (W/m^2) untuk satu model clear-sky.

        Returns
        -------
        pd.Series
            Indexed by timestamps (naive). NaN saat solar position di bawah horizon
            atau model gagal.
        """
        if model not in SUPPORTED_MODELS:
            raise ValueError(
                f"[pvlib_estimator] unsupported model={model!r}. "
                f"Supported: {SUPPORTED_MODELS}"
            )

        idx_naive, idx_aware = self._prepare_index(timestamps)
        ghi, dni, dhi = self._clearsky_components(idx_aware, model=model)
        poa = self._transpose_to_poa(idx_aware, ghi=ghi, dni=dni, dhi=dhi)
        poa = pd.Series(poa.values, index=idx_naive, name=f"poa_pvlib_clearsky_{model}")
        return poa

    def estimate_all_models(
        self,
        timestamps,
    ) -> pd.DataFrame:
        """Side-by-side POA untuk semua model yang didukung."""
        out = {}
        for model in SUPPORTED_MODELS:
            out[f"pvlib_clearsky_{model}"] = self.estimate(timestamps, model=model)
        return pd.DataFrame(out)

    # ---------- Solar position helper (Fase 2 Wave 2) ----------

    def get_solar_elevation(self, timestamps) -> pd.Series:
        """Apparent solar elevation (deg) untuk filter daylight (Fase 2).

        Replace ``hour_cutoff_end`` heuristic dengan physical filter:
        ``elevation > 5 deg`` -> matahari di atas horizon dengan margin sun-glare.

        Parameters
        ----------
        timestamps : array-like or DatetimeIndex
            Naive (akan dilocalize ke self.timezone) atau tz-aware timestamps.

        Returns
        -------
        pd.Series
            Apparent elevation (deg). Indexed by *naive* timestamps untuk
            konsistensi dengan PyranometerLoader + notebook convention.
            Negative saat matahari di bawah horizon (night/twilight).
        """
        idx_naive, idx_aware = self._prepare_index(timestamps)
        solpos = self.location.get_solarposition(idx_aware)
        elev = pd.Series(
            solpos["apparent_elevation"].values,
            index=idx_naive,
            name="solar_elevation_deg",
        )
        return elev

    # ---------- Internal ----------

    def _prepare_index(self, timestamps) -> "tuple[pd.DatetimeIndex, pd.DatetimeIndex]":
        """Kembalikan (naive_idx, tz_aware_idx).

        Aware tz-aware kalau caller kirim naive: localize ke ``self.timezone``.
        """
        idx = pd.DatetimeIndex(timestamps)
        if idx.tz is None:
            idx_aware = idx.tz_localize(self.timezone, ambiguous="NaT", nonexistent="NaT")
            idx_naive = idx
        else:
            idx_aware = idx.tz_convert(self.timezone)
            idx_naive = idx_aware.tz_localize(None)
        return idx_naive, idx_aware

    def _clearsky_components(
        self,
        idx_aware: pd.DatetimeIndex,
        *,
        model: str,
    ) -> "tuple[pd.Series, pd.Series, pd.Series]":
        """Hitung (ghi, dni, dhi) menggunakan model yang dipilih.

        Untuk Haurwitz (cuma GHI), decompose pakai ERBS.
        """
        import pvlib  # noqa: WPS433

        if model == MODEL_HAURWITZ:
            # haurwitz hanya butuh apparent_zenith; return DataFrame dengan kolom 'ghi'.
            solpos = self.location.get_solarposition(idx_aware)
            ghi_df = pvlib.clearsky.haurwitz(solpos["apparent_zenith"])
            ghi = ghi_df["ghi"]
            # Decompose GHI -> (dni, dhi, kt) via ERBS.
            erbs = pvlib.irradiance.erbs(ghi, solpos["zenith"], idx_aware)
            dni = erbs["dni"]
            dhi = erbs["dhi"]
        else:
            cs = self.location.get_clearsky(idx_aware, model=model)
            ghi = cs["ghi"]
            dni = cs["dni"]
            dhi = cs["dhi"]
        return ghi, dni, dhi

    def _resolve_albedo(self, idx_aware: pd.DatetimeIndex):
        """Return albedo per-timestamp (Series) atau scalar fallback.

        Priority:
        1. albedo_provider callable -> Series (NaN gaps di-fill dengan self.albedo).
        2. albedo_provider.get_albedo(...) method -> sama.
        3. Static self.albedo (scalar float).
        """
        if self.albedo_provider is None:
            return self.albedo

        # Provider boleh berupa AlbedoLoader (has .get_albedo) atau plain callable.
        # Pass naive idx supaya konsisten dengan loader convention.
        idx_naive = idx_aware.tz_localize(None) if idx_aware.tz is not None else idx_aware
        try:
            if hasattr(self.albedo_provider, "get_albedo"):
                vals = self.albedo_provider.get_albedo(idx_naive)
            else:
                vals = self.albedo_provider(idx_naive)
        except Exception as exc:  # pragma: no cover (defensive)
            warnings.warn(
                f"[pvlib_estimator] albedo_provider call failed ({exc.__class__.__name__}): "
                f"{exc}. Falling back ke static albedo={self.albedo}.",
                stacklevel=2,
            )
            return self.albedo

        if not isinstance(vals, pd.Series):
            vals = pd.Series(vals, index=idx_naive)
        # NaN gaps -> fill static fallback supaya pvlib tidak NaN-propagate.
        vals = vals.fillna(self.albedo)
        # pvlib menerima array-like; pakai tz-aware index untuk align dengan input.
        vals.index = idx_aware
        return vals

    def _transpose_to_poa(
        self,
        idx_aware: pd.DatetimeIndex,
        *,
        ghi: pd.Series,
        dni: pd.Series,
        dhi: pd.Series,
    ) -> pd.Series:
        import pvlib  # noqa: WPS433

        solpos = self.location.get_solarposition(idx_aware)
        # ``get_total_irradiance`` butuh: surface_tilt, surface_azimuth, solar_zenith,
        # solar_azimuth, dni, ghi, dhi, plus optional dni_extra (haydavies/perez).
        dni_extra = pvlib.irradiance.get_extra_radiation(idx_aware)
        airmass = self.location.get_airmass(idx_aware, solar_position=solpos).get(
            "airmass_relative"
        )

        albedo_resolved = self._resolve_albedo(idx_aware)

        total = pvlib.irradiance.get_total_irradiance(
            surface_tilt=self.tilt_deg,
            surface_azimuth=self.azimuth_deg,
            solar_zenith=solpos["apparent_zenith"],
            solar_azimuth=solpos["azimuth"],
            dni=dni,
            ghi=ghi,
            dhi=dhi,
            albedo=albedo_resolved,
            dni_extra=dni_extra,
            airmass=airmass,
            model=self.transposition_model,
        )
        poa = total["poa_global"]
        # Saat sun di bawah horizon, beberapa model bisa balikin nilai negatif tipis.
        poa = poa.where(poa > 0, 0.0)
        return poa


if __name__ == "__main__":
    # Smoke: load default geometry, estimate ke-3 model untuk noon 2026-05-07.
    import sys

    geom = sys.argv[1] if len(sys.argv) > 1 else "config/site_geometry.yaml"
    est = PvlibClearSkyEstimator.from_geometry_yaml(geom)
    print(f"[pvlib_estimator] location: lat={est.latitude} lon={est.longitude} "
          f"elev={est.elevation_m}m tz={est.timezone}")
    print(f"  tilt={est.tilt_deg}deg azimuth={est.azimuth_deg}deg "
          f"albedo={est.albedo} transposition={est.transposition_model}")

    ts = pd.DatetimeIndex([
        "2026-05-07 06:00:00",
        "2026-05-07 09:00:00",
        "2026-05-07 12:00:00",
        "2026-05-07 15:00:00",
        "2026-05-07 18:00:00",
    ])
    df = est.estimate_all_models(ts)
    print("  POA (W/m^2) per model:")
    print(df.round(1).to_string())
    print("[pvlib_estimator] smoke OK")
