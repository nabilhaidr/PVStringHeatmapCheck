"""Ekstrak tabel koordinat setting-out DW-004 -> config/site_layout.yaml.

Sumber: ``raw data input/IKN-CE-PP-DW-004 Mounting Foundation Layout
Drawing.pdf`` (8 halaman WB03..WB10). Tiap halaman memuat "Coordinate
Table" berisi NO / X / Y, dengan X = Northing dan Y = Easting pada WGS 84
UTM zone 50S (EPSG:32750).

BUKAN koordinat per string. Titik-titik ini adalah patok setting-out blok
array (74-141 titik per WB, sementara WB03 saja punya 366 string), jadi
hasilnya cukup untuk peta ikhtisar dan pin per petak -- bukan pin per
string. Elevasi (mdpl) TIDAK ikut: angka spot level dan garis kontur pada
gambar sudah di-outline jadi geometri, tidak ada di lapisan teks PDF.

Jalankan: python build_site_layout.py
"""
from __future__ import annotations

import math
import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

TextItem = Tuple[float, float, str]  # (x_pdf, y_pdf, teks)

PDF_PREFIX = "IKN-CE-PP-DW-004"
RAW_DIR = "raw data input"
OUT_PATH = os.path.join("config", "site_layout.yaml")
SITE_GEOMETRY_PATH = os.path.join("config", "site_geometry.yaml")

# Tabel DW-004 memakai 3 desimal, callout ISPP halaman 19 memakai 4.
NORTHING_RE = re.compile(r"^98\d{5}\.\d{1,4}$")
EASTING_RE = re.compile(r"^4\d{5}\.\d{1,4}$")
INT_RE = re.compile(r"^\d{1,3}$")
WB_TITLE_RE = re.compile(r"Drawing of WB0?(\d+)")

ROW_TOL = 2.0        # toleransi y (unit PDF) supaya dianggap satu baris
NO_MAX_GAP = 400.0   # jarak x maksimum kolom NO -> kolom X

# WGS 84 + UTM zone 50S
WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
UTM_K0 = 0.9996
UTM_ZONE50_CM_DEG = 117.0
UTM_FALSE_EASTING = 500000.0
UTM_FALSE_NORTHING_SOUTH = 10000000.0

# Jarak maksimum centroid hasil ekstraksi thd koordinat site_geometry. Kalau
# terlampaui, dugaan CRS-nya salah -- lebih baik gagal keras daripada
# menerbitkan peta yang menempatkan PLTS di lokasi yang keliru.
SANITY_MAX_KM = 2.0


def parse_coordinate_items(items: Sequence[TextItem]) -> List[Dict]:
    """Pasangkan item teks satu halaman -> [{no, north, east}, ...] urut no.

    Tabel koordinat dicetak sebagai beberapa blok kolom berdampingan, jadi
    pasangan ditentukan per baris (y sama): kolom NO = integer terdekat di
    kiri, kolom Y = easting terdekat di kanan.
    """
    norths = [(x, y, v) for x, y, v in items if NORTHING_RE.match(v)]
    easts = [(x, y, v) for x, y, v in items if EASTING_RE.match(v)]
    ints = [(x, y, v) for x, y, v in items if INT_RE.match(v)]

    rows: List[Dict] = []
    for xn, yn, vn in norths:
        east = [(xe, ve) for xe, ye, ve in easts
                if abs(ye - yn) < ROW_TOL and xe > xn]
        no = [(xi, vi) for xi, yi, vi in ints
              if abs(yi - yn) < ROW_TOL and 0 < xn - xi < NO_MAX_GAP]
        if not east or not no:
            continue
        rows.append({
            "no": int(max(no)[1]),
            "north": float(vn),
            "east": float(min(east)[1]),
        })
    return sorted(rows, key=lambda r: r["no"])


CALLOUT_MAX_DX = 4.0    # toleransi x agar dianggap satu callout
CALLOUT_MAX_DY = 16.0   # jarak vertikal maksimum antara label X dan Y


def parse_callout_pairs(items: Sequence[TextItem]) -> List[Dict]:
    """Callout 'X = ... / Y = ...' pada denah ISPP -> [{north, east}, ...].

    Di keluarga gambar ISPP (WB01/WB02) X = Easting dan Y = Northing --
    KEBALIKAN dari DW-004. Pasangan karena itu dipilih dari BESARAN nilai
    (98xxxxx = northing, 4xxxxx = easting), bukan dari labelnya, sehingga
    salah baca label tidak memindahkan site ribuan kilometer. Urutan cetak
    juga tidak konsisten antar halaman, jadi pasangan dicari ke atas maupun
    ke bawah.
    """
    norths = [(x, y, float(v)) for x, y, v in items if NORTHING_RE.match(v)]
    easts = [(x, y, float(v)) for x, y, v in items if EASTING_RE.match(v)]

    rows: List[Dict] = []
    for xe, ye, east in easts:
        near = [(abs(yn - ye), north) for xn, yn, north in norths
                if abs(xn - xe) < CALLOUT_MAX_DX and 0 < abs(yn - ye) <= CALLOUT_MAX_DY]
        if near:
            rows.append({"north": min(near)[1], "east": east})
    return rows


def utm50s_to_latlon(northing: float, easting: float) -> Tuple[float, float]:
    """WGS 84 UTM zone 50S -> (lat, lon) dalam derajat (deret Snyder)."""
    e2 = WGS84_F * (2.0 - WGS84_F)
    ep2 = e2 / (1.0 - e2)

    x = easting - UTM_FALSE_EASTING
    y = northing - UTM_FALSE_NORTHING_SOUTH

    m = y / UTM_K0
    mu = m / (WGS84_A * (1.0 - e2 / 4.0 - 3.0 * e2**2 / 64.0
                         - 5.0 * e2**3 / 256.0))
    e1 = (1.0 - math.sqrt(1.0 - e2)) / (1.0 + math.sqrt(1.0 - e2))
    phi1 = (mu
            + (1.5 * e1 - 27.0 / 32.0 * e1**3) * math.sin(2.0 * mu)
            + (21.0 / 16.0 * e1**2 - 55.0 / 32.0 * e1**4) * math.sin(4.0 * mu)
            + (151.0 / 96.0 * e1**3) * math.sin(6.0 * mu)
            + (1097.0 / 512.0 * e1**4) * math.sin(8.0 * mu))

    sin_phi1, cos_phi1, tan_phi1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1 = ep2 * cos_phi1**2
    t1 = tan_phi1**2
    denom = math.sqrt(1.0 - e2 * sin_phi1**2)
    n1 = WGS84_A / denom
    r1 = WGS84_A * (1.0 - e2) / denom**3
    d = x / (n1 * UTM_K0)

    lat = phi1 - (n1 * tan_phi1 / r1) * (
        d**2 / 2.0
        - (5.0 + 3.0 * t1 + 10.0 * c1 - 4.0 * c1**2 - 9.0 * ep2) * d**4 / 24.0
        + (61.0 + 90.0 * t1 + 298.0 * c1 + 45.0 * t1**2
           - 252.0 * ep2 - 3.0 * c1**2) * d**6 / 720.0
    )
    lon = math.radians(UTM_ZONE50_CM_DEG) + (
        d
        - (1.0 + 2.0 * t1 + c1) * d**3 / 6.0
        + (5.0 - 2.0 * c1 + 28.0 * t1 - 3.0 * c1**2 + 8.0 * ep2
           + 24.0 * t1**2) * d**5 / 120.0
    ) / cos_phi1
    return math.degrees(lat), math.degrees(lon)


def summarize_block(points: Sequence[Dict]) -> Dict:
    """Ringkas satu WB: jumlah titik, bbox UTM, pusat lat/lon, bentang meter."""
    norths = [p["north"] for p in points]
    easts = [p["east"] for p in points]
    lat, lon = utm50s_to_latlon(
        (min(norths) + max(norths)) / 2.0, (min(easts) + max(easts)) / 2.0,
    )
    return {
        "n_points": len(points),
        "bbox": {
            "north_min": round(min(norths), 3),
            "north_max": round(max(norths), 3),
            "east_min": round(min(easts), 3),
            "east_max": round(max(easts), 3),
        },
        "center": {"lat": round(lat, 7), "lon": round(lon, 7)},
        "span_m": {
            "ns": round(max(norths) - min(norths), 1),
            "ew": round(max(easts) - min(easts), 1),
        },
    }


# WB01/WB02 (Phase One) -- gambar keluarga ISPP, skala 1:1700, tanpa tabel
# patok. Yang ada hanya callout sudut batas di denahnya.
ISPP_PREFIX = "ISPP-PSC-DWG-1004-001"

# Batas lahan resmi. Diketik dari hasil OCR "Tabel Koordinat Geografis yang
# Disetujui" (Lampiran II, halaman 10) -- dokumennya hasil scan sehingga tidak
# bisa diparse ulang secara andal saat runtime.
PARCEL_KKPR: List[Dict] = [
    {
        "id": "KKPR 1417",
        "luas_ha": 80.6264,
        "peruntukan": "area utama PLTS",
        "sumber": "1417 - KKPR Pemutakhiran PT NSSE (80,6264 ha)"
                  "_240621_083003.pdf hal. 10",
        "points": [
            {"no": 1, "lon": 116.635467596, "lat": -0.996075992},
            {"no": 2, "lon": 116.635331790, "lat": -0.982481696},
            {"no": 3, "lon": 116.640202889, "lat": -0.982484624},
            {"no": 4, "lon": 116.640163350, "lat": -0.996089998},
        ],
    },
    {
        "id": "KKPR 1418",
        "luas_ha": 6.8425,
        "peruntukan": "strip berbentuk U di tepi luar KKPR 1417 (koridor/penyangga)",
        "sumber": "1418 - KKPR Pemutakhiran PT NSSE (6,8425 ha)"
                  "_240621_082916.pdf hal. 10",
        "points": [
            {"no": 1, "lon": 116.640199291, "lat": -0.983722536},
            {"no": 2, "lon": 116.640293765, "lat": -0.993558841},
            {"no": 3, "lon": 116.640387802, "lat": -0.993765099},
            {"no": 4, "lon": 116.640439802, "lat": -0.994343172},
            {"no": 5, "lon": 116.640405151, "lat": -0.995318375},
            {"no": 6, "lon": 116.640365953, "lat": -0.995927018},
            {"no": 7, "lon": 116.640351820, "lat": -0.996244077},
            {"no": 8, "lon": 116.640160933, "lat": -0.996591805},
            {"no": 9, "lon": 116.637004831, "lat": -0.996604690},
            {"no": 10, "lon": 116.636060423, "lat": -0.996412589},
            {"no": 11, "lon": 116.635764190, "lat": -0.996575157},
            {"no": 12, "lon": 116.635531082, "lat": -0.996704003},
            {"no": 13, "lon": 116.635217392, "lat": -0.996711084},
            {"no": 14, "lon": 116.635217457, "lat": -0.996703998},
            {"no": 15, "lon": 116.635129081, "lat": -0.996349813},
            {"no": 16, "lon": 116.635102446, "lat": -0.994873339},
            {"no": 17, "lon": 116.635233492, "lat": -0.994118109},
            {"no": 18, "lon": 116.635242737, "lat": -0.993975891},
            {"no": 19, "lon": 116.635340731, "lat": -0.983400463},
            {"no": 20, "lon": 116.635340968, "lat": -0.983400461},
            {"no": 21, "lon": 116.635467596, "lat": -0.996075992},
            {"no": 22, "lon": 116.640163350, "lat": -0.996089998},
            {"no": 23, "lon": 116.640199291, "lat": -0.983722536},
        ],
    },
]
PARCEL_NOTE = (
    "Diketik dari Lampiran II tiap KKPR (dokumen hasil scan, tidak bisa diparse "
    "ulang saat runtime). Urutan titik dipertahankan apa adanya: titik 1 = titik "
    "23 pada 1418 (poligon tertutup), titik 19 dan 20 berjarak 2,6 cm, dan titik "
    "21-22 sama persis dengan sudut 1 dan 4 KKPR 1417 -- di situlah strip U "
    "kembali menyusuri sisi selatan area utama. Luas hitung planar 80,06 dan "
    "6,79 ha, keduanya 0,70% di bawah angka resmi; bias yang sama besar pada dua "
    "parsel menunjukkan itu perbedaan metode luas, bukan koordinat yang meleset."
)

DsmHeader = Tuple[float, float, float, float]  # (origin_e, origin_n, px, py)


def dsm_pixel(header: DsmHeader, north: float, east: float) -> Tuple[int, int]:
    """(northing, easting) -> (kolom, baris) raster. Origin di sudut BARAT-LAUT,
    jadi baris BERTAMBAH ke selatan."""
    origin_e, origin_n, px, py = header
    return int(round((east - origin_e) / px)), int(round((origin_n - north) / py))


def fit_plane(samples: Sequence[Tuple[float, float, float]]) -> Optional[Dict]:
    """Fit z = a*E + b*N + c ke titik (north, east, z) -> slope/aspect/rms.

    ``aspect_deg`` = arah TURUN paling curam, konvensi GIS (0=U, 90=T,
    180=S, 270=B). ``rms_m`` adalah rem kejujurannya: dsm.tif itu SURFACE
    model, jadi petak yang masih berisi meja PV atau vegetasi akan punya
    residual besar dan kemiringannya tidak boleh dibaca sebagai kemiringan
    tanah.

    None bila titiknya < 3 atau segaris -- bidang tidak tertentukan, dan
    menebaknya lebih buruk daripada mengaku tidak tahu.
    """
    import numpy as np

    if len(samples) < 3:
        return None
    north = np.array([s[0] for s in samples], dtype=float)
    east = np.array([s[1] for s in samples], dtype=float)
    z = np.array([s[2] for s in samples], dtype=float)
    # Koordinat UTM (E ~4,6e5, N ~9,9e6) dengan variasi belasan meter membuat
    # matriks desain nyaris singular: lstsq melaporkan rank < 3 dan fungsi ini
    # menjawab "tidak tahu" padahal datanya rapi. Pemusatan menghilangkan itu;
    # kemiringan tidak berubah karena hanya translasi.
    north = north - north.mean()
    east = east - east.mean()
    design = np.column_stack([east, north, np.ones_like(z)])
    coef, _, rank, _ = np.linalg.lstsq(design, z, rcond=None)
    if rank < 3:
        return None

    a, b = float(coef[0]), float(coef[1])
    residual = design @ coef - z
    return {
        "slope_deg": round(math.degrees(math.atan(math.hypot(a, b))), 3),
        "aspect_deg": round(math.degrees(math.atan2(-a, -b)) % 360.0, 1),
        "rms_m": round(float(np.sqrt((residual ** 2).mean())), 3),
        "n_samples": len(samples),
    }


# DSM survei topografi (WGS 84 / UTM 50S, 0,1187 m/piksel). 298 MB -- di luar
# repo, dirujuk lewat path seperti dc_cable_list_path.
DSM_PATH = os.path.join(
    RAW_DIR, "Meteorological_and_Hydrological_Survey__Report PLTS IKN", "dsm.tif",
)
DSM_NODATA = -9000.0
# Jendela fit bidang. Meja PV lebar 4,95 m dengan pitch 7,12 m, jadi buffer
# 10 m per sisi memberi jendela >= 20 m -- cukup untuk merata-ratakan struktur
# seukuran meja alih-alih melaporkan kemiringannya sebagai kemiringan tanah.
PLANE_BUFFER_M = 10.0
PLANE_STEP_M = 2.0

BLOCK_GAP_M = 25.0


def segment_points(points: Sequence[Dict],
                   max_gap_m: float = BLOCK_GAP_M) -> List[Dict]:
    """Pecah rangkaian patok bernomor jadi petak array.

    Patok berurutan menyusuri tepi satu petak dengan jarak ~row pitch
    (median 6,8 m; desain 7,12 m), lalu melompat saat pindah petak.
    Distribusi 728 jarak nyata: 494 di 5-10 m, 146 di 15-20 m, hanya 1 di
    20-25 m, KOSONG di 25-30 m, lalu ekor >= 30 m. Ambang diletakkan di
    pita kosong itu sehingga 25 m dan 30 m memberi segmentasi identik --
    hasilnya tidak bergantung pada pemilihan angka.
    """
    if not points:
        return []

    runs: List[List[Dict]] = [[points[0]]]
    for prev, cur in zip(points, points[1:]):
        gap = math.hypot(cur["north"] - prev["north"], cur["east"] - prev["east"])
        if gap > max_gap_m:
            runs.append([])
        runs[-1].append(cur)

    return [
        {"no_start": run[0]["no"], "no_end": run[-1]["no"], **summarize_block(run)}
        for run in runs
    ]


def open_dsm(path: str):
    """Buka DSM GeoTIFF -> (image, header). None bila berkasnya tidak ada.

    Non-fatal: site_layout tetap bisa dibangun tanpa elevasi, seperti
    sebelum DSM ditemukan.
    """
    if not os.path.exists(path):
        return None, None
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    image = Image.open(path)
    tags = image.tag_v2
    scale, tie = tags.get(33550), tags.get(33922)
    if not scale or not tie:
        raise ValueError(f"{path}: tanpa tag GeoTIFF (ModelPixelScale/Tiepoint).")
    return image, (tie[3], tie[4], scale[0], scale[1])


def sample_dsm(image, header: DsmHeader, north: float, east: float) -> Optional[float]:
    """Elevasi (m) pada satu titik; None bila di luar raster atau nodata."""
    col, row = dsm_pixel(header, north, east)
    if not (0 <= col < image.size[0] and 0 <= row < image.size[1]):
        return None
    value = image.crop((col, row, col + 1, row + 1)).getpixel((0, 0))
    if value is None or value <= DSM_NODATA:
        return None
    return round(float(value), 2)


def segment_plane(image, header: DsmHeader, bbox: Dict) -> Optional[Dict]:
    """Fit bidang tanah pada footprint satu petak yang diberi buffer."""
    import numpy as np

    north_lo = bbox["north_min"] - PLANE_BUFFER_M
    north_hi = bbox["north_max"] + PLANE_BUFFER_M
    east_lo = bbox["east_min"] - PLANE_BUFFER_M
    east_hi = bbox["east_max"] + PLANE_BUFFER_M
    col_lo, row_hi = dsm_pixel(header, north_lo, east_lo)
    col_hi, row_lo = dsm_pixel(header, north_hi, east_hi)
    col_lo, row_lo = max(col_lo, 0), max(row_lo, 0)
    col_hi = min(col_hi, image.size[0] - 1)
    row_hi = min(row_hi, image.size[1] - 1)
    if col_hi <= col_lo or row_hi <= row_lo:
        return None

    window = np.asarray(
        image.crop((col_lo, row_lo, col_hi + 1, row_hi + 1)), dtype=float,
    )
    stride = max(1, int(round(PLANE_STEP_M / header[2])))
    window = window[::stride, ::stride]
    rows = row_lo + np.arange(window.shape[0]) * stride
    cols = col_lo + np.arange(window.shape[1]) * stride
    norths = header[1] - rows * header[3]
    easts = header[0] + cols * header[2]

    samples = [
        (float(norths[r]), float(easts[c]), float(window[r, c]))
        for r in range(window.shape[0])
        for c in range(window.shape[1])
        if window[r, c] > DSM_NODATA
    ]
    return fit_plane(samples)


def extract_blocks(pdf_path: str) -> Dict[str, List[Dict]]:
    """Baca DW-004 -> {"WB03": [{no, north, east}, ...], ...}.

    Nama WB diambil dari judul di halaman itu sendiri, bukan dari urutan
    halaman -- kalau suatu revisi menyisipkan/menghapus lembar, pemetaan
    tetap benar.
    """
    from pypdf import PdfReader

    blocks: Dict[str, List[Dict]] = {}
    for page in PdfReader(pdf_path).pages:
        items: List[TextItem] = []

        def visitor(text, cm, tm, font, size, _items=items):
            stripped = text.strip()
            if stripped:
                _items.append((round(tm[4], 1), round(tm[5], 1), stripped))

        full_text = page.extract_text(visitor_text=visitor) or ""
        rows = parse_coordinate_items(items)
        if not rows:
            continue
        title = WB_TITLE_RE.search(full_text)
        if not title:
            raise ValueError(
                f"Halaman dengan {len(rows)} titik koordinat tanpa judul WB; "
                f"pemetaan halaman->WB tidak bisa ditebak dari urutan."
            )
        blocks[f"WB{int(title.group(1)):02d}"] = rows
    return blocks


def extract_phase_one(pdf_path: str) -> List[Dict]:
    """Callout sudut batas Phase One (WB01+WB02) dari denah ISPP.

    WB01 dan WB02 TIDAK terpisahkan dari gambar ini -- callout-nya menandai
    batas tapak Phase One sebagai satu kesatuan, bukan patok per WB.
    """
    import pymupdf

    pymupdf.TOOLS.mupdf_display_errors(False)
    rows: List[Dict] = []
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            items = [((w[0] + w[2]) / 2, (w[1] + w[3]) / 2, w[4])
                     for w in page.get_text("words")]
            rows.extend(parse_callout_pairs(items))
    return rows


def _assert_matches_site_geometry(lat: float, lon: float) -> float:
    """Gagal keras kalau centroid hasil ekstraksi jauh dari koordinat site."""
    import yaml

    with open(SITE_GEOMETRY_PATH, encoding="utf-8") as handle:
        site = yaml.safe_load(handle)["site"]
    dlat = math.radians(lat - site["latitude"])
    dlon = math.radians(lon - site["longitude"]) * math.cos(math.radians(lat))
    km = 6371.0 * math.hypot(dlat, dlon)
    if km > SANITY_MAX_KM:
        raise AssertionError(
            f"Centroid hasil ekstraksi ({lat:.6f}, {lon:.6f}) berjarak "
            f"{km:.1f} km dari site_geometry -- dugaan CRS UTM 50S "
            f"kemungkinan salah."
        )
    return km


def main() -> None:
    import yaml

    pdf_name = next(n for n in os.listdir(RAW_DIR) if n.startswith(PDF_PREFIX))
    blocks = extract_blocks(os.path.join(RAW_DIR, pdf_name))
    dsm, dsm_header = open_dsm(DSM_PATH)
    if dsm is None:
        print(f"[site-layout] DSM tidak ada ({DSM_PATH}) -- elevasi dilewati.")

    doc_blocks: Dict[str, Dict] = {}
    all_points: List[Dict] = []
    for wb in sorted(blocks):
        points = blocks[wb]
        if [p["no"] for p in points] != list(range(1, len(points) + 1)):
            raise AssertionError(
                f"{wb}: nomor titik tidak runtut 1..{len(points)} -- "
                f"ekstraksi kolom NO meleset."
            )
        entry = summarize_block(points)
        segments = segment_points(points)
        if dsm is not None:
            for seg in segments:
                seg["terrain"] = segment_plane(dsm, dsm_header, seg["bbox"])
        entry["n_segments"] = len(segments)
        entry["segments"] = segments
        entry["points"] = []
        for p in points:
            lat, lon = utm50s_to_latlon(p["north"], p["east"])
            entry["points"].append({
                "no": p["no"],
                "north": round(p["north"], 3),
                "east": round(p["east"], 3),
                "lat": round(lat, 7),
                "lon": round(lon, 7),
                "elev_m": (None if dsm is None
                           else sample_dsm(dsm, dsm_header, p["north"], p["east"])),
            })
        doc_blocks[wb] = entry
        all_points.extend(points)

    ispp_name = next((n for n in os.listdir(RAW_DIR) if n.startswith(ISPP_PREFIX)), None)
    if ispp_name:
        phase_one = extract_phase_one(os.path.join(RAW_DIR, ispp_name))
        if phase_one:
            entry = summarize_block(phase_one)
            entry["n_segments"] = 0
            entry["segments"] = []
            entry["catatan"] = (
                "WB01 + WB02 (Phase One). Titik = callout SUDUT BATAS tapak, "
                "bukan patok setting-out, jadi tidak disegmentasi jadi petak. "
                "WB01 dan WB02 tidak terpisahkan dari gambar ini."
            )
            entry["points"] = []
            for i, p in enumerate(sorted(phase_one, key=lambda r: -r["north"]), 1):
                lat, lon = utm50s_to_latlon(p["north"], p["east"])
                entry["points"].append({
                    "no": i,
                    "north": round(p["north"], 3),
                    "east": round(p["east"], 3),
                    "lat": round(lat, 7),
                    "lon": round(lon, 7),
                    "elev_m": (None if dsm is None
                               else sample_dsm(dsm, dsm_header, p["north"], p["east"])),
                })
            doc_blocks["PHASE_ONE"] = entry
            all_points.extend(phase_one)

    site = summarize_block(all_points)
    km = _assert_matches_site_geometry(site["center"]["lat"], site["center"]["lon"])

    doc = {
        "meta": {
            "source": f"{RAW_DIR}/{pdf_name}",
            "generator": "build_site_layout.py",
            "crs": "EPSG:32750 (WGS 84 / UTM zone 50S)",
            "kolom_x_adalah": "northing (DW-004); pada gambar ISPP X = easting",
            "kolom_y_adalah": "easting (DW-004); pada gambar ISPP Y = northing",
            "total_points": len(all_points),
            "cakupan": "WB03-WB10 dari tabel patok DW-004 + PHASE_ONE (WB01+WB02) "
                       "dari callout sudut ISPP-PSC-DWG-1004-001. WB01 dan WB02 "
                       "tidak terpisahkan satu sama lain dari gambar yang ada.",
            "granularitas": "patok setting-out blok array, BUKAN per PV string "
                            "(WB03: 101 titik vs 366 string).",
            "segments": f"petak array hasil pemisahan rangkaian patok pada "
                        f"lompatan > {BLOCK_GAP_M:.0f} m. Ambang berada di pita "
                        f"kosong histogram jarak (25-30 m), jadi 25 m dan 30 m "
                        f"memberi hasil identik.",
            "elevasi": (
                f"points[].elev_m disampel dari {DSM_PATH} (WGS 84 / UTM 50S, "
                f"0,1187 m/piksel). segments[].terrain = fit bidang least-squares "
                f"pada footprint petak + buffer {PLANE_BUFFER_M:.0f} m, langkah "
                f"{PLANE_STEP_M:.0f} m. aspect_deg = arah TURUN (0=U, 90=T)."
                if os.path.exists(DSM_PATH) else "tidak tersedia (dsm.tif tak ada)."
            ),
            "peringatan_dsm": "dsm.tif adalah SURFACE model -- memuat vegetasi "
                              "dan berpotensi meja PV. Baca slope/aspect hanya "
                              "bila rms_m kecil; rms besar = permukaan berstruktur.",
            "parcel": PARCEL_KKPR,
            "parcel_catatan": PARCEL_NOTE,
        },
        "site": site,
        "blocks": doc_blocks,
    }

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as handle:
        yaml.safe_dump(doc, handle, sort_keys=False, allow_unicode=True,
                       default_flow_style=False, width=100)

    print(f"[site-layout] {pdf_name}")
    for wb, entry in doc_blocks.items():
        print(f"  {wb}: {entry['n_points']:3d} titik  {entry['n_segments']:2d} petak  "
              f"pusat ({entry['center']['lat']:.6f}, {entry['center']['lon']:.6f})  "
              f"bentang {entry['span_m']['ns']:.0f} x {entry['span_m']['ew']:.0f} m")
    print(f"  SITE: {site['n_points']} titik, pusat "
          f"({site['center']['lat']:.6f}, {site['center']['lon']:.6f}), "
          f"{km:.2f} km dari site_geometry")
    print(f"[site-layout] ditulis: {OUT_PATH} "
          f"({os.path.getsize(OUT_PATH) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
