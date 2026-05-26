# Product Requirements Document (PRD)

## PV Module Performance Analytics Notebook and Pipeline

Status: Draft v0.1  
Date: 2026-05-27  
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
- Estimate site-level soiling loss and cleaning economics when enough daily data exists.

Core functions/modules:
- `pv_pipeline.m2a.soiling`
- Optional dependency: `rdtools`

Key calculations:

```text
PR_daily = E_daily / (H_POA_daily * Capacity_kWp)
Soiling loss = 1 - soiling_ratio
Daily value loss = avg_daily_energy_kWh * tariff_IDR_per_kWh * soiling_loss
Payback days = cleaning_cost_IDR / daily_value_loss
```

Acceptance criteria:
- If days of data are below minimum, emit insufficient-data output.
- If enough data exists, produce soiling ratio, loss estimate, and cleaning recommendation.

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

### Outputs

- PV string heatmap figures.
- Daily PR chart and CSV.
- Findings JSONL/XLSX.
- Detector artifact sheets.
- Availability summary.
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
| LSTM needs clean baseline | Model may learn faults as normal | Use baseline accumulator and manual review before training. |
| Curtailment not fully integrated into every detector | False positives during power limitation | Add curtailment-aware detector gating. |

---

## 15. Roadmap

### Phase 1: Notebook Stability

- Standardize Google Drive folder structure.
- Add input validation cell.
- Print effective config summary.
- Ensure all outputs are saved to Drive.

### Phase 2: Detector Productionization

- Calibrate Isolation Forest.
- Mature soiling SRR workflow.
- Add curtailment-aware gating to detector decisions.
- Add complete loss waterfall.

### Phase 3: Advanced Analytics

- Train LSTM Autoencoder using healthy baseline.
- Add bifacial gain analytics.
- Add microcrack/degradation-specific indicators.
- Add stronger residual attribution engine.

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

