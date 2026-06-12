"""M2 findings visual map page: severity matrix per inverter/string/detector."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from pv_pipeline.dashboard.auth import require_auth
from pv_pipeline.dashboard.data.cache import cached_findings_range, clear_dashboard_cache
from pv_pipeline.dashboard.styles import inject_dense_css
from pv_pipeline.dashboard.widgets.date_picker import pick_date_range
from pv_pipeline.dashboard.widgets.filters import normalize_findings_df

SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "INFO", "NORMAL"]
SEVERITY_COLORS = ["#d62728", "#ff7f0e", "#e7ba52", "#1f77b4", "#2ca02c"]


def _default_range() -> tuple[date, date]:
    end = date.today()
    return end - timedelta(days=6), end


def _filter_sidebar(df: pd.DataFrame) -> pd.DataFrame:
    import streamlit as st  # noqa: WPS433

    with st.sidebar:
        selected_sev = [
            sev for sev in SEVERITY_ORDER
            if st.checkbox(sev, value=sev in SEVERITY_ORDER[:3], key=f"m2map_sev_{sev}")
        ]
        detectors = ["All"] + sorted(df["sub_module"].dropna().astype(str).unique().tolist()) if "sub_module" in df else ["All"]
        detector = st.selectbox("Detector", detectors, key="m2map_detector")
        wbs = ["All"] + sorted(df["wb_id"].dropna().astype(str).unique().tolist()) if "wb_id" in df else ["All"]
        wb = st.selectbox("WB", wbs, key="m2map_wb")

    out = df.copy()
    if selected_sev and "severity" in out:
        out = out[out["severity"].isin(selected_sev)]
    if detector != "All" and "sub_module" in out:
        out = out[out["sub_module"] == detector]
    if wb != "All" and "wb_id" in out:
        out = out[out["wb_id"] == wb]
    return out


def build_severity_matrix(df: pd.DataFrame, row_col: str) -> pd.DataFrame:
    """Aggregate findings into (row_col x source_date) cells.

    Returns one row per cell with ``worst_severity`` (CRITICAL paling parah),
    ``finding_count``, dan daftar ``detectors``.
    """
    if df.empty or row_col not in df.columns or "source_date" not in df.columns:
        return pd.DataFrame()
    out = df.copy()
    out["source_date"] = pd.to_datetime(out["source_date"], errors="coerce").dt.date.astype(str)
    rank = {sev: idx for idx, sev in enumerate(SEVERITY_ORDER)}
    out["_rank"] = out.get("severity", pd.Series(dtype=str)).map(rank).fillna(len(SEVERITY_ORDER))
    if "sub_module" not in out.columns:
        out["sub_module"] = ""
    grouped = (
        out.groupby([row_col, "source_date"], dropna=False)
        .agg(
            _worst_rank=("_rank", "min"),
            finding_count=("_rank", "size"),
            detectors=("sub_module", lambda s: ", ".join(sorted(set(s.dropna().astype(str))))),
        )
        .reset_index()
    )
    rank_to_sev = {idx: sev for sev, idx in rank.items()}
    grouped["worst_severity"] = grouped["_worst_rank"].map(rank_to_sev).fillna("UNKNOWN")
    return grouped.drop(columns="_worst_rank")


def _severity_color_scale():
    import altair as alt  # noqa: WPS433

    return alt.Scale(domain=SEVERITY_ORDER, range=SEVERITY_COLORS)


def _render_severity_matrix(matrix: pd.DataFrame, row_col: str, row_title: str) -> None:
    import altair as alt  # noqa: WPS433
    import streamlit as st  # noqa: WPS433

    chart = alt.Chart(matrix).mark_rect(stroke="white", strokeWidth=1).encode(
        x=alt.X("source_date:O", title="Date"),
        y=alt.Y(f"{row_col}:N", title=row_title),
        color=alt.Color("worst_severity:N", title="Worst severity", scale=_severity_color_scale()),
        tooltip=[
            alt.Tooltip(f"{row_col}:N", title=row_title),
            alt.Tooltip("source_date:O", title="Date"),
            alt.Tooltip("worst_severity:N", title="Worst severity"),
            alt.Tooltip("finding_count:Q", title="Findings"),
            alt.Tooltip("detectors:N", title="Detectors"),
        ],
    ).properties(height=max(120, 24 * matrix[row_col].nunique()))
    st.altair_chart(chart, use_container_width=True)


def main() -> None:
    import altair as alt  # noqa: WPS433
    import streamlit as st  # noqa: WPS433

    st.set_page_config(page_title="M2 Visual Map", layout="wide")
    require_auth()
    inject_dense_css()

    st.title("M2 Visual Map")
    st.caption("Peta severity M2 findings: inverter x tanggal, PV string x tanggal, dan detector x tanggal.")

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
    if filtered.empty:
        st.info("Tidak ada findings yang lolos filter.")
        return
    st.caption(f"Showing {len(filtered):,} findings (filtered from {len(findings):,})")

    st.subheader("Inverter x Date")
    inv_matrix = build_severity_matrix(filtered, "inverter_id")
    if inv_matrix.empty:
        st.info("Kolom inverter_id/source_date tidak tersedia.")
    else:
        _render_severity_matrix(inv_matrix, "inverter_id", "Inverter")

    st.subheader("PV String x Date")
    with_string = filtered[filtered.get("pv_string", pd.Series(dtype=str)).notna()] if "pv_string" in filtered else pd.DataFrame()
    if with_string.empty:
        st.info("Tidak ada findings dengan pv_string untuk date range ini.")
    else:
        inverters = sorted(with_string["inverter_id"].dropna().astype(str).unique())
        selected_inv = st.selectbox("Inverter", inverters, key="m2map_string_inverter")
        per_inv = with_string[with_string["inverter_id"] == selected_inv].copy()
        per_inv["pv_string"] = per_inv["pv_string"].astype(str)
        string_matrix = build_severity_matrix(per_inv, "pv_string")
        if string_matrix.empty:
            st.info("Tidak ada findings pv_string untuk inverter ini.")
        else:
            _render_severity_matrix(string_matrix, "pv_string", "PV String")

    st.subheader("Detector x Date")
    det_matrix = build_severity_matrix(filtered, "sub_module")
    if det_matrix.empty:
        st.info("Kolom sub_module/source_date tidak tersedia.")
    else:
        chart = alt.Chart(det_matrix).mark_rect(stroke="white", strokeWidth=1).encode(
            x=alt.X("source_date:O", title="Date"),
            y=alt.Y("sub_module:N", title="Detector"),
            color=alt.Color("finding_count:Q", title="Findings", scale=alt.Scale(scheme="oranges")),
            tooltip=[
                alt.Tooltip("sub_module:N", title="Detector"),
                alt.Tooltip("source_date:O", title="Date"),
                alt.Tooltip("worst_severity:N", title="Worst severity"),
                alt.Tooltip("finding_count:Q", title="Findings"),
            ],
        ).properties(height=max(120, 24 * det_matrix["sub_module"].nunique()))
        st.altair_chart(chart, use_container_width=True)


if __name__ == "__main__":
    main()
