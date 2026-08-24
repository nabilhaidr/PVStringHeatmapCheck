"""Tes skema artefak deret waktu defisit dan konversinya ke kWh."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.baseline import DEFAULT_FREQ_HOURS
from pv_pipeline.m2f.deficit import (
    DEFICIT_COLUMNS,
    build_deficit_frame,
    deficit_to_kwh,
)


def _frame(actual, counterfactual, flagged):
    idx = pd.date_range("2026-05-13 12:00", periods=len(actual), freq="5min")
    return build_deficit_frame(
        timestamps=idx,
        inverter_id="WB03-INV01",
        pv_string="PV5",
        actual_kw=np.array(actual, dtype=float),
        counterfactual_kw=np.array(counterfactual, dtype=float),
        flagged=np.array(flagged, dtype=bool),
    )


def test_frame_has_exact_schema():
    frame = _frame([1.0], [2.0], [True])
    assert list(frame.columns) == DEFICIT_COLUMNS


def test_deficit_counts_only_flagged_timestamps():
    # WHY: defisit di luar jendela ter-flag bukan milik detektor ini. Kalau
    # ikut diklaim, kategori lain kehilangan energinya.
    frame = _frame([1.0, 1.0], [3.0, 3.0], [True, False])
    kwh = deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)
    assert kwh.sum() == pytest.approx(2.0 * DEFAULT_FREQ_HOURS)


def test_negative_deficit_is_clipped_to_zero():
    # String yang melampaui counterfactual bukan "rugi negatif".
    frame = _frame([5.0], [3.0], [True])
    kwh = deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)
    assert kwh.sum() == pytest.approx(0.0)


def test_nan_deficit_is_zero():
    frame = _frame([np.nan], [3.0], [True])
    kwh = deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)
    assert kwh.sum() == pytest.approx(0.0)


def test_kwh_is_indexed_by_timestamp():
    frame = _frame([1.0, 1.0], [2.0, 2.0], [True, True])
    kwh = deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)
    assert isinstance(kwh.index, pd.DatetimeIndex)
    assert len(kwh) == 2


def test_missing_column_raises():
    frame = _frame([1.0], [2.0], [True]).drop(columns=["flagged"])
    with pytest.raises(KeyError, match="flagged"):
        deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)
