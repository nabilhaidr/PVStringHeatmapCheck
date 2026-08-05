"""Loader rekap manual cleaning PV module + mapping DC cable ST->PV.

Sumber data (raw data input/):

1. ``Report & Schedule Cleaning PLTS IKN.xlsx`` -- checklist cleaning per
   string per tanggal (sel TRUE). Sheet per zona: ``STS{n}`` (boleh dengan
   suffix tahun, mis. ``STS1 (2025)``); STS n == WB-0n. Layout per sheet:
   beberapa baris banner, lalu baris header ``Zone | Inverter | String |
   <kolom tanggal...>``. Kolom Inverter berisi ``INV-{sts}{nn}`` (INV-101 =
   STS1 inverter 01, INV-1015 = STS10 inverter 15) dan hanya terisi di baris
   pertama tiap blok (forward-fill ke bawah). Kolom String berisi ``ST01..``.

2. ``List of DC Cables 0411.xls`` (== PDF IKN-EE-PP-ML-003, sudah
   di-spot-check identik) -- sheet FINAL, kolom G ``WB03INV01ST01+`` (nomor
   string sisi lapangan, dipakai checklist cleaning) -> kolom H
   ``WB03INV01M3PV10`` (nomor PV sisi inverter, dipakai Huawei xlsx /
   combined_df). HANYA WB03..WB10 -- di WB01/WB02 nomor ST == nomor PV
   (identity). Tiap string muncul 2x (+/-) dengan tujuan sama.

Konsumen: M2aSoiling (artifact ManualCleaning + klasifikasi CleaningEvents
SRR manual-vs-hujan) via config m2a_soiling.cleaning_report_path +
dc_cable_list_path.
"""
from __future__ import annotations

import re
import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

SHEET_STS_RE = re.compile(r"^STS\s*(\d+)", re.IGNORECASE)
INV_RE = re.compile(r"INV[-\s]*(\d+)", re.IGNORECASE)
ST_RE = re.compile(r"^ST\s*(\d+)$", re.IGNORECASE)
CABLE_SRC_RE = re.compile(r"^WB(\d{2})INV(\d+)ST(\d+)[+-]$")
CABLE_DST_RE = re.compile(r"^WB(\d{2})INV(\d+)M(\d+)PV(\d+)$")

IDENTITY_ST_EQ_PV_WBS = {1, 2}  # WB01/WB02: nomor string == nomor PV


def _ensure_xlrd() -> None:
    """Legacy .xls butuh xlrd (mirror pola _ensure_rdtools)."""
    try:
        import xlrd  # noqa: F401
    except ImportError:  # pragma: no cover
        import subprocess
        import sys
        print("Installing missing package: xlrd (untuk .xls DC cable list)")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "xlrd"])


CABLE_MAP_COLUMNS: List[str] = ["wb", "inv", "st", "mppt", "pv",
                                "length_m", "vdrop_pct"]
CABLE_METRIC_COLUMNS: List[str] = ["inverter_id", "pv", "length_m", "vdrop_pct"]


def parse_dc_cable_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Parse frame src/dst (+ opsional panjang & vdrop) -> DataFrame
    [wb, inv, st, mppt, pv, length_m, vdrop_pct].

    Kolom 3 dan 4 (panjang meter, voltage drop) opsional: pemanggil yang
    hanya menyediakan src/dst tetap dapat kedua kolom itu berisi NA.
    Voltage drop di sumber Excel berformat persen (fraksi 0,0179 = 1,79%)
    dan hanya terisi di baris polaritas '+', jadi saat baris +/- disatukan
    diambil nilai pertama yang tidak kosong.

    Baris dengan WB/inverter sumber != tujuan (cross-inverter, kemungkinan
    typo desain) di-skip dengan warning.
    """
    n = len(frame)
    blank = pd.Series([np.nan] * n, index=frame.index)
    lengths = frame.iloc[:, 2] if frame.shape[1] > 2 else blank
    vdrops = frame.iloc[:, 3] if frame.shape[1] > 3 else blank

    rows: List[dict] = []
    mismatched: List[Tuple[str, str]] = []
    for src, dst, length, vdrop in zip(
        frame.iloc[:, 0], frame.iloc[:, 1], lengths, vdrops
    ):
        ms = CABLE_SRC_RE.match(str(src).strip())
        if not ms:
            continue
        md = CABLE_DST_RE.match(str(dst).strip())
        if not md or (ms.group(1), ms.group(2)) != (md.group(1), md.group(2)):
            mismatched.append((str(src).strip(), str(dst).strip()))
            continue
        ln = pd.to_numeric(length, errors="coerce")
        vd = pd.to_numeric(vdrop, errors="coerce")
        # vdrop diturunkan dari panjang (korelasi r=1,0 di file asli), jadi
        # baris tanpa panjang menulis vdrop 0 yang berarti BELUM DIISI.
        # Dibiarkan 0 ia akan terbaca sebagai "kabel sempurna".
        rows.append({
            "wb": int(ms.group(1)),
            "inv": int(ms.group(2)),
            "st": int(ms.group(3)),
            "mppt": int(md.group(3)),
            "pv": int(md.group(4)),
            "length_m": ln,
            "vdrop_pct": vd * 100.0 if pd.notna(vd) and pd.notna(ln) else np.nan,
        })
    if mismatched:
        warnings.warn(
            f"[cleaning_report] {len(mismatched)} baris DC cable di-skip "
            f"(sumber != tujuan, mis. {mismatched[0]}).",
            stacklevel=2,
        )
    out = pd.DataFrame(rows, columns=CABLE_MAP_COLUMNS)
    if out.empty:
        return out
    return out.groupby(
        ["wb", "inv", "st", "mppt", "pv"], as_index=False, sort=False,
    ).first()


def load_dc_cable_map(path: str) -> pd.DataFrame:
    """Baca List of DC Cables (G=src, H=dst, I=panjang m, J=voltage drop)."""
    if str(path).lower().endswith(".xls"):
        _ensure_xlrd()
    frame = pd.read_excel(path, sheet_name=0, header=None, usecols=[6, 7, 8, 9])
    return parse_dc_cable_frame(frame)


def build_cable_metrics(cable_map: pd.DataFrame) -> pd.DataFrame:
    """Metrik kabel per string dgn kunci konsumen: ``WB03-INV01`` + pv int.

    Dipakai sebagai kolom BUKTI (bukan koreksi): voltage drop DC tidak
    diterjemahkan lurus ke arus terukur di terminal inverter karena MPPT
    bekerja di level array, jadi angkanya disajikan apa adanya dan analis
    yang menilai. PV ganda dalam satu inverter (typo penomoran di as-built)
    diambil kemunculan pertama supaya kunci join tetap unik.
    """
    if cable_map is None or cable_map.empty:
        return pd.DataFrame(columns=CABLE_METRIC_COLUMNS)
    wb = cable_map["wb"].astype(int).map("WB{:02d}".format)
    inv = cable_map["inv"].astype(int).map("-INV{:02d}".format)
    out = pd.DataFrame({
        "inverter_id": wb + inv,
        "pv": cable_map["pv"].astype(int),
        "length_m": pd.to_numeric(cable_map["length_m"], errors="coerce"),
        "vdrop_pct": pd.to_numeric(cable_map["vdrop_pct"], errors="coerce"),
    })
    return out.drop_duplicates(
        subset=["inverter_id", "pv"], ignore_index=True,
    )[CABLE_METRIC_COLUMNS]


def build_st_to_pv(cable_map: pd.DataFrame) -> Dict[Tuple[int, int, int], Tuple[int, Optional[int]]]:
    """Dict (wb, inv, st) -> (pv, mppt)."""
    return {
        (int(r.wb), int(r.inv), int(r.st)): (int(r.pv), int(r.mppt))
        for r in cable_map.itertuples(index=False)
    }


def _find_header_row(df: pd.DataFrame, max_scan: int = 10) -> Optional[int]:
    for i in range(min(max_scan, len(df))):
        if str(df.iat[i, 2]).strip().lower() == "string":
            return i
    return None


def _is_true(value) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return isinstance(value, str) and value.strip().upper() == "TRUE"


def load_cleaning_report(
    path: str,
    st_to_pv: Optional[Dict[Tuple[int, int, int], Tuple[int, Optional[int]]]] = None,
) -> pd.DataFrame:
    """Parse checklist cleaning -> long DataFrame per (tanggal, string).

    Kolom output: date (normalized), inverter_id (``WBxx-INVyy``), wb, inv,
    st, pv, mppt. ``pv`` = nomor PV sisi inverter: identity untuk WB01/WB02,
    dari ``st_to_pv`` untuk WB lain (NaN + warning kalau mapping tidak ada).
    """
    st_to_pv = st_to_pv or {}
    xl = pd.ExcelFile(path)
    events: List[dict] = []
    unmapped: set = set()

    for sheet in xl.sheet_names:
        m_sheet = SHEET_STS_RE.match(str(sheet).strip())
        if not m_sheet:
            continue
        sheet_sts = int(m_sheet.group(1))
        df = pd.read_excel(path, sheet_name=sheet, header=None)
        hdr_row = _find_header_row(df)
        if hdr_row is None:
            warnings.warn(
                f"[cleaning_report] sheet {sheet!r}: header 'String' tidak "
                "ditemukan; sheet di-skip.",
                stacklevel=2,
            )
            continue
        dates = pd.to_datetime(df.iloc[hdr_row, 3:], errors="coerce")
        inverter_raw = df.iloc[hdr_row + 1:, 1].ffill()
        strings_raw = df.iloc[hdr_row + 1:, 2]
        body = df.iloc[hdr_row + 1:, 3:]

        for offset in range(len(body)):
            m_st = ST_RE.match(str(strings_raw.iloc[offset]).strip())
            m_inv = INV_RE.search(str(inverter_raw.iloc[offset]))
            if not m_st or not m_inv:
                continue
            digits = int(m_inv.group(1))
            wb, inv_no = digits // 100, digits % 100
            if wb != sheet_sts:
                warnings.warn(
                    f"[cleaning_report] sheet {sheet!r}: inverter "
                    f"INV-{digits} tidak cocok dengan STS{sheet_sts}; "
                    "pakai nomor dari nama inverter.",
                    stacklevel=2,
                )
            st = int(m_st.group(1))
            row_vals = body.iloc[offset]
            true_cols = [j for j in range(len(row_vals)) if _is_true(row_vals.iloc[j])]
            if not true_cols:
                continue
            if wb in IDENTITY_ST_EQ_PV_WBS:
                pv, mppt = st, None
            else:
                pv, mppt = st_to_pv.get((wb, inv_no, st), (None, None))
                if pv is None:
                    unmapped.add((wb, inv_no, st))
            for j in true_cols:
                day = dates.iloc[j]
                if pd.isna(day):
                    continue
                events.append({
                    "date": day.normalize(),
                    "inverter_id": f"WB{wb:02d}-INV{inv_no:02d}",
                    "wb": wb,
                    "inv": inv_no,
                    "st": st,
                    "pv": pv,
                    "mppt": mppt,
                })

    if unmapped:
        warnings.warn(
            f"[cleaning_report] {len(unmapped)} string tanpa mapping ST->PV "
            f"di DC cable list (pv=NaN), mis. {sorted(unmapped)[:3]}.",
            stacklevel=2,
        )
    out = pd.DataFrame(
        events,
        columns=["date", "inverter_id", "wb", "inv", "st", "pv", "mppt"],
    )
    return out.sort_values(["date", "inverter_id", "st"], ignore_index=True)


def daily_cleaning_counts(events: pd.DataFrame) -> pd.Series:
    """Jumlah string yang dibersihkan per hari (site-level)."""
    if events.empty:
        return pd.Series(dtype=float)
    counts = events.groupby("date").size().astype(float)
    counts.index = pd.DatetimeIndex(counts.index)
    counts.name = "strings_cleaned"
    return counts.sort_index()


def classify_cleaning_intervals(
    interval_summary: pd.DataFrame,
    manual_daily: Optional[pd.Series],
    precip_daily: Optional[pd.Series] = None,
    *,
    window_days: int = 3,
    precip_threshold_mm: float = 1.0,
) -> pd.DataFrame:
    """Anotasi interval SRR: recovery di awal interval karena manual/hujan?

    Untuk tiap baris ``interval_summary`` (rdtools soiling_interval_summary,
    kolom ``start``), jumlahkan string yang dibersihkan manual dan hujan mm
    dalam jendela ``start +/- window_days`` lalu simpulkan ``likely_cause``:
    manual+rain / manual / rain / unknown. Tanpa anotasi ini semua recovery
    SRR terlihat sama (default rdtools: dianggap cleaning) -- false positive
    di iklim monsoon.
    """
    out = interval_summary.copy()
    if "start" not in out.columns:
        return out
    starts = pd.to_datetime(out["start"], errors="coerce")
    window = pd.Timedelta(days=window_days)

    def _sum_window(series: Optional[pd.Series], start) -> float:
        if series is None or series.empty or pd.isna(start):
            return 0.0
        mask = (series.index >= start - window) & (series.index <= start + window)
        return float(series.loc[mask].sum())

    manual_counts = [_sum_window(manual_daily, s) for s in starts]
    precip_sums = [_sum_window(precip_daily, s) for s in starts]
    causes = []
    for n_manual, mm in zip(manual_counts, precip_sums):
        is_manual = n_manual > 0
        is_rain = mm >= precip_threshold_mm
        if is_manual and is_rain:
            causes.append("manual+rain")
        elif is_manual:
            causes.append("manual")
        elif is_rain:
            causes.append("rain")
        else:
            causes.append("unknown")
    out["manual_strings_cleaned_window"] = manual_counts
    out["precip_mm_window"] = precip_sums
    out["likely_cause"] = causes
    return out
