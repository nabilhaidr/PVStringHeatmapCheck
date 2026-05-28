from __future__ import annotations

from datetime import date


def pick_date_range(default_start: date, default_end: date) -> tuple[date, date]:
    import streamlit as st  # noqa: WPS433

    selected = st.date_input(
        "Date range",
        value=(default_start, default_end),
        key="dashboard_date_range",
    )
    if isinstance(selected, tuple) and len(selected) == 2:
        return selected[0], selected[1]
    return default_start, default_end
