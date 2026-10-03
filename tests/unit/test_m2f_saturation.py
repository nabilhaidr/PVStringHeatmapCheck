"""Tes pemilah kekurangan daya di POA tinggi: plafon set point vs sensor."""
import numpy as np
import pandas as pd
import pytest

import run_saturation_check as cli
from pv_pipeline.m2f.saturation import SATURATION_METRICS, saturation_metrics
from pv_pipeline.poa.loader import PyranometerLoader

TS = pd.date_range("2026-08-10 06:30", "2026-08-10 17:30", freq="5min")
HRS = np.asarray(TS.hour + TS.minute / 60.0)
POA = 1000.0 * np.sin(np.pi * (HRS - 6.0) / 12.0) ** 2      # hari cerah mulus
ELEV = 85.0 * np.sin(np.pi * (HRS - 6.0) / 12.0)
T25 = np.full(len(TS), 25.0)                                 # tanpa efek suhu


def _metrics(poa_sensor, p_dc, p_ac=None, **kwargs):
    p_ac = p_dc if p_ac is None else p_ac
    return saturation_metrics(poa_sensor, p_dc, p_ac, T25, ELEV, gamma=-0.0029, **kwargs)


def test_setpoint_ceiling_is_attributed_to_curtailment_not_sensor():
    # WHY: plafon set point busbar adalah pembatasan jaringan 20 kV, bukan
    # galat baseline atau sensor. Kalau tercampur ke "kekurangan lain", derate
    # yang dikalibrasi ikut menyerap curtailment jaringan.
    m = _metrics(POA, np.minimum(0.3 * POA, 270.0))
    assert list(m) == SATURATION_METRICS
    assert m["at_ceiling"] == 1.0
    assert m["ceiling_loss_pct"] > 0.5
    assert m["uncapped_high_loss_pct"] == pytest.approx(0.0, abs=1e-9)
    assert m["r_high_stable_uncapped"] == pytest.approx(1.0, abs=1e-9)


def test_setpoint_history_marks_ceiling_too_short_for_plateau():
    # WHY: plafon yang hanya disentuh sebentar tidak membentuk plateau; riwayat
    # set point tetap mengenalinya, jadi keduanya dipakai bersama.
    p = np.minimum(0.3 * POA, 295.0)
    assert _metrics(POA, p)["at_ceiling"] == 0.0
    m = _metrics(POA, p, cap_kw=np.full(len(TS), 295.0), pmax_kw=330.0)
    assert m["at_ceiling"] == 1.0
    assert m["uncapped_high_loss_pct"] == pytest.approx(0.0, abs=1e-9)


def test_flat_noon_top_without_ceiling_is_not_a_ceiling():
    # WHY: di hari cerah daya berada dalam 1% dari maksimumnya sekitar satu jam
    # di sekitar tengah hari -- itu puncak kurva, bukan plafon.
    m = _metrics(POA, 0.3 * POA)
    assert m["at_ceiling"] == 0.0
    assert m["ceiling_loss_pct"] == pytest.approx(0.0)
    assert m["r_high_stable"] == pytest.approx(1.0, abs=1e-9)


def test_steady_high_irradiance_deficit_stays_visible_on_stable_samples():
    # WHY: kekurangan yang bertahan di kondisi cerah stabil TIDAK bisa berasal
    # dari awan yang tak terwakili sensor titik -- ia steady-state (kalibrasi
    # sensor, spektrum, suhu). Pemilah harus menyisakannya, bukan menghapusnya.
    derate = 1.0 - 0.1 * np.clip((POA - 700.0) / 300.0, 0.0, 1.0)
    m = _metrics(POA, 0.3 * POA * derate)
    assert m["at_ceiling"] == 0.0
    assert m["r_high_stable_uncapped"] < 0.97
    assert m["uncapped_high_loss_pct"] > 0.5


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
    assert np.isnan(m["ceiling_loss_pct"])


def test_cli_memakai_poa_terkoreksi_dan_mencetak_statusnya(synthetic_pyranometer_xlsx, tmp_path, monkeypatch,
                                                           capsys):
    """Bias sensor WS (mis. WS-2 +8..14 % sejak Jun 2026) terbaca sebagai kekurangan daya bila POA mentah."""
    geo = tmp_path / "geo.yaml"
    geo.write_text(
        "pyranometer:\n"
        f"  xlsx_path: {synthetic_pyranometer_xlsx!r}\n"
        "  koreksi:\n"
        "    aktif: true\n"
        "    sumber: uji\n"
        "    ws_faktor_periode:\n"
        "      WS-3:\n"
        "        - {mulai: 2026-05-14, akhir: null, faktor: 1.1}\n",
        encoding="utf-8",
    )
    terbaca = []

    def mulus(poa_df, day):
        terbaca.append(poa_df)
        return 0.0                                   # hari dilewati sebelum telemetri dibaca
    monkeypatch.setattr(cli, "discover_baseline_csvs", lambda *a: [(pd.Timestamp("2026-05-14"), "baseline.csv")])
    monkeypatch.setattr(cli, "poa_smooth_share", mulus)
    for kelas in (cli.CellTempProvider, cli.PvlibClearSkyEstimator, cli.SetpointCaps):
        monkeypatch.setattr(kelas, "from_geometry_yaml", classmethod(lambda cls, *a, **k: None))
    with pytest.raises(SystemExit):
        cli.main(["--geometry", str(geo)])
    t = pd.Timestamp("2026-05-14 12:00")
    mentah = PyranometerLoader(synthetic_pyranometer_xlsx).df
    assert terbaca[0].loc[t, "WS-3"] == pytest.approx(1.1 * mentah.loc[t, "WS-3"])
    assert "koreksi POA aktif (uji)" in capsys.readouterr().out
