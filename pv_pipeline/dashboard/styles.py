"""Small CSS helpers for Streamlit dashboard pages."""

from __future__ import annotations


def inject_clean_css() -> None:
    import streamlit as st  # noqa: WPS433

    st.markdown(
        """
        <style>
        .block-container { padding-top: 2rem; }
        div[data-testid="stMetric"] {
            border: 1px solid #e4e7ec;
            border-radius: 8px;
            padding: 0.75rem 0.9rem;
            background: #ffffff;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def inject_dense_css() -> None:
    import streamlit as st  # noqa: WPS433

    st.markdown(
        """
        <style>
        .stApp { background: #101114; color: #e7e7ea; }
        .block-container { padding-top: 1.5rem; max-width: 1500px; }
        div[data-testid="stDataFrame"] {
            border: 1px solid #353844;
            border-radius: 6px;
        }
        div[data-testid="stDataFrame"] * {
            font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
            font-size: 0.86rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
