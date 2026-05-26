"""Test pv_pipeline.preprocessing: Hampel outlier filter wrapper (Fase 2 Wave 3)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.preprocessing import (
    DEFAULT_HAMPEL_MAX_DEVIATION,
    DEFAULT_HAMPEL_WINDOW,
    apply_hampel_to_pv_dataframe,
    clean_pv_string_columns,
    clean_with_hampel,
    hampel_outlier_mask,
)


# ---------- Constants ----------


def test_default_window_and_threshold():
    """Defaults: window=15 (~75 min @ 5-min), max_deviation=3.0 (3-sigma MAD)."""
    assert DEFAULT_HAMPEL_WINDOW == 15
    assert DEFAULT_HAMPEL_MAX_DEVIATION == 3.0


# ---------- hampel_outlier_mask ----------


@pytest.fixture
def synthetic_v_series_with_spikes():
    """Sinus-ramping V curve + 3 obvious spikes injected at idx 50/100/150."""
    rng = np.random.default_rng(2026)
    n = 200
    t = pd.date_range("2026-05-14 06:00", periods=n, freq="5min")
    base = 1200.0 + 50.0 * np.sin(np.linspace(0, np.pi, n)) + rng.normal(0, 5, n)
    series = pd.Series(base, index=t, name="PV5 input voltage(V)")
    for i in (50, 100, 150):
        series.iloc[i] += 200.0
    return series


def test_hampel_mask_flags_obvious_spikes(synthetic_v_series_with_spikes):
    """3 spike (+200V from ~1200V base, MAD ~5) harus flagged."""
    mask = hampel_outlier_mask(synthetic_v_series_with_spikes, window=15, max_deviation=3.0)
    assert isinstance(mask, pd.Series)
    assert mask.dtype == bool
    assert mask.name == "hampel_outlier"
    for idx in (50, 100, 150):
        assert bool(mask.iloc[idx]), f"spike @ idx {idx} not flagged"


def test_hampel_mask_clean_series_no_false_positive():
    """Clean sinus + small noise (no spike) -> very few False positives."""
    rng = np.random.default_rng(123)
    n = 200
    t = pd.date_range("2026-05-14 06:00", periods=n, freq="5min")
    base = 1200.0 + 50.0 * np.sin(np.linspace(0, np.pi, n)) + rng.normal(0, 5, n)
    series = pd.Series(base, index=t)

    mask = hampel_outlier_mask(series, window=15, max_deviation=3.0)
    assert mask.sum() < 0.05 * n, f"too many false positives: {int(mask.sum())}/{n}"


def test_hampel_mask_aligns_to_input_index():
    """Output mask index == input index."""
    idx = pd.date_range("2026-05-14 12:00", periods=20, freq="5min")
    series = pd.Series(np.linspace(100.0, 110.0, 20), index=idx)
    mask = hampel_outlier_mask(series)
    assert mask.index.equals(idx)


def test_hampel_mask_empty_series_returns_empty():
    """Empty input -> empty bool Series (no crash)."""
    empty = pd.Series([], dtype="float64")
    mask = hampel_outlier_mask(empty)
    assert isinstance(mask, pd.Series)
    assert mask.empty
    assert mask.dtype == bool


def test_hampel_mask_raises_on_non_series():
    """Non-Series input raises TypeError."""
    with pytest.raises(TypeError, match="expects pd.Series"):
        hampel_outlier_mask([1.0, 2.0, 3.0])  # type: ignore[arg-type]


# ---------- clean_with_hampel ----------


def test_clean_with_hampel_replaces_outliers_with_nan(synthetic_v_series_with_spikes):
    """Outliers default-replaced dengan NaN, length + index preserved."""
    cleaned = clean_with_hampel(synthetic_v_series_with_spikes, window=15, max_deviation=3.0)
    assert isinstance(cleaned, pd.Series)
    assert len(cleaned) == len(synthetic_v_series_with_spikes)
    assert cleaned.index.equals(synthetic_v_series_with_spikes.index)
    for idx in (50, 100, 150):
        assert np.isnan(cleaned.iloc[idx]), f"spike @ {idx} not replaced"


def test_clean_with_hampel_custom_replace_value(synthetic_v_series_with_spikes):
    """replace_with=0.0 -> outlier positions = 0.0 (bukan NaN)."""
    cleaned = clean_with_hampel(
        synthetic_v_series_with_spikes, window=15, max_deviation=3.0, replace_with=0.0,
    )
    for idx in (50, 100, 150):
        assert cleaned.iloc[idx] == 0.0


def test_clean_with_hampel_does_not_mutate_input(synthetic_v_series_with_spikes):
    """Original series tidak berubah setelah clean."""
    spike_value_before = synthetic_v_series_with_spikes.iloc[50]
    _ = clean_with_hampel(synthetic_v_series_with_spikes, window=15)
    assert synthetic_v_series_with_spikes.iloc[50] == spike_value_before


# ---------- clean_pv_string_columns ----------


@pytest.fixture
def df_2_pv_strings_with_spikes():
    """Synthetic combined_df dengan PV1 + PV2 V/I cols. PV1 V punya 1 spike."""
    rng = np.random.default_rng(7)
    n = 50
    t = pd.date_range("2026-05-14 09:00", periods=n, freq="5min")
    base_v = 1200.0 + rng.normal(0, 5, n)
    base_i = 10.0 + rng.normal(0, 0.2, n)
    df = pd.DataFrame({
        "Start Time": t,
        "PV1 input voltage(V)": base_v,
        "PV1 input current(A)": base_i,
        "PV2 input voltage(V)": base_v + 5.0,
        "PV2 input current(A)": base_i + 0.5,
    })
    df.loc[25, "PV1 input voltage(V)"] = 1500.0
    return df


def test_clean_pv_string_columns_auto_discovers_4_cols(df_2_pv_strings_with_spikes):
    """Auto-detect 2 PV strings -> 4 kolom (V+I per string)."""
    out = clean_pv_string_columns(df_2_pv_strings_with_spikes, pv_max=2, window=15)
    assert len(out) == 4
    assert "PV1 input voltage(V)" in out
    assert "PV1 input current(A)" in out
    assert "PV2 input voltage(V)" in out
    assert "PV2 input current(A)" in out


def test_clean_pv_string_columns_replaces_pv1_spike(df_2_pv_strings_with_spikes):
    """PV1 V spike di idx 25 -> NaN di output."""
    out = clean_pv_string_columns(df_2_pv_strings_with_spikes, pv_max=2, window=15)
    pv1_v_cleaned = out["PV1 input voltage(V)"]
    assert np.isnan(pv1_v_cleaned.iloc[25])
    assert not np.isnan(out["PV2 input voltage(V)"].iloc[25])


def test_clean_pv_string_columns_skips_missing_columns(df_2_pv_strings_with_spikes):
    """Override columns dengan list yang punya 1 valid + 1 missing -> warn + skip missing."""
    with pytest.warns(UserWarning, match="not in df"):
        out = clean_pv_string_columns(
            df_2_pv_strings_with_spikes,
            columns=["PV1 input voltage(V)", "PV99 input voltage(V)"],
        )
    assert "PV1 input voltage(V)" in out
    assert "PV99 input voltage(V)" not in out


# ---------- Wave 9: apply_hampel_to_pv_dataframe ----------


def test_apply_hampel_to_pv_dataframe_returns_new_df(df_2_pv_strings_with_spikes):
    """Output dataframe baru (not in-place), V/I cols cleaned, audit ada."""
    orig_pv1_v_spike = df_2_pv_strings_with_spikes.loc[25, "PV1 input voltage(V)"]
    cleaned_df, audit = apply_hampel_to_pv_dataframe(
        df_2_pv_strings_with_spikes, pv_max=2, window=15,
    )
    # Input not mutated.
    assert df_2_pv_strings_with_spikes.loc[25, "PV1 input voltage(V)"] == orig_pv1_v_spike
    # Spike removed in output.
    import numpy as np
    assert np.isnan(cleaned_df.loc[25, "PV1 input voltage(V)"])
    # Audit emitted (4 cols: PV1 V/I + PV2 V/I).
    assert len(audit) == 4
    cols_in_audit = {a["column"] for a in audit}
    assert cols_in_audit == {
        "PV1 input voltage(V)", "PV1 input current(A)",
        "PV2 input voltage(V)", "PV2 input current(A)",
    }


def test_apply_hampel_to_pv_dataframe_audit_counts(df_2_pv_strings_with_spikes):
    """PV1 V has injected spike + edge-window FP, others clean."""
    _, audit = apply_hampel_to_pv_dataframe(
        df_2_pv_strings_with_spikes, pv_max=2, window=15,
    )
    pv1_v = next(a for a in audit if a["column"] == "PV1 input voltage(V)")
    assert pv1_v["n_outliers"] >= 1
    assert pv1_v["total_samples"] == 50
    assert pv1_v["pct_outliers"] > 0


def test_apply_hampel_to_pv_dataframe_no_pv_cols_returns_empty_audit():
    """DF tanpa PV columns -> cleaned_df = copy, audit empty."""
    df = pd.DataFrame({"Start Time": pd.date_range("2026-05-14", periods=5, freq="5min")})
    cleaned_df, audit = apply_hampel_to_pv_dataframe(df, pv_max=5)
    assert audit == []
    assert list(cleaned_df.columns) == list(df.columns)


def test_clean_pv_string_columns_empty_df_returns_empty_dict():
    """DF tanpa PV columns -> dict kosong (no crash)."""
    df_empty = pd.DataFrame({"Start Time": pd.date_range("2026-05-14", periods=3, freq="5min")})
    out = clean_pv_string_columns(df_empty, pv_max=5)
    assert out == {}
