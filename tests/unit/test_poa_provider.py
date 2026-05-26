"""Test pv_pipeline.poa.provider: POAProvider orchestrator (loader + estimator)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.poa.loader import PyranometerLoader
from pv_pipeline.poa.provider import (
    ALL_NON_AUTO_SOURCES,
    ALL_SOURCES,
    DEFAULT_AUTO_FALLBACK_CHAIN,
    POAProvider,
    SOURCE_AUTO,
    SOURCE_PVLIB_INEICHEN,
    SOURCE_PYRANOMETER_AVG,
    SOURCE_PYRANOMETER_PER_WS,
)


# ---------- Source identifier constants ----------


def test_all_sources_constant():
    assert len(ALL_SOURCES) == 6  # 5 explicit + auto
    assert SOURCE_AUTO in ALL_SOURCES
    assert SOURCE_PYRANOMETER_PER_WS in ALL_NON_AUTO_SOURCES
    assert SOURCE_PVLIB_INEICHEN in ALL_NON_AUTO_SOURCES


def test_default_auto_fallback_chain():
    """Default fallback: per_ws -> avg -> ineichen."""
    chain = DEFAULT_AUTO_FALLBACK_CHAIN
    assert chain[0] == SOURCE_PYRANOMETER_PER_WS
    assert chain[1] == SOURCE_PYRANOMETER_AVG
    assert chain[2] == SOURCE_PVLIB_INEICHEN


def test_provider_rejects_auto_in_fallback_chain():
    """Auto di fallback chain -> ValueError (akan infinite loop)."""
    with pytest.raises(ValueError, match="auto_fallback_chain tidak boleh berisi 'auto'"):
        POAProvider(auto_fallback_chain=[SOURCE_AUTO, SOURCE_PYRANOMETER_PER_WS])


# ---------- get_poa per source ----------


WS_TO_WB_MAP = {
    "WS-1": ["WB08", "WB09", "WB10"],
    "WS-2": ["WB05", "WB07"],
    "WS-3": ["WB06"],
    "WS-4": ["WB03", "WB04"],
    "WS-5": ["WB01", "WB02"],
}


@pytest.fixture
def provider_with_pyranometer_only(synthetic_pyranometer_xlsx):
    """POAProvider hanya dengan pyranometer (no pvlib)."""
    loader = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    return POAProvider(pyranometer=loader, pvlib_estimator=None)


def test_get_poa_pyranometer_per_ws(provider_with_pyranometer_only):
    """Source=per_ws, WB01 -> WS-5, noon = 1000 (sin curve peak)."""
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    poa = provider_with_pyranometer_only.get_poa(ts, "WB01", source=SOURCE_PYRANOMETER_PER_WS)
    assert poa.iloc[0] == pytest.approx(1000.0, abs=1.0)


def test_get_poa_pyranometer_avg(provider_with_pyranometer_only):
    """Source=avg, returns site-wide average column."""
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    poa = provider_with_pyranometer_only.get_poa(ts, "WB01", source=SOURCE_PYRANOMETER_AVG)
    # At noon, WS-2 is NaN (synthetic gap), avg = mean of 4 non-NaN = 1000
    assert poa.iloc[0] == pytest.approx(1000.0, abs=10.0)


def test_get_poa_no_pyranometer_returns_nan(synthetic_pyranometer_xlsx):
    """POAProvider tanpa pyranometer -> source pyranometer_* return NaN."""
    prov = POAProvider(pyranometer=None, pvlib_estimator=None)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    poa = prov.get_poa(ts, "WB01", source=SOURCE_PYRANOMETER_PER_WS)
    assert pd.isna(poa.iloc[0])


def test_get_poa_invalid_source_raises(provider_with_pyranometer_only):
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    with pytest.raises(ValueError, match="unsupported source"):
        provider_with_pyranometer_only.get_poa(ts, "WB01", source="bad_source")


# ---------- auto fallback chain ----------


def test_auto_fallback_uses_per_ws_when_available(provider_with_pyranometer_only):
    """WB01 (-> WS-5) di noon, per_ws non-NaN -> auto = per_ws value."""
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    auto = provider_with_pyranometer_only.get_poa(ts, "WB01", source=SOURCE_AUTO)
    per_ws = provider_with_pyranometer_only.get_poa(ts, "WB01", source=SOURCE_PYRANOMETER_PER_WS)
    assert auto.iloc[0] == per_ws.iloc[0]


def test_auto_fallback_to_avg_when_per_ws_nan(provider_with_pyranometer_only):
    """WB05 (-> WS-2) di noon, WS-2 NaN, fallback ke avg.

    Wave 11 hotfix #4: per_ws sekarang punya intrinsic fallback ke avg
    (default ON di PyranometerLoader.get_per_ws). Jadi:
      - per_ws untuk WB05 di noon: avg-filled (BUKAN NaN, kecuali strict).
      - auto chain pakai per_ws first; karena sudah avg-filled, auto = avg.
    """
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    per_ws = provider_with_pyranometer_only.get_poa(ts, "WB05", source=SOURCE_PYRANOMETER_PER_WS)
    avg = provider_with_pyranometer_only.get_poa(ts, "WB05", source=SOURCE_PYRANOMETER_AVG)
    auto = provider_with_pyranometer_only.get_poa(ts, "WB05", source=SOURCE_AUTO)

    # per_ws kini auto-filled dari avg (intrinsic fallback Wave 11 hotfix #4).
    assert not pd.isna(per_ws.iloc[0]), "WB05 per_ws sekarang avg-filled, bukan NaN"
    assert per_ws.iloc[0] == pytest.approx(avg.iloc[0], abs=0.01)
    # auto chain pakai per_ws first -> dapat avg-filled value, ekuivalen avg.
    assert auto.iloc[0] == pytest.approx(avg.iloc[0], abs=0.01)


# ---------- get_poa_all_sources ----------


def test_get_poa_all_sources_returns_dataframe(provider_with_pyranometer_only):
    """get_poa_all_sources -> DataFrame 5 non-auto + 1 auto kolom."""
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    df = provider_with_pyranometer_only.get_poa_all_sources(ts, "WB01")
    assert len(df.columns) == 6
    assert SOURCE_AUTO in df.columns
    assert SOURCE_PYRANOMETER_PER_WS in df.columns


def test_get_poa_all_sources_no_auto_flag(provider_with_pyranometer_only):
    """include_auto=False -> drop auto col."""
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    df = provider_with_pyranometer_only.get_poa_all_sources(ts, "WB01", include_auto=False)
    assert SOURCE_AUTO not in df.columns
    assert len(df.columns) == 5


# ---------- albedo_loader property ----------


def test_albedo_loader_property_returns_none_without_pvlib():
    """Provider tanpa pvlib -> albedo_loader = None."""
    prov = POAProvider(pyranometer=None, pvlib_estimator=None)
    assert prov.albedo_loader is None


# ---------- Fase 2 Wave 2: solar_elevation passthrough ----------


def test_get_solar_elevation_no_pvlib_returns_nan_series():
    """Provider tanpa pvlib -> get_solar_elevation all-NaN Series."""
    prov = POAProvider(pyranometer=None, pvlib_estimator=None)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00", "2026-05-14 18:00:00"])
    elev = prov.get_solar_elevation(ts)
    assert isinstance(elev, pd.Series)
    assert len(elev) == 2
    assert elev.isna().all()
    assert elev.name == "solar_elevation_deg"


def test_get_solar_elevation_with_pvlib_positive_at_noon():
    """Provider dengan pvlib -> elevation positive @ tropical noon."""
    from pv_pipeline.poa.pvlib_estimator import PvlibClearSkyEstimator
    est = PvlibClearSkyEstimator(
        latitude=-0.9911713315158186,
        longitude=116.63811127764585,
        elevation_m=85.0,
        timezone="Asia/Makassar",
        tilt_deg=10.0,
        azimuth_deg=0.0,
    )
    prov = POAProvider(pyranometer=None, pvlib_estimator=est)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    elev = prov.get_solar_elevation(ts)
    assert elev.iloc[0] > 60.0, f"noon elev={elev.iloc[0]}, expected >60 deg tropical"


def test_get_solar_elevation_negative_at_night():
    """Pvlib elevation < 0 saat malam (sun below horizon)."""
    from pv_pipeline.poa.pvlib_estimator import PvlibClearSkyEstimator
    est = PvlibClearSkyEstimator(
        latitude=-0.9911713315158186,
        longitude=116.63811127764585,
        elevation_m=85.0,
        timezone="Asia/Makassar",
        tilt_deg=10.0,
        azimuth_deg=0.0,
    )
    prov = POAProvider(pyranometer=None, pvlib_estimator=est)
    ts = pd.DatetimeIndex(["2026-05-14 02:00:00"])  # 2am WITA = night
    elev = prov.get_solar_elevation(ts)
    assert elev.iloc[0] < 0.0, f"night elev={elev.iloc[0]}, expected <0"
