"""Heatmap page backed by one df_plot export CSV day (CSV Export PV String)."""

from __future__ import annotations

from datetime import date

from pv_pipeline.dashboard.auth import require_auth
from pv_pipeline.dashboard.data.cache import cached_pv_export_csv_day, clear_dashboard_cache


def _render_inverter_heatmap(df, inv_id: str, empty_map: dict) -> None:
    import matplotlib.pyplot as plt  # noqa: WPS433
    import streamlit as st  # noqa: WPS433

    from pv_pipeline.viz import plot_single_inv_heatmap

    try:
        plot_single_inv_heatmap(
            inv_id,
            df,
            show=False,
            close_after_show=False,
            empty_pv_map=empty_map,
        )
        fig = plt.gcf()
        st.pyplot(fig, clear_figure=True)
        plt.close(fig)
    except Exception as exc:
        st.error(f"Gagal render heatmap {inv_id}.")
        with st.expander("Detail traceback"):
            st.exception(exc)


def main() -> None:
    import streamlit as st  # noqa: WPS433

    from pv_pipeline.string_config import get_empty_pv_map

    st.set_page_config(page_title="PV Heatmap", layout="wide")
    require_auth()

    st.title("Heatmap String PV")
    st.caption(
        "Source: CSV Export PV String YYYYMMDD.csv (df_plot dari notebook Cell 8, "
        "data penuh tanpa filter NORMAL)."
    )
    with st.sidebar:
        selected_day = st.date_input("Date", value=date.today(), key="heatmap_date")
        show_all = st.toggle("Tampilkan semua inverter", value=False, key="heatmap_show_all")
        if st.button("Refresh data"):
            clear_dashboard_cache()
            st.rerun()

    result = cached_pv_export_csv_day(selected_day)
    if result.error:
        st.error(result.error)
        return
    if result.missing:
        st.info("CSV export (YYYYMMDD.csv) untuk tanggal ini tidak tersedia di Google Drive.")
        if result.available_dates:
            st.caption("Tanggal tersedia: " + ", ".join(d.isoformat() for d in result.available_dates[-10:]))
        return
    df = result.dataframe
    if df.empty:
        st.info("CSV export kosong.")
        return

    inverters = sorted(df["Inverter_ID"].dropna().astype(str).unique())
    try:
        empty_map = get_empty_pv_map("config/strings.yaml", pv_max_allowed=28)
    except Exception:
        empty_map = {}

    if not show_all:
        selected_inv = st.sidebar.selectbox("Inverter", inverters)
        _render_inverter_heatmap(df, selected_inv, empty_map)
        return

    wbs = sorted(df["WB"].dropna().astype(str).unique())
    selected_wbs = st.sidebar.multiselect("WB", wbs, default=wbs, key="heatmap_wbs")
    targets = [inv for inv in inverters if inv.split("-")[0] in selected_wbs]
    if not targets:
        st.info("Tidak ada inverter untuk WB terpilih.")
        return
    st.caption(f"Render {len(targets)} heatmap inverter. Untuk fleet besar render bisa memakan waktu.")
    progress = st.progress(0.0)
    for idx, inv in enumerate(targets, start=1):
        st.subheader(inv)
        _render_inverter_heatmap(df, inv, empty_map)
        progress.progress(idx / len(targets))
    progress.empty()
