# M2 Trends Dashboard Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Streamlit "Trends" page that plots daily time-series (per `source_date`) for every M2 detector and its computed metrics.

**Architecture:** A pure aggregation module `data/trends.py` (no Streamlit import, unit-tested) transforms the concatenated findings sheets from the existing `cached_findings_range` into per-day trend frames. A thin UI page `pages/trends.py` (+ numbered wrapper `pages/5_Trends.py`) renders Altair line charts section-by-section. One extra line in `detectors.py` adds an Intermittent tab so the existing deep-dive page also covers the LSTM-AE detector.

**Tech Stack:** Python 3, pandas, Altair, Streamlit. No new dependencies.

## Global Constraints

- No new data layer: consume `cached_findings_range(start, end)` from `pv_pipeline.dashboard.data.cache`. Do NOT add Drive/gdrive/cache code.
- Every row in `LoadResult.sheets[...]` already carries a `source_date` column (added by `concat_findings_range`). This is the trend X-axis.
- Aggregation functions in `data/trends.py` MUST NOT import Streamlit and MUST return an empty DataFrame (never raise) when a sheet/column is missing or the input is empty.
- Streamlit and Altair are imported lazily INSIDE functions with `# noqa: WPS433` (existing project convention — see `detector_tab.py`, `findings.py`).
- Sheet names are emitted as `{submodule.name}_{artifact}` (verified `core.py:265`). Exact names used here: `Findings`, `M2a_soiling_EconomicAnalysis`, `M2b_intermittent_WindowErrors`, `M2b_mppt_ratio_StringStatus`, `M2_iforest_AnomalySummary`, `M2b_peer_zscore_StringStatus`, `M2b_open_circuit_StringStatus`, `M2b_ground_fault_StringStatus`, `M2a_shading_ShadingSummary`, `M2a_low_irradiance_LowIrradianceSummary`.
- Tests live in `tests/unit/dashboard/test_trends.py`, run with `python -m pytest`.
- Commits are LOCAL only (`git commit`, no push). The user pushes to both remotes (nabilhaidr + ompltsikn) separately when they ask.
- Commit message style: conventional commits (`feat:`, `test:`).

---

### Task 1: Core aggregation functions (findings / status / numeric)

**Files:**
- Create: `pv_pipeline/dashboard/data/trends.py`
- Test: `tests/unit/dashboard/test_trends.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `findings_counts_per_day(findings_df: pd.DataFrame) -> pd.DataFrame` with columns `["source_date", "sub_module", "severity", "count"]`.
  - `status_counts_per_day(df: pd.DataFrame, status_col: str = "status") -> pd.DataFrame` with columns `["source_date", <status_col>, "count", "pct"]`.
  - `numeric_metric_per_day(df: pd.DataFrame, value_col: str, aggs: tuple[str, ...] = ("mean", "max", "median")) -> pd.DataFrame` with columns `["source_date", f"{value_col}_{agg}", ...]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/dashboard/test_trends.py`:

```python
from __future__ import annotations

from datetime import date

import pandas as pd

from pv_pipeline.dashboard.data.trends import (
    findings_counts_per_day,
    numeric_metric_per_day,
    status_counts_per_day,
)


def test_findings_counts_per_day_groups_by_day_submodule_severity():
    df = pd.DataFrame({
        "source_date": [date(2026, 5, 1), date(2026, 5, 1), date(2026, 5, 2)],
        "sub_module": ["M2a_soiling", "M2a_soiling", "M2a_soiling"],
        "severity": ["HIGH", "HIGH", "HIGH"],
    })
    out = findings_counts_per_day(df)
    day1 = out[out["source_date"] == date(2026, 5, 1)]
    assert int(day1["count"].iloc[0]) == 2
    day2 = out[out["source_date"] == date(2026, 5, 2)]
    assert int(day2["count"].iloc[0]) == 1


def test_findings_counts_per_day_missing_columns_returns_empty():
    out = findings_counts_per_day(pd.DataFrame({"foo": [1]}))
    assert out.empty
    assert list(out.columns) == ["source_date", "sub_module", "severity", "count"]


def test_status_counts_per_day_computes_count_and_pct():
    df = pd.DataFrame({
        "source_date": [date(2026, 5, 1)] * 4,
        "status": ["NORMAL", "NORMAL", "NORMAL", "high_R"],
    })
    out = status_counts_per_day(df)
    normal = out[out["status"] == "NORMAL"]
    assert int(normal["count"].iloc[0]) == 3
    assert float(normal["pct"].iloc[0]) == 75.0


def test_status_counts_per_day_missing_column_returns_empty():
    out = status_counts_per_day(pd.DataFrame({"source_date": [date(2026, 5, 1)]}))
    assert out.empty


def test_numeric_metric_per_day_aggregates_mean_max_median():
    df = pd.DataFrame({
        "source_date": [date(2026, 5, 1), date(2026, 5, 1), date(2026, 5, 2)],
        "flagged_pct": [10.0, 20.0, 5.0],
    })
    out = numeric_metric_per_day(df, "flagged_pct")
    day1 = out[out["source_date"] == date(2026, 5, 1)].iloc[0]
    assert day1["flagged_pct_mean"] == 15.0
    assert day1["flagged_pct_max"] == 20.0
    assert day1["flagged_pct_median"] == 15.0


def test_numeric_metric_per_day_coerces_and_drops_non_numeric():
    df = pd.DataFrame({
        "source_date": [date(2026, 5, 1), date(2026, 5, 1)],
        "ratio_median_daylight": [1.0, None],
    })
    out = numeric_metric_per_day(df, "ratio_median_daylight", aggs=("median",))
    assert out["ratio_median_daylight_median"].iloc[0] == 1.0


def test_numeric_metric_per_day_missing_column_returns_empty():
    out = numeric_metric_per_day(
        pd.DataFrame({"source_date": [date(2026, 5, 1)]}), "flagged_pct",
    )
    assert out.empty


def test_window_errors_group_by_source_date_not_internal_date():
    # Rule 9 intent: trend must key off the artifact day (source_date),
    # NOT the WindowErrors internal `date` column. They differ here on purpose.
    df = pd.DataFrame({
        "source_date": [date(2026, 5, 1), date(2026, 5, 1)],
        "date": [pd.Timestamp("2026-04-30"), pd.Timestamp("2026-04-30")],
        "error_ratio": [0.5, 1.5],
    })
    out = numeric_metric_per_day(df, "error_ratio", aggs=("max",))
    assert list(out["source_date"]) == [date(2026, 5, 1)]
    assert out["error_ratio_max"].iloc[0] == 1.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/dashboard/test_trends.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pv_pipeline.dashboard.data.trends'`

- [ ] **Step 3: Write minimal implementation**

Create `pv_pipeline/dashboard/data/trends.py`:

```python
"""Pure daily-trend aggregations over concatenated M2 findings sheets.

Every function groups by ``source_date`` (the artifact day added by
``concat_findings_range``) and returns an EMPTY DataFrame when the input is
empty or missing required columns -- never raises. The UI turns an empty
result into an "inactive detector" notice instead of crashing.

No Streamlit import here: this module is unit-tested standalone.
"""

from __future__ import annotations

import pandas as pd


def findings_counts_per_day(findings_df: pd.DataFrame) -> pd.DataFrame:
    """Count findings per (source_date, sub_module, severity)."""
    cols = ["source_date", "sub_module", "severity", "count"]
    required = {"source_date", "sub_module", "severity"}
    if findings_df is None or findings_df.empty or not required.issubset(findings_df.columns):
        return pd.DataFrame(columns=cols)
    return (
        findings_df.groupby(["source_date", "sub_module", "severity"])
        .size()
        .reset_index(name="count")
    )


def status_counts_per_day(df: pd.DataFrame, status_col: str = "status") -> pd.DataFrame:
    """Count and percent of rows per (source_date, status)."""
    cols = ["source_date", status_col, "count", "pct"]
    if df is None or df.empty or not {"source_date", status_col}.issubset(df.columns):
        return pd.DataFrame(columns=cols)
    counts = df.groupby(["source_date", status_col]).size().reset_index(name="count")
    totals = counts.groupby("source_date")["count"].transform("sum")
    counts["pct"] = (counts["count"] / totals * 100.0).round(2)
    return counts


def numeric_metric_per_day(
    df: pd.DataFrame,
    value_col: str,
    aggs: tuple[str, ...] = ("mean", "max", "median"),
) -> pd.DataFrame:
    """Aggregate a numeric column per source_date into one column per agg."""
    out_cols = ["source_date", *[f"{value_col}_{a}" for a in aggs]]
    if df is None or df.empty or not {"source_date", value_col}.issubset(df.columns):
        return pd.DataFrame(columns=out_cols)
    work = df[["source_date"]].copy()
    work[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    work = work.dropna(subset=[value_col])
    if work.empty:
        return pd.DataFrame(columns=out_cols)
    grouped = work.groupby("source_date")[value_col].agg(list(aggs))
    grouped.columns = [f"{value_col}_{a}" for a in aggs]
    return grouped.reset_index()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/dashboard/test_trends.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/dashboard/data/trends.py tests/unit/dashboard/test_trends.py
git commit -m "feat(dashboard): core per-day trend aggregations for M2 detectors"
```

---

### Task 2: Specialized aggregation functions (soiling / wide-melt)

**Files:**
- Modify: `pv_pipeline/dashboard/data/trends.py`
- Test: `tests/unit/dashboard/test_trends.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `soiling_ratio_per_day(econ_df: pd.DataFrame) -> pd.DataFrame` with columns `["source_date", "soiling_ratio", "sr_ci_lower", "sr_ci_upper", "recommend_cleaning"]`.
  - `wide_counts_per_day(df: pd.DataFrame, count_cols: list[str]) -> pd.DataFrame` with columns `["source_date", "classification", "count"]`.

- [ ] **Step 1: Append the failing tests**

Extend the import at the top of `tests/unit/dashboard/test_trends.py` to:

```python
from pv_pipeline.dashboard.data.trends import (
    findings_counts_per_day,
    numeric_metric_per_day,
    soiling_ratio_per_day,
    status_counts_per_day,
    wide_counts_per_day,
)
```

Then append these tests to the end of the file:

```python
def test_soiling_ratio_per_day_passthrough_and_coerce():
    df = pd.DataFrame({
        "source_date": [date(2026, 5, 1), date(2026, 5, 2)],
        "soiling_ratio": ["0.98", 0.95],
        "sr_ci_lower": [0.97, 0.94],
        "sr_ci_upper": [0.99, 0.96],
        "recommend_cleaning": [False, True],
    })
    out = soiling_ratio_per_day(df)
    assert out["soiling_ratio"].iloc[0] == 0.98
    assert bool(out["recommend_cleaning"].iloc[1]) is True


def test_soiling_ratio_per_day_missing_optional_ci_columns_still_present():
    df = pd.DataFrame({"source_date": [date(2026, 5, 1)], "soiling_ratio": [0.98]})
    out = soiling_ratio_per_day(df)
    assert out["soiling_ratio"].iloc[0] == 0.98
    assert "sr_ci_lower" in out.columns
    assert pd.isna(out["sr_ci_lower"].iloc[0])


def test_soiling_ratio_per_day_missing_required_returns_empty():
    out = soiling_ratio_per_day(pd.DataFrame({"source_date": [date(2026, 5, 1)]}))
    assert out.empty


def test_wide_counts_per_day_melts_count_columns_to_long():
    df = pd.DataFrame({
        "source_date": [date(2026, 5, 1), date(2026, 5, 2)],
        "normal": [10, 12],
        "low_irradiance_underperform": [2, 1],
        "general_underperform": [1, 0],
        "skipped": [0, 3],
    })
    cols = ["normal", "low_irradiance_underperform", "general_underperform", "skipped"]
    out = wide_counts_per_day(df, cols)
    assert len(out) == 8  # 2 days x 4 classifications
    cell = out[(out["source_date"] == date(2026, 5, 1)) & (out["classification"] == "normal")]
    assert int(cell["count"].iloc[0]) == 10


def test_wide_counts_per_day_missing_all_count_cols_returns_empty():
    out = wide_counts_per_day(pd.DataFrame({"source_date": [date(2026, 5, 1)]}), ["normal"])
    assert out.empty
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `python -m pytest tests/unit/dashboard/test_trends.py -k "soiling or wide" -v`
Expected: FAIL with `ImportError: cannot import name 'soiling_ratio_per_day'`

- [ ] **Step 3: Append the implementation**

Add to the end of `pv_pipeline/dashboard/data/trends.py`:

```python
def soiling_ratio_per_day(econ_df: pd.DataFrame) -> pd.DataFrame:
    """Soiling ratio + CI band per source_date (light passthrough + coercion)."""
    cols = ["source_date", "soiling_ratio", "sr_ci_lower", "sr_ci_upper", "recommend_cleaning"]
    if (
        econ_df is None
        or econ_df.empty
        or not {"source_date", "soiling_ratio"}.issubset(econ_df.columns)
    ):
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame({"source_date": econ_df["source_date"].to_numpy()})
    for col in ["soiling_ratio", "sr_ci_lower", "sr_ci_upper"]:
        out[col] = (
            pd.to_numeric(econ_df[col], errors="coerce").to_numpy()
            if col in econ_df.columns
            else pd.NA
        )
    out["recommend_cleaning"] = (
        econ_df["recommend_cleaning"].to_numpy()
        if "recommend_cleaning" in econ_df.columns
        else pd.NA
    )
    return out.reset_index(drop=True)


def wide_counts_per_day(df: pd.DataFrame, count_cols: list[str]) -> pd.DataFrame:
    """Melt wide count columns (one column per class) into long trend rows."""
    cols = ["source_date", "classification", "count"]
    if df is None or df.empty or "source_date" not in df.columns:
        return pd.DataFrame(columns=cols)
    present = [c for c in count_cols if c in df.columns]
    if not present:
        return pd.DataFrame(columns=cols)
    long = df.melt(
        id_vars="source_date",
        value_vars=present,
        var_name="classification",
        value_name="count",
    )
    long["count"] = pd.to_numeric(long["count"], errors="coerce").fillna(0)
    return long.groupby(["source_date", "classification"], as_index=False)["count"].sum()
```

- [ ] **Step 4: Run the full test file to verify all pass**

Run: `python -m pytest tests/unit/dashboard/test_trends.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/dashboard/data/trends.py tests/unit/dashboard/test_trends.py
git commit -m "feat(dashboard): soiling-ratio and wide-melt trend aggregations"
```

---

### Task 3: Trends UI page + numbered wrapper

**Files:**
- Create: `pv_pipeline/dashboard/pages/trends.py`
- Create: `pv_pipeline/dashboard/pages/5_Trends.py`

**Interfaces:**
- Consumes: `cached_findings_range`, `clear_dashboard_cache` (from `data.cache`); `require_auth` (from `auth`); `pick_date_range` (from `widgets.date_picker`); all five functions from `data.trends`.
- Produces: `main()` callable rendered by the numbered wrapper. Not imported by other code.

Note: page files are not unit-tested in this codebase (convention: `detectors.py`, `findings.py` have no page tests). Verification is a smoke import plus a manual checklist.

- [ ] **Step 1: Write the page module**

Create `pv_pipeline/dashboard/pages/trends.py`:

```python
"""M2 daily-trend dashboard page."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from pv_pipeline.dashboard.auth import require_auth
from pv_pipeline.dashboard.data.cache import cached_findings_range, clear_dashboard_cache
from pv_pipeline.dashboard.data.trends import (
    findings_counts_per_day,
    numeric_metric_per_day,
    soiling_ratio_per_day,
    status_counts_per_day,
    wide_counts_per_day,
)
from pv_pipeline.dashboard.widgets.date_picker import pick_date_range

# Detectors whose StringStatus sheet is long-format with a `status` column.
_STATUS_DETECTORS = [
    ("PeerZ", "M2b_peer_zscore_StringStatus"),
    ("OpenCircuit", "M2b_open_circuit_StringStatus"),
    ("GroundFault", "M2b_ground_fault_StringStatus"),
]
_LOW_IRR_COUNT_COLS = [
    "normal",
    "low_irradiance_underperform",
    "general_underperform",
    "skipped",
]


def _default_range() -> tuple[date, date]:
    end = date.today()
    return end - timedelta(days=29), end


def _sheet(sheets: dict[str, pd.DataFrame], name: str) -> pd.DataFrame:
    return sheets.get(name, pd.DataFrame())


def _render_findings_overview(st, alt, sheets: dict[str, pd.DataFrame]) -> None:
    st.subheader("Ringkasan semua detector (jumlah temuan/hari)")
    trend = findings_counts_per_day(_sheet(sheets, "Findings"))
    if trend.empty:
        st.info("Tidak ada sheet Findings untuk range ini.")
        return
    per_detector = trend.groupby(["source_date", "sub_module"], as_index=False)["count"].sum()
    chart = alt.Chart(per_detector).mark_line(point=True).encode(
        x=alt.X("source_date:T", title="Tanggal"),
        y=alt.Y("count:Q", title="Jumlah temuan"),
        color=alt.Color("sub_module:N", title="Detector"),
    )
    st.altair_chart(chart, use_container_width=True)


def _render_status(st, alt, sheets: dict[str, pd.DataFrame], label: str, sheet_name: str) -> None:
    st.subheader(label)
    trend = status_counts_per_day(_sheet(sheets, sheet_name))
    if trend.empty:
        st.info(f"Detector {label} tidak aktif untuk range ini.")
        return
    chart = alt.Chart(trend).mark_line(point=True).encode(
        x=alt.X("source_date:T", title="Tanggal"),
        y=alt.Y("count:Q", title="Jumlah string"),
        color=alt.Color("status:N"),
    )
    st.altair_chart(chart, use_container_width=True)


def _render_numeric(
    st, alt, sheets: dict[str, pd.DataFrame], label: str, sheet_name: str, value_col: str, y_title: str,
) -> None:
    st.subheader(label)
    trend = numeric_metric_per_day(_sheet(sheets, sheet_name), value_col)
    if trend.empty:
        st.info(f"Detector {label} tidak aktif untuk range ini.")
        return
    long = trend.melt(id_vars="source_date", var_name="metric", value_name="value")
    chart = alt.Chart(long).mark_line(point=True).encode(
        x=alt.X("source_date:T", title="Tanggal"),
        y=alt.Y("value:Q", title=y_title),
        color=alt.Color("metric:N"),
    )
    st.altair_chart(chart, use_container_width=True)


def _render_intermittent(st, alt, sheets: dict[str, pd.DataFrame]) -> None:
    st.subheader("Intermittent (LSTM-AE) — error rekonstruksi vs threshold")
    trend = numeric_metric_per_day(
        _sheet(sheets, "M2b_intermittent_WindowErrors"), "error_ratio", aggs=("mean", "max"),
    )
    if trend.empty:
        st.info("Detector Intermittent tidak aktif untuk range ini.")
        return
    long = trend.melt(id_vars="source_date", var_name="metric", value_name="error_ratio")
    lines = alt.Chart(long).mark_line(point=True).encode(
        x=alt.X("source_date:T", title="Tanggal"),
        y=alt.Y("error_ratio:Q", title="error_ratio (1.0 = threshold)"),
        color=alt.Color("metric:N"),
    )
    threshold = alt.Chart(pd.DataFrame({"y": [1.0]})).mark_rule(strokeDash=[4, 4]).encode(y="y:Q")
    st.altair_chart(lines + threshold, use_container_width=True)


def _render_soiling(st, alt, sheets: dict[str, pd.DataFrame]) -> None:
    st.subheader("Soiling ratio (dengan pita CI)")
    trend = soiling_ratio_per_day(_sheet(sheets, "M2a_soiling_EconomicAnalysis"))
    if trend.empty:
        st.info("Detector Soiling tidak aktif untuk range ini.")
        return
    base = alt.Chart(trend)
    band = base.mark_area(opacity=0.2).encode(
        x=alt.X("source_date:T", title="Tanggal"),
        y=alt.Y("sr_ci_lower:Q", title="Soiling ratio"),
        y2="sr_ci_upper:Q",
    )
    line = base.mark_line(point=True).encode(
        x="source_date:T",
        y=alt.Y("soiling_ratio:Q"),
    )
    st.altair_chart(band + line, use_container_width=True)


def _render_low_irradiance(st, alt, sheets: dict[str, pd.DataFrame]) -> None:
    st.subheader("Low irradiance — klasifikasi inverter/hari")
    trend = wide_counts_per_day(
        _sheet(sheets, "M2a_low_irradiance_LowIrradianceSummary"), _LOW_IRR_COUNT_COLS,
    )
    if trend.empty:
        st.info("Detector LowIrradiance tidak aktif untuk range ini.")
        return
    chart = alt.Chart(trend).mark_line(point=True).encode(
        x=alt.X("source_date:T", title="Tanggal"),
        y=alt.Y("count:Q", title="Jumlah inverter"),
        color=alt.Color("classification:N"),
    )
    st.altair_chart(chart, use_container_width=True)


def main() -> None:
    import altair as alt  # noqa: WPS433
    import streamlit as st  # noqa: WPS433

    st.set_page_config(page_title="M2 Trends", layout="wide")
    require_auth()
    st.title("M2 Daily Trends")
    st.caption(
        "Load pertama mengunduh 1 workbook per hari (bisa lambat). "
        "Pakai tombol Refresh data untuk memuat ulang.",
    )

    start_default, end_default = _default_range()
    start, end = pick_date_range(start_default, end_default)
    if st.button("Refresh data"):
        clear_dashboard_cache()
        st.rerun()

    result = cached_findings_range(start, end)
    for err in result.errors:
        st.error(err)
    if result.missing_dates:
        st.caption(
            f"{len(result.missing_dates)} hari tanpa artifact: "
            + ", ".join(str(d) for d in result.missing_dates),
        )
    if not result.sheets:
        st.info("Tidak ada workbook sheets untuk date range ini.")
        return

    sheets = result.sheets
    _render_findings_overview(st, alt, sheets)
    _render_soiling(st, alt, sheets)
    _render_intermittent(st, alt, sheets)
    _render_numeric(
        st, alt, sheets, "MPPT ratio (median daylight)",
        "M2b_mppt_ratio_StringStatus", "ratio_median_daylight", "Ratio",
    )
    _render_numeric(
        st, alt, sheets, "IForest (flagged %)",
        "M2_iforest_AnomalySummary", "flagged_pct", "Flagged %",
    )
    for label, sheet_name in _STATUS_DETECTORS:
        _render_status(st, alt, sheets, label, sheet_name)
    _render_numeric(
        st, alt, sheets, "Shading (jumlah jam mencurigakan)",
        "M2a_shading_ShadingSummary", "n_suspicious", "n_suspicious",
    )
    _render_low_irradiance(st, alt, sheets)
```

- [ ] **Step 2: Write the numbered wrapper**

Create `pv_pipeline/dashboard/pages/5_Trends.py`:

```python
from __future__ import annotations

from pv_pipeline.dashboard.pages.trends import main

main()
```

- [ ] **Step 3: Smoke-test the import (no Streamlit runtime needed)**

Run:
```bash
python -c "import pv_pipeline.dashboard.pages.trends as t; print([n for n in dir(t) if not n.startswith('__')])"
```
Expected: prints a list containing `main`, `findings_counts_per_day`, `numeric_metric_per_day`, `soiling_ratio_per_day`, `status_counts_per_day`, `wide_counts_per_day` (no import error).

- [ ] **Step 4: Run the full dashboard test suite to confirm no regressions**

Run: `python -m pytest tests/unit/dashboard/ -v`
Expected: PASS (all dashboard tests, including the 13 trends tests).

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/dashboard/pages/trends.py pv_pipeline/dashboard/pages/5_Trends.py
git commit -m "feat(dashboard): M2 Trends page with per-detector daily charts"
```

---

### Task 4: Add Intermittent tab to the deep-dive page

**Files:**
- Modify: `pv_pipeline/dashboard/pages/detectors.py:17` (inside the `DETECTOR_SHEETS` dict)

**Interfaces:**
- Consumes: existing `render_detector_tab` (unchanged).
- Produces: nothing new; extends the tab list.

- [ ] **Step 1: Add the Intermittent entry**

In `pv_pipeline/dashboard/pages/detectors.py`, inside the `DETECTOR_SHEETS` dict, add the `Intermittent` entry immediately after the `MpptRatio` line so LSTM-AE gets a deep-dive tab. The edited region reads:

```python
    "MpptRatio": ["M2b_mppt_ratio_StringStatus"],
    "Intermittent": ["M2b_intermittent_WindowErrors"],
    "GroundFault": ["M2b_ground_fault_StringStatus"],
```

- [ ] **Step 2: Smoke-test the import**

Run:
```bash
python -c "from pv_pipeline.dashboard.pages.detectors import DETECTOR_SHEETS; assert DETECTOR_SHEETS['Intermittent'] == ['M2b_intermittent_WindowErrors']; print('ok', list(DETECTOR_SHEETS))"
```
Expected: prints `ok` followed by the detector list including `Intermittent`.

- [ ] **Step 3: Run the full dashboard test suite**

Run: `python -m pytest tests/unit/dashboard/ -v`
Expected: PASS (no regressions).

- [ ] **Step 4: Commit**

```bash
git add pv_pipeline/dashboard/pages/detectors.py
git commit -m "feat(dashboard): add Intermittent (LSTM-AE) tab to detector deep-dive"
```

---

## Self-Review Notes

- **Spec coverage:** Every detector row in the spec's sheet table maps to a task: Findings overview + Soiling + Intermittent + MpptRatio + IForest + PeerZ/OpenCircuit/GroundFault + Shading + LowIrradiance are all rendered in Task 3; the `WindowErrors` group-by-`source_date` intent is a Task 1 test; the `detectors.py` +1 line is Task 4. The two special data shapes (wide LowIrradianceSummary via `wide_counts_per_day`; WindowErrors keyed on `source_date`) are covered by Task 2 and Task 1 tests respectively.
- **Availability** has no dedicated curated metric — per the spec it is covered by the Findings overview (its `sub_module` appears there). No separate task needed.
- **Placeholder scan:** no TBD/TODO; all code steps show full code.
- **Type consistency:** function names and return columns referenced in Task 3 (`findings_counts_per_day`, `status_counts_per_day`, `numeric_metric_per_day`, `soiling_ratio_per_day`, `wide_counts_per_day`) match their definitions in Tasks 1–2 exactly.
