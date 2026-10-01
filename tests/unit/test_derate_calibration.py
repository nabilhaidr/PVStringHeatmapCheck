"""Uji kalibrasi dc_derate_per_wb (docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md)."""
import math

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.derate_calibration import (
    fit_wb, hari_sejak_hujan, kt_poa, porsi_kosong, putuskan, validasi,
)

_IDX = pd.date_range("2026-06-01 06:00", "2026-06-01 18:00", freq="5min")


def _hujan(nilai: dict) -> pd.Series:
    s = pd.Series(nilai, dtype=float)
    s.index = pd.to_datetime(s.index)
    return s


def _elev() -> pd.Series:
    """Matahari naik dari 0 ke 60 derajat lalu turun: pagi/sore di bawah 15 derajat."""
    jam = (_IDX.hour + _IDX.minute / 60.0).to_numpy()
    return pd.Series(np.clip(60.0 * np.sin(np.pi * (jam - 6.0) / 12.0), 0.0, None), index=_IDX)


class TestHariSejakHujan:
    def test_hujan_hari_itu_nol_lalu_bertambah(self):
        """Debu menumpuk sejak hujan pembersih terakhir; hujan kecil tidak membersihkan."""
        h = _hujan({"2026-06-01": 0.0, "2026-06-02": 12.0, "2026-06-03": 0.0, "2026-06-04": 1.0})
        r = hari_sejak_hujan(h, pd.DatetimeIndex(["2026-06-02", "2026-06-03", "2026-06-04"]))
        assert list(r) == [0, 1, 2]

    def test_ambang_tepat_5_mm_terhitung(self):
        h = _hujan({"2026-06-01": 5.0, "2026-06-02": 4.9})
        assert list(hari_sejak_hujan(h, pd.DatetimeIndex(["2026-06-03"]))) == [2]

    def test_dibatasi_maks(self):
        h = _hujan({"2026-01-01": 30.0})
        assert list(hari_sejak_hujan(h, pd.DatetimeIndex(["2026-06-01"]), maks=30)) == [30]

    def test_tanpa_hujan_sebelumnya_maks(self):
        """Hujan SESUDAH hari itu tidak boleh dihitung membersihkan."""
        h = _hujan({"2026-06-05": 20.0})
        assert list(hari_sejak_hujan(h, pd.DatetimeIndex(["2026-06-01"]))) == [30]


class TestKtPoa:
    def test_poa_tujuh_persepuluh_langit_cerah(self):
        cerah = pd.Series(900.0, index=_IDX)
        assert kt_poa(0.7 * cerah, cerah, _elev()) == pytest.approx(0.7)

    def test_sampel_elevasi_rendah_tidak_dihitung(self):
        """POA pagi/sore dangkal didominasi sudut datang dan bayangan antar-baris, bukan langit."""
        e = _elev()
        cerah = pd.Series(900.0, index=_IDX)
        poa = (0.7 * cerah).where(e > 15.0, 5000.0)
        assert kt_poa(poa, cerah, e) == pytest.approx(0.7)

    def test_nan_diabaikan(self):
        e = _elev()
        cerah = pd.Series(900.0, index=_IDX)
        poa = 0.7 * cerah
        poa.iloc[60:80] = np.nan
        assert kt_poa(poa, cerah, e) == pytest.approx(0.7)


class TestPorsiKosong:
    def test_pecahan_nan_pada_siang(self):
        """WB yang POA-nya diisi dari rata-rata stasiun cuaca lain tidak punya indeks langit sendiri."""
        e = _elev()
        poa = pd.Series(500.0, index=_IDX)
        siang = np.flatnonzero(e.to_numpy() > 15.0)
        poa.iloc[siang[: len(siang) // 4]] = np.nan
        assert porsi_kosong(poa, e) == pytest.approx((len(siang) // 4) / len(siang))


def _tabel(n=42, *, a=0.9, b=-0.1, c=0.0, e=-0.002, derau=0.002, kt_tetap=False, seed=1):
    rng = np.random.default_rng(seed)
    kt = np.full(n, 0.6) if kt_tetap else rng.uniform(0.3, 0.9, n)
    mulus = rng.uniform(0.0, 0.5, n)
    hsh = rng.integers(0, 20, n).astype(float)
    rasio = (a + b * (kt - np.median(kt)) + c * (mulus - np.median(mulus)) + e * hsh
             + rng.normal(0.0, derau, n))
    return pd.DataFrame({"rasio": rasio, "kt": kt, "mulus": mulus, "hari_sejak_hujan": hsh})


class TestFitWb:
    def test_koefisien_yang_ditanam_kembali(self):
        """Bila regresi tak mengembalikan efek langit/debu yang ditanam, keputusan berikutnya tak bermakna."""
        f = fit_wb(_tabel())
        assert f["n"] == 42
        assert f["b"] == pytest.approx(-0.1, rel=0.1)
        assert f["e"] == pytest.approx(-0.002, rel=0.1)

    def test_rasio_bersih_menghapus_debu(self):
        """Derate = rasio pada kondisi baru hujan; soiling punya estimator sendiri di M2f."""
        f = fit_wb(_tabel(kt_tetap=True, b=0.0))
        assert f["rasio_bersih"] == pytest.approx(0.9, abs=0.002)

    def test_data_terlalu_sedikit_koefisien_nan(self):
        f = fit_wb(_tabel(n=3))
        assert f["n"] == 3 and math.isnan(f["b"]) and math.isnan(f["rasio_bersih"])


def _fit(**ubah):
    f = {"n": 42, "a": 0.9, "b": -0.05, "c": 0.0, "e": -0.001, "t_b": -5.0, "t_c": 0.5, "t_e": -3.0,
         "ayunan_langit": 0.05, "rasio_bersih": 0.91}
    f.update(ubah)
    return f


class TestPutuskan:
    def test_data_kurang(self):
        r = putuskan(_fit(n=19))
        assert r["keputusan"] == "data_kurang" and math.isnan(r["nilai"])

    def test_debu_positif_bermakna_berarti_tercampur(self):
        """Rasio yang NAIK seiring debu menumpuk mustahil secara fisika: langit dan debu tak terpisah."""
        assert putuskan(_fit(e=0.002, t_e=2.0))["keputusan"] == "tercampur"

    def test_debu_positif_tak_bermakna_bukan_tercampur(self):
        assert putuskan(_fit(e=0.002, t_e=1.9))["keputusan"] != "tercampur"

    @pytest.mark.parametrize("ayunan, harap", [(0.0299, "konstanta"), (0.03, "model_langit")])
    def test_ambang_ayunan_langit(self, ayunan, harap):
        assert putuskan(_fit(ayunan_langit=ayunan))["keputusan"] == harap

    @pytest.mark.parametrize("t_b, harap", [(-1.99, "konstanta"), (-2.0, "model_langit")])
    def test_ambang_t_langit(self, t_b, harap):
        """Ayunan besar tetapi tak bermakna secara statistik tetap konstanta."""
        assert putuskan(_fit(t_b=t_b))["keputusan"] == harap

    def test_konstanta_bernilai_rasio_bersih(self):
        r = putuskan(_fit(ayunan_langit=0.01))
        assert r["keputusan"] == "konstanta" and r["nilai"] == 0.91


class TestValidasi:
    def test_derate_benar_sisa_nol_di_tiap_tercile(self):
        """Konstanta yang benar tidak meninggalkan tren langit pada sisa."""
        t = _tabel(b=0.0, derau=0.0005).assign(wb_id="WB03")
        fits = {"WB03": fit_wb(t)}
        v = validasi(t, fits, {"WB03": fits["WB03"]["rasio_bersih"]})
        assert set(v["tercile_kt"]) == {"rendah", "sedang", "tinggi"}
        assert v["sisa_median"].abs().max() < 0.005
        assert v["beda_antar_tercile"].max() < 0.005
