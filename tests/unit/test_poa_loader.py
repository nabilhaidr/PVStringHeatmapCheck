"""Test pv_pipeline.poa.loader: PyranometerLoader (xlsx parser + WB->WS mapping)."""
from __future__ import annotations

import pandas as pd
import pytest

from pv_pipeline.poa.loader import PyranometerLoader


WS_TO_WB_MAP = {
    "WS-1": ["WB08", "WB09", "WB10"],
    "WS-2": ["WB05", "WB07"],
    "WS-3": ["WB06"],
    "WS-4": ["WB03", "WB04"],
    "WS-5": ["WB01", "WB02"],
}


# ---------- Constructor + xlsx parsing ----------


def test_loader_parses_synthetic_xlsx(synthetic_pyranometer_xlsx):
    """Load synthetic xlsx -> df dengan WS-1..5 + avg columns."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    assert not loader.df.empty
    for col in ["WS-1", "WS-2", "WS-3", "WS-4", "WS-5", "avg"]:
        assert col in loader.df.columns
    # Synthetic: 288 timestamps (1 day @ 5-min)
    assert len(loader.df) == 288


def test_loader_naive_datetime_index(synthetic_pyranometer_xlsx):
    """Loader strip tz dari index untuk konsisten dengan naive WITA convention."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    assert loader.df.index.tz is None


def test_loader_wb_to_ws_reverse_mapping(synthetic_pyranometer_xlsx):
    """Reverse map WB -> WS resolved dari ws_to_wb."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    assert loader.wb_to_ws["WB01"] == "WS-5"
    assert loader.wb_to_ws["WB05"] == "WS-2"
    assert loader.wb_to_ws["WB10"] == "WS-1"


# ---------- get_per_ws ----------


def test_get_per_ws_returns_series_for_mapped_wb(synthetic_pyranometer_xlsx):
    """WB01 -> WS-5, return Series untuk WS-5 column."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    ts = pd.date_range("2026-05-14 11:00", "2026-05-14 13:00", freq="1h")
    poa = loader.get_per_ws(ts, "WB01")
    assert isinstance(poa, pd.Series)
    assert len(poa) == 3
    # Noon should have non-zero POA
    assert poa.loc["2026-05-14 12:00:00"] > 500.0


def test_get_per_ws_unmapped_wb_returns_nan(synthetic_pyranometer_xlsx):
    """WB tidak ada di mapping -> all-NaN Series + warning."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    ts = pd.date_range("2026-05-14 12:00", periods=1, freq="1h")
    poa = loader.get_per_ws(ts, "WB99")  # tidak ada di mapping
    assert poa.isna().all()


def test_get_per_ws_handles_ws2_nan_gap(synthetic_pyranometer_xlsx):
    """WS-2 sintetik punya NaN gap di 08-14. WB05->WS-2.

    Wave 11 hotfix #4: default `fallback_to_avg=True` mengisi NaN dari avg.
    Untuk verify strict-NaN behavior, opt-out via fallback_to_avg=False.
    """
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    # Strict mode (no fallback): WS-2 NaN at noon -> NaN.
    poa_wb05_strict = loader.get_per_ws(ts, "WB05", fallback_to_avg=False)
    assert pd.isna(poa_wb05_strict.iloc[0])
    # Default mode (fallback ON): WS-2 NaN at noon -> filled from avg, not NaN.
    poa_wb05_default = loader.get_per_ws(ts, "WB05")
    assert not pd.isna(poa_wb05_default.iloc[0])
    # Sanity: WB01 (WS-5) at same noon should NOT be NaN regardless of mode.
    poa_wb01 = loader.get_per_ws(ts, "WB01")
    assert not pd.isna(poa_wb01.iloc[0])


# ---------- get_avg ----------


def test_get_avg_returns_avg_column(synthetic_pyranometer_xlsx):
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    avg = loader.get_avg(ts)
    assert avg.iloc[0] > 0.0


# ---------- Reindex tolerance ----------


def test_reindex_nearest_with_tolerance(synthetic_pyranometer_xlsx):
    """Source 5-min, query off-grid -> nearest match dalam 2-min tolerance."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    # Query 12:02 (off-grid, nearest = 12:00 atau 12:05)
    ts = pd.DatetimeIndex(["2026-05-14 12:02:00"])
    poa = loader.get_per_ws(ts, "WB01")
    assert not pd.isna(poa.iloc[0]), "12:02 harus match nearest 12:00 atau 12:05"


def test_reindex_outside_tolerance_returns_nan(synthetic_pyranometer_xlsx):
    """Query 1 jam di luar source range (after 23:55) -> NaN."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    ts = pd.DatetimeIndex(["2027-01-01 12:00:00"])  # tahun depan, jauh
    poa = loader.get_per_ws(ts, "WB01")
    assert pd.isna(poa.iloc[0])


# ---------- File not found ----------


def test_loader_raises_for_missing_xlsx():
    with pytest.raises(FileNotFoundError):
        PyranometerLoader("nonexistent_xlsx_file.xlsx", ws_to_wb=WS_TO_WB_MAP)


# ---------- Wave 11 hotfix #4: per-WS -> avg fallback ----------


def test_get_per_ws_fallback_to_avg_fills_nan_positions(synthetic_pyranometer_xlsx):
    """Wave 11 hotfix #4: WS-2 NaN di hours 8-14 (fixture); fallback fills from avg.

    Mirrors user's real-world scenario di mana ``POA PLTS IKN 2026.xlsx``
    punya WS-2 column all-NaN untuk 2026-05-14. WB05/WB07 -> WS-2.
    Tanpa fallback, get_per_ws('WB05') returns NaN di posisi tsb -> M2b
    detector fan-out. Dengan fallback default ON, posisi NaN diisi dari
    kolom ``Rata-rata WS 1 - WS 5``.
    """
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    # Hours 10-13 = WS-2 NaN region di synthetic fixture (hours 8-14).
    ts = pd.date_range("2026-05-14 10:00", "2026-05-14 13:00", freq="5min")
    poa_wb05 = loader.get_per_ws(ts, "WB05")  # WB05 -> WS-2

    assert poa_wb05.notna().all(), "fallback should fill all NaN from avg"
    assert (poa_wb05 > 0).all(), "filled values from avg should be positive at noon"
    # Sanity attrs.
    assert poa_wb05.attrs["ws_label"] == "WS-2"
    assert poa_wb05.attrs["fallback_filled"] == len(ts)
    assert poa_wb05.attrs["fallback_total"] == len(ts)


def test_get_per_ws_no_fallback_preserves_nan_when_disabled(synthetic_pyranometer_xlsx):
    """Wave 11 hotfix #4: opt-out via fallback_to_avg=False preserves original NaN."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    ts = pd.date_range("2026-05-14 10:00", "2026-05-14 13:00", freq="5min")
    poa_wb05 = loader.get_per_ws(ts, "WB05", fallback_to_avg=False)

    assert poa_wb05.isna().all(), "WS-2 NaN region preserved when fallback disabled"
    assert poa_wb05.attrs["fallback_filled"] == 0


def test_get_per_ws_fallback_skipped_when_per_ws_has_data(synthetic_pyranometer_xlsx):
    """Wave 11 hotfix #4: fallback only fills NaN positions; valid per-WS data
    pass-through unchanged."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    # WB06 -> WS-3, which has valid data throughout the day.
    ts = pd.date_range("2026-05-14 10:00", "2026-05-14 13:00", freq="5min")
    poa_wb06 = loader.get_per_ws(ts, "WB06")  # WB06 -> WS-3

    assert poa_wb06.notna().all()
    assert poa_wb06.attrs["ws_label"] == "WS-3"
    # No NaN positions, so no fill.
    assert poa_wb06.attrs["fallback_filled"] == 0
