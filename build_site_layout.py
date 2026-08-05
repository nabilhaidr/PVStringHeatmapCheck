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
from typing import Dict, List, Sequence, Tuple

TextItem = Tuple[float, float, str]  # (x_pdf, y_pdf, teks)

PDF_PREFIX = "IKN-CE-PP-DW-004"
RAW_DIR = "raw data input"
OUT_PATH = os.path.join("config", "site_layout.yaml")
SITE_GEOMETRY_PATH = os.path.join("config", "site_geometry.yaml")

NORTHING_RE = re.compile(r"^98\d{5}\.\d{1,3}$")
EASTING_RE = re.compile(r"^4\d{5}\.\d{1,3}$")
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
            })
        doc_blocks[wb] = entry
        all_points.extend(points)

    site = summarize_block(all_points)
    km = _assert_matches_site_geometry(site["center"]["lat"], site["center"]["lon"])

    doc = {
        "meta": {
            "source": f"{RAW_DIR}/{pdf_name}",
            "generator": "build_site_layout.py",
            "crs": "EPSG:32750 (WGS 84 / UTM zone 50S)",
            "kolom_x_adalah": "northing",
            "kolom_y_adalah": "easting",
            "total_points": len(all_points),
            "cakupan": "WB03-WB10 saja; WB01-WB02 ada di 104-002 Site Grading "
                       "yang lapisan teksnya kosong (perlu OCR atau file DWG).",
            "granularitas": "patok setting-out blok array, BUKAN per PV string "
                            "(WB03: 101 titik vs 366 string).",
            "segments": f"petak array hasil pemisahan rangkaian patok pada "
                        f"lompatan > {BLOCK_GAP_M:.0f} m. Ambang berada di pita "
                        f"kosong histogram jarak (25-30 m), jadi 25 m dan 30 m "
                        f"memberi hasil identik.",
            "elevasi": "tidak tersedia -- spot level & garis kontur di gambar "
                       "sudah di-outline jadi geometri, tidak ada di lapisan teks.",
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
