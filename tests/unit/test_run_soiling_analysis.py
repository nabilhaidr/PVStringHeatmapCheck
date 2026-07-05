"""Tests data-prep run_soiling_analysis.py (tanpa rdtools/POA).

Kenapa penting:
- Kolom rainfall 5-menit adalah COUNTER KUMULATIF harian. Kalau diagregasi
  SUM (kesalahan yang wajar diduga), total harian menggandakan berkali-kali
  dan SRR salah mengklasifikasi cleaning events. Harus MAX per hari.
- CSV presipitasi yang ditulis harus terbaca kembali oleh
  _load_precipitation() detector (kontrak format antar modul).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pv_pipeline.m2a.soiling import _load_precipitation
from run_soiling_analysis import (
    RAINFALL_SHEET,
    load_baseline_for_soiling,
    load_daily_rainfall,
    write_precipitation_csv,
)


def _write_rainfall_xlsx(path, day_ws_ramps):
    """day_ws_ramps: {day: {ws_col: [nilai kumulatif per 5-menit]}}."""
    rows = []
    for day, ws_map in day_ws_ramps.items():
        n = max(len(v) for v in ws_map.values())
        t = pd.date_range(f"{day} 00:00", periods=n, freq="5min")
        for k in range(n):
            row = {"Date time": t[k]}
            for ws_col, ramp in ws_map.items():
                row[ws_col] = ramp[k] if k < len(ramp) else np.nan
            rows.append(row)
    pd.DataFrame(rows).to_excel(path, sheet_name=RAINFALL_SHEET, index=False)
    return str(path)


def test_load_daily_rainfall_uses_daily_max_of_cumulative_counter(tmp_path):
    path = _write_rainfall_xlsx(tmp_path / "rain.xlsx", {
        "2026-01-01": {
            "Daily Rainfall (mm) WS 1": [0.0, 1.0, 3.0],   # kumulatif -> total 3.0
            "Daily Rainfall (mm) WS 2": [0.0, 0.5, 1.0],   # kumulatif -> total 1.0
        },
        "2026-01-02": {
            "Daily Rainfall (mm) WS 1": [0.0, 0.0, 0.0],
            "Daily Rainfall (mm) WS 2": [np.nan, np.nan, np.nan],
        },
    })

    daily = load_daily_rainfall([path])

    # MAX per WS lalu mean antar WS: (3.0 + 1.0) / 2 = 2.0.
    # Kalau agregasi keliru pakai SUM, hasilnya (4.0 + 1.5) / 2 = 2.75.
    assert daily[pd.Timestamp("2026-01-01")] == 2.0
    assert daily[pd.Timestamp("2026-01-02")] == 0.0


def test_load_daily_rainfall_merges_multiple_files(tmp_path):
    p2025 = _write_rainfall_xlsx(tmp_path / "rain2025.xlsx", {
        "2025-12-31": {"Daily Rainfall (mm) WS 1": [0.0, 5.0]},
    })
    p2026 = _write_rainfall_xlsx(tmp_path / "rain2026.xlsx", {
        "2026-01-01": {"Daily Rainfall (mm) WS 1": [0.0, 2.5]},
    })

    daily = load_daily_rainfall([p2025, p2026])

    assert list(daily.index) == [
        pd.Timestamp("2025-12-31"), pd.Timestamp("2026-01-01"),
    ]
    assert daily.tolist() == [5.0, 2.5]


def test_precipitation_csv_roundtrip_compatible_with_detector_loader(tmp_path):
    daily = pd.Series(
        [0.0, 12.5],
        index=pd.DatetimeIndex(["2026-01-01", "2026-01-02"]),
        name="precipitation_mm",
    )
    out_path = write_precipitation_csv(daily, str(tmp_path / "precip.csv"))

    loaded = _load_precipitation(out_path)

    assert loaded is not None
    assert loaded[pd.Timestamp("2026-01-01")] == 0.0
    assert loaded[pd.Timestamp("2026-01-02")] == 12.5


def test_load_baseline_for_soiling_prefers_active_power_and_drops_pv_cols(tmp_path):
    csv_path = tmp_path / "2026-05-14.csv"
    pd.DataFrame({
        "Inverter_ID": ["WB05-INV01"] * 3,
        "Start Time": pd.date_range("2026-05-14 06:00", periods=3, freq="5min"),
        "Active power(kW)": [100.0, 110.0, 120.0],
        "PV1 Power(kW)": [10.0, 11.0, 12.0],
        "PV1 input voltage(V)": [1200.0, 1201.0, 1202.0],  # tak relevan -> dibuang
    }).to_csv(csv_path, index=False)

    df = load_baseline_for_soiling([(pd.Timestamp("2026-05-14"), str(csv_path))])

    assert list(df.columns) == ["Inverter_ID", "Start Time", "Active power(kW)"]
    assert len(df) == 3
