# Transpose All-String Yield Recap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change `Rekap_Yield_kWh` so PV strings are rows and requested dates are columns, without changing yield calculations or the other workbook sheets.

**Architecture:** Build the recap directly from `Detail_Harian` with `pv_string` as the pivot index and dates as columns. Keep natural string ordering and inclusive date ordering, then update workbook formatting and verification to enforce that exact matrix.

**Tech Stack:** Python, pandas, openpyxl, pytest, generated Jupyter notebook JSON.

## Global Constraints

- Keep `Detail_Harian` and `Metadata` unchanged.
- Preserve zero values and keep unavailable yield cells blank.
- Keep sheet order `Rekap_Yield_kWh`, `Detail_Harian`, `Metadata`.
- Do not modify the executed notebook or workbook under `coba/`.

---

### Task 1: Lock the transposed recap contract

**Files:**
- Modify: `tests/unit/test_all_string_yield_report.py`

- [x] Change summary assertions to expect `pv_string` followed by ordered date columns.
- [x] Change workbook assertions to expect strings down column A and dates across row 1.
- [x] Change tamper tests so duplicate/invalid recap strings and dates are rejected.
- [x] Run focused tests and confirm they fail against the old orientation.

### Task 2: Transpose the production recap

**Files:**
- Modify: `pv_pipeline/all_string_yield_report.py`

- [x] Pivot with `index="pv_string"` and `columns="date"`.
- [x] Reindex rows by natural string order and columns by requested date order.
- [x] Update Excel row/column preflight for the transposed recap.
- [x] Format column A as identifiers, row 1 date headers as `yyyy-mm-dd`, and body yields as `0.000`.
- [x] Update verification to enforce exact row strings and exact date headers.
- [x] Run focused tests and confirm they pass.

### Task 3: Regenerate and verify the notebook

**Files:**
- Modify: `output_string/All_String_Daily_Yield.ipynb`
- Verify: `output_string/_build_all_string_yield_notebook.py`
- Verify: `output_string/_smoke_all_string_yield_notebook.py`

- [x] Regenerate the committed notebook from its builder.
- [x] Update smoke expectations if required by the new recap dimensions.
- [x] Run all-string and single-string report tests.
- [x] Run the notebook smoke test and `git diff --check`.
- [x] Confirm unrelated local files remain unstaged and unchanged.
