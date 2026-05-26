"""Pyranometer dataset loader untuk PLTS-IKN.

Tanggung jawab:
- Baca pyranometer xlsx (satu file atau multi-year list 2025+2026) sekali
  di constructor -> simpan sebagai DataFrame ber-index ``DatetimeIndex``
  (naive, asumsi WITA, 5-min interval).
- Resolve mapping WB -> WS dari ``ws_to_wb`` config.
- Public method:
    - ``get_per_ws(timestamps, wb_id)`` -> ``pd.Series`` POA (W/m^2) dari WS yang
      ditugaskan untuk WB tersebut.
    - ``get_avg(timestamps)`` -> ``pd.Series`` POA rata-rata 5 WS.

Catatan timezone:
- Sheet ``POA PLTS IKN`` punya kolom ``Date time`` naive (tanpa tz). PLTS-IKN
  beroperasi di WITA = UTC+8. Konsumen di notebook v1.4 saat ini juga pakai
  naive timestamps (dari Huawei xlsx), jadi loader ini *match naive vs naive*.
- Bila konsumen pakai tz-aware timestamps, loader akan strip tz untuk matching.
  Konversi tz dilakukan di luar loader (kalau perlu) — tanggung jawab caller.

Strategi reindex:
- Default ``method='nearest'`` dengan ``tolerance=pd.Timedelta('2min')``.
- 5-min source data + 2-min tolerance -> entry yang lebih dari 2 menit jauh dari
  timestamp request akan NaN (provider boleh fallback ke source lain).
"""
from __future__ import annotations

import os
import warnings
from typing import Dict, List, Optional, Sequence, Union

import pandas as pd


XlsxPathLike = Union[str, Sequence[str]]


# Default kolom names di sheet "POA PLTS IKN" (per inspeksi xlsx).
COL_TIMESTAMP: str = "Date time"
COL_PER_WS_FMT: str = "POA Irradiance (W/m2) WS {ws_num}"
COL_AVG: str = "Rata-rata WS 1 - WS 5"

# Tolerance default untuk reindex (timestamps di luar tolerance -> NaN).
DEFAULT_REINDEX_TOLERANCE: pd.Timedelta = pd.Timedelta("2min")


def _ensure_yaml() -> None:
    try:
        import yaml  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: PyYAML")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "PyYAML"])


def _ensure_openpyxl() -> None:
    """pandas butuh openpyxl untuk read_excel pada .xlsx."""
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: openpyxl")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl"])


def _parse_ws_num(ws_label: str) -> Optional[int]:
    """Convert label ``"WS-1"`` / ``"ws_1"`` / ``"WS 1"`` -> integer 1..5.

    Returns ``None`` kalau parsing gagal.
    """
    import re

    m = re.search(r"(\d+)", str(ws_label))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


class PyranometerLoader:
    """Stateful loader: load xlsx sekali di constructor, query banyak kali.

    Parameters
    ----------
    xlsx_path : str or list of str
        Path ke pyranometer xlsx. Accept single string atau list (multi-year).
        Bila list, semua file di-concat (sort by timestamp, dedup keep-first).
    sheet : str
        Nama sheet (default ``"POA PLTS IKN"``).
    ws_to_wb : Dict[str, List[str]]
        Mapping ``{"WS-1": ["WB08","WB09","WB10"], ...}``.

    Attributes
    ----------
    df : pd.DataFrame
        DataFrame ber-index ``DatetimeIndex`` (naive), sort ascending.
        Kolom = WS labels (mis. ``"WS-1"``) + ``"avg"`` untuk rata-rata.
    wb_to_ws : Dict[str, str]
        Reverse map ``{"WB01": "WS-5", "WB02": "WS-5", ...}``.
    xlsx_paths : List[str]
        Daftar file yang di-load (untuk audit/log).
    """

    def __init__(
        self,
        xlsx_path: XlsxPathLike,
        sheet: str = "POA PLTS IKN",
        ws_to_wb: Optional[Dict[str, List[str]]] = None,
    ):
        # Normalize ke list of paths.
        if isinstance(xlsx_path, (list, tuple)):
            paths = [str(p) for p in xlsx_path]
        else:
            paths = [str(xlsx_path)]
        if not paths:
            raise ValueError("[pyranometer] xlsx_path must be non-empty str or list.")

        for p in paths:
            if not os.path.exists(p):
                raise FileNotFoundError(
                    f"[pyranometer] {p!r} not found. Cwd={os.getcwd()!r}."
                )

        _ensure_openpyxl()

        # Read + concat (multi-year support).
        raw_parts: List[pd.DataFrame] = []
        for p in paths:
            part = pd.read_excel(p, sheet_name=sheet)
            if COL_TIMESTAMP not in part.columns:
                raise KeyError(
                    f"[pyranometer] Sheet {sheet!r} di {p!r} missing column "
                    f"{COL_TIMESTAMP!r}. Found: {list(part.columns)}"
                )
            raw_parts.append(part)
        raw = pd.concat(raw_parts, ignore_index=True) if len(raw_parts) > 1 else raw_parts[0]

        raw[COL_TIMESTAMP] = pd.to_datetime(raw[COL_TIMESTAMP], errors="coerce")
        raw = raw.dropna(subset=[COL_TIMESTAMP]).set_index(COL_TIMESTAMP).sort_index()

        # Dedup duplicate timestamps (cross-file overlap), keep first.
        raw = raw[~raw.index.duplicated(keep="first")]

        # Strip tz (paksa naive) supaya matching naive-vs-naive konsisten.
        if raw.index.tz is not None:
            raw.index = raw.index.tz_localize(None)

        # Build internal DataFrame dengan kolom rapi.
        ws_data: Dict[str, pd.Series] = {}
        for ws_num in range(1, 6):
            src_col = COL_PER_WS_FMT.format(ws_num=ws_num)
            if src_col in raw.columns:
                ws_data[f"WS-{ws_num}"] = pd.to_numeric(raw[src_col], errors="coerce")
            else:
                warnings.warn(
                    f"[pyranometer] Column {src_col!r} not found, WS-{ws_num} unavailable.",
                    stacklevel=2,
                )
        if COL_AVG in raw.columns:
            ws_data["avg"] = pd.to_numeric(raw[COL_AVG], errors="coerce")
        else:
            warnings.warn(
                f"[pyranometer] Column {COL_AVG!r} not found, avg source unavailable.",
                stacklevel=2,
            )

        self.df: pd.DataFrame = pd.DataFrame(ws_data, index=raw.index)
        self.xlsx_paths: List[str] = paths
        # Backwards-compat: keep xlsx_path scalar (first file) for callers
        # yang inspect attribute langsung. Multi-file caller harus pakai xlsx_paths.
        self.xlsx_path: str = paths[0]
        self.sheet: str = sheet

        # Build reverse map WB -> WS (uppercase keys).
        self.ws_to_wb: Dict[str, List[str]] = {}
        self.wb_to_ws: Dict[str, str] = {}
        for ws_label, wb_list in (ws_to_wb or {}).items():
            ws_norm = self._normalize_ws_label(ws_label)
            self.ws_to_wb[ws_norm] = [str(wb).upper() for wb in (wb_list or [])]
            for wb in self.ws_to_wb[ws_norm]:
                if wb in self.wb_to_ws:
                    warnings.warn(
                        f"[pyranometer] WB {wb!r} mapped to multiple WS: "
                        f"{self.wb_to_ws[wb]!r} vs {ws_norm!r}. Using last seen.",
                        stacklevel=2,
                    )
                self.wb_to_ws[wb] = ws_norm

    # ---------- IO helpers ----------

    @classmethod
    def from_geometry_yaml(cls, geometry_path: str) -> "PyranometerLoader":
        """Convenience: load dari ``config/site_geometry.yaml``."""
        if not os.path.exists(geometry_path):
            raise FileNotFoundError(
                f"[pyranometer] geometry yaml {geometry_path!r} not found."
            )
        _ensure_yaml()
        import yaml  # noqa: WPS433

        with open(geometry_path, "r", encoding="utf-8") as fp:
            cfg = yaml.safe_load(fp) or {}

        pyr = cfg.get("pyranometer") or {}
        # Accept str atau list (multi-year). Default ke folder raw data input.
        xlsx_value = pyr.get("xlsx_path", "raw data input/POA PLTS IKN 2026.xlsx")
        if isinstance(xlsx_value, (list, tuple)):
            xlsx_path: XlsxPathLike = [str(p) for p in xlsx_value]
        else:
            xlsx_path = str(xlsx_value)
        sheet = str(pyr.get("sheet", "POA PLTS IKN"))
        ws_to_wb = cfg.get("ws_to_wb") or {}

        return cls(xlsx_path=xlsx_path, sheet=sheet, ws_to_wb=ws_to_wb)

    # ---------- Query API ----------

    def get_per_ws(
        self,
        timestamps,
        wb_id: str,
        *,
        tolerance: pd.Timedelta = DEFAULT_REINDEX_TOLERANCE,
        fallback_to_avg: bool = True,
    ) -> pd.Series:
        """POA dari WS yang ditugaskan untuk ``wb_id``.

        Parameters
        ----------
        timestamps : array-like atau pd.DatetimeIndex
        wb_id : str
        tolerance : pd.Timedelta, default 2-min
            Toleransi reindex-nearest. Source xlsx 5-min, jadi 2-min cocok.
        fallback_to_avg : bool, default True
            **Wave 11 hotfix #4**: kalau kolom per-WS punya NaN di posisi
            requested timestamps (misal WS-1/WS-2 kosong di
            ``POA PLTS IKN 2026.xlsx`` untuk 2026-05-14), fill NaN positions
            tersebut dari kolom ``Rata-rata WS 1 - WS 5`` (avg). Default ON
            supaya inverter di WS yang data-nya hilang (WB05/WB07->WS-2,
            WB08-10->WS-1) tetap dapat POA valid dan tidak ke-fan-out.

            Set ``False`` untuk debugging atau ketika user mau strict
            per-WS-only (e.g., investigate sensor drift per WS).

        Returns
        -------
        pd.Series
            Indexed by ``timestamps``, values W/m^2.

            Series ``.attrs`` (Wave 11 hotfix #4):
              - ``ws_label`` : str, WS yang dipakai (e.g. "WS-2")
              - ``fallback_filled`` : int, jumlah posisi yang diisi dari avg
              - ``fallback_total`` : int, total posisi yang diminta
        """
        idx = self._coerce_index(timestamps)
        wb_norm = str(wb_id).upper()
        ws_label = self.wb_to_ws.get(wb_norm)
        if ws_label is None or ws_label not in self.df.columns:
            warnings.warn(
                f"[pyranometer] No WS mapping for WB={wb_norm!r} (mapping={self.wb_to_ws!r})",
                stacklevel=2,
            )
            empty = pd.Series(
                index=idx, dtype="float64", name=f"poa_per_ws_{wb_norm}"
            )
            empty.attrs["ws_label"] = None
            empty.attrs["fallback_filled"] = 0
            empty.attrs["fallback_total"] = len(idx)
            return empty
        series = self._reindex_nearest(self.df[ws_label], idx, tolerance=tolerance)

        # Wave 11 hotfix #4: per-WS -> avg fallback for NaN positions.
        n_nan_before = int(series.isna().sum())
        n_filled = 0
        if fallback_to_avg and n_nan_before > 0 and "avg" in self.df.columns:
            avg_series = self._reindex_nearest(
                self.df["avg"], idx, tolerance=tolerance,
            )
            # Fill only positions where per-WS is NaN AND avg is not NaN.
            fill_mask = series.isna() & avg_series.notna()
            n_filled = int(fill_mask.sum())
            if n_filled > 0:
                series = series.where(~fill_mask, avg_series)

        series.name = f"poa_per_ws_{wb_norm}"
        series.attrs["ws_label"] = ws_label
        series.attrs["fallback_filled"] = n_filled
        series.attrs["fallback_total"] = len(idx)
        return series

    def get_avg(
        self,
        timestamps,
        *,
        tolerance: pd.Timedelta = DEFAULT_REINDEX_TOLERANCE,
    ) -> pd.Series:
        """POA rata-rata 5 WS (kolom ``Rata-rata WS 1 - WS 5``)."""
        idx = self._coerce_index(timestamps)
        if "avg" not in self.df.columns:
            warnings.warn(
                "[pyranometer] avg column unavailable, returning all-NaN series.",
                stacklevel=2,
            )
            return pd.Series(index=idx, dtype="float64", name="poa_avg")
        series = self._reindex_nearest(self.df["avg"], idx, tolerance=tolerance)
        series.name = "poa_avg"
        return series

    def get_for_ws(
        self,
        timestamps,
        ws_label: str,
        *,
        tolerance: pd.Timedelta = DEFAULT_REINDEX_TOLERANCE,
    ) -> pd.Series:
        """Access langsung per-WS (bypass WB mapping). Berguna untuk debug/plot."""
        idx = self._coerce_index(timestamps)
        ws_norm = self._normalize_ws_label(ws_label)
        if ws_norm not in self.df.columns:
            warnings.warn(
                f"[pyranometer] WS {ws_norm!r} not in df columns {list(self.df.columns)!r}.",
                stacklevel=2,
            )
            return pd.Series(index=idx, dtype="float64", name=f"poa_{ws_norm.lower()}")
        series = self._reindex_nearest(self.df[ws_norm], idx, tolerance=tolerance)
        series.name = f"poa_{ws_norm.lower()}"
        return series

    # ---------- Internal ----------

    @staticmethod
    def _normalize_ws_label(label: str) -> str:
        """Normalize variasi ``ws-1``, ``WS_1``, ``WS 1`` -> ``"WS-1"``."""
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
        """Reindex pakai nearest dengan tolerance window."""
        # ``reindex`` butuh sort dan unique index. Sumber sudah sort di constructor.
        # Buang duplikat timestamps di source (keep first).
        src = series[~series.index.duplicated(keep="first")]
        return src.reindex(idx, method="nearest", tolerance=tolerance)


if __name__ == "__main__":
    # Smoke test: load default geometry yaml, query 1 hari (2026-05-07).
    import sys

    geom = sys.argv[1] if len(sys.argv) > 1 else "config/site_geometry.yaml"
    loader = PyranometerLoader.from_geometry_yaml(geom)
    print(f"[pyranometer] loaded {loader.xlsx_path!r} sheet={loader.sheet!r}")
    print(f"  rows={len(loader.df)}  date_range={loader.df.index.min()} -> {loader.df.index.max()}")
    print(f"  columns={list(loader.df.columns)}")
    print(f"  wb_to_ws sample: WB01->{loader.wb_to_ws.get('WB01')!r}, "
          f"WB05->{loader.wb_to_ws.get('WB05')!r}, WB10->{loader.wb_to_ws.get('WB10')!r}")

    # Pilih 3 timestamp di 2026-05-07 saat noon WITA.
    ts = pd.DatetimeIndex(["2026-05-07 11:00:00", "2026-05-07 12:00:00", "2026-05-07 13:00:00"])
    for wb in ["WB01", "WB05", "WB10"]:
        per_ws = loader.get_per_ws(ts, wb)
        print(f"  WB={wb}: per-WS POA at noon = {per_ws.tolist()}")
    avg = loader.get_avg(ts)
    print(f"  avg POA at noon = {avg.tolist()}")
    print("[pyranometer] smoke OK")
