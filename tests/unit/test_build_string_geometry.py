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

import pytest

from build_string_geometry import (
    cross_slope_deg,
    disprove_empty_channel,
    empty_pv_channels,
    parse_dxf_string_labels,
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
