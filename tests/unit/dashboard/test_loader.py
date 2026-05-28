from __future__ import annotations

from datetime import date
from io import BytesIO

import pandas as pd
import pytest

from pv_pipeline.dashboard.data.loader import (
    concat_findings_range,
    load_baseline_csv_day,
    load_findings_workbook,
    parse_baseline_csv_date,
    parse_findings_date,
)


def test_parse_findings_date_accepts_pipeline_output_name():
    assert parse_findings_date("m2_findings_20260514.xlsx") == date(2026, 5, 14)


def test_parse_findings_date_rejects_non_pipeline_output_name():
    assert parse_findings_date("findings_20260514.xlsx") is None
    assert parse_findings_date("m2_findings_2026-05-14.xlsx") is None


def test_parse_baseline_csv_date_accepts_baseline_output_name():
    assert parse_baseline_csv_date("2026-05-14.csv") == date(2026, 5, 14)


def test_parse_baseline_csv_date_rejects_other_csv_names():
    assert parse_baseline_csv_date("manifest.csv") is None
    assert parse_baseline_csv_date("20260514.csv") is None


def test_load_findings_workbook_preserves_sheet_names():
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        pd.DataFrame({"inverter_id": ["WB01-INV01"]}).to_excel(
            writer,
            sheet_name="Findings",
            index=False,
        )
        pd.DataFrame({"status": ["NORMAL"]}).to_excel(
            writer,
            sheet_name="M2e_hybrid_AllStrings",
            index=False,
        )
    bio.seek(0)

    sheets = load_findings_workbook(bio)

    assert set(sheets) == {"Findings", "M2e_hybrid_AllStrings"}
    assert sheets["Findings"].loc[0, "inverter_id"] == "WB01-INV01"


def test_concat_findings_range_adds_source_date_per_sheet():
    day1 = date(2026, 5, 14)
    day2 = date(2026, 5, 15)
    per_day = {
        day1: {
            "Findings": pd.DataFrame({
                "timestamp": ["2026-05-14T08:00:00"],
                "severity": ["HIGH"],
            }),
            "DetectorSheet": pd.DataFrame({"status": ["NORMAL"]}),
        },
        day2: {
            "Findings": pd.DataFrame({
                "timestamp": ["2026-05-15T08:00:00"],
                "severity": ["MEDIUM"],
            }),
            "DetectorSheet": pd.DataFrame({"status": ["HIGH"]}),
        },
    }

    combined = concat_findings_range(per_day)

    assert combined["Findings"]["source_date"].tolist() == [day1, day2]
    assert combined["DetectorSheet"]["source_date"].tolist() == [day1, day2]


def test_load_baseline_csv_day_derives_wb_from_inverter_id():
    csv_bytes = BytesIO(
        (
            "Inverter_ID,Start Time,PV1 Power(kW),PV2 Power(kW)\n"
            "WB02-INV05,2026-05-14 06:00,1.2,1.3\n"
        ).encode("utf-8")
    )

    df = load_baseline_csv_day(csv_bytes)

    assert df.loc[0, "WB"] == "WB02"
    assert pd.api.types.is_datetime64_any_dtype(df["Start Time"])


def test_load_baseline_csv_day_fails_loud_without_required_columns():
    csv_bytes = BytesIO("Inverter_ID,PV1 Power(kW)\nWB02-INV05,1.2\n".encode("utf-8"))

    with pytest.raises(ValueError, match="Start Time"):
        load_baseline_csv_day(csv_bytes)


def test_load_baseline_csv_day_fails_loud_without_pv_power_columns():
    csv_bytes = BytesIO(
        "Inverter_ID,Start Time,Active power(kW)\n"
        "WB02-INV05,2026-05-14 06:00,1.2\n".encode("utf-8")
    )

    with pytest.raises(ValueError, match="PV.*Power"):
        load_baseline_csv_day(csv_bytes)
