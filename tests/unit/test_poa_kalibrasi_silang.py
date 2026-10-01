"""Uji kalibrasi silang POA (docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md)."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.poa.kalibrasi_silang import rasio_ke_median, sampel_stabil

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
