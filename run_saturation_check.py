"""Batch pemilah kekurangan daya di POA tinggi: clipping vs sensor vs steady-state.

Menjalankan ``pv_pipeline.m2f.saturation`` atas baseline Drive per
inverter-hari. Hari yang menjawab pertanyaannya adalah hari cerah STABIL
(kolom ``clear_stable`` di sheet ``summary``):
  * ``r_high_stable_unclipped`` ~ 1  -> kekurangan di POA tinggi = clipping AC;
  * ``r_high_stable_unclipped`` < 1  -> steady-state (kalibrasi sensor,
    spektrum, suhu) -- ikut menjelaskan measured_ratio ~0,85 di hari cerah;
  * ``r_high_all`` < ``r_high_stable`` -> efek keterwakilan pyranometer titik.

POA dibaca per WS TANPA fallback avg. Offset waktu POA dari
``pyranometer.time_offset_minutes``, atau ``--poa-offset-min`` sebelum config
diisi dari hasil run_poa_offset_check.py.

Colab:
    !python run_saturation_check.py --baseline-dir "/content/drive/MyDrive/Cek PV String/baseline" --poa-offset-min 5
"""
from __future__ import annotations

import argparse
import os
from typing import Dict, Iterator, Tuple

import numpy as np
import pandas as pd
import yaml

from pv_pipeline.cell_temp import CellTempProvider
from pv_pipeline.core import load_empty_pv_map
from pv_pipeline.m2_config import load_m2_config
from pv_pipeline.m2f.saturation import SATURATION_METRICS, saturation_metrics
from pv_pipeline.panel_spec import PanelSpec
from pv_pipeline.poa.loader import PyranometerLoader
from pv_pipeline.poa.pvlib_estimator import PvlibClearSkyEstimator
from pv_pipeline.transformations import PV_POWER_RE
from train_lstm_ae import discover_baseline_csvs

# Hari cerah-stabil: median porsi sampel POA tinggi yang stabil >= ini.
CLEAR_STABLE_SHARE = 0.5


def poa_smooth_share(poa_df: pd.DataFrame, day: pd.Timestamp, smooth_tol: float = 0.02) -> float:
    """Median antar-WS porsi sampel 09-15 (POA > 300) yang mulus -- saringan murah
    sebelum membaca CSV telemetri. Survei 2025-2026: median ~0,2; hanya
    ~16-30 hari per WS >= 0,5, jadi hari berawan tidak perlu dibaca."""
    d = poa_df.loc[day + pd.Timedelta(hours=6): day + pd.Timedelta(hours=18)]
    d = d[[c for c in d.columns if c.startswith("WS-")]]
    dev = ((d - (d.shift(1) + d.shift(-1)) / 2.0).abs() / d).between_time("09:00", "15:00")
    valid = d.between_time("09:00", "15:00") > 300.0
    shares = [
        float((dev[ws][valid[ws]] < smooth_tol).mean())
        for ws in d.columns if valid[ws].sum() >= 20
    ]
    return float(np.median(shares)) if shares else np.nan


def inverter_frames(
    csv_path: str, empty_map: Dict[str, list],
) -> Iterator[Tuple[str, pd.DatetimeIndex, np.ndarray, np.ndarray]]:
    """(inverter_id, stempel, daya DC kW, daya AC kW) per inverter, "Grid connected"."""
    df = pd.read_csv(csv_path, low_memory=False)
    ts = pd.to_datetime(df["Start Time"], errors="coerce")
    keep = (df["Inverter status"] == "Grid connected") & ts.dt.hour.between(6, 17)
    df = df.loc[keep].assign(ts=ts[keep])
    pv_cols = {c: int(m.group(1)) for c in df.columns if (m := PV_POWER_RE.search(str(c)))}
    for inv, g in df.groupby("Inverter_ID"):
        g = g.sort_values("ts").drop_duplicates("ts")
        empties = {int(n) for n in empty_map.get(str(inv).upper(), [])}
        cols = [c for c, n in pv_cols.items() if n not in empties]
        p_dc = g[cols].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1)
        p_ac = pd.to_numeric(g["Active power(kW)"], errors="coerce")
        yield str(inv), pd.DatetimeIndex(g["ts"]), p_dc.to_numpy(), p_ac.to_numpy()


def summarize(per_inv: pd.DataFrame) -> pd.DataFrame:
    g = per_inv.groupby("day")
    medians = g[[
        "stable_high_share", "r_high_all", "r_high_stable", "r_high_stable_unclipped",
        "clip_loss_pct", "nonclip_high_loss_pct", "high_share_pct",
    ]].median()
    summary = pd.concat([
        g.size().rename("n_inverters"),
        g["clipping"].count().rename("n_calibrated"),
        g["clipping"].mean().rename("clipping_share"),
        medians,
    ], axis=1).reset_index()
    summary["clear_stable"] = summary["stable_high_share"] >= CLEAR_STABLE_SHARE
    return summary


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Pemilah kekurangan daya di POA tinggi.")
    parser.add_argument("--baseline-dir", default="baseline")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--geometry", default=os.path.join("config", "site_geometry.yaml"))
    parser.add_argument("--config", default=os.path.join("config", "m2_config.yaml"))
    parser.add_argument("--poa-offset-min", type=float, default=None,
                        help="menimpa pyranometer.time_offset_minutes (mis. 5)")
    parser.add_argument("--min-smooth-share", type=float, default=0.3,
                        help="lewati hari dengan porsi POA mulus < ini (0 = baca semua)")
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args(argv)

    files = discover_baseline_csvs(args.baseline_dir, args.start_date, args.end_date)
    if not files:
        raise SystemExit(f"[saturation] tidak ada baseline CSV di {args.baseline_dir!r}")

    cfg = load_m2_config(args.config)
    gamma = PanelSpec.from_yaml(cfg["panel"]["spec_path"]).temp_coef.pmax_pct_per_c / 100.0
    tcell_source = str((cfg.get("m2f") or {}).get("tcell_source", "measured_per_ws"))
    empty_map = load_empty_pv_map(cfg)
    with open(args.geometry, "r", encoding="utf-8") as fp:
        geo = yaml.safe_load(fp) or {}
    pyr = geo.get("pyranometer") or {}
    offset = (args.poa_offset_min if args.poa_offset_min is not None
              else pyr.get("time_offset_minutes", 0.0))
    loader = PyranometerLoader(
        pyr["xlsx_path"], sheet=str(pyr.get("sheet", "POA PLTS IKN")),
        ws_to_wb=geo.get("ws_to_wb") or {}, time_offset_minutes=offset,
    )
    tcell_p = CellTempProvider.from_geometry_yaml(args.geometry)
    solar = PvlibClearSkyEstimator.from_geometry_yaml(args.geometry)
    print(f"[saturation] {len(files)} hari, offset POA {offset} menit, Tcell {tcell_source}")

    rows = []
    for day, path in files:
        smooth = poa_smooth_share(loader.df, day)
        if not smooth >= args.min_smooth_share:
            print(f"{day.date()}: dilewati (POA mulus {smooth:.2f} < {args.min_smooth_share})")
            continue
        frames = list(inverter_frames(path, empty_map))
        if not frames:
            print(f"{day.date()}: dilewati (tanpa inverter Grid connected)")
            continue
        idx_all = pd.DatetimeIndex(sorted(set().union(*(f[1] for f in frames))))
        elev = solar.get_solar_elevation(idx_all)
        by_wb = {}
        for inv, idx, p_dc, p_ac in frames:
            wb = inv[:4].upper()
            if wb not in by_wb:
                by_wb[wb] = (
                    loader.get_per_ws(idx_all, wb, fallback_to_avg=False),
                    tcell_p.get_tcell(idx_all, wb, source=tcell_source),
                )
            poa_s, tc_s = by_wb[wb]
            metrics = saturation_metrics(
                poa_s.reindex(idx).to_numpy(), p_dc, p_ac,
                tc_s.reindex(idx).to_numpy(), elev.reindex(idx).to_numpy(), gamma=gamma,
            )
            rows.append({"day": day, "inverter_id": inv, "wb_id": wb, **metrics})
        day_rows = pd.DataFrame(rows[-len(frames):])
        print(f"{day.date()}: stabil {day_rows['stable_high_share'].median():.2f}  "
              f"clipping {day_rows['clipping'].mean():.2f}  "
              f"r_all {day_rows['r_high_all'].median():.3f}  "
              f"r_stabil_tanpa_clip {day_rows['r_high_stable_unclipped'].median():.3f}")
    if not rows:
        raise SystemExit("[saturation] tidak ada inverter-hari yang terevaluasi")

    per_inv = pd.DataFrame(rows)[["day", "inverter_id", "wb_id", *SATURATION_METRICS]]
    summary = summarize(per_inv)
    os.makedirs(args.output_dir, exist_ok=True)
    out = os.path.join(
        args.output_dir,
        f"saturation_check_{files[0][0]:%Y%m%d}_{files[-1][0]:%Y%m%d}.xlsx",
    )
    with pd.ExcelWriter(out) as writer:
        summary.to_excel(writer, sheet_name="summary", index=False)
        per_inv.to_excel(writer, sheet_name="per_inverter_day", index=False)
    clear = summary[summary["clear_stable"]]
    print(f"\nHari cerah-stabil: {len(clear)}/{len(summary)}")
    if len(clear):
        cols = ["r_high_all", "r_high_stable", "r_high_stable_unclipped",
                "clip_loss_pct", "nonclip_high_loss_pct", "clipping_share"]
        print(clear[cols].median().round(3).to_string())
    print("ditulis:", out)


if __name__ == "__main__":
    main()
