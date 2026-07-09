"""Soiling analysis run -- gabung >=90 hari baseline CSV + rdtools SRR.

Menjalankan M2aSoiling (pv_pipeline.m2a.soiling) sebagai analisis offline:
notebook harian hanya memuat 1 hari sehingga selalu berhenti di gate
insufficient_data (min_days=90). Script ini menggabungkan baseline CSV
multi-bulan menjadi satu combined_df lalu memanggil detector sekali.

Cara pakai (lokal, folder baseline hasil download dari Drive):

    python run_soiling_analysis.py --baseline-dir baseline

Di Google Colab (Drive mount):

    from google.colab import drive
    drive.mount("/content/drive")
    !git clone https://github.com/nabilhaidr/PVStringHeatmapCheck.git
    %cd PVStringHeatmapCheck
    !python run_soiling_analysis.py \
        --baseline-dir "/content/drive/MyDrive/<path-ke>/baseline" \
        --rainfall-xlsx "raw data input/Daily Rainfall PLTS IKN 2025.xlsx" \
                        "raw data input/Daily Rainfall PLTS IKN 2026.xlsx"

Jalankan dari root repo (butuh config/m2_config.yaml, config/strings.yaml,
config/site_geometry.yaml untuk POA -- fallback pvlib clearsky dipakai untuk
tanggal tanpa data pyranometer). rdtools auto-install saat pertama dipakai.

Presipitasi
-----------
Sumber: "raw data input/Daily Rainfall PLTS IKN {2025,2026}.xlsx", sheet
"Daily Rainfall PLTS IKN" (Date time 5-menit + Daily Rainfall (mm) WS 1..4).
Nilai 5-menit adalah COUNTER KUMULATIF harian (reset tiap tengah malam;
WS-2 2026-01-01: 0.2 -> 0.4 -> ... -> 1.2), sehingga:

    total harian per WS = MAX nilai kumulatif hari itu (bukan SUM -- sum
    menggandakan berkali-kali), site-level = mean antar WS yang ada.

(Kolom "Rata-rata WS 1 - WS 4" dan kolom harian di sheet WS-x berisi
rata-rata kurva kumulatif -- bukan total harian -- jadi tidak dipakai.)
Hasil digabung ke satu CSV (date, precipitation_mm) lalu dipass ke
config m2a_soiling.precipitation_path (format yang dimengerti
_load_precipitation di detector).

Link BMKG (peringatan dini, prakiraan, analisis hujan bulanan, SPI) adalah
produk regional berbentuk halaman web/PDF -- referensi validasi manual,
bukan input pipeline.

Output
------
    {output_dir}/precipitation_daily_plts_ikn.csv
    {output_dir}/soiling_srr_{start}_{end}.xlsx
        sheet Findings         : M2Finding hasil run (sr, p_loss, payback)
        sheet EconomicAnalysis : ringkasan ekonomi cleaning
        sheet SoilingRatio     : profil stochastic SR (bila rdtools emit)
        sheet CleaningEvents   : interval/cleaning summary (bila ada)
"""
from __future__ import annotations

import argparse
import os
import re
import warnings
from typing import List, Optional, Tuple

import pandas as pd

from pv_pipeline.m2_config import load_m2_config
from pv_pipeline.m2a.soiling import (
    ACTIVE_POWER_COL_CANDIDATES,
    DEFAULT_SAMPLE_FREQ_HOURS,
    M2aSoiling,
    build_direct_cleaning_impact_per_string,
)
from train_lstm_ae import discover_baseline_csvs

DEFAULT_RAINFALL_XLSX: List[str] = [
    os.path.join("raw data input", "Daily Rainfall PLTS IKN 2025.xlsx"),
    os.path.join("raw data input", "Daily Rainfall PLTS IKN 2026.xlsx"),
]
DEFAULT_CLEANING_REPORT_XLSX = os.path.join(
    "raw data input", "Report & Schedule Cleaning PLTS IKN.xlsx",
)
DEFAULT_DC_CABLE_XLS = os.path.join(
    "raw data input", "List of DC Cables 0411.xls",
)
RAINFALL_SHEET = "Daily Rainfall PLTS IKN"
RAINFALL_TS_COL = "Date time"
RAINFALL_WS_COL_RE = re.compile(r"^Daily Rainfall \(mm\) WS \d+$")
TIMESTAMP_COL = "Start Time"
INVERTER_COL = "Inverter_ID"
PV_POWER_COLS = [f"PV{n} Power(kW)" for n in range(1, 29)]


def load_daily_rainfall(xlsx_paths: List[str]) -> pd.Series:
    """Gabung file rainfall -> series mm/hari site-level.

    Per WS: max counter kumulatif per hari (lihat docstring modul).
    Site-level: mean antar WS (skip WS yang NaN hari itu). Tanggal duplikat
    antar file diambil max-nya.
    """
    per_file: List[pd.Series] = []
    for path in xlsx_paths:
        df = pd.read_excel(path, sheet_name=RAINFALL_SHEET)
        ws_cols = [c for c in df.columns if RAINFALL_WS_COL_RE.match(str(c))]
        if RAINFALL_TS_COL not in df.columns or not ws_cols:
            raise ValueError(
                f"[soiling-run] {path!r} sheet {RAINFALL_SHEET!r} tidak punya "
                f"kolom {RAINFALL_TS_COL!r} + 'Daily Rainfall (mm) WS n'."
            )
        ts = pd.to_datetime(df[RAINFALL_TS_COL], errors="coerce")
        valid = ts.notna()
        daily_per_ws = (
            df.loc[valid, ws_cols]
            .apply(pd.to_numeric, errors="coerce")
            .groupby(ts[valid].dt.normalize().values)
            .max()
        )
        site_daily = daily_per_ws.mean(axis=1, skipna=True).dropna()
        site_daily.index = pd.DatetimeIndex(site_daily.index)
        per_file.append(site_daily)
    if not per_file:
        return pd.Series(dtype=float)
    combined = pd.concat(per_file).groupby(level=0).max().sort_index()
    combined.name = "precipitation_mm"
    return combined


def write_precipitation_csv(precip_daily: pd.Series, out_path: str) -> str:
    """Tulis CSV (date, precipitation_mm) -- format _load_precipitation detector."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    out = pd.DataFrame({
        "date": precip_daily.index.strftime("%Y-%m-%d"),
        "precipitation_mm": precip_daily.values,
    })
    out.to_csv(out_path, index=False)
    return out_path


def load_baseline_for_soiling(
    files: List[Tuple[pd.Timestamp, str]],
    with_per_string: bool = False,
):
    """Concat baseline CSV, kolom seperlunya saja (hemat memori multi-bulan).

    Detector memilih 'Active power(kW)' kalau ada; kolom PV{n} Power(kW)
    hanya dipertahankan bila file tidak punya kolom active power.

    ``with_per_string=True``: return ``(combined_df, string_daily)`` --
    string_daily = long df [date, Inverter_ID, pv, energy_kwh] hasil agregasi
    harian per-string PER FILE (kolom PV lebar langsung diringkas sebelum
    dibuang, jadi memorinya tetap kecil: ~4500 string x n hari).
    """
    wanted = set([TIMESTAMP_COL, INVERTER_COL]) | set(ACTIVE_POWER_COL_CANDIDATES)
    wanted |= set(PV_POWER_COLS)
    parts: List[pd.DataFrame] = []
    string_parts: List[pd.DataFrame] = []
    for i, (day, path) in enumerate(files, start=1):
        df = pd.read_csv(path, usecols=lambda c: c in wanted)
        if with_per_string:
            pv_cols = [c for c in PV_POWER_COLS if c in df.columns]
            if pv_cols:
                wide = df.groupby(INVERTER_COL)[pv_cols].sum(min_count=1)
                long = (wide * DEFAULT_SAMPLE_FREQ_HOURS).stack().rename("energy_kwh").reset_index()
                long.columns = [INVERTER_COL, "pv_col", "energy_kwh"]
                # pandas 3.x stack() tidak drop NaN -> slot PV kosong dibuang
                # eksplisit di sini.
                long = long.dropna(subset=["energy_kwh"])
                long["pv"] = long["pv_col"].str.extract(r"PV(\d+)").astype(int)
                long["date"] = pd.Timestamp(day).normalize()
                string_parts.append(long[["date", INVERTER_COL, "pv", "energy_kwh"]])
        apc = next((c for c in ACTIVE_POWER_COL_CANDIDATES if c in df.columns), None)
        if apc is not None:
            df = df[[INVERTER_COL, TIMESTAMP_COL, apc]]
        parts.append(df)
        print(f"[{i}/{len(files)}] {day.date()}  rows={len(df)}")
    combined = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if not with_per_string:
        return combined
    string_daily = (
        pd.concat(string_parts, ignore_index=True) if string_parts
        else pd.DataFrame(columns=["date", INVERTER_COL, "pv", "energy_kwh"])
    )
    return combined, string_daily


def filter_combined_by_wb(combined_df: pd.DataFrame, wb_list: List[str]) -> pd.DataFrame:
    """Filter baris combined_df ke kelompok WB (prefix Inverter_ID)."""
    prefixes = tuple(str(w).strip().upper() for w in wb_list)
    mask = combined_df[INVERTER_COL].astype(str).str.upper().str.startswith(prefixes)
    return combined_df[mask]


# Kapasitas DC aktual nameplate per WB (kWp), dari data owner (2026-07-08).
# Total = 71513 kWp ~ site 71500. Soiling ratio di-recenter rdtools ->
# presisi kapasitas tidak kritis untuk sr/p_loss; angka ini menjaga PR di
# rentang fisik dan dipakai untuk pelaporan PR absolut.
PER_WB_CAPACITY_KWP = {
    "WB01": 6750.0, "WB02": 6750.0,
    "WB03": 5996.0, "WB04": 7621.0, "WB05": 7865.0, "WB06": 7995.0,
    "WB07": 6793.0, "WB08": 6793.0, "WB09": 7881.0, "WB10": 7069.0,
}
# Estimasi biaya cleaning per WB (dari alokasi zona: WB01-02 1.165jt/2;
# WB03-10 55jt/8). Sesuaikan bila ada angka aktual per WB.
PER_WB_CLEANING_COST_IDR = {
    "WB01": 582500.0, "WB02": 582500.0,
    "WB03": 6875000.0, "WB04": 6875000.0, "WB05": 6875000.0, "WB06": 6875000.0,
    "WB07": 6875000.0, "WB08": 6875000.0, "WB09": 6875000.0, "WB10": 6875000.0,
}

OUTPUT_SHEETS = [
    "EconomicAnalysis", "DirectCleaningImpact", "DirectCleaningImpactPerString",
    "PerInverterSRR", "CleaningImpact", "SoilingRatio", "CleaningEvents",
    "ManualCleaning",
]


def _run_and_save(
    combined_full: pd.DataFrame,
    cfg: dict,
    soil_base: dict,
    files: List[Tuple[pd.Timestamp, str]],
    output_dir: str,
    *,
    wb_filter: Optional[List[str]],
    capacity_kwp: Optional[float],
    cleaning_cost_idr: Optional[float],
    string_daily: Optional[pd.DataFrame] = None,
) -> None:
    """Jalankan M2aSoiling untuk satu konfigurasi (site atau subset WB) lalu
    tulis xlsx. combined_full dimuat sekali; difilter di sini bila wb_filter."""
    soil = dict(soil_base)
    if wb_filter:
        soil["wb_filter"] = [str(w).strip().upper() for w in wb_filter]
        soil["capacity_kwp"] = capacity_kwp
    if cleaning_cost_idr is not None:
        soil["cleaning_cost_idr"] = cleaning_cost_idr
    cfg = dict(cfg)
    cfg["m2a_soiling"] = soil

    df = combined_full
    if wb_filter:
        df = filter_combined_by_wb(combined_full, wb_filter)
        print(f"\n[soiling-run] === {'-'.join(soil['wb_filter'])} "
              f"(capacity_kwp={capacity_kwp}, cleaning_cost_idr={cleaning_cost_idr}) ===")
        print(f"[soiling-run] filter: {len(combined_full)} -> {len(df)} rows")
    if df.empty:
        print(f"[soiling-run] SKIP {wb_filter}: 0 rows (tidak ada data WB itu).")
        return
    print(f"[soiling-run] combined_df: {df.shape[0]} rows, "
          f"{df['Inverter_ID'].nunique()} inverter")

    detector = M2aSoiling()
    findings = detector.run(df, cfg)

    # Sheet DirectCleaningImpactPerString: pre/post PR per string di sekitar
    # tanggal cleaning string itu sendiri (independen SRR). Butuh string_daily
    # dari loader + ManualCleaning & insolation dari detector.
    if string_daily is not None and not string_daily.empty:
        sd = filter_combined_by_wb(string_daily, wb_filter) if wb_filter else string_daily
        manual = detector.artifacts.get("ManualCleaning")
        insol = getattr(detector, "last_insolation_daily", None)
        if (isinstance(manual, pd.DataFrame) and not manual.empty
                and insol is not None and not insol.empty):
            per_string = build_direct_cleaning_impact_per_string(
                sd, insol, manual,
                window_days=int(soil.get("direct_impact_window_days", 7)),
                gap_days=int(soil.get("direct_impact_gap_days", 7)),
            )
            if not per_string.empty:
                detector.artifacts["DirectCleaningImpactPerString"] = per_string

    print(f"[soiling-run] findings: {len(findings)}")
    rows = []
    for f in findings:
        sev = getattr(f.severity, "value", str(f.severity))
        print(f"  [{sev}] {f.fault_type}: {f.message}")
        rows.append({
            "timestamp": f.timestamp, "inverter_id": f.inverter_id,
            "sub_module": f.sub_module, "severity": sev,
            "fault_type": f.fault_type, "value": f.value,
            "threshold": f.threshold, "confidence": f.confidence,
            "message": f.message, "evidence": str(f.evidence),
        })

    start_s = files[0][0].strftime("%Y%m%d")
    end_s = files[-1][0].strftime("%Y%m%d")
    group_tag = "_" + "-".join(soil["wb_filter"]) if wb_filter else ""
    os.makedirs(output_dir, exist_ok=True)
    out_xlsx = os.path.join(output_dir, f"soiling_srr_{start_s}_{end_s}{group_tag}.xlsx")
    with pd.ExcelWriter(out_xlsx) as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Findings", index=False)
        for sheet in OUTPUT_SHEETS:
            artifact = detector.artifacts.get(sheet)
            if isinstance(artifact, pd.DataFrame) and not artifact.empty:
                artifact.to_excel(writer, sheet_name=sheet, index=False)
    print(f"[soiling-run] hasil: {out_xlsx}")
    dci = detector.artifacts.get("DirectCleaningImpact")
    if isinstance(dci, pd.DataFrame) and not dci.empty:
        print(f"[soiling-run] DirectCleaningImpact (independen SRR): "
              f"{len(dci)} campaign; median soiling_loss_pct = "
              f"{dci['soiling_loss_pct'].median():.2f}%")
    dps = detector.artifacts.get("DirectCleaningImpactPerString")
    if isinstance(dps, pd.DataFrame) and not dps.empty:
        top = dps.iloc[0]
        print(f"[soiling-run] DirectCleaningImpactPerString: {len(dps)} "
              f"string-campaign; median uplift = {dps['uplift_pct'].median():.2f}%; "
              f"terkotor: {top['inverter_id']} PV{top['pv']} "
              f"(uplift {top['uplift_pct']:.2f}%)")
    pis = detector.artifacts.get("PerInverterSRR")
    if isinstance(pis, pd.DataFrame) and not pis.empty:
        ok = pis[pis["status"] == "ok"]
        print(f"[soiling-run] PerInverterSRR: {len(pis)} inverter "
              f"({len(ok)} ok); "
              + (f"p_loss tertinggi: {ok.iloc[0]['inverter_id']} "
                 f"({ok.iloc[0]['p_loss_pct']:.2f}%)" if not ok.empty
                 else "tidak ada yang ok"))
    ci = detector.artifacts.get("CleaningImpact")
    if isinstance(ci, pd.DataFrame) and not ci.empty:
        print(f"[soiling-run] CleaningImpact (SRR): {len(ci)} event; "
              f"total rupiah_per_day dipulihkan = "
              f"{ci['rupiah_per_day'].clip(lower=0).sum():.0f} IDR")
    econ = detector.artifacts.get("EconomicAnalysis")
    if isinstance(econ, pd.DataFrame) and not econ.empty:
        print(econ.to_string(index=False))


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Soiling SRR analysis run dari gabungan baseline CSV.",
    )
    parser.add_argument(
        "--baseline-dir", required=True,
        help="Folder baseline berisi {YYYY-MM}/{YYYY-MM-DD}.csv",
    )
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD (inclusive)")
    parser.add_argument(
        "--rainfall-xlsx", nargs="*", default=None,
        help="File Daily Rainfall PLTS IKN xlsx (default: dua file di "
             "'raw data input/'; run tetap lanjut tanpa presipitasi bila "
             "default tidak ditemukan)",
    )
    parser.add_argument(
        "--cleaning-report-xlsx", default=None,
        help="Rekap manual cleaning (checklist TRUE per string per tanggal). "
             f"Default: {DEFAULT_CLEANING_REPORT_XLSX!r} bila ada.",
    )
    parser.add_argument(
        "--dc-cable-xls", default=None,
        help="List of DC Cables (mapping ST->PV WB03-WB10). "
             f"Default: {DEFAULT_DC_CABLE_XLS!r} bila ada.",
    )
    parser.add_argument("--config", default=os.path.join("config", "m2_config.yaml"))
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--min-days", type=int, default=90)
    parser.add_argument(
        "--cleaning-cost-idr", type=float, default=None,
        help="Override m2a_soiling.cleaning_cost_idr (WAJIB > 0 supaya "
             "payback/rekomendasi cleaning bermakna)",
    )
    parser.add_argument("--rdtools-reps", type=int, default=None,
                        help="Override Monte-Carlo reps (default ikut config)")
    parser.add_argument(
        "--clean-criterion", default=None,
        choices=["shift", "precip_and_shift", "precip_or_shift", "precip"],
        help="Deteksi cleaning event SRR (default rdtools: shift). Di iklim "
             "monsoon dengan PR berisik, 'precip_and_shift'/'precip' "
             "mengurangi cleaning palsu.",
    )
    parser.add_argument(
        "--precip-threshold-mm", type=float, default=None,
        help="Ambang mm/hari dianggap hujan pembersih (default rdtools 0.01; "
             "saran 1.0 untuk data mm kami).",
    )
    parser.add_argument(
        "--min-interval-length", type=int, default=None,
        help="Panjang minimal interval soiling valid, hari (default 7, min 2).",
    )
    parser.add_argument(
        "--day-scale", type=int, default=None,
        help="Window rolling-median deteksi cleaning, hari (default 13, "
             "sebaiknya ganjil).",
    )
    parser.add_argument(
        "--wb", nargs="+", default=None,
        help="Analisis per kelompok WB (mis. --wb WB01 WB02). Wajib disertai "
             "--capacity-kwp karena kapasitas default 71500 kWp = site penuh.",
    )
    parser.add_argument(
        "--capacity-kwp", type=float, default=None,
        help="Override kapasitas DC kWp (nameplate). Referensi per WB: "
             "WB01/02 6750, WB03 5996, WB04 7621, WB05 7865, WB06 7995, "
             "WB07/08 6793, WB09 7881, WB10 7069; zona WB01-02 13500; "
             "site penuh 71500.",
    )
    parser.add_argument(
        "--per-inverter-srr", action="store_true",
        help="Sheet PerInverterSRR: soiling_srr per inverter (~194 unit, "
             "MAHAL: +- beberapa menit ekstra; reps ikut config "
             "per_inverter_reps, default 200).",
    )
    parser.add_argument(
        "--per-wb", action="store_true",
        help="Analisis TIAP WB01..WB10 sendiri-sendiri (baseline dimuat "
             "sekali). Kapasitas & cleaning cost per WB pakai tabel bawaan "
             "(PER_WB_CAPACITY_KWP / PER_WB_CLEANING_COST_IDR).",
    )
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)

    files = discover_baseline_csvs(args.baseline_dir, args.start_date, args.end_date)
    if not files:
        raise SystemExit(
            f"[soiling-run] tidak ada baseline CSV di {args.baseline_dir!r} "
            f"(range {args.start_date}..{args.end_date})."
        )
    print(f"[soiling-run] {len(files)} hari baseline "
          f"({files[0][0].date()} .. {files[-1][0].date()})")
    if len(files) < args.min_days:
        warnings.warn(
            f"[soiling-run] hanya {len(files)} hari < min_days={args.min_days}; "
            "detector akan emit insufficient_data.",
            stacklevel=1,
        )

    # --- Presipitasi -> satu CSV harian --------------------------------------
    precip_path = ""
    rainfall_paths = args.rainfall_xlsx
    strict_rainfall = rainfall_paths is not None
    if rainfall_paths is None:
        rainfall_paths = DEFAULT_RAINFALL_XLSX
    existing = [p for p in rainfall_paths if os.path.exists(p)]
    missing = [p for p in rainfall_paths if not os.path.exists(p)]
    if missing and strict_rainfall:
        raise SystemExit(f"[soiling-run] rainfall xlsx tidak ditemukan: {missing}")
    if missing:
        print(f"[soiling-run] WARNING rainfall default tidak ditemukan: {missing}")
    if existing:
        precip_daily = load_daily_rainfall(existing)
        precip_path = write_precipitation_csv(
            precip_daily,
            os.path.join(args.output_dir, "precipitation_daily_plts_ikn.csv"),
        )
        rain_days = int((precip_daily > 0).sum())
        print(f"[soiling-run] presipitasi: {len(precip_daily)} hari "
              f"({precip_daily.index.min().date()}..{precip_daily.index.max().date()}), "
              f"{rain_days} hari hujan -> {precip_path}")
    else:
        print("[soiling-run] TANPA presipitasi: cleaning events dari SRR "
              "kurang reliable (semua recovery dianggap manual).")

    # --- Rekap manual cleaning + mapping ST->PV -------------------------------
    def _optional_path(explicit: str | None, default: str, label: str) -> str:
        if explicit is not None:
            if not os.path.exists(explicit):
                raise SystemExit(f"[soiling-run] {label} tidak ditemukan: {explicit!r}")
            return explicit
        if os.path.exists(default):
            return default
        print(f"[soiling-run] WARNING {label} default tidak ditemukan: {default!r}")
        return ""

    cleaning_report_path = _optional_path(
        args.cleaning_report_xlsx, DEFAULT_CLEANING_REPORT_XLSX, "cleaning report",
    )
    dc_cable_path = _optional_path(
        args.dc_cable_xls, DEFAULT_DC_CABLE_XLS, "DC cable list",
    )
    if cleaning_report_path:
        print(f"[soiling-run] cleaning report: {cleaning_report_path}"
              + (f" (mapping: {dc_cable_path})" if dc_cable_path else " (TANPA mapping ST->PV)"))

    # --- Config: override umum m2a_soiling (tanpa wb/capacity/cost) ----------
    cfg = load_m2_config(args.config)
    soil_base = dict(cfg.get("m2a_soiling", {}) or {})
    soil_base["enabled"] = True
    soil_base["min_days"] = args.min_days
    soil_base["precipitation_path"] = precip_path
    soil_base["cleaning_report_path"] = cleaning_report_path
    soil_base["dc_cable_list_path"] = dc_cable_path
    if args.rdtools_reps is not None:
        soil_base["rdtools_reps"] = args.rdtools_reps
    if args.clean_criterion is not None:
        soil_base["rdtools_clean_criterion"] = args.clean_criterion
    if args.precip_threshold_mm is not None:
        soil_base["rdtools_precip_threshold"] = args.precip_threshold_mm
    if args.min_interval_length is not None:
        soil_base["rdtools_min_interval_length"] = args.min_interval_length
    if args.day_scale is not None:
        soil_base["rdtools_day_scale"] = args.day_scale
    if args.per_inverter_srr:
        soil_base["per_inverter_srr"] = True

    # --- Gabung baseline (SEKALI), termasuk agregasi harian per-string -------
    combined_df, string_daily = load_baseline_for_soiling(files, with_per_string=True)
    if combined_df.empty:
        raise SystemExit("[soiling-run] combined_df kosong.")
    print(f"[soiling-run] combined_df total: {combined_df.shape[0]} rows, "
          f"{combined_df['Inverter_ID'].nunique()} inverter; "
          f"string_daily: {len(string_daily)} baris "
          f"({string_daily.groupby([INVERTER_COL, 'pv']).ngroups if not string_daily.empty else 0} string)")

    if args.per_wb:
        for wb in sorted(PER_WB_CAPACITY_KWP):
            _run_and_save(
                combined_df, cfg, soil_base, files, args.output_dir,
                wb_filter=[wb],
                capacity_kwp=PER_WB_CAPACITY_KWP[wb],
                cleaning_cost_idr=PER_WB_CLEANING_COST_IDR[wb],
                string_daily=string_daily,
            )
        return

    if args.wb and args.capacity_kwp is None:
        raise SystemExit(
            "[soiling-run] --wb butuh --capacity-kwp (referensi: "
            "WB01-02 ~13500 masing2, WB03-10 ~6000-8000, site penuh 71500)."
        )
    cost = args.cleaning_cost_idr
    if float(cost or 0.0) <= 0.0:
        print("[soiling-run] WARNING cleaning_cost_idr=0 -> payback=inf, "
              "rekomendasi cleaning tidak akan pernah muncul. "
              "Isi --cleaning-cost-idr atau config m2a_soiling.cleaning_cost_idr.")
    _run_and_save(
        combined_df, cfg, soil_base, files, args.output_dir,
        wb_filter=args.wb,
        capacity_kwp=args.capacity_kwp,
        cleaning_cost_idr=cost,
        string_daily=string_daily,
    )


if __name__ == "__main__":
    main()
