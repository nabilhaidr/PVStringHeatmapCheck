# All-String Daily Yield Notebook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a separate Colab notebook that selectively downloads daily CSV files, calculates observed daily kWh for every detected PV string, and exports an auditable three-sheet Excel workbook.

**Architecture:** Add a power-only sibling module rather than adding an all-string mode to the existing POA-aware single-string report. Read every selected CSV once, immediately reduce its tidy 5-minute rows to daily per-string statistics, derive the union without concatenating days in memory, pivot the wide recap, and export it through a deterministic notebook artifact.

**Tech Stack:** Python 3, pandas, NumPy, openpyxl, gdown 6+, pytest, nbformat-compatible JSON, Google Colab.

## Global Constraints

- Default `URL_CSV` is exactly `https://drive.google.com/drive/folders/1f_KrPuqfZJTE5I9cVQiyp65QrHbkF3Iw?usp=sharing`.
- Default `START_DATE` is `2026-05-01` and default `END_DATE` is `2026-05-14`.
- User-editable notebook values are only `URL_CSV`, `START_DATE`, and `END_DATE`.
- Date range is inclusive; input format is `yyyy-mm-dd`.
- Treat naive source timestamps as local WITA without timezone conversion.
- Download only exact `YYYYMMDD.csv` basenames in the requested range.
- Detect only canonical `WBxx-INVxx-PVn` strings with PV number `1..28` and at least one valid sample in the range.
- Calculate `sum(power_kw_valid * 5/60)` without fill, interpolation, estimation, or extrapolation.
- Prefer `PVn Power(kW)`; use voltage times current only when the direct power column is absent.
- Keep negative power and report it; average duplicate string/timestamp rows.
- Do not load POA, produce graphs, or export five-minute data.
- Workbook name is `output_string/all_string_yield_<yyyymmdd>_<yyyymmdd>.xlsx`.
- Workbook sheet order is `Rekap_Yield_kWh`, `Detail_Harian`, `Metadata`.
- Fail before materializing detail when the date-string product exceeds Excel's 1,048,576-row limit, and report the maximum supported days for the detected string count.
- Do not alter the behavior or workbook contract of `String_Yield_Power_Irradiance.ipynb`.

---

## File map

- Create `pv_pipeline/all_string_yield_report.py`: CSV-only orchestration, extraction, aggregation, workbook writing, and verification.
- Modify `pv_pipeline/string_yield_report.py`: add an opt-out flag for POA selection while preserving the current default.
- Create `tests/unit/test_all_string_yield_report.py`: input, aggregation, error, workbook, and builder contracts.
- Create `output_string/_build_all_string_yield_notebook.py`: deterministic notebook builder.
- Create `output_string/All_String_Daily_Yield.ipynb`: generated notebook artifact.
- Create `output_string/_smoke_all_string_yield_notebook.py`: offline end-to-end cell execution.

### Task 1: CSV-only selective input contract

**Files:**
- Modify: `pv_pipeline/string_yield_report.py:172-211`
- Create: `pv_pipeline/all_string_yield_report.py`
- Create: `tests/unit/test_all_string_yield_report.py`

**Interfaces:**
- Consumes: `DriveItem`, `SourceManifest`, `DownloadedInputs`, `parse_date_range()`, `validate_drive_folder_url()`, `inventory_drive_folder()`, `select_source_manifest()`, and `download_manifest()` from `pv_pipeline.string_yield_report`.
- Produces: `download_csv_inputs(url_csv: str, dates: pd.DatetimeIndex, destination: Path) -> tuple[SourceManifest, DownloadedInputs]`.

- [ ] **Step 1: Write the failing selective-input test**

Add this test and its imports to `tests/unit/test_all_string_yield_report.py`:

```python
from datetime import date
from pathlib import Path

import pandas as pd

import pv_pipeline.all_string_yield_report as report_module
from pv_pipeline.all_string_yield_report import download_csv_inputs
from pv_pipeline.string_yield_report import DownloadedInputs, DriveItem


CSV_URL = (
    "https://drive.google.com/drive/folders/"
    "1f_KrPuqfZJTE5I9cVQiyp65QrHbkF3Iw?usp=sharing"
)


def test_download_csv_inputs_inventories_one_folder_and_selects_dates(
    monkeypatch,
    tmp_path,
):
    calls = []
    items = [
        DriveItem("https://drive.google.com/uc?id=a", "nested/20260501.csv"),
        DriveItem("https://drive.google.com/uc?id=b", "20260502.csv"),
        DriveItem("https://drive.google.com/uc?id=c", "20260503.csv"),
    ]

    def fake_inventory(url):
        calls.append(("inventory", url))
        return items

    def fake_download(manifest, destination):
        calls.append(("download", manifest, Path(destination)))
        return DownloadedInputs({}, {}, {})

    monkeypatch.setattr(report_module, "inventory_drive_folder", fake_inventory)
    monkeypatch.setattr(report_module, "download_manifest", fake_download)
    dates = pd.date_range("2026-05-01", "2026-05-02", freq="D")

    manifest, inputs = download_csv_inputs(CSV_URL, dates, tmp_path)

    assert list(manifest.csv_by_date) == [date(2026, 5, 1), date(2026, 5, 2)]
    assert manifest.poa_by_year == {}
    assert manifest.missing_poa_years == []
    assert manifest.url_poa == ""
    assert manifest.csv_inventory_count == 3
    assert inputs == DownloadedInputs({}, {}, {})
    assert [entry[0] for entry in calls] == ["inventory", "download"]
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
python -m pytest tests/unit/test_all_string_yield_report.py::test_download_csv_inputs_inventories_one_folder_and_selects_dates -q
```

Expected: FAIL because `pv_pipeline.all_string_yield_report` does not exist.

- [ ] **Step 3: Add the minimal CSV-only implementation**

Change the existing selector signature without changing its default behavior:

```python
def select_source_manifest(
    csv_items,
    poa_items,
    dates: pd.DatetimeIndex,
    *,
    url_csv="",
    url_poa="",
    include_poa=True,
) -> SourceManifest:
```

Inside it, replace POA year/name selection with:

```python
years = sorted({d.year for d in requested_dates}) if include_poa else []
poa_names = {f"POA PLTS IKN {year}.xlsx" for year in years}
```

Create `pv_pipeline/all_string_yield_report.py` with these initial contents:

```python
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pv_pipeline.string_yield_report import (
    DownloadedInputs,
    SourceManifest,
    download_manifest,
    inventory_drive_folder,
    select_source_manifest,
    validate_drive_folder_url,
)


def download_csv_inputs(
    url_csv: str,
    dates: pd.DatetimeIndex,
    destination: Path,
) -> tuple[SourceManifest, DownloadedInputs]:
    url_csv = validate_drive_folder_url(url_csv)
    csv_items = inventory_drive_folder(url_csv)
    manifest = select_source_manifest(
        csv_items,
        [],
        dates,
        url_csv=url_csv,
        include_poa=False,
    )
    return manifest, download_manifest(manifest, Path(destination))
```

- [ ] **Step 4: Run focused and regression tests**

Run:

```powershell
python -m pytest tests/unit/test_all_string_yield_report.py::test_download_csv_inputs_inventories_one_folder_and_selects_dates tests/unit/test_string_yield_report.py::test_select_inventory_matches_only_requested_csv_dates_and_poa_years -q
```

Expected: `2 passed` with no skips.

- [ ] **Step 5: Commit Task 1**

```powershell
git add pv_pipeline/string_yield_report.py pv_pipeline/all_string_yield_report.py tests/unit/test_all_string_yield_report.py
git commit -m "feat: add selective all-string CSV inputs"
```

### Task 2: All-string extraction and daily aggregation

**Files:**
- Modify: `pv_pipeline/all_string_yield_report.py`
- Modify: `tests/unit/test_all_string_yield_report.py`

**Interfaces:**
- Consumes: `csv_by_date: dict[date, Path]`, requested `dates`, optional `SourceManifest`, and download errors from Task 1.
- Produces: `AllStringYieldData(summary: pd.DataFrame, daily: pd.DataFrame, metadata: dict[str, object])` and `build_all_string_daily_yield(csv_by_date, dates, *, source_manifest: SourceManifest | None = None, download_errors: dict[str, str] | None = None) -> AllStringYieldData`.

- [ ] **Step 1: Write failing aggregation tests**

Add a helper and tests that encode the business rules:

```python
import numpy as np
import pytest

from pv_pipeline.all_string_yield_report import build_all_string_daily_yield


def _write_csv(path, frame):
    frame.to_csv(path, index=False)
    return path


def test_daily_yield_builds_union_once_and_leaves_missing_values_blank(tmp_path):
    stamps = pd.date_range("2026-05-01 00:00", periods=12, freq="5min")
    day_one = pd.concat([
        pd.DataFrame({
            "Start Time": stamps,
            "Inverter_ID": "WB02-INV10",
            "PV1 Power(kW)": 20.0,
            "PV2 Voltage(V)": np.nan,
            "PV2 Current(A)": np.nan,
        }),
        pd.DataFrame({
            "Start Time": stamps,
            "Inverter_ID": "WB02-INV02",
            "PV1 Power(kW)": 10.0,
            "PV2 Voltage(V)": 500.0,
            "PV2 Current(A)": 10.0,
        }),
    ], ignore_index=True)
    day_two = day_one.loc[day_one["Inverter_ID"] == "WB02-INV02"].copy()
    day_two["Start Time"] = pd.date_range(
        "2026-05-02 00:00", periods=12, freq="5min"
    )
    paths = {
        date(2026, 5, 1): _write_csv(tmp_path / "20260501.csv", day_one),
        date(2026, 5, 2): _write_csv(tmp_path / "20260502.csv", day_two),
    }
    dates = pd.date_range("2026-05-01", "2026-05-03", freq="D")

    result = build_all_string_daily_yield(paths, dates)

    expected_strings = [
        "WB02-INV02-PV1",
        "WB02-INV02-PV2",
        "WB02-INV10-PV1",
    ]
    assert list(result.summary.columns) == ["date", *expected_strings]
    assert len(result.daily) == 9
    first = result.daily.set_index(["date", "pv_string"])
    assert first.loc[(date(2026, 5, 1), "WB02-INV02-PV1"), "string_yield_kwh"] == pytest.approx(10.0)
    assert first.loc[(date(2026, 5, 1), "WB02-INV02-PV2"), "string_yield_kwh"] == pytest.approx(5.0)
    assert first.loc[(date(2026, 5, 2), "WB02-INV10-PV1"), "status"] == "NO_STRING_DATA"
    assert first.loc[(date(2026, 5, 3), "WB02-INV02-PV1"), "status"] == "MISSING_CSV"
    assert pd.isna(result.summary.loc[2, "WB02-INV02-PV1"])


def test_direct_power_column_does_not_fall_back_when_values_are_empty(tmp_path):
    day_one = pd.DataFrame({
        "Start Time": ["2026-05-01 00:00"],
        "Inverter_ID": ["WB01-INV01"],
        "PV1 Power(kW)": [4.0],
        "PV1 Voltage(V)": [500.0],
        "PV1 Current(A)": [10.0],
    })
    day_two = day_one.copy()
    day_two["Start Time"] = "2026-05-02 00:00"
    day_two["PV1 Power(kW)"] = np.nan
    paths = {
        date(2026, 5, 1): _write_csv(tmp_path / "20260501.csv", day_one),
        date(2026, 5, 2): _write_csv(tmp_path / "20260502.csv", day_two),
    }

    result = build_all_string_daily_yield(
        paths,
        pd.date_range("2026-05-01", "2026-05-02", freq="D"),
    )

    second = result.daily.loc[result.daily["date"] == date(2026, 5, 2)].iloc[0]
    assert second["status"] == "NO_STRING_DATA"
    assert pd.isna(second["string_yield_kwh"])


def test_read_error_is_distinct_and_no_usable_range_fails_loudly(tmp_path):
    valid = _write_csv(tmp_path / "20260501.csv", pd.DataFrame({
        "Start Time": ["2026-05-01 00:00"],
        "Inverter_ID": ["WB01-INV01"],
        "PV1 Power(kW)": [6.0],
    }))
    broken = _write_csv(tmp_path / "20260502.csv", pd.DataFrame({"bad": [1]}))
    dates = pd.date_range("2026-05-01", "2026-05-02", freq="D")

    result = build_all_string_daily_yield(
        {date(2026, 5, 1): valid, date(2026, 5, 2): broken},
        dates,
    )
    assert result.daily.loc[result.daily["date"] == date(2026, 5, 2), "status"].tolist() == ["CSV_READ_ERROR"]
    assert "20260502.csv" in result.metadata["csv_read_errors"]

    with pytest.raises(RuntimeError, match="No requested CSV could be read"):
        build_all_string_daily_yield({date(2026, 5, 2): broken}, dates[1:])
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/unit/test_all_string_yield_report.py -q
```

Expected: the Task 1 test passes and new tests fail because `build_all_string_daily_yield` is missing.

- [ ] **Step 3: Implement the minimal aggregation contract**

Add these public constants and type:

```python
from dataclasses import dataclass
from datetime import date
import re

import numpy as np

from pv_pipeline.physics import compute_active_power_integration_kwh
from pv_pipeline.transformations import add_inverter_id


DETAIL_COLUMNS = [
    "date", "pv_string", "inverter_id", "pv_label",
    "string_yield_kwh", "valid_power_samples", "expected_samples",
    "coverage_pct", "missing_power_samples", "source_csv", "status",
]
SHEET_ORDER = ["Rekap_Yield_kWh", "Detail_Harian", "Metadata"]
INVERTER_RE = re.compile(r"^WB(\d{2})-INV(\d{2})$", re.I)
POWER_RE = re.compile(r"^PV0*(\d{1,2}) Power\(kW\)$", re.I)
VOLTAGE_RE = re.compile(r"^PV0*(\d{1,2}) Voltage\(V\)$", re.I)
CURRENT_RE = re.compile(r"^PV0*(\d{1,2}) Current\(A\)$", re.I)


@dataclass
class AllStringYieldData:
    summary: pd.DataFrame
    daily: pd.DataFrame
    metadata: dict[str, object]
```

Implement private helpers with these exact contracts:

```python
def _find_column(frame: pd.DataFrame, expected: str) -> str | None:
    return next(
        (str(column) for column in frame.columns if str(column).casefold() == expected.casefold()),
        None,
    )


def _natural_string_key(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"WB(\d{2})-INV(\d{2})-PV(\d{1,2})", value)
    if not match:
        raise ValueError(f"Invalid canonical PV string: {value}")
    return tuple(int(part) for part in match.groups())
```

Implement `_extract_all_string_power(frame, requested_day)` so it:

- resolves `Start Time` and either `Inverter_ID` or normalized `ManageObject`;
- rejects a structurally invalid CSV with `KeyError`;
- drops invalid timestamps, wrong dates, invalid inverter identifiers, off-grid seconds/microseconds, and minutes not divisible by five;
- discovers PV `1..28`, preferring direct power columns file-wide and using paired voltage/current only when direct is absent;
- returns tidy columns `timestamp`, `inverter_id`, `pv_string`, `pv_label`, `power_kw`, plus diagnostics;
- drops nonnumeric power, retains negatives, and groups duplicate `(timestamp, pv_string)` rows with mean.

Implement `build_all_string_daily_yield(csv_by_date, dates, *, source_manifest=None, download_errors=None)` so it:

- raises `ValueError` for an empty date range;
- reads each available path exactly once;
- records `csv_read_errors`, wrong-date rows, duplicate rows, negative samples, and source types;
- raises `RuntimeError("No requested CSV could be read and validated.")` if no file validates;
- raises `RuntimeError("No valid PV string power sample found in requested range.")` if the union is empty;
- natural-sorts the detected union;
- emits every requested date x detected string using 288 expected slots;
- assigns `MISSING_CSV`, `CSV_READ_ERROR`, `NO_STRING_DATA`, `PARTIAL`, or `COMPLETE` exactly as specified;
- calls `compute_active_power_integration_kwh(series, freq_hours=5 / 60)` only when valid samples exist;
- creates `summary` with `daily.pivot(index="date", columns="pv_string", values="string_yield_kwh")`, reindexes dates/strings, and resets the index;
- returns metadata with start/end, WITA generation time, canonical source URL, requested/loaded/missing files, errors, diagnostics, formula, interval, requested-day count, detected-string count, and warnings.

- [ ] **Step 4: Add coverage for exact completeness, duplicates, wrong dates, negative values, and `ManageObject`**

Add parameterized or focused tests that assert:

```python
assert complete_row["valid_power_samples"] == 288
assert complete_row["status"] == "COMPLETE"
assert duplicate_result.metadata["duplicate_rows"] == 1
assert wrong_date_result.metadata["wrong_date_rows"]["20260501.csv"] == 1
assert negative_result.metadata["negative_power_samples"] == 1
assert manage_object_result.daily["pv_string"].tolist() == ["WB01-INV01-PV1"]
assert off_grid_result.daily.iloc[0]["valid_power_samples"] == 1
```

For `off_grid_result`, use one timestamp exactly at `00:00:00` and one at
`00:05:30`; only the aligned sample may count. Use real temporary CSV files;
do not mock pandas or the integration helper.

- [ ] **Step 5: Run Task 2 tests**

Run:

```powershell
python -m pytest tests/unit/test_all_string_yield_report.py -q
```

Expected: all Task 1-2 tests pass with no skips or warnings caused by production code.

- [ ] **Step 6: Commit Task 2**

```powershell
git add pv_pipeline/all_string_yield_report.py tests/unit/test_all_string_yield_report.py
git commit -m "feat: calculate daily yield for all strings"
```

### Task 3: Three-sheet workbook and verification

**Files:**
- Modify: `pv_pipeline/all_string_yield_report.py`
- Modify: `tests/unit/test_all_string_yield_report.py`

**Interfaces:**
- Consumes: `AllStringYieldData` from Task 2.
- Produces: `build_all_string_output_path()`, `write_all_string_workbook()`, and `verify_all_string_workbook()`.

- [ ] **Step 1: Write the failing workbook contract test**

Create a fixture by calling the real Task 2 builder, then assert:

```python
from openpyxl import load_workbook
import pytest

from pv_pipeline.all_string_yield_report import (
    build_all_string_output_path,
    build_all_string_daily_yield,
    verify_all_string_workbook,
    write_all_string_workbook,
)


@pytest.fixture
def all_string_result(tmp_path):
    csv_path = tmp_path / "20260501.csv"
    pd.DataFrame({
        "Start Time": ["2026-05-01 00:00"],
        "Inverter_ID": ["WB01-INV01"],
        "PV1 Power(kW)": [0.0],
    }).to_csv(csv_path, index=False)
    return build_all_string_daily_yield(
        {date(2026, 5, 1): csv_path},
        pd.date_range("2026-05-01", "2026-05-03", freq="D"),
    )


def test_all_string_workbook_has_wide_detail_and_metadata_contract(
    tmp_path,
    all_string_result,
):
    output = build_all_string_output_path(
        tmp_path,
        date(2026, 5, 1),
        date(2026, 5, 3),
    )
    assert output.name == "all_string_yield_20260501_20260503.xlsx"

    write_all_string_workbook(output, all_string_result)
    verify_all_string_workbook(output)

    workbook = load_workbook(output, data_only=False)
    assert workbook.sheetnames == ["Rekap_Yield_kWh", "Detail_Harian", "Metadata"]
    recap = workbook["Rekap_Yield_kWh"]
    assert [cell.value for cell in recap[1]] == list(all_string_result.summary.columns)
    assert recap.freeze_panes == "B2"
    assert recap["B2"].number_format == "0.000"
    detail = workbook["Detail_Harian"]
    assert [cell.value for cell in detail[1]] == DETAIL_COLUMNS
    assert detail.max_row == len(all_string_result.daily) + 1
    assert workbook["Metadata"]["A1"].value == "key"
    workbook.close()
```

The fixture already provides a real observed zero followed by missing dates. In
the same test assert `recap["B2"].value == 0` and `recap["B3"].value is None`.
Add a parameterized rejection test for metadata keys containing `token`,
`cookie`, `secret`, `credential`, or `password`; for each key assert
`ValueError` and assert the target workbook does not exist.

- [ ] **Step 2: Run the workbook tests and verify RED**

Run:

```powershell
python -m pytest tests/unit/test_all_string_yield_report.py -k "workbook or output_path" -q
```

Expected: FAIL because the workbook functions do not exist.

- [ ] **Step 3: Implement workbook output**

Add openpyxl imports and the output-path implementation:

```python
def build_all_string_output_path(output_dir, start, end) -> Path:
    return Path(output_dir) / f"all_string_yield_{start:%Y%m%d}_{end:%Y%m%d}.xlsx"
```

Add `write_all_string_workbook(output_path: Path, report:
AllStringYieldData) -> Path` and `verify_all_string_workbook(path: Path) ->
None` with concrete code that:

- validates exact `DETAIL_COLUMNS`, `date` plus natural-sorted summary columns, and sensitive metadata before creating a file;
- creates only the three sheets in `SHEET_ORDER`;
- writes native dates/numbers and converts pandas missing values to `None`;
- uses `B2` freeze panes on recap, `A2` on detail/metadata, and filters all three headers;
- formats recap/detail dates as `yyyy-mm-dd`, yield as `0.000`, and coverage as `0.0"%"`;
- sets practical widths without inspecting cell contents dynamically;
- canonicalizes `source_url_csv` before writing metadata and JSON-serializes dict/list/tuple values;
- saves, calls `verify_all_string_workbook`, and returns the path;
- reopens the workbook in verification, checks exact sheet/header order, checks recap dimensions against `requested_days` and `detected_string_count` metadata, checks detail row count equals their product, and always closes the workbook.

- [ ] **Step 4: Run Task 3 tests**

Run:

```powershell
python -m pytest tests/unit/test_all_string_yield_report.py -q
```

Expected: all all-string tests pass with no skips.

- [ ] **Step 5: Commit Task 3**

```powershell
git add pv_pipeline/all_string_yield_report.py tests/unit/test_all_string_yield_report.py
git commit -m "feat: export all-string yield workbook"
```

### Task 4: Generated Colab notebook and offline smoke test

**Files:**
- Create: `output_string/_build_all_string_yield_notebook.py`
- Create: `output_string/All_String_Daily_Yield.ipynb`
- Create: `output_string/_smoke_all_string_yield_notebook.py`
- Modify: `tests/unit/test_all_string_yield_report.py`

**Interfaces:**
- Consumes: all public functions from Tasks 1-3.
- Produces: a seven-cell nbformat 4.5 notebook and repeatable offline smoke runner.

- [ ] **Step 1: Write failing builder artifact tests**

Add tests that import the builder by file path, write to a temporary target, parse JSON, and assert:

```python
assert notebook["nbformat"] == 4
assert notebook["nbformat_minor"] == 5
assert len(notebook["cells"]) == 7
assert [cell["cell_type"] for cell in notebook["cells"]] == [
    "markdown", "code", "code", "code", "code", "code", "code",
]
config = "".join(notebook["cells"][2]["source"])
assert config.count("URL_CSV =") == 1
assert CSV_URL in config
assert config.count("START_DATE =") == 1
assert config.count("END_DATE =") == 1
assert "PV_STRING" not in config
all_source = "\n".join("".join(cell["source"]) for cell in notebook["cells"])
assert "URL_RAW_DATA_INPUT" not in all_source
assert "POA" not in all_source
assert "download_csv_inputs" in all_source
assert "build_all_string_daily_yield" in all_source
assert "write_all_string_workbook" in all_source
assert "files.download" in all_source
```

- [ ] **Step 2: Run builder test and verify RED**

Run:

```powershell
python -m pytest tests/unit/test_all_string_yield_report.py -k builder -q
```

Expected: FAIL because `_build_all_string_yield_notebook.py` is missing.

- [ ] **Step 3: Implement the deterministic builder**

Create a builder using `json`, `Path`, `_cell(kind, source, index)`, and `build(out=OUT)`. Use these exact notebook responsibilities:

```python
OUT = Path(__file__).parent / "All_String_Daily_Yield.ipynb"

CELLS = [
    ("markdown", MD_INTRO),
    ("code", CODE_SETUP),
    ("code", CODE_CONFIG),
    ("code", CODE_DOWNLOAD),
    ("code", CODE_PROCESS),
    ("code", CODE_EXPORT),
    ("code", CODE_DOWNLOAD_XLSX),
]
```

The cell sources must implement these exact calls and guards:

```python
# Config
DATES = parse_date_range(START_DATE, END_DATE)
validate_drive_folder_url(URL_CSV)

# Download
MANIFEST, INPUTS = download_csv_inputs(URL_CSV, DATES, INPUT_DIR)

# Process
REPORT = build_all_string_daily_yield(
    INPUTS.csv_by_date,
    DATES,
    source_manifest=MANIFEST,
    download_errors=INPUTS.download_errors,
)

# Export
OUTPUT_XLSX = build_all_string_output_path(
    OUTPUT_DIR,
    DATES[0].date(),
    DATES[-1].date(),
)
write_all_string_workbook(OUTPUT_XLSX, REPORT)
verify_all_string_workbook(OUTPUT_XLSX)

# Colab download
files.download(str(OUTPUT_XLSX))
```

Setup must install `gdown>=6.0.0`, find a repo containing `pv_pipeline` and `config`, clone `https://github.com/nabilhaidr/PVStringHeatmapCheck.git` when absent, add the repo to `sys.path`, create `output_string`, and use a temporary input directory. Each downstream cell must raise a clear `RuntimeError` when its prerequisite global is absent.

Run:

```powershell
python output_string/_build_all_string_yield_notebook.py
python -m pytest tests/unit/test_all_string_yield_report.py -k builder -q
```

Expected: notebook is regenerated and builder tests pass.

- [ ] **Step 4: Write and run the offline smoke test**

Create `output_string/_smoke_all_string_yield_notebook.py` that:

- loads the committed notebook JSON;
- creates two local `YYYYMMDD.csv` files with two strings and known observed yields;
- patches `pv_pipeline.all_string_yield_report.download_csv_inputs` to return the local `SourceManifest` and `DownloadedInputs`;
- executes config through export cells in one scope while overriding dates to the synthetic period;
- opens the workbook and asserts exact sheet order, expected recap values, detail row count, and metadata counts;
- reruns download through export in the same scope and asserts one deterministic workbook exists with unchanged sheet/row counts;
- closes workbooks and prints `[smoke] OK`.

Run:

```powershell
python output_string/_smoke_all_string_yield_notebook.py
```

Expected: `[smoke] OK` and no traceback.

- [ ] **Step 5: Run complete verification**

Run:

```powershell
python -m pytest tests/unit/test_all_string_yield_report.py -q
python output_string/_smoke_all_string_yield_notebook.py
python -m pytest
git diff --check
```

Expected: focused tests pass, smoke prints `[smoke] OK`, the full suite passes with no failures, and `git diff --check` is silent. Report any pre-existing skipped tests explicitly instead of saying all tests ran.

- [ ] **Step 6: Commit Task 4**

```powershell
git add output_string/_build_all_string_yield_notebook.py output_string/All_String_Daily_Yield.ipynb output_string/_smoke_all_string_yield_notebook.py tests/unit/test_all_string_yield_report.py
git commit -m "feat: add all-string daily yield notebook"
```

## Final review checkpoint

After all four task commits:

1. Compare the complete diff against `docs/superpowers/specs/2026-07-14-all-string-daily-yield-notebook-design.md`.
2. Confirm no POA URL, POA download, graph, or five-minute sheet entered the new notebook/workbook.
3. Confirm the existing single-string notebook artifact and its tests remain unchanged except for the backward-compatible selector signature.
4. Run the focused tests, smoke runner, full pytest suite, and `git diff --check` again from the final branch head.
5. Review `git status --short` and name every remaining unrelated local change; do not stage it.
6. Run a one-day live public-Drive verification after installing the notebook's declared `gdown>=6.0.0` dependency; record inventory, selected/downloaded files, detected strings, statuses, and workbook reopen evidence.
