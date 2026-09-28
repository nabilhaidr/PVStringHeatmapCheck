"""Tes pemilah penyebab kekurangan daya di POA tinggi: clipping vs sensor."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.saturation import SATURATION_METRICS, saturation_metrics

TS = pd.date_range("2026-08-10 06:30", "2026-08-10 17:30", freq="5min")
HRS = np.asarray(TS.hour + TS.minute / 60.0)
POA = 1000.0 * np.sin(np.pi * (HRS - 6.0) / 12.0) ** 2      # hari cerah mulus
ELEV = 85.0 * np.sin(np.pi * (HRS - 6.0) / 12.0)
T25 = np.full(len(TS), 25.0)                                 # tanpa efek suhu


def _metrics(poa_sensor, p_dc, p_ac=None):
    p_ac = p_dc if p_ac is None else p_ac
    return saturation_metrics(poa_sensor, p_dc, p_ac, T25, ELEV, gamma=-0.0029)


def test_ac_ceiling_is_attributed_to_clipping_not_sensor():
    # WHY: clipping adalah rugi ukuran inverter (keputusan desain), bukan galat
    # baseline atau sensor. Kalau tercampur ke "kekurangan non-clipping",
    # derate yang dikalibrasi ikut menyerap rugi yang sebenarnya bisa dipilah.
    m = _metrics(POA, np.minimum(0.3 * POA, 270.0))
    assert list(m) == SATURATION_METRICS
    assert m["clipping"] == 1.0
    assert m["clip_loss_pct"] > 0.5
    assert m["nonclip_high_loss_pct"] == pytest.approx(0.0, abs=1e-9)
    assert m["r_high_stable_unclipped"] == pytest.approx(1.0, abs=1e-9)


def test_flat_noon_top_without_ceiling_is_not_clipping():
    # WHY: di hari cerah daya berada dalam 1% dari maksimumnya sekitar satu jam
    # di sekitar tengah hari -- itu puncak kurva, bukan plafon inverter.
    m = _metrics(POA, 0.3 * POA)
    assert m["clipping"] == 0.0
    assert m["clip_loss_pct"] == pytest.approx(0.0)
    assert m["r_high_stable"] == pytest.approx(1.0, abs=1e-9)


def test_steady_high_irradiance_deficit_stays_visible_on_stable_samples():
    # WHY: kekurangan yang bertahan di kondisi cerah stabil TIDAK bisa berasal
    # dari awan yang tak terwakili sensor titik -- ia steady-state (kalibrasi
    # sensor, spektrum, suhu). Pemilah harus menyisakannya, bukan menghapusnya.
    derate = 1.0 - 0.1 * np.clip((POA - 700.0) / 300.0, 0.0, 1.0)
    m = _metrics(POA, 0.3 * POA * derate)
    assert m["clipping"] == 0.0
    assert m["r_high_stable_unclipped"] < 0.97
    assert m["nonclip_high_loss_pct"] > 0.5


def test_cloud_enhancement_spikes_are_excluded_from_stable_samples():
    # WHY: lonjakan tepi awan terbaca sensor titik tapi tidak diterima seluruh
    # array. Semua sampel POA tinggi ikut terseret (r_high_all < 1), sampel
    # stabil tidak -- selisih keduanya adalah efek keterwakilan sensor.
    sensor = POA.copy()
    spikes = (POA > 750.0) & (np.arange(len(TS)) % 6 == 0)
    sensor[spikes] *= 1.2
    m = _metrics(sensor, 0.3 * POA)
    assert m["r_high_all"] < 0.99
    assert m["r_high_stable"] == pytest.approx(1.0, abs=1e-9)


def test_day_without_stable_readings_gives_nan_not_numbers():
    # WHY: tanpa sampel stabil, penyebab tidak bisa dipilah; angka karangan
    # akan masuk rekap batch sebagai bukti palsu.
    sensor = POA * (1.0 + 0.2 * np.where(np.arange(len(TS)) % 2 == 0, 1.0, -1.0))
    m = _metrics(sensor, 0.3 * POA)
    assert m["n_calib"] == 0
    assert np.isnan(m["r_high_stable"])
    assert np.isnan(m["clip_loss_pct"])
