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


# --- kolom bukti geometris: cross-slope, asimetri harapan, residual ------------

# Asimetri pagi-sore (rasio pagi - rasio sore) yang ditimbulkan MURNI oleh
# kemiringan tanah menyamping, relatif meja datar. Diturunkan dari pvlib
# clear-sky pada lintang site (-0,9912), tilt 10 derajat menghadap utara,
# jendela pagi 7-9 dan sore 15-17 -- jendela yang sama dengan classify_strings.
# Angka ini fisika, bukan penyetelan: tes di bawah menguncinya.
DERIVED_AMPM_ASYM = {
    80:  {2.5: 0.130, 5.0: 0.259, 9.2: 0.476, 13.2: 0.680, 18.3: 0.935},
    172: {2.5: 0.114, 5.0: 0.227, 9.2: 0.417, 13.2: 0.596, 18.3: 0.820},
    264: {2.5: 0.127, 5.0: 0.253, 9.2: 0.464, 13.2: 0.663, 18.3: 0.912},
    356: {2.5: 0.143, 5.0: 0.285, 9.2: 0.523, 13.2: 0.747, 18.3: 1.026},
}


def _geom(rows):
    """DataFrame geometri minimal: inverter_id + pv + cross_slope_deg."""
    return pd.DataFrame(
        [{"inverter_id": i, "pv": p, "cross_slope_deg": c} for i, p, c in rows]
    )


def _equinox_days(tmp_path):
    """Tiga hari mengapit 21 Maret -> timestamp median jatuh di doy 80."""
    return [_make_day(tmp_path, d, HEALTHY)
            for d in ("2026-03-20", "2026-03-21", "2026-03-22")]


def test_expected_asymmetry_reproduces_derived_clear_sky_physics():
    """Nilai harapan harus cocok dengan geometri surya yang sudah diturunkan.

    Kalau bentuk fungsinya diganti (linear, tan, polinom), 20 angka ini
    adalah yang pertama meleset -- dan begitu meleset, ``ampm_residual``
    ikut bergeser sehingga string geometris murni tampak seperti obstruksi
    dan regu lapangan dikirim ke petak yang tidak perlu dikunjungi.
    """
    from pv_pipeline.string_intraday_diagnostic import expected_ampm_asymmetry

    for doy, per_slope in DERIVED_AMPM_ASYM.items():
        for cs, want in per_slope.items():
            got = expected_ampm_asymmetry(cs, doy)
            assert got == pytest.approx(want, abs=0.005), (doy, cs)


def test_expected_asymmetry_follows_slope_direction():
    """Tanah turun ke TIMUR -> pagi lebih kuat; ke BARAT -> sore lebih kuat.

    Tanda inilah yang membedakan SHADING_PAGI geometris dari SHADING_SORE
    geometris. Nilai mutlak tanpa tanda akan menukar keduanya.
    """
    from pv_pipeline.string_intraday_diagnostic import expected_ampm_asymmetry

    assert expected_ampm_asymmetry(12.0, 80) > 0
    assert expected_ampm_asymmetry(-12.0, 80) < 0
    assert expected_ampm_asymmetry(12.0, 80) == pytest.approx(
        -expected_ampm_asymmetry(-12.0, 80)
    )
    assert expected_ampm_asymmetry(0.0, 80) == pytest.approx(0.0)


def test_seasonal_drift_is_the_same_fraction_at_every_cross_slope():
    """Drift musiman harus ~22,5% dari nilainya sendiri, apa pun cross-slope-nya.

    Ini bukan sekadar sifat menarik: ``SEASONAL_REL_RANGE_MAX = 0,30`` sah
    sebagai ambang tunggal HANYA kalau angkanya tidak bergantung pada besar
    cross-slope. Bentuk yang membuatnya bergantung (misal linear murni)
    membuat satu ambang tidak bisa melayani string landai dan curam sekaligus.
    """
    from pv_pipeline.string_intraday_diagnostic import (
        SEASONAL_REL_RANGE_MAX, expected_ampm_asymmetry,
    )

    fractions = []
    for cs in (2.5, 9.2, 18.3, 31.5):
        vals = [expected_ampm_asymmetry(cs, doy) for doy in DERIVED_AMPM_ASYM]
        fractions.append((max(vals) - min(vals)) / (sum(vals) / len(vals)))

    assert max(fractions) - min(fractions) < 0.001   # praktis identik
    assert max(fractions) == pytest.approx(0.225, abs=0.005)
    assert max(fractions) < SEASONAL_REL_RANGE_MAX   # ambang menampung drift


def test_expected_asymmetry_is_referenced_to_inverter_median_not_flat_ground(tmp_path):
    """Pembandingnya median se-inverter, sama seperti ``ratio`` -- bukan meja datar.

    ``ratio`` lahir dari pembagian terhadap median tetangga se-inverter.
    Kalau SEMUA string satu inverter miring 10 derajat ke timur yang sama,
    tidak satu pun yang tampak asimetris terhadap tetangganya, jadi harapan
    yang benar adalah NOL. Memakai meja datar sebagai acuan akan memberi
    +0,52 untuk keempatnya -- offset sistematis per inverter yang diam-diam
    menggeser seluruh peringkat kerja lapangan.
    """
    from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

    geom = _geom([("WB01-INV01", pv, 10.0) for pv in (1, 2, 3, 4)])

    rep = build_intraday_diagnostic(
        _equinox_days(tmp_path), inverter_ids=["WB01-INV01"], string_geometry=geom,
    )
    out = rep.classification.set_index("pv_string")

    assert out["cross_slope_deg"].tolist() == [10.0] * 4
    assert out["expected_ampm_asym"].abs().max() == pytest.approx(0.0, abs=0.001)


def test_residual_subtracts_geometry_from_measured_asymmetry(tmp_path):
    """``ampm_residual`` = asimetri terukur - asimetri yang dijelaskan geometri.

    Residual inilah sinyal diagnostiknya. Asimetri besar dengan residual ~0
    adalah geometri murni dan tidak perlu dikunjungi; residual besar barulah
    obstruksi nyata.
    """
    from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

    # Tiga string datar, satu miring 10 derajat ke timur -> median sin = 0.
    geom = _geom([("WB01-INV01", 1, 0.0), ("WB01-INV01", 2, 0.0),
                  ("WB01-INV01", 3, 0.0), ("WB01-INV01", 4, 10.0)])

    rep = build_intraday_diagnostic(
        _equinox_days(tmp_path), inverter_ids=["WB01-INV01"], string_geometry=geom,
    )
    row = rep.classification.set_index("pv_string").loc["WB01-INV01-PV4"]

    # 21 Maret, cross-slope 10 derajat: interpolasi tabel 9,2 -> 13,2 = ~0,517.
    assert row["expected_ampm_asym"] == pytest.approx(0.517, abs=0.005)
    # Keempat string identik daya -> asimetri terukur nol; sisanya tak terjelaskan.
    assert row["pagi"] == pytest.approx(row["sore"])
    assert row["ampm_residual"] == pytest.approx(-0.517, abs=0.005)


def test_string_without_geometry_row_gets_na_not_zero(tmp_path):
    """Tanpa baris geometri harus NA, bukan 0.

    Nilai 0 terbaca sebagai "tanahnya datar" padahal yang benar "tidak
    diketahui" -- WB01/WB02 memang tidak ada di string_geometry.csv, dan 62
    string WB03-10 dikosongkan karena fit bidangnya buruk.
    """
    from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

    geom = _geom([("WB01-INV01", 1, 8.0)])

    rep = build_intraday_diagnostic(
        _equinox_days(tmp_path), inverter_ids=["WB01-INV01"], string_geometry=geom,
    )
    row = rep.classification.set_index("pv_string").loc["WB01-INV01-PV4"]

    assert pd.isna(row["cross_slope_deg"])
    assert pd.isna(row["expected_ampm_asym"])
    assert pd.isna(row["ampm_residual"])


def test_label_found_at_two_positions_yields_na(tmp_path):
    """Label string yang muncul di dua tempat harus NA, bukan salah satunya.

    Delapan inverter punya label ganda di DXF dengan jarak median 308 m
    antar kemunculan. Memilih yang pertama berarti menebak lokasi sebuah
    string dalam radius ratusan meter lalu menyajikannya sebagai bukti.
    """
    from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

    geom = _geom([("WB01-INV01", 1, 8.0),
                  ("WB01-INV01", 4, 17.07), ("WB01-INV01", 4, 9.25)])

    rep = build_intraday_diagnostic(
        _equinox_days(tmp_path), inverter_ids=["WB01-INV01"], string_geometry=geom,
    )
    out = rep.classification.set_index("pv_string")

    assert pd.isna(out.loc["WB01-INV01-PV4", "cross_slope_deg"])
    assert pd.isna(out.loc["WB01-INV01-PV4", "ampm_residual"])
    assert out.loc["WB01-INV01-PV1", "cross_slope_deg"] == pytest.approx(8.0)


def test_geometry_is_evidence_only_and_never_corrects_the_ratio(tmp_path):
    """Menambahkan geometri TIDAK boleh menggeser rasio maupun kategori.

    Mengoreksi ``ratio`` memakai cross-slope butuh model POA per string per
    timestamp, bukan aritmetika -- alasan yang sama dengan penolakan koreksi
    voltage drop. Kolom geometri disajikan sebagai bukti di samping angka
    terukur, dan angka terukurnya harus tetap apa adanya.
    """
    from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

    paths = _equinox_days(tmp_path)
    geom = _geom([("WB01-INV01", 1, -20.0), ("WB01-INV01", 2, 0.0),
                  ("WB01-INV01", 3, 5.0), ("WB01-INV01", 4, 25.0)])

    polos = build_intraday_diagnostic(paths, inverter_ids=["WB01-INV01"])
    dengan = build_intraday_diagnostic(
        paths, inverter_ids=["WB01-INV01"], string_geometry=geom,
    )

    kolom = ["ratio_median", "deficit_pct", "ratio_range", "pagi", "sore",
             "kategori"]
    pd.testing.assert_frame_equal(
        polos.classification.set_index("pv_string")[kolom],
        dengan.classification.set_index("pv_string")[kolom],
    )


def test_geometry_evidence_attaches_to_a_table_that_predates_the_columns():
    """Workbook lama harus bisa dinilai ulang tanpa membaca ulang CSV baseline.

    Laporan 20 string yang sudah beredar lahir dari workbook Juni yang dibuat
    sebelum kolom ini ada. Yang berubah pada penilaian ulang hanyalah bukti
    geometrisnya -- ``pagi`` dan ``sore`` terukur tidak bergerak sedikit pun --
    jadi memaksa membaca ulang 700 MB CSV hanya untuk mendapat angka yang sama
    adalah pemborosan yang menunda koreksi laporan.
    """
    from pv_pipeline.string_intraday_diagnostic import attach_geometry_evidence

    lama = pd.DataFrame([   # kolom persis workbook Juni: tanpa vdrop, tanpa geometri
        {"pv_string": "WB05-INV03-PV1", "inverter_id": "WB05-INV03", "pv": "PV1",
         "pagi": 1.16, "sore": 0.64, "kategori": "SHADING_SORE"},
        {"pv_string": "WB05-INV03-PV17", "inverter_id": "WB05-INV03", "pv": "PV17",
         "pagi": 1.00, "sore": 1.00, "kategori": "UNIFORM"},
        {"pv_string": "WB05-INV03-PV18", "inverter_id": "WB05-INV03", "pv": "PV18",
         "pagi": 0.75, "sore": 0.77, "kategori": "UNIFORM"},
    ])
    geom = _geom([("WB05-INV03", 1, 12.0), ("WB05-INV03", 17, 0.0),
                  ("WB05-INV03", 18, 0.0)])

    out = attach_geometry_evidence(lama, geom, 166)   # pertengahan Juni

    # Kolom lama tetap di tempatnya, isinya tidak disentuh.
    assert list(out.columns)[:6] == list(lama.columns)
    pd.testing.assert_frame_equal(out[lama.columns], lama)

    row = out.set_index("pv_string").loc["WB05-INV03-PV1"]
    assert row["cross_slope_deg"] == pytest.approx(12.0)
    assert row["expected_ampm_asym"] == pytest.approx(0.548, abs=0.005)
    assert row["ampm_residual"] == pytest.approx(1.16 - 0.64 - 0.548, abs=0.005)


def test_geometry_evidence_leaves_columns_present_when_geometry_is_missing():
    """Tanpa berkas geometri, kolomnya tetap ada berisi NA.

    Sel penilaian di notebook membaca ketiga kolom itu tanpa syarat; hilangnya
    kolom akan menggagalkan seluruh sel, bukan sekadar mengosongkan satu bukti.
    """
    from pv_pipeline.string_intraday_diagnostic import attach_geometry_evidence

    lama = pd.DataFrame([
        {"pv_string": "WB05-INV03-PV1", "inverter_id": "WB05-INV03", "pv": "PV1",
         "pagi": 1.16, "sore": 0.64, "kategori": "SHADING_SORE"},
    ])

    out = attach_geometry_evidence(lama, None, 166)

    assert out["cross_slope_deg"].isna().all()
    assert out["expected_ampm_asym"].isna().all()
    assert out["ampm_residual"].isna().all()


# --- pembeda musiman: geometri vs obstruksi ------------------------------------


def _klas(rows):
    """DataFrame klasifikasi minimal: pv_string + pagi + sore."""
    return pd.DataFrame(
        [{"pv_string": k, "pagi": a, "sore": b} for k, a, b in rows]
    )


def test_seasonal_calls_stable_asymmetry_geometric():
    """Asimetri dari kemiringan tanah bertanda TETAP dan besarnya hanya
    bergeser ~22,5% dari nilainya sendiri antar solstis.

    Angka 22,5% itu turunan geometri surya, bukan hasil penyetelan: untuk
    cross-slope 2,5 sampai 18,3 derajat rentang relatifnya 22,4-22,7%.
    """
    from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

    out = seasonal_discriminator({
        "jun": _klas([("WB05-INV03-PV1", 0.70, 1.15)]),   # asimetri -0,45
        "des": _klas([("WB05-INV03-PV1", 0.68, 1.18)]),   # asimetri -0,50
    }).set_index("pv_string")

    assert out.loc["WB05-INV03-PV1", "verdikt"] == "GEOMETRI"


def test_seasonal_calls_sign_flip_obstruction():
    """Kemiringan tanah tidak pernah membalik tanda sepanjang tahun.

    Bayangan pohon bisa: yang menaungi pagi di Juni bisa menaungi sore di
    Desember karena deklinasi bergeser. Pembalikan tanda karena itu bukti
    kuat obstruksi, bukan geometri.
    """
    from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

    out = seasonal_discriminator({
        "jun": _klas([("WB09-INV20-PV1", 0.70, 1.10)]),   # -0,40
        "des": _klas([("WB09-INV20-PV1", 1.12, 0.72)]),   # +0,40
    }).set_index("pv_string")

    assert out.loc["WB09-INV20-PV1", "verdikt"] == "OBSTRUKSI"


def test_seasonal_calls_large_magnitude_swing_obstruction():
    """Tanda tetap tapi besarnya melonjak jauh di atas 22,5% -> bukan geometri."""
    from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

    out = seasonal_discriminator({
        "jun": _klas([("WB03-INV09-PV7", 0.95, 1.05)]),   # -0,10
        "des": _klas([("WB03-INV09-PV7", 0.45, 1.15)]),   # -0,70
    }).set_index("pv_string")

    assert out.loc["WB03-INV09-PV7", "verdikt"] == "OBSTRUKSI"


def test_seasonal_ignores_strings_without_asymmetry():
    """String simetris tidak punya apa pun untuk dibedakan."""
    from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

    out = seasonal_discriminator({
        "jun": _klas([("WB07-INV04-PV20", 0.86, 0.88)]),
        "des": _klas([("WB07-INV04-PV20", 0.87, 0.86)]),
    }).set_index("pv_string")

    assert out.loc["WB07-INV04-PV20", "verdikt"] == "TANPA_ASIMETRI"


def test_seasonal_needs_two_seasons_per_string():
    """Satu musim tidak bisa membedakan apa pun -- jangan menebak."""
    from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

    out = seasonal_discriminator({
        "jun": _klas([("WB08-INV15-PV20", 0.70, 1.15)]),
        "des": _klas([("WB10-INV03-PV24", 0.70, 1.15)]),
    }).set_index("pv_string")

    assert out.loc["WB08-INV15-PV20", "verdikt"] == "DATA_KURANG"
    assert out.loc["WB10-INV03-PV24", "verdikt"] == "DATA_KURANG"


def test_seasonal_threshold_clears_the_geometric_drift_it_must_tolerate():
    """Ambang harus di ATAS 22,5% supaya geometri tidak salah dicap obstruksi,
    dan tidak jauh di atasnya supaya obstruksi ringan tetap tertangkap."""
    from pv_pipeline.string_intraday_diagnostic import SEASONAL_REL_RANGE_MAX

    assert 0.225 < SEASONAL_REL_RANGE_MAX <= 0.40


# --- vonis satu musim tidak boleh membebaskan string --------------------------


def _baris(kategori="SHADING_PAGI", dropout=0.0, cs=-15.7, residual=-0.048):
    return {"kategori": kategori, "dropout_share_pct": dropout,
            "cross_slope_deg": cs, "ampm_residual": residual}


def test_one_season_never_clears_a_string():
    """Residual kecil pada SATU musim hanya melahirkan CALON, bukan vonis.

    Ini bukan kehati-hatian abstrak: WB08-INV15-PV20 pernah dicoret dari daftar
    kunjungan atas dasar residual satu musim (-0,048 di Juni), lalu pengukuran
    November-Desember membantahnya -- asimetrinya MENYUSUT 33% padahal geometri
    menuntut TUMBUH 21%. Satu musim tidak bisa membedakan kemiringan tanah dari
    objek yang kebetulan berbayang ke arah yang sama pada bulan itu.

    Beban pembuktian jatuh pada klaim yang MENGHAPUS tindakan pengaman. Karena
    itu 'geometri' harus lolos dua musim, sementara 'obstruksi' -- yang justru
    mengirim orang untuk melihat -- boleh berdiri dari satu musim.
    """
    from pv_pipeline.string_intraday_diagnostic import provisional_direction_verdict

    assert provisional_direction_verdict(_baris(residual=-0.048)) == "CALON_GEOMETRI"
    assert provisional_direction_verdict(_baris(residual=-0.400)) == "OBSTRUKSI"


def test_verdict_follows_the_branch_that_produced_the_label():
    """Urutannya mengikuti prioritas cabang di classify_strings.

    Cross-slope tidak bisa membuat string berhenti produksi, dan tidak bisa
    membuatnya melampaui tetangga. Label yang lahir dari kedua cabang itu
    berada di luar wewenang uji geometris, berapa pun residualnya.
    """
    from pv_pipeline.string_intraday_diagnostic import provisional_direction_verdict

    assert provisional_direction_verdict(
        _baris(kategori="SHADING_PULIH", residual=-0.001)) == "TIDAK_BERLAKU"
    assert provisional_direction_verdict(
        _baris(dropout=86.0, residual=-0.001)) == "MATI_DINI"


def test_missing_cross_slope_is_reported_not_assumed_flat():
    """Tanpa cross-slope tepercaya tidak ada prediksi untuk diuji."""
    from pv_pipeline.string_intraday_diagnostic import provisional_direction_verdict

    assert provisional_direction_verdict(
        _baris(cs=float("nan"))) == "DATA_GEOMETRI_TIDAK_ADA"


# --- pembeda musiman sebagai VALIDATOR prediksi geometris ---------------------


def _musim(rows):
    """Klasifikasi satu musim: pv_string + pagi + sore + ampm_residual."""
    return pd.DataFrame(
        [{"pv_string": k, "pagi": a, "sore": b, "ampm_residual": r}
         for k, a, b, r in rows]
    )


def test_validator_agrees_when_geometry_explains_a_stable_asymmetry():
    """Dua metode independen sepakat -> prediksi geometrisnya tervalidasi.

    ``seasonal_discriminator`` menilai dari data terukur saja dan buta
    terhadap cross-slope; ``ampm_residual`` menilai dari prediksi geometri.
    Kesepakatan keduanya bukan tautologi -- model k(hari)*sin(cross_slope)
    diturunkan dari pvlib clear-sky, tidak pernah dipaskan ke telemetri.

    ``residual_drift`` menguji bagian yang paling mudah salah dalam model:
    penskalaan musimannya. Asimetri mentah string ini bergeser 0,05 antar
    musim; kalau k(hari) benar, residualnya bergeser jauh lebih kecil.
    """
    from pv_pipeline.string_intraday_diagnostic import validate_geometry_seasonally

    out = validate_geometry_seasonally({
        "jun": _musim([("WB05-INV03-PV1", 0.70, 1.15, -0.02)]),   # asym -0,45
        "des": _musim([("WB05-INV03-PV1", 0.68, 1.18, -0.03)]),   # asym -0,50
    }).set_index("pv_string")
    row = out.loc["WB05-INV03-PV1"]

    assert row["verdikt_musiman"] == "GEOMETRI"
    assert row["verdikt_geometris"] == "GEOMETRI"
    assert row["hasil"] == "SEPAKAT"
    assert row["residual_drift"] == pytest.approx(0.01, abs=1e-9)
    assert row["residual_drift"] < 0.05      # < drift asimetri mentahnya


def test_validator_catches_the_obstruction_the_seasonal_test_calls_geometry():
    """Inti nilai validator ini: menangkap kesalahan yang dulu tak terlihat.

    Bangunan permanen di sisi timur menghasilkan asimetri bertanda TETAP
    yang bergeser pelan sepanjang tahun -- persis tanda tangan yang dipakai
    ``seasonal_discriminator`` untuk menyimpulkan GEOMETRI. Sebelum ada
    koordinat per string, tidak ada cara membedakannya.

    String ini duduk di tanah DATAR, jadi geometri tidak menjelaskan apa pun
    dan residualnya sama besar dengan asimetri mentahnya. Regu lapangan
    harus tetap dikirim.
    """
    from pv_pipeline.string_intraday_diagnostic import validate_geometry_seasonally

    out = validate_geometry_seasonally({
        "jun": _musim([("WB09-INV11-PV3", 0.70, 1.15, -0.45)]),
        "des": _musim([("WB09-INV11-PV3", 0.68, 1.18, -0.50)]),
    }).set_index("pv_string")
    row = out.loc["WB09-INV11-PV3"]

    assert row["verdikt_musiman"] == "GEOMETRI"      # metode lama tertipu
    assert row["verdikt_geometris"] == "OBSTRUKSI"   # residual membongkarnya
    assert row["hasil"] == "MUSIMAN_TERLALU_LONGGAR"


def test_validator_agrees_when_the_sign_flips():
    """Tanda yang berbalik tidak mungkin geometri, dan residual sepakat.

    Kemiringan tanah tidak pernah membalik arah sepanjang tahun. Kedua
    metode harus menyebut ini obstruksi tanpa saling bergantung.
    """
    from pv_pipeline.string_intraday_diagnostic import validate_geometry_seasonally

    out = validate_geometry_seasonally({
        "jun": _musim([("WB04-INV17-PV5", 1.10, 0.80, 0.28)]),    # asym +0,30
        "des": _musim([("WB04-INV17-PV5", 0.80, 1.15, -0.37)]),   # asym -0,35
    }).set_index("pv_string")
    row = out.loc["WB04-INV17-PV5"]

    assert row["verdikt_musiman"] == "OBSTRUKSI"
    assert row["verdikt_geometris"] == "OBSTRUKSI"
    assert row["hasil"] == "SEPAKAT"


def test_validator_reports_a_seasonal_call_it_cannot_check():
    """Tanpa cross-slope tepercaya, validator harus DIAM -- bukan menyetujui.

    Kesepakatan palsu di sini akan terbaca seolah prediksi geometris sudah
    diuji untuk string ini, padahal tidak ada prediksi sama sekali.
    """
    from pv_pipeline.string_intraday_diagnostic import validate_geometry_seasonally

    nan = float("nan")
    out = validate_geometry_seasonally({
        "jun": _musim([("WB04-INV17-PV1", 0.70, 1.15, nan)]),
        "des": _musim([("WB04-INV17-PV1", 0.68, 1.18, nan)]),
    }).set_index("pv_string")
    row = out.loc["WB04-INV17-PV1"]

    assert row["verdikt_musiman"] == "GEOMETRI"
    assert row["verdikt_geometris"] == "DATA_GEOMETRI_TIDAK_ADA"
    assert row["hasil"] == "TIDAK_BERLAKU"
    assert pd.isna(row["residual_drift"])


# ---------------------------------------------------------------------------
# Putusan uji hujan -- median, bukan rata-rata
# ---------------------------------------------------------------------------

def _hujan(baris):
    """DataFrame Uji_Hujan sintetis: (pv_string, delta_pp) -> satu kejadian."""
    return pd.DataFrame(
        [{"pv_string": s, "event": "hujan uji",
          "ratio_before": 0.80, "ratio_after": 0.80 + d / 100.0,
          "delta_pp": d}
         for s, d in baris],
        columns=["pv_string", "event", "ratio_before", "ratio_after", "delta_pp"],
    )


class TestRainRecoveryVerdict:
    """Sebaran pemulihan hujan menjulur; rata-rata bukan alat yang tepat.

    Run 14 Agustus mengumumkan "komponen soiling nyata" atas rata-rata +1,21 pp
    padahal median tiap kelompok NEGATIF, dan 5 dari 8 string yang pulih ada di
    satu inverter. Tindakan yang benar adalah membersihkan satu inverter;
    kesimpulan yang tercetak mengarahkan ke pembersihan se-situs.
    """

    def test_ekor_panjang_tidak_boleh_jadi_vonis_se_situs(self):
        """Median ~0 dengan rata-rata positif = pemulihan LOKAL, bukan menyeluruh.

        Ini bentuk data yang sebenarnya terjadi. Kalau vonisnya masih memakai
        rata-rata, regresi yang sama kembali tanpa ada yang menyadarinya.
        """
        from pv_pipeline.string_intraday_diagnostic import rain_recovery_verdict

        tenang = [(f"WB05-INV{i:02d}-PV1", 0.0) for i in range(1, 21)]
        pulih = [(f"WB07-INV08-PV{n}", 10.0) for n in (2, 3, 13, 14, 18)]
        rain = _hujan(tenang + pulih)

        hasil = rain_recovery_verdict(rain, [s for s, _ in tenang + pulih])

        assert hasil["mean_pp"] > 1.0, "rata-ratanya memang tertarik ekor"
        assert hasil["median_pp"] < 1.0
        assert hasil["putusan"] == "SOILING_LOKAL"

    def test_inverter_dominan_disebut_namanya(self):
        """Bagian dominan dihitung atas PEMULIH, bukan atas semua kandidat.

        5 dari 8 pemulih di satu inverter itu angka yang menggerakkan kerja
        lapangan. 5 dari 42 kandidat tidak berarti apa-apa.
        """
        from pv_pipeline.string_intraday_diagnostic import rain_recovery_verdict

        rain = _hujan(
            [(f"WB05-INV{i:02d}-PV1", 0.0) for i in range(1, 21)]
            + [(f"WB07-INV08-PV{n}", 10.0) for n in (2, 3, 13, 14, 18)]
            + [("WB09-INV20-PV1", 9.0), ("WB03-INV06-PV2", 8.0),
               ("WB04-INV02-PV3", 7.0)]
        )
        hasil = rain_recovery_verdict(rain, list(rain["pv_string"]))

        assert hasil["n_pulih"] == 8
        assert hasil["inverter_dominan"] == "WB07-INV08"
        assert hasil["bagian_dominan"] == pytest.approx(5 / 8)

    def test_pemulihan_merata_tetap_divonis_menyeluruh(self):
        """Kalau kandidat KHAS memang pulih, vonis se-situs itu benar.

        Penjaga arah sebaliknya: median yang dipakai tidak boleh membuat
        soiling sungguhan jadi tak terdeteksi.
        """
        from pv_pipeline.string_intraday_diagnostic import rain_recovery_verdict

        rain = _hujan([(f"WB0{i%7+3}-INV{i:02d}-PV1", 5.0) for i in range(1, 21)])
        hasil = rain_recovery_verdict(rain, list(rain["pv_string"]))

        assert hasil["median_pp"] == pytest.approx(5.0)
        assert hasil["putusan"] == "SOILING_MENYELURUH"

    def test_tanpa_pemulihan_sama_sekali_bukan_debu(self):
        """Nol pemulih -> defisitnya bukan debu, dan cleaning tidak menolong."""
        from pv_pipeline.string_intraday_diagnostic import rain_recovery_verdict

        rain = _hujan([(f"WB05-INV{i:02d}-PV1", -0.2) for i in range(1, 21)])
        hasil = rain_recovery_verdict(rain, list(rain["pv_string"]))

        assert hasil["n_pulih"] == 0
        assert hasil["inverter_dominan"] is None
        assert hasil["putusan"] == "TIDAK_PULIH"


# ---------------------------------------------------------------------------
# Musim ketiga -- kenapa dua titik tidak cukup
# ---------------------------------------------------------------------------

class TestTigaMusim:
    """Dua musim hanya memberi SATU selisih, dan satu selisih selalu "konsisten".

    ``SEASONAL_REL_RANGE_MAX`` membandingkan rentang asimetri terhadap
    besarnya. Dengan dua titik, rentang itu hanyalah jarak antar keduanya --
    dua nilai apa pun yang berdekatan lolos, termasuk milik obstruksi yang
    kebetulan bergeser pelan antara Juni dan Desember. Titik KETIGA di tengah
    tahunlah yang bisa membantahnya, karena geometri menuntut asimetri
    bergerak TERATUR sepanjang tahun, bukan sekadar mirip di dua ujung.

    Dukungan N-musim sudah ada di ``seasonal_discriminator`` sejak awal tapi
    tidak pernah diuji. Kelas ini menguncinya sebelum run Maret 2026
    bergantung padanya.
    """

    def test_musim_tengah_bisa_membatalkan_vonis_geometri_dua_musim(self):
        """Sepasang ujung yang rapi bisa menyembunyikan obstruksi.

        Juni −0,45 dan Desember −0,50 lolos sebagai GEOMETRI. Tambahkan Maret
        di −0,30 dan rentang relatifnya melompat ke 0,48: asimetrinya tidak
        bergerak teratur, jadi bukan kemiringan tanah. Tanpa titik ketiga,
        string ini akan dibebaskan dari daftar kunjungan secara keliru.
        """
        from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

        dua = seasonal_discriminator({
            "jun": _klas([("X", 0.70, 1.15)]),
            "des": _klas([("X", 0.68, 1.18)]),
        }).set_index("pv_string")
        assert dua.loc["X", "verdikt"] == "GEOMETRI"

        tiga = seasonal_discriminator({
            "jun": _klas([("X", 0.70, 1.15)]),
            "mar": _klas([("X", 0.85, 1.15)]),
            "des": _klas([("X", 0.68, 1.18)]),
        }).set_index("pv_string")

        assert tiga.loc["X", "n_musim"] == 3
        assert tiga.loc["X", "verdikt"] == "OBSTRUKSI"

    def test_musim_tengah_yang_menginterpolasi_menegaskan_geometri(self):
        """Penjaga arah sebaliknya: titik ketiga tidak boleh menolak semuanya.

        Kalau Maret jatuh di antara kedua ekstrem seperti yang dituntut
        geometri, vonisnya harus tetap GEOMETRI -- kalau tidak, menambah musim
        cuma akan mengubur setiap string di bawah label OBSTRUKSI.
        """
        from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

        tiga = seasonal_discriminator({
            "jun": _klas([("X", 0.70, 1.15)]),
            "mar": _klas([("X", 0.6875, 1.1625)]),
            "des": _klas([("X", 0.68, 1.18)]),
        }).set_index("pv_string")

        assert tiga.loc["X", "verdikt"] == "GEOMETRI"
        assert tiga.loc["X", "asym_rel_range"] < 0.30

    def test_tiap_musim_dapat_kolomnya_sendiri(self):
        """Nilai per musim harus terbaca, bukan hanya ringkasannya.

        Tanpa kolom per musim, pembaca tidak bisa melihat musim MANA yang
        menyimpang -- dan itu satu-satunya cara menilai apakah vonis OBSTRUKSI
        masuk akal atau produk satu musim yang datanya buruk.
        """
        from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

        out = seasonal_discriminator({
            "jun": _klas([("X", 0.70, 1.15)]),
            "mar": _klas([("X", 0.85, 1.15)]),
            "des": _klas([("X", 0.68, 1.18)]),
        })

        for label in ("asym_jun", "asym_mar", "asym_des"):
            assert label in out.columns, label

    def test_string_yang_absen_di_satu_musim_terlihat_dari_n_musim(self):
        """Komposisi berbeda antar musim tidak boleh lolos tanpa jejak.

        Phase One absen dari sebagian hari karena putus fiber IconPlus, jadi
        string yang tidak hadir di semua musim itu keadaan nyata, bukan
        hipotetis. Ia dinilai atas lebih sedikit titik, dan ``n_musim`` adalah
        satu-satunya tempat hal itu terbaca.
        """
        from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

        out = seasonal_discriminator({
            "jun": _klas([("X", 0.70, 1.15), ("Y", 0.70, 1.15)]),
            "mar": _klas([("X", 0.69, 1.16)]),
            "des": _klas([("X", 0.68, 1.18), ("Y", 0.68, 1.18)]),
        }).set_index("pv_string")

        assert out.loc["X", "n_musim"] == 3
        assert out.loc["Y", "n_musim"] == 2


# ---------------------------------------------------------------------------
# Normalisasi musiman -- membuang faktor bersama sebelum menilai
# ---------------------------------------------------------------------------

class TestNormalisasiMusiman:
    """Uji musiman ikut mengukur cuaca, bukan hanya geometri.

    Diukur pada run tiga musim: |asimetri| median se-situs Juni 0,162,
    Nov-Des 0,095, Maret 0,083 -- selisih sekitar 50%. Ambang
    ``SEASONAL_REL_RANGE_MAX`` = 0,30 diturunkan dari variasi GEOMETRIS antar
    solstis yang cuma ~22,5%. Jadi suku sebaran didominasi faktor musiman
    bersama yang tidak ada hubungannya dengan string mana pun, dan 95% string
    melewati ambang -- vonis OBSTRUKSI jadi hampir otomatis.

    Faktornya multiplikatif (rasio Nov/Jun median 0,53, Mar/Jun 0,46, keduanya
    rapat), jadi obatnya membagi tiap musim dengan skalanya sendiri. Skalanya
    diambil dari string LAIN pada musim itu -- data terukur, bukan model --
    sehingga uji musiman tetap BEBAS dari prediksi geometris dan kesepakatan
    keduanya tetap validasi, bukan tautologi.
    """

    def _kohort(self, faktor, n=40, penyimpang=None):
        """``n`` string berasimetri tetap, dikalikan ``faktor`` di musim ini.

        ``penyimpang``: {pv_string: faktor sendiri} untuk string yang TIDAK
        mengikuti pola bersama.
        """
        baris = []
        for i in range(n):
            nama = f"WB05-INV{i // 10 + 1:02d}-PV{i % 10 + 1}"
            f = (penyimpang or {}).get(nama, faktor)
            asym = (0.20 + 0.01 * i) * f
            baris.append((nama, 1.0 + asym / 2, 1.0 - asym / 2))
        return _klas(baris)

    def test_faktor_musiman_bersama_tidak_lagi_memicu_obstruksi(self):
        """Seluruh situs meredup bersamaan bukan bukti apa pun tentang string.

        Tanpa normalisasi, penyusutan seragam 50% memberi rentang relatif jauh
        di atas 0,30 dan SETIAP string divonis OBSTRUKSI -- termasuk yang
        asimetrinya sempurna mengikuti pola bersama.
        """
        from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

        musim = {"jun": self._kohort(1.0), "mar": self._kohort(0.5)}

        mentah = seasonal_discriminator(musim, season_scale=None)
        ternormal = seasonal_discriminator(musim, season_scale="auto")

        assert (mentah["verdikt"] == "OBSTRUKSI").all(), "prakondisi uji"
        assert (ternormal["verdikt"] == "GEOMETRI").all(), (
            ternormal["verdikt"].value_counts().to_dict()
        )

    def test_string_yang_menyimpang_dari_pola_bersama_tetap_tertangkap(self):
        """Normalisasi tidak boleh membutakan ujinya.

        Penjaga arah sebaliknya: kalau membuang faktor bersama juga membuang
        sinyal per-string, ujinya berhenti berguna dan setiap string lolos
        sebagai GEOMETRI.
        """
        from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

        nakal = "WB05-INV01-PV1"
        musim = {
            "jun": self._kohort(1.0),
            "mar": self._kohort(0.5, penyimpang={nakal: 0.1}),
        }

        out = seasonal_discriminator(musim, season_scale="auto").set_index("pv_string")

        assert out.loc[nakal, "verdikt"] == "OBSTRUKSI"
        assert (out.drop(index=nakal)["verdikt"] == "GEOMETRI").all()

    def test_kohort_terlalu_kecil_tidak_menormalkan_dan_menyalak(self):
        """Diam-diam kembali ke metode terkonfound adalah cara cacat ini bertahan.

        Dengan satu-dua string, skala musim sama dengan nilai string itu
        sendiri: normalisasi jadi tak berarti dan setiap string mendarat di
        1,0. Fungsi harus menolak menormalkan DAN mengatakannya.
        """
        from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

        musim = {"jun": _klas([("X", 1.10, 0.90)]),
                 "mar": _klas([("X", 1.05, 0.95)])}

        with pytest.warns(UserWarning, match="normalisasi"):
            out = seasonal_discriminator(musim, season_scale="auto")

        assert not out["ternormalisasi"].any()

    def test_skala_dihitung_dari_kohort_yang_sama_di_tiap_musim(self):
        """Memilih string per musim membuat skalanya bias.

        Kalau tiap musim memakai himpunan stringnya sendiri (mis. yang
        melewati ambang di musim itu), musim redup akan kehilangan string
        lemahnya lebih dulu sehingga skalanya justru naik -- persis kebalikan
        dari yang harus dikoreksi.
        """
        from pv_pipeline.string_intraday_diagnostic import season_scales

        musim = {"jun": self._kohort(1.0), "mar": self._kohort(0.5)}
        skala = season_scales(musim)

        assert skala["mar"] == pytest.approx(skala["jun"] * 0.5, rel=1e-6)

    def test_kolom_ternormalisasi_melaporkan_apa_yang_terjadi(self):
        """Pembaca membandingkan workbook lama dan baru; keadaannya harus terbaca."""
        from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

        musim = {"jun": self._kohort(1.0), "mar": self._kohort(0.5)}

        assert seasonal_discriminator(musim, season_scale="auto")["ternormalisasi"].all()
        assert not seasonal_discriminator(musim, season_scale=None)["ternormalisasi"].any()


class TestAmbangTernormalisasiTurunanNol:
    """Setelah normalisasi, geometri memprediksi rentang relatif NOL.

    Asimetri berbentuk ``k(doy) * sin(cross_slope)``, dan cahaya difus
    meredamnya dengan faktor ``D`` yang juga se-musim. Skala musim adalah
    median |asimetri| atas kohort, yaitu ``k * D * median|sin theta|``. Maka

        z = asym / skala = sin(theta) / median|sin theta|

    ``k`` DAN ``D`` dua-duanya tercoret, dan z tidak lagi bergantung musim.

    Konsekuensinya menentukan: angka 22,5% yang mendasari
    ``SEASONAL_REL_RANGE_MAX`` = 0,30 adalah drift geometris antar solstis, dan
    drift itu HABIS dikonsumsi normalisasi. Tidak ada komponen geometris
    tersisa untuk menurunkan ambang -- sisanya murni kelonggaran derau.

    Arah risikonya perlu disadari: ambang TINGGI menghasilkan lebih banyak
    GEOMETRI, karenanya lebih banyak pembebasan dari daftar kunjungan, dan
    itulah arah yang berbahaya. Terhadap prediksi nol, 0,30 sudah longgar.
    Menaikkannya menuntut estimasi derau empiris yang belum ada.
    """

    def _musim(self, k, damping, sudut, n=40):
        """Satu musim: asimetri = k * D * sin(theta) untuk tiap string."""
        import math
        baris = []
        for i, th in enumerate(sudut):
            asym = k * damping * math.sin(math.radians(th))
            baris.append((f"WB05-INV{i // 10 + 1:02d}-PV{i % 10 + 1}",
                          1.0 + asym / 2, 1.0 - asym / 2))
        return _klas(baris)

    def _sudut(self, n=40):
        return [2.5 + i * 0.7 for i in range(n)]

    def test_string_geometris_murni_memberi_rentang_relatif_nol(self):
        """Musim boleh berbeda k DAN berbeda redaman; z tetap sama.

        Ini penurunannya, dijalankan. Kalau suatu saat normalisasi berhenti
        mencoret kedua faktor itu, tes ini gagal dan klaim "prediksinya nol"
        ikut gugur bersamanya.
        """
        from pv_pipeline.string_intraday_diagnostic import seasonal_discriminator

        sudut = self._sudut()
        musim = {
            "jun": self._musim(1.00, 1.00, sudut),
            "mar": self._musim(0.85, 0.52, sudut),    # k dan D beda jauh
            "des": self._musim(1.22, 0.58, sudut),
        }

        out = seasonal_discriminator(musim)

        assert out["ternormalisasi"].all()
        assert out["asym_rel_range"].max() < 1e-6, (
            out["asym_rel_range"].describe().to_dict()
        )
        # Sudut kecil benar mendapat TANPA_ASIMETRI: ambang 0,12 diperiksa pada
        # nilai TERUKUR, dan sin(2,5 derajat) memang di bawahnya. Yang penting
        # tidak ada satu pun yang divonis OBSTRUKSI.
        assert set(out["verdikt"]) <= {"GEOMETRI", "TANPA_ASIMETRI"}, (
            out["verdikt"].value_counts().to_dict()
        )
        assert (out["verdikt"] == "GEOMETRI").sum() > 0

    def test_ambang_yang_berlaku_kini_kelonggaran_derau_bukan_drift_geometris(self):
        """Nilainya boleh tetap 0,30, tapi PEMBENARANNYA berbeda.

        Dibiarkan tanpa catatan, angka itu terbaca seolah masih diturunkan dari
        22,5% drift antar solstis. Ia tidak. Tes ini menahan nilainya di tempat
        supaya kenaikan -- satu-satunya arah yang menciptakan pembebasan baru --
        tidak bisa terjadi tanpa seseorang menyentuh berkas ini.
        """
        from pv_pipeline.string_intraday_diagnostic import SEASONAL_REL_RANGE_MAX

        assert SEASONAL_REL_RANGE_MAX == 0.30


class TestKonsentrasiButuhCukupPemulih:
    """"50% dari yang pulih" tidak berarti apa-apa kalau pemulihnya dua.

    Run Maret mencetak: "2 dari 28 kandidat pulih... Terkumpul di WB05-INV03:
    50% dari yang pulih. TINDAKAN: bersihkan WB05-INV03." Lima puluh persen dari
    dua adalah SATU string, dan itu instruksi kerja lapangan yang lahir dari
    satu titik data.

    Bandingkan Nov-Des: 5 dari 8 di WB07-INV08. Bagian yang sama-sama di atas
    separuh, tapi yang satu bermakna dan yang lain tidak. Yang membedakan bukan
    bagiannya melainkan JUMLAHNYA, jadi ambang bagian saja tidak cukup.
    """

    def _hujan(self, baris):
        return pd.DataFrame(
            [{"pv_string": s, "event": "uji", "ratio_before": 0.8,
              "ratio_after": 0.8 + d / 100.0, "delta_pp": d}
             for s, d in baris],
            columns=["pv_string", "event", "ratio_before", "ratio_after",
                     "delta_pp"],
        )

    def test_dua_pemulih_tidak_cukup_untuk_menamai_inverter(self):
        """Satu string bukan pola; jangan kirim orang atas dasar itu."""
        from pv_pipeline.string_intraday_diagnostic import rain_recovery_verdict

        rain = self._hujan(
            [(f"WB05-INV{i:02d}-PV1", 0.0) for i in range(1, 27)]
            + [("WB05-INV03-PV4", 9.0), ("WB09-INV20-PV1", 8.0)]
        )
        hasil = rain_recovery_verdict(rain, list(rain["pv_string"]))

        assert hasil["n_pulih"] == 2
        assert hasil["bagian_dominan"] == pytest.approx(0.5)
        assert not hasil["terkonsentrasi"], (
            "bagian 50% dari dua pemulih bukan konsentrasi"
        )

    def test_pemulih_cukup_banyak_tetap_menamai_inverter(self):
        """Penjaga arah sebaliknya: pola nyata harus tetap tersebut.

        Bentuk data Nov-Des: 5 dari 8 pemulih di satu inverter. Kalau syarat
        jumlah membuat kasus ini ikut diam, temuan WB07-INV08 yang sudah
        menggerakkan uji cuci akan hilang.
        """
        from pv_pipeline.string_intraday_diagnostic import rain_recovery_verdict

        rain = self._hujan(
            [(f"WB05-INV{i:02d}-PV1", 0.0) for i in range(1, 35)]
            + [(f"WB07-INV08-PV{n}", 9.0) for n in (2, 3, 13, 14, 18)]
            + [("WB09-INV20-PV1", 8.0), ("WB03-INV06-PV2", 7.0),
               ("WB04-INV02-PV3", 6.0)]
        )
        hasil = rain_recovery_verdict(rain, list(rain["pv_string"]))

        assert hasil["n_pulih"] == 8
        assert hasil["inverter_dominan"] == "WB07-INV08"
        assert hasil["terkonsentrasi"]

    def test_ambang_jumlah_pemulih_terpasang_sebagai_konstanta(self):
        """Angkanya konvensi; menguburnya membuat orang mengira ia diturunkan."""
        from pv_pipeline.string_intraday_diagnostic import (
            DEFAULT_RAIN_MIN_RECOVERERS,
        )

        assert DEFAULT_RAIN_MIN_RECOVERERS >= 3
