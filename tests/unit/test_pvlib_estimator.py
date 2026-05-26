"""Test pv_pipeline.poa.pvlib_estimator: PvlibClearSkyEstimator 3 models."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.poa.pvlib_estimator import (
    DEFAULT_ALBEDO,
    DEFAULT_TRANSPOSITION_MODEL,
    MODEL_HAURWITZ,
    MODEL_INEICHEN,
    MODEL_SOLIS,
    PvlibClearSkyEstimator,
    SUPPORTED_MODELS,
)


# PLTS-IKN site params (sama default site_geometry.yaml)
SITE_PARAMS = dict(
    latitude=-0.9911713315158186,
    longitude=116.63811127764585,
    elevation_m=85.0,
    timezone="Asia/Makassar",
    tilt_deg=10.0,
    azimuth_deg=0.0,
)


@pytest.fixture(scope="module")
def estimator():
    """Single estimator instance reused across tests (pvlib init agak mahal)."""
    return PvlibClearSkyEstimator(**SITE_PARAMS, albedo=DEFAULT_ALBEDO)


# ---------- Basic structure ----------


def test_supported_models_constant():
    """3 model didukung per spec user."""
    assert MODEL_INEICHEN == "ineichen"
    assert MODEL_SOLIS == "simplified_solis"
    assert MODEL_HAURWITZ == "haurwitz"
    assert len(SUPPORTED_MODELS) == 3


def test_default_transposition_model_is_perez():
    """Fase 2: DEFAULT_TRANSPOSITION_MODEL == 'perez' per Master Context."""
    assert DEFAULT_TRANSPOSITION_MODEL == "perez"


def test_estimator_uses_perez_by_default():
    """Estimator tanpa override transposition_model -> attribute == 'perez'."""
    est = PvlibClearSkyEstimator(**SITE_PARAMS, albedo=DEFAULT_ALBEDO)
    assert est.transposition_model == "perez"


def test_perez_noon_poa_valid(estimator):
    """Estimator default (Perez transposition) emit POA > 500 W/m^2 @ tropical noon."""
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    poa = estimator.estimate(ts, model=MODEL_INEICHEN)
    # Internal transposition Perez; output harus valid (non-NaN, positive).
    assert not np.isnan(poa.iloc[0])
    assert poa.iloc[0] > 500.0, f"Perez+Ineichen noon POA={poa.iloc[0]}, expected >500"


def test_default_transposition_model_is_perez():
    """Fase 2: DEFAULT_TRANSPOSITION_MODEL == 'perez' per Master Context."""
    assert DEFAULT_TRANSPOSITION_MODEL == "perez"


def test_estimator_uses_perez_by_default():
    """Estimator tanpa override transposition_model -> attribute == 'perez'."""
    est = PvlibClearSkyEstimator(**SITE_PARAMS, albedo=DEFAULT_ALBEDO)
    assert est.transposition_model == "perez"


def test_perez_noon_poa_valid(estimator):
    """Estimator default (Perez) emit POA > 500 W/m^2 @ tropical noon, no NaN."""
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    poa = estimator.estimate(ts, model=MODEL_INEICHEN)
    # Estimator pakai Perez transposition (default) untuk ineichen clear-sky.
    assert not np.isnan(poa.iloc[0])
    assert poa.iloc[0] > 500.0, f"Perez+Ineichen noon POA={poa.iloc[0]}, expected >500"


def test_estimator_init_stores_params(estimator):
    """Init store all geometry params + Location object."""
    assert estimator.latitude == pytest.approx(SITE_PARAMS["latitude"])
    assert estimator.tilt_deg == 10.0
    assert estimator.azimuth_deg == 0.0
    assert estimator.albedo == DEFAULT_ALBEDO
    assert estimator.location is not None


# ---------- estimate() single model ----------


def test_estimate_ineichen_shape(estimator):
    """Ineichen: output Series shape match input timestamps."""
    ts = pd.date_range("2026-05-14 06:00", "2026-05-14 18:00", freq="1h")
    poa = estimator.estimate(ts, model=MODEL_INEICHEN)
    assert isinstance(poa, pd.Series)
    assert len(poa) == 13


def test_estimate_solis_returns_positive_at_noon(estimator):
    """Simplified Solis @ noon harus > 500 W/m^2 (clear-sky tropical noon)."""
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    poa = estimator.estimate(ts, model=MODEL_SOLIS)
    assert poa.iloc[0] > 500.0, f"Solis noon POA={poa.iloc[0]}, expected >500"


def test_estimate_haurwitz_zero_at_night(estimator):
    """Haurwitz pre-sunrise (04:00) harus ~0 (sun below horizon)."""
    ts = pd.DatetimeIndex(["2026-05-14 04:00:00"])
    poa = estimator.estimate(ts, model=MODEL_HAURWITZ)
    assert poa.iloc[0] == pytest.approx(0.0, abs=1.0)


def test_estimate_invalid_model_raises(estimator):
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    with pytest.raises(ValueError, match="unsupported model"):
        estimator.estimate(ts, model="not_a_real_model")


# ---------- estimate_all_models() side-by-side ----------


def test_estimate_all_models_returns_df_with_3_cols(estimator):
    """Output DataFrame harus punya 3 model columns."""
    ts = pd.date_range("2026-05-14 12:00", periods=1, freq="1h")
    df = estimator.estimate_all_models(ts)
    assert len(df.columns) == 3
    assert "pvlib_clearsky_ineichen" in df.columns
    assert "pvlib_clearsky_simplified_solis" in df.columns
    assert "pvlib_clearsky_haurwitz" in df.columns


def test_estimate_all_models_noon_comparison(estimator):
    """Saat noon, ke-3 model harus emit POA > 500 (clear-sky tropical)."""
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    df = estimator.estimate_all_models(ts)
    for col in df.columns:
        assert df[col].iloc[0] > 500.0, f"{col} noon POA={df[col].iloc[0]}, expected >500"


# ---------- albedo_provider dynamic ----------


class _MockAlbedoProvider:
    """Mock callable albedo provider."""

    def get_albedo(self, timestamps):
        return pd.Series([0.15] * len(timestamps), index=pd.DatetimeIndex(timestamps))


def test_estimate_with_dynamic_albedo_provider():
    """albedo_provider callable di-pass ke estimator, di-resolve di _resolve_albedo."""
    est = PvlibClearSkyEstimator(
        **SITE_PARAMS, albedo=DEFAULT_ALBEDO, albedo_provider=_MockAlbedoProvider(),
    )
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    poa = est.estimate(ts, model=MODEL_INEICHEN)
    # Hasil POA should be valid (dynamic albedo handled OK).
    assert poa.iloc[0] > 0.0
    assert not np.isnan(poa.iloc[0])


def test_estimator_falls_back_to_static_albedo_when_no_provider(estimator):
    """Tanpa albedo_provider, estimator pakai static self.albedo."""
    assert estimator.albedo_provider is None
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    poa = estimator.estimate(ts, model=MODEL_INEICHEN)
    assert poa.iloc[0] > 500.0  # masih emit valid value


# ---------- Naive vs tz-aware timestamps ----------


def test_estimate_accepts_naive_timestamps(estimator):
    """Naive timestamps -> dilocalize ke site timezone internally."""
    ts_naive = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    poa = estimator.estimate(ts_naive, model=MODEL_INEICHEN)
    assert poa.index.tz is None  # output naive
    assert poa.iloc[0] > 500.0


def test_estimate_output_index_matches_input(estimator):
    """Output Series index match input timestamps (naive)."""
    ts = pd.DatetimeIndex([
        "2026-05-14 06:00:00",
        "2026-05-14 12:00:00",
        "2026-05-14 18:00:00",
    ])
    poa = estimator.estimate(ts, model=MODEL_INEICHEN)
    assert len(poa) == 3
    assert poa.index.equals(ts)
