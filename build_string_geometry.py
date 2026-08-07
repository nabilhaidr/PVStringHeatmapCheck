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
    BLOCK_GAP_M,
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

# --- Phase One (WB01/WB02) ----------------------------------------------------
# Sumbernya gambar tray AC, bukan gambar string: 7.840 entitas teks, hanya 900
# di layer di bawah ini. Tanpa penyaringan layer, dimensi BOQ dan teks kop
# gambar ikut terbaca.
PHASE_ONE_DXF_PREFIX = "Cable Routing"
PHASE_ONE_LAYER = "_TEXT_STRING"
PHASE_ONE_LABEL_RE = re.compile(r"^S([12])(\d{2})-(\d{2})$")
# Tiap label membawa kode format MTEXT di depan nilainya (mis.
# "\W1.23077x;S101-18"). Tanpa dibersihkan tidak satu pun dari 900 label
# cocok dengan polanya dan parser mengembalikan daftar KOSONG -- gagal diam.
MTEXT_FORMAT_RE = re.compile(r"\\[A-Za-z][^;\\]*;|[{}]")
# S226 sisa revisi gambar: sudah diubah menjadi S125 = WB01-INV25, tapi sheet
# tray AC masih membawa label lamanya. Dibaca apa adanya ia menjadi
# WB02-INV26 yang TIDAK ADA di telemetri, sekaligus meninggalkan WB01-INV25
# tanpa koordinat. Bukti spasial sejalan: S226 duduk di blok barat, terpisah
# ~99 m dari seluruh gugus S2xx.
PHASE_ONE_REVISED = {(2, 26): (1, 25)}
# WB01/WB02 memakai Huawei SUN2000-215KTL: 9 MPPT x 2 string berurutan.
# Tabel pasangannya TIDAK ditulis ulang di sini -- lihat phase_one_mppt_map().
PHASE_ONE_MODEL = "SUN2000-215KTL-H0"
STRINGS_YAML = os.path.join("config", "strings.yaml")

# --- koreksi penomoran inverter di 1129.dxf -----------------------------------
# Dua blok salah dinomori. Ditemukan lewat JUMLAH STRING per inverter yang
# dicocokkan ke as-built DC cable list -- bukti yang tidak bergantung koordinat
# sama sekali -- lalu dikuatkan susunan kolom pada General Layout DW-001.
#
# WB04 melewatkan INV17 lalu menggeser sisanya naik satu: label INV18 membawa
# 27 string sementara as-built INV18 punya 24 dan INV17 punya 27; ketiga
# pergeserannya cocok berurutan (27, 24, 23).
DXF_RELABEL = {
    (4, 18): (4, 17), (4, 19): (4, 18), (4, 20): (4, 19),
    # WB05 berhenti di INV19 baik menurut as-built maupun General Layout, jadi
    # label INV20 sepenuhnya milik WB06.
    (5, 20): (6, 20),
}
# Label INV15-INV19 di WB05 dipakai DUA KALI: sekali untuk array WB05, sekali
# untuk array WB06 ratusan meter di timurnya. Gugus TIMUR adalah WB06 dengan
# nomor inverter yang sama. Jumlah kedua sisi cocok persis dengan as-built pada
# keenam label, jadi pemisahannya memverifikasi dirinya sendiri.
DXF_SPLIT_EAST = {(5, i): (6, i) for i in range(15, 20)}
# Beberapa label muncul dua kali DI DALAM satu inverter. Salinan yang nyasar
# ternyata melanjutkan grid inverter LAIN -- dan inverter itu kekurangan ST yang
# sama persis menurut as-built, jadi kedua sisinya saling menutup. Diurutkan
# menaik karena ST14 baru bisa dijangkar setelah ST13 pindah.
DXF_STRAY = {
    (3, 11, 13): (3, 8, 13),
    (3, 11, 14): (3, 8, 14),
    (3, 11, 15): (3, 8, 15),
    (5, 14, 25): (6, 14, 25),
}

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


def _iter_dxf_text(path: str):
    """Streaming entitas teks DXF -> {label, layer, east, north}.

    Per pasangan (kode, nilai) karena DXF hasil export bisa ratusan MB --
    gambar tray AC yang satu itu 337 MB. Kode 8 = layer, 10 = easting,
    20 = northing.
    """
    cur: Optional[Dict] = None
    code: Optional[str] = None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if code is None:
                token = line.strip()
                code = token if token.lstrip("-").isdigit() else None
                continue
            value, current_code, code = line.rstrip("\r\n"), code, None
            if current_code == "0":
                if cur:
                    yield cur
                cur = {} if value.strip().upper() in TEXT_ENTITIES else None
            elif cur is not None:
                if current_code == "1":
                    cur["label"] = value.strip()
                elif current_code == "8":
                    cur["layer"] = value.strip()
                elif current_code == "10":
                    cur["east"] = float(value)
                elif current_code == "20":
                    cur["north"] = float(value)
    if cur:
        yield cur


def parse_dxf_string_labels(path: str) -> List[Dict]:
    """Label string WB03-WB10 -> [{label, wb, inv, st, north, east}, ...].

    Label tanpa titik sisip dilewati: memberinya koordinat 0 akan menaruh
    string di lepas pantai Afrika tanpa ada yang menyadari.
    """
    rows: List[Dict] = []
    for entry in _iter_dxf_text(path):
        match = LABEL_RE.match(entry.get("label", ""))
        if not match or "east" not in entry or "north" not in entry:
            continue
        rows.append({
            "label": entry["label"],
            "wb": int(match.group(1)),
            "inv": int(match.group(2)),
            "st": int(match.group(3)),
            "east": entry["east"],
            "north": entry["north"],
        })
    return rows


def parse_phase_one_labels(path: str) -> List[Dict]:
    """Label WB01/WB02 -> [{label, wb, inv, st, pv, mppt, north, east}, ...].

    Konvensi Phase One (ISPP/Tractebel) berbeda sama sekali dari
    ``WB##INV##ST##`` milik SEPEC: ``S<blok><inv>-<st>``, blok 1 = WB01,
    blok 2 = WB02.

    ``pv`` diisi dari ``st`` karena di sini nomor ST lapangan ADALAH kanal
    PV Huawei. Itu sebabnya Phase One tidak butuh as-built DC cable list
    sama sekali, berbeda dengan WB03-WB10 yang pemetaan ST->PV-nya hanya
    ada di sana. MPPT-nya menyusul lewat ``phase_one_mppt_map()``.
    """
    rows: List[Dict] = []
    for entry in _iter_dxf_text(path):
        if entry.get("layer") != PHASE_ONE_LAYER:
            continue
        label = MTEXT_FORMAT_RE.sub("", entry.get("label", "")).strip()
        match = PHASE_ONE_LABEL_RE.match(label)
        if not match or "east" not in entry or "north" not in entry:
            continue
        wb, inv = int(match.group(1)), int(match.group(2))
        wb, inv = PHASE_ONE_REVISED.get((wb, inv), (wb, inv))
        st = int(match.group(3))
        rows.append({
            "label": label, "wb": wb, "inv": inv, "st": st, "pv": st,
            "east": entry["east"], "north": entry["north"],
        })
    return rows


def resolve_dxf_relabels(labels: List[Dict]) -> List[Dict]:
    """Perbaiki penomoran inverter yang salah di 1129.dxf.

    Dua cacat berbeda, dua penanganan berbeda:

    * ``DXF_RELABEL`` -- seluruh label memang milik inverter lain (WB04 yang
      penomorannya bergeser, dan WB05-INV20 yang tidak pernah ada).
    * ``DXF_SPLIT_EAST`` -- satu label dipakai dua array berbeda. Gugus TIMUR
      pindah blok; gugus barat tetap. Pemisahnya celah easting terbesar,
      dan hanya diterima bila celah itu melebihi ``BLOCK_GAP_M`` -- ambang
      pemisah petak yang sudah diturunkan dari histogram jarak patok. Celah
      nyatanya 71-334 m, jauh di atas lebar satu inverter (~50 m).
    """
    keluar = [dict(row) for row in labels]
    for row in keluar:
        baru = DXF_RELABEL.get((row["wb"], row["inv"]))
        if baru:
            row["wb"], row["inv"] = baru

    for kunci, baru in DXF_SPLIT_EAST.items():
        grup = [r for r in keluar if (r["wb"], r["inv"]) == kunci]
        if len(grup) < 2:
            continue
        grup.sort(key=lambda r: r["east"])
        jarak = [grup[i + 1]["east"] - grup[i]["east"] for i in range(len(grup) - 1)]
        lebar = max(jarak)
        if lebar <= BLOCK_GAP_M:
            continue
        for row in grup[jarak.index(lebar) + 1:]:
            row["wb"], row["inv"] = baru

    for (wb, inv, st), (wb2, inv2, st2) in DXF_STRAY.items():
        salinan = [r for r in keluar if (r["wb"], r["inv"], r["st"]) == (wb, inv, st)]
        acuan = _tetangga_tunggal(keluar, wb2, inv2, st2)
        if len(salinan) != 2 or acuan is None:
            continue
        nyasar = min(salinan, key=lambda r: math.hypot(r["east"] - acuan["east"],
                                                       r["north"] - acuan["north"]))
        nyasar["wb"], nyasar["inv"], nyasar["st"] = wb2, inv2, st2
    return keluar


def _tetangga_tunggal(rows: List[Dict], wb: int, inv: int, st: int) -> Optional[Dict]:
    """String bernomor bersebelahan pada inverter tujuan, bila tidak ambigu.

    Dipakai sebagai jangkar grid: slot yang ditinggalkan as-built pasti
    bersebelahan dengan ST sebelum atau sesudahnya.
    """
    for tetangga in (st - 1, st + 1):
        cocok = [r for r in rows if (r["wb"], r["inv"], r["st"]) == (wb, inv, tetangga)]
        if len(cocok) == 1:
            return cocok[0]
    return None


def phase_one_mppt_map(path: str = STRINGS_YAML) -> Dict[int, int]:
    """PV -> MPPT untuk WB01/WB02, dibaca dari ``config/strings.yaml``.

    Fakta ini sudah dimiliki repo, berkunci model inverter, dan strings.yaml
    adalah acuan yang tervalidasi telemetri. Menyalin pasangannya ke builder
    ini akan membuat dua rumah untuk satu fakta yang sama.
    """
    import yaml

    with open(path, encoding="utf-8") as handle:
        entry = yaml.safe_load(handle)["mppt_map"][PHASE_ONE_MODEL]
    return {pv: mppt for mppt, pvs in entry["mppt"].items() for pv in pvs}


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


def _geom_row(item: Dict, image, header, pv, mppt) -> Dict:
    """Satu baris string_geometry.csv dari label + DSM."""
    lat, lon = utm50s_to_latlon(item["north"], item["east"])
    plane = local_plane(image, header, item["north"], item["east"])
    clean = plane is not None and plane["rms_m"] <= MAX_PLANE_RMS_M
    return {
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
    }


def main() -> None:
    dxf_path = find_raw(DXF_NAME)
    labels = resolve_dxf_relabels(parse_dxf_string_labels(dxf_path))
    if not labels:
        raise SystemExit(f"{dxf_path}: tidak ada label string ditemukan.")
    print(f"[string-geometry] {dxf_path}: {len(labels)} label string")

    dsm_file = dsm_path()
    image, header = open_dsm(dsm_file)
    if image is None:
        raise SystemExit(f"DSM tidak ada: {dsm_file}")
    st_map = _st_to_pv()

    rows: List[Dict] = [
        _geom_row(item, image, header,
                  *st_map.get((item["wb"], item["inv"], item["st"]),
                              (None, None)))
        for item in labels
    ]

    # Phase One (WB01/WB02) datang dari gambar tray AC: pv = st, dan MPPT dari
    # strings.yaml. Blok ini karena itu tidak menyentuh as-built cable list
    # sama sekali -- satu-satunya bagian site yang pemetaan kanalnya diketahui
    # penuh tanpanya.
    phase_one_path = find_raw(PHASE_ONE_DXF_PREFIX, required=False)
    if phase_one_path:
        phase_one = parse_phase_one_labels(phase_one_path)
        mppt_by_pv = phase_one_mppt_map()
        print(f"[string-geometry] {phase_one_path}: "
              f"{len(phase_one)} label Phase One")
        rows += [_geom_row(item, image, header, item["pv"],
                           mppt_by_pv.get(item["pv"]))
                 for item in phase_one]
    else:
        print(f"[string-geometry] {PHASE_ONE_DXF_PREFIX}*.dxf tidak ada -> "
              f"WB01/WB02 tanpa koordinat")

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
