"""Tests untuk ``pv_pipeline.string_intraday_diagnostic``.

Nilai modul ini bergantung pada satu keputusan: memisahkan soiling dari
shading lewat BENTUK kurva harian. Tes di bawah menjaga pemisahan itu --
kalau ambang atau logika klasifikasi bergeser sampai string ternaungi
terbaca sebagai kotor (atau sebaliknya), tim lapangan akan dikirim membawa
alat yang salah dan defisitnya tidak akan hilang.
"""
from __future__ import annotations

import pandas as pd
import pytest


HOURS = list(range(7, 18))

# Kurva "sehat": naik sampai siang lalu turun (bentuk kubah).
HEALTHY = [2.0, 4.0, 6.0, 8.0, 9.0, 10.0, 9.0, 8.0, 6.0, 4.0, 2.0]


def _day_rows(date, profiles):
    """Baris 1 timestamp per jam untuk satu inverter.

    ``profiles``: {pv_index: [daya kW per jam]}.
    """
    rows = []
    for k, hour in enumerate(HOURS):
        row = {
            "Inverter_ID": "WB01-INV01",
            "Start Time": f"{date} {hour:02d}:00:00",
        }
        for pv, series in profiles.items():
            row[f"PV{pv} Power(kW)"] = series[k]
        rows.append(row)
    return rows


def _make_day(tmp_path, date, target_series):
    """Satu hari: 3 string sehat + 1 string target dgn kurva yang diuji."""
    profiles = {1: HEALTHY, 2: HEALTHY, 3: HEALTHY, 4: target_series}
    path = tmp_path / f"{date}.csv"
    pd.DataFrame(_day_rows(date, profiles)).to_csv(path, index=False)
    return path


def test_shaded_string_is_not_labelled_soiling(tmp_path):
    """String ternaungi pagi harus jadi SHADING, bukan UNIFORM.

    Ciri pemutusnya: siang/sore rasionya MELAMPAUI 1,0. Panel yang kotor
    atau rusak tidak mungkin mengungguli tetangganya yang bersih, jadi
    rasio >1,0 membuktikan panelnya sehat dan yang salah adalah halangan
    di jam lain. Membersihkannya tidak akan menolong.
    """
    from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

    # Pagi tertutup, siang normal, sore justru lebih tinggi dari tetangga.
    shaded = [0.6, 1.2, 1.8, 8.0, 9.0, 10.0, 9.5, 8.8, 6.9, 4.8, 2.6]
    paths = [_make_day(tmp_path, d, shaded)
             for d in ("2026-06-01", "2026-06-02", "2026-06-03")]

    rep = build_intraday_diagnostic(paths, inverter_ids=["WB01-INV01"])
    row = rep.classification.set_index("pv_string").loc["WB01-INV01-PV4"]

    assert row["kategori"] == "SHADING_PULIH"
    assert row["ratio_max_hourly"] >= 1.02
    assert row["jam_terburuk"] < row["jam_terbaik"]  # defisit di pagi hari


def test_uniformly_soiled_string_is_labelled_uniform(tmp_path):
    """Rugi proporsional sepanjang hari harus jadi UNIFORM.

    Ini satu-satunya bentuk yang konsisten dengan soiling; hanya string
    kategori ini yang layak dites dengan pembersihan.
    """
    from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

    soiled = [round(v * 0.80, 3) for v in HEALTHY]
    paths = [_make_day(tmp_path, d, soiled)
             for d in ("2026-06-01", "2026-06-02", "2026-06-03")]

    rep = build_intraday_diagnostic(paths, inverter_ids=["WB01-INV01"])
    row = rep.classification.set_index("pv_string").loc["WB01-INV01-PV4"]

    assert row["kategori"] == "UNIFORM"
    assert row["ratio_median"] == pytest.approx(0.80, abs=0.02)
    assert row["ratio_range"] <= 0.10       # datar = tak bergantung jam
    assert row["ratio_max_hourly"] < 1.02   # tak pernah melampaui tetangga


def test_uniform_string_carries_cable_vdrop_evidence(tmp_path):
    """UNIFORM punya dua sebab; bentuk kurva tidak bisa memisahkannya.

    Defisit rata sepanjang hari adalah tanda tangan soiling DAN tanda
    tangan rugi resistif kabel DC. Kolom vdrop dibandingkan terhadap
    median se-inverter -- pembanding yang sama dengan yang dipakai
    ``ratio`` -- supaya terlihat apakah string ini memang lebih rugi
    secara permanen daripada tetangganya sebelum regu cuci dikirim.
    """
    from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

    dirty = [1.6, 3.2, 4.8, 6.4, 7.2, 8.0, 7.2, 6.4, 4.8, 3.2, 1.6]  # 80% rata
    paths = [_make_day(tmp_path, d, dirty)
             for d in ("2026-06-01", "2026-06-02", "2026-06-03")]
    metrics = pd.DataFrame([
        {"inverter_id": "WB01-INV01", "pv": 1, "length_m": 20.0,
         "vdrop_pct": 0.30},
        {"inverter_id": "WB01-INV01", "pv": 2, "length_m": 22.0,
         "vdrop_pct": 0.30},
        {"inverter_id": "WB01-INV01", "pv": 3, "length_m": 24.0,
         "vdrop_pct": 0.40},
        {"inverter_id": "WB01-INV01", "pv": 4, "length_m": 190.0,
         "vdrop_pct": 2.60},
    ])

    rep = build_intraday_diagnostic(
        paths, inverter_ids=["WB01-INV01"], cable_metrics=metrics,
    )
    row = rep.classification.set_index("pv_string").loc["WB01-INV01-PV4"]

    assert row["kategori"] == "UNIFORM"
    assert row["vdrop_pct"] == pytest.approx(2.60)
    # median se-inverter dari [0,30 0,30 0,40 2,60] = 0,35 -> selisih 2,25 pp
    assert row["vdrop_minus_inv_median"] == pytest.approx(2.25)


def test_string_without_cable_row_gets_na_vdrop(tmp_path):
    """String tanpa pasangan di as-built cable list harus NA, bukan 0.

    Nilai 0 akan terbaca sebagai "kabelnya pendek", padahal yang benar
    adalah "tidak diketahui" -- 24 string WB03-10 memang tidak punya baris
    kabel karena strings.yaml dan as-built berbeda di 21 inverter.
    """
    from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

    dirty = [1.6, 3.2, 4.8, 6.4, 7.2, 8.0, 7.2, 6.4, 4.8, 3.2, 1.6]
    paths = [_make_day(tmp_path, d, dirty)
             for d in ("2026-06-01", "2026-06-02", "2026-06-03")]
    metrics = pd.DataFrame([
        {"inverter_id": "WB01-INV01", "pv": 1, "length_m": 20.0,
         "vdrop_pct": 0.30},
    ])

    rep = build_intraday_diagnostic(
        paths, inverter_ids=["WB01-INV01"], cable_metrics=metrics,
    )
    row = rep.classification.set_index("pv_string").loc["WB01-INV01-PV4"]

    assert pd.isna(row["vdrop_pct"])
    assert pd.isna(row["vdrop_minus_inv_median"])


def test_late_dropout_counts_as_afternoon_shading(tmp_path):
    """String yang mati dini tiap sore = shading sore, bukan string rusak.

    Tetangga se-inverter masih produksi di jam yang sama, jadi ini bukan
    matahari terbenam melainkan halangan di sisi barat.
    """
    from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

    dropout = HEALTHY[:8] + [0.0, 0.0, 0.0]   # mati sejak jam 15
    paths = [_make_day(tmp_path, d, dropout)
             for d in ("2026-06-01", "2026-06-02", "2026-06-03")]

    rep = build_intraday_diagnostic(paths, inverter_ids=["WB01-INV01"])
    row = rep.classification.set_index("pv_string").loc["WB01-INV01-PV4"]

    assert row["kategori"].startswith("SHADING")
    assert row["dropout_share_pct"] == pytest.approx(100.0)


def test_rain_recovery_separates_dirt_from_obstruction(tmp_path):
    """Hujan menaikkan rasio string kotor, tidak untuk yang ternaungi.

    Inilah ujian lapangan yang paling murah: kalau delta_pp ~0 setelah
    hujan lebat, defisitnya bukan debu dan cleaning percuma.
    """
    from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

    dirty = [round(v * 0.70, 3) for v in HEALTHY]     # sebelum hujan
    washed = [round(v * 0.95, 3) for v in HEALTHY]    # sesudah hujan
    paths = [
        _make_day(tmp_path, "2026-06-01", dirty),
        _make_day(tmp_path, "2026-06-02", dirty),
        _make_day(tmp_path, "2026-06-05", washed),
        _make_day(tmp_path, "2026-06-06", washed),
    ]
    events = [{
        "nama": "hujan 3 Jun",
        "before": ("2026-06-01", "2026-06-02"),
        "after": ("2026-06-05", "2026-06-06"),
    }]

    rep = build_intraday_diagnostic(
        paths, inverter_ids=["WB01-INV01"], rain_events=events,
    )
    row = rep.rain.set_index("pv_string").loc["WB01-INV01-PV4"]

    assert row["delta_pp"] > 15.0   # pulih jelas -> memang debu


def test_empty_pv_slots_are_excluded(tmp_path):
    """Slot PV kosong by design tidak boleh muncul sebagai string bermasalah.

    Tanpa ini, slot kosong tampil dgn rasio 0 dan membanjiri daftar
    prioritas -- persis kegagalan yang pernah terjadi di
    CleaningRecommendation.
    """
    from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

    profiles = {1: HEALTHY, 2: HEALTHY, 3: HEALTHY, 4: [0.0] * len(HOURS)}
    path = tmp_path / "2026-06-01.csv"
    pd.DataFrame(_day_rows("2026-06-01", profiles)).to_csv(path, index=False)

    rep = build_intraday_diagnostic(
        [path], inverter_ids=["WB01-INV01"],
        empty_pv_map={"WB01-INV01": [4]},
    )
    assert "WB01-INV01-PV4" not in set(rep.classification["pv_string"])


def test_empty_input_returns_typed_empty():
    """Tanpa CSV yang cocok, kembalikan frame kosong bertipe -- bukan crash."""
    from pv_pipeline.string_intraday_diagnostic import (
        CLASSIFICATION_COLUMNS, build_intraday_diagnostic,
    )

    rep = build_intraday_diagnostic([], inverter_ids=["WB01-INV01"])
    assert list(rep.classification.columns) == CLASSIFICATION_COLUMNS
    assert rep.classification.empty
    assert rep.profile.empty
    assert "Tidak ada string" in rep.summary()


def test_report_writes_four_sheets(tmp_path):
    """Workbook harus punya empat sheet yang dijanjikan ke pembaca."""
    from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

    paths = [_make_day(tmp_path, "2026-06-01", HEALTHY)]
    rep = build_intraday_diagnostic(paths, inverter_ids=["WB01-INV01"])
    out = rep.to_excel(tmp_path / "diag.xlsx")

    assert out.exists()
    assert pd.ExcelFile(out).sheet_names == [
        "Klasifikasi", "Profil_Jam", "Uji_Hujan", "Metadata",
    ]
