"""Tes skema artefak deret waktu defisit dan konversinya ke kWh."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.baseline import DEFAULT_FREQ_HOURS
from pv_pipeline.m2f.deficit import (
    build_deficit_frame,
    deficit_to_kwh,
    reduce_deficit_frames,
)


def _frame(actual, counterfactual, flagged, poa_source="pyranometer"):
    idx = pd.date_range("2026-05-13 12:00", periods=len(actual), freq="5min")
    return build_deficit_frame(
        timestamps=idx,
        poa_source=poa_source,
        inverter_id="WB03-INV01",
        pv_string="PV5",
        actual_kw=np.array(actual, dtype=float),
        counterfactual_kw=np.array(counterfactual, dtype=float),
        flagged=np.array(flagged, dtype=bool),
    )


def test_frame_has_exact_schema():
    # WHY: dibandingkan ke daftar literal, bukan ke DEFICIT_COLUMNS itu sendiri
    # -- kalau dibandingkan ke konstanta yang sama yang dipakai
    # build_deficit_frame untuk mem-filter kolom, tes ini tidak pernah bisa
    # gagal walau skema berubah diam-diam.
    frame = _frame([1.0], [2.0], [True])
    assert list(frame.columns) == [
        "poa_source",
        "timestamp",
        "inverter_id",
        "pv_string",
        "actual_kw",
        "counterfactual_kw",
        "flagged",
    ]


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


def test_flagged_nan_gap_is_preserved_not_zeroed():
    # WHY: NaN pada actual_kw/counterfactual_kw di baris ter-flag berarti
    # string tidak bisa dievaluasi sama sekali (mis. kolom tegangan hilang
    # upstream di open_circuit.py/mppt_ratio.py), BUKAN "dicek, tidak ada
    # rugi". Mengisi 0.0 di sini akan membuat dc_cable_fault melaporkan
    # 0.0 kWh untuk string yang sebenarnya tak terukur.
    frame = _frame([np.nan], [3.0], [True])
    kwh = deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)
    assert pd.isna(kwh.iloc[0])


def test_unflagged_nan_gap_is_still_zero():
    # Timestamp tak ter-flag dijamin 0.0 walau gapnya NaN -- itu memang
    # bukan kandidat rugi kategori ini, beda dari kasus flagged-tapi-NaN.
    frame = _frame([np.nan], [3.0], [False])
    kwh = deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)
    assert kwh.iloc[0] == pytest.approx(0.0)


def test_reduce_deficit_frames_takes_max_not_sum_across_detectors():
    # WHY: dua detektor menandai fisik yang sama pada timestamp yang sama
    # menjelaskan SATU rugi, bukan dua -- summing akan melipatgandakannya.
    idx = pd.date_range("2026-05-13 12:00", periods=2, freq="5min")
    frame_a = _frame([1.0, 1.0], [4.0, 4.0], [True, True])  # gap = 3.0
    frame_b = _frame([1.0, 1.0], [3.0, 3.0], [True, True])  # gap = 2.0
    reduced = reduce_deficit_frames(
        [frame_a, frame_b], poa_source="pyranometer", index=idx
    )
    expected_per_ts = 3.0 * DEFAULT_FREQ_HOURS  # max(3.0, 2.0), bukan 5.0
    assert reduced.sum() == pytest.approx(2 * expected_per_ts)


def test_reduce_deficit_frames_selects_only_given_poa_source():
    idx = pd.date_range("2026-05-13 12:00", periods=2, freq="5min")
    frame_pyra = _frame([1.0, 1.0], [3.0, 3.0], [True, True], poa_source="pyranometer")
    frame_sat = _frame([1.0, 1.0], [9.0, 9.0], [True, True], poa_source="satellite")
    reduced = reduce_deficit_frames(
        [frame_pyra, frame_sat], poa_source="pyranometer", index=idx
    )
    assert reduced.sum() == pytest.approx(2 * 2.0 * DEFAULT_FREQ_HOURS)


def test_reduce_deficit_frames_handles_duplicate_timestamps_without_raising():
    # WHY: production emit_all_sources=True x 3 detektor -> union defisit
    # mentah membawa hingga 15 baris per timestamp per string; `.reindex()`
    # polos pada itu melempar
    # `ValueError: cannot reindex on an axis with duplicate labels`.
    idx = pd.date_range("2026-05-13 12:00", periods=3, freq="5min")
    frame_a = _frame([0.0, 0.0, 0.0], [2.0, 2.0, 2.0], [True, True, True])
    frame_b = _frame([0.0, 0.0, 0.0], [2.0, 2.0, 2.0], [True, True, True])
    reduced = reduce_deficit_frames(
        [frame_a, frame_b], poa_source="pyranometer", index=idx
    )
    assert len(reduced) == 3
    assert reduced.sum() == pytest.approx(3 * 2.0 * DEFAULT_FREQ_HOURS)


def test_reduce_deficit_frames_reindexes_with_zero_fill():
    idx = pd.date_range("2026-05-13 12:00", periods=4, freq="5min")
    frame = _frame([1.0], [3.0], [True])  # hanya 1 timestamp dari 4
    reduced = reduce_deficit_frames([frame], poa_source="pyranometer", index=idx)
    assert len(reduced) == 4
    assert reduced.iloc[1:].tolist() == [0.0, 0.0, 0.0]


def test_reduce_deficit_frames_empty_list_returns_zero_series():
    idx = pd.date_range("2026-05-13 12:00", periods=2, freq="5min")
    reduced = reduce_deficit_frames([], poa_source="pyranometer", index=idx)
    assert reduced.tolist() == [0.0, 0.0]


def test_reduce_deficit_frames_all_nan_at_timestamp_is_preserved():
    # WHY: kalau SEMUA detektor tidak bisa mengevaluasi satu timestamp (mis.
    # kolom tegangan hilang di semua), hasilnya harus tetap NaN -- bukan
    # di-fillna ke 0.0, supaya "tidak terukur" tidak menyamar jadi "aman"
    # setelah lolos ke claim_dc_cable_fault -> ledger.claim().
    idx = pd.date_range("2026-05-13 12:00", periods=1, freq="5min")
    frame_a = _frame([np.nan], [3.0], [True])
    frame_b = _frame([np.nan], [5.0], [True])
    reduced = reduce_deficit_frames(
        [frame_a, frame_b], poa_source="pyranometer", index=idx
    )
    assert pd.isna(reduced.iloc[0])
