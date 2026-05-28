from __future__ import annotations

import pandas as pd


def first_available_sheet(sheets: dict[str, pd.DataFrame], names: list[str]) -> tuple[str, pd.DataFrame] | None:
    for name in names:
        if name in sheets:
            return name, sheets[name]
    lowered = {key.lower(): key for key in sheets}
    for name in names:
        key = lowered.get(name.lower())
        if key is not None:
            return key, sheets[key]
    return None


def render_detector_tab(label: str, sheets: dict[str, pd.DataFrame], aliases: list[str]) -> None:
    import altair as alt  # noqa: WPS433
    import streamlit as st  # noqa: WPS433

    selected = first_available_sheet(sheets, aliases)
    if selected is None:
        st.info(f"Detector {label} tidak aktif atau sheet tidak tersedia untuk range ini.")
        return
    sheet_name, df = selected
    st.caption(f"Sheet: {sheet_name} | Rows: {len(df):,}")
    if df.empty:
        st.info("Sheet tersedia tapi kosong.")
        return

    status_cols = [col for col in df.columns if str(col).lower() in {"status", "severity", "classification"}]
    if status_cols:
        col = status_cols[0]
        counts = df[col].astype(str).value_counts().reset_index()
        counts.columns = [col, "count"]
        chart = alt.Chart(counts).mark_bar().encode(x=alt.X("count:Q"), y=alt.Y(f"{col}:N", sort="-x"))
        st.altair_chart(chart, use_container_width=True)
    else:
        numeric = df.select_dtypes(include="number").columns.tolist()
        if numeric:
            col = numeric[0]
            chart = alt.Chart(df[[col]].dropna()).mark_bar().encode(x=alt.X(f"{col}:Q", bin=True), y="count()")
            st.altair_chart(chart, use_container_width=True)

    st.dataframe(df, use_container_width=True, height=420)
    st.download_button(
        "Download tab CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name=f"{label.lower()}_{sheet_name}.csv",
        mime="text/csv",
    )
