"""Test pv_pipeline.generation.loader: GenerationLoader (Wave 7)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.generation import GenerationLoader


# ---------- Synthetic xlsx fixture ----------


@pytest.fixture
def synthetic_generation_xlsx(tmp_path):
    """Mimics Summary (PV) layout: row 0 section labels, row 1 col names, row 2+ data."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary (PV)"

    # Row 0: section labels (sparse). Updated for Wave 11 (Q + R columns).
    ws.append(
        [None, "STS Generation (kWh)"]
        + [None] * 12
        + ["Propotional Setpoint (kW)", None, None, None]
    )

    # Row 1: actual column names.
    cols = ["Date"] + [f"WB{n:02d}" for n in range(1, 11)] + [
        "Total STS Generation (kWh)",
        "PAE Energy (kWh) from 00.00 to 24.00",
        "Generation STS (kWh)",
        "Busbar 1 (WB 01 - WB 5)",
        "Busbar 2 (WB 06 - WB 10)",
        "Deem Dispatch (kWh)",  # Wave 11
        "Curtailment",          # Wave 11
    ]
    ws.append(cols)

    # Row 2+: 5 days of data starting 2026-05-10.
    dates = pd.date_range("2026-05-10", periods=5, freq="D")
    # Curtailment pattern: alternating Yes/No untuk uji parsing.
    curtailment_pattern = ["No", "Yes", "No", "Yes", "No"]
    deem_dispatch_pattern = [0.0, 1500.0, 0.0, 2200.0, 500.0]
    for i, dt in enumerate(dates):
        wb_vals = [1000.0 + 100 * i + 10 * n for n in range(10)]
        total = sum(wb_vals)
        pae = total * 1.05
        gen_sts = max(total, pae)
        busbar1 = 23000.0
        busbar2 = 25000.0
        ws.append(
            [dt.to_pydatetime()]
            + wb_vals
            + [total, pae, gen_sts, busbar1, busbar2,
               deem_dispatch_pattern[i], curtailment_pattern[i]]
        )

    path = tmp_path / "synthetic_generation.xlsx"
    wb.save(str(path))
    return str(path)


# ---------- Basic load ----------


def test_loader_parses_header_row_1(synthetic_generation_xlsx):
    """header_row=1 skips section labels row -> 5 data rows."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    assert len(loader.df) == 5
    for wb in [f"WB{n:02d}" for n in range(1, 11)]:
        assert wb in loader.df.columns
    for key in ["total_kwh", "pae_kwh", "generation_sts_kwh",
                "busbar1_setpoint_kw", "busbar2_setpoint_kw"]:
        assert key in loader.df.columns


def test_loader_indexed_by_date(synthetic_generation_xlsx):
    """Date column became DatetimeIndex, sort ascending."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    assert isinstance(loader.df.index, pd.DatetimeIndex)
    assert loader.df.index[0] == pd.Timestamp("2026-05-10")
    assert loader.df.index[-1] == pd.Timestamp("2026-05-14")
    assert loader.df.index.is_monotonic_increasing


def test_loader_renames_long_columns_to_short_keys(synthetic_generation_xlsx):
    """Long xlsx column names renamed to short Python-friendly keys."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    assert "Total STS Generation (kWh)" not in loader.df.columns
    assert "total_kwh" in loader.df.columns
    assert "PAE Energy (kWh) from 00.00 to 24.00" not in loader.df.columns
    assert "pae_kwh" in loader.df.columns


def test_loader_missing_xlsx_raises():
    with pytest.raises(FileNotFoundError, match="not found"):
        GenerationLoader(xlsx_path="nonexistent.xlsx")


def test_loader_missing_date_col_raises(tmp_path):
    """Sheet without Date col -> KeyError."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary (PV)"
    ws.append([None] * 5)
    ws.append(["NotADate", "WB01", "WB02", "WB03", "WB04"])
    ws.append(["x", 1, 2, 3, 4])
    path = tmp_path / "no_date_col.xlsx"
    wb.save(str(path))
    with pytest.raises(KeyError, match="Date"):
        GenerationLoader(xlsx_path=str(path))


# ---------- get_daily ----------


def test_get_daily_wb01(synthetic_generation_xlsx):
    """WB01 daily: 1000 + 100*i for i in 0..4."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    ts = pd.date_range("2026-05-10", periods=5, freq="D")
    s = loader.get_daily(ts, "WB01")
    assert s.iloc[0] == pytest.approx(1000.0)
    assert s.iloc[4] == pytest.approx(1400.0)


def test_get_daily_total_kwh(synthetic_generation_xlsx):
    """Total = sum WB01..WB10 day 0."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    ts = pd.date_range("2026-05-10", periods=1, freq="D")
    s = loader.get_daily(ts, "total_kwh")
    expected = sum(1000.0 + 10 * n for n in range(10))
    assert s.iloc[0] == pytest.approx(expected)


def test_get_daily_missing_date_returns_nan(synthetic_generation_xlsx):
    """Date di luar range -> NaN."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    ts = pd.DatetimeIndex(["2027-01-01"])
    s = loader.get_daily(ts, "WB01")
    assert np.isnan(s.iloc[0])


def test_get_daily_invalid_column_raises(synthetic_generation_xlsx):
    loader = GenerationLoader(synthetic_generation_xlsx)
    with pytest.raises(KeyError, match="Unknown column"):
        loader.get_daily(pd.DatetimeIndex(["2026-05-10"]), "WB99")


def test_get_daily_preserves_input_index(synthetic_generation_xlsx):
    """Output Series index == input timestamps (caller-friendly)."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    ts = pd.DatetimeIndex(["2026-05-10 14:30:00"])
    s = loader.get_daily(ts, "WB01")
    assert s.index[0] == ts[0]


# ---------- get_period_total ----------


def test_get_period_total_inclusive(synthetic_generation_xlsx):
    """Total kWh sum [2026-05-10, 2026-05-12] = 3 days WB01-10 sums."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    total = loader.get_period_total("2026-05-10", "2026-05-12", "total_kwh")
    assert total == pytest.approx(10450 + 11450 + 12450)


def test_get_period_total_skips_nan(synthetic_generation_xlsx):
    """sum(skipna=True): future-out-of-range dates not in df -> not counted."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    total = loader.get_period_total("2026-05-10", "2027-12-31", "WB01")
    expected = sum(1000.0 + 100 * i for i in range(5))
    assert total == pytest.approx(expected)


def test_get_period_total_default_total_kwh_column(synthetic_generation_xlsx):
    """Default column = 'total_kwh' bila tidak spesifikasi."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    explicit = loader.get_period_total("2026-05-10", "2026-05-14", "total_kwh")
    default = loader.get_period_total("2026-05-10", "2026-05-14")
    assert explicit == pytest.approx(default)


def test_get_period_total_invalid_column_raises(synthetic_generation_xlsx):
    loader = GenerationLoader(synthetic_generation_xlsx)
    with pytest.raises(KeyError, match="Unknown column"):
        loader.get_period_total("2026-05-10", "2026-05-14", "bogus_col")


# ---------- from_geometry_yaml ----------


def test_from_geometry_yaml_loads(tmp_path, synthetic_generation_xlsx):
    """Yaml dengan generation.xlsx_path -> loader load."""
    import yaml

    cfg = {"generation": {"xlsx_path": synthetic_generation_xlsx}}
    yp = tmp_path / "geom.yaml"
    yp.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    loader = GenerationLoader.from_geometry_yaml(str(yp))
    assert len(loader.df) == 5


def test_from_geometry_yaml_missing_xlsx_path_raises(tmp_path):
    """Yaml tanpa generation.xlsx_path -> KeyError."""
    import yaml

    yp = tmp_path / "geom.yaml"
    yp.write_text(yaml.safe_dump({"generation": {}}), encoding="utf-8")
    with pytest.raises(KeyError, match="xlsx_path missing"):
        GenerationLoader.from_geometry_yaml(str(yp))


# ---------- Wave 11: Deem Dispatch + Curtailment columns ----------


def test_loader_loads_deem_dispatch_kwh(synthetic_generation_xlsx):
    """Col Q (Deem Dispatch (kWh)) -> numeric deem_dispatch_kwh column."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    assert "deem_dispatch_kwh" in loader.df.columns
    vals = loader.df["deem_dispatch_kwh"].tolist()
    assert vals == [0.0, 1500.0, 0.0, 2200.0, 500.0]
    # Accept any numeric kind (i / f / u) - synthetic ints inferred as int64.
    assert pd.api.types.is_numeric_dtype(loader.df["deem_dispatch_kwh"])


def test_loader_loads_curtailment_as_string(synthetic_generation_xlsx):
    """Col R (Curtailment) -> string curtailment_flag column ('Yes'/'No')."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    assert "curtailment_flag" in loader.df.columns
    vals = loader.df["curtailment_flag"].tolist()
    assert vals == ["No", "Yes", "No", "Yes", "No"]
    # NOT a numeric dtype.
    assert loader.df["curtailment_flag"].dtype == object


def test_get_daily_returns_curtailment_for_date_range(synthetic_generation_xlsx):
    """get_daily works on string columns too (used by PR cross-check)."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    ts = pd.date_range("2026-05-11", "2026-05-13", freq="D")
    flags = loader.get_daily(ts, "curtailment_flag")
    assert list(flags) == ["Yes", "No", "Yes"]


def test_get_period_total_rejects_string_column(synthetic_generation_xlsx):
    """get_period_total('curtailment_flag') -> TypeError (not summable)."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    with pytest.raises(TypeError, match="string column"):
        loader.get_period_total("2026-05-10", "2026-05-14", "curtailment_flag")


def test_get_period_total_sums_deem_dispatch(synthetic_generation_xlsx):
    """get_period_total works on Deem Dispatch (numeric)."""
    loader = GenerationLoader(synthetic_generation_xlsx)
    total = loader.get_period_total("2026-05-10", "2026-05-14", "deem_dispatch_kwh")
    # 0 + 1500 + 0 + 2200 + 500 = 4200.
    assert total == pytest.approx(4200.0)
