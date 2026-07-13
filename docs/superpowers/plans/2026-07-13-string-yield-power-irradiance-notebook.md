# String Yield, Power, and Irradiance Notebook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Membuat notebook Colab di `output_string/` yang mengunduh input Google Drive publik secara selektif, menghitung yield harian satu PV string tanpa mengisi power hilang, menampilkan dua grafik, dan menghasilkan workbook Excel empat-sheet yang dapat diunduh.

**Architecture:** Logika deterministik ditempatkan di `pv_pipeline/string_yield_report.py`; notebook hanya menjadi orkestrator tipis. Adapter jaringan memakai JSON inventory resmi `gdown>=6`, pengolahan CSV/POA menghasilkan satu `ReportData`, dan objek yang sama dipakai oleh grafik notebook serta writer Excel. Notebook dihasilkan sebagai JSON nbformat 4.5 oleh builder, lalu diuji offline memakai CSV/POA sintetis dan adapter download yang di-patch.

**Tech Stack:** Python 3.10+, pandas, matplotlib, openpyxl, PyYAML, `pv_pipeline.poa.PyranometerLoader`, `pv_pipeline.physics.compute_active_power_integration_kwh`, gdown 6.x di Colab, pytest.

## Global Constraints

- Ikuti spesifikasi yang disetujui di `docs/superpowers/specs/2026-07-13-string-yield-power-irradiance-notebook-design.md`.
- Jangan mount Google Drive, jangan mengunduh seluruh folder, dan jangan mengubah data sumber.
- Import `gdown` harus lazy di adapter download. Environment lokal saat ini tidak memiliki `gdown`; unit test dan builder wajib tetap dapat diimpor tanpa paket itu.
- JSON inventory gdown berisi `url` dan `path`. Nama file selalu diambil dari `Path(item.path).name`; target basename ganda harus gagal keras.
- Target CSV harus bernama tepat `YYYYMMDD.csv`; target POA harus bernama tepat `POA PLTS IKN YYYY.xlsx` untuk setiap tahun dalam rentang.
- `PyranometerLoader.from_geometry_yaml()` tidak dipakai karena path POA berasal dari hasil download. Baca `ws_to_wb` dan nama sheet dari YAML, lalu instansiasi loader dengan path hasil download.
- Untuk `poa_source` per slot, bandingkan `get_per_ws(year_grid, selection.wb_id, fallback_to_avg=False)` dengan `get_per_ws(year_grid, selection.wb_id, fallback_to_avg=True)`. Mask fallback hanya `strict.isna() & final.notna()`, sehingga WB tanpa mapping tidak keliru menerima seluruh seri rata-rata.
- Power hilang tetap `NaN`. Tidak ada `fillna`, interpolasi, forward-fill, backward-fill, atau ekstrapolasi pada power.
- `compute_active_power_integration_kwh()` menghasilkan `0.0` untuk seri all-NaN; caller wajib memeriksa jumlah sampel valid dan menyimpan `NaN` untuk `NO_STRING_DATA`/`MISSING_CSV`.
- Konflik format coverage diselesaikan dengan mempertahankan arti spesifikasi: `coverage_pct` disimpan 0..100. Excel memakai number format `0.0\"%\"` agar nilai 50 ditampilkan `50.0%`, bukan `5000%`.
- `Data_5Menit.data_status` memakai empat nilai eksplisit: `POWER_POA`, `POWER_ONLY`, `POA_ONLY`, `MISSING_BOTH`.
- Builder mengikuti konvensi repo: JSON mentah, `indent=1`, `ensure_ascii=False`, nbformat 4/minor 5; edit builder lalu generate `.ipynb`, jangan edit notebook langsung.
- Hanya tambahkan `output_string/string_yield_*.xlsx` ke `.gitignore`; jangan ignore seluruh `output_string/`.
- Commit per task hanya file task tersebut. Jangan stage atau mengubah file user lain yang sudah dirty.

## Public Interfaces

```python
@dataclass(frozen=True)
class StringSelection:
    canonical: str
    wb_id: str
    inverter_id: str
    pv_number: int
    pv_label: str

@dataclass(frozen=True)
class DriveItem:
    url: str
    path: str

@dataclass
class SourceManifest:
    csv_by_date: dict[date, DriveItem]
    poa_by_year: dict[int, DriveItem]
    missing_csv_dates: list[date]
    missing_poa_years: list[int]
    url_csv: str = ""
    url_poa: str = ""
    csv_inventory_count: int = 0
    poa_inventory_count: int = 0

@dataclass
class DownloadedInputs:
    csv_by_date: dict[date, Path]
    poa_by_year: dict[int, Path]
    download_errors: dict[str, str]

@dataclass
class ReportData:
    daily: pd.DataFrame
    five_minute: pd.DataFrame
    metadata: dict[str, object]
```

Public functions:

```text
parse_string_selection(value: str) -> StringSelection
parse_date_range(start: str, end: str) -> pd.DatetimeIndex
validate_drive_folder_url(url: str) -> str
parse_inventory_json(payload: str) -> list[DriveItem]
inventory_drive_folder(folder_url: str) -> list[DriveItem]
select_source_manifest(csv_items, poa_items, dates, *, url_csv="", url_poa="") -> SourceManifest
download_manifest(manifest, destination) -> DownloadedInputs
download_report_inputs(url_csv, url_poa, dates, destination) -> tuple[SourceManifest, DownloadedInputs]
build_report_data(csv_by_date, poa_by_year, selection, dates, geometry_path, *, source_manifest=None, download_errors=None) -> ReportData
plot_daily_yield(daily, selection) -> tuple[Figure, Axes]
plot_power_vs_poa(five_minute, selection, start, end) -> tuple[Figure, tuple[Axes, Axes]]
build_output_path(output_dir, selection, start, end) -> Path
write_report_workbook(output_path, report) -> Path
verify_report_workbook(path) -> None
```

---

### Task 1: Configuration and selective Drive acquisition

**Files:**
- Create: `pv_pipeline/string_yield_report.py`
- Create: `tests/unit/test_string_yield_report.py`

**Interfaces:** `StringSelection`, `DriveItem`, `SourceManifest`, `DownloadedInputs`, and the parsing/inventory/download functions listed above.

- [ ] **Step 1: Write failing contract tests**

Add tests that encode exact normalization, inclusive dates, Drive URL validation, gdown JSON keys, date/year selection, duplicate target rejection, and per-file download:

```python
from datetime import date, datetime
from pathlib import Path

import pytest

from pv_pipeline.string_yield_report import (
    DriveItem,
    SourceManifest,
    download_manifest,
    parse_date_range,
    parse_inventory_json,
    parse_string_selection,
    select_source_manifest,
    validate_drive_folder_url,
)


def test_parse_string_selection_normalizes_case_and_pv_number():
    got = parse_string_selection("wb05-inv01-pv03")
    assert (got.canonical, got.wb_id, got.inverter_id, got.pv_number, got.pv_label) == (
        "WB05-INV01-PV3", "WB05", "WB05-INV01", 3, "PV3"
    )


@pytest.mark.parametrize("value", ["WB5-INV01-PV03", "WB05-INV01-PV0", "WB05-INV01-PV29"])
def test_parse_string_selection_rejects_invalid_format_and_range(value):
    with pytest.raises(ValueError):
        parse_string_selection(value)


def test_parse_date_range_is_inclusive_and_rejects_reverse():
    dates = parse_date_range("2026-12-31", "2027-01-02")
    assert [d.date() for d in dates] == [date(2026, 12, 31), date(2027, 1, 1), date(2027, 1, 2)]
    with pytest.raises(ValueError, match="START_DATE"):
        parse_date_range("2027-01-02", "2026-12-31")


def test_drive_url_and_inventory_json_contract():
    url = "https://drive.google.com/drive/folders/folder-id?usp=sharing"
    assert validate_drive_folder_url(url) == url
    items = parse_inventory_json('[{"url":"u1","path":"root/20260501.csv"}]')
    assert items == [DriveItem(url="u1", path="root/20260501.csv")]
    with pytest.raises(ValueError):
        validate_drive_folder_url("https://example.com/folder-id")


def test_select_inventory_matches_only_requested_csv_dates_and_poa_years():
    dates = parse_date_range("2026-12-31", "2027-01-01")
    csv_items = [
        DriveItem("csv-old", "root/20261230.csv"),
        DriveItem("csv-a", "root/20261231.csv"),
    ]
    poa_items = [
        DriveItem("poa-26", "root/POA PLTS IKN 2026.xlsx"),
        DriveItem("poa-27", "root/POA PLTS IKN 2027.xlsx"),
        DriveItem("other", "root/other.xlsx"),
    ]
    got = select_source_manifest(csv_items, poa_items, dates)
    assert got.csv_by_date == {date(2026, 12, 31): csv_items[1]}
    assert got.missing_csv_dates == [date(2027, 1, 1)]
    assert got.poa_by_year == {2026: poa_items[0], 2027: poa_items[1]}
    assert got.missing_poa_years == []
```

Also test that two inventory paths with the same requested basename raise `ValueError`. Because `gdown` is intentionally absent locally, use this exact fake-module seam for `download_manifest()`:

```python
import sys
from types import SimpleNamespace


def test_download_manifest_downloads_only_selected_items(monkeypatch, tmp_path):
    calls = []

    def fake_download(*, url, output, quiet):
        calls.append((url, Path(output).name, quiet))
        Path(output).write_text("synthetic", encoding="utf-8")
        return output

    monkeypatch.setitem(sys.modules, "gdown", SimpleNamespace(download=fake_download))
    manifest = SourceManifest(
        csv_by_date={date(2026, 5, 1): DriveItem("csv-url", "root/20260501.csv")},
        poa_by_year={2026: DriveItem("poa-url", "root/POA PLTS IKN 2026.xlsx")},
        missing_csv_dates=[],
        missing_poa_years=[],
    )
    got = download_manifest(manifest, tmp_path)
    assert calls == [
        ("csv-url", "20260501.csv", False),
        ("poa-url", "POA PLTS IKN 2026.xlsx", False),
    ]
    assert got.download_errors == {}
    assert got.csv_by_date[date(2026, 5, 1)].is_file()
    assert got.poa_by_year[2026].is_file()
```

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```powershell
python -X utf8 -m pytest tests/unit/test_string_yield_report.py -q
```

Expected: collection fails with `ModuleNotFoundError: pv_pipeline.string_yield_report`.

- [ ] **Step 3: Implement the minimum acquisition layer**

Create `pv_pipeline/string_yield_report.py` with these exact rules:

```python
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

import pandas as pd

STRING_RE = re.compile(r"^(WB\d{2})-(INV\d{2})-PV(\d{1,2})$", re.I)


@dataclass(frozen=True)
class StringSelection:
    canonical: str
    wb_id: str
    inverter_id: str
    pv_number: int
    pv_label: str


@dataclass(frozen=True)
class DriveItem:
    url: str
    path: str


@dataclass
class SourceManifest:
    csv_by_date: dict[date, DriveItem]
    poa_by_year: dict[int, DriveItem]
    missing_csv_dates: list[date]
    missing_poa_years: list[int]
    url_csv: str = ""
    url_poa: str = ""
    csv_inventory_count: int = 0
    poa_inventory_count: int = 0


@dataclass
class DownloadedInputs:
    csv_by_date: dict[date, Path]
    poa_by_year: dict[int, Path]
    download_errors: dict[str, str]


def parse_string_selection(value: str) -> StringSelection:
    match = STRING_RE.fullmatch(str(value).strip())
    if not match:
        raise ValueError("PV_STRING must match WBxx-INVxx-PVxx.")
    wb_id, inv_part, pv_text = match.groups()
    pv_number = int(pv_text)
    if not 1 <= pv_number <= 28:
        raise ValueError("PV number must be in range 1..28.")
    wb_id, inv_part = wb_id.upper(), inv_part.upper()
    inverter_id = f"{wb_id}-{inv_part}"
    return StringSelection(
        canonical=f"{inverter_id}-PV{pv_number}",
        wb_id=wb_id,
        inverter_id=inverter_id,
        pv_number=pv_number,
        pv_label=f"PV{pv_number}",
    )


def parse_date_range(start: str, end: str) -> pd.DatetimeIndex:
    try:
        start_ts = pd.Timestamp(datetime.strptime(start, "%Y-%m-%d"))
        end_ts = pd.Timestamp(datetime.strptime(end, "%Y-%m-%d"))
    except (TypeError, ValueError) as exc:
        raise ValueError("START_DATE and END_DATE must use yyyy-mm-dd.") from exc
    if start_ts > end_ts:
        raise ValueError("START_DATE must be on or before END_DATE.")
    return pd.date_range(start_ts.normalize(), end_ts.normalize(), freq="D")


def validate_drive_folder_url(url: str) -> str:
    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https" or parsed.netloc != "drive.google.com" or "/drive/folders/" not in parsed.path:
        raise ValueError(f"Expected a public Google Drive folder URL, got {url!r}.")
    return str(url).strip()


def parse_inventory_json(payload: str) -> list[DriveItem]:
    raw = json.loads(payload)
    if not isinstance(raw, list):
        raise ValueError("gdown inventory must be a JSON array.")
    items = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("url") or not entry.get("path"):
            raise ValueError(f"Invalid gdown inventory entry: {entry!r}")
        items.append(DriveItem(url=str(entry["url"]), path=str(entry["path"])))
    return items


def inventory_drive_folder(folder_url: str) -> list[DriveItem]:
    folder_url = validate_drive_folder_url(folder_url)
    result = subprocess.run(
        [sys.executable, "-m", "gdown", folder_url, "--folder", "--json"],
        check=True, capture_output=True, text=True,
    )
    return parse_inventory_json(result.stdout)


def _index_exact_basename(items: Sequence[DriveItem], targets: set[str]) -> dict[str, DriveItem]:
    found: dict[str, DriveItem] = {}
    for item in items:
        name = Path(item.path).name
        if name not in targets:
            continue
        if name in found:
            raise ValueError(f"Duplicate requested basename in Drive inventory: {name}")
        found[name] = item
    return found


def select_source_manifest(csv_items, poa_items, dates: pd.DatetimeIndex, *, url_csv="", url_poa="") -> SourceManifest:
    requested_dates = [ts.date() for ts in dates]
    csv_names = {d.strftime("%Y%m%d") + ".csv" for d in requested_dates}
    years = sorted({d.year for d in requested_dates})
    poa_names = {f"POA PLTS IKN {year}.xlsx" for year in years}
    csv_index = _index_exact_basename(csv_items, csv_names)
    poa_index = _index_exact_basename(poa_items, poa_names)
    csv_by_date = {d: csv_index[d.strftime("%Y%m%d") + ".csv"] for d in requested_dates if d.strftime("%Y%m%d") + ".csv" in csv_index}
    poa_by_year = {year: poa_index[f"POA PLTS IKN {year}.xlsx"] for year in years if f"POA PLTS IKN {year}.xlsx" in poa_index}
    return SourceManifest(
        csv_by_date=csv_by_date,
        poa_by_year=poa_by_year,
        missing_csv_dates=[d for d in requested_dates if d not in csv_by_date],
        missing_poa_years=[year for year in years if year not in poa_by_year],
        url_csv=url_csv,
        url_poa=url_poa,
        csv_inventory_count=len(csv_items),
        poa_inventory_count=len(poa_items),
    )


def download_manifest(manifest: SourceManifest, destination: Path) -> DownloadedInputs:
    import gdown

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    csv_paths: dict[date, Path] = {}
    poa_paths: dict[int, Path] = {}
    errors: dict[str, str] = {}
    for key, item in manifest.csv_by_date.items():
        path = destination / Path(item.path).name
        try:
            gdown.download(url=item.url, output=str(path), quiet=False)
            if not path.is_file():
                raise RuntimeError(f"gdown did not create {path.name}")
        except Exception as exc:
            errors[path.name] = f"{type(exc).__name__}: {exc}"
        else:
            csv_paths[key] = path
    for key, item in manifest.poa_by_year.items():
        path = destination / Path(item.path).name
        try:
            gdown.download(url=item.url, output=str(path), quiet=False)
            if not path.is_file():
                raise RuntimeError(f"gdown did not create {path.name}")
        except Exception as exc:
            errors[path.name] = f"{type(exc).__name__}: {exc}"
        else:
            poa_paths[key] = path
    return DownloadedInputs(csv_by_date=csv_paths, poa_by_year=poa_paths, download_errors=errors)


def download_report_inputs(url_csv, url_poa, dates, destination):
    csv_items = inventory_drive_folder(url_csv)
    poa_items = inventory_drive_folder(url_poa)
    manifest = select_source_manifest(
        csv_items, poa_items, dates, url_csv=url_csv, url_poa=url_poa,
    )
    return manifest, download_manifest(manifest, Path(destination))
```

- [ ] **Step 4: Run targeted tests and confirm GREEN**

```powershell
python -X utf8 -m pytest tests/unit/test_string_yield_report.py -q
```

Expected: all Task 1 tests pass; no network access occurs.

- [ ] **Step 5: Commit Task 1**

```powershell
git add pv_pipeline/string_yield_report.py tests/unit/test_string_yield_report.py
git commit -m "feat: add selective string report inputs"
```

---

### Task 2: CSV/POA processing, complete grid, and daily yield

**Files:**
- Modify: `pv_pipeline/string_yield_report.py`
- Modify: `tests/unit/test_string_yield_report.py`

**Interfaces:** Add `ReportData` and `build_report_data(csv_by_date, poa_by_year, selection, dates, geometry_path, *, source_manifest=None, download_errors=None)`. Private helpers may be `_extract_string_power`, `_load_geometry`, `_load_poa`, `_build_daily_summary`; do not create a second module.

- [ ] **Step 1: Add failing data-contract tests**

Use small DataFrames and temporary CSV/XLSX files to encode:

```python
import numpy as np
import pandas as pd

from pv_pipeline.string_yield_report import (
    _extract_string_power,
    build_report_data,
)


def test_extract_power_prefers_explicit_power_column():
    df = pd.DataFrame({
        "Start Time": ["2026-05-01 00:00"],
        "Inverter_ID": ["wb05-inv01"],
        "PV3 Power(kW)": [7.5],
        "PV3 Input Voltage(V)": [1000],
        "PV3 Input Current(A)": [10],
    })
    series, diagnostics = _extract_string_power(df, parse_string_selection("WB05-INV01-PV03"))
    assert series.iloc[0] == pytest.approx(7.5)
    assert diagnostics["power_source"] == "direct"


def test_extract_power_falls_back_case_insensitively_and_deduplicates_mean():
    df = pd.DataFrame({
        "start time": ["2026-05-01 00:00", "2026-05-01 00:00", "2026-05-01 00:10"],
        "inverter_id": ["WB05-INV01"] * 3,
        "pv03 INPUT voltage(v)": [1000, 1000, 1000],
        "PV3 input CURRENT(a)": [8, 12, 10],
    })
    series, diagnostics = _extract_string_power(df, parse_string_selection("WB05-INV01-PV3"))
    assert list(series) == pytest.approx([10.0, 10.0])
    assert diagnostics["duplicate_rows"] == 1
    assert diagnostics["power_source"] == "voltage_current"


def test_daily_yield_uses_only_observed_slots_and_distinguishes_statuses(tmp_path):
    # Day 1: 12 observed samples x 10 kW = 10 kWh (PARTIAL).
    # Day 2: readable CSV but selected string absent (NO_STRING_DATA).
    # Day 3: no CSV path (MISSING_CSV).
    dates = parse_date_range("2026-05-01", "2026-05-03")
    csv1 = tmp_path / "20260501.csv"
    csv2 = tmp_path / "20260502.csv"
    pd.DataFrame({
        "Start Time": pd.date_range("2026-05-01", periods=12, freq="5min"),
        "Inverter_ID": "WB05-INV01",
        "PV3 Power(kW)": 10.0,
    }).to_csv(csv1, index=False)
    pd.DataFrame({
        "Start Time": ["2026-05-02 00:00"],
        "Inverter_ID": ["WB05-INV02"],
        "PV3 Power(kW)": [10.0],
    }).to_csv(csv2, index=False)
    report = build_report_data(
        {date(2026, 5, 1): csv1, date(2026, 5, 2): csv2}, {},
        parse_string_selection("WB05-INV01-PV03"), dates,
        Path("config/site_geometry.yaml"),
    )
    assert len(report.five_minute) == 3 * 288
    assert report.five_minute["power_kw"].notna().sum() == 12
    assert report.daily["status"].tolist() == ["PARTIAL", "NO_STRING_DATA", "MISSING_CSV"]
    assert report.daily.loc[0, "string_yield_kwh"] == pytest.approx(10.0)
    assert report.daily.loc[1:, "string_yield_kwh"].isna().all()
```

Add separate tests for: exact 288-sample `COMPLETE`; wrong-file-date rows are dropped and warned; negative values are retained and warned; a missing 5-minute slot stays `NaN`; `ManageObject` fallback; no readable CSV raises; no valid selected-string sample across all readable CSVs raises; POA WB05 maps to WS-2; WS-2 gap is filled from avg and counted/labeled `avg`.

- [ ] **Step 2: Run the new tests and confirm RED**

```powershell
python -X utf8 -m pytest tests/unit/test_string_yield_report.py -q
```

Expected: imports or assertions fail because report processing does not exist.

- [ ] **Step 3: Implement processing with one source of truth**

Add these mechanics to `string_yield_report.py`:

```python
@dataclass
class ReportData:
    daily: pd.DataFrame
    five_minute: pd.DataFrame
    metadata: dict[str, object]


def _find_column(df: pd.DataFrame, predicate) -> str | None:
    return next((str(col) for col in df.columns if predicate(str(col))), None)


def _normalize_manage_object_value(value):
    if pd.isna(value):
        return value
    parts = str(value).strip().split("/")
    leaf = re.sub(r"(?i)inv_a_", "Inv_A_", parts[-1])
    leaf = re.sub(r"(?i)inv_b_", "Inv_B_", leaf)
    if re.fullmatch(r"(?i)WB\d{2}-INV\d+", leaf):
        leaf = leaf.upper()
    parts[-1] = leaf
    return "/".join(parts)


def _extract_string_power(df: pd.DataFrame, selection: StringSelection):
    work = df.copy()
    ts_col = _find_column(work, lambda c: c.casefold() == "start time")
    inv_col = _find_column(work, lambda c: c.casefold() == "inverter_id")
    manage_col = _find_column(work, lambda c: c.casefold() == "manageobject")
    if ts_col is None:
        raise KeyError("CSV missing Start Time column.")
    if inv_col is None:
        if manage_col is None:
            raise KeyError("CSV missing Inverter_ID and ManageObject columns.")
        if manage_col != "ManageObject":
            work = work.rename(columns={manage_col: "ManageObject"})
        from pv_pipeline.transformations import add_inverter_id
        work["ManageObject"] = work["ManageObject"].map(_normalize_manage_object_value)
        work = add_inverter_id(work)
        inv_col = "Inverter_ID"
    mask = work[inv_col].astype("string").str.strip().str.upper() == selection.inverter_id
    work = work.loc[mask]
    direct_re = re.compile(rf"^PV0*{selection.pv_number}\s+Power\(kW\)$", re.I)
    direct_col = _find_column(work, lambda c: direct_re.fullmatch(c.strip()) is not None)
    if direct_col is not None:
        power = pd.to_numeric(work[direct_col], errors="coerce")
        source = "direct"
    else:
        voltage_re = re.compile(rf"^PV0*{selection.pv_number}(?!\d).*voltage", re.I)
        current_re = re.compile(rf"^PV0*{selection.pv_number}(?!\d).*current", re.I)
        voltage_col = _find_column(work, lambda c: voltage_re.search(c) is not None)
        current_col = _find_column(work, lambda c: current_re.search(c) is not None)
        if voltage_col is None or current_col is None:
            return pd.Series(dtype="float64", name="power_kw"), {
                "power_source": "missing", "duplicate_rows": 0, "negative_samples": 0,
            }
        power = pd.to_numeric(work[voltage_col], errors="coerce") * pd.to_numeric(work[current_col], errors="coerce") / 1000.0
        source = "voltage_current"
    indexed = pd.Series(power.to_numpy(), index=pd.to_datetime(work[ts_col], errors="coerce"), name="power_kw").dropna(axis="index")
    indexed = indexed[indexed.index.notna()]
    duplicate_rows = int(len(indexed) - indexed.index.nunique())
    indexed = indexed.groupby(level=0).mean().sort_index()
    return indexed, {
        "power_source": source,
        "duplicate_rows": duplicate_rows,
        "negative_samples": int((indexed < 0).sum()),
    }
```

Implement `build_report_data()` in this order:

1. Build `grid = pd.date_range(dates[0], dates[-1] + 1 day - 5 minutes, freq="5min")`.
2. For each requested date: read only its mapped CSV; on read failure record the exception and treat that date as `MISSING_CSV`; validate timestamps against the filename date; warn and drop mismatches; extract selected power.
3. Require at least one readable CSV. Concatenate all extracted series, average cross-file duplicate timestamps, reindex to `grid`, and require at least one valid selected-string power sample across the complete run.
4. Load YAML with `geometry = yaml.safe_load(Path(geometry_path).read_text(encoding="utf-8")) or {}` and obtain `sheet = str((geometry.get("pyranometer") or {}).get("sheet", "POA PLTS IKN"))`. For each `(year, path)` independently, instantiate `PyranometerLoader(str(path), sheet=sheet, ws_to_wb=geometry.get("ws_to_wb") or {})` inside a logged `try/except`; an unreadable POA year records `poa_read_errors[path.name]` and leaves only that year's POA blank instead of aborting yield processing.
5. Query each successful loader only for `year_grid = grid[grid.year == year]`. Obtain `strict = loader.get_per_ws(year_grid, selection.wb_id, fallback_to_avg=False)` and `final = loader.get_per_ws(year_grid, selection.wb_id, fallback_to_avg=True)` using the loader's default 2-minute tolerance. Set `fallback_mask = strict.isna() & final.notna()`, assign `poa = final`, and label `poa_source` with `strict.attrs["ws_label"]`, `avg` on fallback slots, or blank. Sum the mask into the exact metadata key `poa_fallback_samples`.
6. Construct `five_minute` with exactly `timestamp`, `inverter_id`, `pv_string`, `power_kw`, `poa_wm2`, `source_csv`, `poa_source`, `data_status`. Derive `data_status` vectorially from the two non-null masks.
7. Group by calendar date. `valid_power_samples == 288` => `COMPLETE`; 1..287 => `PARTIAL`; zero with readable CSV => `NO_STRING_DATA`; zero without readable CSV => `MISSING_CSV`. Only call `compute_active_power_integration_kwh(series, freq_hours=5/60)` when the valid count is positive.
8. Build `daily` in the exact approved column order. Set `coverage_pct = valid / 288 * 100` and `missing_power_samples = 288 - valid`.
9. Accept keyword-only `source_manifest` and `download_errors`; put configuration, `source_url_csv`, `source_url_poa`, manifest inventory counts/missing inputs, failed downloads, mapped WS, loaded/read-failed files, wrong-date rows, duplicate count, negative count, `poa_fallback_samples`, and warnings in `metadata`; never include auth material.

Use this concrete implementation for the orchestration; add `import numpy as np`, `import yaml`, `from pv_pipeline.physics import compute_active_power_integration_kwh`, and `from pv_pipeline.poa import PyranometerLoader` to the module imports:

```python
def build_report_data(
    csv_by_date,
    poa_by_year,
    selection,
    dates,
    geometry_path,
    *,
    source_manifest=None,
    download_errors=None,
):
    if len(dates) == 0:
        raise ValueError("dates must contain at least one requested day.")
    grid = pd.date_range(
        pd.Timestamp(dates[0]).normalize(),
        pd.Timestamp(dates[-1]).normalize() + pd.Timedelta(days=1) - pd.Timedelta(minutes=5),
        freq="5min",
    )
    source_by_day = {}
    power_parts = []
    csv_read_errors = {}
    wrong_date_rows = {}
    duplicate_rows = 0
    negative_samples = 0
    power_sources = set()

    for requested_ts in dates:
        requested_day = pd.Timestamp(requested_ts).date()
        path = csv_by_date.get(requested_day)
        if path is None:
            continue
        path = Path(path)
        try:
            frame = pd.read_csv(path)
            timestamp_column = _find_column(frame, lambda name: name.casefold() == "start time")
            if timestamp_column is None:
                raise KeyError("CSV missing Start Time column.")
            parsed = pd.to_datetime(frame[timestamp_column], errors="coerce")
            wrong_mask = parsed.notna() & (parsed.dt.date != requested_day)
            wrong_date_rows[path.name] = int(wrong_mask.sum())
            frame = frame.loc[~wrong_mask].copy()
            series, diagnostics = _extract_string_power(frame, selection)
        except Exception as exc:
            csv_read_errors[path.name] = f"{type(exc).__name__}: {exc}"
            continue
        source_by_day[requested_day] = path.name
        power_parts.append(series)
        duplicate_rows += int(diagnostics["duplicate_rows"])
        negative_samples += int(diagnostics["negative_samples"])
        power_sources.add(str(diagnostics["power_source"]))

    if not source_by_day:
        raise RuntimeError("No requested CSV could be read and validated.")
    non_empty = [series for series in power_parts if not series.empty]
    if not non_empty:
        raise RuntimeError(f"No valid power sample found for {selection.canonical}.")
    combined = pd.concat(non_empty).sort_index()
    duplicate_rows += int(len(combined) - combined.index.nunique())
    combined = combined.groupby(level=0).mean().sort_index()
    power = combined.reindex(grid)
    if not power.notna().any():
        raise RuntimeError(f"No valid power sample found for {selection.canonical} in requested range.")

    geometry = yaml.safe_load(Path(geometry_path).read_text(encoding="utf-8")) or {}
    ws_to_wb = geometry.get("ws_to_wb") or {}
    sheet = str((geometry.get("pyranometer") or {}).get("sheet", "POA PLTS IKN"))
    poa = pd.Series(index=grid, dtype="float64", name="poa_wm2")
    poa_source = pd.Series(index=grid, dtype="object", name="poa_source")
    poa_read_errors = {}
    poa_fallback_samples = 0
    mapped_ws = None

    for year, raw_path in sorted(poa_by_year.items()):
        year_grid = grid[grid.year == int(year)]
        if year_grid.empty:
            continue
        path = Path(raw_path)
        try:
            loader = PyranometerLoader(str(path), sheet=sheet, ws_to_wb=ws_to_wb)
            strict = loader.get_per_ws(
                year_grid, selection.wb_id, fallback_to_avg=False,
            )
            final = loader.get_per_ws(
                year_grid, selection.wb_id, fallback_to_avg=True,
            )
        except Exception as exc:
            poa_read_errors[path.name] = f"{type(exc).__name__}: {exc}"
            continue
        mapped_ws = strict.attrs.get("ws_label") or mapped_ws
        fallback_mask = strict.isna() & final.notna()
        poa.loc[year_grid] = final.to_numpy()
        poa_source.loc[year_grid[strict.notna().to_numpy()]] = strict.attrs.get("ws_label")
        poa_source.loc[year_grid[fallback_mask.to_numpy()]] = "avg"
        poa_fallback_samples += int(fallback_mask.sum())

    has_power = power.notna().to_numpy()
    has_poa = poa.notna().to_numpy()
    data_status = np.select(
        [has_power & has_poa, has_power & ~has_poa, ~has_power & has_poa],
        ["POWER_POA", "POWER_ONLY", "POA_ONLY"],
        default="MISSING_BOTH",
    )
    five_minute = pd.DataFrame({
        "timestamp": grid,
        "inverter_id": selection.inverter_id,
        "pv_string": selection.pv_label,
        "power_kw": power.to_numpy(),
        "poa_wm2": poa.to_numpy(),
        "source_csv": [source_by_day.get(timestamp.date()) for timestamp in grid],
        "poa_source": poa_source.to_numpy(),
        "data_status": data_status,
    })

    daily_rows = []
    for requested_ts in dates:
        requested_day = pd.Timestamp(requested_ts).date()
        day_mask = five_minute["timestamp"].dt.date == requested_day
        day_power = five_minute.loc[day_mask].set_index("timestamp")["power_kw"]
        valid = int(day_power.notna().sum())
        if requested_day not in source_by_day:
            status = "MISSING_CSV"
        elif valid == 0:
            status = "NO_STRING_DATA"
        elif valid == 288:
            status = "COMPLETE"
        else:
            status = "PARTIAL"
        yield_kwh = (
            compute_active_power_integration_kwh(day_power, freq_hours=5 / 60)
            if valid > 0
            else np.nan
        )
        daily_rows.append({
            "date": requested_day,
            "string_yield_kwh": yield_kwh,
            "valid_power_samples": valid,
            "expected_samples": 288,
            "coverage_pct": valid / 288 * 100,
            "missing_power_samples": 288 - valid,
            "poa_valid_samples": int(five_minute.loc[day_mask, "poa_wm2"].notna().sum()),
            "source_csv": source_by_day.get(requested_day),
            "status": status,
        })
    daily = pd.DataFrame(daily_rows)

    inventory_missing_csv_dates = [
        pd.Timestamp(value).date().isoformat()
        for value in (source_manifest.missing_csv_dates if source_manifest else [])
    ]
    missing_csv_dates = [
        row["date"].isoformat()
        for row in daily_rows
        if row["status"] == "MISSING_CSV"
    ]
    missing_poa_years = list(source_manifest.missing_poa_years) if source_manifest else []
    warnings_list = []
    if any(wrong_date_rows.values()):
        warnings_list.append("Rows outside their YYYYMMDD.csv date were dropped.")
    if negative_samples:
        warnings_list.append("Negative power samples were retained.")
    if missing_poa_years or poa_read_errors:
        warnings_list.append("POA is unavailable for one or more requested years.")

    metadata = {
        "pv_string_input": selection.canonical,
        "inverter_id": selection.inverter_id,
        "pv_string": selection.pv_label,
        "start_date": pd.Timestamp(dates[0]).date().isoformat(),
        "end_date": pd.Timestamp(dates[-1]).date().isoformat(),
        "generated_at_wita": pd.Timestamp.now(tz="Asia/Makassar").isoformat(),
        "source_url_csv": source_manifest.url_csv if source_manifest else "",
        "source_url_poa": source_manifest.url_poa if source_manifest else "",
        "csv_inventory_count": source_manifest.csv_inventory_count if source_manifest else 0,
        "poa_inventory_count": source_manifest.poa_inventory_count if source_manifest else 0,
        "loaded_csv_files": sorted(source_by_day.values()),
        "missing_csv_dates": missing_csv_dates,
        "inventory_missing_csv_dates": inventory_missing_csv_dates,
        "missing_poa_years": missing_poa_years,
        "download_errors": dict(download_errors or {}),
        "csv_read_errors": csv_read_errors,
        "poa_read_errors": poa_read_errors,
        "wrong_date_rows": wrong_date_rows,
        "duplicate_rows": duplicate_rows,
        "negative_power_samples": negative_samples,
        "power_sources": sorted(power_sources),
        "mapped_ws": mapped_ws,
        "ws_to_wb": ws_to_wb,
        "poa_fallback_samples": poa_fallback_samples,
        "yield_formula": "sum(power_kw_valid * 5/60)",
        "interval_minutes": 5,
        "warnings": warnings_list,
    }
    return ReportData(daily=daily, five_minute=five_minute, metadata=metadata)
```

- [ ] **Step 4: Run processing and regression tests**

```powershell
python -X utf8 -m pytest tests/unit/test_string_yield_report.py tests/unit/test_poa_loader.py tests/unit/test_physics.py -q
```

Expected: all pass, no tests skipped, no network call.

- [ ] **Step 5: Commit Task 2**

```powershell
git add pv_pipeline/string_yield_report.py tests/unit/test_string_yield_report.py
git commit -m "feat: calculate daily string yield report"
```

---

### Task 3: Matplotlib figures and four-sheet Excel workbook

**Files:**
- Modify: `pv_pipeline/string_yield_report.py`
- Modify: `tests/unit/test_string_yield_report.py`

**Interfaces:** Add both plotting functions, `build_output_path`, `write_report_workbook`, and `verify_report_workbook`.

- [ ] **Step 1: Write failing plot/workbook tests**

Create a two-day `ReportData` fixture with one valid day and one gap day. Assert:

```python
from openpyxl import load_workbook

from pv_pipeline.string_yield_report import (
    ReportData,
    build_output_path,
    plot_daily_yield,
    plot_power_vs_poa,
    write_report_workbook,
    verify_report_workbook,
)


@pytest.fixture
def selection():
    return parse_string_selection("WB05-INV01-PV03")


@pytest.fixture
def report_fixture():
    grid = pd.date_range("2026-05-01", "2026-05-02 23:55", freq="5min")
    power = pd.Series(np.nan, index=grid, dtype="float64")
    power.iloc[pd.Index(range(13)).difference([1])] = 10.0
    poa = pd.Series(500.0, index=grid)
    return ReportData(
        daily=pd.DataFrame({
            "date": [date(2026, 5, 1), date(2026, 5, 2)],
            "string_yield_kwh": [10.0, np.nan],
            "valid_power_samples": [12, 0],
            "expected_samples": [288, 288],
            "coverage_pct": [12 / 288 * 100, 0.0],
            "missing_power_samples": [276, 288],
            "poa_valid_samples": [288, 288],
            "source_csv": ["20260501.csv", None],
            "status": ["PARTIAL", "MISSING_CSV"],
        }),
        five_minute=pd.DataFrame({
            "timestamp": grid,
            "inverter_id": "WB05-INV01",
            "pv_string": "PV3",
            "power_kw": power.to_numpy(),
            "poa_wm2": poa.to_numpy(),
            "source_csv": ["20260501.csv" if ts.date() == date(2026, 5, 1) else None for ts in grid],
            "poa_source": "WS-2",
            "data_status": np.where(power.notna(), "POWER_POA", "POA_ONLY"),
        }),
        metadata={
            "source_url_csv": "https://drive.google.com/drive/folders/csv-test",
            "source_url_poa": "https://drive.google.com/drive/folders/poa-test",
            "missing_csv_dates": ["2026-05-02"],
            "poa_fallback_samples": 1,
            "warnings": [],
        },
    )


def test_output_path_and_workbook_contract(tmp_path, report_fixture, selection):
    path = build_output_path(tmp_path, selection, date(2026, 5, 1), date(2026, 5, 2))
    assert path.name == "string_yield_WB05-INV01_PV3_20260501_20260502.xlsx"
    written = write_report_workbook(path, report_fixture)
    verify_report_workbook(written)
    wb = load_workbook(written, data_only=False)
    assert wb.sheetnames == ["Ringkasan_Harian", "Data_5Menit", "Grafik", "Metadata"]
    assert len(wb["Ringkasan_Harian"]._charts) == 1
    assert len(wb["Grafik"]._charts) == 2
    assert wb["Ringkasan_Harian"]["B2"].value == pytest.approx(10.0)
    assert wb["Ringkasan_Harian"]["E2"].value == pytest.approx(12 / 288 * 100)
    assert wb["Ringkasan_Harian"]["E2"].number_format == '0.0"%"'
    assert wb["Data_5Menit"]["D3"].value is None


def test_plot_contract_uses_gap_and_secondary_axis(report_fixture, selection):
    fig_yield, ax_yield = plot_daily_yield(report_fixture.daily, selection)
    assert np.isnan(ax_yield.lines[0].get_ydata()).any()
    fig, (ax_power, ax_poa) = plot_power_vs_poa(
        report_fixture.five_minute, selection, date(2026, 5, 1), date(2026, 5, 2)
    )
    assert ax_power.get_ylabel() == "Power string (kW)"
    assert ax_poa.get_ylabel() == "POA irradiance (W/m²)"
    assert ax_power is not ax_poa
```

Also assert freeze panes/autofilter, full 5-minute row count, chart references point to source sheets, combo chart contains a secondary axis, workbook reopens, and Metadata contains both source URLs plus exact key `poa_fallback_samples`, while no key whose normalized name contains `token`, `cookie`, `secret`, `credential`, or `password`.

- [ ] **Step 2: Run and confirm RED**

```powershell
python -X utf8 -m pytest tests/unit/test_string_yield_report.py -q
```

Expected: missing plotting/writer functions or workbook assertions fail.

- [ ] **Step 3: Implement figures and writer**

Plot from `ReportData` only; do not recalculate yield:

```python
def plot_daily_yield(daily, selection):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(pd.to_datetime(daily["date"]), daily["string_yield_kwh"], marker="o")
    ax.set(title=f"Yield harian {selection.canonical}", xlabel="Tanggal", ylabel="String yield (kWh)")
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig, ax


def plot_power_vs_poa(five_minute, selection, start, end):
    import matplotlib.pyplot as plt
    fig, ax_power = plt.subplots(figsize=(14, 5))
    ax_poa = ax_power.twinx()
    line_power = ax_power.plot(five_minute["timestamp"], five_minute["power_kw"], label="Power string", color="tab:blue")[0]
    line_poa = ax_poa.plot(five_minute["timestamp"], five_minute["poa_wm2"], label="POA irradiance", color="tab:orange", alpha=0.75)[0]
    ax_power.set(xlabel="Waktu", ylabel="Power string (kW)", title=f"Power vs POA {selection.canonical} | {start} s.d. {end}")
    ax_poa.set_ylabel("POA irradiance (W/m²)")
    ax_power.legend([line_power, line_poa], [line_power.get_label(), line_poa.get_label()], loc="upper left")
    ax_power.grid(True, alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig, (ax_power, ax_poa)
```

Implement the workbook with `openpyxl.Workbook`, remove the default sheet, and append DataFrame values with `openpyxl.utils.dataframe.dataframe_to_rows`. Convert pandas `NaN`/`NaT` to `None` before append. Required writer details:

- `Ringkasan_Harian`: approved columns/order, date/yield/coverage formats, `freeze_panes="A2"`, autofilter, one `LineChart` with `display_blanks="gap"`.
- `Data_5Menit`: exact approved columns/order, timestamp format `yyyy-mm-dd hh:mm`, `freeze_panes="A2"`, autofilter.
- `Grafik`: a new daily `LineChart`, plus a power `LineChart` combined with a POA `LineChart` whose `y_axis.axId` is unique and `y_axis.crosses="max"`. Both charts reference `Ringkasan_Harian`/`Data_5Menit`; do not duplicate hidden chart data.
- `Metadata`: rows `key`, `value`; serialize lists/dicts with `json.dumps(value, ensure_ascii=False, default=str)`; reject sensitive normalized keys before saving.
- Save, reopen with `load_workbook`, call `verify_report_workbook()`, then return the same `Path`.

`verify_report_workbook()` must check file existence, exact sheet order, at least one chart in `Ringkasan_Harian`, exactly two charts in `Grafik`, and at least one data row in both source sheets. Raise `RuntimeError` with the failed invariant.

Use this concrete writer implementation; add `from openpyxl import Workbook, load_workbook`, `from openpyxl.chart import LineChart, Reference`, and `from openpyxl.styles import Font` to the module imports:

```python
DAILY_COLUMNS = [
    "date", "string_yield_kwh", "valid_power_samples", "expected_samples",
    "coverage_pct", "missing_power_samples", "poa_valid_samples", "source_csv", "status",
]
FIVE_MINUTE_COLUMNS = [
    "timestamp", "inverter_id", "pv_string", "power_kw", "poa_wm2",
    "source_csv", "poa_source", "data_status",
]
SHEET_ORDER = ["Ringkasan_Harian", "Data_5Menit", "Grafik", "Metadata"]
SENSITIVE_METADATA_TERMS = ("token", "cookie", "secret", "credential", "password")


def _excel_value(value):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return value
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    return value


def _append_dataframe(sheet, frame):
    sheet.append(list(frame.columns))
    for row in frame.itertuples(index=False, name=None):
        sheet.append([_excel_value(value) for value in row])
    for cell in sheet[1]:
        cell.font = Font(bold=True)


def _daily_excel_chart(source_sheet):
    chart = LineChart()
    chart.title = "String Yield Harian"
    chart.y_axis.title = "Yield (kWh)"
    chart.x_axis.title = "Tanggal"
    chart.height = 8
    chart.width = 16
    chart.display_blanks = "gap"
    values = Reference(
        source_sheet, min_col=2, min_row=1, max_row=source_sheet.max_row,
    )
    dates = Reference(
        source_sheet, min_col=1, min_row=2, max_row=source_sheet.max_row,
    )
    chart.add_data(values, titles_from_data=True)
    chart.set_categories(dates)
    return chart


def build_output_path(output_dir, selection, start, end):
    return Path(output_dir) / (
        f"string_yield_{selection.inverter_id}_{selection.pv_label}_"
        f"{start:%Y%m%d}_{end:%Y%m%d}.xlsx"
    )


def write_report_workbook(output_path, report):
    if list(report.daily.columns) != DAILY_COLUMNS:
        raise ValueError(f"Unexpected daily columns: {list(report.daily.columns)!r}")
    if list(report.five_minute.columns) != FIVE_MINUTE_COLUMNS:
        raise ValueError(
            f"Unexpected five-minute columns: {list(report.five_minute.columns)!r}"
        )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()
    workbook.remove(workbook.active)
    daily_sheet = workbook.create_sheet("Ringkasan_Harian")
    five_sheet = workbook.create_sheet("Data_5Menit")
    graph_sheet = workbook.create_sheet("Grafik")
    metadata_sheet = workbook.create_sheet("Metadata")

    _append_dataframe(daily_sheet, report.daily[DAILY_COLUMNS])
    daily_sheet.freeze_panes = "A2"
    daily_sheet.auto_filter.ref = daily_sheet.dimensions
    for row in range(2, daily_sheet.max_row + 1):
        daily_sheet.cell(row=row, column=1).number_format = "yyyy-mm-dd"
        daily_sheet.cell(row=row, column=2).number_format = "0.000"
        daily_sheet.cell(row=row, column=5).number_format = '0.0"%"'
    daily_sheet.add_chart(_daily_excel_chart(daily_sheet), "K2")

    _append_dataframe(five_sheet, report.five_minute[FIVE_MINUTE_COLUMNS])
    five_sheet.freeze_panes = "A2"
    five_sheet.auto_filter.ref = five_sheet.dimensions
    for row in range(2, five_sheet.max_row + 1):
        five_sheet.cell(row=row, column=1).number_format = "yyyy-mm-dd hh:mm"
        five_sheet.cell(row=row, column=4).number_format = "0.000"
        five_sheet.cell(row=row, column=5).number_format = "0.0"

    graph_sheet.sheet_view.showGridLines = False
    graph_sheet.add_chart(_daily_excel_chart(daily_sheet), "A1")
    power_chart = LineChart()
    power_chart.title = "Power String vs POA Irradiance"
    power_chart.y_axis.title = "Power string (kW)"
    power_chart.x_axis.title = "Waktu"
    power_chart.y_axis.axId = 10
    power_chart.height = 12
    power_chart.width = 24
    power_chart.display_blanks = "gap"
    time_values = Reference(five_sheet, min_col=1, min_row=2, max_row=five_sheet.max_row)
    power_values = Reference(five_sheet, min_col=4, min_row=1, max_row=five_sheet.max_row)
    power_chart.add_data(power_values, titles_from_data=True)
    power_chart.set_categories(time_values)

    poa_chart = LineChart()
    poa_chart.y_axis.title = "POA irradiance (W/m²)"
    poa_chart.y_axis.axId = 200
    poa_chart.y_axis.crosses = "max"
    poa_chart.display_blanks = "gap"
    poa_values = Reference(five_sheet, min_col=5, min_row=1, max_row=five_sheet.max_row)
    poa_chart.add_data(poa_values, titles_from_data=True)
    poa_chart.set_categories(time_values)
    power_chart += poa_chart
    graph_sheet.add_chart(power_chart, "A18")

    metadata_sheet.append(["key", "value"])
    for cell in metadata_sheet[1]:
        cell.font = Font(bold=True)
    for key, value in report.metadata.items():
        normalized_key = re.sub(r"[^a-z]", "", str(key).casefold())
        if any(term in normalized_key for term in SENSITIVE_METADATA_TERMS):
            raise ValueError(f"Sensitive metadata key is not allowed: {key!r}")
        if isinstance(value, (dict, list, tuple)):
            value = json.dumps(value, ensure_ascii=False, default=str)
        metadata_sheet.append([str(key), _excel_value(value)])
    metadata_sheet.freeze_panes = "A2"
    metadata_sheet.auto_filter.ref = metadata_sheet.dimensions

    workbook.save(output_path)
    verify_report_workbook(output_path)
    return output_path


def verify_report_workbook(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"Workbook was not created: {path}")
    workbook = load_workbook(path, read_only=False, data_only=False)
    try:
        if workbook.sheetnames != SHEET_ORDER:
            raise RuntimeError(f"Unexpected sheet order: {workbook.sheetnames!r}")
        if workbook["Ringkasan_Harian"].max_row < 2:
            raise RuntimeError("Ringkasan_Harian has no data row.")
        if workbook["Data_5Menit"].max_row < 2:
            raise RuntimeError("Data_5Menit has no data row.")
        if len(workbook["Ringkasan_Harian"]._charts) != 1:
            raise RuntimeError("Ringkasan_Harian must contain one chart.")
        if len(workbook["Grafik"]._charts) != 2:
            raise RuntimeError("Grafik must contain daily and combo charts.")
    finally:
        workbook.close()
```

- [ ] **Step 4: Run workbook tests and compile**

```powershell
python -X utf8 -m pytest tests/unit/test_string_yield_report.py -q
python -X utf8 -m py_compile pv_pipeline/string_yield_report.py
```

Expected: all pass; workbook fixture reopens; no skipped test.

- [ ] **Step 5: Commit Task 3**

```powershell
git add pv_pipeline/string_yield_report.py tests/unit/test_string_yield_report.py
git commit -m "feat: export string yield workbook"
```

---

### Task 4: Generated nine-cell notebook and offline rerunnable smoke

**Files:**
- Create: `output_string/_build_string_yield_notebook.py`
- Create (generated): `output_string/String_Yield_Power_Irradiance.ipynb`
- Create: `output_string/_smoke_string_yield_notebook.py`
- Modify: `tests/unit/test_string_yield_report.py`
- Modify: `.gitignore`

**Interfaces:** Builder `build(out: Path = OUT) -> Path`; notebook cells 0..8 exactly as approved; smoke executes cells 2..7 with patched acquisition and synthetic files.

- [ ] **Step 1: Add failing builder structure test**

```python
import ast
import importlib.util
import json


def test_builder_writes_nbformat_45_with_nine_expected_cells(tmp_path):
    path = Path("output_string/_build_string_yield_notebook.py")
    spec = importlib.util.spec_from_file_location("string_yield_nb_builder", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    target = module.build(tmp_path / "report.ipynb")
    nb = json.loads(target.read_text(encoding="utf-8"))
    assert (nb["nbformat"], nb["nbformat_minor"]) == (4, 5)
    assert [c["cell_type"] for c in nb["cells"]] == ["markdown"] + ["code"] * 8
    markers = ["gdown>=6.0.0", "URL_CSV", "download_report_inputs", "build_report_data", "plot_daily_yield", "plot_power_vs_poa", "write_report_workbook", "google.colab"]
    for cell, marker in zip(nb["cells"][1:], markers):
        source = "".join(cell["source"])
        assert marker in source
        ast.parse(source)
```

- [ ] **Step 2: Run and confirm RED**

```powershell
python -X utf8 -m pytest tests/unit/test_string_yield_report.py -q
```

Expected: builder file is absent.

- [ ] **Step 3: Implement builder with exact cell contract**

Create `output_string/_build_string_yield_notebook.py`. Use the existing `_cell()` raw-JSON pattern and these cell bodies:

```python
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "String_Yield_Power_Irradiance.ipynb"

MD_INTRO = '''# Laporan String Yield, Power, dan Irradiance

Notebook ini memeriksa satu PV string pada rentang tanggal inklusif.

- CSV string: folder Google Drive publik CSV Export PV String.
- POA: file `POA PLTS IKN YYYY.xlsx` dari folder raw data input.
- Yield harian: `Σ(power_kw_valid × 5/60)`; power hilang tidak diisi atau diestimasi.
- Output: workbook empat-sheet di `output_string/`, lalu dapat diunduh dari Cell 8.

Jalankan Cell 1 sampai Cell 8 berurutan. Edit hanya lima nilai di Cell 2.
'''

CODE_SETUP = '''# Cell 1 — Setup Colab/repo/output
import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "--upgrade", "gdown>=6.0.0"])
from pathlib import Path
import os, tempfile

def find_repo_root(start=None):
    path = Path(start or os.getcwd()).resolve()
    for candidate in [path, *path.parents]:
        if (candidate / "pv_pipeline").is_dir() and (candidate / "config").is_dir():
            return candidate
    raise RuntimeError("Repo root tidak ditemukan; jalankan notebook dari clone SolarYieldPro.")

REPO_DIR = find_repo_root()
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))
OUTPUT_DIR = REPO_DIR / "output_string"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
INPUT_DIR = Path(tempfile.mkdtemp(prefix="string_yield_inputs_"))
print("OUTPUT_DIR:", OUTPUT_DIR)
'''

CODE_CONFIG = '''# Cell 2 — Konfigurasi input (edit lima nilai ini)
from pv_pipeline.string_yield_report import parse_date_range, parse_string_selection, validate_drive_folder_url
URL_CSV = "https://drive.google.com/drive/folders/1f_KrPuqfZJTE5I9cVQiyp65QrHbkF3Iw?usp=sharing"
URL_RAW_DATA_INPUT = "https://drive.google.com/drive/folders/1y37AsVViI7IL1tCRhvAGEjiq9wWnfu_e?usp=drive_link"
PV_STRING = "WB05-INV01-PV03"
START_DATE = "2026-05-01"
END_DATE = "2026-05-14"
SELECTION = parse_string_selection(PV_STRING)
DATES = parse_date_range(START_DATE, END_DATE)
validate_drive_folder_url(URL_CSV)
validate_drive_folder_url(URL_RAW_DATA_INPUT)
print(SELECTION, DATES[0].date(), "s.d.", DATES[-1].date())
'''

CODE_DOWNLOAD = '''# Cell 3 — Inventaris dan download selektif
from pv_pipeline.string_yield_report import download_report_inputs
if "DATES" not in globals():
    raise RuntimeError("Jalankan Cell 2 terlebih dahulu.")
MANIFEST, INPUTS = download_report_inputs(URL_CSV, URL_RAW_DATA_INPUT, DATES, INPUT_DIR)
print("CSV inventory:", MANIFEST.csv_inventory_count, "dipilih:", len(MANIFEST.csv_by_date), "berhasil:", len(INPUTS.csv_by_date), "missing:", MANIFEST.missing_csv_dates)
print("POA inventory:", MANIFEST.poa_inventory_count, "dipilih:", len(MANIFEST.poa_by_year), "berhasil:", len(INPUTS.poa_by_year), "missing years:", MANIFEST.missing_poa_years)
print("Download errors:", INPUTS.download_errors)
'''

CODE_PROCESS = '''# Cell 4 — Load, grid 5-menit, POA, dan yield harian
try:
    from IPython.display import display
except ImportError:
    display = print
from pv_pipeline.string_yield_report import build_report_data
if "INPUTS" not in globals():
    raise RuntimeError("Jalankan Cell 3 terlebih dahulu.")
REPORT = build_report_data(
    INPUTS.csv_by_date, INPUTS.poa_by_year, SELECTION, DATES,
    REPO_DIR / "config/site_geometry.yaml",
    source_manifest=MANIFEST, download_errors=INPUTS.download_errors,
)
display(REPORT.daily)
print(REPORT.metadata)
STATUS_COUNTS = REPORT.daily["status"].value_counts()
print({
    "requested_days": len(DATES),
    "loaded_csv_days": len(REPORT.metadata.get("loaded_csv_files", [])),
    "missing_csv_days": int((REPORT.daily["status"] == "MISSING_CSV").sum()),
    "complete_days": int(STATUS_COUNTS.get("COMPLETE", 0)),
    "partial_days": int(STATUS_COUNTS.get("PARTIAL", 0)),
})
'''

CODE_DAILY_PLOT = '''# Cell 5 — Grafik yield harian per tanggal
from pv_pipeline.string_yield_report import plot_daily_yield
if "REPORT" not in globals():
    raise RuntimeError("Jalankan Cell 4 terlebih dahulu.")
FIG_DAILY, AX_DAILY = plot_daily_yield(REPORT.daily, SELECTION)
import matplotlib.pyplot as plt
plt.show()
'''

CODE_POWER_PLOT = '''# Cell 6 — Grafik power vs irradiance, sumbu-Y sekunder
from pv_pipeline.string_yield_report import plot_power_vs_poa
if "REPORT" not in globals():
    raise RuntimeError("Jalankan Cell 4 terlebih dahulu.")
FIG_POWER_POA, (AX_POWER, AX_POA) = plot_power_vs_poa(REPORT.five_minute, SELECTION, DATES[0].date(), DATES[-1].date())
import matplotlib.pyplot as plt
plt.show()
'''

CODE_EXPORT = '''# Cell 7 — Ekspor dan verifikasi workbook
from openpyxl import load_workbook
from pv_pipeline.string_yield_report import build_output_path, verify_report_workbook, write_report_workbook
if "REPORT" not in globals():
    raise RuntimeError("Jalankan Cell 4 terlebih dahulu.")
OUTPUT_XLSX = build_output_path(OUTPUT_DIR, SELECTION, DATES[0].date(), DATES[-1].date())
write_report_workbook(OUTPUT_XLSX, REPORT)
verify_report_workbook(OUTPUT_XLSX)
CHECK_WB = load_workbook(OUTPUT_XLSX, read_only=False, data_only=False)
print("Workbook:", OUTPUT_XLSX, "sheets:", CHECK_WB.sheetnames, "bytes:", OUTPUT_XLSX.stat().st_size)
CHECK_WB.close()
'''

CODE_DOWNLOAD_XLSX = '''# Cell 8 — Download dari Colab; lokal hanya menampilkan path
if "OUTPUT_XLSX" not in globals() or not OUTPUT_XLSX.exists():
    raise RuntimeError("Jalankan Cell 7 terlebih dahulu.")
try:
    from google.colab import files
except ImportError:
    print("Bukan Colab; workbook tersedia di", OUTPUT_XLSX)
else:
    files.download(str(OUTPUT_XLSX))
'''

CELLS = [
    ("markdown", MD_INTRO),
    ("code", CODE_SETUP),
    ("code", CODE_CONFIG),
    ("code", CODE_DOWNLOAD),
    ("code", CODE_PROCESS),
    ("code", CODE_DAILY_PLOT),
    ("code", CODE_POWER_PLOT),
    ("code", CODE_EXPORT),
    ("code", CODE_DOWNLOAD_XLSX),
]


def _cell(kind: str, source: str, index: int) -> dict:
    cell = {
        "cell_type": kind,
        "id": f"string-yield-{index:02d}",
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }
    if kind == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def build(out: Path = OUT) -> Path:
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    notebook = {
        "cells": [_cell(kind, source, index) for index, (kind, source) in enumerate(CELLS)],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    target.write_text(json.dumps(notebook, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {target} ({len(CELLS)} cells)")
    return target


if __name__ == "__main__":
    build()
```

The builder above is the complete scaffold; do not introduce `nbformat` or edit the generated notebook by hand.

- [ ] **Step 4: Generate notebook and run builder test**

```powershell
python -X utf8 output_string/_build_string_yield_notebook.py
python -X utf8 -m pytest tests/unit/test_string_yield_report.py -q
```

Expected: generated notebook has nine cells; all code cells parse; tests pass.

- [ ] **Step 5: Implement offline smoke and runtime ignore**

Create `output_string/_smoke_string_yield_notebook.py` with this complete scaffold:

```python
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from openpyxl import load_workbook

plt.show = lambda *args, **kwargs: None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pv_pipeline.string_yield_report import (
    DownloadedInputs,
    DriveItem,
    SourceManifest,
    parse_date_range,
    parse_string_selection,
)

NOTEBOOK = ROOT / "output_string/String_Yield_Power_Irradiance.ipynb"


def _source(notebook: dict, index: int) -> str:
    return "".join(notebook["cells"][index]["source"])


def _metadata_values(workbook) -> dict[str, object]:
    sheet = workbook["Metadata"]
    return {sheet.cell(row=row, column=1).value: sheet.cell(row=row, column=2).value for row in range(2, sheet.max_row + 1)}


def main() -> None:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="string_yield_smoke_") as temp_name:
        temp = Path(temp_name)
        input_dir = temp / "inputs"
        output_dir = temp / "output_string"
        input_dir.mkdir()
        output_dir.mkdir()

        # 13 nominal slots 00:00..01:00; delete 00:05 so 12 x 10 kW = 10 kWh.
        nominal = pd.date_range("2026-05-01 00:00", periods=13, freq="5min")
        observed = nominal.delete(1)
        csv_path = input_dir / "20260501.csv"
        pd.DataFrame({
            "Start Time": observed,
            "Inverter_ID": "WB05-INV01",
            "PV3 Power(kW)": 10.0,
        }).to_csv(csv_path, index=False)

        poa_path = input_dir / "POA PLTS IKN 2026.xlsx"
        poa_data = {"Date time": nominal, "Rata-rata WS 1 - WS 5": 450.0}
        for ws_number in range(1, 6):
            poa_data[f"POA Irradiance (W/m2) WS {ws_number}"] = 500.0
        poa_data["POA Irradiance (W/m2) WS 2"] = np.array(
            [500.0, np.nan] + [500.0] * 11,
        )
        with pd.ExcelWriter(poa_path, engine="openpyxl") as writer:
            pd.DataFrame(poa_data).to_excel(writer, sheet_name="POA PLTS IKN", index=False)

        selected_csv = DriveItem("local-csv", "20260501.csv")
        selected_poa = DriveItem("local-poa", "POA PLTS IKN 2026.xlsx")
        manifest = SourceManifest(
            csv_by_date={date(2026, 5, 1): selected_csv},
            poa_by_year={2026: selected_poa},
            missing_csv_dates=[],
            missing_poa_years=[],
            url_csv="https://drive.google.com/drive/folders/csv-smoke",
            url_poa="https://drive.google.com/drive/folders/poa-smoke",
            csv_inventory_count=1,
            poa_inventory_count=1,
        )
        inputs = DownloadedInputs(
            csv_by_date={date(2026, 5, 1): csv_path},
            poa_by_year={2026: poa_path},
            download_errors={},
        )

        scope = {
            "REPO_DIR": ROOT,
            "OUTPUT_DIR": output_dir,
            "INPUT_DIR": input_dir,
        }
        exec(_source(notebook, 2), scope)
        scope.update({
            "PV_STRING": "WB05-INV01-PV03",
            "START_DATE": "2026-05-01",
            "END_DATE": "2026-05-01",
            "SELECTION": parse_string_selection("WB05-INV01-PV03"),
            "DATES": parse_date_range("2026-05-01", "2026-05-01"),
        })

        with patch(
            "pv_pipeline.string_yield_report.download_report_inputs",
            return_value=(manifest, inputs),
        ):
            for index in range(3, 8):
                exec(_source(notebook, index), scope)

            output_xlsx = Path(scope["OUTPUT_XLSX"])
            workbook = load_workbook(output_xlsx, data_only=False)
            assert workbook.sheetnames == ["Ringkasan_Harian", "Data_5Menit", "Grafik", "Metadata"]
            assert workbook["Ringkasan_Harian"]["B2"].value == 10.0
            headers = {cell.value: cell.column for cell in workbook["Data_5Menit"][1]}
            assert workbook["Data_5Menit"].cell(row=3, column=headers["power_kw"]).value is None
            assert workbook["Data_5Menit"].cell(row=3, column=headers["poa_source"]).value == "avg"
            metadata = _metadata_values(workbook)
            assert metadata["poa_fallback_samples"] == 1
            assert len(workbook["Ringkasan_Harian"]._charts) == 1
            assert len(workbook["Grafik"]._charts) == 2
            row_counts = (workbook["Ringkasan_Harian"].max_row, workbook["Data_5Menit"].max_row)
            workbook.close()

            plt.close("all")
            for index in range(3, 8):
                exec(_source(notebook, index), scope)

        workbooks = list(output_dir.glob("string_yield_*.xlsx"))
        assert workbooks == [output_xlsx]
        rerun = load_workbook(output_xlsx, data_only=False)
        assert (rerun["Ringkasan_Harian"].max_row, rerun["Data_5Menit"].max_row) == row_counts
        assert len(rerun["Ringkasan_Harian"]._charts) == 1
        assert len(rerun["Grafik"]._charts) == 2
        rerun.close()
        print("[smoke] OK")


if __name__ == "__main__":
    main()
```

The smoke must not execute Cell 1 or Cell 8 and must make no network call.

Add exactly this line to `.gitignore`:

```gitignore
output_string/string_yield_*.xlsx
```

- [ ] **Step 6: Run the complete offline verification bundle**

```powershell
python -X utf8 output_string/_build_string_yield_notebook.py
python -X utf8 output_string/_smoke_string_yield_notebook.py
python -X utf8 -m pytest tests/unit/test_string_yield_report.py tests/unit/test_poa_loader.py tests/unit/test_physics.py -q
python -X utf8 -m py_compile pv_pipeline/string_yield_report.py output_string/_build_string_yield_notebook.py output_string/_smoke_string_yield_notebook.py
git diff --check
```

Expected: builder and smoke print success; all tests pass with zero skipped; compile and whitespace checks exit 0. This proves offline processing/export, not live Google Drive permissions or a real Colab download.

- [ ] **Step 7: Inspect the generated artifact and commit Task 4**

```powershell
git status --short
git diff --stat
git add .gitignore output_string/_build_string_yield_notebook.py output_string/String_Yield_Power_Irradiance.ipynb output_string/_smoke_string_yield_notebook.py tests/unit/test_string_yield_report.py
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: add string yield Colab notebook"
```

Before committing, confirm no pre-existing dirty docs, `coba/`, or other user artifacts are staged.

---

## Final Verification and Handoff

- [ ] Re-run the full Task 4 verification bundle from a clean Python process.
- [ ] Inspect `git status --short` and ensure only intended commits/files belong to this feature.
- [ ] Report exact targeted test count, smoke result, workbook sheet/chart checks, and the limitation that public Drive/Colab behavior still needs one live run.
- [ ] Do not claim live Google Drive success from the offline smoke.
