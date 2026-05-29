"""Dense findings browser page."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from pv_pipeline.dashboard.auth import require_auth
from pv_pipeline.dashboard.data.cache import cached_findings_range, clear_dashboard_cache
from pv_pipeline.dashboard.styles import inject_dense_css
from pv_pipeline.dashboard.widgets.date_picker import pick_date_range
from pv_pipeline.dashboard.widgets.filters import normalize_findings_df
from pv_pipeline.dashboard.widgets.json_display import render_jsonish


def _default_range() -> tuple[date, date]:
    end = date.today()
    return end - timedelta(days=6), end


def _filter_sidebar(df: pd.DataFrame) -> pd.DataFrame:
    import streamlit as st  # noqa: WPS433

    with st.sidebar:
        severities = ["CRITICAL", "HIGH", "MEDIUM", "INFO", "NORMAL"]
        selected_sev = [sev for sev in severities if st.checkbox(sev, value=sev in severities[:3])]
        detectors = ["All"] + sorted(df["sub_module"].dropna().astype(str).unique().tolist()) if "sub_module" in df else ["All"]
        detector = st.selectbox("Detector", detectors)
        inverters = ["All"] + sorted(df["inverter_id"].dropna().astype(str).unique().tolist()) if "inverter_id" in df else ["All"]
        inverter = st.selectbox("Inverter", inverters)
        wbs = ["All"] + sorted(df["wb_id"].dropna().astype(str).unique().tolist()) if "wb_id" in df else ["All"]
        wb = st.selectbox("WB", wbs)

    out = df.copy()
    if selected_sev and "severity" in out:
        out = out[out["severity"].isin(selected_sev)]
    if detector != "All" and "sub_module" in out:
        out = out[out["sub_module"] == detector]
    if inverter != "All" and "inverter_id" in out:
        out = out[out["inverter_id"] == inverter]
    if wb != "All" and "wb_id" in out:
        out = out[out["wb_id"] == wb]
    return out


def main() -> None:
    import streamlit as st  # noqa: WPS433

    st.set_page_config(page_title="Findings Browser", layout="wide")
    require_auth()
    inject_dense_css()

    st.title("Findings Browser")
    start_default, end_default = _default_range()
    start, end = pick_date_range(start_default, end_default)
    if st.button("Refresh data"):
        clear_dashboard_cache()
        st.rerun()

    result = cached_findings_range(start, end)
    for err in result.errors:
        st.error(err)
    findings = normalize_findings_df(result.sheets.get("Findings", pd.DataFrame()))
    if findings.empty:
        st.info("Tidak ada findings untuk date range ini.")
        return
    filtered = _filter_sidebar(findings)
    st.caption(f"Showing {len(filtered):,} findings (filtered from {len(findings):,})")
    st.download_button(
        "Export filtered CSV",
        filtered.to_csv(index=False).encode("utf-8"),
        file_name="filtered_findings.csv",
        mime="text/csv",
    )
    event = st.dataframe(
        filtered,
        use_container_width=True,
        height=600,
        on_select="rerun",
        selection_mode="single-row",
    )
    selected_rows = getattr(getattr(event, "selection", None), "rows", [])
    if selected_rows:
        row = filtered.iloc[selected_rows[0]]
        with st.expander("Detail finding", expanded=True):
            st.write(row.drop(labels=["extra", "evidence"], errors="ignore").to_frame("value"))
            if "extra" in row:
                render_jsonish("extra", row["extra"])
            if "evidence" in row:
                render_jsonish("evidence", row["evidence"])
