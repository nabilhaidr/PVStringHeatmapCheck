"""Heatmap page backed by one baseline CSV day."""

from __future__ import annotations

from datetime import date

from pv_pipeline.dashboard.auth import require_auth
from pv_pipeline.dashboard.data.cache import cached_baseline_csv_day, clear_dashboard_cache


def main() -> None:
    import matplotlib.pyplot as plt  # noqa: WPS433
    import streamlit as st  # noqa: WPS433

    from pv_pipeline.string_config import get_empty_pv_map
    from pv_pipeline.viz import plot_single_inv_heatmap

    st.set_page_config(page_title="PV Heatmap", layout="wide")
    require_auth()

    st.title("Heatmap String PV")
    st.caption("Source: baseline YYYY-MM-DD.csv. Data ini sudah filtered NORMAL oleh BaselineAccumulator.")
    with st.sidebar:
        selected_day = st.date_input("Date", value=date.today(), key="heatmap_date")
        if st.button("Refresh data"):
            clear_dashboard_cache()
            st.rerun()

    result = cached_baseline_csv_day(selected_day)
    if result.error:
        st.error(result.error)
        return
    if result.missing:
        st.info("Baseline CSV untuk tanggal ini tidak tersedia di Google Drive.")
        if result.available_dates:
            st.caption("Tanggal tersedia: " + ", ".join(d.isoformat() for d in result.available_dates[-10:]))
        return
    df = result.dataframe
    if df.empty:
        st.info("Baseline CSV kosong.")
        return

    inverters = sorted(df["Inverter_ID"].dropna().astype(str).unique())
    selected_inv = st.sidebar.selectbox("Inverter", inverters)
    try:
        empty_map = get_empty_pv_map("config/strings.yaml", pv_max_allowed=28)
    except Exception:
        empty_map = {}
    try:
        plot_single_inv_heatmap(
            selected_inv,
            df,
            show=False,
            close_after_show=False,
            empty_pv_map=empty_map,
        )
        fig = plt.gcf()
        st.pyplot(fig, clear_figure=True)
        plt.close(fig)
    except Exception as exc:
        st.error("Gagal render heatmap.")
        with st.expander("Detail traceback"):
            st.exception(exc)
