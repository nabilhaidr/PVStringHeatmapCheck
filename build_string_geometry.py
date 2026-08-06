"""Koordinat + kemiringan tanah PER STRING -> config/string_geometry.csv.

Sumber:
1. ``raw data input/1129.dxf`` -- export DXF dari 1129.dwg. Layer
   "String number" memuat 3.570 label ``WB##INV##ST##`` beserta titik
   sisipnya dalam WGS 84 / UTM zone 50S. Ini satu-satunya sumber koordinat
   per string yang tersedia; gambar PDF hanya memberi patok setting-out
   per petak.
2. ``dsm.tif`` survei topografi (0,1187 m/piksel) untuk elevasi dan bidang
   tanah lokal di posisi tiap string.
3. ``List of DC Cables 0411.xls`` untuk memetakan nomor ST (sisi lapangan)
   ke nomor PV Huawei (sisi telemetri), supaya artefak ini bisa di-join ke
   data monitoring.

Kenapa penting: foto lapangan 2026-08-06 dan foto drone memastikan meja PV
di WB03-WB10 MENGIKUTI kontur berbukit -- tidak diratakan di atas bench.
Karena itu kemiringan tanah di posisi sebuah string adalah orientasi bidang
modulnya, dan ``cross_slope_deg`` (komponen timur-barat) menentukan berapa
besar bias pagi-sore yang murni geometris pada perbandingan antar-sibling.

Jalankan: python build_string_geometry.py
"""
from __future__ import annotations

import csv
import math
import os
import re
from typing import Dict, List, Optional

from build_site_layout import (
    dsm_path,
    find_raw,
    fit_plane,
    open_dsm,
    sample_dsm,
    utm50s_to_latlon,
)

RAW_DIR = "raw data input"
DXF_NAME = "1129.dxf"
CABLE_NAME = "List of DC Cables 0411.xls"
OUT_PATH = os.path.join("config", "string_geometry.csv")

LABEL_RE = re.compile(r"^WB(\d{2})INV(\d{2})ST(\d+)$", re.IGNORECASE)
TEXT_ENTITIES = {"TEXT", "MTEXT", "ATTRIB"}

# Jendela fit bidang di posisi string: 15 m timur-barat (panjang satu meja)
# x 4 m utara-selatan, langkah 0,5 m. Cukup lebar untuk meredam kekasaran
# tanah, cukup sempit untuk tetap mewakili meja itu sendiri.
WIN_EW_M = 7.5
WIN_NS_M = 2.0
WIN_STEP_M = 0.5
# rms di atas ini = permukaan tidak cukup planar (vegetasi/tanah kasar).
MAX_PLANE_RMS_M = 0.5

COLUMNS = [
    "inverter_id", "st", "pv", "mppt", "north", "east", "lat", "lon",
    "elev_m", "slope_deg", "aspect_deg", "cross_slope_deg", "plane_rms_m",
]


def parse_dxf_string_labels(path: str) -> List[Dict]:
    """Baca DXF -> [{label, wb, inv, st, north, east}, ...].

    Streaming per pasangan (kode, nilai) karena DXF hasil export bisa
    ratusan MB. Kode 10 = easting, 20 = northing. Label tanpa titik sisip
    dilewati: memberinya koordinat 0 akan menaruh string di lepas pantai
    Afrika tanpa ada yang menyadari.
    """
    rows: List[Dict] = []
    cur: Optional[Dict] = None
    code: Optional[str] = None

    def flush(entry: Optional[Dict]) -> None:
        if not entry:
            return
        match = LABEL_RE.match(entry.get("label", ""))
        if not match or "east" not in entry or "north" not in entry:
            return
        rows.append({
            "label": entry["label"],
            "wb": int(match.group(1)),
            "inv": int(match.group(2)),
            "st": int(match.group(3)),
            "east": entry["east"],
            "north": entry["north"],
        })

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if code is None:
                token = line.strip()
                code = token if token.lstrip("-").isdigit() else None
                continue
            value, current_code, code = line.rstrip("\r\n"), code, None
            if current_code == "0":
                flush(cur)
                cur = {} if value.strip().upper() in TEXT_ENTITIES else None
            elif cur is not None:
                if current_code == "1":
                    cur["label"] = value.strip()
                elif current_code == "10":
                    cur["east"] = float(value)
                elif current_code == "20":
                    cur["north"] = float(value)
    flush(cur)
    return rows


def cross_slope_deg(slope_deg: float, aspect_deg: float) -> float:
    """Komponen timur-barat dari kemiringan tanah, BERTANDA.

    Positif = tanah turun ke TIMUR = bidang modul condong ke timur = pagi
    lebih kuat. Komponen utara-selatan sengaja diabaikan: struktur DW-003
    menyediakan varian lereng utara/selatan yang menjaga tilt 10 derajat,
    jadi hanya komponen menyamping yang memutar bidang modul.
    """
    return math.degrees(math.atan(
        math.tan(math.radians(slope_deg)) * math.sin(math.radians(aspect_deg))
    ))


def local_plane(image, header, north: float, east: float) -> Optional[Dict]:
    """Fit bidang tanah pada jendela seukuran meja di sekitar satu string."""
    samples = []
    n_steps = int(round(WIN_NS_M / 1.0))
    e_steps = int(round(WIN_EW_M / WIN_STEP_M))
    for i in range(-n_steps, n_steps + 1):
        for j in range(-e_steps, e_steps + 1):
            north_i, east_j = north + i * 1.0, east + j * WIN_STEP_M
            z = sample_dsm(image, header, north_i, east_j)
            if z is not None:
                samples.append((north_i, east_j, z))
    return fit_plane(samples)


def _st_to_pv() -> Dict:
    """(wb, inv, st) -> (pv, mppt) dari as-built DC cable list. {} bila absen."""
    cable_path = find_raw(CABLE_NAME, required=False)
    if cable_path is None:
        print(f"[string-geometry] cable list tidak ada ({CABLE_NAME}); "
              f"kolom pv/mppt dikosongkan.")
        return {}
    from pv_pipeline.m2a.cleaning_report import build_st_to_pv, load_dc_cable_map

    return build_st_to_pv(load_dc_cable_map(cable_path))


def main() -> None:
    dxf_path = find_raw(DXF_NAME)
    labels = parse_dxf_string_labels(dxf_path)
    if not labels:
        raise SystemExit(f"{dxf_path}: tidak ada label string ditemukan.")
    print(f"[string-geometry] {dxf_path}: {len(labels)} label string")

    dsm_file = dsm_path()
    image, header = open_dsm(dsm_file)
    if image is None:
        raise SystemExit(f"DSM tidak ada: {dsm_file}")
    st_map = _st_to_pv()

    rows: List[Dict] = []
    for item in labels:
        lat, lon = utm50s_to_latlon(item["north"], item["east"])
        pv, mppt = st_map.get((item["wb"], item["inv"], item["st"]), (None, None))
        plane = local_plane(image, header, item["north"], item["east"])
        clean = plane is not None and plane["rms_m"] <= MAX_PLANE_RMS_M
        rows.append({
            "inverter_id": f"WB{item['wb']:02d}-INV{item['inv']:02d}",
            "st": item["st"],
            "pv": pv,
            "mppt": mppt,
            "north": round(item["north"], 3),
            "east": round(item["east"], 3),
            "lat": round(lat, 7),
            "lon": round(lon, 7),
            "elev_m": sample_dsm(image, header, item["north"], item["east"]),
            "slope_deg": plane["slope_deg"] if clean else None,
            "aspect_deg": plane["aspect_deg"] if clean else None,
            "cross_slope_deg": (
                round(cross_slope_deg(plane["slope_deg"], plane["aspect_deg"]), 2)
                if clean else None
            ),
            "plane_rms_m": plane["rms_m"] if plane else None,
        })

    rows.sort(key=lambda r: (r["inverter_id"], r["st"]))
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    n_pv = sum(1 for r in rows if r["pv"] is not None)
    n_plane = sum(1 for r in rows if r["cross_slope_deg"] is not None)
    print(f"[string-geometry] pv termapping : {n_pv}/{len(rows)}")
    print(f"[string-geometry] bidang bersih : {n_plane}/{len(rows)} "
          f"(rms <= {MAX_PLANE_RMS_M} m)")
    print(f"[string-geometry] ditulis: {OUT_PATH} "
          f"({os.path.getsize(OUT_PATH) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
