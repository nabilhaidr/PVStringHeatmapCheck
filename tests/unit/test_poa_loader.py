"""Test pv_pipeline.poa.loader: PyranometerLoader (xlsx parser + WB->WS mapping)."""
from __future__ import annotations

import pandas as pd
import pytest

from pv_pipeline.poa.loader import PyranometerLoader


WS_TO_WB_MAP = {
    "WS-1": ["WB08", "WB09", "WB10"],
    "WS-2": ["WB05", "WB07"],
    "WS-3": ["WB06"],
    "WS-4": ["WB03", "WB04"],
    "WS-5": ["WB01", "WB02"],
}


# ---------- Constructor + xlsx parsing ----------


def test_loader_parses_synthetic_xlsx(synthetic_pyranometer_xlsx):
    """Load synthetic xlsx -> df dengan WS-1..5 + avg columns."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    assert not loader.df.empty
    for col in ["WS-1", "WS-2", "WS-3", "WS-4", "WS-5", "avg"]:
        assert col in loader.df.columns
    # Synthetic: 288 timestamps (1 day @ 5-min)
    assert len(loader.df) == 288


def test_loader_naive_datetime_index(synthetic_pyranometer_xlsx):
    """Loader strip tz dari index untuk konsisten dengan naive WITA convention."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    assert loader.df.index.tz is None


def test_loader_wb_to_ws_reverse_mapping(synthetic_pyranometer_xlsx):
    """Reverse map WB -> WS resolved dari ws_to_wb."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    assert loader.wb_to_ws["WB01"] == "WS-5"
    assert loader.wb_to_ws["WB05"] == "WS-2"
    assert loader.wb_to_ws["WB10"] == "WS-1"


# ---------- get_per_ws ----------


def test_get_per_ws_returns_series_for_mapped_wb(synthetic_pyranometer_xlsx):
    """WB01 -> WS-5, return Series untuk WS-5 column."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    ts = pd.date_range("2026-05-14 11:00", "2026-05-14 13:00", freq="1h")
    poa = loader.get_per_ws(ts, "WB01")
    assert isinstance(poa, pd.Series)
    assert len(poa) == 3
    # Noon should have non-zero POA
    assert poa.loc["2026-05-14 12:00:00"] > 500.0


def test_get_per_ws_unmapped_wb_returns_nan(synthetic_pyranometer_xlsx):
    """WB tidak ada di mapping -> all-NaN Series + warning."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    ts = pd.date_range("2026-05-14 12:00", periods=1, freq="1h")
    poa = loader.get_per_ws(ts, "WB99")  # tidak ada di mapping
    assert poa.isna().all()


def test_get_per_ws_handles_ws2_nan_gap(synthetic_pyranometer_xlsx):
    """WS-2 sintetik punya NaN gap di 08-14. WB05->WS-2.

    Wave 11 hotfix #4: default `fallback_to_avg=True` mengisi NaN dari avg.
    Untuk verify strict-NaN behavior, opt-out via fallback_to_avg=False.
    """
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    # Strict mode (no fallback): WS-2 NaN at noon -> NaN.
    poa_wb05_strict = loader.get_per_ws(ts, "WB05", fallback_to_avg=False)
    assert pd.isna(poa_wb05_strict.iloc[0])
    # Default mode (fallback ON): WS-2 NaN at noon -> filled from avg, not NaN.
    poa_wb05_default = loader.get_per_ws(ts, "WB05")
    assert not pd.isna(poa_wb05_default.iloc[0])
    # Sanity: WB01 (WS-5) at same noon should NOT be NaN regardless of mode.
    poa_wb01 = loader.get_per_ws(ts, "WB01")
    assert not pd.isna(poa_wb01.iloc[0])


# ---------- get_avg ----------


def test_get_avg_returns_avg_column(synthetic_pyranometer_xlsx):
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    avg = loader.get_avg(ts)
    assert avg.iloc[0] > 0.0


# ---------- Reindex tolerance ----------


def test_reindex_nearest_with_tolerance(synthetic_pyranometer_xlsx):
    """Source 5-min, query off-grid -> nearest match dalam 2-min tolerance."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    # Query 12:02 (off-grid, nearest = 12:00 atau 12:05)
    ts = pd.DatetimeIndex(["2026-05-14 12:02:00"])
    poa = loader.get_per_ws(ts, "WB01")
    assert not pd.isna(poa.iloc[0]), "12:02 harus match nearest 12:00 atau 12:05"


def test_reindex_outside_tolerance_returns_nan(synthetic_pyranometer_xlsx):
    """Query 1 jam di luar source range (after 23:55) -> NaN."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    ts = pd.DatetimeIndex(["2027-01-01 12:00:00"])  # tahun depan, jauh
    poa = loader.get_per_ws(ts, "WB01")
    assert pd.isna(poa.iloc[0])


# ---------- File not found ----------


def test_loader_raises_for_missing_xlsx():
    with pytest.raises(FileNotFoundError):
        PyranometerLoader("nonexistent_xlsx_file.xlsx", ws_to_wb=WS_TO_WB_MAP)


# ---------- Wave 11 hotfix #4: per-WS -> avg fallback ----------


def test_get_per_ws_fallback_to_avg_fills_nan_positions(synthetic_pyranometer_xlsx):
    """Wave 11 hotfix #4: WS-2 NaN di hours 8-14 (fixture); fallback fills from avg.

    Mirrors user's real-world scenario di mana ``POA PLTS IKN 2026.xlsx``
    punya WS-2 column all-NaN untuk 2026-05-14. WB05/WB07 -> WS-2.
    Tanpa fallback, get_per_ws('WB05') returns NaN di posisi tsb -> M2b
    detector fan-out. Dengan fallback default ON, posisi NaN diisi dari
    kolom ``Rata-rata WS 1 - WS 5``.
    """
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    # Hours 10-13 = WS-2 NaN region di synthetic fixture (hours 8-14).
    ts = pd.date_range("2026-05-14 10:00", "2026-05-14 13:00", freq="5min")
    poa_wb05 = loader.get_per_ws(ts, "WB05")  # WB05 -> WS-2

    assert poa_wb05.notna().all(), "fallback should fill all NaN from avg"
    assert (poa_wb05 > 0).all(), "filled values from avg should be positive at noon"
    # Sanity attrs.
    assert poa_wb05.attrs["ws_label"] == "WS-2"
    assert poa_wb05.attrs["fallback_filled"] == len(ts)
    assert poa_wb05.attrs["fallback_total"] == len(ts)


def test_get_per_ws_no_fallback_preserves_nan_when_disabled(synthetic_pyranometer_xlsx):
    """Wave 11 hotfix #4: opt-out via fallback_to_avg=False preserves original NaN."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    ts = pd.date_range("2026-05-14 10:00", "2026-05-14 13:00", freq="5min")
    poa_wb05 = loader.get_per_ws(ts, "WB05", fallback_to_avg=False)

    assert poa_wb05.isna().all(), "WS-2 NaN region preserved when fallback disabled"
    assert poa_wb05.attrs["fallback_filled"] == 0


def test_get_per_ws_fallback_skipped_when_per_ws_has_data(synthetic_pyranometer_xlsx):
    """Wave 11 hotfix #4: fallback only fills NaN positions; valid per-WS data
    pass-through unchanged."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    # WB06 -> WS-3, which has valid data throughout the day.
    ts = pd.date_range("2026-05-14 10:00", "2026-05-14 13:00", freq="5min")
    poa_wb06 = loader.get_per_ws(ts, "WB06")  # WB06 -> WS-3

    assert poa_wb06.notna().all()
    assert poa_wb06.attrs["ws_label"] == "WS-3"
    # No NaN positions, so no fill.
    assert poa_wb06.attrs["fallback_filled"] == 0


# ---------- time_offset_minutes (2026-09-28) ----------


def _ws1(loader, stamp):
    return loader.get_for_ws(pd.DatetimeIndex([stamp]), "WS-1").iloc[0]


def test_time_offset_shifts_poa_stamps_later(synthetic_pyranometer_xlsx):
    # WHY: POA per WS terukur ~5 menit lebih awal dari telemetri inverter.
    # Koreksi MENAMBAH offset ke stempel POA -- nilai berstempel 09:00 kini
    # berstempel 09:05. Tanda terbalik menggandakan galatnya jadi 10 menit.
    base = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    shifted = PyranometerLoader(
        synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, time_offset_minutes=5,
    )
    original = _ws1(base, "2026-05-14 09:00")
    assert _ws1(shifted, "2026-05-14 09:05") == pytest.approx(original)
    assert _ws1(shifted, "2026-05-14 09:00") != pytest.approx(original)


def test_time_offset_per_file_shifts_only_its_own_file(synthetic_pyranometer_xlsx, tmp_path):
    # WHY: file POA 2025 dan 2026 bisa memakai konvensi stempel berbeda
    # (2025-12-01 hasilnya campuran); offset per file tidak boleh ikut
    # menggeser file lain.
    older = pd.read_excel(synthetic_pyranometer_xlsx, sheet_name="POA PLTS IKN")
    older["Date time"] = older["Date time"] - pd.Timedelta(days=365)
    older_path = tmp_path / "older.xlsx"
    older.to_excel(older_path, sheet_name="POA PLTS IKN", index=False)

    base = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    loader = PyranometerLoader(
        [str(older_path), synthetic_pyranometer_xlsx],
        ws_to_wb=WS_TO_WB_MAP, time_offset_minutes=[0, 5],
    )
    original = _ws1(base, "2026-05-14 09:00")
    assert _ws1(loader, "2025-05-14 09:00") == pytest.approx(original)
    assert _ws1(loader, "2026-05-14 09:05") == pytest.approx(original)


def test_time_offset_list_must_match_file_count(synthetic_pyranometer_xlsx):
    with pytest.raises(ValueError, match="time_offset_minutes"):
        PyranometerLoader(
            synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, time_offset_minutes=[0, 5],
        )


def test_from_geometry_yaml_reads_time_offset(synthetic_pyranometer_xlsx, tmp_path):
    # WHY: nilai offset diatur lewat config setelah batch verifikasi; kalau
    # kuncinya tak terbaca, koreksi diam-diam tidak pernah berlaku.
    geo = tmp_path / "geo.yaml"
    geo.write_text(
        "pyranometer:\n"
        f"  xlsx_path: {synthetic_pyranometer_xlsx!r}\n"
        "  time_offset_minutes: 5\n",
        encoding="utf-8",
    )
    loader = PyranometerLoader.from_geometry_yaml(str(geo))
    base = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    assert _ws1(loader, "2026-05-14 09:05") == pytest.approx(_ws1(base, "2026-05-14 09:00"))


# ---------- Koreksi sensor (spec 2026-10-02-penerapan-koreksi-poa-loader) ----------

def _k(**isi):
    return {"aktif": True, **isi}


def _poa(loader, ws, t):
    return float(loader.df.loc[pd.Timestamp(t), ws])


def test_koreksi_tidak_aktif_identik_mentah(synthetic_pyranometer_xlsx):
    """Blok koreksi yang belum disetujui (aktif: false) tidak boleh mengubah satu angka pun."""
    base = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    off = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, koreksi={
        "aktif": False, "ws_faktor_periode": {"WS-3": [{"mulai": "2026-05-14", "akhir": None, "faktor": 1.1}]}})
    pd.testing.assert_frame_equal(base.df, off.df)
    assert not off.koreksi_aktif and off.ringkasan_koreksi.empty


def test_faktor_hanya_di_rentang(synthetic_pyranometer_xlsx):
    """WS-3 1 Jan-10 Agu 2026 membaca ~6 % rendah: faktor berlaku tepat di rentangnya saja."""
    base = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    k = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, koreksi=_k(ws_faktor_periode={
        "WS-3": [{"mulai": "2026-05-14", "akhir": "2026-05-14", "faktor": 1.1}],
        "WS-4": [{"mulai": "2026-05-15", "akhir": None, "faktor": 1.1}]}))
    assert _poa(k, "WS-3", "2026-05-14 12:00") == pytest.approx(1.1 * _poa(base, "WS-3", "2026-05-14 12:00"))
    assert _poa(k, "WS-3", "2026-05-14 23:55") == pytest.approx(1.1 * _poa(base, "WS-3", "2026-05-14 23:55"))
    assert _poa(k, "WS-4", "2026-05-14 12:00") == pytest.approx(_poa(base, "WS-4", "2026-05-14 12:00"))


def test_penghalang_hanya_jam_dan_rentangnya(synthetic_pyranometer_xlsx):
    """WS-1 terbayangi pukul 11-12 sejak Jun 2026: hanya jam itu yang dibuang."""
    k = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, koreksi=_k(ws_jam_penghalang={
        "WS-1": [{"mulai": "2026-05-14", "akhir": None, "jam": [11]}]}))
    assert k.df.loc["2026-05-14 11:00":"2026-05-14 11:55", "WS-1"].isna().all()
    assert k.df.loc["2026-05-14 10:55", "WS-1"] > 0 and k.df.loc["2026-05-14 12:00", "WS-1"] > 0


def test_dikecualikan_diisi_avg_terkoreksi(synthetic_pyranometer_xlsx):
    """WS-1 dikeluarkan: WB08 diisi rata-rata WS lain yang SUDAH dikoreksi, bukan avg xlsx yang tercemar."""
    k = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, koreksi=_k(
        ws_dikecualikan={"WS-1": [{"mulai": "2026-05-14", "akhir": None}]},
        ws_faktor_periode={"WS-3": [{"mulai": "2026-05-14", "akhir": None, "faktor": 1.2}]}))
    t = pd.DatetimeIndex(["2026-05-14 12:00"])
    s = k.get_per_ws(t, "WB08")
    p = float(k.df_mentah.loc[t[0], "WS-4"])                 # semua WS mentah sama; WS-2 NaN pukul 8-14
    assert s.iloc[0] == pytest.approx((1.2 * p + p + p) / 3)  # WS-3 x1,2; WS-4; WS-5
    assert s.attrs["koreksi_aktif"] and s.attrs["fallback_filled"] == 1


def test_ringkasan_koreksi_menghitung_sampel(synthetic_pyranometer_xlsx):
    k = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, koreksi=_k(ws_jam_penghalang={
        "WS-1": [{"mulai": "2026-05-14", "akhir": "2026-05-14", "jam": [11]}]}))
    r = k.ringkasan_koreksi.iloc[0]
    assert (r["ws"], r["jenis"], r["n_sampel"]) == ("WS-1", "ws_jam_penghalang", 12)


@pytest.mark.parametrize("koreksi", [
    _k(ws_faktor_periode={"WS-3": [{"mulai": "2026-05-14", "akhir": None, "faktor": 1.5}]}),
    _k(ws_faktor_periode={"WS-3": [{"mulai": "2026-05-01", "akhir": "2026-05-20", "faktor": 1.1},
                                   {"mulai": "2026-05-14", "akhir": None, "faktor": 1.05}]}),
    _k(ws_dikecualikan={"WS-9": [{"mulai": "2026-05-14", "akhir": None}]}),
    _k(ws_jam_penghalang={"WS-1": [{"mulai": "2026-05-14", "akhir": None, "jam": [24]}]}),
    {"aktif": "ya"},
])
def test_koreksi_tidak_sah_ditolak(synthetic_pyranometer_xlsx, koreksi):
    """Salah ketik di config tidak boleh diam-diam menghasilkan POA yang salah."""
    with pytest.raises(ValueError):
        PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, koreksi=koreksi)


def test_from_geometry_yaml_membaca_dan_bisa_mematikan_koreksi(synthetic_pyranometer_xlsx, tmp_path):
    geo = tmp_path / "geo.yaml"
    geo.write_text(
        "pyranometer:\n"
        f"  xlsx_path: {synthetic_pyranometer_xlsx!r}\n"
        "  koreksi:\n"
        "    aktif: true\n"
        "    ws_faktor_periode:\n"
        "      WS-3:\n"
        "        - {mulai: 2026-05-14, akhir: null, faktor: 1.1}\n",
        encoding="utf-8",
    )
    base = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    t = "2026-05-14 12:00"
    assert _poa(PyranometerLoader.from_geometry_yaml(str(geo)), "WS-3", t) == pytest.approx(1.1 * _poa(base, "WS-3", t))
    assert _poa(PyranometerLoader.from_geometry_yaml(str(geo), koreksi=False), "WS-3", t) == pytest.approx(
        _poa(base, "WS-3", t))
