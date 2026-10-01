"""Uji kalibrasi dc_derate_per_wb (docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md)."""
import math

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.derate_calibration import hari_sejak_hujan, kt_poa, porsi_kosong

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
