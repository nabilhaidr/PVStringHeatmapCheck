# PV String Underperform Dashboard M0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Streamlit M0 page that surfaces PV string underperformance from existing `pv_string` findings and, when available, shows Cell-3-compatible baseline CSV power/current context.

**Architecture:** Keep detector behavior unchanged. Add pure pandas helpers under `pv_pipeline.dashboard.data.underperform`, then add a thin Streamlit page under `pv_pipeline.dashboard.pages.underperform` and a root `pages/5_Underperform.py` shim.

**Tech Stack:** Python, pandas, Streamlit, Altair, pytest.

---

### Task 1: Pure Underperform Transforms

**Files:**
- Create: `pv_pipeline/dashboard/data/underperform.py`
- Create: `tests/unit/dashboard/test_underperform.py`

- [ ] **Step 1: Write failing tests**

Cover findings grouping, severity ordering, Cell 3 normalization, optional current column, and all-NaN selected string handling.

- [ ] **Step 2: Run tests to verify RED**

Run: `pytest tests/unit/dashboard/test_underperform.py -q`

Expected: fails because `pv_pipeline.dashboard.data.underperform` does not exist.

- [ ] **Step 3: Implement minimal pure helpers**

Implement:
- `summarize_pv_string_findings(findings: pd.DataFrame) -> pd.DataFrame`
- `build_string_timeseries(df: pd.DataFrame, inverter_id: str, pv_string: str, empty_pv_map: dict | None = None, pv_max_allowed: int = 28) -> tuple[pd.DataFrame, str]`
- `analyze_inverter_strings(df: pd.DataFrame, inverter_id: str, empty_pv_map: dict | None = None, pv_max_allowed: int = 28) -> pd.DataFrame`

- [ ] **Step 4: Run transform tests to verify GREEN**

Run: `pytest tests/unit/dashboard/test_underperform.py -q`

Expected: pass.

### Task 2: Streamlit Page

**Files:**
- Create: `pv_pipeline/dashboard/pages/underperform.py`
- Create: `pages/5_Underperform.py`
- Modify: `tests/integration/dashboard/test_pages_smoke.py`

- [ ] **Step 1: Add page import smoke test**

Extend dashboard module import parametrization with `pv_pipeline.dashboard.pages.underperform`.

- [ ] **Step 2: Run smoke test to verify RED**

Run: `pytest tests/integration/dashboard/test_pages_smoke.py -q`

Expected: fails because page module does not exist.

- [ ] **Step 3: Implement page and shim**

Page should:
- require auth;
- load findings range;
- summarize only populated `pv_string` rows;
- allow date range, severity, detector, WB, inverter filters;
- support selecting one summarized row;
- load baseline CSV for the selected date;
- render optional power/current/sibling-median/normalized time-series;
- degrade explicitly when baseline CSV, current column, or selected PV data is missing.

- [ ] **Step 4: Run dashboard tests**

Run: `pytest tests/unit/dashboard/test_underperform.py tests/integration/dashboard/test_pages_smoke.py -q`

Expected: pass.

### Task 3: Verification

**Files:**
- Existing dashboard files and new page files.

- [ ] **Step 1: Run targeted dashboard suite**

Run: `pytest tests/unit/dashboard tests/integration/dashboard -q`

Expected: dashboard tests pass.

- [ ] **Step 2: Check git diff**

Run: `git diff --stat` and `git status --short --branch`.

Expected: only planned files plus pre-existing unrelated local changes.

