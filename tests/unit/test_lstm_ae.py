"""Tests inference path M2bIntermittentDetector (tanpa torch).

Kenapa penting: combined_df harian hanya berisi jam operasional (~12 jam =
~50 step 15-min). Sliding window SequenceBuilder butuh >=96 step sehingga
menghasilkan 0 window -> detector diam selamanya walau model sudah trained.
build_inference_windows harus memakai day-grid + night-fill 0 yang sama
dengan training supaya tiap inverter-hari menghasilkan tepat 1 window.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.lstm_ae import (
    M2bIntermittentDetector,
    build_inference_windows,
    build_window_errors_df,
)
from pv_pipeline.training_data import SequenceMetadata

FEATURE_COLS = [f"PV{n} input current(A)" for n in range(1, 29)]


def test_build_inference_windows_daily_operational_hours_yield_one_window_per_inverter(
    synthetic_combined_df,
):
    sequences, metas = build_inference_windows(synthetic_combined_df, FEATURE_COLS)

    # 3 inverter x 1 hari (06:00..18:00 saja) -> tetap 3 window 24h penuh.
    assert sequences.shape == (3, 96, 28)
    assert sorted(m.inverter_id for m in metas) == [
        "WB02-INV05", "WB05-INV01", "WB05-INV02",
    ]
    slot_midnight = 0
    slot_noon = 12 * 4
    assert float(np.abs(sequences[:, slot_midnight, :]).sum()) == 0.0
    assert sequences[0, slot_noon, 0] > 5.0  # arus siang ~13 A


def test_build_inference_windows_groups_by_calendar_day():
    rows = []
    for day in ["2026-05-14", "2026-05-15"]:
        t = pd.date_range(f"{day} 06:00", f"{day} 17:55", freq="5min")
        rows.append(pd.DataFrame({
            "Inverter_ID": "WB05-INV01",
            "Start Time": t,
            "PV1 input current(A)": 13.0,
        }))
    df = pd.concat(rows, ignore_index=True)

    sequences, metas = build_inference_windows(df, FEATURE_COLS)

    assert sequences.shape == (2, 96, 28)
    assert [m.window_start.date().isoformat() for m in metas] == [
        "2026-05-14", "2026-05-15",
    ]


def test_build_inference_windows_empty_df_returns_empty():
    sequences, metas = build_inference_windows(pd.DataFrame(), FEATURE_COLS)

    assert sequences.shape == (0, 96, 28)
    assert metas == []


def test_build_window_errors_df_ranks_all_windows_and_flags_threshold():
    metas = [
        SequenceMetadata(
            inverter_id=inv,
            window_start=pd.Timestamp("2026-05-01"),
            window_end=pd.Timestamp("2026-05-01 23:45"),
            n_features=2,
            feature_cols=["PV1 input current(A)", "PV2 input current(A)"],
        )
        for inv in ["WB01-INV01", "WB01-INV02"]
    ]
    sequences = np.ones((2, 4, 2), dtype=np.float32)
    errors = np.array([0.05, 0.20])

    df = build_window_errors_df(errors, metas, sequences, threshold=0.10)

    # SEMUA window masuk (bukan hanya > threshold), urut error terbesar dulu.
    assert len(df) == 2
    assert df["inverter_id"].tolist() == ["WB01-INV02", "WB01-INV01"]
    assert df["flagged"].tolist() == [True, False]
    assert df.iloc[0]["error_ratio"] == pytest.approx(2.0)
    assert df.iloc[1]["reconstruction_error"] == pytest.approx(0.05)
    assert df.iloc[0]["date"] == pd.Timestamp("2026-05-01")


def test_detector_disabled_warns_and_returns_empty(synthetic_combined_df):
    detector = M2bIntermittentDetector(enabled=False)

    with pytest.warns(UserWarning, match="disabled"):
        findings = detector.run(synthetic_combined_df, {})

    assert findings == []


def test_detector_enabled_without_artifacts_warns_and_returns_empty(
    synthetic_combined_df,
):
    detector = M2bIntermittentDetector(enabled=True)

    with pytest.warns(UserWarning, match="model_path"):
        findings = detector.run(synthetic_combined_df, {})

    assert findings == []
