"""Overview page for the PV Pipeline Streamlit dashboard."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from pv_pipeline.dashboard.auth import require_auth
from pv_pipeline.dashboard.data.cache import cached_findings_range, clear_dashboard_cache
from pv_pipeline.dashboard.styles import inject_clean_css
from pv_pipeline.dashboard.widgets.date_picker import pick_date_range
from pv_pipeline.dashboard.widgets.filters import normalize_findings_df
from pv_pipeline.dashboard.widgets.kpi import render_kpis


def _default_range() -> tuple[date, date]:
    end = date.today()
    return end - timedelta(days=6), end


def _render_warnings(result) -> None:
    import streamlit as st  # noqa: WPS433

    if result.missing_dates:
        missing = ", ".join(day.isoformat() for day in result.missing_dates[:10])
        st.caption(f"Hari tanpa findings xlsx: {missing}")
    for err in result.errors:
        st.warning(err)


def _severity_order(df: pd.DataFrame) -> pd.DataFrame:
    order = ["CRITICAL", "HIGH", "MEDIUM", "INFO", "NORMAL"]
    out = df.copy()
    out["severity"] = pd.Categorical(out["severity"], categories=order, ordered=True)
    return out


def main() -> None:
    import altair as alt  # noqa: WPS433
    import streamlit as st  # noqa: WPS433

    st.set_page_config(page_title="PV Pipeline Dashboard", layout="wide")
    require_auth()
    inject_clean_css()

    st.title("PV Pipeline Dashboard")
    st.caption("M2 findings overview dari Google Drive.")
    if st.button("Refresh data", type="primary"):
        clear_dashboard_cache()
        st.rerun()

    start_default, end_default = _default_range()
    start, end = pick_date_range(start_default, end_default)
    result = cached_findings_range(start, end)
    _render_warnings(result)

    findings = normalize_findings_df(result.sheets.get("Findings", pd.DataFrame()))
    all_strings = result.sheets.get("M2e_hybrid_AllStrings", pd.DataFrame())
    if findings.empty:
        render_kpis([
            ("FINDINGS", "0"),
            ("CRITICAL", "0"),
            ("FLEET UPTIME", "-"),
            ("INVERTERS", "-"),
        ])
        st.info("Tidak ada findings untuk date range ini.")
        return

    critical = int((findings.get("severity", pd.Series(dtype=str)) == "CRITICAL").sum())
    inverters = findings["inverter_id"].nunique() if "inverter_id" in findings else 0
    uptime = "-"
    if not all_strings.empty and "uptime_pct" in all_strings:
        uptime = f"{pd.to_numeric(all_strings['uptime_pct'], errors='coerce').mean():.1f}%"
    render_kpis([
        ("FINDINGS", f"{len(findings):,}"),
        ("CRITICAL", f"{critical:,}"),
        ("FLEET UPTIME", uptime),
        ("INVERTERS", f"{inverters:,}"),
    ])

    left, right = st.columns(2)
    with left:
        st.subheader("Findings per WB")
        if "wb_id" in findings:
            wb_counts = findings.groupby(["wb_id", "severity"], dropna=False).size().reset_index(name="count")
            chart = alt.Chart(_severity_order(wb_counts)).mark_bar().encode(
                x=alt.X("wb_id:N", title="WB"),
                y=alt.Y("count:Q", title="Findings"),
                color="severity:N",
                tooltip=["wb_id", "severity", "count"],
            )
            st.altair_chart(chart, use_container_width=True)
    with right:
        st.subheader("Findings per Detector")
        if "sub_module" in findings:
            det_counts = findings["sub_module"].value_counts().reset_index()
            det_counts.columns = ["sub_module", "count"]
            chart = alt.Chart(det_counts).mark_bar().encode(
                x=alt.X("count:Q", title="Findings"),
                y=alt.Y("sub_module:N", sort="-x", title="Detector"),
                tooltip=["sub_module", "count"],
            )
            st.altair_chart(chart, use_container_width=True)

    st.subheader("Severity Trend")
    if "source_date" in findings:
        trend = findings.groupby(["source_date", "severity"], dropna=False).size().reset_index(name="count")
        chart = alt.Chart(_severity_order(trend)).mark_line(point=True).encode(
            x=alt.X("source_date:T", title="Date"),
            y=alt.Y("count:Q", title="Findings"),
            color="severity:N",
            tooltip=["source_date", "severity", "count"],
        )
        st.altair_chart(chart, use_container_width=True)


if __name__ == "__main__":
    main()
