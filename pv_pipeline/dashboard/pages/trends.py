"""M2 daily-trend dashboard page."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from pv_pipeline.dashboard.auth import require_auth
from pv_pipeline.dashboard.data.cache import cached_findings_range, clear_dashboard_cache
from pv_pipeline.dashboard.data.trends import (
    findings_counts_per_day,
    numeric_metric_per_day,
    soiling_ratio_per_day,
    status_counts_per_day,
    wide_counts_per_day,
)
from pv_pipeline.dashboard.widgets.date_picker import pick_date_range

_STATUS_DETECTORS = [
    ("PeerZ", "M2b_peer_zscore_StringStatus"),
    ("OpenCircuit", "M2b_open_circuit_StringStatus"),
    ("GroundFault", "M2b_ground_fault_StringStatus"),
]
_LOW_IRR_COUNT_COLS = [
    "normal",
    "low_irradiance_underperform",
    "general_underperform",
    "skipped",
]


def _default_range() -> tuple[date, date]:
    end = date.today()
    return end - timedelta(days=29), end


def _sheet(sheets: dict[str, pd.DataFrame], name: str) -> pd.DataFrame:
    return sheets.get(name, pd.DataFrame())


def _render_findings_overview(st, alt, sheets: dict[str, pd.DataFrame]) -> None:
    st.subheader("Ringkasan semua detector (jumlah temuan/hari)")
    trend = findings_counts_per_day(_sheet(sheets, "Findings"))
    if trend.empty:
        st.info("Tidak ada sheet Findings untuk range ini.")
        return
    per_detector = trend.groupby(["source_date", "sub_module"], as_index=False)["count"].sum()
    chart = alt.Chart(per_detector).mark_line(point=True).encode(
        x=alt.X("source_date:T", title="Tanggal"),
        y=alt.Y("count:Q", title="Jumlah temuan"),
        color=alt.Color("sub_module:N", title="Detector"),
    )
    st.altair_chart(chart, use_container_width=True)


def _render_status(st, alt, sheets: dict[str, pd.DataFrame], label: str, sheet_name: str) -> None:
    st.subheader(label)
    trend = status_counts_per_day(_sheet(sheets, sheet_name))
    if trend.empty:
        st.info(f"Detector {label} tidak aktif untuk range ini.")
        return
    chart = alt.Chart(trend).mark_line(point=True).encode(
        x=alt.X("source_date:T", title="Tanggal"),
        y=alt.Y("count:Q", title="Jumlah string"),
        color=alt.Color("status:N"),
    )
    st.altair_chart(chart, use_container_width=True)


def _render_numeric(
    st,
    alt,
    sheets: dict[str, pd.DataFrame],
    label: str,
    sheet_name: str,
    value_col: str,
    y_title: str,
) -> None:
    st.subheader(label)
    trend = numeric_metric_per_day(_sheet(sheets, sheet_name), value_col)
    if trend.empty:
        st.info(f"Detector {label} tidak aktif untuk range ini.")
        return
    long = trend.melt(id_vars="source_date", var_name="metric", value_name="value")
    chart = alt.Chart(long).mark_line(point=True).encode(
        x=alt.X("source_date:T", title="Tanggal"),
        y=alt.Y("value:Q", title=y_title),
        color=alt.Color("metric:N"),
    )
    st.altair_chart(chart, use_container_width=True)


def _render_intermittent(st, alt, sheets: dict[str, pd.DataFrame]) -> None:
    st.subheader("Intermittent (LSTM-AE) - error rekonstruksi vs threshold")
    trend = numeric_metric_per_day(
        _sheet(sheets, "M2b_intermittent_WindowErrors"),
        "error_ratio",
        aggs=("mean", "max"),
    )
    if trend.empty:
        st.info("Detector Intermittent tidak aktif untuk range ini.")
        return
    long = trend.melt(id_vars="source_date", var_name="metric", value_name="error_ratio")
    lines = alt.Chart(long).mark_line(point=True).encode(
        x=alt.X("source_date:T", title="Tanggal"),
        y=alt.Y("error_ratio:Q", title="error_ratio (1.0 = threshold)"),
        color=alt.Color("metric:N"),
    )
    threshold = alt.Chart(pd.DataFrame({"y": [1.0]})).mark_rule(strokeDash=[4, 4]).encode(y="y:Q")
    st.altair_chart(lines + threshold, use_container_width=True)


def _render_soiling(st, alt, sheets: dict[str, pd.DataFrame]) -> None:
    st.subheader("Soiling ratio (dengan pita CI)")
    trend = soiling_ratio_per_day(_sheet(sheets, "M2a_soiling_EconomicAnalysis"))
    if trend.empty:
        st.info("Detector Soiling tidak aktif untuk range ini.")
        return
    base = alt.Chart(trend)
    band = base.mark_area(opacity=0.2).encode(
        x=alt.X("source_date:T", title="Tanggal"),
        y=alt.Y("sr_ci_lower:Q", title="Soiling ratio"),
        y2="sr_ci_upper:Q",
    )
    line = base.mark_line(point=True).encode(
        x="source_date:T",
        y=alt.Y("soiling_ratio:Q"),
    )
    st.altair_chart(band + line, use_container_width=True)


def _render_low_irradiance(st, alt, sheets: dict[str, pd.DataFrame]) -> None:
    st.subheader("Low irradiance - klasifikasi inverter/hari")
    trend = wide_counts_per_day(
        _sheet(sheets, "M2a_low_irradiance_LowIrradianceSummary"),
        _LOW_IRR_COUNT_COLS,
    )
    if trend.empty:
        st.info("Detector LowIrradiance tidak aktif untuk range ini.")
        return
    chart = alt.Chart(trend).mark_line(point=True).encode(
        x=alt.X("source_date:T", title="Tanggal"),
        y=alt.Y("count:Q", title="Jumlah inverter"),
        color=alt.Color("classification:N"),
    )
    st.altair_chart(chart, use_container_width=True)


def main() -> None:
    import altair as alt  # noqa: WPS433
    import streamlit as st  # noqa: WPS433

    st.set_page_config(page_title="M2 Trends", layout="wide")
    require_auth()
    st.title("M2 Daily Trends")
    st.caption(
        "Load pertama mengunduh 1 workbook per hari (bisa lambat). "
        "Pakai tombol Refresh data untuk memuat ulang.",
    )

    start_default, end_default = _default_range()
    start, end = pick_date_range(start_default, end_default)
    if st.button("Refresh data"):
        clear_dashboard_cache()
        st.rerun()

    result = cached_findings_range(start, end)
    for err in result.errors:
        st.error(err)
    if result.missing_dates:
        st.caption(
            f"{len(result.missing_dates)} hari tanpa artifact: "
            + ", ".join(str(day) for day in result.missing_dates),
        )
    if not result.sheets:
        st.info("Tidak ada workbook sheets untuk date range ini.")
        return

    sheets = result.sheets
    _render_findings_overview(st, alt, sheets)
    _render_soiling(st, alt, sheets)
    _render_intermittent(st, alt, sheets)
    _render_numeric(
        st,
        alt,
        sheets,
        "MPPT ratio (median daylight)",
        "M2b_mppt_ratio_StringStatus",
        "ratio_median_daylight",
        "Ratio",
    )
    _render_numeric(
        st,
        alt,
        sheets,
        "IForest (flagged %)",
        "M2_iforest_AnomalySummary",
        "flagged_pct",
        "Flagged %",
    )
    for label, sheet_name in _STATUS_DETECTORS:
        _render_status(st, alt, sheets, label, sheet_name)
    _render_numeric(
        st,
        alt,
        sheets,
        "Shading (jumlah jam mencurigakan)",
        "M2a_shading_ShadingSummary",
        "n_suspicious",
        "n_suspicious",
    )
    _render_low_irradiance(st, alt, sheets)
