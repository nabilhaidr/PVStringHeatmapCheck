"""Test pv_pipeline.weather.loader: 3 weather variable loaders (Wave 5)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.weather import (
    AmbientTempLoader,
    WeatherLoaderBase,
    WindDirectionLoader,
    WindSpeedLoader,
)


# ---------- Synthetic xlsx fixtures ----------


def _build_xlsx(
    tmp_path,
    fname: str,
    sheet: str,
    col_per_ws_fmt: str,
    base_values_per_ws: dict,
    n_rows: int = 24,
    start_iso: str = "2026-05-14 00:00:00",
    freq: str = "1h",
    ws1_all_nan: bool = False,
) -> str:
    """Generate synthetic 4-WS xlsx mirroring real layout."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    headers = ["Date time"] + [col_per_ws_fmt.format(ws_num=i) for i in range(1, 5)] + [
        "Rata-rata WS 1 - WS 4"
    ]
    ws.append(headers)

    ts = pd.date_range(start_iso, periods=n_rows, freq=freq)
    for t in ts:
        ws1_val = np.nan if ws1_all_nan else base_values_per_ws[1]
        row_vals = [
            ws1_val,
            base_values_per_ws[2],
            base_values_per_ws[3],
            base_values_per_ws[4],
        ]
        avg = float(np.nanmean(row_vals))
        ws.append([t.to_pydatetime()] + row_vals + [avg])

    path = tmp_path / fname
    wb.save(str(path))
    return str(path)


@pytest.fixture
def synthetic_ambient_xlsx(tmp_path):
    return _build_xlsx(
        tmp_path, "synthetic_ambient.xlsx",
        sheet="Ambient Temperature PLTS IKN",
        col_per_ws_fmt="Ambient Temp (oC) WS {ws_num}",
        base_values_per_ws={1: 28.0, 2: 28.5, 3: 28.2, 4: 28.3},
    )


@pytest.fixture
def synthetic_windspeed_xlsx(tmp_path):
    return _build_xlsx(
        tmp_path, "synthetic_windspeed.xlsx",
        sheet="Wind Speed PLTS IKN",
        col_per_ws_fmt="Wind Speed (m/s) WS {ws_num}",
        base_values_per_ws={1: 1.5, 2: 2.0, 3: 1.8, 4: 2.2},
    )


@pytest.fixture
def synthetic_winddirection_xlsx(tmp_path):
    """Wind direction: WS-1 all-NaN (sensor missing, per production reality)."""
    return _build_xlsx(
        tmp_path, "synthetic_winddir.xlsx",
        sheet="Wind Direction PLTS IKN",
        col_per_ws_fmt="Wind Direction (o) WS {ws_num}",
        base_values_per_ws={1: 180.0, 2: 175.0, 3: 190.0, 4: 185.0},
        ws1_all_nan=True,
    )


@pytest.fixture
def ws_to_wb_weather_mapping():
    """4-WS mapping, WB01/02 piggyback ke WS-4."""
    return {
        "WS-1": ["WB08", "WB09", "WB10"],
        "WS-2": ["WB05", "WB07"],
        "WS-3": ["WB06"],
        "WS-4": ["WB01", "WB02", "WB03", "WB04"],
    }


# ---------- AmbientTempLoader ----------


def test_ambient_loads_xlsx(synthetic_ambient_xlsx, ws_to_wb_weather_mapping):
    loader = AmbientTempLoader(
        xlsx_path=synthetic_ambient_xlsx, ws_to_wb=ws_to_wb_weather_mapping,
    )
    assert loader.YAML_KEY == "ambient_temperature"
    assert "WS-1" in loader.df.columns
    assert "WS-4" in loader.df.columns
    assert "avg" in loader.df.columns
    assert len(loader.df) == 24


def test_ambient_per_ws_wb05_returns_ws2(synthetic_ambient_xlsx, ws_to_wb_weather_mapping):
    """WB05 -> WS-2 mapping -> value 28.5."""
    loader = AmbientTempLoader(
        xlsx_path=synthetic_ambient_xlsx, ws_to_wb=ws_to_wb_weather_mapping,
    )
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    val = loader.get_per_ws(ts, "WB05").iloc[0]
    assert val == pytest.approx(28.5)


def test_ambient_wb01_piggyback_ws4(synthetic_ambient_xlsx, ws_to_wb_weather_mapping):
    """WB01 piggyback WS-4 (28.3, bukan WS-1)."""
    loader = AmbientTempLoader(
        xlsx_path=synthetic_ambient_xlsx, ws_to_wb=ws_to_wb_weather_mapping,
    )
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    val = loader.get_per_ws(ts, "WB01").iloc[0]
    assert val == pytest.approx(28.3)


def test_ambient_avg_column(synthetic_ambient_xlsx, ws_to_wb_weather_mapping):
    loader = AmbientTempLoader(
        xlsx_path=synthetic_ambient_xlsx, ws_to_wb=ws_to_wb_weather_mapping,
    )
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    avg = loader.get_avg(ts).iloc[0]
    expected = float(np.mean([28.0, 28.5, 28.2, 28.3]))
    assert avg == pytest.approx(expected, abs=1e-6)


# ---------- WindSpeedLoader ----------


def test_windspeed_loads_xlsx(synthetic_windspeed_xlsx, ws_to_wb_weather_mapping):
    loader = WindSpeedLoader(
        xlsx_path=synthetic_windspeed_xlsx, ws_to_wb=ws_to_wb_weather_mapping,
    )
    assert loader.YAML_KEY == "wind_speed"
    assert loader.DEFAULT_SHEET == "Wind Speed PLTS IKN"
    assert len(loader.df) == 24


def test_windspeed_wb10_uses_ws1(synthetic_windspeed_xlsx, ws_to_wb_weather_mapping):
    """WB10 -> WS-1 mapping -> 1.5 m/s."""
    loader = WindSpeedLoader(
        xlsx_path=synthetic_windspeed_xlsx, ws_to_wb=ws_to_wb_weather_mapping,
    )
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    val = loader.get_per_ws(ts, "WB10").iloc[0]
    assert val == pytest.approx(1.5)


# ---------- WindDirectionLoader ----------


def test_winddir_loads_xlsx(synthetic_winddirection_xlsx, ws_to_wb_weather_mapping):
    loader = WindDirectionLoader(
        xlsx_path=synthetic_winddirection_xlsx, ws_to_wb=ws_to_wb_weather_mapping,
    )
    assert loader.YAML_KEY == "wind_direction"


def test_winddir_ws1_nan_per_production_reality(
    synthetic_winddirection_xlsx, ws_to_wb_weather_mapping,
):
    """WB10 -> WS-1 NaN (sensor missing). Avg masih valid (3 of 4 WS)."""
    loader = WindDirectionLoader(
        xlsx_path=synthetic_winddirection_xlsx, ws_to_wb=ws_to_wb_weather_mapping,
    )
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    wb10_val = loader.get_per_ws(ts, "WB10").iloc[0]
    assert np.isnan(wb10_val)
    avg = loader.get_avg(ts).iloc[0]
    assert not np.isnan(avg)
    # Mean of [nan, 175, 190, 185] = mean(175, 190, 185) = 183.33...
    expected = float(np.mean([175.0, 190.0, 185.0]))
    assert avg == pytest.approx(expected, abs=1e-6)


def test_winddir_wb05_returns_ws2_value(
    synthetic_winddirection_xlsx, ws_to_wb_weather_mapping,
):
    """WB05 -> WS-2 mapping -> 175 deg."""
    loader = WindDirectionLoader(
        xlsx_path=synthetic_winddirection_xlsx, ws_to_wb=ws_to_wb_weather_mapping,
    )
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    val = loader.get_per_ws(ts, "WB05").iloc[0]
    assert val == pytest.approx(175.0)


# ---------- Multi-year support ----------


def test_multi_year_concat(tmp_path, ws_to_wb_weather_mapping):
    """Two xlsx files (2025 + 2026) -> concat + sort + dedup."""
    p2025 = _build_xlsx(
        tmp_path, "ambient_2025.xlsx",
        sheet="Ambient Temperature PLTS IKN",
        col_per_ws_fmt="Ambient Temp (oC) WS {ws_num}",
        base_values_per_ws={1: 26.0, 2: 26.5, 3: 26.2, 4: 26.3},
        n_rows=24, start_iso="2025-12-31 01:00:00", freq="1h",
    )
    p2026 = _build_xlsx(
        tmp_path, "ambient_2026.xlsx",
        sheet="Ambient Temperature PLTS IKN",
        col_per_ws_fmt="Ambient Temp (oC) WS {ws_num}",
        base_values_per_ws={1: 28.0, 2: 28.5, 3: 28.2, 4: 28.3},
        n_rows=24, start_iso="2026-01-01 00:00:00", freq="1h",
    )
    loader = AmbientTempLoader(
        xlsx_path=[p2025, p2026], ws_to_wb=ws_to_wb_weather_mapping,
    )
    assert len(loader.xlsx_paths) == 2
    # 24 from 2025 (01:00..2026-01-01 00:00) + 24 from 2026 (00:00..23:00).
    # Overlap di 2026-01-01 00:00 -> dedup keep first -> 24+24-1 = 47.
    assert len(loader.df) == 47
    assert loader.df.index.min().year == 2025
    assert loader.df.index.max().year == 2026


def test_multi_year_dedup_keeps_first(tmp_path, ws_to_wb_weather_mapping):
    """Overlapping timestamps -> dedup keep first file's value."""
    p1 = _build_xlsx(
        tmp_path, "amb1.xlsx",
        sheet="Ambient Temperature PLTS IKN",
        col_per_ws_fmt="Ambient Temp (oC) WS {ws_num}",
        base_values_per_ws={1: 20.0, 2: 20.0, 3: 20.0, 4: 20.0},
        n_rows=5, start_iso="2026-05-14 10:00:00", freq="1h",
    )
    p2 = _build_xlsx(
        tmp_path, "amb2.xlsx",
        sheet="Ambient Temperature PLTS IKN",
        col_per_ws_fmt="Ambient Temp (oC) WS {ws_num}",
        base_values_per_ws={1: 99.0, 2: 99.0, 3: 99.0, 4: 99.0},
        n_rows=5, start_iso="2026-05-14 10:00:00", freq="1h",
    )
    loader = AmbientTempLoader(
        xlsx_path=[p1, p2], ws_to_wb=ws_to_wb_weather_mapping,
    )
    # 5 unique timestamps (5 overlap -> kept first = 20.0 values).
    assert len(loader.df) == 5
    assert loader.df["WS-1"].iloc[0] == pytest.approx(20.0)


# ---------- Error / edge cases ----------


def test_missing_xlsx_raises():
    with pytest.raises(FileNotFoundError, match="not found"):
        AmbientTempLoader(xlsx_path="nonexistent.xlsx", ws_to_wb={})


def test_empty_xlsx_path_raises():
    with pytest.raises(ValueError, match="non-empty"):
        AmbientTempLoader(xlsx_path=[], ws_to_wb={})


def test_unmapped_wb_returns_nan(synthetic_ambient_xlsx):
    """WB tidak ada di mapping -> all-NaN series + warning."""
    loader = AmbientTempLoader(
        xlsx_path=synthetic_ambient_xlsx, ws_to_wb={"WS-1": ["WB99"]},
    )
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    with pytest.warns(UserWarning, match="No WS mapping"):
        s = loader.get_per_ws(ts, "WB05")
    assert np.isnan(s.iloc[0])


def test_from_geometry_yaml_requires_yaml_key():
    """Base class tanpa YAML_KEY -> NotImplementedError."""
    class _AnonymousLoader(WeatherLoaderBase):
        DEFAULT_SHEET = "x"
        COL_PER_WS_FMT = "y WS {ws_num}"
        YAML_KEY = ""

    with pytest.raises(NotImplementedError, match="YAML_KEY"):
        _AnonymousLoader.from_geometry_yaml("dummy.yaml")
