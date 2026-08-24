"""m2f: pin kW arithmetic + flagged mask untuk deficit_frames open_circuit/mppt_ratio.

Fixture existing kedua detektor ini (test_open_circuit.py, test_mppt_ratio.py)
current-only -- tidak ada kolom voltage, jadi tidak ada tes yang memverifikasi
angka actual_kw/counterfactual_kw ataupun bahwa flagged mask di deficit_frames
mengikuti mask yang SUDAH ter-debounce (bukan qualifying mentah). File baru ini
(bukan edit ke file tes lama) mengisi celah itu.
"""
from __future__ import annotations

import pandas as pd
import pytest

from pv_pipeline.mppt_ratio import M2bMpptRatio
from pv_pipeline.open_circuit import M2bOpenCircuit


class _MockPOAConstant:
    """POA konstan 1000 W/m^2 -- cukup untuk gate daylight, sunset bukan fokus tes ini."""

    def get_poa(self, timestamps, wb_id, source="auto"):
        idx = pd.DatetimeIndex(timestamps)
        return pd.Series(1000.0, index=idx)


# ---------- open_circuit ----------

_OC_VOLTAGE_V = 500.0
_OC_HEALTHY_I = 10.0
_OC_FAULT_I = 0.05      # PV5: open-circuit sungguhan, near-zero sepanjang window
_OC_DIP_I = 0.2         # PV1: dip 1x5menit terisolasi (cloud-edge, bukan fault)


def _oc_df_with_voltage(t: pd.DatetimeIndex, dip_ts: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for ts in t:
        row = {"Inverter_ID": "WB05-INV05", "Start Time": ts}
        for pv_n in range(1, 5):  # PV1-PV4: sehat
            i_val = _OC_DIP_I if (pv_n == 1 and ts == dip_ts) else _OC_HEALTHY_I
            row[f"PV{pv_n} input current(A)"] = i_val
            row[f"PV{pv_n} input voltage(V)"] = _OC_VOLTAGE_V
        row["PV5 input current(A)"] = _OC_FAULT_I  # open-circuit sungguhan
        row["PV5 input voltage(V)"] = _OC_VOLTAGE_V
        rows.append(row)
    return pd.DataFrame(rows)


def _oc_cfg() -> dict:
    return {
        "m2b_open_circuit": {
            "poa_threshold_wm2": 200.0,
            "poa_floor_wm2": 50.0,
            "hour_cutoff_end": 18.0,
            "respect_inverter_shutdown": False,
            "filter_mode": "hour_cutoff",
            "i_ratio_threshold": 0.05,
            "debounce_consecutive_steps": 20,  # production default (~100 menit)
            "confidence_pct": 95.0,
            "pv_max": 5,
            "min_peer_strings": 3,
            "min_daylight_samples": 5,
        },
        "poa": {
            "emit_all_sources": False,
            "default_source": "auto",
            "site_geometry_path": "config/site_geometry.yaml",
        },
    }


def test_open_circuit_deficit_kw_and_debounced_flag():
    # WHY: sebelum fix, flag_mask deficit open_circuit dibangun dari mask yang
    # sudah ter-debounce -- tes ini mengunci perilaku itu (bukan cuma
    # menganggap benar) sekaligus memverifikasi actual_kw/counterfactual_kw
    # (I*V/1000) yang sebelumnya tidak ada tes-nya sama sekali (fixture lama
    # current-only).
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 09:00", freq="5min")
    dip_ts = t[18]
    df = _oc_df_with_voltage(t, dip_ts)
    sm = M2bOpenCircuit(poa=_MockPOAConstant())
    sm.run(df, _oc_cfg())

    assert sm.deficit_frames, "deficit_frames harus terisi"
    frames = pd.concat(sm.deficit_frames, ignore_index=True)
    pv1 = frames[frames["pv_string"] == "PV1"].set_index("timestamp")
    pv5 = frames[frames["pv_string"] == "PV5"].set_index("timestamp")

    # kW arithmetic: I_q95 sibling = 10.0 A di SEMUA baris (4 dari 5 kolom
    # selalu 10.0 A persis, termasuk baris dip PV1 -- quantile 0.95 dari 5
    # nilai jatuh di duplikat 10.0 itu).
    normal_ts = t[0]
    assert pv1.loc[dip_ts, "actual_kw"] == pytest.approx(_OC_DIP_I * _OC_VOLTAGE_V / 1000.0)
    assert pv1.loc[dip_ts, "counterfactual_kw"] == pytest.approx(_OC_HEALTHY_I * _OC_VOLTAGE_V / 1000.0)
    assert pv1.loc[normal_ts, "actual_kw"] == pytest.approx(_OC_HEALTHY_I * _OC_VOLTAGE_V / 1000.0)
    assert pv5.loc[normal_ts, "actual_kw"] == pytest.approx(_OC_FAULT_I * _OC_VOLTAGE_V / 1000.0)
    assert pv5.loc[normal_ts, "counterfactual_kw"] == pytest.approx(_OC_HEALTHY_I * _OC_VOLTAGE_V / 1000.0)

    # flagged mask HARUS ikut hasil debounce, bukan qualifying mentah: dip
    # 1x5menit (panjang run=1) jauh di bawah debounce=20 -> tidak boleh
    # diklaim kWh sama sekali.
    assert bool(pv1.loc[dip_ts, "flagged"]) is False
    assert not pv1["flagged"].any()
    status = sm.artifacts["StringStatus"]
    pv1_status = status[status["pv_string"] == "PV1"].iloc[0]
    assert pv1_status["n_qualifying_steps"] == 1   # qualifying mentah tetap 1x
    assert pv1_status["n_debounced_events"] == 0   # tapi tidak lolos debounce
    assert bool(pv1_status["emitted_finding"]) is False

    # PV5 open-circuit sungguhan (near-zero sepanjang 37 baris >> 20) harus
    # flagged penuh -- kontras positif terhadap PV1.
    assert pv5["flagged"].all()


# ---------- mppt_ratio ----------

_MR_VOLTAGE_V = 500.0
_MR_HEALTHY_I = 10.0
_MR_DEGRADED_I = 1.0    # PV3: underperform sungguhan, 10% partner, sepanjang window
_MR_DIP_I = 0.2         # PV1: dip 1x5menit terisolasi


def _mppt_strings_yaml(tmp_path) -> str:
    text = (
        "empty_pv_map: {}\n"
        "mppt_map:\n"
        "  TEST-MODEL:\n"
        "    wbs: [WB05]\n"
        "    mppt:\n"
        "      1: [1, 2]\n"
        "      2: [3, 4]\n"
    )
    path = tmp_path / "strings.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _mr_df_with_voltage(t: pd.DatetimeIndex, dip_ts: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for ts in t:
        row = {"Inverter_ID": "WB05-INV01", "Start Time": ts}
        pv1_i = _MR_DIP_I if ts == dip_ts else _MR_HEALTHY_I
        values = {1: pv1_i, 2: _MR_HEALTHY_I, 3: _MR_DEGRADED_I, 4: _MR_HEALTHY_I}
        for pv_n, i_val in values.items():
            row[f"PV{pv_n} input current(A)"] = i_val
            row[f"PV{pv_n} input voltage(V)"] = _MR_VOLTAGE_V
        rows.append(row)
    return pd.DataFrame(rows)


def _mr_cfg(strings_yaml: str) -> dict:
    return {
        "m2b_mppt_ratio": {
            "poa_threshold_wm2": 200.0,
            "poa_floor_wm2": 50.0,
            "hour_cutoff_end": 18.0,
            "respect_inverter_shutdown": False,
            "filter_mode": "hour_cutoff",
            "ratio_threshold": 0.85,
            "ratio_high": 0.50,
            "ratio_critical": 0.20,
            "debounce_consecutive_steps": 20,  # production default (~100 menit)
            "pv_max": 4,
            "min_partner_strings": 1,
            "min_daylight_samples": 5,
            "mppt_map_path": strings_yaml,
        },
        "m2e": {"empty_pv_map_path": strings_yaml},
        "poa": {"emit_all_sources": False, "default_source": "auto"},
        "preprocessing": {"enabled": False},
    }


def test_mppt_ratio_deficit_kw_and_debounced_flag(tmp_path):
    # WHY: brief eksplisit -- sebelum fix, flag_mask deficit pakai `qualifying`
    # pre-debounce, jadi dip 5-menit terisolasi diklaim kWh walau n_events==0
    # dan emitted==False. Tes ini mengunci bahwa itu tidak lagi terjadi,
    # sekaligus memverifikasi actual_kw/counterfactual_kw (I*V/1000, fixture
    # lama current-only jadi belum pernah ke-cover).
    strings_yaml = _mppt_strings_yaml(tmp_path)
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 09:00", freq="5min")
    dip_ts = t[18]
    df = _mr_df_with_voltage(t, dip_ts)
    sm = M2bMpptRatio(poa=_MockPOAConstant())
    sm.run(df, _mr_cfg(strings_yaml))

    assert sm.deficit_frames, "deficit_frames harus terisi"
    frames = pd.concat(sm.deficit_frames, ignore_index=True)
    pv1 = frames[frames["pv_string"] == "PV1"].set_index("timestamp")
    pv3 = frames[frames["pv_string"] == "PV3"].set_index("timestamp")

    # kW arithmetic: partner PV1 = PV2 (median konstan 10.0 A); partner PV3 = PV4.
    normal_ts = t[0]
    assert pv1.loc[dip_ts, "actual_kw"] == pytest.approx(_MR_DIP_I * _MR_VOLTAGE_V / 1000.0)
    assert pv1.loc[dip_ts, "counterfactual_kw"] == pytest.approx(_MR_HEALTHY_I * _MR_VOLTAGE_V / 1000.0)
    assert pv1.loc[normal_ts, "actual_kw"] == pytest.approx(_MR_HEALTHY_I * _MR_VOLTAGE_V / 1000.0)
    assert pv3.loc[normal_ts, "actual_kw"] == pytest.approx(_MR_DEGRADED_I * _MR_VOLTAGE_V / 1000.0)
    assert pv3.loc[normal_ts, "counterfactual_kw"] == pytest.approx(_MR_HEALTHY_I * _MR_VOLTAGE_V / 1000.0)

    # flagged mask HARUS ikut hasil debounce, bukan qualifying mentah.
    assert bool(pv1.loc[dip_ts, "flagged"]) is False
    assert not pv1["flagged"].any()
    status = sm.artifacts["StringStatus"]
    pv1_status = status[status["pv_string"] == "PV1"].iloc[0]
    assert pv1_status["n_qualifying_steps"] == 1   # qualifying mentah tetap 1x
    assert pv1_status["n_debounced_events"] == 0   # tapi tidak lolos debounce
    assert bool(pv1_status["emitted_finding"]) is False

    # PV3 underperform sungguhan (10% partner, 37 baris >> debounce 20) harus
    # flagged penuh -- kontras positif terhadap PV1.
    assert pv3["flagged"].all()
