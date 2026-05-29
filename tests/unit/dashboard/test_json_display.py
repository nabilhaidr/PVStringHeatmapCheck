from __future__ import annotations

import pandas as pd

from pv_pipeline.dashboard.widgets.json_display import coerce_jsonish


def test_coerce_jsonish_keeps_existing_dict():
    value, structured = coerce_jsonish({"poa_source": "auto"})

    assert structured is True
    assert value == {"poa_source": "auto"}


def test_coerce_jsonish_parses_valid_json_string():
    value, structured = coerce_jsonish('{"poa_source": "auto", "n": 12}')

    assert structured is True
    assert value == {"poa_source": "auto", "n": 12}


def test_coerce_jsonish_parses_python_dict_repr_from_excel():
    raw = "{'poa_source': 'pvlib_clearsky_ineichen', 'i_string_median': 0.0, 'n': 12}"

    value, structured = coerce_jsonish(raw)

    assert structured is True
    assert value["poa_source"] == "pvlib_clearsky_ineichen"
    assert value["i_string_median"] == 0.0
    assert value["n"] == 12


def test_coerce_jsonish_falls_back_to_text_for_truncated_repr():
    raw = "{'poa_source': 'pvlib_clearsky_ineichen', 'n'"

    value, structured = coerce_jsonish(raw)

    assert structured is False
    assert value == raw


def test_coerce_jsonish_treats_nan_as_blank_text():
    value, structured = coerce_jsonish(pd.NA)

    assert structured is False
    assert value == ""
