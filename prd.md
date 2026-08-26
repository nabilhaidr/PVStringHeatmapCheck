# Product Requirements Document (PRD)

## PV Module Performance Analytics Notebook and Pipeline

Status: Draft v0.4  
Date: 2026-08-13 (v0.3: 2026-08-06, v0.2: 2026-07-10, v0.1: 2026-05-27)  
Repository: `SolarYieldPro-main/kodingan pv string`  
Primary runtime: Google Colab  
Primary storage: Google Drive  

---

## 1. Overview

This product is a PV module performance analytics system for PLTS monitoring. The current implementation is centered on a Google Colab notebook, with reusable Python modules in the main repository. The system ingests Huawei inverter/string monitoring data from Google Drive, calculates PV string power, visualizes string heatmaps, runs M2 detector modules, and produces findings/artifacts for engineering review.

The product objective is to convert raw inverter, PV string, irradiance, temperature, generation, and configuration data into actionable engineering diagnostics:

- Which inverter or PV string is unavailable?
- Which PV string behaves abnormally against its peers?
- Which events indicate open circuit, ground fault, shading, low irradiance underperformance, or soiling risk?
- Which low performance days are actual faults versus curtailment?
- Which data should be excluded from healthy baseline training?

The notebook remains the primary analyst-facing workflow. The repository provides the modular engine, configuration, detectors, physics helpers, loaders, tests, and future dashboard foundation.

---

## 2. Product Goals

1. Provide a repeatable Google Colab workflow for daily or multi-day PV string performance analysis.
2. Generate engineering-grade PV string heatmaps and detector findings from raw monitoring files.
3. Standardize detector logic through reusable Python modules under `pv_pipeline`.
4. Support physics-based normalization using POA, cell temperature, panel specifications, and generation data.
5. Produce audit-friendly outputs: CSV, JSONL, Excel workbook artifacts, and baseline datasets.
6. Support future evolution into a dashboard and model-driven anomaly detection platform.

---

## 3. Users and Use Cases

| User | Primary Need |
|---|---|
| PV Performance Engineer | Diagnose underperforming string/inverter and validate detector findings. |
| O&M Engineer | Identify open circuit, ground fault, availability loss, and maintenance priority. |
| Data Analyst | Re-run notebook, export findings, inspect heatmap and PR trend. |
| Asset Manager | Understand energy loss, curtailment impact, soiling risk, and cleaning economics. |
| ML/Analytics Engineer | Build healthy baseline and train advanced anomaly models. |

---

## 4. In Scope

- Google Colab notebook execution.
- Google Drive input/output workflow.
- Raw inverter Excel ingestion.
- Inverter ID normalization and PV string power calculation.
- PV string heatmap visualization.
- M2 detector pipeline:
  - M2e Availability
  - M2b Peer Z-score / High-R
  - M2b Open Circuit
  - M2b Ground Fault
  - M2 Isolation Forest
  - M2a Shading
  - M2a Low Irradiance
  - M2a Soiling skeleton
- POA, Tcell, albedo, panel specification, and generation integration.
- Daily PR and curtailment cross-check.
- Baseline accumulator for healthy data.
- Export of findings and analysis artifacts.

---

## 5. Out of Scope for Current Notebook Version

- Full production web dashboard operation.
- User authentication and role-based access.
- Real-time streaming ingestion.
- Automatic notification dispatch.
- Work order management.
- Fully trained LSTM Autoencoder production inference.
- Full I-V curve tracing and diode parameter extraction.
- Complete bifacial rear-side irradiance model.
- Final bankable financial model.

These can be implemented as later phases.

---

## 6. System Architecture

```text
Google Drive
  |-- raw Huawei inverter Excel files
  |-- generation Excel
  |-- pyranometer, weather, albedo, Tcell files
  v
Google Colab notebook
  |-- download files via gdown / Drive integration
  |-- load and normalize data
  |-- calculate PV string power
  |-- run visualization and M2 detectors
  |-- export CSV / JSONL / XLSX artifacts
  v
pv_pipeline modules
  |-- loaders
  |-- transformations
  |-- physics
  |-- POA / Tcell / weather providers
  |-- detector modules
  |-- baseline accumulator
  v
Outputs
  |-- string heatmaps
  |-- PR charts
  |-- M2 findings
  |-- detector artifact sheets
  |-- baseline CSV/parquet
```

---

## 7. End-to-End Workflow

1. Analyst opens notebook in Google Colab.
2. Notebook downloads or mounts source files from Google Drive.
3. Raw Excel files are loaded using configured header and file list.
4. `ManageObject` is transformed into standardized `Inverter_ID`.
5. PV string power is calculated from voltage and current:

   ```text
   PVn Power(kW) = PVn input voltage(V) * PVn input current(A) / 1000
   ```

6. `df_plot` is prepared for heatmap and dashboard-style output.
7. Heatmap is rendered per inverter and PV string.
8. M2 engine runs enabled detectors using configuration from `config/m2_config.yaml`.
9. POA, Tcell, panel spec, and generation data are used for physics-based calculations.
10. Daily PR and curtailment cross-check are generated.
11. Baseline accumulator excludes poor-quality or faulted data from healthy baseline.
12. Notebook exports analysis artifacts for review and future dashboard ingestion.

---

## 8. Feature Requirements

### 8.1 Raw Data Loading

Requirement:
- Load one or more Huawei inverter Excel files from Google Drive or local Colab workspace.
- Preserve source file traceability.
- Support configured header row and expected file names.

Core functions/modules:
- `pv_pipeline.data_loader.load_and_prepare_data`
- Notebook download cell using `gdown.download_folder`

Acceptance criteria:
- Raw rows from all expected files are concatenated.
- Missing expected files are surfaced clearly.
- Loaded data contains `ManageObject`, `Start Time`, PV voltage/current columns, and inverter status columns when available.

Two source systems, two networks:

The site exports from two separate Fusion Solar instances, and they do not share
a transport. Phase One (WB01/WB02) arrives as `1-2.xlsx` over an IconPlus fibre
internet link; WB03-WB10 arrives as `3-10.xlsx` over local ethernet. When the
fibre link is disrupted — a cut cable, or a fault in IconPlus equipment — the
Phase One block is absent from that day's Huawei export entirely. Local ethernet
rarely fails, so WB03-WB10 is almost always present.

A day with zero Phase One inverters is therefore an external telecoms outage,
not an old export format and not a plant outage. This is observable: in the
Nov-Dec 2025 export `2025-11-03.csv` carries 0 Phase One inverters while
`2025-11-13.csv` carries all 50. The consequence for analysis is that Phase One
strings systematically receive fewer usable days than WB03-WB10 (about 40 of 48
in the Nov-Dec 2025 diagnostic run) with nothing wrong at the plant. See 8.4 for
what this must not be counted as, and `pv_pipeline/drive_probe.py::probe_inverter_coverage`
for the per-file count that distinguishes the two cases.

### 8.2 Inverter and PV String Transformation

Requirement:
- Convert raw `ManageObject` naming into stable IDs such as `WB02-INV14`.
- Calculate PV string power for PV1 through PV28.
- Calculate total inverter PV power.

Core functions/modules:
- `pv_pipeline.transformations`

Acceptance criteria:
- Every valid inverter row has `Inverter_ID`.
- PV power columns follow `PVn Power(kW)`.
- `Total_PV_power_kW` equals the sum of available PV power columns.

### 8.3 PV String Heatmap

Requirement:
- Render per-inverter PV string heatmap across time.
- Mark configured empty PV channels.
- Normalize power per timestamp against sibling strings.

Core functions/modules:
- `pv_pipeline.viz`
- `config/strings.yaml`

Acceptance criteria:
- Heatmap rows represent PV1 through PV28.
- Columns represent timestamp.
- Empty PV strings are visually distinct.
- Relative string underperformance is visible.

### 8.4 M2e Availability

Requirement:
- Classify inverter status into ON, DOWN, TRANSITIONAL, UNKNOWN.
- Calculate inverter uptime.
- Detect string-level proxy downtime during qualified operating periods.

Core functions/modules:
- `pv_pipeline.availability.M2eAvailability`

Key calculations:

```text
Inverter uptime(%) = 100 * N_ON / (N_ON + N_DOWN)
String proxy down = P_string < 0.1 kW
                  AND sibling_median >= 1.0 kW
                  AND active_siblings >= 50%
                  AND duration >= debounce_steps
```

Acceptance criteria:
- Inverter-level findings are emitted when uptime crosses severity thresholds.
- Empty PV channels are excluded.
- String proxy events are debounced to reduce false positives.
- Absent telemetry is not downtime. A day on which Phase One (WB01/WB02) is
  missing from the export is an IconPlus fibre outage, an external factor on the
  monitoring path, not a stopped plant (8.1). Counting those days as DOWN makes
  Phase One availability look poor while the inverters were exporting energy
  normally, and the resulting findings would send crews to healthy equipment.

  The discriminator is the count, not the presence: **all 50** Phase One
  inverters vanishing together is the link; a subset vanishing is the inverters.
  WB03-WB10 riding a different transport gives the control — if WB03-WB10 is
  present on the same timestamps, the plant was up.

  Implemented in `pv_pipeline.availability`. Absence was never scored as DOWN
  here — an inverter with no rows forms no group, and a blank status maps to
  UNKNOWN, which stays out of the uptime denominator. The defect was silence,
  not arithmetic: without a marker, a cross-date uptime average is computed over
  a different number of days per plant and nothing tells the reader. So the rule
  is enforced as a report, not as a filter on the uptime figure.

  `detect_link_outage` runs automatically inside `M2eAvailability.run()` and
  emits a single `M2e_link` finding at INFO severity — INFO because a fibre
  outage is external to the plant, and a higher severity would dispatch crews to
  healthy equipment. Verdicts are `LINK_OUTAGE` (whole group absent, control
  present), `NO_DATA` (all groups absent, no control to attribute against), and
  `INVERTER_ABSENCE` (a subset absent — a real finding, follow it up). A complete
  day emits nothing, so the marker stays rare enough to keep being read. The
  roster comes from `empty_pv_map`; without it the check is skipped with a
  warning rather than guessing which inverters should exist.

  **A third failure class, found 2026-08-15 and not yet discriminated.** The
  rule splits absence into "all of a group" (the link) and "a subset" (the
  inverters). A subset can also mean neither: an **incomplete copy of the
  export**. On 2025-12-01 the baseline file carries 49 of 50 Phase One
  inverters and `detect_link_outage` returns `INVERTER_ABSENCE` for
  `WB02-INV15` — the verdict that sends a crew out. The same date's export file
  carries all 50, and `WB02-INV15` has its full 155 rows there, running
  normally at a median 56.7 kW. The two files differ by exactly 155 rows, one
  inverter's worth, while every other inverter has exactly 155 in both.

  So the inverter was fine and the copy was short. From a single file the two
  are indistinguishable, and the error direction is the expensive one: a
  transfer artefact dressed as equipment failure.

  **Implemented 2026-08-16 as `detect_incomplete_copy`**, which compares two or
  more copies of the same date and reports any inverter missing from some but
  present in others as `INCOMPLETE_COPY`. It refuses to run on a single copy
  rather than return an empty list, because "nothing found" would read as safe
  when the truth is "not checked".

  An earlier draft of this section proposed a within-file discriminator — that
  a whole-inverter shortfall shows up as an exact multiple of the per-inverter
  row count. **That is wrong and was removed.** It held only by coincidence on
  2025-12-01, where every inverter happened to carry 155 or 156 rows. On
  2026-07-01 the same export carries 140, 145, 152, 153, 154 and 224 rows for
  different inverters, so there is no single figure to be a multiple of. No
  sound single-file signal has been found; comparison against another copy is
  the only check that actually caught this.

  Both observed pairs are covered by the detector: it returns `WB02-INV15` on
  2025-12-01 and nothing at all on 2026-07-01, where the two copies match at
  29,741 rows and 194 inverters.

  Known limit: the check compares the roster against the inverter IDs present in
  whatever frame it is handed. The daily pipeline passes one date per run, so
  this is per-date in production — but a single-date outage inside a multi-date
  frame would be masked by the other dates. `probe_inverter_coverage` in
  `pv_pipeline/drive_probe.py` remains the per-file cross-check.

Related tooling:
- `rekap_m2e_allstrings.py` merges the `M2e_hybrid_AllStrings` sheet across daily `m2_findings_{YYYYMMDD}.xlsx` outputs into one Excel: uptime pivot per date, per-string summary (worst days, days below threshold, total downtime), and the combined long table (CSV fallback beyond the Excel row limit).
- Daily M2e results are also consumed by M2a Soiling as an inverter-outage mask (see 8.11).

### 8.5 M2b Peer Z-score / High-R

Requirement:
- Compare each PV string resistance proxy against peer strings in the same inverter.
- Gate analysis by daylight/POA and valid solar elevation.

Core functions/modules:
- `pv_pipeline.peer_zscore`
- `pv_pipeline.voc_estimator`
- `pv_pipeline.panel_spec`

Key calculations:

```text
R_string = V_string / max(I_string, 0.1)
z = (median(R_string) - peer_median) / peer_std
Voc_ratio = Voc_actual / Voc_nominal
```

Acceptance criteria:
- Findings are emitted when `abs(z) > threshold` and `Voc_ratio` passes configured rule.
- Detector produces per-string status artifacts.

### 8.6 M2b Open Circuit

Requirement:
- Detect PV strings with near-zero current while peer strings are active under high POA.

Core functions/modules:
- `pv_pipeline.open_circuit`

Key calculation:

```text
I_ratio = I_string / max(I_q95_peer, 0.01)
Open circuit candidate = I_ratio < 0.05
```

Acceptance criteria:
- Events require daylight gate and debounce.
- Severity is critical for confirmed open circuit events.

### 8.7 M2b Ground Fault

Requirement:
- Detect inverter/string ground fault signatures using voltage-to-ground, adaptive fleet comparison, and electrical specification signals.

Core functions/modules:
- `pv_pipeline.ground_fault`

Key signals:

```text
Absolute trigger = max(abs(V_to_ground)) > 50 V
Adaptive trigger = abs(Vg_median - fleet_median) / fleet_std > 3
Spec trigger = Voc_ratio < 0.85 AND I_z > 2
```

Acceptance criteria:
- Findings include which signal triggered the fault.
- Confidence increases when multiple signals agree.

### 8.8 Isolation Forest Anomaly Detection

Requirement:
- Detect unsupervised anomalies using voltage, current, peer deviation, and resistance features.

Core functions/modules:
- `pv_pipeline.iforest`

Feature vector:

```text
[V, I, V_dev, I_dev, R]
```

Acceptance criteria:
- Detector produces anomaly scores and summaries.
- Findings can be excluded from main Findings sheet when configured, because this detector can be noisy.

### 8.9 M2a Shading

Requirement:
- Detect likely shading behavior from hourly cross-string variation and PR proxy.

Core functions/modules:
- `pv_pipeline.m2a.shading`

Key calculations:

```text
CV_hour = std(PV string powers) / mean(PV string powers)
PR_proxy_hour = mean(P_inverter) / mean(POA)
Suspicious hour = CV_hour < 0.5 * median(CV)
               AND PR_proxy_hour < 0.85 * median(PR_proxy)
```

Acceptance criteria:
- Detector classifies morning, afternoon, or uniform shading.
- Detector outputs hourly artifact tables.

### 8.10 M2a Low Irradiance

Requirement:
- Identify underperformance that appears specifically in low irradiance conditions.

Core functions/modules:
- `pv_pipeline.m2a.low_irradiance`

Key calculation:

```text
PR_proxy = P_inverter / POA
Fit PR_proxy = intercept + slope * POA
```

Acceptance criteria:
- Low and mid irradiance bands are analyzed separately.
- Detector distinguishes low-irradiance underperformance from general underperformance.

### 8.11 M2a Soiling

Requirement:
- Estimate soiling loss and cleaning economics from accumulated daily baseline data using rdtools SRR, at site, WB-group, per-WB, and (opt-in) per-inverter scope.
- Correct daily PR for module temperature before SRR (insolation-weighted).
- Mask inverter outage using M2e availability results (uptime below threshold removes that inverter-day's energy and its capacity from the PR denominator) so partial outage is not read as soiling.
- Join manual cleaning reports and rainfall to classify SRR cleaning events (manual vs rain).
- Quantify recovery from real cleaning campaigns via pre/post PR (plant-level and per-string), independent of SRR interval validity.
- Break down soiling loss monthly and rank per-string cleaning priority.

Core functions/modules:
- `pv_pipeline.m2a.soiling`, `pv_pipeline.m2a.cleaning_report`
- Runner: `run_soiling_analysis.py`; analyst notebook `notebook/M2aSoiling.ipynb` (Cell 7 replots the sawtooth trend from the output workbook alone).
- Optional dependency: `rdtools`

Key calculations:

```text
PR_daily = E_daily / (H_POA_daily * Capacity_kWp * TempFactor * CapacityFactor)
TempFactor      = 1 + gamma_Pmax/100 * (Tcell - 25 C)   (insolation-weighted per day)
CapacityFactor  = available capacity fraction from M2e uptime mask
Soiling loss    = 1 - soiling_ratio (SRR Monte Carlo, with confidence interval)
Monthly loss    = insolation-weighted (1 - SR_p50) per calendar month
Direct impact   = (PR_after - PR_before) / PR_after around cleaning campaigns
Recommendation  = string deficit vs sibling median PR + inverter p_loss (score, rank)
Daily value loss = avg_daily_energy_kWh * tariff_IDR_per_kWh * soiling_loss
Payback days    = cleaning_cost_IDR / daily_value_loss
```

Output sheets (`soiling_srr_*.xlsx`):
- `EconomicAnalysis`, `MonthlySoilingLoss`, `PRDaily`, `SoilingRatio` (SR p50 + CI), `CleaningEvents`, `CleaningImpact`, `DirectCleaningImpact`, `DirectCleaningImpactPerString`, `PerInverterSRR` (opt-in), `CleaningRecommendation`, `ManualCleaning`, `AvailabilityMask`.

Column naming (changed 2026-07-29 — workbooks generated before this date carry the old names):
- `DirectCleaningImpactPerString`: `uplift_pct` → `soiling_loss_pct`, `rank_uplift` → `rank_soiling_loss`. The formula is unchanged, `(after - before) / after`. The old name implied the `(after - before) / before` convention used by `pv_pipeline.yf_ratio_report`, and contradicted `soiling_loss_pct` in the sibling sheet `DirectCleaningImpact`, which computes exactly the same quantity.
- `CleaningRecommendation`: `hist_uplift_pct` → `hist_soiling_loss_pct` (mean of the renamed column).
- `CleaningImpact`: `uplift_pct` → `sr_gain_pp`. This is `sr_after - sr_before`, a difference of two ratios in percentage points — a different unit from `soiling_loss_pct`, so sharing the name `uplift_pct` invited direct comparison of incomparable numbers.
- Old workbooks are deliberately not migrated. Renaming rather than redefining means a stale consumer fails loudly with `KeyError` instead of silently reading a number that means something else.

Acceptance criteria:
- If days of data are below minimum, emit insufficient-data output (`PRDaily` is still exported).
- If enough data exists, produce soiling ratio with confidence interval, monthly loss breakdown, classified cleaning events and recovery, cleaning economics, and per-string cleaning priority.
- Sawtooth trend (daily PR points + SRR interval fits + SR confidence band) can be replotted from the workbook without re-running SRR or rdtools.
- `CleaningRecommendation` is directly usable as a work order: strings whose recent PR falls below `dead_ratio` (default 0.10) of their inverter median are flagged `status = DEAD_OR_OFFLINE` and excluded from `rank` (rank is NA), because a ~100% deficit is an availability fault owned by M2e, not soiling. Threshold and status vocabulary match `pv_pipeline.yf_ratio_report`.

### 8.12 PR and Curtailment Cross-Check

Requirement:
- Calculate daily site and WB-level PR.
- Cross-check low PR days against curtailment flag and deemed dispatch data.

Core functions/modules:
- `pv_pipeline.generation`
- `pv_pipeline.physics`

Key calculation:

```text
PR = E_actual_kWh / (POA_kWh_m2 * Capacity_kWp)
Low PR = PR < 0.65
```

Acceptance criteria:
- Low PR with curtailment is labeled as operational curtailment.
- Low PR without curtailment is surfaced as potential performance issue.

### 8.13 Baseline Accumulator

Requirement:
- Build healthy baseline dataset by excluding faulted inverter/string/time windows.

Core functions/modules:
- `pv_pipeline.baseline`

Acceptance criteria:
- Critical/high findings are excluded from baseline.
- PV-string-level findings only exclude affected PV string columns.
- Inverter-level findings can exclude the full inverter-day.

---

### 8.14 Design and Monitoring Benchmarks

Requirement:
- Give every loss detector an external reference so its output can be judged as
  high or low against something other than its own history.

Sources (all under `raw data input/`, none committed):

| Source | What it is |
| --- | --- |
| `IKN 50 MW limit.pdf` | PVsyst V7.4.8, variant `71.5MWp - bifacial - actual`, simulated 2025-04-21. Despite the filename this is a full simulation report, not a limit document. |
| `Solar OS/*.csv` | Monitoring platform export: measured per-inverter loss decomposition plus a daily loss time series. WB03-WB10 only. |
| `IKN Generation.xlsx` | Sheet `Setpoint` (per busbar, 10-minute, from 2024-12-28) and sheet `Summary (PV)` (daily per-WB generation, proportional setpoint, deemed dispatch, curtailment flag). |

This PVsyst report supersedes the earlier `...VCE-Report.pdf`, which models a
54 MWp / 134-inverter variant that was never built. Use the 71.5 MWp one: it
covers all 194 inverters including WB01-WB02.

Design configuration (as-built):

```text
114,420 modules / 71.51 MWp     194 inverters (144 x 300 kVA + 50 x 200 kVA)
PR 81.95 %                      Specific production 1299 kWh/kWp/year
Grid export limit 53.20 MW      Bifacial modelled (factor 0.80, ground albedo 0.13)
```

Design loss budget, for comparison against detector output:

| Loss | Design | Detector that should see it |
| --- | --- | --- |
| Soiling | -1.50 % | 8.11 M2a Soiling |
| Near shadings (irradiance) | -2.07 % | 8.9 M2a Shading |
| Far shadings / horizon | -0.38 % | 8.9 M2a Shading |
| Temperature | -4.97 % | physics / cell temp |
| Mismatch, modules and strings | -2.05 % | 8.5 M2b Peer Z-score |
| DC ohmic wiring | -0.48 % | cable metrics, see 8.11 |
| System unavailability | -0.98 % | 8.4 M2e Availability |
| Unused energy (grid limitation) | -2.38 % | 8.12 PR and Curtailment |

Measured reference (Solar OS, per inverter):

```text
Shading loss   max 1.47 %   (WB04INV17)
Soiling loss   max 2.25 %   (WB07INV04)
PR             min 45.4 %   (WB04INV18, WB09INV09)
```

Curtailment:
- The -2.38 % above is the *design* figure at a 53.20 MW export limit.
- Actual curtailment must come from the setpoint history in
  `IKN Generation.xlsx`, not from this number. Observed setpoints run about
  21.1 MW per busbar (~42.3 MW total), well below the design limit, so the
  design figure understates real curtailment.
- Busbar 1 covers WB01-WB05 and busbar 2 covers WB06-WB10, a different grouping
  from the MV station split used elsewhere.

Acceptance criteria:
- Detector outputs are reported alongside the corresponding design figure.
- Curtailment analysis uses the setpoint history; the PVsyst figure appears only
  as a design reference.
- Solar OS values are treated as an independent measurement to agree or disagree
  with, never as ground truth: it covers WB03-WB10 only and its attribution
  method is not documented to us.

### 8.15 String Intraday Diagnostic and Ground Geometry

Requirement:
- Separate soiling from shading at the level of the individual string. 8.9 M2a
  Shading cannot reach this: it works on inverter aggregates, and one or two
  shaded strings among 24-28 healthy ones barely move the aggregate CV.

Core functions/modules:
- `pv_pipeline.string_intraday_diagnostic`
- `build_string_geometry.py`, which produces `config/string_geometry.csv`
- Notebooks `output_string/String_Intraday_Diagnostic.ipynb` (full run from
  baseline CSVs) and `output_string/String_Geometry_Rescore.ipynb` (re-scores an
  existing workbook without re-reading the baseline)

Key calculations:

```text
ratio       = P_string / median(P_siblings on same inverter, same timestamp)
soiling     -> proportional loss; ratio FLAT from morning to afternoon
shading     -> loss concentrated in specific hours
ratio > 1.0 -> panel is HEALTHY; a dirty or damaged panel cannot outperform
               clean siblings, so the deficit is an obstruction at other hours
```

Categories, in priority order: `DEAD_OR_OFFLINE`, `SHADING_PULIH` (recovery
branch, ratio >= 1.02), `SHADING_SORE` (early-death branch, dead at the last
hour on >= 40 % of days), `SHADING_PAGI` / `SHADING_SORE` (asymmetry branch,
`|pagi - sore| >= 0.12`), `UNIFORM`, `CAMPURAN`. A directional label can come
from either the early-death or the asymmetry branch; downstream interpretation
must respect which one fired.

Ground geometry evidence:

`ratio` compares a string against the median of its inverter siblings. On
WB03-WB10 those siblings do not face the same direction: the PV tables follow
the hillside contour rather than sitting on levelled benches (confirmed by field
and drone photographs, 2026-08-06), so the ground slope at a string's position
*is* the orientation of its module plane. The spread of `cross_slope_deg`
*within a single inverter* has a median of 21.7 degrees, and every inverter is
affected. Part of every morning-afternoon asymmetry is therefore pure geometry.

Three evidence columns quantify that part:

| Column | Meaning |
| --- | --- |
| `cross_slope_deg` | East-west component of the ground slope at the string, signed: positive = ground falls east = mornings stronger. NULL where unknown. |
| `expected_ampm_asym` | Asymmetry the cross-slope alone should produce, referenced to the inverter median. |
| `ampm_residual` | `(pagi - sore) - expected_ampm_asym`. This is the diagnostic signal. |

The expectation has the form `k(day_of_year) * sin(cross_slope)`, fitted to
pvlib clear-sky asymmetry derived at the site latitude (-0.9912), 10 degree tilt
facing north, morning window 07-09 and afternoon window 15-17 - the same windows
`classify_strings` uses. That form reproduces all twenty derived values to
within 0.0005; it explains why the seasonal drift is a constant 22.5 % of its
own value regardless of cross-slope magnitude, since `sin` factors the magnitude
out (this is what makes `SEASONAL_REL_RANGE_MAX = 0.30` legitimate as a single
threshold for both gentle and steep strings — note that this 22.5 % reasoning
applies to the **raw** quantity and is superseded once the seasonal
normalisation below cancels it to zero, after which the threshold is justified
against the measured noise floor instead); and it extrapolates correctly to
the site's real range of -29.8 to +31.5 degrees, where a linear fit overshoots.

The reference is the **inverter median**, not flat ground, because `ratio` is
itself relative to the inverter median:

```text
expected_ampm_asym[i] = asym(cross_slope[i]) - median  asym(cross_slope[j])
                                                     j on the same inverter
```

A flat-ground reference would leave a systematic per-inverter offset that no
downstream reader would notice.

These columns are **evidence, not correction**. `ratio`, `deficit_pct` and
`kategori` are never adjusted. Correcting them needs a per-string per-timestamp
POA model, not arithmetic - the same reason the cable voltage-drop columns in
8.11 are presented as evidence rather than applied as a correction.

`config/string_geometry.csv` (4,470 rows, all 194 inverters, all ten WB blocks):

| Field | Meaning |
| --- | --- |
| `inverter_id`, `st`, `pv`, `mppt` | Field string number and its Huawei PV/MPPT channel. Join key to telemetry is `inverter_id` + `pv`; `st` comes from the DXF, `pv` from the as-built DC cable list. |
| `north`, `east`, `lat`, `lon`, `elev_m` | Position in WGS 84 / UTM zone 50S and in degrees. |
| `slope_deg`, `aspect_deg`, `cross_slope_deg` | Local ground plane fitted from `dsm.tif` over a 15 m (east-west) by 4 m (north-south) window at the string position. |
| `plane_rms_m` | Plane fit residual. Slope fields are left empty above 0.5 m (62 strings): the surface is not planar enough to trust. |

Known data limits, deliberately left NULL rather than guessed:

- 11 rows have no `pv`. Before the relabelling below this was 56, and almost
  all of that turned out to be a symptom of the fault rather than a genuine
  disagreement between the as-built list and `strings.yaml`. Nine of the
  eleven are channels the as-built list assigns but `strings.yaml` marks empty
  by design, and telemetry settles it: at midday on 2026-05-13 those channels
  read 0.00 kW while the used channels on the same inverter ran 3.1-3.8 kW,
  and every other channel marked empty on those inverters read zero as well.
  The builder therefore drops any as-built mapping that lands on an empty
  channel (`disprove_empty_channel`), because a `pv` that has been tested and
  disproven is worse than none — nothing downstream could tell it had failed.
  One of the nine, WB05-INV05 PV9, could not be read on 2026-05-13 because that
  inverter did not report at all that day. It has since been confirmed against
  the Nov-Dec 2025 export, where the inverter reports normally: at midday on
  2025-11-03 the channel read 0.000 kW while its 26 live siblings ran a
  7.351 kW median (`pv_pipeline/drive_probe.py::probe_channel_silence`). All
  nine are now confirmed directly. The probe returns `TIDAK_MELAPOR` with a
  null reading rather than 0.00 kW when an inverter is absent, so a repeat of
  the 2026-05-13 situation cannot be mistaken for a silent channel.
- 9 inverters still carry a string label that resolves to two rows — 18 in
  total. Those `(inverter_id, pv)` pairs stay NULL rather than silently taking
  the first match. Sixteen of them are not a drawing fault at all: the as-built
  cable list maps two different `st` values onto one `pv` channel, and each of
  those inverters has three or four unused channels, so no unique repair
  exists. The other two are a stray copy whose position matches no inverter
  with a gap to fill.
- Three inverters still differ from the as-built list by one string, and all
  three sit on the as-built side rather than the drawing's: one carries an `st`
  value of 203, one repeats an `st`, and one is a stray drawing label for which
  no inverter is missing a slot.
- Against the June 2026 diagnostic workbook, 98.4 % of classified strings
  receive a trusted cross-slope.

`1129.dxf` numbers two blocks wrongly, and the builder corrects it in
`resolve_dxf_relabels`. WB04 skips INV17 and shifts everything above it up by
one, so its labels INV18/INV19/INV20 are really INV17/INV18/INV19. WB05 reuses
its INV15-INV19 labels a second time for the WB06 arrays a few hundred metres
east, and its INV20 label belongs to WB06 outright — WB05 stops at INV19 in
both the as-built list and the General Layout.

The correction rests on evidence that does not depend on coordinates at all:
per-inverter string counts matched against the as-built DC cable list. WB04's
label INV18 carries 27 strings where as-built INV18 has 24 and INV17 has 27,
and all three shifted counts line up in sequence (27, 24, 23). For the reused
WB05 labels each split reproduces the as-built counts on both sides exactly —
`47+2 = 24+25`, `50 = 25+25`, `49 = 24+25`, `51 = 26+25`, `51 = 26+25`,
`25 = 25` — so the rule verifies itself. The column arrangement in the General
Layout drawing agrees independently: the orphan clusters form a lone array, a
pair and a triple, in the same west-to-east order as INV15, INV16-17 and
INV18-20 on the sheet. Splitting uses the largest easting gap and accepts it
only above `BLOCK_GAP_M`; the real gaps run 71-334 m against an inverter
footprint of about 50 m.

A second, smaller fault sits inside single inverters: a handful of labels
appear twice within one inverter, and the stray copy turns out to continue
*another* inverter's grid — one that the as-built list shows is short by
exactly that `st`. `DXF_STRAY` moves those four strings. The discriminator is
the destination's grid, not distance from the label's own inverter: rows sit
about 7 m apart and columns about 15.4 m, numbered west to east then down, so
the stray lands one column beyond the target's last labelled string. Judging by
distance to the parent inverter picks the wrong copy — in WB03-INV11 the
correct one is 31 m from its own centroid while the stray is only 21 m.

Before this, six inverters had no coordinates at all and two labels pointed at
inverters that do not exist. All 194 now carry geometry.

Phase One (WB01-WB02) comes from a different drawing and a different labelling
convention. Its 900 strings sit on the `_TEXT_STRING` layer of the AC cable
tray drawing as `S<block><inverter>-<string>`; block 1 is WB01, block 2 is
WB02. Two facts make it simpler than WB03-WB10: the field string number *is*
the Huawei PV channel, so no DC cable list lookup is needed, and no label
appears twice. `mppt` comes from `config/strings.yaml`, whose `mppt_map`
already carries the SUN2000-215KTL layout keyed by inverter model (9 MPPTs of
two sequential strings: PV1+PV2 through PV17+PV18); the builder reads that
table rather than restating it, so the pairing has one home. Phase One is
therefore the only part of the site whose channel mapping is fully known
without the as-built cable list. One label, `S226`, is a leftover from a
superseded revision that renamed it `S125` = WB01-INV25; read literally it
would produce a WB02-INV26 that does not exist in telemetry while leaving
WB01-INV25 without coordinates.

The layout reconciles independently on three counts: 50 groups of exactly 18
strings matches the 50 Phase One inverters whose `strings.yaml` entries mark
PV19-PV28 empty; 50 x 18 x 24 modules plus 3,570 x 26 for WB03-WB10 equals the
114,420 modules of the as-built PVsyst report in 8.14; and the two blocks
separate spatially with a 99 m empty band in easting.

**Phase One is flat, and that is now measured rather than assumed.** Sampling
the DSM at all 900 positions gives a within-inverter cross-slope spread with a
median of 1.4 degrees, against 21.7 degrees for WB03-WB10 - roughly fifteen
times more uniform. At that spread `expected_ampm_asym` stays far below the
0.12 gap that raises a directional label, so ground geometry effectively never
explains a morning or afternoon deficit in WB01-WB02. Four inverters are the
exception (WB02-INV01, INV02, INV04, INV06, spreads of 5.6 to 17.0 degrees):
all sit on the northern edge of Phase One against the WB03/WB04 boundary, where
the 15 m fit window most likely catches an embankment rather than the module
plane.

Seasonal cross-check:

`seasonal_discriminator` predates the geometry columns and used to be the only
way to tell a ground-slope asymmetry from an obstruction: it reads measured
`pagi`/`sore` across two date ranges and calls a stable-signed, slowly drifting
asymmetry geometry. With per-string cross-slope available its role changes from
discriminator to **validator**, and `validate_geometry_seasonally` runs the two
against each other. They are independent — the seasonal test never sees a
cross-slope, and the residual comes from a pvlib clear-sky model never fitted to
telemetry — so agreement is evidence rather than tautology.

Disagreement is the more informative outcome. `MUSIMAN_TERLALU_LONGGAR` marks a
string the seasonal test calls geometry while the residual still exceeds the
gap: a permanent structure produces exactly the stable-signed, slowly drifting
signature the seasonal rule keys on, which is what could not be separated before
coordinates existed. Those strings still get a site visit. `residual_drift`
tests the part of the model most likely to be wrong, its seasonal scaling: for a
genuinely geometric string the residual must be far more stable across seasons
than the raw asymmetry it replaced. Where no trusted cross-slope exists the
validator reports `TIDAK_BERLAKU` rather than agreement, so a string that was
never checked is not read as one that passed.

Running it needs a second diagnostic workbook from a different part of the year;
`String_Geometry_Rescore.ipynb` activates the check when `WORKBOOK_MUSIM_LAIN`
is set. The wider the seasonal separation the sharper the test: `k` differs by
22.5 % between solstices but only 8.8 % between April and June.

Validation result, 2026-08-07:

The check was first run for real over June 2026 (day-of-year 166, 23 days)
against November-December 2025 (day-of-year 335, 49 days), the same 431 strings
on both sides. It validated one half of the model and refuted the other.

**Those 431 strings are not the site.** Both runs were narrowed by
`INVERTER_IDS` in `_build_string_intraday_notebook.py` to the same 17 named
inverters of WB03-WB10, so WB01, WB02 and WB06 never entered either season. The
numbers below therefore calibrate 17 inverters of one plant and must not be read
site-wide. Phase One has never been scored at all - not for want of data or of a
loader, both of which work today, but because the suspect list was carried
forward from an earlier investigation without being revisited. That list has
since been widened to 67 inverters - the same 17 plus all 50 of Phase One,
about 1,331 strings - so the next run supersedes the scope described here. The
addition is additive by construction, since the sibling ratio is computed
within an inverter: the 431 original strings must reproduce identically, and
that is the regression control for the widened run.

| What was tested | Result |
| --- | --- |
| Does cross-slope drive the asymmetry, in the stated direction? | **Yes.** r = +0.748 (June), +0.690 (Dec); R² 0.56 and 0.48 |
| Is the predicted magnitude right? | **No.** Regression slope 0.48 (June) and 0.23 (Dec) against 1.0 if correct |
| Is the seasonal scaling `k(doy)` right? | **No.** Predicted drift correlates −0.638 with the drift actually observed |

One cause probably explains both failures: the coefficients come from a pvlib
*clear-sky* derivation, which assumes radiation is mostly beam. IKN is cloudy
and December is peak monsoon, so the diffuse fraction is high, and diffuse light
is nearly isotropic — it damps the directional asymmetry of a tilted plane. That
over-predicts the magnitude everywhere, and it reverses the seasonal direction,
because `k(doy)` tracks solar geometry while the cloud cycle at this site moves
the opposite way. The same mechanism appears independently in the
classifications: re-running the same 431 strings in November-December moved
**30 % of them into a different category**, almost all toward `UNIFORM` (49 from
`SHADING_PULIH`, 19 from `SHADING_SORE`, 13 from `SHADING_PAGI`) — diffuse light
flattening the hourly profile, not the strings changing. A category is therefore
a statement about one month, not a permanent property.

**This does not invalidate the two-season test, and that is the point.**
`residual_drift` compares one string against itself across seasons, so a stable
multiplicative bias cancels out of it. On the real pair it separates cleanly:
median 0.027 where both methods say geometry against 0.190 where both say
obstruction — a factor of seven, achieved without correcting the magnitude at
all.

That argument holds for `residual_drift` and **fails for the other half of the
pair**, which went unnoticed until the third season arrived (2026-08-15).
`seasonal_discriminator` judges on `spread / mean_abs` of the *measured*
asymmetry, and that ratio does not cancel a common multiplicative factor — it is
inflated by it. Measured across the three seasons, the site-wide median
|asymmetry| is 0.162 (June), 0.095 (Nov-Dec) and 0.083 (March): a spread of
about 50 %, where `SEASONAL_REL_RANGE_MAX` = 0.30 was derived from the ~22.5 %
that solar geometry allows between solstices. The consequence is severe: on the
raw quantity **95 % of strings exceed the threshold**, so `OBSTRUKSI` was very
nearly automatic, and the agreement counts looked healthy only because both
methods were biased the same way (231 of 233 agreements were `OBSTRUKSI`).

The factor is multiplicative — the Nov/June ratio has median 0.53 and the
March/June ratio 0.46, both tight — so `season_scales` divides each season by
its own scale before the spread is taken. The scale is the median |asymmetry|
over a **fixed cohort present in every season**; per-season selection would bias
it, because a dimmer season loses its weak strings first and its scale would
rise, the opposite of the needed correction. Crucially the scale comes from
*measured* strings, never from the model, so the two assessors stay independent
and their agreement remains validation rather than tautology.

Normalisation does not increase agreement — it lowers it, from 233 to 181, and
raises `MUSIMAN_TERLALU_LONGGAR` from 12 to 71. That is the intended result: the
raw agreement was inflated by shared bias, and the disagreement it hid is now
visible. `GEOMETRI` agreements rise from 2 to 9.

One limit now closed, one still open. **Closed 2026-08-15.**
`SEASONAL_REL_RANGE_MAX` = 0.30 was derived for the raw quantity, and applied to
the normalised one it is a different measure. It has now been re-derived — but
not from geometry, because after normalisation geometry predicts a relative
range of exactly **zero**. That leaves the threshold as pure noise allowance and
nothing else, so the reference quantity is the empirical noise floor. It was
measured by an interleaved split-half **inside a single season**, so that no
seasonal change at all sits between the halves: Nov-Dec 2025, halves of 25 and
24 days, 168 strings scoreable in both, giving **p95 = 0.316**.

The value was deliberately **not** moved to 0.316. A p95 from 168 samples rests
on roughly eight tail points and its own 95 % confidence interval spans
[0.271, 0.584] — the tail is steep (p90 = 0.248, p99 = 0.641) — so 0.30 sits
inside it and there is no statistical basis for shifting it. Moving it would
also be fitting the threshold to the data, which this section forbids. What
changed instead is the guard: the bound in
`test_seasonal_threshold_sits_below_the_measured_noise_floor` tightened from
`<= 0.40` to `<= 0.316`.

The direction is the whole point. A threshold *above* the noise floor returns
`GEOMETRI` on noise alone, and `GEOMETRI` **releases** a string from the visit
list — an error that removes the only action that would have caught it. Below
the floor the cost is a wasted visit to a healthy string. The tightened bound
makes the unrecoverable direction unreachable; the previous `<= 0.40` did not,
since it admitted values such as 0.35 that sit above the measured floor.

**Still open:** three runs over the same borderline strings (two-season, three-season
raw, three-season normalised) have produced three different verdicts for a
handful of them; that is a signal the method is at its resolution limit for
those cases, not that any one run is authoritative. The conservative rule
absorbs this: a string is released only when both methods agree, so disagreement
leaves it on the visit list.

What it does invalidate is the single-season verdict. `WB08-INV15-PV20` was
cleared from the field-visit list on a June residual of −0.048; the second
season showed its asymmetry **shrinking 33 %** where geometry demands it **grow
21 %**, and both methods then agreed on obstruction. The ground slope does not
change between seasons; an obstruction's shadow path does.

`provisional_direction_verdict` therefore never returns `GEOMETRI`. A small
single-season residual yields `CALON_GEOMETRI`, a candidate, and only
`validate_geometry_seasonally` over two seasons may clear a string. `OBSTRUKSI`
is still allowed from one season, deliberately: the burden of proof falls on the
claim that *removes* a safety action, not on the one that adds it.

Mounting geometry, settled 2026-08-12: the tables **follow the terrain** rather
than sitting on a levelled bench, so a `cross_slope_deg` fitted to the ground
plane *is* the module cross-tilt and `expected_ampm_asym` is not inflated by a
levelling effect. `IKN-CE-PP-DW-004` section A-A (east-west) dimensions pile
spacing "parallel to ground" with a constant 200 mm stick-up, over a ground
slope declared `variable (-30..+30 deg)`; the Phase One as-built `407-001` gives
a precast pedestal of fixed 600 mm above local existing land elevation for 479
of its 900 tables. This closes the question the field-observation form was
created to answer, and removes the main competitor to the diffuse-fraction
explanation of the magnitude error above. The form keeps the column as a
cross-check only.

Independent inventory check, EL drone survey 2025 (`raw data input/el drone
2025/all.csv`): it covers all 4,470 strings and 114,420 modules, matching
`config/string_geometry.csv` row for row, and it is the only source for modules
per string - 26 in WB03-WB10, 24 in Phase One. It disagreed with the DXF labels
on exactly two inverters, and `List of DC Cables` sided with the survey in both,
with four neighbouring inverters as a positive control where all three sources
agree exactly. WB06-INV06 was labelled 1, 3-24 where the truth is 1-23, which
silently handed each string its neighbour's PV, MPPT and cable metrics; and
WB10-INV03 carried ST25 twice while missing ST27. Both are corrected in
`build_string_geometry.py` (`DXF_ST_SHIFT`, `DXF_RENUMBER_SPATIAL`), each fenced
by the as-built string count so a changed drawing disables the correction rather
than silently renumbering a different input.

First Phase One run, 2026-08-13. Widening `INVERTER_IDS` to 67 produced 1,331
strings over the same June window, and the regression control held: the 431
strings of the original 17 inverters reproduced identically across all fourteen
columns the older workbook carried. Phase One was scored for the first time -
900 strings, 50 inverters - and the asymmetry model failed there completely.

| Group | n | slope | r |
| --- | --- | --- | --- |
| WB03-WB10, 17 inverters | 426 | +0.450 ± 0.022 | +0.699 |
| Phase One, all | 899 | -0.024 ± 0.028 | -0.029 |
| Phase One, four northern-edge inverters | 72 | -0.002 ± 0.013 | -0.020 |

The last row carries the weight. Those four are the only Phase One inverters
with real cross-slope leverage - predicted asymmetry reaches 0.718, comparable
to WB03-WB10 - and the model explains nothing there at all. The rest of Phase
One is too flat to test: its predicted asymmetry never exceeds 0.096.

The failure is not scattered correlation but an **absent signal**. Measured
asymmetry spread is 9-30 % of predicted spread at those four inverters, against
47-69 % in WB03-WB10. That distinction matters, because it rules out a
within-inverter label permutation: shuffling labels destroys the correlation
while leaving the measured spread intact. What remains is either a whole-cluster
displacement of the attached geometry - consistent with the 14-36 m offsets
measured for *every* string of WB02-INV06, not a subset - or modules that are
not canted as the terrain says. Until Open Question 8 settles it, the geometric
evidence columns must not be read as evidence for Phase One's northern edge:
they are the input to that question, not an answer to it. **Since 2026-08-13
they are no longer emitted at all** for those four inverters - `PLACEMENT_DISPUTED`
in `build_string_geometry.py` nulls `slope_deg`, `aspect_deg` and
`cross_slope_deg` for their 72 strings, so the validator returns
`TIDAK_BERLAKU` instead of letting a wrong number pass as evidence and clear a
string off the visit list. Coordinates, `pv` and `mppt` are kept: what is
disputed is the label-to-position link, not the existence of the positions.

No harm reached the field list. **None of the 35 directional verdicts in Phase
One fall on those four inverters.** All 35 sit where cross-slope is negligible
(-0.94 to +1.4 degrees, predicted asymmetry near zero), so their residual is
effectively the raw measured asymmetry and does not lean on the suspect
geometry. Several carry trivial deficits, one of them -0.7 %, so the set needs
filtering by `MIN_DEFICIT_PCT` before it becomes a visit plan - the verdict
column describes shape, not severity.

Phase One's 22 `CALON_GEOMETRI` stay candidates. Clearing any of them still
requires a second season, which does not yet exist for these blocks.

**Settled 2026-08-15: the data supports the EL placement, and the cascade that
blocked acting on it is far smaller than believed.** Two independent lines now
agree. Terrain: the DXF positions put these four on slopes reaching −15.4°,
while no Phase One string anywhere exceeds 2.67°. Cloud correlation
(`Spatial_Correlation.ipynb`): under the EL placement the disputed strings show
Spearman ρ = −0.885 against a control of −0.887 — statistically the same decay
quality as inverters whose placement is known correct — while the DXF placement
gives −0.697, a margin of 0.188.

The standing objection was that the EL positions are already occupied by other
inverters' DXF rows. Measured against all 4,388 rows belonging to other
inverters, that is true for **7 of the 72 strings**, not for the set. The median
distance from an EL position to the nearest other-inverter row is 41 m, and the
median DXF→EL displacement is only **9.0 m** — for most of these strings the
correction is small, not a relocation. The collisions are concentrated: six are
WB02-INV02 strings landing ~2 m from WB02-INV03 rows, and those same six are the
ones that move 60–75 m. The seventh is WB02-INV04-ST18 against WB02-INV05-ST13.

So the re-derivation this blocks is **WB02-INV02 against WB02-INV03**, six
strings — not the 900-string Phase One remap previously assumed.

**Not yet applied, and the reason is specific.** Moving the coordinates without
recomputing `slope_deg` / `aspect_deg` / `cross_slope_deg` at the new positions
would leave the row internally inconsistent — coordinates from the EL survey,
terrain from the DXF position — which is worse than the current NULL, because a
NULL is visibly absent while a stale slope reads as evidence. Recomputing needs
`dsm.tif`, which is not in the repository. The move should be done in
`build_string_geometry.py` with the DSM at hand, in one pass, so coordinates and
plane fit stay consistent.

Verified on live data, 2026-08-14. Re-running both notebooks over the same June
window with `WB06-INV06` added - 68 inverters, 1,354 strings - closed three
questions at once.

The regression control held exactly as designed: of the 1,331 strings carried
over from the previous run, the **only** cells that moved were `cross_slope_deg`,
`expected_ampm_asym` and `ampm_residual` on the **72** northern-edge strings,
which now read empty and return `TIDAK_BERLAKU`. Nothing else in 1,331 strings
shifted by a single value. Nulling changed no verdict, because those 72 were
already non-directional; what it repaired was the integrity of the evidence, not
the calls made from it.

Phase One's remaining 827 strings regress at slope -0.196, r -0.075 - the **same
figures** the non-northern subset produced before the nulling. That coincidence
is the point: it shows the null removed precisely the group carrying cross-slope
leverage and left the rest untouched.

And the ST renumbering proved itself where only live telemetry could test it.
`WB06-INV06`, whose 22 strings had each been receiving a neighbour's `pv`,
`mppt` and cable metrics, now regresses at **slope +0.427, r +0.554** over its
23 strings - against +0.450 for its own plant. A misaligned join cannot produce
that agreement. This is confirmation rather than a controlled comparison: WB06
had never entered any run, so no "before" measurement exists to set against it.

One caveat on how that was established. The version banner in cell 1 reported
`a660ec7`, a July commit, while the same folder's `config/string_geometry.csv`
plainly carried the current nulls - the Drive copy is synced as files while its
`.git` lags. Git HEAD is not a trustworthy version marker for a repository that
lives on Drive; the two data-based markers are what confirmed the run.

Acceptance criteria:
- Classification distinguishes soiling-shaped from shading-shaped deficits per
  string, and emits the hourly ratio profile behind that call.
- No `(inverter_id, st)` key repeats in `config/string_geometry.csv`, and the ST
  set of every inverter is contiguous from 1 to its as-built string count.
  *Met since 2026-08-13 and confirmed on live data 2026-08-14:* the file holds
  no duplicated key at all, and the inverter that had carried one - `WB10-INV03`
  with `ST25` twice - now runs 1-27 unbroken. The criterion is not merely a
  schema check: a repeated key silently hands one string its neighbour's `pv`,
  `mppt` and cable metrics, which is exactly what `WB06-INV06` suffered until it
  was renumbered and what its +0.427 slope now shows repaired.
- A string with no geometry row reads NULL in all three columns, never 0.
- `ratio`, `deficit_pct` and `kategori` are identical with and without
  `string_geometry` supplied.
- Field-visit ranking for directional labels uses `ampm_residual`, not raw
  asymmetry.
- **No string is ever removed from the field-visit list on one season's data.**
  A single season yields `CALON_GEOMETRI` at most; clearing requires two
  seasons agreeing through `validate_geometry_seasonally`.
- The residual is interpreted only for labels that came from the asymmetry
  branch. Labels from the early-death and recovery branches are outside its
  scope, because a cross-slope can neither stop a string from producing nor
  make it outperform its siblings.

## 9. Tools and Tech Stack

### Runtime and Workflow

| Tool | Role |
|---|---|
| Google Colab | Primary notebook execution environment. |
| Google Drive | Source data storage and output sharing. |
| Jupyter Notebook / `.ipynb` | Analyst-facing workflow and visualization. |
| Python | Core analytics and detector implementation. |

### Python Libraries

| Library | Role |
|---|---|
| pandas | Tabular data loading, transformation, grouping, time series. |
| numpy | Numeric calculations and vectorized math. |
| matplotlib | Heatmap and chart rendering. |
| seaborn | Optional heatmap/chart styling. |
| openpyxl | Excel read/write support. |
| PyYAML | Configuration loading. |
| gdown | Google Drive folder/file download from notebook. |
| pvlib | Solar position, clear-sky model, POA transposition, SAPM Tcell. |
| pvanalytics | Hampel outlier filtering. |
| scikit-learn | Isolation Forest anomaly detection. |
| rdtools | Soiling SRR analysis, optional and data-dependent. |
| torch / PyTorch | LSTM Autoencoder skeleton/future model. |
| pytest | Unit and integration testing. |

### Repository Components

| Component | Responsibility |
|---|---|
| `pv_pipeline/data_loader.py` | Raw Excel ingestion. |
| `pv_pipeline/transformations.py` | ID normalization and PV power columns. |
| `pv_pipeline/viz.py` | Heatmap visualization. |
| `pv_pipeline/core.py` | M2Finding schema, severity enum, M2 engine. |
| `pv_pipeline/availability.py` | M2e availability detector. |
| `pv_pipeline/peer_zscore.py` | Peer Z-score / high-R detector. |
| `pv_pipeline/open_circuit.py` | Open circuit detector. |
| `pv_pipeline/ground_fault.py` | Ground fault detector. |
| `pv_pipeline/iforest.py` | Isolation Forest detector. |
| `pv_pipeline/m2a/` | Shading, low irradiance, soiling detectors. |
| `pv_pipeline/poa/` | POA loaders and pvlib estimator. |
| `pv_pipeline/cell_temp.py` | Measured and SAPM cell temperature provider. |
| `pv_pipeline/panel_spec.py` | Panel datasheet and string voltage helpers. |
| `pv_pipeline/physics.py` | Expected power, Kt, DeltaP, PR, energy integration. |
| `pv_pipeline/generation/` | Generation Excel loader. |
| `pv_pipeline/baseline.py` | Healthy baseline accumulator. |
| `pv_pipeline/string_intraday_diagnostic.py` | Per-string intraday soiling vs shading classifier, plus cable and ground-geometry evidence columns. |
| `build_site_layout.py` | Setting-out stakes, plot polygons, UTM conversion and DSM plane fitting into `config/site_layout.yaml`. |
| `build_string_geometry.py` | Per-string coordinates and ground slope into `config/string_geometry.csv`. |
| `output_string/_build_*_notebook.py` | Builders that regenerate the Colab notebooks (`String_Intraday_Diagnostic`, `String_Geometry_Rescore`). Edit the builder, not the `.ipynb`. |
| `run_soiling_analysis.py` | CLI runner for soiling SRR analysis from baseline CSVs (site / WB group / per-WB). |
| `rekap_m2e_allstrings.py` | Cross-date recap of M2e `AllStrings` sheets into one Excel. |
| `train_lstm_ae.py` | LSTM Autoencoder training script (healthy baseline input). |
| `config/*.yaml` | Detector thresholds, strings, site geometry, panel spec, baseline config. |
| `tests/` | Unit and integration tests. |

---

## 10. Inputs and Outputs

### Inputs

- Huawei inverter Excel files, for example `1-2.xlsx`, `3-10.xlsx`.
- Generation workbook, for example `IKN Generation.xlsx`.
- POA pyranometer files.
- Ambient temperature, wind speed, wind direction files.
- PV module temperature file.
- NSRDB or static albedo data.
- YAML configuration:
  - `config/m2_config.yaml`
  - `config/strings.yaml`
  - `config/site_geometry.yaml`
  - `config/panel_spec.yaml`
  - `config/baseline.yaml`
- `config/string_geometry.csv` - per-string coordinates and ground slope (8.15).
  Committed, unlike the raw drawings it is derived from.
- As-built drawing sources, used offline by the two builder scripts and not
  needed at pipeline runtime: `raw data input/1129.dxf` (WB03-WB10 string-number
  labels with UTM insertion points), `Cable Routing & Tray Layout AC & DC(1).dxf`
  (AC tray drawing; its `_TEXT_STRING` layer carries the 900 Phase One string
  labels), `dsm.tif` (topographic survey DSM, 0.1187 m/px),
  `List of DC Cables 0411.xls` (ST-to-PV mapping and cable length / voltage drop),
  and the `IKN-CE-PP-DW-004` / `ISPP-PSC-DWG-1004-001` drawings.
- `raw data input/el drone 2025/all.csv` - per-module electroluminescence survey
  covering all 4,470 strings and 114,420 modules. Independent of both the DXF
  and the cable list, so it arbitrates string numbering; also the only source
  for modules per string (8.15). Its header starts at row 33 under a ragged
  preamble, and it carries two ID conventions in one file (`S101_01` for Phase
  One, `WB06INV06ST01` elsewhere).
  The builders resolve these by name prefix anywhere under `raw data input/`, so
  the drawings can be filed into subfolders. The shallowest match wins, and two
  matches at the same depth raise rather than pick one. Runtime loaders (POA,
  cell temperature, generation, rainfall, cleaning schedule, cable list) still
  use flat hardcoded paths and expect their file directly in `raw data input/`.

### Outputs

- PV string heatmap figures.
- Daily PR chart and CSV.
- Findings JSONL/XLSX.
- Detector artifact sheets.
- Availability summary.
- Soiling SRR workbook (`soiling_srr_*.xlsx`) and sawtooth trend PNG per analysis scope.
- M2e cross-date recap Excel (`rekap_m2e_allstrings.xlsx`).
- String intraday diagnostic workbook (`string_intraday_diagnostic_*.xlsx`:
  `Klasifikasi`, `Profil_Jam`, `Uji_Hujan`, `Metadata`) and hourly profile PNG.
- Geometry rescore workbook (`geometry_rescore_*.xlsx`), re-judging an existing
  diagnostic workbook against `config/string_geometry.csv`.
- Baseline CSV/parquet.
- Final `df_plot` CSV export.

---

## 11. Data Quality Requirements

1. Timestamp must be parseable and ordered.
2. Duplicate timestamp/inverter rows must be detected or handled.
3. Missing PV voltage/current columns must be surfaced.
4. Empty PV channels must be excluded using `strings.yaml`.
5. POA and Tcell fallback chains must be traceable.
6. Outlier filtering must be configurable and auditable.
7. Detector outputs must include enough evidence for engineering review.

---

## 12. Configuration Requirements

All detector thresholds should be configurable through YAML rather than hard-coded in notebook cells. The notebook should load config once, print effective detector status, and include config metadata in output artifacts.

Minimum configurable areas:

- POA thresholds.
- Solar elevation filters.
- Severity thresholds.
- Debounce steps.
- Empty PV map path.
- Panel spec path.
- Baseline skip rules.
- Detector enabled/disabled flags.
- Exclusion from main Findings sheet.

---

## 13. Acceptance Criteria

The product is considered usable for engineering review when:

1. Notebook can run from a fresh Google Colab session using Google Drive data.
2. Required files are downloaded or mounted successfully.
3. `combined_df` and `df_plot` are generated.
4. PV string power and total inverter power are calculated.
5. Heatmap renders for selected inverter(s).
6. Enabled M2 detectors complete without fatal errors.
7. Findings and artifact files are written.
8. PR and curtailment cross-check outputs are generated.
9. Baseline accumulator can export healthy data.
10. Errors are surfaced clearly rather than silently skipped.

---

## 14. Risks and Known Gaps

| Risk / Gap | Impact | Mitigation |
|---|---|---|
| Google Drive file naming changes | Notebook cannot find inputs | Add file discovery validation and clear error messages. |
| Colab runtime reset | Lost local outputs | Save outputs back to Drive. |
| Large Excel files | Slow notebook execution | Cache intermediate CSV/parquet where possible. |
| Duplicate config keys | Unexpected effective settings | Add config validation and duplicate-key check. |
| Isolation Forest noise | Too many false findings | Keep excluded from main Findings until calibrated. |
| Soiling needs long history | Cannot run SRR reliably on short data | Emit insufficient-data status until 90-180 days available. |
| Monsoon rain resets shorten dry intervals | SRR yields few valid intervals (fragmentary sawtooth); site-level blend can fail with NoValidIntervalError | Analyze per cleaning zone (WB group), use `precip_and_shift` criterion with shorter min interval; interpret via sawtooth plot (Cell 7). |
| Partial inverter outage depresses PR | SRR reads outage as soiling | M2e availability mask removes affected inverter-days from energy and capacity (enabled via `availability_dir`). |
| LSTM needs clean baseline | Model may learn faults as normal | Use baseline accumulator and manual review before training. |
| Curtailment not fully integrated into every detector | False positives during power limitation | Add curtailment-aware detector gating. |
| Sibling ratio compares strings that face different directions | Morning/afternoon labels partly reflect ground slope rather than obstruction; field crews sent to plots with nothing to prune | Report `ampm_residual` beside the raw asymmetry and rank field visits by it (8.15). |
| String labels ambiguous in the as-built DXF | A guessed position would be presented to engineers as measured evidence | The 8 affected inverters resolve to NULL cross-slope; NULL is never rendered as "flat". |
| Geometry explains asymmetry but not deficit level | A string cleared of a directional label may still be read as "nothing wrong" | 8.15 states explicitly that a midday deficit survives the geometry test and still needs investigation. |
| Phase One telemetry rides an IconPlus fibre link, WB03-WB10 rides local ethernet | A fibre outage removes all 900 Phase One strings from the export; read as downtime it depresses availability for a plant that was running, and shortens the day count behind every Phase One statistic | External factor, outside site control. Treat zero-Phase-One dates as UNKNOWN, not DOWN (8.4); confirm with the per-file inverter count — all 50 missing is the link, a subset is the inverters. |

---

## 15. Roadmap

### Phase 1: Notebook Stability

- Standardize Google Drive folder structure.
- Add input validation cell.
- Print effective config summary.
- Ensure all outputs are saved to Drive.

### Phase 2: Detector Productionization

- Calibrate Isolation Forest.
- Mature soiling SRR workflow. (Done 2026-07: temperature correction, M2e availability mask, monthly loss breakdown, per-string cleaning recommendation, sawtooth replotting from workbook.)
- Separate soiling from shading per string, below inverter-aggregate resolution. (Done 2026-08: `string_intraday_diagnostic` module, cable voltage-drop evidence, and ground-geometry evidence derived from the as-built DXF and the survey DSM. See 8.15.)
- Add curtailment-aware gating to detector decisions.
- Add complete loss waterfall. (Done 2026-08: modul `pv_pipeline/m2f/` — ledger klaim sekuensial, tabel Pareto, grafik waterfall, terangkai lewat `notebook/m2f_loss_attribution.ipynb`. Menunggu data POA/Tcell untuk menghasilkan angka.)

### Phase 3: Advanced Analytics

- Train LSTM Autoencoder using healthy baseline.
- Add bifacial gain analytics.
- Add microcrack/degradation-specific indicators.
- Add stronger residual attribution engine. (Sebagian 2026-08: v1 mengatribusikan availability_outage, dc_cable_fault, dan soiling; shading dan low_irradiance_eff masih jatuh ke `unexplained` — v2.)

### Phase 4: Dashboard and Operations

- Build Streamlit dashboard from generated findings/artifacts.
- Add multi-day findings browser.
- Add alert workflow: acknowledge, snooze, resolve.
- Add notification dispatch for high/critical events.

---

## 16. Success Metrics

- Notebook run success rate.
- Number of valid inverter/string records processed.
- Detector completion rate.
- False positive rate after engineering review.
- Time from raw data upload to findings output.
- Number of confirmed O&M issues detected.
- Reduction in unexplained low PR days.
- Baseline healthy-data coverage.

---

## 17. Open Questions

1. What is the final Google Drive folder convention for raw input and output artifacts?
2. Should notebook outputs be versioned by date, detector config hash, or both?
3. Which detector findings should trigger operational alerts?
4. What cleaning cost and tariff assumptions should be used for soiling economics?
5. What is the minimum review workflow before data enters healthy baseline?
6. Should Streamlit dashboard become a required production deliverable or remain optional?
7. Should a per-string POA model be built? 8.15 quantifies the morning-afternoon asymmetry a cross-slope produces, but deliberately stops short of the level effect, so a midday deficit is never attributed to geometry. Closing that gap would allow correcting `ratio` instead of only annotating it - and would also decide whether the cable voltage-drop columns in 8.11 stay evidence-only. **Deferred, and the 2026-08-07 validation lowered rather than raised its urgency:** the diffuse fraction it would model is the identified cause of the magnitude error, but `residual_drift` already discriminates seven-to-one without it, because a stable multiplicative bias cancels when a string is compared against itself across seasons. It becomes worth building if single-season verdicts must be trusted - a new site with under half a year of data - or if the goal moves from annotating `ratio` to correcting it.
8. Which source places a Phase One string correctly when the DXF labels and the EL survey disagree? The two agree closely on most of the site - median offset 1.8-2.1 m in WB01/WB02 - but 215 strings sit more than 20 m apart, concentrated in 21 Phase One inverters including all four on the northern edge. Adjudicating by telemetry (residual-ratio correlation must decay with distance; positive control passes at rho -0.28 to -0.30, permutation test passes) **split 3-1** on 2026-08-13: the survey explains WB02-INV02/04/06 better, the DXF explains WB02-INV01 better and most strongly of all. Margins are 0.06-0.10 from a single day, so nothing in `config/string_geometry.csv` was changed. Settling it needs the same test over several days, chosen **partly cloudy** - a uniformly clear day suppresses the spatial structure the test reads. Until then the terrain analysis stands, because it concerns points on the ground rather than which string occupies them. **Raised in priority the same day by the first Phase One run** (8.15): over the whole of June, across 72 strings rather than one day's correlation, the asymmetry model has no predictive power whatsoever at those four inverters - r = -0.020 where the identical model reaches +0.699 in WB03-WB10, and measured asymmetry spread collapses to 9-30 % of predicted. A shuffled label set cannot produce that, since shuffling leaves the spread intact; a whole-cluster displacement can, and every string of WB02-INV06 was measured 14-36 m from where the survey puts it. This is now the strongest single piece of evidence on the question and it points away from the DXF placement, which makes the earlier 3-1 split - decided on margins of 0.06-0.10 from one day - too weak to lean on. Until it is settled, treat the geometric evidence columns for WB02-INV01/02/04/06 as unresolved input rather than evidence. **Half-answered the same day.** Reading the terrain at both candidate positions settles the negative half: at the EL positions all four inverters sit on flat ground (|cross-slope| <= 1.9 degrees, sd <= 0.69) where the DXF positions put them on the ramp (sd 1.24-5.53, reaching -15.4). The control holds - for WB05-INV03, WB07-INV04 and WB03-INV06, where the two sources agree, the same reading returns the same numbers (8.26/8.17, 4.58/4.58, 9.07/9.07) - so the collapse is real rather than a method artefact. Flat ground predicts flat morning-afternoon asymmetry, which is exactly what June measured. Three independent lines therefore agree that **the DXF placement for these four is wrong**, and their plane columns are now emitted as NULL rather than as numbers (8.15). What stays open is the replacement, and it is larger than four inverters: the EL positions for these strings are already occupied by DXF rows belonging to others - WB02-INV02's land on WB02-INV03's, which is undisputed and has no evidence against it - so relocating propagates, and one string's EL position is 17.0 m from the nearest DXF row at all, falling in a gap rather than on a table. Settling the placement means re-deriving the label-to-position mapping for the whole 900-string Phase One block from the survey and validating it against a third source that does not yet exist; the partly-cloudy telemetry test remains the candidate. **Applied 2026-08-15**, once `dsm.tif` was located in the meteorological survey folder. All 72 rows of WB02-INV01/02/04/06 now carry the EL coordinates and plane columns recomputed from the DSM at those positions, replacing the NULLs: site-wide `cross_slope_deg` coverage rises 4335 -> 4407. The recomputation reproduces the independent evidence rather than assuming it — at the EL positions |cross-slope| has median 1.34 and max 3.29 degrees, flat ground of exactly the character the rest of Phase One shows (site median 0.57), where the DXF positions put these four on a -15.4 degree ramp. Displacement is median 8.8-23.4 m by inverter, max 74.4 m.

Seven of the 72 land within about 2.0 m of rows belonging to WB02-INV03 and WB02-INV05, against a Phase One nearest-neighbour distance that is a uniform 7.27 m (p5 = p95). That was first read as a collision created by the move, and as an open question of label ownership needing a third source. **Both readings were wrong, corrected 2026-08-16.**

The 2.0 m separation is not created by the move: it is already present between the *stored* DXF coordinates of WB02-INV02 and WB02-INV03, at the identical 1.97 m, before anything was applied. What the move does is leave the pair straddling two coordinate sources. Measured consistently — EL against EL — the same two inverters sit **7.21 m** apart, on the lattice, with no collision at all. The apparent conflict was an artefact of comparing a relocated inverter against a neighbour still held at its DXF position, and it needed no third source to settle.

That neighbour is itself displaced: WB02-INV03 differs from its EL position by a median 16.25 m.

**The WB01 cluster was tested in full on 2026-08-16 and does not resolve.** Thirteen WB01 inverters sit more than 5 m from their EL positions, and the cloud-shadow test was run over all of them in three waves against a clean control (WB01-INV24/22/23/17, which agree with EL to 0.11 m after recentering). Only the first four separate:

| wave | inverters | control | DXF | EL | margin | verdict |
|---|---|---|---|---|---|---|
| 2, run 1 | INV21/18/25/01 | -0.927 | -0.785 | -0.891 | 0.107 | EL |
| 2, run 2 | same, other days | -0.897 | -0.769 | -0.900 | 0.131 | EL |
| 3 | INV20/19/08/02 | -0.934 | -0.859 | -0.868 | 0.009 | tie |
| 4 | INV07/03/13/12/06 | -0.950 | -0.729 | -0.808 | 0.079 | tie |

The controls are healthy in all four runs, so the ties are readable results rather than failed measurements. They are also not a blind spot of the method: a rigid translation of a whole inverter would preserve pairwise distances and leave the test unable to see it, but that is not what happens here — pairwise distances change by a median 25.09 m in wave 3, more than the 24.06 m in wave 2 that did separate. The telemetry simply does not distinguish the two placements for those nine.

Terrain does not break the tie either, and this is where WB01 differs from WB02. The wave-1 argument was that the DXF positions put four inverters on a -15.4 degree ramp while the EL positions were flat. Nothing like that appears anywhere in WB01: at both placements |cross-slope| has a median of 0.4-0.7 degrees and a maximum near 2, on all three waves. So wave 2's verdict rests on **one** line of evidence measured twice, not on two independent lines as in wave 1. One mild consideration points the other way — the DSM plane fit is about twice as rough at the EL positions (median rms 0.33-0.38 m against 0.13-0.16 m at DXF) — which is not decisive but is not nothing.

**Nothing was moved, and moving the tested four alone would make the data worse.** Partial relocation leaves neighbours at their DXF positions and the split pair collides: moving 4 of the 13 raises collisions under 3 m from 14 to 68, moving 7 gives 28, and only moving all 13 returns to 14. So the choice is between relocating nine inverters on a tie and relocating none, and the project's rule puts the burden of proof on the change. The cluster stays where it is.

What would settle it is not another telemetry run but a **field check**: read the physical string labels at a few tables in the WB01 cluster, which distinguishes the two placements directly and cheaply. The visit-list document already instructs crews to trust physical labels over map points for WB01-INV07 PV4/PV5 for exactly this reason. It is one of **16 Phase One inverters whose stored placement disagrees with the EL survey by more than 5 m** (worst: WB01-INV21 at a median 50.05 m), none of which carry the cloud-shadow and terrain evidence that settled the four. That is the real open question, and it is the one already tracked here — larger than the four inverters and unchanged in nature by this correction.

