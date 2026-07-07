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
from typing import List, Tuple

import pandas as pd

from pv_pipeline.m2_config import load_m2_config
from pv_pipeline.m2a.soiling import ACTIVE_POWER_COL_CANDIDATES, M2aSoiling
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
) -> pd.DataFrame:
    """Concat baseline CSV, kolom seperlunya saja (hemat memori multi-bulan).

    Detector memilih 'Active power(kW)' kalau ada; kolom PV{n} Power(kW)
    hanya dipertahankan bila file tidak punya kolom active power.
    """
    wanted = set([TIMESTAMP_COL, INVERTER_COL]) | set(ACTIVE_POWER_COL_CANDIDATES)
    wanted |= set(PV_POWER_COLS)
    parts: List[pd.DataFrame] = []
    for i, (day, path) in enumerate(files, start=1):
        df = pd.read_csv(path, usecols=lambda c: c in wanted)
        apc = next((c for c in ACTIVE_POWER_COL_CANDIDATES if c in df.columns), None)
        if apc is not None:
            df = df[[INVERTER_COL, TIMESTAMP_COL, apc]]
        parts.append(df)
        print(f"[{i}/{len(files)}] {day.date()}  rows={len(df)}")
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def filter_combined_by_wb(combined_df: pd.DataFrame, wb_list: List[str]) -> pd.DataFrame:
    """Filter baris combined_df ke kelompok WB (prefix Inverter_ID)."""
    prefixes = tuple(str(w).strip().upper() for w in wb_list)
    mask = combined_df[INVERTER_COL].astype(str).str.upper().str.startswith(prefixes)
    return combined_df[mask]


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
        help="Override kapasitas DC kWp. Referensi: WB01-02 ~13500 "
             "(900 string x 24 modul x 625 Wp), WB03-10 ~58000 "
             "(3571 string x 26 modul x 625 Wp), site penuh 71500.",
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

    # --- Config: enable m2a_soiling + overrides ------------------------------
    cfg = load_m2_config(args.config)
    soil_cfg = dict(cfg.get("m2a_soiling", {}) or {})
    soil_cfg["enabled"] = True
    soil_cfg["min_days"] = args.min_days
    soil_cfg["precipitation_path"] = precip_path
    soil_cfg["cleaning_report_path"] = cleaning_report_path
    soil_cfg["dc_cable_list_path"] = dc_cable_path
    if args.wb:
        if args.capacity_kwp is None:
            raise SystemExit(
                "[soiling-run] --wb butuh --capacity-kwp (referensi: "
                "WB01-02 ~13500, WB03-10 ~58000, site penuh 71500)."
            )
        soil_cfg["wb_filter"] = [str(w).strip().upper() for w in args.wb]
    if args.capacity_kwp is not None:
        soil_cfg["capacity_kwp"] = args.capacity_kwp
    if args.cleaning_cost_idr is not None:
        soil_cfg["cleaning_cost_idr"] = args.cleaning_cost_idr
    if args.rdtools_reps is not None:
        soil_cfg["rdtools_reps"] = args.rdtools_reps
    if args.clean_criterion is not None:
        soil_cfg["rdtools_clean_criterion"] = args.clean_criterion
    if args.precip_threshold_mm is not None:
        soil_cfg["rdtools_precip_threshold"] = args.precip_threshold_mm
    if args.min_interval_length is not None:
        soil_cfg["rdtools_min_interval_length"] = args.min_interval_length
    if args.day_scale is not None:
        soil_cfg["rdtools_day_scale"] = args.day_scale
    cfg["m2a_soiling"] = soil_cfg
    if float(soil_cfg.get("cleaning_cost_idr", 0.0) or 0.0) <= 0.0:
        print("[soiling-run] WARNING cleaning_cost_idr=0 -> payback=inf, "
              "rekomendasi cleaning tidak akan pernah muncul. "
              "Isi --cleaning-cost-idr atau config m2a_soiling.cleaning_cost_idr.")

    # --- Gabung baseline + run detector --------------------------------------
    combined_df = load_baseline_for_soiling(files)
    if args.wb:
        n_before = len(combined_df)
        combined_df = filter_combined_by_wb(combined_df, args.wb)
        print(f"[soiling-run] filter {soil_cfg['wb_filter']}: "
              f"{n_before} -> {len(combined_df)} rows, "
              f"capacity_kwp={soil_cfg['capacity_kwp']}")
    if combined_df.empty:
        raise SystemExit("[soiling-run] combined_df kosong.")
    print(f"[soiling-run] combined_df: {combined_df.shape[0]} rows, "
          f"{combined_df['Inverter_ID'].nunique()} inverter")

    detector = M2aSoiling()  # POA self-init dari cfg['poa'].site_geometry_path
    findings = detector.run(combined_df, cfg)

    # --- Report + save artifacts ---------------------------------------------
    print(f"\n[soiling-run] findings: {len(findings)}")
    rows = []
    for f in findings:
        sev = getattr(f.severity, "value", str(f.severity))
        print(f"  [{sev}] {f.fault_type}: {f.message}")
        rows.append({
            "timestamp": f.timestamp,
            "inverter_id": f.inverter_id,
            "sub_module": f.sub_module,
            "severity": sev,
            "fault_type": f.fault_type,
            "value": f.value,
            "threshold": f.threshold,
            "confidence": f.confidence,
            "message": f.message,
            "evidence": str(f.evidence),
        })

    start_s = files[0][0].strftime("%Y%m%d")
    end_s = files[-1][0].strftime("%Y%m%d")
    group_tag = "_" + "-".join(soil_cfg["wb_filter"]) if args.wb else ""
    os.makedirs(args.output_dir, exist_ok=True)
    out_xlsx = os.path.join(
        args.output_dir, f"soiling_srr_{start_s}_{end_s}{group_tag}.xlsx",
    )
    with pd.ExcelWriter(out_xlsx) as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Findings", index=False)
        for sheet in ["EconomicAnalysis", "SoilingRatio", "CleaningEvents", "ManualCleaning"]:
            artifact = detector.artifacts.get(sheet)
            if isinstance(artifact, pd.DataFrame) and not artifact.empty:
                artifact.to_excel(writer, sheet_name=sheet, index=False)
    print(f"[soiling-run] hasil: {out_xlsx}")
    manual = detector.artifacts.get("ManualCleaning")
    if isinstance(manual, pd.DataFrame) and not manual.empty:
        print(f"[soiling-run] manual cleaning: {len(manual)} string-event, "
              f"{manual['date'].nunique()} hari "
              f"({manual['date'].min().date()}..{manual['date'].max().date()})")
    econ = detector.artifacts.get("EconomicAnalysis")
    if isinstance(econ, pd.DataFrame) and not econ.empty:
        print("[soiling-run] EconomicAnalysis:")
        print(econ.to_string(index=False))


if __name__ == "__main__":
    main()
