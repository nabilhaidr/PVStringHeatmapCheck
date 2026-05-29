# PV String Underperform Dashboard M0 Extension

**Date:** 2026-05-29
**Status:** Draft for user review
**Scope owner:** Fase 4 Task 3 Streamlit Dashboard M0 extension

## 1. Scope Decision

This extension is intentionally narrow.

In scope:
- Add a dashboard view for **PV String Underperform**.
- Use existing `Findings` rows where `pv_string` is populated as the primary signal.
- Use baseline `YYYY-MM-DD.csv` as an optional per-day time-series context source when available.
- Align the baseline analysis with notebook Cell 3 heatmap semantics.

Out of scope for this M0 extension:
- No new Availability section. `M2e_hybrid_AllStrings` is already available in Detectors and Overview already has fleet uptime.
- No automatic cleaning or soiling alarm yet. `M2a_soiling` stays gated until enough baseline exists for SRR, ideally 90 to 180 days plus precipitation/cleaning evidence.
- No new production detector that writes new M2 findings. The dashboard may compute display-only diagnostics from baseline CSV, but those are not persisted as detector findings.

## 2. Current Behavior Confirmed

Notebook Cell 3 does:

1. `prepare_df_work(combined_df, pv_max_allowed=PV_MAX_ALLOWED)`.
2. Build `PVn Power(kW)` from the pipeline-prepared data.
3. Replace zero PV power with `NaN`.
4. Render `plot_all_inverters(...)`.

`plot_single_inv_heatmap()` then:

1. Builds a PV string by timestamp pivot for one inverter.
2. Masks `EMPTY_PV_MAP` PV slots as `NaN`.
3. Normalizes each timestamp column across sibling PV strings:

   ```text
   normalized = (pv_power - min_positive_power_at_timestamp)
                / (max_positive_power_at_timestamp - min_positive_power_at_timestamp)
   ```

4. Renders red/yellow/green as **peer-relative power at that timestamp**, not absolute power.

Dashboard underperform time-series must preserve this interpretation. A red/low score means "weakest among siblings at the same timestamp", not necessarily "bad because irradiance is low".

## 3. Data Sources

Primary source:
- `m2_findings_YYYYMMDD.xlsx` sheet `Findings`, or JSONL fallback.
- Use rows with non-empty `pv_string`.
- Keep detector, severity, fault type, confidence, timestamp, value, threshold, evidence, `source_date`, `inverter_id`, and `wb_id`.

Optional context source:
- Baseline `YYYY-MM-DD.csv`, loaded through the existing dashboard baseline loader.
- Baseline CSV preserves the filtered daily `combined_df` rows. It can include `PVn Power(kW)`, `PVn input current(A)`, and `PVn input voltage(V)` columns depending on the upstream daily snapshot.
- Because `BaselineAccumulator` may set HIGH/CRITICAL per-PV strings to `NaN`, baseline CSV is a **context source**, not the authoritative fault source. If a string was auto-skipped, the dashboard should show that the time-series is unavailable or partly `NaN` rather than pretending the string is normal.

## 4. Underperform View

Add one new Streamlit page:

```text
pages/5_Underperform.py
pv_pipeline/dashboard/pages/underperform.py
```

Page title:

```text
PV String Underperform
```

Top controls:
- Date range picker, same as Findings Browser.
- Severity filter, default `CRITICAL`, `HIGH`, `MEDIUM`.
- Detector filter, default `All`.
- WB and inverter filters.
- Optional toggle: `Show baseline snapshot if available`, default on.

Main sections:

1. **PV String Findings Summary**
   - Table grouped by `source_date`, `wb_id`, `inverter_id`, `pv_string`, `sub_module`.
   - Columns: finding count, worst severity, latest timestamp, fault types, max confidence.
   - Sort by worst severity then latest timestamp.

2. **Selected String Detail**
   - Streamlit single-row selection from the summary table.
   - Show the raw findings for that selected inverter/string/date.
   - Show `evidence` JSON for the selected row when present.

3. **Baseline Time-Series Context**
   - If baseline CSV exists for the selected date and contains the selected inverter/string:
     - show `PVn Power(kW)` time-series;
     - show `PVn input current(A)` time-series if that column exists;
     - show sibling median power for the same timestamp;
     - show Cell-3-compatible normalized score for the selected string.
   - If current or voltage columns are absent, the page must degrade to power-only.
   - If baseline CSV is missing, show an explicit info state.
   - If the selected PV columns are all `NaN`, show an explicit note that baseline auto-skip may have removed the affected string from normal-data accumulation.

## 5. Display-Only Baseline Analysis

The dashboard may compute a display-only diagnostic table from baseline CSV:

Per selected inverter/day:
- `pv_string`
- `median_power_kw`
- `median_sibling_power_kw`
- `median_power_ratio_to_sibling`
- `p10_power_ratio_to_sibling`
- `low_norm_pct`
- `n_samples`
- `n_current_samples`
- `median_current_a` when current column exists

Definitions:

```text
sibling_median_t = median(PV power across non-empty sibling strings at t)
power_ratio_t = selected_pv_power_t / sibling_median_t
cell3_norm_t = (selected_pv_power_t - min_positive_power_t)
               / (max_positive_power_t - min_positive_power_t)
low_norm_pct = share of valid timestamps where cell3_norm_t <= 0.25
```

This table is for operator triage only. It must not create new M2 findings in M0 because baseline CSV contains filtered NORMAL data and can be incomplete for strings already excluded by `BaselineAccumulator`.

## 6. Tests

Test-first implementation should cover:

- Findings grouping includes only populated `pv_string` rows.
- Worst severity ordering is stable: `CRITICAL > HIGH > MEDIUM > INFO > NORMAL`.
- Baseline underperform analysis matches Cell 3 normalization on a small synthetic inverter/day.
- Current snapshot is optional: power-only baseline CSV still works.
- All-NaN selected PV power returns a clear unavailable state instead of zeros.
- Dashboard page module imports without Google Drive calls.

Target tests:

```text
tests/unit/dashboard/test_underperform.py
tests/integration/dashboard/test_pages_smoke.py
```

## 7. Implementation Notes

Keep the implementation surgical:

- Put pure pandas transforms in a small testable helper module, not inside the Streamlit page body.
- Reuse `normalize_findings_df()`, `cached_findings_range()`, and `cached_baseline_csv_day()`.
- Do not change existing detector thresholds.
- Do not alter `BaselineAccumulator`.
- Do not change Cell 3 notebook behavior.

## 8. Known Risks

- Baseline CSV is not raw data. It is filtered daily normal-data accumulation, so severe strings may be missing or `NaN`.
- If only JSONL findings are available, detector artifact sheets are absent, but the underperform summary still works from `Findings`.
- Current-column names vary by case. The helper should support the existing pipeline column style such as `PV3 input current(A)` and tolerate missing current columns.
- The existing heatmap is peer-relative. Any wording must avoid implying absolute irradiance-normalized performance unless POA and capacity are explicitly added later.

