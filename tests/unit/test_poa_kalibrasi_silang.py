"""Uji kalibrasi silang POA (docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md)."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.poa.kalibrasi_silang import (
    gain_bulanan, gain_relatif, penghalang, profil_jam, rasio_ke_median, sampel_stabil,
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
