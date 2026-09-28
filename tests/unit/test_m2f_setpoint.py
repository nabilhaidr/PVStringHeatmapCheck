"""Tes plafon daya AC dari set point busbar (curtailment jaringan 20 kV)."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.setpoint import SetpointCaps, capped_mask, plateau_mask

BUSBARS = [
    {"column": "Setpoint Busbar 1", "wbs": ["WB01", "WB03"], "max_ac_kw": 28240.0},
    {"column": "Setpoint Busbar 2", "wbs": ["WB06"], "max_ac_kw": 30030.0},
]
PMAX = {"WB01": 215.0, "WB03": 330.0, "WB06": 330.0}
HRS = np.arange(7.0, 17.0, 5.0 / 60.0)
POA = 1000.0 * np.sin(np.pi * (HRS - 6.0) / 12.0) ** 2


def _caps(rows):
    history = pd.DataFrame(
        rows, columns=["t", "Setpoint Busbar 1", "Setpoint Busbar 2"],
    ).set_index("t")
    return SetpointCaps(history, BUSBARS, PMAX)


def test_cap_is_bus_setpoint_shared_by_inverter_max_ac():
    # WHY: set point per busbar dibagi proporsional terhadap kapasitas AC
    # maksimum -- WB01 (215 kW) dan WB03 (330 kW) di bus yang sama punya
    # plafon berbeda. Tanpa pembagian ini plafon WB01-02 tak pernah terbaca.
    caps = _caps([(pd.Timestamp("2026-07-29 10:00"), 25000.0, 25000.0)])
    ts = pd.DatetimeIndex(["2026-07-29 10:00", "2026-07-29 10:05"])
    np.testing.assert_allclose(caps.cap_kw(ts, "WB01"), 25000.0 * 215.0 / 28240.0)
    np.testing.assert_allclose(caps.cap_kw(ts, "WB06"), 25000.0 * 330.0 / 30030.0)


def test_setpoint_holds_until_next_change_but_not_beyond_history():
    # WHY: riwayat 10 menit, telemetri 5 menit -- nilai berlaku sampai
    # perubahan berikutnya. Di luar riwayat plafon tidak diketahui (NaN),
    # bukan diteruskan dari nilai terakhir.
    caps = _caps([
        (pd.Timestamp("2026-07-29 10:00"), 25000.0, 25000.0),
        (pd.Timestamp("2026-07-29 10:10"), 12000.0, 25000.0),
    ])
    ts = pd.DatetimeIndex(["2026-07-29 10:05", "2026-07-29 10:15", "2026-07-29 13:00"])
    cap = caps.cap_kw(ts, "WB03").to_numpy()
    assert cap[0] == pytest.approx(25000.0 * 330.0 / 28240.0)
    assert cap[1] == pytest.approx(12000.0 * 330.0 / 28240.0)
    assert np.isnan(cap[2])


def test_capped_mask_marks_output_held_at_cap():
    p_ac = np.array([292.0, 293.5, 250.0, np.nan])
    np.testing.assert_array_equal(
        capped_mask(p_ac, np.full(4, 292.1), 330.0), [True, True, False, False],
    )


def test_full_setpoint_is_not_a_limit():
    # WHY: set point = kapasitas penuh bus (bus 2 = 30.030 kW sepanjang 2025)
    # tidak membatasi apa pun; inverter di maksimum perangkat kerasnya itu
    # clipping ukuran inverter, bukan curtailment jaringan.
    assert not capped_mask(np.array([329.0, 330.0]), np.full(2, 330.0), 330.0).any()


def test_zero_setpoint_curtails_everything():
    # WHY: set point 0 = perintah henti dari jaringan; seluruh sisa energinya
    # curtailment, apa pun status inverternya.
    assert capped_mask(np.zeros(2), np.zeros(2), 330.0).all()


def test_plateau_below_hardware_max_is_curtailment():
    # WHY: cadangan saat riwayat set point kosong/keliru -- daya datar di
    # plafon sementara POA terus naik adalah batas dari luar.
    p = np.minimum(0.4 * POA, 275.0)
    assert plateau_mask(p, POA, 330.0).sum() >= 6


def test_plateau_at_hardware_max_is_not_curtailment():
    # WHY: plafon = Pmax perangkat keras adalah clipping ukuran inverter,
    # bukan pembatasan penyaluran jaringan.
    p = np.minimum(0.4 * POA, 330.0)
    assert not plateau_mask(p, POA, 330.0).any()


def test_clear_noon_peak_is_not_a_plateau():
    # WHY: di hari cerah daya berada dalam 1% dari maksimumnya sekitar satu
    # jam di tengah hari -- itu puncak kurva, bukan plafon.
    assert not plateau_mask(0.25 * POA, POA, 330.0).any()


def _daily_history(values_by_day):
    """Set point bus 1 per hari (06:00-17:50 @10 menit); nilai boleh dict jam->nilai."""
    frames = []
    for day, value in values_by_day.items():
        idx = pd.date_range(f"{day} 06:00", f"{day} 17:50", freq="10min")
        bus1 = pd.Series(value if not isinstance(value, dict) else np.nan, index=idx)
        if isinstance(value, dict):
            for start, v in sorted(value.items()):
                bus1[bus1.index >= pd.Timestamp(f"{day} {start}")] = v
        frames.append(pd.DataFrame({"Setpoint Busbar 1": bus1, "Setpoint Busbar 2": 30030.0}))
    return SetpointCaps(pd.concat(frames), BUSBARS, PMAX)


def test_setpoint_below_recent_normal_is_dispatch():
    # WHY: operator mencatat Deem Dispatch saat set point diturunkan dari
    # level biasanya. Level biasa = modus harian tertinggi 30 hari terakhir;
    # definisi ini menangkap 98,6% hari Deem Dispatch > 0 (2024-12..2026-08).
    caps = _daily_history({"2026-07-01": 25000.0, "2026-07-10": 12000.0})
    below, known = caps.below_normal(
        pd.DatetimeIndex(["2026-07-01 10:00", "2026-07-10 10:00"]), "WB03",
    )
    np.testing.assert_array_equal(below, [False, True])
    assert known.all()


def test_reduction_older_than_window_becomes_the_normal_cap():
    # WHY: plafon yang diturunkan permanen adalah batas kapasitas jaringan
    # yang baru, bukan dispatch selamanya.
    caps = _daily_history({"2026-05-01": 25000.0, "2026-06-15": 12000.0})
    below, _ = caps.below_normal(pd.DatetimeIndex(["2026-06-15 10:00"]), "WB03")
    assert not below.any()


def test_intraday_stop_is_dispatch():
    # WHY: 2026-07-29 set point bus 2 = 0 pada 12:40-15:10 -- perintah henti di
    # tengah hari berplafon normal.
    caps = _daily_history({"2026-07-29": {"06:00": 25000.0, "12:40": 0.0, "15:20": 25000.0}})
    below, _ = caps.below_normal(
        pd.DatetimeIndex(["2026-07-29 10:00", "2026-07-29 13:00"]), "WB03",
    )
    np.testing.assert_array_equal(below, [False, True])


def test_below_normal_is_unknown_outside_history():
    caps = _daily_history({"2026-07-01": 25000.0})
    below, known = caps.below_normal(pd.DatetimeIndex(["2026-09-05 10:00"]), "WB03")
    assert not below.any() and not known.any()


def _geometry(tmp_path, xlsx_name):
    geo = tmp_path / "geo.yaml"
    geo.write_text(
        "setpoint:\n"
        f"  xlsx_path: {str(tmp_path / xlsx_name)!r}\n"
        "  sheet: Setpoint\n"
        "  timestamp_col: Tanggal/Waktu\n"
        "  busbars:\n"
        "    - {column: Setpoint Busbar 1, wbs: [WB01, WB03], max_ac_kw: 28240}\n"
        "    - {column: Setpoint Busbar 2, wbs: [WB06], max_ac_kw: 30030}\n"
        "  inverter_max_ac_kw: {WB01: 215, WB03: 330, WB06: 330}\n",
        encoding="utf-8",
    )
    return str(geo)


def test_from_geometry_yaml_reads_history_and_capacities(tmp_path):
    pd.DataFrame({
        "Tanggal/Waktu": pd.date_range("2026-07-29 10:00", periods=3, freq="10min"),
        "Setpoint Busbar 1": [25000.0] * 3,
        "Setpoint Busbar 2": [30030.0] * 3,
    }).to_excel(tmp_path / "gen.xlsx", sheet_name="Setpoint", index=False)
    caps = SetpointCaps.from_geometry_yaml(_geometry(tmp_path, "gen.xlsx"))
    assert caps.inverter_max_ac_kw("WB03") == 330.0
    cap = caps.cap_kw(pd.DatetimeIndex(["2026-07-29 10:05"]), "WB03").iloc[0]
    assert cap == pytest.approx(25000.0 * 330.0 / 28240.0)


def test_missing_history_file_keeps_capacities_for_plateau(tmp_path):
    # WHY: tanpa berkas riwayat, penanda plateau masih butuh Pmax per WB;
    # kegagalan membaca harus terdengar, bukan mematikan keduanya diam-diam.
    with pytest.warns(UserWarning, match="set point"):
        caps = SetpointCaps.from_geometry_yaml(_geometry(tmp_path, "absent.xlsx"))
    assert caps.inverter_max_ac_kw("WB01") == 215.0
    assert np.isnan(caps.cap_kw(pd.DatetimeIndex(["2026-07-29 10:05"]), "WB01").iloc[0])


def test_missing_setpoint_section_gives_none(tmp_path):
    geo = tmp_path / "geo.yaml"
    geo.write_text("pyranometer: {}\n", encoding="utf-8")
    assert SetpointCaps.from_geometry_yaml(str(geo)) is None
