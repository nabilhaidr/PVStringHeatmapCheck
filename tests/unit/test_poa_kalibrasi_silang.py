"""Uji kalibrasi silang POA (docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import run_poa_cross_calibration as cli

from pv_pipeline.poa.kalibrasi_silang import (
    gain_absolut, gain_bulanan, gain_larik, gain_relatif, penghalang, profil_jam,
    rasio_ke_median, sampel_stabil, sepakati,
)

_GAIN = (1.0, 0.8, 1.05, 1.0, 1.0)


def _poa(hari=90, gains=_GAIN, mulai="2026-01-01"):
    """Kurva langit cerah mulus yang sama untuk semua WS, dikali gain sensor."""
    idx = pd.date_range(mulai, periods=hari * 288, freq="5min")
    jam = (idx.hour + idx.minute / 60.0).to_numpy()
    dasar = np.clip(1000.0 * np.sin(np.pi * (jam - 6.0) / 12.0), 0.0, None)
    return pd.DataFrame({f"WS-{i + 1}": dasar * g for i, g in enumerate(gains)}, index=idx)


class TestSampelStabil:
    def test_kurva_mulus_stabil_hanya_di_jendela(self):
        s = sampel_stabil(_poa(hari=2))
        assert s.loc["2026-01-01 12:00"].all()
        assert not s.loc["2026-01-01 08:00"].any()     # di luar 09-15
        assert not s.loc["2026-01-01 16:00"].any()

    def test_lonjakan_awan_tidak_stabil(self):
        """Tepi awan di satu stasiun tidak boleh terbaca sebagai bias sensor."""
        p = _poa(hari=1)
        t = pd.Timestamp("2026-01-01 12:00")
        p.loc[t, "WS-2"] *= 1.3
        s = sampel_stabil(p)["WS-2"]
        for dt in (-5, 0, 5):
            assert not s.loc[t + pd.Timedelta(minutes=dt)]
        assert s.loc[t + pd.Timedelta(minutes=30)]

    def test_poa_galat_tidak_stabil(self):
        p = _poa(hari=1)
        p.loc["2026-01-01 11:00", "WS-1"] = 1500.0
        assert not sampel_stabil(p).loc["2026-01-01 11:00", "WS-1"]


class TestRasioKeMedian:
    def test_gain_yang_ditanam_kembali(self):
        """Sensor yang membaca 20 % terlalu rendah harus tampak 0,8 terhadap stasiun lain."""
        p = _poa(hari=3)
        r = rasio_ke_median(p, sampel_stabil(p))
        assert r["WS-2"].median() == pytest.approx(0.8, rel=0.01)
        assert r["WS-3"].median() == pytest.approx(1.05, rel=0.01)

    def test_pembanding_kurang_nan(self):
        """Dua stasiun saja: tiap stasiun hanya punya satu pembanding -> tidak dinilai."""
        p = _poa(hari=1)[["WS-1", "WS-2"]]
        assert rasio_ke_median(p, sampel_stabil(p)).isna().all().all()


def _rasio(p):
    return rasio_ke_median(p, sampel_stabil(p))


def _turunkan(p, ws, jam, faktor, sampai=None):
    """POA ``ws`` dikali ``faktor`` pada jam ``jam`` (penghalang), hingga tanggal ``sampai``."""
    m = p.index.hour == jam
    if sampai is not None:
        m &= p.index < pd.Timestamp(sampai)
    p.loc[m, ws] *= faktor
    return p


class TestGainBulanan:
    def test_tiga_bulan_gain_kembali(self):
        g = gain_bulanan(_rasio(_poa()))
        ws2 = g[g["ws"] == "WS-2"]
        assert list(ws2["bulan"]) == ["2026-01", "2026-02", "2026-03"]
        assert ws2["median"].to_numpy() == pytest.approx([0.8] * 3, rel=0.01)

    def test_bulan_data_tipis_nan(self):
        g = gain_bulanan(_rasio(_poa(hari=2)))          # ~146 sampel stabil per WS < 200
        assert g["median"].isna().all() and set(g["alasan"]) == {"data tipis"}


class TestPenghalang:
    def test_penurunan_jam_11_tiga_bulan_ditandai(self):
        """Penghalang bergantung jam; satu faktor gain tak bisa memperbaikinya."""
        r = _rasio(_turunkan(_poa(), "WS-3", 11, 0.6))
        hal = penghalang(profil_jam(r))
        assert list(zip(hal["ws"], hal["jam"])) == [("WS-3", 11)]
        g = gain_relatif(r, hal).set_index("ws")
        assert g.loc["WS-3", "gain"] == pytest.approx(1.05, rel=0.01)

    def test_satu_bulan_tidak_ditandai(self):
        """Satu bulan berawan di jam tertentu bukan penghalang tetap."""
        r = _rasio(_turunkan(_poa(), "WS-3", 11, 0.6, sampai="2026-02-01"))
        assert penghalang(profil_jam(r)).empty


class TestGainRelatif:
    def test_gain_bergeser_antar_bulan_ditandai(self):
        """Sensor yang mengotor/berubah kalibrasi tidak boleh diberi satu faktor."""
        p = _poa()
        p.loc[p.index >= "2026-02-01", "WS-2"] *= 0.9       # WS-2: 0,80 Januari -> 0,72 sejak Februari
        r = _rasio(p)
        g = gain_relatif(r, penghalang(profil_jam(r))).set_index("ws")
        assert bool(g.loc["WS-2", "bergeser"]) and not bool(g.loc["WS-1", "bergeser"])

    def test_satu_bulan_sah_ayunan_tak_terukur(self):
        """Ayunan antar-bulan butuh >= 2 bulan sah; satu bulan bukan bukti gain stabil (ayunan 0 menipu)."""
        r = _rasio(_poa(hari=20))                            # hanya Januari, ~1460 sampel
        g = gain_relatif(r, penghalang(profil_jam(r))).set_index("ws")
        assert g.loc["WS-2", "n_bulan_sah"] == 1 and np.isnan(g.loc["WS-2", "ayunan_bulanan"])


class TestGainAbsolut:
    def test_poa_delapan_persepuluh_langit_cerah(self):
        p = _poa(hari=2, gains=(0.8,) * 5)
        cerah = _poa(hari=2, gains=(1.0,) * 5)["WS-1"]
        g = gain_absolut(p, cerah, sampel_stabil(p)).set_index("ws")
        assert g.loc["WS-1", "gain"] == pytest.approx(0.8, rel=0.01)

    def test_hari_berawan_tidak_dihitung(self):
        """Kekeruhan dan awan merusak acuan absolut; hanya saat sangat cerah yang dipakai."""
        p = _poa(hari=2, gains=(0.5,) * 5)
        cerah = _poa(hari=2, gains=(1.0,) * 5)["WS-1"]
        assert gain_absolut(p, cerah, sampel_stabil(p))["n"].eq(0).all()

    def test_sensor_rendah_tidak_tersaring(self):
        """Kecerahan dinilai dari median semua WS, jadi WS yang membaca 0,7 tetap terukur."""
        p = _poa(hari=2, gains=(1.0, 0.7, 1.0, 1.0, 1.0))
        cerah = _poa(hari=2, gains=(1.0,) * 5)["WS-1"]
        g = gain_absolut(p, cerah, sampel_stabil(p)).set_index("ws")
        assert g.loc["WS-2", "gain"] == pytest.approx(0.7, rel=0.01)


class TestGainLarik:
    def test_rasio_wb_mengembalikan_gain_sensor(self):
        """POA tinggi -> harapan tinggi -> rasio aktual/harapan rendah: larik adalah standar transfer."""
        gain_ws = dict(zip(["WS-1", "WS-2", "WS-3", "WS-4", "WS-5"], _GAIN))
        wb_to_ws = {f"WB{i:02d}": ws for i, ws in enumerate(
            ["WS-1", "WS-1", "WS-2", "WS-2", "WS-3", "WS-3", "WS-4", "WS-4", "WS-5", "WS-5"], start=1)}
        k = pd.DataFrame([{"date": d, "wb_id": wb, "measured_ratio": 0.9 / gain_ws[ws]}
                          for d in pd.date_range("2026-06-01", periods=3) for wb, ws in wb_to_ws.items()])
        g = gain_larik(k, wb_to_ws).set_index("ws")["gain"]
        assert g["WS-2"] == pytest.approx(0.8, rel=0.01) and g["WS-3"] == pytest.approx(1.05, rel=0.01)


def _rel(gain_ws2=0.8, bergeser=False, n_bulan_sah=3):
    return pd.DataFrame({"ws": ["WS-1", "WS-2", "WS-3", "WS-4", "WS-5"],
                         "gain": [1.0, gain_ws2, 1.05, 1.0, 1.0], "n": 1000, "n_bulan_sah": n_bulan_sah,
                         "ayunan_bulanan": 0.01, "bergeser": [False, bergeser, False, False, False]})


def _acuan(ws2):
    return pd.DataFrame({"ws": ["WS-1", "WS-2", "WS-3", "WS-4", "WS-5"],
                         "gain": [1.0, ws2, 1.05, 1.0, 1.0], "n": 500})


class TestSepakati:
    def test_tiga_acuan_sepakat_usulan_kebalikan_gain(self):
        s = sepakati(_rel(), _acuan(0.8), _acuan(0.8)).set_index("ws")
        assert s.loc["WS-2", "status"] == "usulan_koreksi"
        assert s.loc["WS-2", "usulan"] == pytest.approx(1 / 0.8)

    def test_dua_sepakat_satu_menyimpang(self):
        s = sepakati(_rel(), _acuan(0.8), _acuan(0.9)).set_index("ws")
        assert s.loc["WS-2", "status"] == "usulan_koreksi"

    def test_semua_berselisih_perlu_lapangan(self):
        """Tanpa dua acuan yang sepakat, faktor koreksi bisa memindahkan bias, bukan menghapusnya."""
        s = sepakati(_rel(0.8), _acuan(0.85), _acuan(0.9)).set_index("ws")
        assert s.loc["WS-2", "status"] == "perlu_lapangan" and np.isnan(s.loc["WS-2", "usulan"])

    def test_sepakat_tetapi_bergeser_perlu_lapangan(self):
        s = sepakati(_rel(bergeser=True), _acuan(0.8), _acuan(0.8)).set_index("ws")
        assert s.loc["WS-2", "status"] == "perlu_lapangan"

    def test_tanpa_larik_dua_acuan(self):
        s = sepakati(_rel(), _acuan(0.8), None).set_index("ws")
        assert s.loc["WS-2", "status"] == "usulan_koreksi" and np.isnan(s.loc["WS-2", "gain_larik"])

    def test_satu_bulan_sah_perlu_lapangan(self):
        """Pergeseran sensor tak bisa disingkirkan dari satu bulan; acuan yang sepakat pun tak cukup."""
        s = sepakati(_rel(n_bulan_sah=1), _acuan(0.8), _acuan(0.8)).set_index("ws")
        assert s.loc["WS-2", "status"] == "perlu_lapangan" and np.isnan(s.loc["WS-2", "usulan"])
        assert "data bulanan kurang" in s.loc["WS-2", "alasan"]

    def test_kesepakatan_berantai_hanya_pasangan_terdekat(self):
        """Kasus WS-1 run 1 Okt: rel-abs 0,017 dan abs-larik 0,025 lolos, tetapi rel-larik 0,042 tidak.

        Ketiganya tak SALING sepakat; yang dihitung hanya pasangan terdekat (rel, abs).
        """
        s = sepakati(_rel(0.951), _acuan(0.968), _acuan(0.993)).set_index("ws")
        assert s.loc["WS-2", "alasan"] == "sepakat: abs, rel"
        assert s.loc["WS-2", "usulan"] == pytest.approx(1 / np.median([0.951, 0.968]))


class _Loader:
    def __init__(self):
        self.df = _poa().assign(avg=0.0)
        self.wb_to_ws = {"WB08": "WS-1", "WB05": "WS-2"}


class _Langit:
    def estimate(self, idx):
        return _poa(gains=(1.0,) * 5)["WS-1"].reindex(idx)


def test_cli_delapan_sheet_usulan_ws2_tanpa_mengubah_config(tmp_path, monkeypatch):
    """Usulan hanya dicetak; config dan loader baru berubah lewat spesifikasi terpisah."""
    monkeypatch.setattr(cli, "_muat_poa", lambda geometry, raw_root, offset: (_Loader(), 5.0))
    monkeypatch.setattr(cli.PvlibClearSkyEstimator, "from_geometry_yaml",
                        classmethod(lambda cls, *a, **k: _Langit()))
    config = Path("config/site_geometry.yaml")
    sebelum = config.read_bytes()

    cli.main(["--mulai", "2026-01-01", "--akhir", "2026-03-31", "--output-dir", str(tmp_path)])

    x = pd.ExcelFile(tmp_path / "poa_cross_calibration_20260101_20260331.xlsx")
    assert x.sheet_names == ["Bulanan", "ProfilJam", "Penghalang", "Relatif", "Absolut", "Larik",
                             "Kesepakatan", "Catatan"]
    assert x.parse("Larik").empty
    s = x.parse("Kesepakatan").set_index("ws")
    assert s.loc["WS-2", "status"] == "usulan_koreksi"
    assert s.loc["WS-2", "usulan"] == pytest.approx(1 / 0.8, rel=0.01)
    assert config.read_bytes() == sebelum
