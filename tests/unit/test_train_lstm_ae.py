"""Tests untuk data-prep train_lstm_ae.py (tanpa torch).

Kenapa penting:
- Discovery harus meniru layout Drive: baseline/{YYYY-MM}/{YYYY-MM-DD}.csv,
  manifest.csv ikut nongkrong di folder yang sama dan TIDAK boleh terbaca.
- Baseline CSV hanya berisi jam operasional (~12 jam). Window training harus
  tetap 96 step (24 jam @ 15-min) sesuai spec LSTM-AE, jadi malam/gap wajib
  diisi 0 A -- kalau tidak, tidak ada window yang terbentuk sama sekali.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from train_lstm_ae import (
    build_day_windows,
    discover_baseline_csvs,
    feature_columns,
)


def _touch(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_discover_scans_month_subfolders_and_skips_manifest(tmp_path):
    _touch(tmp_path / "2026-05" / "2026-05-14.csv")
    _touch(tmp_path / "2026-05" / "manifest.csv")
    _touch(tmp_path / "2026-06" / "2026-06-01.csv")
    _touch(tmp_path / "manifest.csv")
    _touch(tmp_path / "20260514.csv")  # nama df_plot export, bukan baseline

    files = discover_baseline_csvs(str(tmp_path))

    assert [d.date().isoformat() for d, _ in files] == ["2026-05-14", "2026-06-01"]


def test_discover_filters_date_range_inclusive(tmp_path):
    for name in ["2026-05-13", "2026-05-14", "2026-05-15"]:
        _touch(tmp_path / "2026-05" / f"{name}.csv")

    files = discover_baseline_csvs(
        str(tmp_path), start_date="2026-05-14", end_date="2026-05-14"
    )

    assert [d.date().isoformat() for d, _ in files] == ["2026-05-14"]


def test_discover_prefers_month_subfolder_over_flat_duplicate(tmp_path):
    _touch(tmp_path / "2026-05" / "2026-05-14.csv")
    _touch(tmp_path / "2026-05-14.csv")

    files = discover_baseline_csvs(str(tmp_path))

    assert len(files) == 1
    assert "2026-05" in files[0][1].replace("\\", "/").rsplit("/", 2)[-2]


def _day_df(day="2026-05-14", inverter="WB05-INV01", current=13.0):
    """Data operasional 06:00..17:55 @5-min saja -- meniru isi baseline CSV."""
    t = pd.date_range(f"{day} 06:00", f"{day} 17:55", freq="5min")
    return pd.DataFrame({
        "Inverter_ID": inverter,
        "Start Time": t,
        "PV1 input current(A)": current,
        "PV2 input current(A)": current / 2.0,
    })


def test_build_day_windows_full_day_grid_with_night_zero_fill():
    feature_cols = feature_columns()  # PV1..PV28
    df = _day_df()

    windows, metas = build_day_windows(df, pd.Timestamp("2026-05-14"), feature_cols)

    # Data cuma 12 jam, tapi window harus tetap 96 step (24h @ 15-min):
    # tanpa night-fill 0, sequence 24h tidak akan pernah terbentuk.
    assert windows.shape == (1, 96, 28)
    assert metas[0].inverter_id == "WB05-INV01"
    slot_midnight = 0            # 00:00 -> malam, no data -> 0.0
    slot_noon = 12 * 4           # 12:00 -> operasional
    assert windows[0, slot_midnight, 0] == 0.0
    assert windows[0, slot_noon, 0] == pytest.approx(13.0)
    assert windows[0, slot_noon, 1] == pytest.approx(6.5)
    # PV3..PV28 tidak ada di CSV -> diisi 0 supaya n_features konsisten.
    assert float(np.abs(windows[0, :, 2:]).sum()) == 0.0


def test_build_day_windows_skips_inverter_without_any_feature_data():
    feature_cols = feature_columns()
    df = _day_df()
    dead = _day_df(inverter="WB05-INV02")
    dead[["PV1 input current(A)", "PV2 input current(A)"]] = np.nan
    df = pd.concat([df, dead], ignore_index=True)

    windows, metas = build_day_windows(df, pd.Timestamp("2026-05-14"), feature_cols)

    assert windows.shape[0] == 1
    assert [m.inverter_id for m in metas] == ["WB05-INV01"]


def test_build_day_windows_rejects_unknown_resample_method():
    with pytest.raises(ValueError, match="resample_method"):
        build_day_windows(
            _day_df(), pd.Timestamp("2026-05-14"), feature_columns(),
            resample_method="max",
        )
