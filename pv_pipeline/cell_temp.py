"""PV module temperature (Tcell) provider per-WB dengan multi-source comparison.

Sumber: ``PV Module Temperature PLTS IKN.xlsx`` sheet ``"PV Module Temp"``.
Layout 18 kolom:
    A : Datetime (5-min interval, naive WITA)
    B-D : WS-1 sensor 01, 02, 03
    E   : Average WS-1
    F-H : WS-2 sensor 01, 02, 03
    I   : Average WS-2
    J-L : WS-3 sensor 01, 02, 03
    M   : Average WS-3
    N-P : WS-4 sensor 01, 02, 03
    Q   : Average WS-4
    R   : Overall Average (rata-rata 4 WS-average)

WS-5 tidak punya Tcell sensor -> WB01 dan WB02 piggyback ke WS-4 via
``ws_to_wb_tcell`` mapping (berbeda dengan ``ws_to_wb`` untuk POA).

Sources (string identifier):
    measured_per_ws          (dari WS yang ditugaskan via ws_to_wb_tcell)
    measured_overall_avg     (kolom Overall, rata-rata 4 WS)
    auto                     (fallback chain per timestamp)

Auto fallback chain (default): per_ws -> overall_avg -> NaN (strict).
Tidak ada model fallback karena ambient temp dataset belum tersedia.
"""
from __future__ import annotations

import os
import warnings
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


# Default kolom names di sheet "PV Module Temp" (per inspeksi xlsx).
DEFAULT_TCELL_SHEET: str = "PV Module Temp"
DEFAULT_TCELL_TIMESTAMP_COL: str = "Datetime"
# Tcell rata-rata per WS ada di kolom index 4, 8, 12, 16 (0-indexed).
# Overall ada di kolom index 17.
TCELL_WS_AVG_COL_INDICES: Dict[str, int] = {
    "WS-1": 4,
    "WS-2": 8,
    "WS-3": 12,
    "WS-4": 16,
}
TCELL_OVERALL_COL_INDEX: int = 17

DEFAULT_REINDEX_TOLERANCE: pd.Timedelta = pd.Timedelta("2min")

# Source identifiers.
SOURCE_MEASURED_PER_WS: str = "measured_per_ws"
SOURCE_MEASURED_OVERALL_AVG: str = "measured_overall_avg"
SOURCE_SAPM: str = "sapm"                       # Wave 6: Sandia model fallback.
SOURCE_AUTO: str = "auto"

ALL_NON_AUTO_SOURCES: List[str] = [
    SOURCE_MEASURED_PER_WS,
    SOURCE_MEASURED_OVERALL_AVG,
    SOURCE_SAPM,
]
ALL_SOURCES: List[str] = ALL_NON_AUTO_SOURCES + [SOURCE_AUTO]

# Default fallback chain untuk source="auto" (Wave 6: SAPM sebagai last fallback).
DEFAULT_AUTO_FALLBACK_CHAIN: List[str] = [
    SOURCE_MEASURED_PER_WS,
    SOURCE_MEASURED_OVERALL_AVG,
    SOURCE_SAPM,
]

# ---------- SAPM (Sandia Array Performance Model) Wave 6 ----------
# Preset thermal parameters per pvlib.temperature.TEMPERATURE_MODEL_PARAMETERS['sapm'].
# Jinko JKM625N = bifacial Tiger Neo N-type, dual-glass -> "open_rack_glass_glass".
SAPM_PRESETS: Dict[str, Dict[str, float]] = {
    "open_rack_glass_glass":      {"a": -3.47, "b": -0.0594, "deltaT": 3.0},
    "open_rack_glass_polymer":    {"a": -3.56, "b": -0.0750, "deltaT": 3.0},
    "close_mount_glass_glass":    {"a": -2.98, "b": -0.0471, "deltaT": 1.0},
    "insulated_back_glass_polymer": {"a": -2.81, "b": -0.0455, "deltaT": 0.0},
}
DEFAULT_SAPM_MODEL: str = "open_rack_glass_glass"
DEFAULT_SAPM_IRRAD_REF: float = 1000.0


def _ensure_pvlib() -> None:
    """Pastikan pvlib tersedia (Wave 6 SAPM butuh pvlib.temperature.sapm_cell)."""
    try:
        import pvlib  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: pvlib")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pvlib"])


def _resolve_poa_provider_cls():
    """Resolve POAProvider class with alias-namespace fallback (Wave 11 hotfix).

    Standard path: ``from pv_pipeline.poa.provider import POAProvider``.

    Alias-namespace scenario: notebook in main repo loads worktree modules via
    ``pv_pipeline_sprint4.*`` aliases (Cell 4 ``_load_sprint4_modules``). After
    ``_load_sprint4_modules`` returns, the temporary remap of
    ``sys.modules["pv_pipeline.poa"]`` has been restored/popped, so standard
    import fails with ModuleNotFoundError. In that case, scan ``sys.modules``
    for any ``*.poa.provider`` entry that exposes ``POAProvider``.

    Returns
    -------
    class or None
        ``POAProvider`` class kalau ditemukan, else ``None``.
    """
    try:
        from pv_pipeline.poa.provider import POAProvider as _POAProvider
        return _POAProvider
    except ModuleNotFoundError:
        import sys
        for _mod_name, _mod in sys.modules.items():
            if _mod_name.endswith(".poa.provider") and hasattr(_mod, "POAProvider"):
                return _mod.POAProvider
    return None


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


def _parse_ws_label(label: str) -> str:
    """Normalize ``"ws-4" / "WS_4" / "WS 4"`` -> ``"WS-4"``."""
    import re

    m = re.search(r"(\d+)", str(label))
    return f"WS-{m.group(1)}" if m else str(label)


class CellTempProvider:
    """Stateful loader: load xlsx Tcell sekali, query multi-source banyak kali.

    Parameters
    ----------
    xlsx_path : str
        Path ke ``PV Module Temperature PLTS IKN.xlsx``.
    sheet : str
        Default ``"PV Module Temp"`` (bukan sheet pertama xlsx).
    ws_to_wb_tcell : Dict[str, List[str]]
        Mapping untuk Tcell (berbeda dari POA's ``ws_to_wb`` karena WS-5 no Tcell).
        Default: ``{"WS-1":[WB08-10], "WS-2":[WB05,WB07], "WS-3":[WB06],
                    "WS-4":[WB01,WB02,WB03,WB04]}``.
    auto_fallback_chain : list of str
        Urutan source untuk source="auto".

    Attributes
    ----------
    df : pd.DataFrame
        Kolom = WS labels (``"WS-1"`` ... ``"WS-4"``) + ``"overall"``.
        Index = DatetimeIndex (naive, sort ascending).
    wb_to_ws : Dict[str, str]
        Reverse map ``{"WB01": "WS-4", ...}``.
    """

    DEFAULT_WS_TO_WB_TCELL: Dict[str, List[str]] = {
        "WS-1": ["WB08", "WB09", "WB10"],
        "WS-2": ["WB05", "WB07"],
        "WS-3": ["WB06"],
        "WS-4": ["WB01", "WB02", "WB03", "WB04"],  # WB01-02 piggyback (no WS-5 Tcell).
    }

    def __init__(
        self,
        xlsx_path: str,
        sheet: str = DEFAULT_TCELL_SHEET,
        ws_to_wb_tcell: Optional[Dict[str, List[str]]] = None,
        auto_fallback_chain: Optional[List[str]] = None,
        *,
        # Wave 6 SAPM fallback dependencies (all optional).
        poa_provider=None,
        ambient_temp_loader=None,
        wind_speed_loader=None,
        sapm_model: str = DEFAULT_SAPM_MODEL,
        sapm_params: Optional[Dict[str, float]] = None,
    ):
        if not os.path.exists(xlsx_path):
            raise FileNotFoundError(
                f"[cell_temp] {xlsx_path!r} not found. Cwd={os.getcwd()!r}."
            )

        _ensure_openpyxl()

        raw = pd.read_excel(xlsx_path, sheet_name=sheet)
        if raw.shape[1] < 18:
            raise ValueError(
                f"[cell_temp] Sheet {sheet!r} expected >=18 columns, got {raw.shape[1]}. "
                f"Cols: {list(raw.columns)}"
            )

        # Row 0 berisi sub-header sensor (PV Module Temperature 01 WS-1, dst).
        # Drop kalau Datetime di row 0 NaN/non-datetime.
        first_ts = pd.to_datetime(raw.iloc[0, 0], errors="coerce")
        if pd.isna(first_ts):
            raw = raw.iloc[1:].reset_index(drop=True)

        # Coerce timestamp.
        ts = pd.to_datetime(raw.iloc[:, 0], errors="coerce")
        mask = ts.notna()
        raw = raw.loc[mask].copy()
        raw.index = pd.DatetimeIndex(ts[mask].values)
        if raw.index.tz is not None:
            raw.index = raw.index.tz_localize(None)
        raw = raw.sort_index()

        # Build internal DataFrame dengan kolom rapi.
        ws_data: Dict[str, pd.Series] = {}
        for ws_label, col_idx in TCELL_WS_AVG_COL_INDICES.items():
            ws_data[ws_label] = pd.to_numeric(raw.iloc[:, col_idx], errors="coerce")
        ws_data["overall"] = pd.to_numeric(raw.iloc[:, TCELL_OVERALL_COL_INDEX], errors="coerce")

        df = pd.DataFrame(ws_data, index=raw.index)
        # Drop duplicate timestamps (keep first).
        df = df[~df.index.duplicated(keep="first")]

        self.df: pd.DataFrame = df
        self.xlsx_path: str = xlsx_path
        self.sheet: str = sheet

        # WS -> [WB] dan reverse map.
        ws_map = ws_to_wb_tcell or self.DEFAULT_WS_TO_WB_TCELL
        self.ws_to_wb_tcell: Dict[str, List[str]] = {}
        self.wb_to_ws: Dict[str, str] = {}
        for ws_label, wb_list in ws_map.items():
            ws_norm = _parse_ws_label(ws_label)
            self.ws_to_wb_tcell[ws_norm] = [str(wb).upper() for wb in (wb_list or [])]
            for wb in self.ws_to_wb_tcell[ws_norm]:
                if wb in self.wb_to_ws:
                    warnings.warn(
                        f"[cell_temp] WB {wb!r} mapped to multiple WS: "
                        f"{self.wb_to_ws[wb]!r} vs {ws_norm!r}. Using last seen.",
                        stacklevel=2,
                    )
                self.wb_to_ws[wb] = ws_norm

        # Auto fallback chain.
        self.auto_fallback_chain: List[str] = list(
            auto_fallback_chain or DEFAULT_AUTO_FALLBACK_CHAIN
        )
        if SOURCE_AUTO in self.auto_fallback_chain:
            raise ValueError(
                f"auto_fallback_chain tidak boleh berisi 'auto'. Got: {self.auto_fallback_chain}"
            )
        for src in self.auto_fallback_chain:
            if src not in ALL_NON_AUTO_SOURCES:
                warnings.warn(
                    f"[cell_temp] auto_fallback_chain source unknown: {src!r}",
                    stacklevel=2,
                )

        # Wave 6: SAPM fallback dependencies (optional).
        # All-NaN return kalau salah satu provider missing saat SAPM dipanggil.
        self.poa_provider = poa_provider
        self.ambient_temp_loader = ambient_temp_loader
        self.wind_speed_loader = wind_speed_loader
        self.sapm_model: str = str(sapm_model)
        if sapm_params is not None:
            self.sapm_params: Dict[str, float] = dict(sapm_params)
        elif self.sapm_model in SAPM_PRESETS:
            self.sapm_params = dict(SAPM_PRESETS[self.sapm_model])
        else:
            warnings.warn(
                f"[cell_temp] sapm_model {self.sapm_model!r} not in SAPM_PRESETS "
                f"({list(SAPM_PRESETS)}). SAPM source akan return NaN.",
                stacklevel=2,
            )
            self.sapm_params = {}

    # ---------- IO helpers ----------

    @classmethod
    def from_geometry_yaml(
        cls,
        geometry_path: str,
        *,
        enable_sapm: bool = True,
    ) -> "CellTempProvider":
        """Convenience: load dari ``config/site_geometry.yaml``.

        Yaml expected fields:
            cell_temp:
              xlsx_path: "raw data input/PV Module Temperature PLTS IKN.xlsx"
              sheet: "PV Module Temp"
              sapm_model: "open_rack_glass_glass"    # Wave 6 optional
              sapm_params: { a: ..., b: ..., deltaT: ... }  # optional override
            ws_to_wb_tcell:    # optional, override default
              WS-1: [WB08, WB09, WB10]
              ...
            weather:           # Wave 6 SAPM dependencies (auto-loaded if available)
              ambient_temperature: { xlsx_path: [...], ... }
              wind_speed:          { xlsx_path: [...], ... }

        Parameters
        ----------
        enable_sapm : bool, default True
            Bila True, lazy-construct POAProvider + AmbientTempLoader +
            WindSpeedLoader untuk SAPM fallback. Bila salah satu missing
            (file/yaml section), SAPM source akan return NaN (gracefully).
        """
        if not os.path.exists(geometry_path):
            raise FileNotFoundError(
                f"[cell_temp] geometry yaml {geometry_path!r} not found."
            )
        _ensure_yaml()
        import yaml  # noqa: WPS433

        with open(geometry_path, "r", encoding="utf-8") as fp:
            cfg = yaml.safe_load(fp) or {}

        ct_cfg = cfg.get("cell_temp") or {}
        xlsx_path = str(ct_cfg.get("xlsx_path", "raw data input/PV Module Temperature PLTS IKN.xlsx"))
        sheet = str(ct_cfg.get("sheet", DEFAULT_TCELL_SHEET))
        ws_to_wb_tcell = cfg.get("ws_to_wb_tcell") or None
        sapm_model = str(ct_cfg.get("sapm_model", DEFAULT_SAPM_MODEL))
        sapm_params = ct_cfg.get("sapm_params") or None

        # Wave 6: lazy-construct SAPM dependencies (best-effort).
        # Wave 11 hotfix: handle ModuleNotFoundError from aliased-namespace
        # notebook scenarios (Cell 4 _load_sprint4_modules pops pv_pipeline.poa
        # from sys.modules after restore -> "pv_pipeline.poa.provider" no longer
        # importable via standard path). Use _resolve_poa_provider_cls() that
        # also scans sys.modules for *.poa.provider aliases.
        poa_provider = None
        ambient_temp_loader = None
        wind_speed_loader = None
        if enable_sapm:
            POAProvider_cls = _resolve_poa_provider_cls()
            if POAProvider_cls is None:
                warnings.warn(
                    "[cell_temp] SAPM POAProvider not importable "
                    "(neither pv_pipeline.poa.provider nor any aliased "
                    "*.poa.provider in sys.modules). SAPM source akan all-NaN.",
                    stacklevel=2,
                )
            else:
                try:
                    poa_provider = POAProvider_cls.from_yaml(geometry_path)
                except (FileNotFoundError, KeyError) as exc:
                    warnings.warn(
                        f"[cell_temp] SAPM POAProvider unavailable: "
                        f"{exc.__class__.__name__}: {exc}. SAPM source akan all-NaN.",
                        stacklevel=2,
                    )
            try:
                from pv_pipeline.weather import AmbientTempLoader, WindSpeedLoader
                ambient_temp_loader = AmbientTempLoader.from_geometry_yaml(geometry_path)
                wind_speed_loader = WindSpeedLoader.from_geometry_yaml(geometry_path)
            except (FileNotFoundError, KeyError, ModuleNotFoundError) as exc:
                warnings.warn(
                    f"[cell_temp] SAPM weather loaders unavailable: "
                    f"{exc.__class__.__name__}: {exc}. SAPM source akan all-NaN.",
                    stacklevel=2,
                )

        return cls(
            xlsx_path=xlsx_path,
            sheet=sheet,
            ws_to_wb_tcell=ws_to_wb_tcell,
            poa_provider=poa_provider,
            ambient_temp_loader=ambient_temp_loader,
            wind_speed_loader=wind_speed_loader,
            sapm_model=sapm_model,
            sapm_params=sapm_params,
        )

    # ---------- Query API ----------

    def get_tcell(
        self,
        timestamps,
        wb_id: str,
        source: str = SOURCE_AUTO,
        *,
        tolerance: pd.Timedelta = DEFAULT_REINDEX_TOLERANCE,
    ) -> pd.Series:
        """Tcell Series (degrees C) untuk satu source identifier."""
        idx = self._coerce_index(timestamps)
        if source == SOURCE_AUTO:
            return self._resolve_auto(idx, wb_id, tolerance=tolerance)
        if source not in ALL_NON_AUTO_SOURCES:
            raise ValueError(
                f"[cell_temp] unsupported source={source!r}. Supported: {ALL_SOURCES}"
            )
        return self._resolve_single(idx, wb_id, source=source, tolerance=tolerance)

    def get_tcell_all_sources(
        self,
        timestamps,
        wb_id: str,
        *,
        include_auto: bool = True,
        tolerance: pd.Timedelta = DEFAULT_REINDEX_TOLERANCE,
    ) -> pd.DataFrame:
        """Side-by-side semua source untuk perbandingan."""
        idx = self._coerce_index(timestamps)
        out: Dict[str, pd.Series] = {}
        for src in ALL_NON_AUTO_SOURCES:
            out[src] = self._resolve_single(idx, wb_id, source=src, tolerance=tolerance)
        if include_auto:
            out[SOURCE_AUTO] = self._resolve_auto(idx, wb_id, tolerance=tolerance)
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
        tolerance: pd.Timedelta,
    ) -> pd.Series:
        if source == SOURCE_MEASURED_PER_WS:
            wb_norm = str(wb_id).upper()
            ws_label = self.wb_to_ws.get(wb_norm)
            if ws_label is None or ws_label not in self.df.columns:
                return self._nan_series(idx, source)
            s = self._reindex_nearest(self.df[ws_label], idx, tolerance=tolerance)
        elif source == SOURCE_MEASURED_OVERALL_AVG:
            if "overall" not in self.df.columns:
                return self._nan_series(idx, source)
            s = self._reindex_nearest(self.df["overall"], idx, tolerance=tolerance)
        elif source == SOURCE_SAPM:
            s = self._resolve_sapm(idx, wb_id)
        else:
            raise ValueError(f"[cell_temp] internal: unknown source {source!r}")
        s.name = source
        return s

    def _resolve_sapm(
        self,
        idx: pd.DatetimeIndex,
        wb_id: str,
    ) -> pd.Series:
        """SAPM cell temp = pvlib.temperature.sapm_cell(POA, T_air, wind, a, b, deltaT).

        Returns all-NaN bila salah satu dependency missing atau model params kosong.
        Per-WS fallback ke avg untuk ambient + wind kalau per-WS NaN
        (mis. WB05 ambient WS-2 sparse).
        """
        # Dependency check.
        if (
            self.poa_provider is None
            or self.ambient_temp_loader is None
            or self.wind_speed_loader is None
            or not self.sapm_params
        ):
            return self._nan_series(idx, SOURCE_SAPM)

        try:
            # POA aligned ke idx (W/m^2). Source "auto" (chain pyranometer + pvlib).
            poa = self.poa_provider.get_poa(idx, wb_id, source="auto")
            poa = poa.reindex(idx)

            # Ambient temp aligned (oC). Fallback ke avg kalau per-WS NaN.
            t_air = self.ambient_temp_loader.get_per_ws(idx, wb_id)
            if t_air.isna().any():
                t_air_avg = self.ambient_temp_loader.get_avg(idx)
                t_air = t_air.where(t_air.notna(), t_air_avg)

            # Wind speed aligned (m/s). Fallback ke avg.
            wind = self.wind_speed_loader.get_per_ws(idx, wb_id)
            if wind.isna().any():
                wind_avg = self.wind_speed_loader.get_avg(idx)
                wind = wind.where(wind.notna(), wind_avg)
        except Exception as exc:  # pragma: no cover (defensive)
            warnings.warn(
                f"[cell_temp] SAPM dependency query failed (wb={wb_id}): "
                f"{exc.__class__.__name__}: {exc}",
                stacklevel=2,
            )
            return self._nan_series(idx, SOURCE_SAPM)

        _ensure_pvlib()
        from pvlib.temperature import sapm_cell

        # sapm_cell broadcast over arrays; mask NaN inputs supaya output NaN cleanly.
        valid = poa.notna() & t_air.notna() & wind.notna()
        result = pd.Series(np.nan, index=idx, dtype="float64", name=SOURCE_SAPM)
        if valid.any():
            t_cell = sapm_cell(
                poa_global=poa.where(valid).values,
                temp_air=t_air.where(valid).values,
                wind_speed=wind.where(valid).values,
                a=self.sapm_params["a"],
                b=self.sapm_params["b"],
                deltaT=self.sapm_params["deltaT"],
                irrad_ref=DEFAULT_SAPM_IRRAD_REF,
            )
            result = pd.Series(t_cell, index=idx, dtype="float64", name=SOURCE_SAPM)
        return result

    def _resolve_auto(
        self,
        idx: pd.DatetimeIndex,
        wb_id: str,
        *,
        tolerance: pd.Timedelta,
    ) -> pd.Series:
        """Fallback chain per timestamp: pakai source pertama yang non-NaN."""
        result = pd.Series(np.nan, index=idx, dtype="float64", name=SOURCE_AUTO)
        for src in self.auto_fallback_chain:
            if result.notna().all():
                break
            candidate = self._resolve_single(idx, wb_id, source=src, tolerance=tolerance)
            fill_mask = result.isna() & candidate.notna()
            result = result.where(~fill_mask, candidate)
        return result

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

    @staticmethod
    def _nan_series(idx: pd.DatetimeIndex, source: str) -> pd.Series:
        return pd.Series(np.nan, index=idx, dtype="float64", name=source)


if __name__ == "__main__":
    # Smoke: load default xlsx, query 1 hari (2026-05-07 setiap jam).
    import sys

    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = "raw data input/PV Module Temperature PLTS IKN.xlsx"

    prov = CellTempProvider(path)
    print(f"[cell_temp] loaded {prov.xlsx_path!r} sheet={prov.sheet!r}")
    print(f"  rows={len(prov.df)}  date_range={prov.df.index.min()} -> {prov.df.index.max()}")
    print(f"  columns={list(prov.df.columns)}")
    print(f"  WB->WS mapping: WB01->{prov.wb_to_ws.get('WB01')!r}, "
          f"WB02->{prov.wb_to_ws.get('WB02')!r}, WB05->{prov.wb_to_ws.get('WB05')!r}, "
          f"WB10->{prov.wb_to_ws.get('WB10')!r}")

    # Pilih timestamps di 2025-06-01 noon (data 2025-2026 ada).
    ts = pd.date_range("2025-06-01 06:00", "2025-06-01 18:00", freq="3h")
    for wb in ["WB01", "WB05", "WB10"]:
        df = prov.get_tcell_all_sources(ts, wb)
        print(f"\n  WB={wb} (Tcell C per source):")
        print(df.round(2).to_string())
    print("\n[cell_temp] smoke OK")
