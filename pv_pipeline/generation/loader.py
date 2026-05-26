"""GenerationLoader: load ``IKN Generation.xlsx`` sheet ``Summary (PV)``.

Sheet layout (per inspeksi, header=1):
  - Col A : Date (daily)
  - Col B-K : WB01..WB10 per-WB STS Generation (kWh, daily)
  - Col L : Total STS Generation (kWh) = sum WB01..10
  - Col M : PAE Energy (kWh) from 00.00 to 24.00 (Projected Available Energy)
  - Col N : Generation STS (kWh) = MAX(L, M) gated busbar setpoint
             (busbar1 = WB01-05 < 24300 kW, busbar2 = WB06-10 < 25700 kW)
  - Col O : Busbar 1 (WB 01 - WB 5) propotional setpoint (kW)
  - Col P : Busbar 2 (WB 06 - WB 10) propotional setpoint (kW)

Row 0 di sheet adalah section labels ("STS Generation (kWh)" + "Propotional
Setpoint (kW)"), bukan column header. Loader pakai ``header=1``.

Future-padded NaN: file punya placeholder rows sampai 2029-06-09 (tail).
Loader pertahankan semua row; caller filter date range sesuai kebutuhan.
"""
from __future__ import annotations

import os
import warnings
from typing import Dict, List, Optional

import pandas as pd


DEFAULT_SHEET: str = "Summary (PV)"
DEFAULT_HEADER_ROW: int = 1  # skip section label row (row 0).
DEFAULT_DATE_COL: str = "Date"

# Mapping dari numeric column names di xlsx -> short attribute keys di loader.df.
_COL_RENAME: Dict[str, str] = {
    "Total STS Generation (kWh)": "total_kwh",
    "PAE Energy (kWh) from 00.00 to 24.00": "pae_kwh",
    "Generation STS (kWh)": "generation_sts_kwh",
    "Busbar 1 (WB 01 - WB 5)": "busbar1_setpoint_kw",
    "Busbar 2 (WB 06 - WB 10)": "busbar2_setpoint_kw",
    # Wave 11: Deem Dispatch (kWh) = monthly-agreed loss kWh akibat curtailment.
    # Per Master Context: dispatchable energy loss yang disepakati antar party.
    "Deem Dispatch (kWh)": "deem_dispatch_kwh",
}

# Mapping dari string column names. Wave 11: Curtailment flag ('Yes'/'No').
# Curtailment = grid operator perintah cut-off generation (busbar setpoint reached).
# Untuk PR analysis cross-check: low PR + Curtailment=Yes -> operational, bukan fault.
_COL_RENAME_STRING: Dict[str, str] = {
    "Curtailment": "curtailment_flag",
}

DERIVED_KEYS: List[str] = [
    "total_kwh",
    "pae_kwh",
    "generation_sts_kwh",
    "busbar1_setpoint_kw",
    "busbar2_setpoint_kw",
    "deem_dispatch_kwh",   # Wave 11
    "curtailment_flag",    # Wave 11 (string column)
]

WB_KEYS: List[str] = [f"WB{n:02d}" for n in range(1, 11)]


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


class GenerationLoader:
    """Loader IKN Generation Summary (PV) untuk PR analysis.

    Parameters
    ----------
    xlsx_path : str
        Path ke ``IKN Generation.xlsx``.
    sheet : str, default "Summary (PV)"
    header_row : int, default 1
        Row 0-indexed yang berisi column names (row 0 = section labels).
    date_col : str, default "Date"

    Attributes
    ----------
    df : pd.DataFrame
        Indexed by Date (naive datetime). Columns = WB01..WB10 (per-WB kWh)
        + 5 derived keys (total_kwh, pae_kwh, generation_sts_kwh,
        busbar1_setpoint_kw, busbar2_setpoint_kw).
    xlsx_path : str
    """

    def __init__(
        self,
        xlsx_path: str,
        sheet: str = DEFAULT_SHEET,
        header_row: int = DEFAULT_HEADER_ROW,
        date_col: str = DEFAULT_DATE_COL,
    ):
        if not os.path.exists(xlsx_path):
            raise FileNotFoundError(
                f"[generation] {xlsx_path!r} not found. Cwd={os.getcwd()!r}."
            )
        _ensure_openpyxl()

        raw = pd.read_excel(xlsx_path, sheet_name=sheet, header=header_row)
        if date_col not in raw.columns:
            raise KeyError(
                f"[generation] Sheet {sheet!r} missing column {date_col!r}. "
                f"Found: {list(raw.columns)}"
            )

        raw[date_col] = pd.to_datetime(raw[date_col], errors="coerce")
        raw = raw.dropna(subset=[date_col]).set_index(date_col).sort_index()
        if raw.index.tz is not None:
            raw.index = raw.index.tz_localize(None)

        # Build clean DataFrame.
        data: Dict[str, pd.Series] = {}
        for wb in WB_KEYS:
            if wb in raw.columns:
                data[wb] = pd.to_numeric(raw[wb], errors="coerce")
            else:
                warnings.warn(
                    f"[generation] Column {wb!r} not found in sheet. Skipping.",
                    stacklevel=2,
                )
        for src_col, short_key in _COL_RENAME.items():
            if src_col in raw.columns:
                data[short_key] = pd.to_numeric(raw[src_col], errors="coerce")
            else:
                warnings.warn(
                    f"[generation] Column {src_col!r} not found, "
                    f"{short_key!r} unavailable.",
                    stacklevel=2,
                )

        # Wave 11: string columns (Curtailment flag). Normalize whitespace,
        # preserve NaN for missing dates. Caller bandingkan via `== 'Yes'`.
        for src_col, short_key in _COL_RENAME_STRING.items():
            if src_col in raw.columns:
                norm = raw[src_col].astype("object").map(
                    lambda x: str(x).strip() if pd.notna(x) else None
                )
                data[short_key] = pd.Series(norm.values, index=raw.index, dtype="object")
            else:
                warnings.warn(
                    f"[generation] Column {src_col!r} not found, "
                    f"{short_key!r} unavailable.",
                    stacklevel=2,
                )

        self.df: pd.DataFrame = pd.DataFrame(data, index=raw.index)
        self.df = self.df[~self.df.index.duplicated(keep="first")]
        self.xlsx_path: str = xlsx_path
        self.sheet: str = sheet

    # ---------- IO ----------

    @classmethod
    def from_geometry_yaml(cls, geometry_path: str) -> "GenerationLoader":
        """Convenience: load dari ``config/site_geometry.yaml`` -> ``generation`` section.

        Yaml expected:
            generation:
              xlsx_path: "raw data input/IKN Generation.xlsx"
              sheet: "Summary (PV)"           # optional
              header_row: 1                   # optional
        """
        if not os.path.exists(geometry_path):
            raise FileNotFoundError(
                f"[generation] geometry yaml {geometry_path!r} not found."
            )
        _ensure_yaml()
        import yaml  # noqa: WPS433

        with open(geometry_path, "r", encoding="utf-8") as fp:
            cfg = yaml.safe_load(fp) or {}

        gen_cfg = cfg.get("generation") or {}
        xlsx_value = gen_cfg.get("xlsx_path")
        if xlsx_value is None:
            raise KeyError(
                f"[generation] yaml generation.xlsx_path missing di {geometry_path!r}."
            )
        sheet = str(gen_cfg.get("sheet", DEFAULT_SHEET))
        header_row = int(gen_cfg.get("header_row", DEFAULT_HEADER_ROW))

        return cls(
            xlsx_path=str(xlsx_value),
            sheet=sheet,
            header_row=header_row,
        )

    # ---------- Query API ----------

    def get_daily(
        self,
        date_range,
        column: str,
    ) -> pd.Series:
        """Daily values untuk satu column (WB01..WB10 atau derived key).

        Parameters
        ----------
        date_range : array-like or DatetimeIndex
            Daftar tanggal (akan di-floor ke day untuk match index).
        column : str
            ``"WB01"`` ... ``"WB10"`` atau salah satu DERIVED_KEYS.

        Returns
        -------
        pd.Series
            Indexed by date_range (NaN bila tanggal tidak ada di file).
        """
        if column not in self.df.columns:
            valid = list(self.df.columns)
            raise KeyError(
                f"[generation] Unknown column {column!r}. Valid: {valid}"
            )
        idx = pd.DatetimeIndex(date_range)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        idx_day = idx.floor("D")
        src = self.df[column]
        src = src[~src.index.duplicated(keep="first")]
        out = src.reindex(idx_day)
        out.index = idx
        out.name = column
        return out

    def get_period_total(
        self,
        start_date,
        end_date,
        column: str = "total_kwh",
    ) -> float:
        """Sum daily values [start_date, end_date] inclusive.

        NaN values di-skip. Returns 0.0 kalau range kosong.

        Raises
        ------
        TypeError
            Bila ``column`` adalah string column (e.g. ``curtailment_flag``)
            yang tidak summable.
        """
        if column not in self.df.columns:
            raise KeyError(f"[generation] Unknown column {column!r}.")
        if column in _COL_RENAME_STRING.values():
            raise TypeError(
                f"[generation] {column!r} is a string column, not summable. "
                "Pakai get_daily() lalu count value occurrences."
            )
        start = pd.Timestamp(start_date).floor("D")
        end = pd.Timestamp(end_date).floor("D")
        mask = (self.df.index >= start) & (self.df.index <= end)
        return float(self.df.loc[mask, column].sum(skipna=True))


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "raw data input/IKN Generation.xlsx"
    loader = GenerationLoader(path)
    print(f"[generation] loaded {loader.xlsx_path!r} sheet={loader.sheet!r}")
    print(f"  rows={len(loader.df)} cols={list(loader.df.columns)}")
    print(f"  date range: {loader.df.index.min()} -> {loader.df.index.max()}")

    ts = pd.date_range("2026-05-08", "2026-05-14", freq="D")
    print(f"\n  Sample week ({ts[0].date()} -> {ts[-1].date()}):")
    for col in ["WB01", "WB05", "WB10", "total_kwh", "pae_kwh", "generation_sts_kwh"]:
        vals = loader.get_daily(ts, col)
        print(f"    {col:>22} = {vals.round(0).tolist()}")
    print(f"\n  total_kwh sum (week) = {loader.get_period_total(ts[0], ts[-1], 'total_kwh'):.1f} kWh")
    print("[generation] smoke OK")
