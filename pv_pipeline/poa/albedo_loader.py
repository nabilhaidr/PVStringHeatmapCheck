"""Surface albedo time-series loader untuk PLTS-IKN.

Sumber: ``Surface Albedo Forecast TMY NSRDB PLTS IKN.xlsx``
- NSRDB Typical Meteorological Year (TMY) 5-year average forecast.
- Tidak ada albedometer di site -> ini *forecast*, bukan pengukuran aktual.
- Resolusi 30 menit, site-wide single value (bukan per-WB).

Tanggung jawab:
- Load xlsx sekali di constructor -> Series ber-index ``DatetimeIndex`` (naive WITA).
- ``get_albedo(timestamps)`` -> reindex nearest dengan tolerance 30 menit
  (cocok dengan source resolution; nilai berubah lambat jadi smooth interpolation
  tidak perlu).
- ``__call__`` alias supaya bisa di-pass sebagai callable ke
  ``PvlibClearSkyEstimator(albedo_provider=...)``.

Catatan signing:
- Datasheet NSRDB pakai fraction (0..1), bukan percent. Di yaml field
  ``site.albedo_pct`` heuristic divisi-100 dipertahankan utk backward compat;
  loader ini selalu treat sebagai fraction.
"""
from __future__ import annotations

import os
import warnings
from typing import Optional

import pandas as pd


# Default kolom untuk xlsx NSRDB albedo (Sheet1).
DEFAULT_ALBEDO_SHEET: str = "Sheet1"
DEFAULT_ALBEDO_TIMESTAMP_COL: str = "Date/Time"
DEFAULT_ALBEDO_VALUE_COL: str = "Surface Albedo"
DEFAULT_ALBEDO_TOLERANCE: pd.Timedelta = pd.Timedelta("30min")


def _ensure_yaml() -> None:
    try:
        import yaml  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: PyYAML")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "PyYAML"])


def _ensure_openpyxl() -> None:
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: openpyxl")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl"])


class AlbedoLoader:
    """Stateful albedo loader: load xlsx sekali, query banyak kali.

    Parameters
    ----------
    xlsx_path : str
        Path ke ``Surface Albedo Forecast TMY NSRDB PLTS IKN.xlsx``.
    sheet : str
        Sheet name (default ``"Sheet1"``).
    timestamp_col : str
        Nama kolom timestamp (default ``"Date/Time"``).
    value_col : str
        Nama kolom albedo (default ``"Surface Albedo"``).

    Attributes
    ----------
    series : pd.Series
        Albedo time-series (naive DatetimeIndex, sort ascending).
    xlsx_path : str
        Path source untuk debug.
    """

    def __init__(
        self,
        xlsx_path: str,
        sheet: str = DEFAULT_ALBEDO_SHEET,
        timestamp_col: str = DEFAULT_ALBEDO_TIMESTAMP_COL,
        value_col: str = DEFAULT_ALBEDO_VALUE_COL,
    ):
        if not os.path.exists(xlsx_path):
            raise FileNotFoundError(
                f"[albedo] {xlsx_path!r} not found. Cwd={os.getcwd()!r}."
            )

        _ensure_openpyxl()

        raw = pd.read_excel(xlsx_path, sheet_name=sheet)
        if timestamp_col not in raw.columns:
            raise KeyError(
                f"[albedo] Sheet {sheet!r} missing timestamp column {timestamp_col!r}. "
                f"Found: {list(raw.columns)}"
            )
        if value_col not in raw.columns:
            raise KeyError(
                f"[albedo] Sheet {sheet!r} missing value column {value_col!r}. "
                f"Found: {list(raw.columns)}"
            )

        raw[timestamp_col] = pd.to_datetime(raw[timestamp_col], errors="coerce")
        raw = raw.dropna(subset=[timestamp_col]).set_index(timestamp_col).sort_index()
        if raw.index.tz is not None:
            raw.index = raw.index.tz_localize(None)

        series = pd.to_numeric(raw[value_col], errors="coerce")
        # Buang duplikat timestamps (keep first).
        series = series[~series.index.duplicated(keep="first")]
        series.name = "albedo"

        self.series: pd.Series = series
        self.xlsx_path: str = xlsx_path
        self.sheet: str = sheet
        self.value_col: str = value_col

    # ---------- IO helpers ----------

    @classmethod
    def from_geometry_yaml(cls, geometry_path: str) -> Optional["AlbedoLoader"]:
        """Convenience: load dari ``config/site_geometry.yaml``.

        Returns ``None`` kalau ``albedo.xlsx_path`` tidak di-set di yaml.
        """
        if not os.path.exists(geometry_path):
            raise FileNotFoundError(
                f"[albedo] geometry yaml {geometry_path!r} not found."
            )
        _ensure_yaml()
        import yaml  # noqa: WPS433

        with open(geometry_path, "r", encoding="utf-8") as fp:
            cfg = yaml.safe_load(fp) or {}

        albedo_cfg = cfg.get("albedo") or {}
        xlsx_path = albedo_cfg.get("xlsx_path")
        if not xlsx_path:
            return None
        sheet = str(albedo_cfg.get("sheet", DEFAULT_ALBEDO_SHEET))
        ts_col = str(albedo_cfg.get("timestamp_col", DEFAULT_ALBEDO_TIMESTAMP_COL))
        val_col = str(albedo_cfg.get("value_col", DEFAULT_ALBEDO_VALUE_COL))

        return cls(
            xlsx_path=str(xlsx_path),
            sheet=sheet,
            timestamp_col=ts_col,
            value_col=val_col,
        )

    # ---------- Query API ----------

    def get_albedo(
        self,
        timestamps,
        *,
        tolerance: pd.Timedelta = DEFAULT_ALBEDO_TOLERANCE,
    ) -> pd.Series:
        """Albedo Series (fraction 0..1) untuk timestamps yang diminta.

        Returns
        -------
        pd.Series
            Indexed by ``timestamps`` (naive). NaN bila tidak ada match dalam
            ``tolerance``.
        """
        idx = pd.DatetimeIndex(timestamps)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        out = self.series.reindex(idx, method="nearest", tolerance=tolerance)
        out.name = "albedo"
        return out

    # Callable alias supaya pas di-pass sebagai albedo_provider ke estimator.
    def __call__(self, timestamps) -> pd.Series:
        return self.get_albedo(timestamps)


if __name__ == "__main__":
    # Smoke: load default xlsx, query 1 hari (2026-05-07 setiap 30 menit).
    import sys

    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = "raw data input/Surface Albedo Forecast TMY NSRDB PLTS IKN.xlsx"

    loader = AlbedoLoader(path)
    print(f"[albedo] loaded {loader.xlsx_path!r} sheet={loader.sheet!r}")
    print(f"  rows={len(loader.series)}")
    print(f"  date_range={loader.series.index.min()} -> {loader.series.index.max()}")
    print(f"  value range: min={loader.series.min():.3f}  max={loader.series.max():.3f}  "
          f"mean={loader.series.mean():.3f}")
    print(f"  non-NaN count: {loader.series.notna().sum()} / {len(loader.series)}")

    # Query 1 hari noon timestamps every 30 minutes.
    ts = pd.date_range("2026-05-07 06:00", "2026-05-07 18:00", freq="30min")
    out = loader.get_albedo(ts)
    print(f"\n  Sample query 2026-05-07 daylight (every 30min):")
    print(out.to_string())
    print("[albedo] smoke OK")
