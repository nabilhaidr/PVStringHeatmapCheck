"""Uji kalibrasi silang POA (docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import run_poa_cross_calibration as cli

from pv_pipeline.poa.kalibrasi_silang import (
    gain_absolut, gain_bulanan, gain_larik, gain_relatif, kalibrasi_per_periode, penghalang, periode_ws,
    mutu_data, profil_jam, rasio_cerah, rasio_harian, rasio_ke_median, sampel_stabil, sepakati, titik_ubah,
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

    def test_bayangan_pekat_tetap_ditandai(self):
        """WS-1 Jun-Agu 2026: bayangan menjatuhkan bacaan ke ~0,3-0,4, di bawah syarat sampel stabil.

        Sampel dipilih dari kecerahan menurut WS LAIN, supaya penghalang tidak tersaring
        keluar dari pengukurannya sendiri.
        """
        p = _turunkan(_poa(), "WS-1", 11, 0.3)                    # 0,3 x ~970 W/m2 < POA minimum stabil
        cerah = _poa(gains=(1.0,) * 5)["WS-1"]
        hal = penghalang(profil_jam(rasio_cerah(p, cerah)))
        assert list(zip(hal["ws"], hal["jam"])) == [("WS-1", 11)]

    def test_bacaan_nol_bukan_bayangan(self):
        """WS-1 Okt 2025-Mei 2026 mencatat 0 W/m2 di siang cerah: logger/sensor mati, bukan penghalang.

        Bayangan selalu menyisakan cahaya baur (WS-1 di bawah bayangan 0,34-0,45), tidak pernah 0.
        """
        p = _turunkan(_poa(), "WS-1", 11, 0.0)
        cerah = _poa(gains=(1.0,) * 5)["WS-1"]
        assert penghalang(profil_jam(rasio_cerah(p, cerah))).empty

    def test_rasio_cerah_menolak_langit_berawan(self):
        """Di langit berawan rasio antar-WS ikut awan lokal; bukan bahan profil penghalang."""
        p = _poa(hari=2, gains=(0.5,) * 5)                         # Kt WS lain 0,5 < 0,75
        cerah = _poa(hari=2, gains=(1.0,) * 5)["WS-1"]
        assert rasio_cerah(p, cerah).isna().all().all()


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


class TestPeriodeWs:
    def test_celah_40_hari_memotong(self):
        """Sensor yang dilepas lalu dipasang ulang bisa kembali dengan orientasi lain: dua periode."""
        p = _poa(hari=120)                                         # 2026-01-01..2026-04-30
        p.loc["2026-02-01":"2026-03-12", "WS-2"] = np.nan          # 40 hari kosong
        per = periode_ws(p)
        ws2 = per[per["ws"] == "WS-2"]
        assert list(ws2["periode"]) == [1, 2]
        assert list(ws2["mulai"]) == [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-03-13")]
        assert list(ws2["akhir"]) == [pd.Timestamp("2026-01-31"), pd.Timestamp("2026-04-30")]
        assert (per["ws"] == "WS-1").sum() == 1

    def test_celah_20_hari_tidak_memotong(self):
        """Celah singkat (gangguan logger) belum tentu berarti sensor berubah."""
        p = _poa(hari=120)
        p.loc["2026-02-01":"2026-02-20", "WS-2"] = np.nan
        assert (periode_ws(p)["ws"] == "WS-2").sum() == 1

    def test_kosong_di_awal_bukan_periode(self):
        p = _poa(hari=120)
        p.loc[:"2026-02-15", "WS-3"] = np.nan
        ws3 = periode_ws(p).query("ws == 'WS-3'")
        assert list(ws3["mulai"]) == [pd.Timestamp("2026-02-16")]

    def test_batas_manual_memotong_tanpa_celah(self):
        """WS-3 ~10 Agu 2026: lompatan tanpa celah data (kubah dibersihkan?); batas manual memotong periode."""
        per = periode_ws(_poa(hari=120), batas={"WS-2": ["2026-02-15"]})
        ws2 = per[per["ws"] == "WS-2"]
        assert list(ws2["mulai"]) == [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-02-15")]
        assert list(ws2["akhir"]) == [pd.Timestamp("2026-02-14"), pd.Timestamp("2026-04-30")]
        assert (per["ws"] == "WS-1").sum() == 1

    def test_batas_di_dalam_celah_tidak_menambah_periode(self):
        p = _poa(hari=120)
        p.loc["2026-02-01":"2026-03-12", "WS-2"] = np.nan
        assert (periode_ws(p, batas={"WS-2": ["2026-02-20"]})["ws"] == "WS-2").sum() == 2


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


class TestKalibrasiPerPeriode:
    def test_sensor_dipasang_ulang_dua_usulan(self):
        """Satu faktor untuk seluruh rentang mencampur dua sensor; per periode masing-masing benar."""
        p = _poa(hari=151, gains=(1.0, 1.0, 1.05, 1.0, 1.0))      # 2026-01-01..2026-05-31
        p.loc["2026-03-01":"2026-04-10", "WS-2"] = np.nan          # 41 hari kosong
        p.loc["2026-04-11":, "WS-2"] *= 0.8                        # kembali 0,8
        stabil = sampel_stabil(p)
        rasio = rasio_ke_median(p, stabil)
        cerah = _poa(hari=151, gains=(1.0,) * 5)["WS-1"]
        h = kalibrasi_per_periode(p, stabil, rasio, penghalang(profil_jam(rasio)), cerah,
                                  periode_ws(p)).set_index(["ws", "periode"])
        assert h.loc[("WS-2", 1), "usulan"] == pytest.approx(1.0, rel=0.01)
        assert h.loc[("WS-2", 2), "usulan"] == pytest.approx(1 / 0.8, rel=0.01)
        assert h.loc[("WS-2", 2), "mulai"] == pd.Timestamp("2026-04-11")

    def test_lompatan_tanpa_celah_dengan_batas_manual(self):
        """Tanpa batas, satu periode mencampur sensor sebelum dan sesudah pemeliharaan."""
        p = _poa(hari=151, gains=(1.0, 1.0, 1.05, 1.0, 1.0))
        p.loc["2026-03-15":, "WS-2"] *= 0.8
        stabil = sampel_stabil(p)
        rasio = rasio_ke_median(p, stabil)
        cerah = _poa(hari=151, gains=(1.0,) * 5)["WS-1"]
        h = kalibrasi_per_periode(p, stabil, rasio, penghalang(profil_jam(rasio)), cerah,
                                  periode_ws(p, batas={"WS-2": ["2026-03-15"]})).set_index(["ws", "periode"])
        assert h.loc[("WS-2", 1), "usulan"] == pytest.approx(1.0, rel=0.01)
        assert h.loc[("WS-2", 2), "usulan"] == pytest.approx(1 / 0.8, rel=0.01)


class TestPembanding:
    def test_acuan_tetap_meredam_ayunan_palsu(self):
        """WS-2 hilang lalu kembali bias: median 'semua WS lain' bergeser, pembanding tetap tidak."""
        p = _poa(hari=120, gains=(0.95, 1.0, 0.95, 1.0, 1.05))
        p.loc["2026-02-01":"2026-02-28", "WS-2"] = np.nan
        p.loc["2026-03-01":, "WS-2"] *= 1.12
        st = sampel_stabil(p)
        semua = gain_relatif(rasio_ke_median(p, st), pd.DataFrame(columns=["ws", "jam"])).set_index("ws")
        assert bool(semua.loc["WS-4", "bergeser"])                      # prasyarat: skenario memang mengayun
        tetap = gain_relatif(rasio_ke_median(p, st, pembanding=["WS-3", "WS-4", "WS-5"]),
                             pd.DataFrame(columns=["ws", "jam"])).set_index("ws")
        assert not bool(tetap.loc["WS-4", "bergeser"]) and tetap.loc["WS-4", "ayunan_bulanan"] < 0.01

    def test_sepakati_normalisasi_abs_ke_pembanding(self):
        absolut = pd.DataFrame({"ws": ["WS-1", "WS-2", "WS-3", "WS-4", "WS-5"],
                                "gain": [1.1, 1.2, 0.98, 1.0, 1.02], "n": 500})
        s = sepakati(_rel(), absolut, None, pembanding=["WS-3", "WS-4", "WS-5"]).set_index("ws")
        assert s.loc["WS-4", "gain_abs"] == pytest.approx(1.0)


class TestTitikUbah:
    def test_lompatan_tanpa_celah_disarankan(self):
        """WS-3 ~10 Agu 2026: lompatan tanpa celah harus muncul sebagai kandidat --batas."""
        p = _poa()
        p.loc["2026-02-15":, "WS-2"] *= 0.9
        tu = titik_ubah(rasio_harian(p), periode_ws(p))
        assert list(zip(tu["ws"], tu["tanggal"])) == [("WS-2", pd.Timestamp("2026-02-15"))]
        assert tu["lompatan"].iloc[0] == pytest.approx(-0.10, abs=0.005)

    def test_tanpa_lompatan_kosong(self):
        assert titik_ubah(rasio_harian(_poa()), periode_ws(_poa())).empty

    def test_turun_lalu_naik_dua_kandidat(self):
        """Dua lompatan berlawanan arah yang berdekatan adalah dua peristiwa, bukan satu (WS-1 Jul-Agu 2026)."""
        p = _poa()
        p.loc["2026-02-01":"2026-02-09", "WS-2"] *= 0.8
        tu = titik_ubah(rasio_harian(p), periode_ws(p))
        assert list(np.sign(tu["lompatan"])) == [-1.0, 1.0]
        assert abs((tu["tanggal"].iloc[0] - pd.Timestamp("2026-02-01")).days) <= 4
        assert abs((tu["tanggal"].iloc[1] - pd.Timestamp("2026-02-10")).days) <= 4

    def test_jam_penghalang_dibuang_dari_rasio_harian(self):
        """Bayangan yang berubah mengikuti matahari bukan lompatan gain; jamnya sudah ditandai penghalang."""
        p = _poa()
        p.loc[(p.index.hour == 11) & (p.index >= "2026-02-15"), "WS-1"] *= 0.3
        hal = pd.DataFrame({"ws": ["WS-1"], "jam": [11]})
        assert not titik_ubah(rasio_harian(p), periode_ws(p)).empty                  # prasyarat
        assert titik_ubah(rasio_harian(p, jam_penghalang=hal), periode_ws(p)).empty

    def test_lompatan_di_batas_celah_sudah_periode(self):
        """Celah >= 30 hari sudah memotong periode; lompatan di situ bukan kandidat baru."""
        p = _poa(hari=120)
        p.loc["2026-02-01":"2026-03-12", "WS-2"] = np.nan
        p.loc["2026-03-13":, "WS-2"] *= 0.9
        assert titik_ubah(rasio_harian(p), periode_ws(p)).empty


class TestMutuData:
    def test_nol_saat_cerah_dan_hari_kosong(self):
        """Logger mati (WS-1 Okt 2025-Mei 2026) dan hari kosong harus terlihat di setiap run."""
        p = _poa(hari=20)
        p.loc["2026-01-05", "WS-1"] = 0.0
        p.loc["2026-01-10", "WS-3"] = np.nan
        m = mutu_data(p).set_index("ws")
        assert m.loc["WS-1", "nol_saat_cerah"] == 49                   # 10:00-14:00 inklusif, langit cerah
        assert m.loc["WS-3", "hari_kosong"] == 1 and m.loc["WS-4", "nol_saat_cerah"] == 0


class _Loader:
    def __init__(self):
        self.df = _poa().assign(avg=0.0)
        self.wb_to_ws = {"WB08": "WS-1", "WB05": "WS-2"}


class _Langit:
    def estimate(self, idx):
        return _poa(gains=(1.0,) * 5)["WS-1"].reindex(idx)


@pytest.mark.filterwarnings("error::UserWarning")
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
                             "Kesepakatan", "TitikUbah", "Catatan"]
    assert x.parse("Larik").empty
    s = x.parse("Kesepakatan").set_index("ws")
    assert (s["periode"] == 1).all()                                   # tanpa celah: satu periode per WS
    assert s.loc["WS-2", "status"] == "usulan_koreksi"
    assert s.loc["WS-2", "usulan"] == pytest.approx(1 / 0.8, rel=0.01)
    assert config.read_bytes() == sebelum


def _pasang(monkeypatch, df):
    loader = _Loader()
    loader.df = df
    monkeypatch.setattr(cli, "_muat_poa", lambda geometry, raw_root, offset: (loader, 5.0))
    monkeypatch.setattr(cli.PvlibClearSkyEstimator, "from_geometry_yaml",
                        classmethod(lambda cls, *a, **k: _Langit()))


def _bulanan(tmp_path, *arg):
    cli.main(["--mulai", "2026-01-01", "--akhir", "2026-01-02", "--output-dir", str(tmp_path), *arg])
    x = pd.ExcelFile(tmp_path / "poa_cross_calibration_20260101_20260102.xlsx")
    return x.parse("Bulanan"), x.parse("Catatan").set_index("butir")["nilai"]


def test_cli_titik_ubah_tanpa_jam_penghalang(tmp_path, monkeypatch):
    """Bayangan yang makin pekat di jam penghalang tidak muncul sebagai kandidat --batas."""
    p = _poa()
    jam11 = p.index.hour == 11
    p.loc[jam11, "WS-1"] *= 0.6
    p.loc[jam11 & (p.index >= "2026-03-01"), "WS-1"] *= 1 / 3                  # 0,6 -> 0,2
    _pasang(monkeypatch, p.assign(avg=0.0))
    cli.main(["--mulai", "2026-01-01", "--akhir", "2026-03-31", "--output-dir", str(tmp_path)])
    x = pd.ExcelFile(tmp_path / "poa_cross_calibration_20260101_20260331.xlsx")
    hal = x.parse("Penghalang")
    assert list(zip(hal["ws"], hal["jam"])) == [("WS-1", 11)]
    assert x.parse("TitikUbah").empty


def test_cli_mutu_data_di_catatan(tmp_path, monkeypatch):
    p = _poa()
    p.loc["2026-01-05", "WS-1"] = 0.0
    _pasang(monkeypatch, p.assign(avg=0.0))
    cli.main(["--mulai", "2026-01-01", "--akhir", "2026-03-31", "--output-dir", str(tmp_path)])
    c = pd.read_excel(tmp_path / "poa_cross_calibration_20260101_20260331.xlsx",
                      sheet_name="Catatan").set_index("butir")["nilai"]
    assert "WS-1: 49" in c["sampel nol saat WS lain cerah (10-14, > 500 W/m2) per WS"]
    assert "WS-1: 0" in c["hari kosong per WS"] and "WS-1: 0" in c["sampel galat (<0 atau >1400) per WS"]


def test_cli_pembanding_tetap(tmp_path, monkeypatch):
    """--pembanding diteruskan: ayunan palsu WS-4 akibat WS-2 yang hilang lalu bias tidak muncul."""
    p = _poa(hari=120, gains=(0.95, 1.0, 0.95, 1.0, 1.05))
    p.loc["2026-02-01":"2026-02-28", "WS-2"] = np.nan
    p.loc["2026-03-01":, "WS-2"] *= 1.12
    _pasang(monkeypatch, p.assign(avg=0.0))
    cli.main(["--mulai", "2026-01-01", "--akhir", "2026-04-30", "--output-dir", str(tmp_path),
              "--pembanding", "WS-3,WS-4,WS-5"])
    x = pd.ExcelFile(tmp_path / "poa_cross_calibration_20260101_20260430.xlsx")
    assert not bool(x.parse("Kesepakatan").set_index("ws").loc["WS-4", "bergeser"])
    assert x.parse("Catatan").set_index("butir").loc["pembanding", "nilai"] == "WS-3, WS-4, WS-5"


def test_cli_batas_manual_memotong_periode(tmp_path, monkeypatch):
    """Tanggal pemeliharaan tanpa celah data (WS-3 ~10 Agu 2026) masuk lewat --batas."""
    _pasang(monkeypatch, _poa().assign(avg=0.0))
    cli.main(["--mulai", "2026-01-01", "--akhir", "2026-03-31", "--output-dir", str(tmp_path),
              "--batas", "WS-3:2026-02-15"])
    x = pd.ExcelFile(tmp_path / "poa_cross_calibration_20260101_20260331.xlsx")
    k = x.parse("Kesepakatan")
    assert list(k.loc[k["ws"] == "WS-3", "periode"]) == [1, 2]
    assert x.parse("Catatan").set_index("butir").loc["batas periode manual", "nilai"] == "WS-3:2026-02-15"


def test_cli_penghalang_dari_hari_cerah(tmp_path, monkeypatch):
    """Bayangan pekat WS-1 harus sampai ke sheet Penghalang (dan usulan ws_jam_penghalang)."""
    _pasang(monkeypatch, _turunkan(_poa(), "WS-1", 11, 0.3).assign(avg=0.0))
    cli.main(["--mulai", "2026-01-01", "--akhir", "2026-03-31", "--output-dir", str(tmp_path)])
    hal = pd.read_excel(tmp_path / "poa_cross_calibration_20260101_20260331.xlsx", sheet_name="Penghalang")
    assert list(zip(hal["ws"], hal["jam"])) == [("WS-1", 11)]


def test_cli_min_sampel_default_100_membuka_bulan_tipis(tmp_path, monkeypatch):
    """Langit IKN jarang stabil: pada 200 sampel/bulan hanya 1 bulan sah, ayunan tak terukur (2 Okt 2026)."""
    _pasang(monkeypatch, _poa().assign(avg=0.0))
    assert _bulanan(tmp_path, "--min-sampel", "200")[0]["median"].isna().all()   # ~146 sampel < 200
    b, catatan = _bulanan(tmp_path)
    assert b["median"].notna().all() and "100 sampel/bulan" in catatan["ambang"]


def test_cli_toleransi_default_3_persen(tmp_path, monkeypatch):
    """Riak ~2,5 % antar-sampel: tak stabil pada 2 % (kriteria lama), stabil pada default 3 %."""
    p = _poa()
    p = p.mul(1.0 + 0.0125 * np.where(np.arange(len(p)) % 2 == 0, 1.0, -1.0), axis=0)
    _pasang(monkeypatch, p.assign(avg=0.0))
    assert _bulanan(tmp_path, "--toleransi", "0.02")[0].empty
    b, catatan = _bulanan(tmp_path)
    assert b["median"].notna().all() and "stabil 3 %" in catatan["ambang"]
