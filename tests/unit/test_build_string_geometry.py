"""Tests build_string_geometry.py (label string DXF + kemiringan tanah).

Kenapa penting:
- Ini satu-satunya sumber koordinat PER STRING yang kita punya. Salah tukar
  X/Y memindahkan seluruh PLTS; salah tanda pada cross-slope menukar meja
  yang condong ke timur dengan yang condong ke barat, dan justru tanda itu
  yang membedakan defisit pagi dari defisit sore.
- Foto lapangan 2026-08-06 memastikan meja di WB03-WB10 MENGIKUTI kontur,
  jadi kemiringan tanah di posisi string adalah orientasi bidang modulnya.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from build_string_geometry import (
    OUT_PATH,
    STRINGS_YAML,
    cross_slope_deg,
    disprove_empty_channel,
    empty_pv_channels,
    fix_asbuilt_pv,
    parse_dxf_string_labels,
    parse_dxf_tables,
    parse_phase_one_labels,
    phase_one_mppt_map,
    resolve_dxf_relabels,
)


def _dxf(tmp_path, entities):
    """DXF minimal: rangkaian pasangan (kode, nilai)."""
    lines = []
    for ent in entities:
        for code, value in ent:
            lines.append(str(code))
            lines.append(str(value))
    lines += ["0", "EOF"]
    path = tmp_path / "sample.dxf"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


# --- parsing DXF --------------------------------------------------------------


def test_parse_reads_label_with_its_utm_coordinates(tmp_path):
    """Kode 10 = easting, kode 20 = northing pada DXF ini."""
    path = _dxf(tmp_path, [[
        ("0", "TEXT"), ("8", "String number"), ("1", "WB05INV01ST01"),
        ("10", "459435.59"), ("20", "9890796.73"),
    ]])

    rows = parse_dxf_string_labels(path)

    assert rows == [{"label": "WB05INV01ST01", "wb": 5, "inv": 1, "st": 1,
                     "east": 459435.59, "north": 9890796.73}]


def test_parse_ignores_text_that_is_not_a_string_label(tmp_path):
    """Gambar juga memuat teks lain (judul, catatan, nomor inverter)."""
    path = _dxf(tmp_path, [
        [("0", "TEXT"), ("1", "WB05INV01"), ("10", "1.0"), ("20", "2.0")],
        [("0", "TEXT"), ("1", "NOTES:"), ("10", "3.0"), ("20", "4.0")],
        [("0", "TEXT"), ("1", "WB05INV01ST07"), ("10", "459000.0"),
         ("20", "9890000.0")],
    ])

    assert [r["label"] for r in parse_dxf_string_labels(path)] == ["WB05INV01ST07"]


def test_parse_skips_label_without_coordinates(tmp_path):
    """Label tanpa titik sisip tidak bisa dipakai -- jangan diam-diam diberi 0."""
    path = _dxf(tmp_path, [[("0", "TEXT"), ("1", "WB05INV01ST01")]])

    assert parse_dxf_string_labels(path) == []


# --- koreksi penomoran inverter di 1129.dxf -----------------------------------


def _lbl(wb, inv, st, east, north=9890600.0):
    """Satu label hasil parse, pada easting (dan northing) tertentu."""
    return {"label": f"WB{wb:02d}INV{inv:02d}ST{st:02d}", "wb": wb, "inv": inv,
            "st": st, "east": east, "north": north}


def _peta(rows):
    """(easting, northing) -> (wb, inv, st) untuk memeriksa hasil pemindahan."""
    return {(round(r["east"], 1), round(r["north"], 1)): (r["wb"], r["inv"], r["st"])
            for r in rows}


def test_stray_copy_moves_to_the_inverter_whose_grid_it_continues():
    """Salinan nyasar dikenali dari grid TUJUAN, bukan jarak ke induknya.

    Array disusun grid: baris berjarak ~7 m, kolom ~15,4 m, dinomori
    barat-ke-timur lalu turun sebaris. Salinan yang benar melanjutkan grid
    inverternya sendiri; yang nyasar melanjutkan grid inverter lain -- dan
    inverter itu memang kekurangan ST yang sama persis menurut as-built.

    Memakai jarak ke pusat inverter INDUK memilih yang terbalik: di
    WB03-INV11 salinan yang benar berjarak 31 m dari pusatnya sendiri
    sedangkan yang nyasar hanya 21 m.
    """
    keluar = _peta(resolve_dxf_relabels([
        _lbl(3, 8, 12, 459618.6, 9890394.8),     # tujuan: ujung baris utara
        _lbl(3, 11, 12, 459649.5, 9890374.1),    # induk: baris selatan
        _lbl(3, 11, 13, 459664.9, 9890374.1),    # benar -> tetap WB03-INV11
        _lbl(3, 11, 13, 459634.0, 9890394.8),    # nyasar -> WB03-INV08
    ]))

    assert keluar[(459664.9, 9890374.1)] == (3, 11, 13)
    assert keluar[(459634.0, 9890394.8)] == (3, 8, 13)


def test_stray_chain_resolves_in_sequence():
    """ST14 baru bisa diputuskan setelah ST13 pindah -- keduanya ganda.

    Tetangga rujukan di inverter tujuan harus tunggal. Selama ST13 masih
    ganda, ST14 tidak punya jangkar dan koreksinya berhenti separuh jalan.
    """
    keluar = _peta(resolve_dxf_relabels([
        _lbl(3, 8, 12, 459618.6, 9890394.8),
        _lbl(3, 11, 13, 459664.9, 9890374.1),
        _lbl(3, 11, 13, 459634.0, 9890394.8),
        _lbl(3, 11, 14, 459603.2, 9890367.0),    # benar: awal baris berikutnya
        _lbl(3, 11, 14, 459649.5, 9890394.8),    # nyasar: lanjut baris INV08
    ]))

    assert keluar[(459634.0, 9890394.8)] == (3, 8, 13)
    assert keluar[(459649.5, 9890394.8)] == (3, 8, 14)
    assert keluar[(459603.2, 9890367.0)] == (3, 11, 14)


def test_single_copy_is_never_moved():
    """Tanpa salinan kedua tidak ada dasar memindahkan -- jangan menebak."""
    keluar = resolve_dxf_relabels([
        _lbl(3, 8, 12, 459618.6, 9890394.8),
        _lbl(3, 11, 13, 459634.0, 9890394.8),
    ])

    assert [(r["wb"], r["inv"], r["st"]) for r in keluar] == [(3, 8, 12), (3, 11, 13)]


def test_wb04_numbering_shifts_by_one_from_inverter_17():
    """1129.dxf melewatkan WB04-INV17 lalu menggeser sisanya naik satu.

    Ketahuan lewat jumlah string per inverter yang dicocokkan ke as-built DC
    cable list -- bukti yang sama sekali tidak bergantung koordinat: label
    INV18 membawa 27 string sedangkan as-built INV18 punya 24 dan INV17 punya
    27. Ketiga pergeserannya cocok berurutan (27, 24, 23).

    Tanpa koreksi ini WB04-INV17 tidak punya koordinat sama sekali -- dan ia
    memiliki dua dari 20 string pada laporan yang sudah beredar.
    """
    keluar = resolve_dxf_relabels([
        _lbl(4, 16, 1, 459793.0),
        _lbl(4, 18, 1, 459887.0),
        _lbl(4, 19, 1, 459941.0),
        _lbl(4, 20, 1, 459930.0),
    ])

    assert [(r["wb"], r["inv"]) for r in keluar] == [(4, 16), (4, 17), (4, 18), (4, 19)]


def test_wb05_label_used_only_for_wb06_moves_wholesale():
    """WB05 berhenti di INV19; label INV20 di DXF sepenuhnya milik WB06.

    Baik as-built maupun General Layout menyatakan WB05 hanya punya 19
    inverter, jadi tidak ada gugus WB05 yang bisa mengklaim label ini.
    """
    keluar = resolve_dxf_relabels([_lbl(5, 20, 1, 459907.0),
                                   _lbl(5, 20, 2, 459909.0)])

    assert {(r["wb"], r["inv"]) for r in keluar} == {(6, 20)}


def test_wb05_label_reused_by_wb06_splits_on_the_spatial_gap():
    """Label INV15-INV19 dipakai DUA KALI: sekali di WB05, sekali di WB06.

    Gugus timur adalah array WB06. Pemisahnya celah easting 71-334 m --
    jauh di atas lebar satu inverter (~50 m), jadi tidak ambigu. Aturannya
    memverifikasi diri sendiri: jumlah kedua sisi harus sama persis dengan
    hitungan as-built WB05 dan WB06, dan untuk keenam label memang begitu.

    Memindahkan seluruh label ke WB06 akan MENGHAPUS inverter WB05 yang sah;
    membiarkannya utuh membuat satu label mengaku dua array berjarak ratusan
    meter -- persis keadaan yang dulu memaksa cross-slope-nya dikosongkan.
    """
    keluar = resolve_dxf_relabels([
        _lbl(5, 17, 1, 459480.0), _lbl(5, 17, 2, 459500.0),   # gugus barat: WB05
        _lbl(5, 17, 3, 459810.0), _lbl(5, 17, 4, 459820.0),   # gugus timur: WB06
    ])
    oleh_st = {r["st"]: (r["wb"], r["inv"]) for r in keluar}

    assert oleh_st[1] == (5, 17) and oleh_st[2] == (5, 17)
    assert oleh_st[3] == (6, 17) and oleh_st[4] == (6, 17)


def test_labels_outside_the_two_broken_blocks_are_untouched():
    """Koreksi ini bedah, bukan sapu rata: hanya WB04 dan WB05 yang cacat."""
    masuk = [_lbl(3, 11, 1, 459500.0), _lbl(6, 14, 1, 459800.0),
             _lbl(10, 3, 1, 459700.0), _lbl(4, 16, 1, 459793.0)]

    keluar = resolve_dxf_relabels(masuk)

    assert [(r["wb"], r["inv"]) for r in keluar] == [(3, 11), (6, 14), (10, 3), (4, 16)]


def test_wb06_inv06_numbering_runs_one_ahead_from_st03():
    """DXF melompati ST02 lalu berjalan satu di depan; as-built dan EL bilang 1-23.

    Cacatnya DIAM: jumlah barisnya tetap 23, jadi tidak ada yang kentara
    salah. Yang rusak adalah join ke as-built pada ST -- tiap string menerima
    pv, mppt, dan metrik kabel milik TETANGGANYA, sementara ST24 yang tidak
    ada di as-built keluar tanpa pv sama sekali.

    Kebenarannya dari dua sumber bebas yang sepakat: List of DC Cables dan
    survei EL drone 2025 sama-sama menyebut ST01-23 kontigu. Kontrol pada
    WB06-INV05/07 menunjukkan ketiga sumber normalnya sepakat persis, jadi
    inverter ini benar-benar menyimpang.
    """
    masuk = [_lbl(6, 6, 1, 459500.0)] + [
        _lbl(6, 6, st, 459500.0 + 15.6 * (st - 2)) for st in range(3, 25)
    ]

    keluar = resolve_dxf_relabels(masuk)

    assert sorted(r["st"] for r in keluar) == list(range(1, 24))
    peta = _peta(keluar)
    assert peta[(459500.0, 9890600.0)] == (6, 6, 1)
    assert peta[(round(459500.0 + 15.6, 1), 9890600.0)] == (6, 6, 2)
    assert peta[(round(459500.0 + 15.6 * 22, 1), 9890600.0)] == (6, 6, 23)


def test_wb10_inv03_st_is_renumbered_by_spatial_order():
    """ST25 muncul dua kali -- kunci ST tidak bisa membetulkannya.

    String fisik ke-5 dilabeli "25", sehingga penomorannya berjalan satu di
    depan sampai akhir dan ST27 hilang. ``DXF_STRAY`` tidak menjangkau kasus
    ini: ia memindahkan salah satu salinan ke slot KOSONG, sedangkan di sini
    slot tujuannya (ST05) sudah terisi.

    Penomoran ulang menurut urutan spasial memulihkan bijeksi 1..27 tanpa
    bergantung pada nomor lama sama sekali. Sah karena diverifikasi 27/27
    terhadap posisi survei EL, yang jumlah dan penomorannya cocok as-built.
    """
    dxf_st = {1: 1, 2: 2, 3: 3, 4: 4, 5: 25, 26: 25, 27: 26}
    for benar in range(6, 26):
        dxf_st[benar] = benar - 1
    masuk = [_lbl(10, 3, dxf_st[benar], 459700.0, 9890600.0 - 7.3 * (benar - 1))
             for benar in range(1, 28)]

    keluar = _peta(resolve_dxf_relabels(masuk))

    for benar in range(1, 28):
        posisi = (459700.0, round(9890600.0 - 7.3 * (benar - 1), 1))
        assert keluar[posisi] == (10, 3, benar)


def test_penempatan_yang_dibantah_pindah_ke_posisi_survei_el():
    """Keempat inverter tepi utara Phase One memakai koordinat survei EL.

    Label DXF memberi mereka cross-slope curam (sampai -15,4 deg), tetapi tiga
    sumber bebas menyanggahnya: survei EL menempatkannya di tanah datar
    (|cs| <= 1,9 deg, sd <= 0,69), telemetri Juni mengukur asimetri praktis nol
    pada 72 string (r = -0,020 di mana model yang sama mencapai +0,699 di
    WB03-10), dan medan di posisi versi EL memang datar. Kontrol pada tiga
    inverter WB03-10 yang kedua sumbernya sepakat memberi sd yang sama persis.

    Ini yang dulu dikerjakan skrip sekali pakai (commit 22b059e) LANGSUNG ke
    CSV, sehingga builder tidak pernah bisa mereproduksinya: regenerasi polos
    memulangkan ke-72 baris ke posisi DXF tanpa satu pun galat, dan ke-72 itu
    hanya jatuh diam-diam ke TIDAK_BERLAKU. Uji ini yang menutup jalur itu.
    """
    import build_string_geometry as b

    labels = [
        {"label": "x", "wb": 2, "inv": 6, "st": 1, "north": 9890228.489, "east": 459790.538},
        {"label": "x", "wb": 2, "inv": 5, "st": 1, "north": 9890228.489, "east": 459790.538},
    ]
    posisi_el = {("WB02-INV06", 1): (9890241.334, 459726.987),
                 ("WB02-INV05", 1): (9890111.111, 459111.111)}

    dibantah, tetangga = b.relocate_to_el_survey(labels, posisi_el)

    assert (dibantah["north"], dibantah["east"]) == (9890241.334, 459726.987)
    assert dibantah["dari_el"] is True
    # Bedah, bukan sapu rata: inverter tetangga tidak dibantah, jadi tidak
    # dipindah walau survei EL memuat posisinya juga.
    assert (tetangga["north"], tetangga["east"]) == (9890228.489, 459790.538)
    assert "dari_el" not in tetangga


def test_bidang_string_yang_dipindah_ke_el_tetap_diisi(monkeypatch):
    """Sesudah pindah ke posisi EL, kolom bidang DIISI -- bukan dikosongkan.

    Sampai 15 Agu 2026 kolomnya sengaja NULL karena yang terbukti hanya "DXF
    salah", bukan "EL benar". Open Question 8 mencabut dasar itu: di posisi EL
    medannya dibaca ulang dari dsm.tif, |cs| median 1,34 maks 3,29 deg -- rata
    seperti sisa Phase One. Kalau NULL kembali, ke-72 string itu hilang dari
    penilaian tanpa satu pun galat.
    """
    import build_string_geometry as b

    monkeypatch.setattr(b, "local_plane",
                        lambda *a: {"slope_deg": 1.3, "aspect_deg": 90.0,
                                    "rms_m": 0.05})
    monkeypatch.setattr(b, "sample_dsm", lambda *a: 70.0)
    item = {"wb": 2, "inv": 6, "st": 1, "north": 9890241.334, "east": 459726.987,
            "dari_el": True}

    baris = b._geom_row(item, None, None, 1, 1)

    assert baris["cross_slope_deg"] is not None
    assert baris["slope_deg"] == 1.3


def test_posisi_el_dibaca_dari_survei_dengan_preambel_bergerigi(tmp_path):
    """Posisi EL = rata-rata lat/lon modul satu string, dari all.csv apa adanya.

    Dua jebakan berkasnya ikut diuji karena keduanya gagal DIAM-DIAM: header
    data mulai di bawah preambel ambang rating, dan nama kolomnya berawalan
    spasi (``' Latitude'``). Tanpa keduanya ditangani, pemetaannya kosong dan
    ke-72 string tetap di posisi DXF.
    """
    import build_string_geometry as b

    baris = ["# preambel ambang rating"] * 33
    baris.append("#String, Table x, Module x, Longitude, Latitude")
    baris.append("S206_01,1,1,116.6380000,-0.9930000")
    baris.append("S206_01,1,2,116.6380200,-0.9930200")
    baris.append("S105_03,1,1,116.6000000,-0.9900000")
    path = tmp_path / "all.csv"
    path.write_text("\n".join(baris) + "\n", encoding="utf-8-sig")

    posisi = b.el_survey_positions(str(path), {"WB02-INV06"})

    assert set(posisi) == {("WB02-INV06", 1)}
    north, east = posisi[("WB02-INV06", 1)]
    lat, lon = b.utm50s_to_latlon(north, east)
    assert lat == pytest.approx(-0.99301, abs=1e-5)
    assert lon == pytest.approx(116.63801, abs=1e-5)


# --- pusat meja dari persegi DXF ----------------------------------------------


def _persegi(east_min, north_min, layer="array5-finish",
             panjang=14.95, lebar=4.87):
    """Satu persegi meja (LWPOLYLINE tertutup) sebagai pasangan kode DXF."""
    sudut = [(east_min, north_min), (east_min + panjang, north_min),
             (east_min + panjang, north_min + lebar), (east_min, north_min + lebar)]
    ent = [(0, "LWPOLYLINE"), (8, layer), (90, 4), (70, 1)]
    for e, n in sudut:
        ent += [(10, e), (20, n)]
    return ent


def test_persegi_meja_dibaca_dari_layer_array(tmp_path):
    """Layer ``arrayN-*`` 1129.dxf memuat satu persegi per meja, 14,95 x 4,87 m.

    4,87 m adalah 4,95 m tampak-atas pada tilt 10 derajat, jadi persegi ini
    memang tapak meja -- bukan kotak pembantu gambar.
    """
    path = _dxf(tmp_path, [_persegi(459800.0, 9890600.0),
                           _persegi(459900.0, 9890600.0, layer="teks")])

    meja = parse_dxf_tables(path, "array")

    assert len(meja) == 1
    assert meja[0] == pytest.approx((459800.0, 459814.95, 9890600.0, 9890604.87))


def test_pusat_meja_datang_dari_persegi_yang_memuat_labelnya(tmp_path):
    """Label yang bergeser DI DALAM mejanya tetap memberi pusat meja yang benar.

    77 label WB03-10 duduk >10% panjang meja ke dalam (median 0,42; maksimum
    0,98) -- terbanyak di WB08-INV07, WB05-INV11, WB03-INV08, WB05-INV04,
    WB08-INV06. Rumus "label + setengah meja" menggeser jendela fit bidang
    sampai 7 m ke meja SEBELAHNYA, dan itulah yang membuat poligon drone di
    0191/0192 tampak seperti "meja tak standar".
    """
    from build_string_geometry import attach_table_centers, table_center_east

    path = _dxf(tmp_path, [_persegi(459800.0, 9890600.0)])
    meja = parse_dxf_tables(path, "array")
    # label 5,2 m ke dalam meja, seperti WB05-INV04-ST06
    label = {"wb": 5, "inv": 4, "st": 6, "east": 459805.2, "north": 9890602.4}

    [keluar] = attach_table_centers([label], meja)

    assert keluar["table_east"] == pytest.approx(459807.475)
    assert keluar["table_north"] == pytest.approx(9890602.435)
    # rumus pecahan akan menaruhnya 5,2 m terlalu ke timur
    assert table_center_east(5, label["east"]) == pytest.approx(459812.69, abs=0.01)


def test_label_di_luar_semua_persegi_tidak_diberi_pusat_meja(tmp_path):
    """Label tanpa meja yang memuatnya dibiarkan TANPA pusat meja, bukan disnap.

    13 label memang jatuh di luar setiap persegi; enam di antaranya 5,6-9,1 m
    jauhnya (WB08-INV06/INV07) dan persegi terdekatnya sudah dipakai label
    lain. Men-snap ke yang terdekat berarti menebak meja milik string lain
    lalu menerbitkannya sebagai koordinat -- kelas kesalahan yang sama dengan
    memilih salah satu dari dua posisi DXF pada label kembar.
    """
    from build_string_geometry import attach_table_centers

    path = _dxf(tmp_path, [_persegi(459800.0, 9890600.0)])
    meja = parse_dxf_tables(path, "array")
    label = {"wb": 8, "inv": 7, "st": 1, "east": 459790.0, "north": 9890602.4}

    [keluar] = attach_table_centers([label], meja)

    assert "table_east" not in keluar
    assert "table_north" not in keluar


# --- relokasi ke survei EL: pusat meja untuk string yang dipindah -------------


_MEJA_P1 = [(459800.0, 459814.4, 9890600.0, 9890604.77),     # A
            (459815.0, 459829.4, 9890600.0, 9890604.77),     # B
            (459830.0, 459844.4, 9890600.0, 9890604.77)]     # C


def _p1(inv, st, east, north, **kw):
    return {"label": "x", "wb": 1, "inv": inv, "st": st, "east": east, "north": north, **kw}


def test_gugus_wb01_yang_bersengketa_dipindah_ke_survei_el():
    """Ke-13 inverter gugus WB01 kini ikut PLACEMENT_FROM_EL.

    Empat jalur bebas memihak EL, satu di tiap gelombang uji awan 16 Agu: label
    fisik WB01-INV07; log cleaning 12 Sep di INV20/21 (EL 14/14, DXF 45 %);
    ke-7 persegi DXF yang oleh foto drone 0216/0217 berisi rumput adalah persis
    persegi yang tidak kebagian satu pun titik EL; dan 6 titik EL di luar
    persegi DXF mana pun mendarat di meja fisik yang tidak tergambar. Aturan
    PRD "semua atau tidak sama sekali" berlaku: memindah sebagian menaikkan
    tabrakan 14 -> 68, jadi ketiga belasnya sekaligus.
    """
    import build_string_geometry as b

    gugus = {f"WB01-INV{i:02d}" for i in (1, 2, 3, 6, 7, 8, 12, 13, 18, 19, 20, 21, 25)}
    assert gugus <= b.PLACEMENT_FROM_EL
    # inverter WB01 di luar gugus -- kontrol uji awan yang sepakat dengan EL -- tidak ikut
    assert not {"WB01-INV17", "WB01-INV22", "WB01-INV23", "WB01-INV24"} & b.PLACEMENT_FROM_EL


def test_string_yang_dipindah_mendapat_pusat_persegi_yang_memuat_titik_elnya():
    """Titik EL jatuh di dalam persegi meja pada 95 % string gugus WB01 (median
    jarak 0 m), jadi pusat persegi itulah pusat mejanya -- bukan titik EL yang
    bisa duduk di mana saja di dalam meja 14,40 x 4,77 m."""
    import build_string_geometry as b

    tetap = _p1(9, 1, 459807.2, 9890602.4)                      # label DXF di pusat A
    pindah = _p1(20, 4, 459836.0, 9890603.5, dari_el=True)      # EL di dalam C

    b.attach_relocated_table_centers([tetap, pindah], _MEJA_P1)

    assert pindah["table_east"] == pytest.approx(459837.2)
    assert pindah["table_north"] == pytest.approx(9890602.385)
    assert tetap["table_east"] == pytest.approx(459807.2)


def test_persegi_milik_string_yang_tidak_dipindah_tidak_bisa_diambil():
    """Titik EL yang jatuh di meja milik string yang TIDAK dipindah tidak
    mengambil meja itu. Dua string di satu meja adalah tabrakan, dan memilih
    salah satunya berarti menebak -- string yang dipindah tetap di titik EL-nya.
    """
    import build_string_geometry as b

    tetap = _p1(9, 1, 459807.2, 9890602.4)
    pindah = _p1(20, 4, 459803.0, 9890601.0, dari_el=True)      # EL di dalam A

    b.attach_relocated_table_centers([tetap, pindah], _MEJA_P1)

    assert "table_east" not in pindah
    assert tetap["table_east"] == pytest.approx(459807.2)


def test_dua_string_dipindah_di_satu_persegi_tidak_diberi_pusat_meja():
    """Bila dua titik EL jatuh di persegi yang sama, keduanya dibiarkan tanpa
    pusat meja. Di data nyata titik EL gugus WB01 yang di dalam persegi
    semuanya unik; pagar ini menjaga agar regenerasi berikutnya tidak diam-diam
    menaruh dua string di satu meja."""
    import build_string_geometry as b

    a = _p1(20, 4, 459836.0, 9890603.5, dari_el=True)
    c = _p1(21, 7, 459840.0, 9890601.0, dari_el=True)           # juga di dalam C

    b.attach_relocated_table_centers([a, c], _MEJA_P1)

    assert "table_east" not in a
    assert "table_east" not in c


def test_titik_el_di_luar_semua_persegi_tetap_di_titik_el():
    """Meja fisik yang tidak tergambar di DXF: enam titik EL gugus WB01 di luar
    persegi mana pun mendarat di meja fisik tanpa poligon di foto 0217. Titik
    itu tetap posisinya, tanpa pusat meja, bukan disnap ke persegi terdekat."""
    import build_string_geometry as b

    pindah = _p1(25, 3, 459870.0, 9890602.0, dari_el=True)

    b.attach_relocated_table_centers([pindah], _MEJA_P1)

    assert "table_east" not in pindah
    assert (pindah["east"], pindah["north"]) == (459870.0, 9890602.0)


# --- jendela fit bidang di pusat meja -----------------------------------------


def _dsm_miring_sebagian(label_east, dari_m, sampai_m, lereng_deg=10.0):
    """DSM sintetis: tanah turun ke timur ``lereng_deg`` hanya pada
    [label + dari_m, label + sampai_m], datar di luarnya. -> (image, header)."""
    import numpy as np
    from PIL import Image

    px = 0.25
    origin_e, origin_n = label_east - 20.0, 9890605.0
    east = origin_e + np.arange(240) * px                 # 60 m timur-barat
    turun = np.clip(east - label_east, dari_m, sampai_m) - dari_m
    z = 70.0 - turun * math.tan(math.radians(lereng_deg))
    image = Image.fromarray(np.tile(z, (40, 1)).astype(np.float32), mode="F")   # 10 m utara-selatan
    return image, (origin_e, origin_n, px, px)


@pytest.mark.parametrize("wb, inv, dari_m, sampai_m", [
    (5, 3, 0.0, 14.98),      # WB03-WB10: label di ujung barat, meja di timurnya
    (2, 5, -6.91, 6.91),     # WB01-WB02: label di pusat meja
], ids=["WB05-label-ujung-barat", "WB02-label-pusat"])
def test_kemiringan_menyamping_diukur_di_meja_bukan_di_titik_label(wb, inv, dari_m, sampai_m):
    """Jendela fit bidang harus menutup MEJA, bukan berpusat di titik label.

    Foto drone 12 September memastikan titik label ``1129.dxf`` di WB03-WB10
    berada di ujung barat meja, dan label DXF Cable Routing WB01-WB02 di
    pusatnya (cv-drone-plts docs/uji_segmentasi_nyata_12sep.md). Jendela yang
    berpusat di label WB03-WB10 separuhnya jatuh di barat meja; pada tanah yang
    hanya miring di bawah meja ia melaporkan lereng setengahnya. Di data nyata,
    separuh selisih cross-slope tetangga timur-barat -- perkiraan galat itu --
    bermedian ~2 derajat dengan p90 5,6.

    ``elev_m`` tetap elevasi DI titik label: ``tapak`` di cv-drone-plts
    membangun pojok meja dari titik itu.
    """
    import numpy as np
    import build_string_geometry as b

    label_east, label_north = 459800.0, 9890600.0
    image, header = _dsm_miring_sebagian(label_east, dari_m, sampai_m)
    item = {"wb": wb, "inv": inv, "st": 1, "north": label_north, "east": label_east}

    baris = b._geom_row(item, image, header, 1, 1)

    assert baris["cross_slope_deg"] == pytest.approx(10.0, abs=0.3)
    turun_di_label = float(np.clip(0.0, dari_m, sampai_m) - dari_m)
    assert baris["elev_m"] == pytest.approx(70.0 - turun_di_label * math.tan(math.radians(10.0)), abs=0.02)


def test_jendela_bidang_mengikuti_pusat_meja_dxf_bukan_rumus_pecahan():
    """Bila persegi DXF-nya diketahui, jendela fit bidang memakai pusat ITU.

    Untuk label yang bergeser di dalam mejanya, rumus pecahan (label + 7,49 m)
    menaruh jendela di meja sebelahnya. Di sini tanah hanya miring pada
    [-5,2 .. +9,7] m dari label -- tepat di bawah meja yang memuat label yang
    bergeser 5,2 m ke dalam -- dan datar di luarnya. Rumus pecahan akan
    melaporkan kemiringan yang jauh lebih kecil karena separuh jendelanya
    jatuh di tanah datar sebelah timur.
    """
    import build_string_geometry as b

    label_east, label_north = 459800.0, 9890600.0
    image, header = _dsm_miring_sebagian(label_east, -5.2, 9.7)
    item = {"wb": 5, "inv": 4, "st": 6, "north": label_north, "east": label_east,
            "table_east": label_east + 2.275, "table_north": label_north}

    baris = b._geom_row(item, image, header, 1, 1)
    tanpa_meja = b._geom_row({k: v for k, v in item.items()
                              if k not in ("table_east", "table_north")},
                             image, header, 1, 1)

    assert baris["cross_slope_deg"] == pytest.approx(10.0, abs=0.3)
    assert tanpa_meja["cross_slope_deg"] < 0.8 * baris["cross_slope_deg"]
    # Pusat meja terbit dalam kedua satuan; tanpa meja kolomnya kosong, bukan
    # diisi perkiraan rumus pecahan yang tidak bisa dibedakan dari hasil DXF.
    assert baris["table_east"] == pytest.approx(item["table_east"], abs=0.001)
    assert baris["table_lat"] == pytest.approx(
        b.utm50s_to_latlon(item["table_north"], item["table_east"])[0], abs=1e-7)
    assert tanpa_meja["table_east"] is None and tanpa_meja["table_lon"] is None


# --- pemetaan kanal yang terbantah telemetri ----------------------------------


def test_as_built_channel_is_dropped_when_strings_yaml_calls_it_empty():
    """Pemetaan as-built yang mendarat di kanal kosong GUGUR, bukan dipakai.

    Telemetri 13 Mei 2026 memutuskannya: delapan kanal semacam ini membaca
    0,00 kW di tengah hari sementara kanal terpakai di inverter yang sama
    berjalan 3,1-3,8 kW. Seluruh kanal lain yang ditandai kosong di inverter
    itu juga nol, jadi yang terukur memang beda terpasang vs tidak.

    strings.yaml adalah acuan yang tervalidasi telemetri; as-built terbukti
    keliru di titik ini. Menyimpan pv yang terbantah membuat artefak geometri
    menunjuk kanal yang tidak pernah menghasilkan apa pun.
    """
    assert disprove_empty_channel(
        "WB06-INV12", 13, 3, {"WB06-INV12": {5, 13, 19, 24}}) == (None, None)


def test_channel_backed_by_telemetry_survives():
    """Kanal yang tidak ditandai kosong tidak boleh ikut digugurkan.

    Aturan ini bedah: hanya 9 baris dari 4.470 yang tersentuh. Menyapu lebih
    luas akan membuang pemetaan pv yang justru benar untuk 4.459 string.
    """
    assert disprove_empty_channel(
        "WB06-INV12", 14, 3, {"WB06-INV12": {5, 13, 19, 24}}) == (14, 3)
    assert disprove_empty_channel("WB03-INV01", 13, 3, {"WB06-INV12": {13}}) == (13, 3)
    assert disprove_empty_channel(
        "WB06-INV12", None, None, {"WB06-INV12": {13}}) == (None, None)


def test_empty_channels_are_read_from_strings_yaml():
    """Daftar kanal kosong dibaca dari acuannya, tidak disalin ke builder."""
    peta = empty_pv_channels()

    assert len(peta) == 194
    assert 13 in peta["WB06-INV12"]
    assert peta["WB01-INV01"] == set(range(19, 29))


# --- dua ST satu kanal di as-built (Koreksi As-Built butir 2.1) ---------------

def test_as_built_channel_is_replaced_only_while_it_still_reads_the_disputed_value():
    """Koreksi berlaku atas nilai yang DIPERSENGKETAKAN, bukan atas nomor ST.

    WB04-INV01 ST23 tercatat M1PV6 padahal PV6 milik MPPT2 -- barisnya
    membantah dirinya sendiri. Bila EPC mengirim cable list revisi, nilai
    barunya yang berlaku: menimpa jawaban EPC diam-diam dengan kesimpulan
    kita jauh lebih berbahaya daripada membiarkan sengketa lama terlihat.
    """
    assert fix_asbuilt_pv((4, 1, 23), 6, 1) == (26, 6)
    assert fix_asbuilt_pv((4, 1, 23), 7, 2) == (7, 2)       # revisi EPC menang
    assert fix_asbuilt_pv((4, 1, 6), 6, 2) == (6, 2)        # pasangannya benar
    assert fix_asbuilt_pv((4, 1, 23), None, None) == (None, None)


def test_undecided_pair_is_blanked_not_guessed():
    """Bila bukti tidak memilih, KEDUA ST kehilangan pv.

    Salah satu dari keduanya pasti keliru (kanal bebas yang berarus tidak
    diklaim siapa pun), tapi pola urutan saja bukan bukti. Menebak berarti
    menyajikan temuan citra satu meja sebagai bukti untuk meja tetangganya.
    """
    for kunci, tercatat in [((4, 19, 19), (16, 4)), ((4, 19, 23), (16, 4)),
                            ((5, 19, 6), (6, 2)), ((5, 19, 14), (6, 2)),
                            ((7, 17, 3), (25, 6)), ((7, 17, 10), (25, 6))]:
        assert fix_asbuilt_pv(kunci, *tercatat) == (None, None), kunci


def test_filled_pv_is_unique_per_inverter_in_the_geometry_artifact():
    """(inverter_id, pv) adalah kunci kontrak tabel ke M2g (repo cv-drone-plts).

    Satu kanal PV Huawei = satu string fisik. Dua baris ber-pv sama DIGABUNG
    oleh kontrak: temuan citra dua meja berbeda jatuh ke satu kanal telemetri.
    pv kosong dikecualikan -- itu keputusan "belum terbukti", bukan kunci.
    """
    g = pd.read_csv(OUT_PATH)
    terisi = g[g["pv"].notna()]
    ganda = terisi[terisi.duplicated(["inverter_id", "pv"], keep=False)]

    assert ganda.empty, ganda[["inverter_id", "st", "pv", "mppt"]].to_string()


# Keputusan untuk string TANPA pv. Tiap baris sudah diuji dan gugur atau tak
# terputuskan -- bukan terlewat. Nomor butir merujuk coba/Koreksi_AsBuilt_DC_
# Cable_List_20260806.md; kanal penggantinya menunggu jawaban EPC.
PV_KOSONG_DIPUTUSKAN = {
    # 2.5 -- kanal as-built membaca 0 kW dan strings.yaml menandainya kosong.
    ("WB03-INV05", 2), ("WB04-INV04", 15), ("WB04-INV15", 8),
    ("WB05-INV05", 7), ("WB06-INV10", 25), ("WB06-INV12", 20),
    ("WB07-INV08", 22), ("WB07-INV13", 19), ("WB09-INV12", 11),
    # 2.4 -- tujuan as-built di inverter lain (WB03-INV12); parser menolaknya.
    ("WB03-INV13", 1),
    # 2.1 tak terputuskan -- dua ST satu kanal, bukti tidak memilih.
    ("WB04-INV19", 19), ("WB04-INV19", 23), ("WB05-INV19", 6),
    ("WB05-INV19", 14), ("WB07-INV17", 3), ("WB07-INV17", 10),
}


def test_strings_without_pv_are_exactly_the_decided_ones():
    """String tanpa pv tidak ikut join ke telemetri maupun ke kontrak citra.

    Tambahan diam-diam = string hilang dari analisis tanpa ada yang
    memutuskan. Pengurangan diam-diam = kanal yang sudah gugur diisi lagi
    tanpa catatan buktinya. Keduanya harus gagal keras di sini.
    """
    g = pd.read_csv(OUT_PATH)
    kosong = g[g["pv"].isna()]

    assert set(zip(kosong["inverter_id"], kosong["st"])) == PV_KOSONG_DIPUTUSKAN
    assert kosong["mppt"].isna().all()


# --- digit MPPT yang membantah kanal PV-nya sendiri ---------------------------

# (wb, inv, st) -> (pv, mppt) tercatat di as-built, per 2026-09-12. Kanal PV-nya
# terbukti telemetri; digit MPPT-nya bertentangan dengan peta perangkat keras.
MPPT_TERCATAT_SALAH = {
    (4, 3, 4): (6, 1), (4, 3, 15): (7, 1), (4, 4, 17): (17, 2),
    (4, 6, 2): (2, 2), (4, 7, 5): (5, 1),
    (4, 10, 19): (22, 4), (4, 10, 20): (21, 4),
    (4, 10, 21): (16, 3), (4, 10, 22): (18, 3),
    (4, 16, 13): (17, 3), (4, 16, 14): (18, 3),
    (5, 4, 19): (15, 3), (5, 4, 20): (17, 3), (5, 6, 23): (23, 4),
    (5, 7, 4): (5, 1), (5, 7, 13): (11, 1), (7, 14, 8): (16, 6),
}


def _mppt_330ktl():
    """PV -> MPPT SUN2000-330KTL-H1 (WB03-WB10), dari acuannya di strings.yaml."""
    import yaml

    with open(STRINGS_YAML, encoding="utf-8") as handle:
        peta = yaml.safe_load(handle)["mppt_map"]["SUN2000-330KTL-H1"]["mppt"]
    return {pv: mppt for mppt, pvs in peta.items() for pv in pvs}


def test_contradicting_mppt_digit_yields_to_the_hardware_map_not_the_pv():
    """Yang dikoreksi digit MPPT-nya; kanal PV-nya dipertahankan.

    Satu kanal PV hanya duduk di satu MPPT, jadi tiap baris ini pasti salah di
    salah satu kolomnya. Telemetri memilih kolomnya: kanal PV yang tercatat
    BERARUS dan tidak diklaim ST lain, sedangkan seluruh kanal berarus di MPPT
    yang tercatat sudah dipakai ST lain. Memindahkan string ke MPPT tercatat
    butuh dua kesalahan sekaligus; mengoreksi digitnya cukup satu. Tegangan
    kanal ikut memastikan: selalu sama dengan saudara se-MPPT menurut peta
    perangkat keras, bukan saudara menurut as-built.

    Kolom ini menentukan kelompok pembanding paling ketat -- string se-MPPT
    dijejak sebagai satu titik daya maksimum. MPPT yang salah menaruh string
    di kelompok yang tegangannya berbeda sampai 44 V.
    """
    peta = _mppt_330ktl()

    for kunci, (pv, mppt) in MPPT_TERCATAT_SALAH.items():
        assert fix_asbuilt_pv(kunci, pv, mppt) == (pv, peta[pv]), kunci


def test_geometry_mppt_agrees_with_the_hardware_map_on_every_wb03_wb10_string():
    """Artefak tidak boleh lagi membawa pasangan PV-MPPT yang mustahil.

    Pasangan semacam itu tidak mungkin terpasang secara fisik, jadi setiap
    baris yang muncul di sini adalah sengketa as-built yang lolos tanpa
    diputuskan. WB01/WB02 dikecualikan karena memakai model lain (215KTL)
    yang MPPT-nya diturunkan dari pv, bukan dibaca dari cable list.
    """
    g = pd.read_csv(OUT_PATH)
    g = g[g["pv"].notna() & ~g["inverter_id"].str[:4].isin(["WB01", "WB02"])]
    salah = g[g["pv"].astype(int).map(_mppt_330ktl()) != g["mppt"]]

    assert salah.empty, salah[["inverter_id", "st", "pv", "mppt"]].to_string()


# --- parsing DXF Phase One (WB01/WB02) ----------------------------------------


def _phase_one(tmp_path, teks, layer="_TEXT_STRING"):
    """Satu MTEXT berlabel ``teks`` pada ``layer``, di koordinat tetap."""
    return _dxf(tmp_path, [[
        ("0", "MTEXT"), ("8", layer), ("1", teks),
        ("10", "459600.0"), ("20", "9890000.0"),
    ]])


def test_phase_one_label_splits_into_block_inverter_and_string(tmp_path):
    """Digit pertama = blok WB, dua berikutnya = inverter, sufiks = string.

    Konvensi Phase One (ISPP/Tractebel) sama sekali berbeda dari
    ``WB##INV##ST##`` milik SEPEC di WB03-WB10, jadi ia butuh parser sendiri
    dan tidak boleh dipaksakan ke LABEL_RE.
    """
    rows = parse_phase_one_labels(_phase_one(tmp_path, "S101-18"))
    assert [(r["wb"], r["inv"], r["st"]) for r in rows] == [(1, 1, 18)]

    rows = parse_phase_one_labels(_phase_one(tmp_path, "S205-10"))
    assert [(r["wb"], r["inv"], r["st"]) for r in rows] == [(2, 5, 10)]


def test_phase_one_pv_channel_equals_the_field_string_number(tmp_path):
    """Di WB01/WB02 nomor ST lapangan ADALAH kanal PV Huawei.

    Ini yang membuat Phase One tidak butuh as-built DC cable list sama sekali,
    berbeda dengan WB03-WB10 yang pemetaan ST->PV-nya harus dibaca dari sana.
    Menyalin pola WB03-10 ke sini akan mengosongkan pv untuk 900 string yang
    sebenarnya sudah diketahui.
    """
    rows = parse_phase_one_labels(_phase_one(tmp_path, "S212-14"))

    assert rows[0]["st"] == 14
    assert rows[0]["pv"] == rows[0]["st"]


def test_phase_one_mppt_pairing_is_sourced_from_strings_yaml():
    """Pasangan MPPT dibaca dari strings.yaml, bukan disalin ulang di builder.

    SUN2000-215KTL di WB01/WB02 memasangkan dua string berurutan per MPPT,
    dan repo SUDAH memiliki tabel itu berkunci model inverter. Menyalinnya
    ke builder akan membuat dua rumah untuk satu fakta; begitu salah satunya
    berubah, keduanya menyimpang tanpa ada yang menyadari.

    Kolom ini bukan hiasan: dua string yang berbagi satu MPPT dijejak sebagai
    SATU titik daya maksimum, jadi pasangan se-MPPT adalah pembanding paling
    ketat yang ada -- keduanya melihat irradiance, suhu, DAN penjejak sama.
    """
    peta = phase_one_mppt_map()

    assert len(peta) == 18
    assert [peta[pv] for pv in (1, 2, 3, 17, 18)] == [1, 1, 2, 9, 9]
    assert sorted(set(peta.values())) == list(range(1, 10))


def test_phase_one_reads_the_revised_label_s226_as_wb01_inv25(tmp_path):
    """S226 sisa revisi gambar: sudah diubah jadi S125 = WB01-INV25.

    Sheet ini masih membawa label lamanya. Dibaca apa adanya, S226 menjadi
    WB02-INV26 yang TIDAK ADA di telemetri, sekaligus meninggalkan
    WB01-INV25 tanpa koordinat. Bukti spasial sejalan: S226 duduk di blok
    barat, terpisah ~99 m dari seluruh gugus S2xx.
    """
    rows = parse_phase_one_labels(_phase_one(tmp_path, "S226-07"))

    assert [(r["wb"], r["inv"], r["st"]) for r in rows] == [(1, 25, 7)]


def test_phase_one_ignores_text_outside_the_string_layer(tmp_path):
    """Gambar ini gambar tray AC: 7.840 teks, hanya 900 di layer string.

    Tanpa penyaringan layer, dimensi BOQ dan teks kop gambar ikut terbaca.
    """
    path = _dxf(tmp_path, [
        [("0", "MTEXT"), ("8", "_TRAY_DIMENSION_FOR_BOQ"), ("1", "S101-18"),
         ("10", "459600.0"), ("20", "9890000.0")],
        [("0", "MTEXT"), ("8", "_TEXT_STRING"), ("1", "S102-03"),
         ("10", "459610.0"), ("20", "9890010.0")],
    ])

    assert [r["label"] for r in parse_phase_one_labels(path)] == ["S102-03"]


def test_phase_one_strips_mtext_formatting_codes(tmp_path):
    """Teks MTEXT membawa kode format di depan nilainya.

    Setiap label di gambar ini berbentuk ``\\W1.23077x;S101-18``. Tanpa
    dibersihkan, tidak satu pun dari 900 label cocok dengan polanya dan
    parser mengembalikan daftar kosong -- gagal DIAM, bukan gagal berisik.
    """
    rows = parse_phase_one_labels(
        _phase_one(tmp_path, "\\W1.23077x;S101-18")
    )

    assert [(r["wb"], r["inv"], r["st"]) for r in rows] == [(1, 1, 18)]


# --- komponen kemiringan timur-barat ------------------------------------------


def test_cross_slope_zero_when_ground_falls_due_north():
    """Kemiringan utara-selatan dikompensasi struktur (DW-003 punya varian
    untuk itu) dan tidak memiringkan bidang modul ke timur/barat."""
    assert cross_slope_deg(15.0, 0.0) == pytest.approx(0.0, abs=1e-9)
    assert cross_slope_deg(15.0, 180.0) == pytest.approx(0.0, abs=1e-9)


def test_cross_slope_positive_when_ground_falls_east():
    """Tanah turun ke timur -> bidang modul menghadap agak timur -> pagi kuat."""
    assert cross_slope_deg(12.0, 90.0) == pytest.approx(12.0, abs=1e-9)


def test_cross_slope_negative_when_ground_falls_west():
    """Tandanya yang membedakan defisit pagi dari defisit sore."""
    assert cross_slope_deg(12.0, 270.0) == pytest.approx(-12.0, abs=1e-9)


def test_cross_slope_takes_only_the_east_west_component():
    """Lereng 20 derajat ke timur laut hanya menyumbang komponen timurnya."""
    expected = math.degrees(math.atan(
        math.tan(math.radians(20.0)) * math.sin(math.radians(45.0))))

    assert cross_slope_deg(20.0, 45.0) == pytest.approx(expected, abs=1e-9)
    assert 0.0 < cross_slope_deg(20.0, 45.0) < 20.0
