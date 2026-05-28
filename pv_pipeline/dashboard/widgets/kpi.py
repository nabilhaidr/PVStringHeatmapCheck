from __future__ import annotations


def render_kpis(metrics: list[tuple[str, str]]) -> None:
    import streamlit as st  # noqa: WPS433

    cols = st.columns(len(metrics) or 1)
    for col, (label, value) in zip(cols, metrics):
        col.metric(label, value)
