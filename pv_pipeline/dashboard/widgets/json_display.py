"""Helpers for displaying JSON-like values from xlsx/jsonl artifacts."""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping, Sequence

import pandas as pd


def coerce_jsonish(value: object) -> tuple[object, bool]:
    """Return a structured object when ``value`` is JSON or Python-literal-like.

    M2 xlsx cells can contain Python ``repr(dict)`` strings with single quotes.
    Streamlit's ``st.json`` only accepts valid JSON strings, so callers should
    coerce first and fall back to text when parsing fails.
    """
    if isinstance(value, Mapping):
        return dict(value), True
    if isinstance(value, list | tuple):
        return list(value), True
    if value is None:
        return "", False
    try:
        if pd.isna(value):
            return "", False
    except (TypeError, ValueError):
        pass

    text = str(value).strip()
    if not text:
        return "", False

    try:
        parsed = json.loads(text)
        if isinstance(parsed, Mapping):
            return dict(parsed), True
        if isinstance(parsed, Sequence) and not isinstance(parsed, str):
            return list(parsed), True
        return parsed, True
    except json.JSONDecodeError:
        pass

    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return text, False

    if isinstance(parsed, Mapping):
        return dict(parsed), True
    if isinstance(parsed, list | tuple):
        return list(parsed), True
    return parsed, True


def render_jsonish(label: str, value: object) -> None:
    """Render JSON-like value safely in Streamlit."""
    import streamlit as st  # noqa: WPS433

    parsed, structured = coerce_jsonish(value)
    if parsed == "":
        return
    st.caption(label)
    if structured:
        st.json(parsed)
    else:
        st.code(str(parsed), language="text")
