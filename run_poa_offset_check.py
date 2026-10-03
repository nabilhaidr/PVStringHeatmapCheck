"""Verifikasi batch offset waktu POA terhadap telemetri inverter.

Per hari baseline: daya aktif median per WS (median inverter di WB yang
dipetakan ke WS itu; hanya "Grid connected", jam 06-17) dibandingkan dengan
POA per WS; lag terbaik dari korelasi perubahan (lihat
``pv_pipeline.poa.offset_check``). Lag +5 = stempel POA 5 menit lebih awal.
Keluaran: sheet ``summary`` (bulan x WS) dan ``per_day``. Dasar keputusan
koreksi offset di ``PyranometerLoader`` -- terutama apakah file POA 2025 dan
2026 memakai konvensi stempel yang sama.

Colab:
    !python run_poa_offset_check.py --baseline-dir "/content/drive/MyDrive/Cek PV String/baseline"
"""
from __future__ import annotations

import argparse
import os
from typing import Dict

import pandas as pd

from pv_pipeline.poa.loader import PyranometerLoader
from pv_pipeline.poa.offset_check import OFFSET_COLUMNS, best_lag_by_ws
from train_lstm_ae import discover_baseline_csvs

USECOLS = ["Start Time", "Inverter_ID", "Inverter status", "Active power(kW)"]


def power_by_ws(csv_path: str, wb_to_ws: Dict[str, str]) -> pd.DataFrame:
    df = pd.read_csv(csv_path, usecols=USECOLS, low_memory=False)
    ts = pd.to_datetime(df["Start Time"], errors="coerce")
    keep = (df["Inverter status"] == "Grid connected") & ts.dt.hour.between(6, 17)
    df = df.assign(ts=ts, p=pd.to_numeric(df["Active power(kW)"], errors="coerce"))[keep]
    per_inv = df.pivot_table(index="ts", columns="Inverter_ID", values="p", aggfunc="mean")
    groups: Dict[str, list] = {}
    for inv in per_inv.columns:
        ws = wb_to_ws.get(str(inv)[:4].upper())
        if ws:
            groups.setdefault(ws, []).append(inv)
    return pd.DataFrame({ws: per_inv[cols].median(axis=1) for ws, cols in sorted(groups.items())})


def summarize(per_day: pd.DataFrame) -> pd.DataFrame:
    per_day = per_day.assign(
        month=per_day["day"].dt.strftime("%Y-%m"),
        gain=per_day["corr_best"] - per_day["corr_lag0"],
    )
    g = per_day.groupby(["month", "ws"])
    return pd.DataFrame({
        "n_days": g.size(),
        "median_lag_min": g["best_lag_min"].median(),
        "share_lag_plus5": g["best_lag_min"].apply(lambda s: float((s == 5).mean())),
        "share_lag_zero": g["best_lag_min"].apply(lambda s: float((s == 0).mean())),
        "median_corr_gain": g["gain"].median(),
    }).reset_index()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Verifikasi batch offset waktu POA vs telemetri.")
    parser.add_argument("--baseline-dir", default="baseline")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--geometry", default=os.path.join("config", "site_geometry.yaml"))
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args(argv)

    files = discover_baseline_csvs(args.baseline_dir, args.start_date, args.end_date)
    if not files:
        raise SystemExit(f"[poa-offset] tidak ada baseline CSV di {args.baseline_dir!r}")
    # Diagnostik sensor: selalu bacaan mentah (koreksi akan menyembunyikan WS yang dikecualikan).
    loader = PyranometerLoader.from_geometry_yaml(args.geometry, koreksi=False)

    rows = []
    for day, path in files:
        window = loader.df.loc[day - pd.Timedelta(hours=1): day + pd.Timedelta(hours=25)]
        result = best_lag_by_ws(power_by_ws(path, loader.wb_to_ws), window)
        if result.empty:
            print(f"{day.date()}: dilewati (data tipis)")
            continue
        rows.append(result.assign(day=day))
        print(f"{day.date()}: " + "  ".join(
            f"{r.ws}={r.best_lag_min:+d} ({r.corr_lag0:.2f}->{r.corr_best:.2f})"
            for r in result.itertuples()
        ))
    if not rows:
        raise SystemExit("[poa-offset] tidak ada hari yang terevaluasi")

    per_day = pd.concat(rows, ignore_index=True)[["day", *OFFSET_COLUMNS]]
    summary = summarize(per_day)
    os.makedirs(args.output_dir, exist_ok=True)
    out = os.path.join(
        args.output_dir,
        f"poa_offset_check_{files[0][0]:%Y%m%d}_{files[-1][0]:%Y%m%d}.xlsx",
    )
    with pd.ExcelWriter(out) as writer:
        summary.to_excel(writer, sheet_name="summary", index=False)
        per_day.to_excel(writer, sheet_name="per_day", index=False)
    print(summary.to_string(index=False))
    print("ditulis:", out)


if __name__ == "__main__":
    main()
