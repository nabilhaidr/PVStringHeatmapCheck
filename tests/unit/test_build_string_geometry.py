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
    parse_dxf_string_labels,
    parse_phase_one_labels,
    phase_one_mppt_map,
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
