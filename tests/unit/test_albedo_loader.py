"""Test pv_pipeline.poa.albedo_loader: AlbedoLoader (NSRDB TMY xlsx)."""
from __future__ import annotations

import pandas as pd
import pytest

from pv_pipeline.poa.albedo_loader import AlbedoLoader


# ---------- Constructor + parsing ----------


def test_loader_parses_synthetic_xlsx(synthetic_albedo_xlsx):
    """Load synthetic xlsx -> Series ber-index DatetimeIndex naive."""
    loader = AlbedoLoader(synthetic_albedo_xlsx)
    assert not loader.series.empty
    assert loader.series.index.tz is None
    assert len(loader.series) == 48  # 1 day @ 30-min


def test_loader_value_range(synthetic_albedo_xlsx):
    """Synthetic value = 0.15 fixed -> all values di range 0.13-0.17."""
    loader = AlbedoLoader(synthetic_albedo_xlsx)
    assert loader.series.min() == pytest.approx(0.15)
    assert loader.series.max() == pytest.approx(0.15)


# ---------- get_albedo reindex ----------


def test_get_albedo_returns_series_for_5min_consumer(synthetic_albedo_xlsx):
    """30-min source, query 5-min interval -> nearest match dalam tolerance."""
    loader = AlbedoLoader(synthetic_albedo_xlsx)
    ts = pd.date_range("2026-05-14 12:00", "2026-05-14 13:00", freq="5min")
    alb = loader.get_albedo(ts)
    assert len(alb) == 13
    # Tolerance comparison (approx 0.15) element-wise via numpy.
    import numpy as np
    assert np.allclose(alb.values, 0.15)


def test_get_albedo_out_of_range_returns_nan(synthetic_albedo_xlsx):
    """Query tahun 2027 jauh dari source 2026-05-14 -> NaN."""
    loader = AlbedoLoader(synthetic_albedo_xlsx)
    ts = pd.DatetimeIndex(["2027-12-31 12:00:00"])
    alb = loader.get_albedo(ts)
    assert pd.isna(alb.iloc[0])


def test_get_albedo_with_tz_aware_input_normalizes(synthetic_albedo_xlsx):
    """Tz-aware input timestamps -> di-strip tz untuk match naive source."""
    loader = AlbedoLoader(synthetic_albedo_xlsx)
    ts_aware = pd.DatetimeIndex(["2026-05-14 12:00:00"]).tz_localize("Asia/Makassar")
    alb = loader.get_albedo(ts_aware)
    assert alb.iloc[0] == pytest.approx(0.15)


# ---------- Callable interface ----------


def test_loader_is_callable(synthetic_albedo_xlsx):
    """Loader bisa di-call langsung sebagai albedo_provider untuk pvlib estimator."""
    loader = AlbedoLoader(synthetic_albedo_xlsx)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    alb_call = loader(ts)
    alb_method = loader.get_albedo(ts)
    assert alb_call.iloc[0] == alb_method.iloc[0]


# ---------- File not found ----------


def test_loader_raises_for_missing_xlsx():
    with pytest.raises(FileNotFoundError):
        AlbedoLoader("nonexistent_albedo.xlsx")


def test_loader_raises_for_missing_timestamp_col(tmp_path):
    """Sheet tanpa 'Date/Time' column raises KeyError."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Wrong Column Name", "Surface Albedo"])
    ws.append([pd.Timestamp("2026-05-14 12:00:00").to_pydatetime(), 0.15])
    bad_xlsx = tmp_path / "bad_albedo.xlsx"
    wb.save(str(bad_xlsx))

    with pytest.raises(KeyError, match="timestamp column"):
        AlbedoLoader(str(bad_xlsx))
