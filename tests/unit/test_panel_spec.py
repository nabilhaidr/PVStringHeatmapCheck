"""Test pv_pipeline.panel_spec: PanelSpec parser + Voc helpers."""
from __future__ import annotations

import os

import pytest

from pv_pipeline.panel_spec import PanelSpec


CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "panel_spec.yaml",
)


@pytest.fixture(scope="module")
def panel_spec():
    return PanelSpec.from_yaml(CONFIG_PATH)


def test_jinko_stc_specs(panel_spec):
    """Jinko JKM625N STC values from datasheet."""
    assert panel_spec.panel_model == "Jinko Solar JKM625N 78HL4-BDV"
    assert panel_spec.stc.voc_v == pytest.approx(55.72)
    assert panel_spec.stc.isc_a == pytest.approx(14.27)
    assert panel_spec.stc.pmax_w == pytest.approx(625.0)


def test_temp_coefficients(panel_spec):
    """Temp coefficients from Jinko datasheet."""
    assert panel_spec.temp_coef.voc_pct_per_c == pytest.approx(-0.25)
    assert panel_spec.temp_coef.pmax_pct_per_c == pytest.approx(-0.29)
    assert panel_spec.temp_coef.isc_pct_per_c == pytest.approx(0.045)


def test_modules_per_string_per_wb(panel_spec):
    """User-provided: WB01/02 = 24 modules, WB03-WB10 = 26."""
    assert panel_spec.modules_per_string("WB01") == 24
    assert panel_spec.modules_per_string("WB02") == 24
    assert panel_spec.modules_per_string("WB03") == 26
    assert panel_spec.modules_per_string("WB05") == 26
    assert panel_spec.modules_per_string("WB10") == 26


def test_modules_per_string_unknown_wb_falls_back(panel_spec):
    """Unknown WB harus fall back ke default (26)."""
    assert panel_spec.modules_per_string("WB99") == panel_spec.default_modules_per_string


def test_voc_at_cell_temp_decrease_with_heat(panel_spec):
    """Voc harus turun saat sel panas (temp_coef_voc negatif)."""
    voc_25 = panel_spec.voc_at_cell_temp(25.0, base="stc")
    voc_50 = panel_spec.voc_at_cell_temp(50.0, base="stc")
    voc_10 = panel_spec.voc_at_cell_temp(10.0, base="stc")
    assert voc_25 == pytest.approx(55.72)
    # 50C: 55.72 * (1 + (-0.25/100) * 25) = 55.72 * 0.9375 = 52.24
    assert voc_50 == pytest.approx(52.24, rel=1e-2)
    # 10C: 55.72 * (1 + (-0.25/100) * (-15)) = 55.72 * 1.0375 = 57.81
    assert voc_10 == pytest.approx(57.81, rel=1e-2)


def test_voc_string_stc(panel_spec):
    """Voc string @ STC = Voc_module * modules_per_string."""
    assert panel_spec.voc_string_stc("WB01") == pytest.approx(55.72 * 24, rel=1e-3)
    assert panel_spec.voc_string_stc("WB05") == pytest.approx(55.72 * 26, rel=1e-3)


def test_voc_string_at_design_min_temp_cold_morning(panel_spec):
    """Cold-morning (10 C cell) Voc baseline untuk High-R rule."""
    # WB05: 57.81 * 26 = 1503 V (slightly overshoots 1500V, by design margin tight)
    voc_cold = panel_spec.voc_string_at_design_min_temp("WB05", min_cell_temp_c=10.0)
    assert voc_cold == pytest.approx(1503.0, rel=1e-2)
