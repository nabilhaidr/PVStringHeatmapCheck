"""Kalibrasi silang POA antar-stasiun cuaca: laporan bias sensor dan jam penghalang.

Rancangan: docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md

Usage:
    python run_poa_cross_calibration.py --raw-root "F:/Downloads part 2" \
        [--mulai 2025-01-01] [--akhir 2026-07-31] [--m2f-dir "F:/Downloads part 2/cek pv/m2f"]

Config dan loader TIDAK diubah: usulan pyranometer.ws_gain / ws_jam_penghalang
dicetak (BELUM dibaca loader) untuk diputuskan pemilik dokumen.
"""
from __future__ import annotations

import argparse
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from pv_pipeline.poa.kalibrasi_silang import (  # noqa: E402
    POA_MAKS, gain_absolut, gain_bulanan, gain_larik, gain_relatif, penghalang, profil_jam,
    rasio_ke_median, sampel_stabil, sepakati,
)
from pv_pipeline.poa.pvlib_estimator import PvlibClearSkyEstimator  # noqa: E402
from rekap_m2f import build_daily_calib, discover_m2f_xlsx, load_day  # noqa: E402
from run_derate_calibration import _muat_poa  # noqa: E402


def _gambar(bulanan: pd.DataFrame, profil: pd.DataFrame, path: str) -> None:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4))
    for ws, g in bulanan.dropna(subset=["median"]).groupby("ws"):
        a1.plot(g["bulan"], g["median"], marker="o", label=ws)
    a1.axhline(1.0, color="grey", lw=0.8)
    a1.set_title("Gain bulanan (vs median WS lain)")
    a1.tick_params(axis="x", rotation=90)
    a1.legend(fontsize=8)
    for ws, g in profil.groupby("ws"):
        m = g.groupby("jam")["profil"].median()
        a2.plot(m.index, m.values, marker="o", label=ws)
    a2.axhline(0.9, color="red", lw=0.8, ls="--")
    a2.set_title("Profil per jam (median antar-bulan)")
    a2.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Kalibrasi silang POA antar-stasiun cuaca.")
    ap.add_argument("--raw-root", default=".", help="awalan path POA relatif di site_geometry.yaml")
    ap.add_argument("--mulai", default="2025-01-01")
    ap.add_argument("--akhir", default="2026-07-31")
    ap.add_argument("--m2f-dir", default=None, help="folder workbook M2f harian untuk acuan larik")
    ap.add_argument("--geometry", default=os.path.join("config", "site_geometry.yaml"))
    ap.add_argument("--output-dir", default="coba")
    a = ap.parse_args(argv)

    loader, offset = _muat_poa(a.geometry, a.raw_root, None)
    kolom = [c for c in loader.df.columns if str(c).startswith("WS-")]
    poa = loader.df.loc[a.mulai:f"{a.akhir} 23:59:59", kolom]
    galat = int(((poa < 0) | (poa > POA_MAKS)).sum().sum())

    stabil = sampel_stabil(poa)
    rasio = rasio_ke_median(poa, stabil)
    bulanan = gain_bulanan(rasio)
    profil = profil_jam(rasio)
    hal = penghalang(profil)
    rel = gain_relatif(rasio, hal)
    estimator = PvlibClearSkyEstimator.from_geometry_yaml(a.geometry, load_albedo_provider=False)
    absolut = gain_absolut(poa, estimator.estimate(poa.index), stabil)
    larik = None
    if a.m2f_dir:
        kal = build_daily_calib([load_day(p) for _, p in discover_m2f_xlsx(a.m2f_dir)])
        larik = gain_larik(kal, loader.wb_to_ws)
    sep = sepakati(rel, absolut, larik)

    awal, akhir = pd.Timestamp(a.mulai), pd.Timestamp(a.akhir)
    catatan = pd.DataFrame({"butir": [
        "rentang", "sampel stabil per WS", "sampel galat (<0 atau >1400)", "offset POA (menit)",
        "acuan larik", "ambang"], "nilai": [
        f"{awal:%Y-%m-%d}..{akhir:%Y-%m-%d}",
        "; ".join(f"{ws}: {int(n)}" for ws, n in stabil.sum().items()), galat, offset,
        a.m2f_dir or "-",
        "stabil 2 %; POA > 300; 09-15; min 2 pembanding; 200 sampel/bulan; penghalang 10 % x 3 bulan; "
        "bergeser 5 %; sepakat 3 %; Kt sangat cerah 0,75"]})
    os.makedirs(a.output_dir, exist_ok=True)
    dasar = os.path.join(a.output_dir, f"poa_cross_calibration_{awal:%Y%m%d}_{akhir:%Y%m%d}")
    with pd.ExcelWriter(dasar + ".xlsx") as w:
        bulanan.to_excel(w, sheet_name="Bulanan", index=False)
        profil.to_excel(w, sheet_name="ProfilJam", index=False)
        hal.to_excel(w, sheet_name="Penghalang", index=False)
        rel.to_excel(w, sheet_name="Relatif", index=False)
        absolut.to_excel(w, sheet_name="Absolut", index=False)
        (larik if larik is not None else pd.DataFrame(columns=["ws", "gain", "n"])).to_excel(
            w, sheet_name="Larik", index=False)
        sep.to_excel(w, sheet_name="Kesepakatan", index=False)
        catatan.to_excel(w, sheet_name="Catatan", index=False)
    _gambar(bulanan, profil, dasar + ".png")

    print(f"[poa-silang] {awal:%Y-%m-%d}..{akhir:%Y-%m-%d} -> {dasar}.xlsx")
    print(sep.round(3).to_string(index=False))
    usul = sep[sep["status"] == "usulan_koreksi"]
    if len(usul) or len(hal):
        print("\n# usulan (BELUM diterapkan; loader belum membaca kunci ini):\npyranometer:")
        if len(usul):
            print("  ws_gain:")
            for r in usul.itertuples():
                print(f"    {r.ws}: {r.usulan:.3f}")
        if len(hal):
            print("  ws_jam_penghalang:")
            for ws, g in hal.groupby("ws"):
                print(f"    {ws}: {sorted(int(j) for j in g['jam'])}")


if __name__ == "__main__":
    main()
