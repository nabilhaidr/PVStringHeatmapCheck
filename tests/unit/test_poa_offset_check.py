"""Tes deteksi offset waktu POA terhadap telemetri inverter."""
import numpy as np
import pandas as pd

from pv_pipeline.poa.offset_check import OFFSET_COLUMNS, best_lag_by_ws


def _truth() -> pd.Series:
    """Iradiansi sebenarnya: kurva siang x awan acak (deterministik)."""
    idx = pd.date_range("2026-05-13 07:00", "2026-05-13 16:00", freq="5min")
    hours = idx.hour + idx.minute / 60.0
    clear = 1000.0 * np.sin(np.pi * (hours - 6.0) / 12.0)
    cloud = np.random.default_rng(7).choice([1.0, 0.4], size=len(idx), p=[0.6, 0.4])
    return pd.Series(clear * cloud, index=idx)


def _frames(poa_early_min: int):
    """Daya berstempel benar; POA berstempel ``poa_early_min`` menit lebih awal."""
    g = _truth()
    power = pd.DataFrame({"WS-1": 0.3 * g.loc["2026-05-13 08:00":"2026-05-13 15:00"]})
    poa = pd.DataFrame({
        "WS-1": pd.Series(g.to_numpy(), index=g.index - pd.Timedelta(minutes=poa_early_min)),
    })
    return power, poa


def test_detects_poa_stamped_five_minutes_early():
    # WHY: arah tanda menentukan arah koreksi di loader. Lag +5 = stempel POA
    # 5 menit lebih awal -> koreksi MENAMBAH 5 menit ke stempel POA; tanda
    # terbalik akan menggandakan offset menjadi 10 menit.
    power, poa = _frames(5)
    out = best_lag_by_ws(power, poa)
    assert list(out.columns) == OFFSET_COLUMNS
    row = out.set_index("ws").loc["WS-1"]
    assert row["best_lag_min"] == 5
    assert row["corr_best"] > 0.9
    assert row["corr_lag0"] < 0.5


def test_aligned_series_give_zero_lag():
    # WHY: tanpa offset, alat verifikasi tidak boleh mengarang koreksi.
    power, poa = _frames(0)
    assert best_lag_by_ws(power, poa).set_index("ws").loc["WS-1", "best_lag_min"] == 0


def test_too_few_points_skips_ws():
    # WHY: hari dengan data tipis (fiber putus, POA kosong) tidak boleh
    # menyumbang lag palsu ke rekap batch.
    power, poa = _frames(5)
    assert best_lag_by_ws(power.iloc[:10], poa).empty
