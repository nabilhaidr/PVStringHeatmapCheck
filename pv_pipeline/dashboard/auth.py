"""Shared password gate for the Streamlit dashboard."""

from __future__ import annotations

import hmac


def _check_password(input_password: str, expected_password: str) -> bool:
    """Return True only for a non-empty exact password match."""
    if not input_password or not expected_password:
        return False
    return hmac.compare_digest(str(input_password), str(expected_password))


def require_auth() -> None:
    """Render login and stop the Streamlit page unless the session is authed."""
    import streamlit as st  # noqa: WPS433

    if st.session_state.get("authed"):
        _render_logout_sidebar(st)
        return
    _render_login(st)
    st.stop()


def _render_login(st) -> None:
    st.title("PV Pipeline Dashboard")
    st.caption("Masukkan password untuk masuk.")
    password = st.text_input("Password", type="password")
    if st.button("Masuk", type="primary"):
        expected = st.secrets.get("dashboard", {}).get("password", "")
        if _check_password(password, expected):
            st.session_state.authed = True
            st.rerun()
        st.error("Password salah.")


def _render_logout_sidebar(st) -> None:
    with st.sidebar:
        if st.button("Logout"):
            st.session_state.clear()
            st.rerun()
