"""Tests build_site_layout.py (tabel koordinat DW-004 -> site_layout.yaml).

Kenapa penting:
- Hasilnya dipakai menaruh pin di peta. Salah pasang kolom X/Y menukar
  northing dengan easting dan memindahkan seluruh PLTS ribuan kilometer;
  salah baca kolom NO memutus rujukan balik ke callout di gambar.
- Konversi UTM ditulis tangan (pyproj tidak terpasang), jadi rumusnya
  harus diikat ke titik yang benar secara definisi, bukan ke angka yang
  kebetulan keluar saat pertama kali dijalankan.
"""
from __future__ import annotations

import math

import pytest

from build_site_layout import (
    BLOCK_GAP_M,
    dsm_pixel,
    fit_plane,
    parse_callout_pairs,
    parse_coordinate_items,
    segment_points,
    summarize_block,
    utm50s_to_latlon,
)


# --- parsing tabel koordinat --------------------------------------------------


def test_parse_pairs_number_northing_and_easting_within_one_row():
    """Satu baris tabel = NO di kiri, X (northing), lalu Y (easting)."""
    items = [
        (7158.0, 12155.0, "1"),
        (7380.0, 12155.0, "9890374.827"),
        (7904.0, 12155.0, "459522.135"),
    ]

    rows = parse_coordinate_items(items)

    assert rows == [{"no": 1, "north": 9890374.827, "east": 459522.135}]


def test_parse_keeps_columns_apart_in_multi_block_table():
    """Tabel dicetak sebagai beberapa blok kolom berdampingan pada y sama.

    Kalau pemasangan tidak memakai "easting terdekat di KANAN", titik blok
    kiri akan mencomot easting milik blok kanan -- pin melompat ratusan
    meter tanpa satu pun nilai terlihat janggal.
    """
    items = [
        (7158.0, 12155.0, "1"),
        (7380.0, 12155.0, "9890374.827"),
        (7904.0, 12155.0, "459522.135"),
        (9158.0, 12155.0, "42"),
        (9380.0, 12155.0, "9890200.592"),
        (9904.0, 12155.0, "459548.428"),
    ]

    rows = parse_coordinate_items(items)

    assert rows[0] == {"no": 1, "north": 9890374.827, "east": 459522.135}
    assert rows[1] == {"no": 42, "north": 9890200.592, "east": 459548.428}


def test_parse_ignores_text_from_other_rows():
    """Item pada y berbeda bukan bagian dari baris ini."""
    items = [
        (7158.0, 12155.0, "1"),
        (7380.0, 12155.0, "9890374.827"),
        (7904.0, 12066.0, "459999.999"),   # baris lain -> tidak boleh dipakai
    ]

    assert parse_coordinate_items(items) == []


def test_parse_returns_rows_sorted_by_drawing_number():
    """Urutan hasil mengikuti nomor patok di gambar, bukan urutan kemunculan
    teks di dalam content stream PDF."""
    items = [
        (9158.0, 12000.0, "9"),
        (9380.0, 12000.0, "9890317.127"),
        (9904.0, 12000.0, "459520.069"),
        (7158.0, 12155.0, "2"),
        (7380.0, 12155.0, "9890367.727"),
        (7904.0, 12155.0, "459522.135"),
    ]

    assert [r["no"] for r in parse_coordinate_items(items)] == [2, 9]


# --- konversi UTM 50S ---------------------------------------------------------


def test_utm_origin_maps_to_equator_on_central_meridian():
    """Titik acuan zona: false easting/northing tepat di 0 LU, 117 BT.

    Ini definisi UTM zone 50 belahan selatan, bukan angka hasil kalibrasi
    -- jadi kalau deret Snyder salah ketik, tes ini yang jatuh duluan.
    """
    lat, lon = utm50s_to_latlon(10_000_000.0, 500_000.0)

    assert lat == pytest.approx(0.0, abs=1e-9)
    assert lon == pytest.approx(117.0, abs=1e-9)


def test_utm_southern_offset_matches_meridian_arc_length():
    """110 km di selatan ekuator ~ 0,995 derajat lintang.

    Panjang busur meridian per derajat di dekat ekuator = 110,574 km;
    faktor skala k0 = 0,9996 membuatnya 110/0,9996/110,574 = 0,9951.
    Pemeriksaan fisik yang berdiri sendiri dari implementasinya.
    """
    lat, lon = utm50s_to_latlon(9_890_000.0, 500_000.0)

    assert lat == pytest.approx(-0.9951, abs=0.001)
    assert lon == pytest.approx(117.0, abs=1e-9)  # tetap di meridian tengah


def test_utm_easting_west_of_central_meridian_gives_smaller_longitude():
    """Site IKN ada di barat CM 117 BT -> bujurnya harus < 117."""
    _, lon = utm50s_to_latlon(9_890_000.0, 459_500.0)

    assert lon < 117.0
    assert lon == pytest.approx(116.636, abs=0.005)


# --- segmentasi petak ---------------------------------------------------------


def _chain(no_start, north0, east0, count, step=6.8):
    """Rantai patok berjarak satu row pitch, memanjang ke utara."""
    return [{"no": no_start + i, "north": north0 + i * step, "east": east0}
            for i in range(count)]


def test_segment_keeps_points_within_one_row_pitch_together():
    """Patok yang berurutan pada satu tepi petak jaraknya ~row pitch 7 m."""
    points = _chain(1, 9890000.0, 459000.0, 5)

    segments = segment_points(points)

    assert len(segments) == 1
    assert segments[0]["no_start"] == 1
    assert segments[0]["no_end"] == 5
    assert segments[0]["n_points"] == 5


def test_segment_splits_where_chain_jumps_to_another_petak():
    """Lompatan besar = pindah petak, bukan patok berikutnya di tepi sama.

    Tanpa pemisahan ini, satu "petak" akan membentang menyeberangi jalan
    atau parit dan pin-nya jatuh di lahan kosong.
    """
    points = _chain(1, 9890000.0, 459000.0, 3) + _chain(4, 9890300.0, 459150.0, 2)

    segments = segment_points(points)

    assert [(s["no_start"], s["no_end"]) for s in segments] == [(1, 3), (4, 5)]


def test_segment_threshold_sits_in_the_empty_band_of_the_gap_histogram():
    """Ambang tidak boleh memotong jarak antar-baris yang sah.

    Distribusi jarak nyata: 494 lompatan 5-10 m, 146 lompatan 15-20 m,
    lalu KOSONG di 25-30 m sebelum ekor >= 30 m. Ambang harus berada di
    pita kosong itu supaya hasilnya sama untuk 25 m maupun 30 m.
    """
    points = _chain(1, 9890000.0, 459000.0, 2, step=19.0)   # masih satu petak
    points += [{"no": 3, "north": 9890000.0 + 19.0 + 31.0, "east": 459000.0}]

    assert BLOCK_GAP_M == pytest.approx(25.0)
    assert [(s["no_start"], s["no_end"])
            for s in segment_points(points)] == [(1, 2), (3, 3)]


def test_segment_reports_its_own_center_and_extent():
    """Tiap segmen adalah satu pin di peta -> perlu pusat & bentangnya sendiri."""
    # 6 patok berjarak 20 m -> membentang 100 m, semuanya masih satu petak.
    points = _chain(1, 9890000.0, 459000.0, 6, step=20.0)

    segments = segment_points(points)

    assert len(segments) == 1
    segment = segments[0]

    assert segment["span_m"] == {"ns": 100.0, "ew": 0.0}
    lat, lon = utm50s_to_latlon(9890050.0, 459000.0)
    assert segment["center"]["lat"] == pytest.approx(lat, abs=1e-7)
    assert segment["center"]["lon"] == pytest.approx(lon, abs=1e-7)


# --- callout koordinat gambar ISPP (WB01/WB02) --------------------------------


def test_callout_pairs_reads_easting_above_northing():
    """Tata letak halaman 1 ISPP-PSC-DWG-1004-001: 'X =' di atas 'Y ='.

    Di keluarga gambar ini X = Easting dan Y = Northing -- KEBALIKAN dari
    DW-004. Pasangan dipilih berdasarkan BESARAN (98xxxxx vs 4xxxxx), bukan
    label, supaya salah baca label tidak memindahkan site ribuan kilometer.
    """
    items = [(160.0, 139.0, "459453.06"), (160.0, 147.0, "9890258.40")]

    assert parse_callout_pairs(items) == [
        {"north": 9890258.40, "east": 459453.06},
    ]


def test_callout_pairs_reads_northing_above_easting():
    """Halaman 19 mencetaknya terbalik. Satu titik hilang kalau hanya satu
    arah yang ditangani."""
    items = [(680.0, 408.0, "9890197.9535"), (680.0, 414.0, "459684.9590")]

    assert parse_callout_pairs(items) == [
        {"north": 9890197.9535, "east": 459684.9590},
    ]


def test_callout_pairs_does_not_join_values_from_different_columns():
    """Callout berjauhan milik sudut yang berbeda."""
    items = [(160.0, 139.0, "459453.06"), (900.0, 147.0, "9890258.40")]

    assert parse_callout_pairs(items) == []


# --- indeks piksel DSM --------------------------------------------------------

# Header nyata dsm.tif: origin di sudut BARAT-LAUT, baris bertambah ke selatan.
DSM_HDR = (459211.512, 9891574.022, 0.1187, 0.1187)


def test_dsm_pixel_maps_raster_origin_to_first_pixel():
    """Titik tiepoint = sudut piksel (0, 0). Salah setengah piksel pun
    tidak apa; salah TANDA pada baris membalik utara-selatan dan menaruh
    elevasi puncak bukit di lembah."""
    assert dsm_pixel(DSM_HDR, 9891574.022, 459211.512) == (0, 0)


def test_dsm_pixel_row_increases_towards_south():
    """Northing turun -> baris naik. Ini arah yang gampang terbalik."""
    col, row = dsm_pixel(DSM_HDR, 9891574.022 - 10 * 0.1187, 459211.512)

    assert (col, row) == (0, 10)


def test_dsm_pixel_column_increases_towards_east():
    col, row = dsm_pixel(DSM_HDR, 9891574.022, 459211.512 + 10 * 0.1187)

    assert (col, row) == (10, 0)


# --- fit bidang: slope & aspect -----------------------------------------------


def _plane(dz_de, dz_dn, n=5, step=10.0, z0=80.0):
    """Grid sintetis di atas bidang z = z0 + dz_de*dE + dz_dn*dN."""
    return [(9890000.0 + j * step, 459000.0 + i * step,
             z0 + dz_de * (i * step) + dz_dn * (j * step))
            for i in range(n) for j in range(n)]


def test_fit_plane_reports_zero_slope_on_flat_ground():
    out = fit_plane(_plane(0.0, 0.0))

    assert out["slope_deg"] == pytest.approx(0.0, abs=1e-9)
    assert out["rms_m"] == pytest.approx(0.0, abs=1e-9)


def test_fit_plane_aspect_zero_when_ground_falls_towards_north():
    """Aspect = arah TURUN. Tanah yang menurun ke utara menghadap utara (0)."""
    out = fit_plane(_plane(0.0, -math.tan(math.radians(10.0))))

    assert out["slope_deg"] == pytest.approx(10.0, abs=1e-6)
    assert out["aspect_deg"] == pytest.approx(0.0, abs=1e-6)


def test_fit_plane_aspect_ninety_when_ground_falls_towards_east():
    """Konvensi GIS: 0=U, 90=T, 180=S, 270=B."""
    out = fit_plane(_plane(-math.tan(math.radians(5.0)), 0.0))

    assert out["slope_deg"] == pytest.approx(5.0, abs=1e-6)
    assert out["aspect_deg"] == pytest.approx(90.0, abs=1e-6)


def test_fit_plane_aspect_two_seventy_when_ground_falls_towards_west():
    out = fit_plane(_plane(+math.tan(math.radians(5.0)), 0.0))

    assert out["aspect_deg"] == pytest.approx(270.0, abs=1e-6)


def test_fit_plane_rms_exposes_a_surface_that_is_not_a_plane():
    """dsm.tif adalah SURFACE model -- memuat vegetasi dan berpotensi meja PV.

    RMS residual adalah satu-satunya rem terhadap pelaporan kemiringan meja
    sebagai kemiringan tanah. Tanpa angka ini, petak berisi struktur akan
    tampak sama meyakinkannya dengan petak yang bersih.
    """
    # Papan catur pada grid GENAP: jumlahnya nol dan tidak berkorelasi dengan
    # E maupun N, jadi bidang terbaiknya tetap datar dan seluruh simpangan
    # +/-2 m jatuh ke residual.
    bumpy = []
    for north, east, z in _plane(0.0, 0.0, n=4):
        i = round((east - 459000.0) / 10.0)
        j = round((north - 9890000.0) / 10.0)
        bumpy.append((north, east, z + (2.0 if (i + j) % 2 == 0 else -2.0)))

    out = fit_plane(bumpy)

    assert out["slope_deg"] == pytest.approx(0.0, abs=1e-9)
    assert out["rms_m"] == pytest.approx(2.0, abs=1e-6)


def test_fit_plane_needs_three_points_to_define_a_plane():
    """Banyak petak berupa rantai patok segaris; bidang tak bisa ditentukan
    dari situ, dan menebaknya lebih buruk daripada mengaku tidak tahu."""
    assert fit_plane([(9890000.0, 459000.0, 70.0),
                      (9890010.0, 459000.0, 71.0)]) is None


# --- ringkasan blok -----------------------------------------------------------


def test_summarize_block_reports_bbox_span_and_center():
    """Ringkasan per WB = bahan peta ikhtisar: kotak batas, bentang, pusat."""
    points = [
        {"no": 1, "north": 9890000.0, "east": 459000.0},
        {"no": 2, "north": 9890400.0, "east": 459200.0},
    ]

    out = summarize_block(points)

    assert out["n_points"] == 2
    assert out["bbox"] == {
        "north_min": 9890000.0, "north_max": 9890400.0,
        "east_min": 459000.0, "east_max": 459200.0,
    }
    assert out["span_m"] == {"ns": 400.0, "ew": 200.0}
    # pusat = titik tengah bbox, diproyeksikan balik ke lat/lon
    mid_lat, mid_lon = utm50s_to_latlon(9890200.0, 459100.0)
    assert out["center"]["lat"] == pytest.approx(mid_lat, abs=1e-7)
    assert out["center"]["lon"] == pytest.approx(mid_lon, abs=1e-7)
