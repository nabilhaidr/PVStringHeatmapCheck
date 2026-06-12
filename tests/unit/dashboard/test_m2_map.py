from __future__ import annotations

from datetime import date

import pandas as pd

from pv_pipeline.dashboard.pages.m2_map import build_severity_matrix


def test_build_severity_matrix_worst_severity_wins_per_cell():
    # Satu cell (inverter, tanggal) berisi campuran severity: visual map harus
    # menampilkan yang paling parah, bukan yang terakhir/terbanyak.
    findings = pd.DataFrame({
        "source_date": [date(2026, 5, 14)] * 3,
        "inverter_id": ["WB05-INV01"] * 3,
        "sub_module": ["M2b_peer_zscore", "M2b_open_circuit", "M2b_peer_zscore"],
        "severity": ["MEDIUM", "CRITICAL", "HIGH"],
    })

    matrix = build_severity_matrix(findings, "inverter_id")

    assert len(matrix) == 1
    row = matrix.iloc[0]
    assert row["worst_severity"] == "CRITICAL"
    assert row["finding_count"] == 3
    assert row["detectors"] == "M2b_open_circuit, M2b_peer_zscore"
    assert row["source_date"] == "2026-05-14"


def test_build_severity_matrix_separates_dates_and_rows():
    findings = pd.DataFrame({
        "source_date": [date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 14)],
        "inverter_id": ["WB05-INV01", "WB05-INV01", "WB05-INV02"],
        "sub_module": ["M2e_string_proxy"] * 3,
        "severity": ["HIGH", "INFO", "CRITICAL"],
    })

    matrix = build_severity_matrix(findings, "inverter_id")

    assert len(matrix) == 3
    by_key = matrix.set_index(["inverter_id", "source_date"])["worst_severity"]
    assert by_key[("WB05-INV01", "2026-05-13")] == "HIGH"
    assert by_key[("WB05-INV01", "2026-05-14")] == "INFO"
    assert by_key[("WB05-INV02", "2026-05-14")] == "CRITICAL"


def test_build_severity_matrix_empty_or_missing_columns_returns_empty():
    assert build_severity_matrix(pd.DataFrame(), "inverter_id").empty

    no_source_date = pd.DataFrame({"inverter_id": ["WB01-INV01"], "severity": ["HIGH"]})
    assert build_severity_matrix(no_source_date, "inverter_id").empty

    no_row_col = pd.DataFrame({"source_date": [date(2026, 5, 14)], "severity": ["HIGH"]})
    assert build_severity_matrix(no_row_col, "inverter_id").empty
