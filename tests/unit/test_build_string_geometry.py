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

from build_string_geometry import cross_slope_deg, parse_dxf_string_labels


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
