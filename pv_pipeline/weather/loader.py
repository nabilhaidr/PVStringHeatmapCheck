"""Weather variable loaders untuk PLTS-IKN (4-WS, multi-year).

Tiga subclass concrete:
- :class:`AmbientTempLoader`   (oC)
- :class:`WindSpeedLoader`     (m/s)
- :class:`WindDirectionLoader` (deg, 0=N, 0-360)

Layout xlsx (sama untuk ketiganya):
- Sheet ``"<Variable> PLTS IKN"`` (mis. ``"Wind Speed PLTS IKN"``).
- Kolom: ``"Date time"`` + ``"<col_per_ws_fmt> WS 1..4"`` + ``"Rata-rata WS 1 - WS 4"``.
- Timestamps naive WITA, 5-min interval.

WB mapping diambil dari ``ws_to_wb_weather`` di site_geometry.yaml
(berbeda dengan ``ws_to_wb`` yang punya WS-5 untuk pyranometer).
Default: WS-1=WB08/09/10, WS-2=WB05/07, WS-3=WB06, WS-4=WB01/02/03/04
(WB01/02 piggyback ke WS-4).

Catatan wind direction:
- Konvensi 0 deg = North, range 0-360. WS-1 sensor missing (data NaN di
  raw xlsx) -- fallback ke avg atau WS-2/3/4 oleh consumer (mis. caller
  bisa pakai ``get_avg`` kalau ``get_per_ws`` NaN).
- Aggregasi rata-rata di xlsx pakai mean arithmetic (kurang akurat untuk
  data angular, tapi cukup untuk SAPM heat-transfer use case).
"""
from __future__ import annotations

import os
import re
import warnings
from typing import Dict, List, Optional, Sequence, Union

import pandas as pd


COL_TIMESTAMP: str = "Date time"
DEFAULT_REINDEX_TOLERANCE: pd.Timedelta = pd.Timedelta("2min")

XlsxPathLike = Union[str, Sequence[str]]


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


def _parse_ws_num(ws_label: str) -> Optional[int]:
    """Convert label ``"WS-1"`` / ``"ws_1"`` / ``"WS 1"`` -> integer 1..4."""
    m = re.search(r"(\d+)", str(ws_label))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


class WeatherLoaderBase:
    """Base class untuk 4-WS weather variable loaders.

    Subclass via class vars:
      - ``DEFAULT_SHEET`` -- nama sheet di xlsx.
      - ``COL_PER_WS_FMT`` -- format string dengan ``{ws_num}`` placeholder.
      - ``COL_AVG`` -- nama kolom rata-rata (biasanya "Rata-rata WS 1 - WS 4").
      - ``YAML_KEY`` -- key di yaml ``weather.<key>`` (mis. "ambient_temperature").

    Parameters
    ----------
    xlsx_path : str or Sequence[str]
        Path single atau list multi-year. List -> concat by timestamp + dedup.
    sheet : str, optional
        Override DEFAULT_SHEET.
    ws_to_wb : Dict[str, List[str]], optional
        Mapping WS label -> list of WB. Default = empty (caller must provide).

    Attributes
    ----------
    df : pd.DataFrame
        Naive DatetimeIndex, columns = ["WS-1","WS-2","WS-3","WS-4","avg"].
    wb_to_ws : Dict[str, str]
        Reverse map WB -> WS label.
    xlsx_paths : List[str]
        List of file paths loaded (multi-file audit).
    """

    DEFAULT_SHEET: str = ""
    COL_PER_WS_FMT: str = ""
    COL_AVG: str = "Rata-rata WS 1 - WS 4"
    YAML_KEY: str = ""
    N_WS: int = 4

    def __init__(
        self,
        xlsx_path: XlsxPathLike,
        sheet: Optional[str] = None,
        ws_to_wb: Optional[Dict[str, List[str]]] = None,
    ):
        # Normalize ke list of paths.
        if isinstance(xlsx_path, (list, tuple)):
            paths = [str(p) for p in xlsx_path]
        else:
            paths = [str(xlsx_path)]
        if not paths:
            raise ValueError(f"[{self.__class__.__name__}] xlsx_path must be non-empty.")
        for p in paths:
            if not os.path.exists(p):
                raise FileNotFoundError(
                    f"[{self.__class__.__name__}] {p!r} not found. Cwd={os.getcwd()!r}."
                )

        sheet_name = sheet or self.DEFAULT_SHEET
        _ensure_openpyxl()

        # Multi-year concat + dedup.
        raw_parts: List[pd.DataFrame] = []
        for p in paths:
            part = pd.read_excel(p, sheet_name=sheet_name)
            if COL_TIMESTAMP not in part.columns:
                raise KeyError(
                    f"[{self.__class__.__name__}] Sheet {sheet_name!r} di {p!r} missing "
                    f"column {COL_TIMESTAMP!r}. Found: {list(part.columns)}"
                )
            raw_parts.append(part)
        raw = pd.concat(raw_parts, ignore_index=True) if len(raw_parts) > 1 else raw_parts[0]

        raw[COL_TIMESTAMP] = pd.to_datetime(raw[COL_TIMESTAMP], errors="coerce")
        raw = raw.dropna(subset=[COL_TIMESTAMP]).set_index(COL_TIMESTAMP).sort_index()
        raw = raw[~raw.index.duplicated(keep="first")]
        if raw.index.tz is not None:
            raw.index = raw.index.tz_localize(None)

        # Build df dengan kolom rapi WS-1..N_WS + avg.
        ws_data: Dict[str, pd.Series] = {}
        for ws_num in range(1, self.N_WS + 1):
            src_col = self.COL_PER_WS_FMT.format(ws_num=ws_num)
            if src_col in raw.columns:
                ws_data[f"WS-{ws_num}"] = pd.to_numeric(raw[src_col], errors="coerce")
            else:
                warnings.warn(
                    f"[{self.__class__.__name__}] Column {src_col!r} not found, "
                    f"WS-{ws_num} unavailable.",
                    stacklevel=2,
                )
        if self.COL_AVG in raw.columns:
            ws_data["avg"] = pd.to_numeric(raw[self.COL_AVG], errors="coerce")
        else:
            warnings.warn(
                f"[{self.__class__.__name__}] Avg column {self.COL_AVG!r} not found.",
                stacklevel=2,
            )

        self.df: pd.DataFrame = pd.DataFrame(ws_data, index=raw.index)
        self.xlsx_paths: List[str] = paths
        self.xlsx_path: str = paths[0]
        self.sheet: str = sheet_name

        # WB -> WS reverse map.
        self.ws_to_wb: Dict[str, List[str]] = {}
        self.wb_to_ws: Dict[str, str] = {}
        for ws_label, wb_list in (ws_to_wb or {}).items():
            ws_norm = self._normalize_ws_label(ws_label)
            self.ws_to_wb[ws_norm] = [str(wb).upper() for wb in (wb_list or [])]
            for wb in self.ws_to_wb[ws_norm]:
                if wb in self.wb_to_ws:
                    warnings.warn(
                        f"[{self.__class__.__name__}] WB {wb!r} mapped to multiple WS: "
                        f"{self.wb_to_ws[wb]!r} vs {ws_norm!r}. Using last seen.",
                        stacklevel=2,
                    )
                self.wb_to_ws[wb] = ws_norm

    # ---------- IO ----------

    @classmethod
    def from_geometry_yaml(cls, geometry_path: str) -> "WeatherLoaderBase":
        """Convenience constructor dari ``config/site_geometry.yaml``.

        Reads ``weather.<YAML_KEY>.xlsx_path`` + ``ws_to_wb_weather``.
        """
        if not cls.YAML_KEY:
            raise NotImplementedError(
                f"{cls.__name__} must set class attribute YAML_KEY."
            )
        if not os.path.exists(geometry_path):
            raise FileNotFoundError(
                f"[{cls.__name__}] geometry yaml {geometry_path!r} not found."
            )

        _ensure_yaml()
        import yaml  # noqa: WPS433

        with open(geometry_path, "r", encoding="utf-8") as fp:
            cfg = yaml.safe_load(fp) or {}

        weather_cfg = (cfg.get("weather") or {}).get(cls.YAML_KEY) or {}
        xlsx_value = weather_cfg.get("xlsx_path")
        if xlsx_value is None:
            raise KeyError(
                f"[{cls.__name__}] yaml weather.{cls.YAML_KEY}.xlsx_path missing."
            )
        if isinstance(xlsx_value, (list, tuple)):
            xlsx_path: XlsxPathLike = [str(p) for p in xlsx_value]
        else:
            xlsx_path = str(xlsx_value)
        sheet = weather_cfg.get("sheet")
        ws_to_wb = cfg.get("ws_to_wb_weather") or {}

        return cls(xlsx_path=xlsx_path, sheet=sheet, ws_to_wb=ws_to_wb)

    # ---------- Query ----------

    def get_per_ws(
        self,
        timestamps,
        wb_id: str,
        *,
        tolerance: pd.Timedelta = DEFAULT_REINDEX_TOLERANCE,
    ) -> pd.Series:
        """Value dari WS yang ditugaskan untuk ``wb_id`` (NaN bila tidak match)."""
        idx = self._coerce_index(timestamps)
        wb_norm = str(wb_id).upper()
        ws_label = self.wb_to_ws.get(wb_norm)
        if ws_label is None or ws_label not in self.df.columns:
            warnings.warn(
                f"[{self.__class__.__name__}] No WS mapping for WB={wb_norm!r} "
                f"(mapping={self.wb_to_ws!r})",
                stacklevel=2,
            )
            return pd.Series(
                index=idx, dtype="float64",
                name=f"{self.YAML_KEY}_per_ws_{wb_norm}",
            )
        series = self._reindex_nearest(self.df[ws_label], idx, tolerance=tolerance)
        series.name = f"{self.YAML_KEY}_per_ws_{wb_norm}"
        return series

    def get_avg(
        self,
        timestamps,
        *,
        tolerance: pd.Timedelta = DEFAULT_REINDEX_TOLERANCE,
    ) -> pd.Series:
        """Rata-rata WS 1-4 (kolom xlsx "Rata-rata WS 1 - WS 4")."""
        idx = self._coerce_index(timestamps)
        if "avg" not in self.df.columns:
            warnings.warn(
                f"[{self.__class__.__name__}] avg column unavailable.",
                stacklevel=2,
            )
            return pd.Series(index=idx, dtype="float64", name=f"{self.YAML_KEY}_avg")
        series = self._reindex_nearest(self.df["avg"], idx, tolerance=tolerance)
        series.name = f"{self.YAML_KEY}_avg"
        return series

    def get_for_ws(
        self,
        timestamps,
        ws_label: str,
        *,
        tolerance: pd.Timedelta = DEFAULT_REINDEX_TOLERANCE,
    ) -> pd.Series:
        """Direct access per WS (bypass WB mapping)."""
        idx = self._coerce_index(timestamps)
        ws_norm = self._normalize_ws_label(ws_label)
        if ws_norm not in self.df.columns:
            warnings.warn(
                f"[{self.__class__.__name__}] WS {ws_norm!r} not in df columns "
                f"{list(self.df.columns)!r}.",
                stacklevel=2,
            )
            return pd.Series(
                index=idx, dtype="float64",
                name=f"{self.YAML_KEY}_{ws_norm.lower()}",
            )
        series = self._reindex_nearest(self.df[ws_norm], idx, tolerance=tolerance)
        series.name = f"{self.YAML_KEY}_{ws_norm.lower()}"
        return series

    # ---------- Internal ----------

    @staticmethod
    def _normalize_ws_label(label: str) -> str:
        num = _parse_ws_num(label)
        if num is None:
            return str(label)
        return f"WS-{num}"

    @staticmethod
    def _coerce_index(timestamps) -> pd.DatetimeIndex:
        idx = pd.DatetimeIndex(timestamps)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        return idx

    @staticmethod
    def _reindex_nearest(
        series: pd.Series,
        idx: pd.DatetimeIndex,
        *,
        tolerance: pd.Timedelta,
    ) -> pd.Series:
        src = series[~series.index.duplicated(keep="first")]
        return src.reindex(idx, method="nearest", tolerance=tolerance)


# ---------- Concrete subclasses ----------


class AmbientTempLoader(WeatherLoaderBase):
    """Ambient air temperature loader (oC)."""

    DEFAULT_SHEET = "Ambient Temperature PLTS IKN"
    COL_PER_WS_FMT = "Ambient Temp (oC) WS {ws_num}"
    COL_AVG = "Rata-rata WS 1 - WS 4"
    YAML_KEY = "ambient_temperature"


class WindSpeedLoader(WeatherLoaderBase):
    """Wind speed loader (m/s)."""

    DEFAULT_SHEET = "Wind Speed PLTS IKN"
    COL_PER_WS_FMT = "Wind Speed (m/s) WS {ws_num}"
    COL_AVG = "Rata-rata WS 1 - WS 4"
    YAML_KEY = "wind_speed"


class WindDirectionLoader(WeatherLoaderBase):
    """Wind direction loader (deg, 0=North, 0-360).

    Catatan: WS-1 sensor wind direction sering missing (NaN di raw xlsx).
    Consumer dapat fallback ke ``get_avg`` atau ``get_for_ws("WS-2")``.
    """

    DEFAULT_SHEET = "Wind Direction PLTS IKN"
    COL_PER_WS_FMT = "Wind Direction (o) WS {ws_num}"
    COL_AVG = "Rata-rata WS 1 - WS 4"
    YAML_KEY = "wind_direction"


if __name__ == "__main__":
    import sys

    geom = sys.argv[1] if len(sys.argv) > 1 else "config/site_geometry.yaml"
    for cls in (AmbientTempLoader, WindSpeedLoader, WindDirectionLoader):
        try:
            loader = cls.from_geometry_yaml(geom)
        except Exception as exc:
            print(f"[{cls.__name__}] failed: {exc.__class__.__name__}: {exc}")
            continue
        print(f"[{cls.__name__}] xlsx_paths={loader.xlsx_paths}")
        print(f"  rows={len(loader.df)} cols={list(loader.df.columns)}")
        print(f"  range={loader.df.index.min()} -> {loader.df.index.max()}")
        ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
        for wb in ("WB01", "WB05", "WB10"):
            val = loader.get_per_ws(ts, wb).iloc[0]
            print(f"  WB={wb}: {val}")
        print()
    print("[weather] smoke OK")
