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

from pv_pipeline.lstm_ae import M2bIntermittentDetector, build_inference_windows

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
