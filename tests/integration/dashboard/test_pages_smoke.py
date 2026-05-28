from __future__ import annotations

import importlib

import pandas as pd
import pytest

from pv_pipeline.dashboard.pages.detectors import DETECTOR_SHEETS
from pv_pipeline.dashboard.widgets.detector_tab import first_available_sheet


@pytest.mark.dashboard
@pytest.mark.parametrize(
    "module_name",
    [
        "pv_pipeline.dashboard.app",
        "pv_pipeline.dashboard.pages.heatmap",
        "pv_pipeline.dashboard.pages.findings",
        "pv_pipeline.dashboard.pages.detectors",
    ],
)
def test_dashboard_page_modules_import_without_gdrive_calls(module_name):
    module = importlib.import_module(module_name)

    assert callable(module.main)


@pytest.mark.dashboard
def test_detector_aliases_handle_low_irradiance_truncated_sheet_names():
    sheets = {
        "LowIrradianceFit": pd.DataFrame({"classification": ["normal"]}),
        "LowIrradianceSummary": pd.DataFrame({"classification": ["normal"]}),
    }

    selected = first_available_sheet(sheets, DETECTOR_SHEETS["LowIrradiance"])

    assert selected is not None
    assert selected[0] == "LowIrradianceFit"
