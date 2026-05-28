"""Per-detector deep-dive page."""

from __future__ import annotations

from datetime import date, timedelta

from pv_pipeline.dashboard.auth import require_auth
from pv_pipeline.dashboard.data.cache import cached_findings_range, clear_dashboard_cache
from pv_pipeline.dashboard.widgets.date_picker import pick_date_range
from pv_pipeline.dashboard.widgets.detector_tab import render_detector_tab


DETECTOR_SHEETS = {
    "Availability": ["M2e_hybrid_AllStrings", "M2e_hybrid_InverterLog"],
    "PeerZ": ["M2b_peer_zscore_StringStatus"],
    "OpenCircuit": ["M2b_open_circuit_StringStatus"],
    "GroundFault": ["M2b_ground_fault_StringStatus"],
    "IForest": ["M2_iforest_AnomalyScores", "M2_iforest_AnomalySummary"],
    "Shading": ["M2a_shading_HourlyMetrics", "M2a_shading_ShadingSummary"],
    "LowIrradiance": [
        "M2a_low_irradiance_LowIrradianceFit",
        "M2a_low_irradiance_Summary",
        "LowIrradianceFit",
        "LowIrradianceSummary",
    ],
    "Soiling": [
        "M2a_soiling_EconomicAnalysis",
        "M2a_soiling_SoilingRatio",
        "M2a_soiling_CleaningEvents",
        "EconomicAnalysis",
        "SoilingRatio",
        "CleaningEvents",
    ],
}


def _default_range() -> tuple[date, date]:
    end = date.today()
    return end - timedelta(days=6), end


def main() -> None:
    import streamlit as st  # noqa: WPS433

    st.set_page_config(page_title="Detector Deep-Dive", layout="wide")
    require_auth()

    st.title("Per-Detector Deep-Dive")
    start_default, end_default = _default_range()
    start, end = pick_date_range(start_default, end_default)
    if st.button("Refresh data"):
        clear_dashboard_cache()
        st.rerun()

    result = cached_findings_range(start, end)
    if not result.sheets:
        st.info("Tidak ada workbook sheets untuk date range ini.")
        return
    tabs = st.tabs(list(DETECTOR_SHEETS))
    for tab, (label, aliases) in zip(tabs, DETECTOR_SHEETS.items()):
        with tab:
            render_detector_tab(label, result.sheets, aliases)
