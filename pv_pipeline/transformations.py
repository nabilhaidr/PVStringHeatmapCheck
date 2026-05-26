"""Transformations: ManageObject -> Inverter_ID, PV power, pivot, df_work prep.

Bagian ini murni fungsional terhadap pandas DataFrame. Tidak melakukan I/O,
sehingga aman dipanggil baik dari notebook (Colab/Jupyter) maupun dari
batch script / Airflow worker nanti.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Regex constants (dideklarasi sekali, dipakai berulang)
# ---------------------------------------------------------------------------
PV_CURRENT_RE = re.compile(r"PV\s*0*?(\d+).*current", flags=re.I)
PV_VOLTAGE_RE = re.compile(r"PV\s*0*?(\d+).*voltage", flags=re.I)
PV_POWER_RE = re.compile(r"PV\s*0*?(\d+)\s+Power\(kW\)", flags=re.I)


# ---------------------------------------------------------------------------
# Inverter ID
# ---------------------------------------------------------------------------
def transform_manage_object_to_id(mo: Optional[str]) -> Optional[str]:
    """Transform ``ManageObject`` field to canonical ``Inverter_ID`` (e.g. ``WB02-INV14``).

    Aturan:
    - ``Inv_A_2XX_IKN`` -> WB02-INV<XX>
    - ``Inv_B_2XX_IKN`` -> WB02-INV<XX>
    - ``Inv_A_1XX_IKN`` -> WB01-INV<XX>
    - Format ``WBNN-INVMM`` di-passthrough setelah normalisasi (zero-padded).
    """
    if pd.isna(mo) or mo is None:
        return None
    mo_part = str(mo).split("/")[-1].strip()
    try:
        if "Inv_A_" in mo_part or "Inv_B_" in mo_part:
            number_str = (
                mo_part.split("Inv_A_")[1].split("_")[0]
                if "Inv_A_" in mo_part
                else mo_part.split("Inv_B_")[1].split("_")[0]
            )
            if len(number_str) >= 2:
                first_digit = number_str[0]
                inv_number = number_str[1:].zfill(2)
                if first_digit == "1":
                    wb = "WB01"
                elif first_digit == "2":
                    wb = "WB02"
                else:
                    wb = f"WBX{first_digit}"
                return f"{wb}-INV{inv_number}"
    except Exception:
        pass

    if mo_part.startswith("WB") and "-" in mo_part:
        parts = mo_part.split("-", 1)
        wb_part = parts[0]
        id_part = parts[1].upper().replace("INV", "").strip()
        id_digits = "".join(ch for ch in id_part if ch.isdigit())
        return f"{wb_part}-INV{int(id_digits):02d}" if id_digits else f"{wb_part}-INV00"
    return mo_part


def add_inverter_id(df: pd.DataFrame) -> pd.DataFrame:
    """Tambahkan kolom ``Inverter_ID`` ke DataFrame berdasarkan ``ManageObject``.

    Mengembalikan **copy** dari DataFrame asli supaya tidak mutate input.
    """
    if "ManageObject" not in df.columns:
        raise RuntimeError(
            "ManageObject column is missing. Cannot generate Inverter_ID."
        )
    out = df.copy()
    out["Inverter_ID"] = out["ManageObject"].apply(transform_manage_object_to_id)
    if out["Inverter_ID"].isna().all():
        raise RuntimeError(
            "Inverter_ID could not be created. Check ManageObject values."
        )
    return out


# ---------------------------------------------------------------------------
# PV power (Vstr * Istr / 1000)
# ---------------------------------------------------------------------------
def add_pv_power_columns(df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
    """Compute ``PV{n} Power(kW) = (Vstr * Istr) / 1000`` untuk setiap pasangan PV input.

    Returns
    -------
    (df, pv_cols)
        DataFrame baru dengan kolom power tambahan, dan list nama kolom yang dibuat.

    Notes
    -----
    Kolom ditambahkan dalam satu ``pd.concat`` untuk menghindari fragmentasi
    DataFrame (peringatan PerformanceWarning saat menambah banyak kolom).
    """
    pv_map: dict = {}
    for col_name in df.columns:
        m_cur = PV_CURRENT_RE.search(col_name)
        m_vol = PV_VOLTAGE_RE.search(col_name)
        if m_cur:
            pv_map.setdefault(m_cur.group(1), {})["current"] = col_name
        if m_vol:
            pv_map.setdefault(m_vol.group(1), {})["voltage"] = col_name

    pv_columns_data: dict = {}
    for pv_num, parts in pv_map.items():
        cur_col = parts.get("current")
        vol_col = parts.get("voltage")
        if cur_col and vol_col:
            new_col = f"PV{pv_num} Power(kW)"
            pv_columns_data[new_col] = (
                pd.to_numeric(df[cur_col], errors="coerce")
                * pd.to_numeric(df[vol_col], errors="coerce")
            ) / 1000.0

    if not pv_columns_data:
        raise RuntimeError(
            "No PV power columns could be created. Check PV input column names."
        )

    pv_power_df = pd.DataFrame(pv_columns_data)
    out = pd.concat([df, pv_power_df], axis=1)
    return out, list(pv_columns_data.keys())


def add_total_pv_power(
    df: pd.DataFrame,
    pv_cols: List[str],
    out_col: str = "Total_PV_power_kW",
) -> pd.DataFrame:
    """Sum ``pv_cols`` per row -> ``out_col``. ``min_count=1`` -> NaN bila semuanya NaN."""
    out = df.copy()
    out[out_col] = out[pv_cols].sum(axis=1, min_count=1)
    return out


# ---------------------------------------------------------------------------
# Pivot
# ---------------------------------------------------------------------------
def make_pivot(
    df: pd.DataFrame,
    index: str = "Inverter_ID",
    columns: str = "Start Time",
    values: str = "Total_PV_power_kW",
    aggfunc: str = "mean",
) -> pd.DataFrame:
    """Buat pivot table Inverter_ID x Start Time -> values.

    Mengangkat exception bila pivot kosong supaya caller bisa fail-fast.
    """
    pivot = df.pivot_table(index=index, columns=columns, values=values, aggfunc=aggfunc)
    pivot = pivot.sort_index().reindex(sorted(pivot.columns), axis=1).astype(float)
    if pivot.empty:
        raise RuntimeError(
            "Pivot table is empty. Ensure your data contains valid inverter and time entries."
        )
    return pivot


# ---------------------------------------------------------------------------
# df_work / df_plot preparation (Cell 3 logic)
# ---------------------------------------------------------------------------
def prepare_df_work(
    combined_df: pd.DataFrame,
    pv_max_allowed: int = 28,
) -> Tuple[pd.DataFrame, pd.DataFrame, List[str]]:
    """Siapkan ``df_work`` dan ``df_plot`` untuk visualisasi heatmap.

    Steps
    -----
    1. Tambah kolom ``WB`` dari prefix ``Inverter_ID``.
    2. Parse ``Start Time`` ke datetime.
    3. Pilih kolom ``PVx Power(kW)`` dengan ``x <= pv_max_allowed``.
    4. Hitung ulang ``Total_PV_power_kW`` dari kolom yang dipilih.
    5. Replace 0 dengan NaN pada kolom PV power (membantu kontras heatmap).
    6. Bangun ``df_plot`` = baris dengan Start Time + Total + Inverter_ID valid.
    """
    df_work = combined_df.copy()
    if "Inverter_ID" not in df_work.columns or "Start Time" not in df_work.columns:
        raise KeyError(
            "Source DataFrame must contain 'Start Time' and 'Inverter_ID' columns."
        )

    df_work["WB"] = df_work["Inverter_ID"].str.split("-").str[0]
    df_work["Start Time"] = pd.to_datetime(df_work["Start Time"], errors="coerce")

    pv_cols_all = [
        (int(m.group(1)), c)
        for c in df_work.columns
        if (m := PV_POWER_RE.search(c))
    ]
    pv_keep = [col for num, col in sorted(pv_cols_all) if num <= pv_max_allowed]
    if not pv_keep:
        raise ValueError("No valid PV columns found. Check PV column names in your DataFrame.")

    df_work["Total_PV_power_kW"] = df_work[pv_keep].sum(axis=1, min_count=1)
    df_work[pv_keep] = df_work[pv_keep].replace(0, np.nan)

    df_plot = df_work.dropna(subset=["Start Time", "Total_PV_power_kW", "Inverter_ID"]).copy()
    df_plot["WB"] = df_plot["Inverter_ID"].str.split("-").str[0]

    return df_work, df_plot, pv_keep
