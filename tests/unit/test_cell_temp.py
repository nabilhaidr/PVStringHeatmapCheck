"""Test pv_pipeline.cell_temp: CellTempProvider (xlsx parser + WB->WS Tcell mapping)."""
from __future__ import annotations

import pandas as pd
import pytest

from pv_pipeline.cell_temp import (
    ALL_SOURCES,
    DEFAULT_SAPM_MODEL,
    SAPM_PRESETS,
    SOURCE_AUTO,
    SOURCE_MEASURED_OVERALL_AVG,
    SOURCE_MEASURED_PER_WS,
    SOURCE_SAPM,
    CellTempProvider,
)


# WB01-02 piggyback ke WS-4 (per user spec, WS-5 no Tcell sensor)
WS_TO_WB_TCELL_MAP = {
    "WS-1": ["WB08", "WB09", "WB10"],
    "WS-2": ["WB05", "WB07"],
    "WS-3": ["WB06"],
    "WS-4": ["WB01", "WB02", "WB03", "WB04"],  # WB01/02 piggyback
}


# ---------- Constructor + parsing ----------


def test_provider_parses_synthetic_xlsx(synthetic_tcell_xlsx):
    """Load synthetic 18-col xlsx -> df dengan 5 cols (WS-1..4 + overall)."""
    prov = CellTempProvider(synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP)
    assert not prov.df.empty
    for col in ["WS-1", "WS-2", "WS-3", "WS-4", "overall"]:
        assert col in prov.df.columns


def test_wb01_piggyback_to_ws4(synthetic_tcell_xlsx):
    """WB01 dan WB02 piggyback ke WS-4 (WS-5 no Tcell)."""
    prov = CellTempProvider(synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP)
    assert prov.wb_to_ws["WB01"] == "WS-4"
    assert prov.wb_to_ws["WB02"] == "WS-4"
    assert prov.wb_to_ws["WB03"] == "WS-4"  # natural
    assert prov.wb_to_ws["WB05"] == "WS-2"
    assert prov.wb_to_ws["WB10"] == "WS-1"


def test_default_mapping_when_not_provided(synthetic_tcell_xlsx):
    """Tanpa ws_to_wb_tcell -> pakai DEFAULT_WS_TO_WB_TCELL (WB01/02 -> WS-4)."""
    prov = CellTempProvider(synthetic_tcell_xlsx)  # no map
    assert prov.wb_to_ws["WB01"] == "WS-4"  # default piggyback bekerja


# ---------- get_tcell single source ----------


def test_get_tcell_measured_per_ws(synthetic_tcell_xlsx):
    """measured_per_ws untuk WB01 (-> WS-4) di noon."""
    prov = CellTempProvider(synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    tc = prov.get_tcell(ts, "WB01", source=SOURCE_MEASURED_PER_WS)
    # Synthetic noon: base = 25 + 20 * sin(pi/2) = 45 C
    assert tc.iloc[0] == pytest.approx(45.0, abs=2.0)


def test_get_tcell_measured_overall_avg(synthetic_tcell_xlsx):
    """measured_overall_avg sama untuk semua WB (site-wide average)."""
    prov = CellTempProvider(synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    tc1 = prov.get_tcell(ts, "WB01", source=SOURCE_MEASURED_OVERALL_AVG)
    tc5 = prov.get_tcell(ts, "WB05", source=SOURCE_MEASURED_OVERALL_AVG)
    # Sama-sama Overall column, value sama
    assert tc1.iloc[0] == tc5.iloc[0]


def test_get_tcell_unmapped_wb_per_ws_returns_nan(synthetic_tcell_xlsx):
    """WB tidak ada di mapping + source=per_ws -> NaN."""
    prov = CellTempProvider(synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    tc = prov.get_tcell(ts, "WB99", source=SOURCE_MEASURED_PER_WS)
    assert pd.isna(tc.iloc[0])


def test_get_tcell_auto_fallback(synthetic_tcell_xlsx):
    """auto fallback: per_ws first, then overall_avg.

    Synthetic data tidak punya NaN gap di WS-4, jadi WB01 auto = per_ws value.
    """
    prov = CellTempProvider(synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    tc_auto = prov.get_tcell(ts, "WB01", source=SOURCE_AUTO)
    tc_per_ws = prov.get_tcell(ts, "WB01", source=SOURCE_MEASURED_PER_WS)
    # auto == per_ws (no fallback needed di synthetic data)
    assert tc_auto.iloc[0] == pytest.approx(tc_per_ws.iloc[0], abs=0.01)


def test_get_tcell_unsupported_source_raises(synthetic_tcell_xlsx):
    prov = CellTempProvider(synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    with pytest.raises(ValueError, match="unsupported source"):
        prov.get_tcell(ts, "WB01", source="not_a_source")


# ---------- get_tcell_all_sources DataFrame ----------


def test_get_tcell_all_sources_includes_auto(synthetic_tcell_xlsx):
    """get_tcell_all_sources include 3 non-auto (per_ws + overall + sapm) + 1 auto."""
    prov = CellTempProvider(synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    df = prov.get_tcell_all_sources(ts, "WB01")
    assert SOURCE_MEASURED_PER_WS in df.columns
    assert SOURCE_MEASURED_OVERALL_AVG in df.columns
    assert SOURCE_SAPM in df.columns
    assert SOURCE_AUTO in df.columns
    assert len(df.columns) == 4


def test_get_tcell_all_sources_no_auto_flag(synthetic_tcell_xlsx):
    """include_auto=False -> drop auto col (sisanya 3: per_ws + overall + sapm)."""
    prov = CellTempProvider(synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    df = prov.get_tcell_all_sources(ts, "WB01", include_auto=False)
    assert SOURCE_AUTO not in df.columns
    assert len(df.columns) == 3


# ---------- Sources constants ----------


def test_all_sources_constant():
    """Wave 6: ALL_SOURCES = per_ws + overall + sapm + auto = 4."""
    assert SOURCE_MEASURED_PER_WS in ALL_SOURCES
    assert SOURCE_MEASURED_OVERALL_AVG in ALL_SOURCES
    assert SOURCE_SAPM in ALL_SOURCES
    assert SOURCE_AUTO in ALL_SOURCES
    assert len(ALL_SOURCES) == 4


# ---------- Wave 6: SAPM source ----------


class _MockPOAProvider:
    """Mock returning fixed POA Series."""
    def __init__(self, poa_wm2: float = 800.0):
        self._poa = poa_wm2

    def get_poa(self, timestamps, wb_id, source="auto"):
        ts = pd.DatetimeIndex(timestamps)
        return pd.Series([self._poa] * len(ts), index=ts)


class _MockWeatherLoader:
    """Mock per-WS + avg returning fixed value."""
    def __init__(self, value: float):
        self._v = value

    def get_per_ws(self, timestamps, wb_id):
        ts = pd.DatetimeIndex(timestamps)
        return pd.Series([self._v] * len(ts), index=ts)

    def get_avg(self, timestamps):
        ts = pd.DatetimeIndex(timestamps)
        return pd.Series([self._v] * len(ts), index=ts)


def test_sapm_returns_nan_without_dependencies(synthetic_tcell_xlsx):
    """Tanpa poa/ambient/wind providers -> SAPM source all NaN."""
    prov = CellTempProvider(synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP)
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    tc = prov.get_tcell(ts, "WB01", source=SOURCE_SAPM)
    assert pd.isna(tc.iloc[0])


def test_sapm_computes_with_mocks(synthetic_tcell_xlsx):
    """SAPM(POA=800, T_air=30, wind=2, open_rack_glass_glass) = ~46 C.

    Formula: T_cell = T_air + POA/1000 * exp(a + b*wind) + POA/1000 * deltaT
           = 30 + 0.8 * exp(-3.47 + (-0.0594)*2) + 0.8 * 3
           = 30 + 0.8 * exp(-3.5888) + 2.4
           = 30 + 0.8 * 0.0276 + 2.4 = ~32.4 C (using T_module formula);
    pvlib sapm_cell adds deltaT * POA/1000 -> back-of-module T.
    Just assert result is finite + reasonable warm zone (>= T_air).
    """
    prov = CellTempProvider(
        synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP,
        poa_provider=_MockPOAProvider(poa_wm2=800.0),
        ambient_temp_loader=_MockWeatherLoader(30.0),
        wind_speed_loader=_MockWeatherLoader(2.0),
    )
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    tc = prov.get_tcell(ts, "WB01", source=SOURCE_SAPM)
    val = tc.iloc[0]
    assert not pd.isna(val)
    # T_cell selalu >= T_air saat POA > 0 (heat from irradiance).
    assert val >= 30.0
    assert val < 80.0  # sanity bound


def test_sapm_fallback_in_auto_chain(synthetic_tcell_xlsx):
    """auto chain pakai SAPM saat WB99 unmapped + overall ada.

    Karena measured_per_ws NaN (WB99 not mapped), auto akan fallback ke
    overall_avg (synthetic punya). Test SAPM-specific: paksa chain hanya sapm.
    """
    prov = CellTempProvider(
        synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP,
        auto_fallback_chain=[SOURCE_SAPM],  # force SAPM only
        poa_provider=_MockPOAProvider(800.0),
        ambient_temp_loader=_MockWeatherLoader(28.0),
        wind_speed_loader=_MockWeatherLoader(1.5),
    )
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    tc = prov.get_tcell(ts, "WB99", source=SOURCE_AUTO)
    assert not pd.isna(tc.iloc[0])


def test_sapm_unknown_model_warns_and_returns_nan(synthetic_tcell_xlsx):
    """sapm_model tidak dikenal -> warning saat init + NaN saat query."""
    with pytest.warns(UserWarning, match="sapm_model"):
        prov = CellTempProvider(
            synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP,
            sapm_model="unknown_preset",
            poa_provider=_MockPOAProvider(800.0),
            ambient_temp_loader=_MockWeatherLoader(28.0),
            wind_speed_loader=_MockWeatherLoader(1.5),
        )
    ts = pd.DatetimeIndex(["2026-05-14 12:00:00"])
    tc = prov.get_tcell(ts, "WB01", source=SOURCE_SAPM)
    assert pd.isna(tc.iloc[0])


def test_sapm_params_override_preset(synthetic_tcell_xlsx):
    """sapm_params override preset thermal coefficients."""
    custom = {"a": -3.0, "b": -0.04, "deltaT": 2.0}
    prov = CellTempProvider(
        synthetic_tcell_xlsx, ws_to_wb_tcell=WS_TO_WB_TCELL_MAP,
        sapm_model="open_rack_glass_glass", sapm_params=custom,
    )
    assert prov.sapm_params == custom


def test_sapm_presets_contain_jinko_default():
    """Default model 'open_rack_glass_glass' tersedia di preset dict."""
    assert DEFAULT_SAPM_MODEL == "open_rack_glass_glass"
    assert DEFAULT_SAPM_MODEL in SAPM_PRESETS
    p = SAPM_PRESETS[DEFAULT_SAPM_MODEL]
    assert p["a"] == pytest.approx(-3.47)
    assert p["b"] == pytest.approx(-0.0594)
    assert p["deltaT"] == pytest.approx(3.0)


# ---------- File not found ----------


def test_provider_raises_for_missing_xlsx():
    with pytest.raises(FileNotFoundError):
        CellTempProvider("nonexistent_tcell.xlsx")


# ---------- Wave 11 hotfix: alias-namespace POAProvider resolution ----------


def test_resolve_poa_provider_cls_standard_path():
    """_resolve_poa_provider_cls() returns POAProvider via standard import."""
    from pv_pipeline.cell_temp import _resolve_poa_provider_cls
    from pv_pipeline.poa.provider import POAProvider
    assert _resolve_poa_provider_cls() is POAProvider


def test_resolve_poa_provider_cls_alias_fallback(monkeypatch):
    """Wave 11: helper falls back to sys.modules scan when standard import fails.

    Simulates the post-_load_sprint4_modules state where pv_pipeline.poa was
    popped from sys.modules and pv_pipeline package no longer exposes .poa
    subpackage (main-repo scenario where worktree-only modules were loaded).
    """
    import sys
    from pv_pipeline.cell_temp import _resolve_poa_provider_cls

    # Inject alias-loaded fake module with POAProvider attribute.
    fake_mod = type(sys)("pv_pipeline_alias_test.poa.provider")

    class FakeAliasedPOA:
        @classmethod
        def from_yaml(cls, path):
            return cls()

    fake_mod.POAProvider = FakeAliasedPOA
    monkeypatch.setitem(sys.modules, "pv_pipeline_alias_test.poa.provider", fake_mod)

    # Force standard `from pv_pipeline.poa.provider import POAProvider` to fail
    # by neutering pv_pipeline.__path__ (blocks subpackage discovery) and
    # popping the cached entries.
    import pv_pipeline
    monkeypatch.setattr(pv_pipeline, "__path__", [])
    monkeypatch.delitem(sys.modules, "pv_pipeline.poa.provider", raising=False)
    monkeypatch.delitem(sys.modules, "pv_pipeline.poa", raising=False)

    result = _resolve_poa_provider_cls()
    assert result is FakeAliasedPOA, f"Expected FakeAliasedPOA, got {result}"


def test_resolve_poa_provider_cls_returns_none_when_nothing_available(monkeypatch):
    """Wave 11: helper returns None when no .poa.provider module is loadable."""
    import sys
    from pv_pipeline.cell_temp import _resolve_poa_provider_cls

    # Purge all *.poa.* entries AND pv_pipeline.poa subpackage so Python
    # cannot resolve standard import via cached sys.modules. Also neuter
    # parent __path__ to block re-discovery.
    purged = [
        n for n in list(sys.modules.keys())
        if n.endswith(".poa.provider")
        or n == "pv_pipeline.poa"
        or n.startswith("pv_pipeline.poa.")
    ]
    for n in purged:
        monkeypatch.delitem(sys.modules, n, raising=False)

    import pv_pipeline
    monkeypatch.setattr(pv_pipeline, "__path__", [])

    result = _resolve_poa_provider_cls()
    assert result is None
