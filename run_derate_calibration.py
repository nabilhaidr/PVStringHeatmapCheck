"""Kalibrasi m2f.dc_derate_per_wb: rasio harian M2f vs langit dan debu.

Rancangan: docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md

Usage:
    python run_derate_calibration.py --m2f-dir "F:/Downloads part 2/cek pv/m2f" \
        --raw-root "F:/Downloads part 2" [--hanya-sesudah "2026-09-29 22:13"]

Config TIDAK diubah: potongan YAML dicetak hanya untuk WB berkeputusan
"konstanta", untuk disalin setelah disetujui.
"""
from __future__ import annotations

import argparse
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from pv_pipeline.m2f.derate_calibration import (  # noqa: E402
    fit_wb, hari_sejak_hujan, kt_poa, porsi_kosong, putuskan, validasi,
)
from pv_pipeline.poa.loader import PyranometerLoader  # noqa: E402
from pv_pipeline.poa.pvlib_estimator import PvlibClearSkyEstimator  # noqa: E402
from rekap_m2f import build_daily_calib, discover_m2f_xlsx, load_day  # noqa: E402
from run_saturation_check import poa_smooth_share  # noqa: E402

MIN_N_CALIB = 100
MAKS_POA_KOSONG = 0.5


def _muat_poa(geometry: str, raw_root: str, offset, *, koreksi: bool = True):
    """PyranometerLoader dari geometri; path POA relatif diawali ``raw_root``.

    ``koreksi=False`` mengabaikan blok ``pyranometer.koreksi`` (POA mentah).
    """
    with open(geometry, "r", encoding="utf-8") as fp:
        geo = yaml.safe_load(fp) or {}
    pyr = geo.get("pyranometer") or {}
    paths = pyr["xlsx_path"] if isinstance(pyr["xlsx_path"], list) else [pyr["xlsx_path"]]
    paths = [p if os.path.isabs(p) else os.path.join(raw_root, p) for p in paths]
    off = float(pyr.get("time_offset_minutes", 0.0) if offset is None else offset)
    loader = PyranometerLoader(paths, sheet=str(pyr.get("sheet", "POA PLTS IKN")),
                               ws_to_wb=geo.get("ws_to_wb") or {}, time_offset_minutes=off,
                               koreksi=(pyr.get("koreksi") if koreksi else None))
    return loader, off


def bangun_harian(rasio: pd.DataFrame, loader, estimator, hujan: pd.Series) -> pd.DataFrame:
    """Satu baris per WB-hari: rasio M2f + indeks langit (stasiun cuaca WB itu) + debu + saringan."""
    baris = []
    for hari, g in rasio.groupby("date", sort=True):
        hari = pd.Timestamp(hari).normalize()
        idx = pd.date_range(hari + pd.Timedelta(hours=6), hari + pd.Timedelta(hours=18), freq="5min")
        cerah, elev = estimator.estimate(idx), estimator.get_solar_elevation(idx)
        for r in g.itertuples(index=False):
            wb = str(r.wb_id).upper()
            poa = loader.get_per_ws(idx, wb, fallback_to_avg=False)
            ws = loader.wb_to_ws.get(wb)
            baris.append({
                "date": hari, "wb_id": wb, "rasio": float(r.measured_ratio),
                "n_calib": int(r.n_calib_string_days), "kt": kt_poa(poa, cerah, elev),
                "mulus": poa_smooth_share(loader.df[[ws]], hari) if ws in loader.df.columns else np.nan,
                "poa_kosong": porsi_kosong(poa, elev),
            })
    t = pd.DataFrame(baris)
    t["hari_sejak_hujan"] = hari_sejak_hujan(hujan, pd.DatetimeIndex(t["date"])).to_numpy()
    t["alasan"] = np.select(
        [t["n_calib"] < MIN_N_CALIB, t["poa_kosong"] > MAKS_POA_KOSONG,
         t[["rasio", "kt", "mulus"]].isna().any(axis=1)],
        [f"n_calib < {MIN_N_CALIB}", f"poa_kosong > {MAKS_POA_KOSONG}", "nilai kosong"], default="")
    t["dibuang"] = t["alasan"] != ""
    return t


def _gambar(sah: pd.DataFrame, path: str) -> None:
    """Rasio terhadap Kt per WB; warna = hari sejak hujan (debu)."""
    wbs = sorted(sah["wb_id"].unique())
    kolom = min(5, len(wbs))
    baris = int(np.ceil(len(wbs) / kolom))
    fig, ax = plt.subplots(baris, kolom, figsize=(3.2 * kolom, 2.8 * baris), squeeze=False,
                           sharex=True, sharey=True)
    for a, wb in zip(ax.ravel(), wbs):
        g = sah[sah["wb_id"] == wb]
        a.scatter(g["kt"], g["rasio"], s=12, c=g["hari_sejak_hujan"], cmap="viridis")
        a.set_title(wb, fontsize=9)
    for a in ax.ravel()[len(wbs):]:
        a.axis("off")
    fig.supxlabel("Kt POA")
    fig.supylabel("measured_ratio")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Kalibrasi m2f.dc_derate_per_wb dari workbook M2f harian.")
    ap.add_argument("--m2f-dir", required=True)
    ap.add_argument("--raw-root", default=".", help="awalan path POA relatif di site_geometry.yaml")
    ap.add_argument("--geometry", default=os.path.join("config", "site_geometry.yaml"))
    ap.add_argument("--precip", default=os.path.join("coba", "precipitation_daily_plts_ikn.csv"))
    ap.add_argument("--poa-offset-min", type=float, default=None)
    ap.add_argument("--hanya-sesudah", default=None, help="'YYYY-MM-DD HH:MM': workbook dimodifikasi sesudahnya")
    ap.add_argument("--output-dir", default="coba")
    a = ap.parse_args(argv)

    found = discover_m2f_xlsx(a.m2f_dir)
    if a.hanya_sesudah:
        batas = pd.Timestamp(a.hanya_sesudah).timestamp()
        found = [(d, p) for d, p in found if os.path.getmtime(p) >= batas]
    if not found:
        raise SystemExit(f"[derate] tidak ada workbook harian di {a.m2f_dir!r}")
    rasio = build_daily_calib([load_day(p) for _, p in found])
    loader, offset = _muat_poa(a.geometry, a.raw_root, a.poa_offset_min)
    estimator = PvlibClearSkyEstimator.from_geometry_yaml(a.geometry, load_albedo_provider=False)
    hujan = pd.read_csv(a.precip, parse_dates=["date"]).set_index("date")["precipitation_mm"]

    harian = bangun_harian(rasio, loader, estimator, hujan)
    sah = harian[~harian["dibuang"]]
    fits = {wb: fit_wb(g) for wb, g in sah.groupby("wb_id")}
    per_wb = pd.DataFrame([{"wb_id": wb, **f, **putuskan(f)} for wb, f in sorted(fits.items())])
    derate = {r.wb_id: r.nilai for r in per_wb.itertuples() if r.keputusan == "konstanta"}
    val = validasi(sah, fits, derate)

    awal, akhir = pd.Timestamp(rasio["date"].min()), pd.Timestamp(rasio["date"].max())
    bulan = (akhir.year - awal.year) * 12 + akhir.month - awal.month + 1
    dibuang = harian.loc[harian["dibuang"], "alasan"].value_counts()
    catatan = pd.DataFrame({"butir": [
        "rentang", "workbook", "WB-hari", "WB-hari dibuang", "offset POA (menit)", "label musim",
        "ambang", "hanya_sesudah", "koreksi POA"], "nilai": [
        f"{awal:%Y-%m-%d}..{akhir:%Y-%m-%d}", len(found), len(harian),
        "; ".join(f"{k}: {v}" for k, v in dibuang.items()) or "0",
        offset, "satu musim" if bulan < 3 else f"{bulan} bulan",
        f"ayunan 0.03; |t| 2; min 20 hari; n_calib {MIN_N_CALIB}; poa_kosong {MAKS_POA_KOSONG}; hujan 5 mm; maks 30",
        a.hanya_sesudah or "-",
        f"aktif ({loader.koreksi.get('sumber', '-')})" if getattr(loader, "koreksi_aktif", False) else "tidak aktif"]})
    os.makedirs(a.output_dir, exist_ok=True)
    dasar = os.path.join(a.output_dir, f"derate_calibration_{awal:%Y%m%d}_{akhir:%Y%m%d}")
    with pd.ExcelWriter(dasar + ".xlsx") as w:
        harian.to_excel(w, sheet_name="Harian", index=False)
        per_wb.to_excel(w, sheet_name="PerWB", index=False)
        val.to_excel(w, sheet_name="Validasi", index=False)
        catatan.to_excel(w, sheet_name="Catatan", index=False)
    if len(sah):
        _gambar(sah, dasar + ".png")

    print(f"[derate] {len(found)} workbook, {len(harian)} WB-hari "
          f"({int(harian['dibuang'].sum())} dibuang) -> {dasar}.xlsx")
    print(per_wb[["wb_id", "n", "b", "t_b", "c", "t_c", "e", "t_e", "ayunan_langit", "rasio_bersih",
                  "keputusan"]].round(4).to_string(index=False))
    if derate:
        print("\n# usulan (BELUM diterapkan) -- salin setelah disetujui:\ndc_derate_per_wb:")
        for wb, v in sorted(derate.items()):
            print(f"  {wb}: {v:.3f}")


if __name__ == "__main__":
    main()
