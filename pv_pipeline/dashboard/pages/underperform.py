"""PV string underperform dashboard page."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from matplotlib.patches import Rectangle

from pv_pipeline.dashboard.auth import require_auth
from pv_pipeline.dashboard.data.cache import (
    cached_baseline_csv_day,
    cached_findings_range,
    clear_dashboard_cache,
)
from pv_pipeline.dashboard.data.underperform import (
    analyze_inverter_strings,
    build_string_timeseries,
    summarize_pv_string_findings,
)
from pv_pipeline.dashboard.styles import inject_dense_css
from pv_pipeline.dashboard.widgets.date_picker import pick_date_range
from pv_pipeline.dashboard.widgets.filters import normalize_findings_df
from pv_pipeline.dashboard.widgets.json_display import render_jsonish


def _default_range() -> tuple[date, date]:
    end = date.today()
    return end - timedelta(days=6), end


def _filter_summary(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    import streamlit as st  # noqa: WPS433

    with st.sidebar:
        severities = ["CRITICAL", "HIGH", "MEDIUM", "INFO", "NORMAL"]
        selected_sev = [
            sev for sev in severities
            if st.checkbox(sev, value=sev in severities[:3], key=f"underperform_sev_{sev}")
        ]
        detectors = ["All"] + sorted(df["sub_module"].dropna().astype(str).unique().tolist()) if "sub_module" in df else ["All"]
        detector = st.selectbox("Detector", detectors, key="underperform_detector")
        wbs = ["All"] + sorted(df["wb_id"].dropna().astype(str).unique().tolist()) if "wb_id" in df else ["All"]
        wb = st.selectbox("WB", wbs, key="underperform_wb")
        inverters = ["All"] + sorted(df["inverter_id"].dropna().astype(str).unique().tolist()) if "inverter_id" in df else ["All"]
        inverter = st.selectbox("Inverter", inverters, key="underperform_inverter")
        show_baseline = st.toggle("Show baseline snapshot if available", value=True)

    out = df.copy()
    if selected_sev and "worst_severity" in out:
        out = out[out["worst_severity"].isin(selected_sev)]
    if detector != "All" and "sub_module" in out:
        out = out[out["sub_module"] == detector]
    if wb != "All" and "wb_id" in out:
        out = out[out["wb_id"] == wb]
    if inverter != "All" and "inverter_id" in out:
        out = out[out["inverter_id"] == inverter]
    return out, show_baseline


def _source_date(value: object) -> date | None:
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


def _matching_findings(findings: pd.DataFrame, selected: pd.Series) -> pd.DataFrame:
    if findings.empty:
        return findings
    out = findings.copy()
    if "source_date" in out:
        out["_source_date_cmp"] = pd.to_datetime(out["source_date"], errors="coerce").dt.date
        selected_day = _source_date(selected.get("source_date"))
        if selected_day is not None:
            out = out[out["_source_date_cmp"] == selected_day]
    for col in ["inverter_id", "pv_string", "sub_module"]:
        if col in out.columns and col in selected:
            out = out[out[col].astype(str) == str(selected[col])]
    return out.drop(columns=["_source_date_cmp"], errors="ignore")


def _load_empty_map() -> dict:
    try:
        from pv_pipeline.string_config import get_empty_pv_map  # noqa: WPS433
        return get_empty_pv_map("config/strings.yaml", pv_max_allowed=28)
    except Exception:
        return {}


def _highlight_heatmap_row(ax, pivot: pd.DataFrame, pv_string: str) -> bool:
    """Draw an outline around the selected PV row in a Cell-3 heatmap."""
    label = f"{str(pv_string).strip().upper()} Power(kW)"
    if pivot is None or pivot.empty or label not in pivot.index:
        return False
    row_idx = list(pivot.index).index(label)
    ax.add_patch(Rectangle(
        (0, row_idx),
        pivot.shape[1],
        1,
        facecolor="none",
        edgecolor="#00A3FF",
        linewidth=2.4,
        zorder=8,
    ))
    return True


def _render_selected_heatmap(
    df: pd.DataFrame,
    inverter_id: str,
    pv_string: str,
    empty_map: dict,
) -> None:
    import matplotlib.pyplot as plt  # noqa: WPS433
    import streamlit as st  # noqa: WPS433

    from pv_pipeline.viz import plot_single_inv_heatmap

    st.subheader("Selected String Heatmap")
    st.caption("Heatmap memakai logic Cell 3: peer-relative normalized PV power per timestamp.")
    try:
        pivot, _ = plot_single_inv_heatmap(
            inverter_id,
            df,
            show=False,
            close_after_show=False,
            empty_pv_map=empty_map,
        )
        fig = plt.gcf()
        ax = fig.axes[0] if fig.axes else plt.gca()
        highlighted = _highlight_heatmap_row(ax, pivot, pv_string)
        if not highlighted:
            st.info(f"Row {pv_string} tidak ditemukan di heatmap baseline untuk {inverter_id}.")
        st.pyplot(fig, clear_figure=True)
        plt.close(fig)
    except Exception as exc:
        st.error("Gagal render heatmap selected string.")
        with st.expander("Detail traceback"):
            st.exception(exc)


def _render_baseline_context(selected: pd.Series) -> None:
    import altair as alt  # noqa: WPS433
    import streamlit as st  # noqa: WPS433

    selected_day = _source_date(selected.get("source_date"))
    if selected_day is None:
        st.info("Tanggal source finding tidak valid, baseline snapshot tidak bisa dimuat.")
        return

    result = cached_baseline_csv_day(selected_day)
    if result.error:
        st.error(result.error)
        return
    if result.missing:
        st.info("Baseline CSV untuk tanggal finding ini tidak tersedia di Google Drive.")
        return
    if result.dataframe.empty:
        st.info("Baseline CSV tersedia tapi kosong.")
        return

    empty_map = _load_empty_map()
    inverter_id = str(selected["inverter_id"])
    pv_string = str(selected["pv_string"])
    ts_df, message = build_string_timeseries(
        result.dataframe,
        inverter_id,
        pv_string,
        empty_pv_map=empty_map,
    )
    if ts_df.empty:
        st.info(message or "Baseline time-series tidak tersedia untuk string ini.")
        return
    if message:
        st.warning(message + " Baseline mungkin sudah dinormalisasi oleh auto-skip per-PV.")

    st.subheader("Baseline Time-Series Context")
    chart_df = ts_df.melt(
        id_vars=["Start Time"],
        value_vars=["pv_power_kw", "sibling_median_power_kw"],
        var_name="metric",
        value_name="value_kw",
    ).dropna(subset=["value_kw"])
    if not chart_df.empty:
        chart = alt.Chart(chart_df).mark_line(point=True).encode(
            x=alt.X("Start Time:T", title="Time"),
            y=alt.Y("value_kw:Q", title="Power (kW)"),
            color=alt.Color("metric:N", title="Metric"),
            tooltip=["Start Time:T", "metric:N", "value_kw:Q"],
        )
        st.altair_chart(chart, use_container_width=True)

    cols = ["Start Time", "pv_power_kw", "sibling_median_power_kw", "power_ratio_to_sibling", "cell3_norm"]
    if "pv_current_a" in ts_df.columns:
        cols.append("pv_current_a")
    st.dataframe(ts_df[cols], use_container_width=True, height=260)

    _render_selected_heatmap(result.dataframe, inverter_id, pv_string, empty_map)

    analysis = analyze_inverter_strings(
        result.dataframe,
        inverter_id,
        empty_pv_map=empty_map,
    )
    if not analysis.empty:
        with st.expander("Display-only inverter string metrics", expanded=False):
            st.caption("Metrics ini mengikuti normalisasi Cell 3 dan tidak membuat M2 finding baru.")
            st.dataframe(analysis, use_container_width=True, height=280)


def main() -> None:
    import streamlit as st  # noqa: WPS433

    st.set_page_config(page_title="PV String Underperform", layout="wide")
    require_auth()
    inject_dense_css()

    st.title("PV String Underperform")
    st.caption("Primary signal: existing M2 findings with populated pv_string. Baseline CSV is display-only context.")

    start_default, end_default = _default_range()
    start, end = pick_date_range(start_default, end_default)
    if st.button("Refresh data"):
        clear_dashboard_cache()
        st.rerun()

    result = cached_findings_range(start, end)
    for err in result.errors:
        st.error(err)
    findings = normalize_findings_df(result.sheets.get("Findings", pd.DataFrame()))
    summary = summarize_pv_string_findings(findings)
    if summary.empty:
        st.info("Tidak ada findings dengan pv_string untuk date range ini.")
        return

    filtered, show_baseline = _filter_summary(summary)
    display_cols = [
        "source_date", "wb_id", "inverter_id", "pv_string", "sub_module",
        "finding_count", "worst_severity", "latest_timestamp",
        "fault_types", "max_confidence",
    ]
    st.caption(f"Showing {len(filtered):,} PV string groups (filtered from {len(summary):,})")
    event = st.dataframe(
        filtered[display_cols],
        use_container_width=True,
        height=420,
        on_select="rerun",
        selection_mode="single-row",
    )
    selected_rows = getattr(getattr(event, "selection", None), "rows", [])
    if not selected_rows:
        st.info("Pilih satu PV string row untuk melihat detail dan baseline context.")
        return

    selected = filtered.iloc[selected_rows[0]]
    details = _matching_findings(findings, selected)
    with st.expander("Selected string findings", expanded=True):
        st.dataframe(details, use_container_width=True, height=260)
        if not details.empty and "evidence" in details.columns:
            first_evidence = details.iloc[0].get("evidence")
            if pd.notna(first_evidence):
                render_jsonish("evidence", first_evidence)

    if show_baseline:
        _render_baseline_context(selected)


if __name__ == "__main__":
    main()
