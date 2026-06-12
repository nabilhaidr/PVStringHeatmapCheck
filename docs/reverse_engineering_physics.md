# Reverse Engineering — Perhitungan Physics PV Pipeline (PLTS-IKN)

Dokumen ini membedah (reverse-engineer) seluruh perhitungan berbasis fisika di repo ini: Performance Ratio (PR), P_expected, clearness index (Kt), ΔP, suhu sel (Tcell), POA, Voc, dan detector yang memakainya. Setiap formula ditelusuri langsung dari kode, dengan referensi `file:baris` agar bisa diaudit ulang.

Basis: kondisi repo per 2026-06-11. Sumber kebenaran utama: `pv_pipeline/physics.py`, `pv_pipeline/cell_temp.py`, `pv_pipeline/panel_spec.py`, `pv_pipeline/poa/pvlib_estimator.py`, notebook `20260514stringmap_v1.5.ipynb` (Cell PR), dan `config/*.yaml`.

---

## 1. Peta Alur Data

```
RAW INPUT (xlsx, 5-menit, naive WITA/UTC+8)
│
├─ POA PLTS IKN 2025/2026.xlsx ──────────► PyranometerLoader (per WS-1..WS-5)
├─ (pvlib) lat/lon/tilt/azimuth ─────────► PvlibClearSkyEstimator (clear-sky POA)
├─ Surface Albedo NSRDB.xlsx ────────────► AlbedoLoader (albedo dinamis 30-menit)
├─ PV Module Temperature.xlsx ───────────► CellTempProvider (Tcell per WS)
├─ Ambient Temp / Wind Speed.xlsx ───────► weather loaders (untuk SAPM fallback)
├─ Huawei inverter xlsx (V/I/P per PV) ──► combined_df (per inverter, per string)
└─ IKN Generation.xlsx ──────────────────► GenerationLoader (kWh harian per WB,
                                            curtailment, deem dispatch)
│
▼
POAProvider ──► POA(t, WB)          CellTempProvider ──► Tcell(t, WB)
│                                   │
└────────────┬──────────────────────┘
             ▼
   physics.py: P_expected(POA, Tcell)  ;  Kt = POA/POA_clearsky
             │
             ├──► ΔP_ratio = P_actual/P_expected − 1     (per inverter/string)
             ├──► Integrasi energi: E = Σ P·dt           (Riemann sum)
             └──► PR = E_actual / (H_POA × C_kWp)        (IEC 61724-1)
                          │
                          └──► outputs/pr_daily_YYYYMMDD.csv
                               (+ curtailment_flag, deem_dispatch_kwh)

Detector pemakai: M2b peer z-score (R_str), open-circuit, ground-fault,
M2e availability, M2a shading / low-irradiance / soiling-SRR.
```

---

## 2. Konstanta Global & Spesifikasi Panel

### 2.1 Konstanta STC (`physics.py:35-48`)

| Konstanta | Nilai | Arti |
|---|---|---|
| `G_STC_WM2` | 1000.0 W/m² | Irradiance referensi STC (IEC 61215) |
| `T_STC_C` | 25.0 °C | Suhu sel referensi STC |
| `KT_MIN_POA_WM2` | 1.0 W/m² | POA clearsky minimum untuk Kt (di bawah ini → NaN) |
| `DELTA_P_MIN_EXPECTED_W` | 1.0 W | P_expected minimum untuk ΔP (malam → NaN) |
| `PR_MIN_POA_KWH_PER_M2` | 0.01 kWh/m² | Insolasi minimum untuk PR (di bawah ini → NaN) |

### 2.2 Panel: Jinko JKM625N 78HL4-BDV (`config/panel_spec.yaml`)

| Parameter | Nilai | Catatan |
|---|---|---|
| Pmax STC | 625 W | bifacial Tiger Neo N-type, dual-glass |
| Voc STC | 55.72 V | |
| Vmp / Imp STC | 46.10 V / 13.56 A | |
| Isc STC | 14.27 A | |
| γ_Pmax | **−0.29 %/°C** | lihat catatan konflik di §14.1 |
| β_Voc | −0.25 %/°C | |
| α_Isc | +0.045 %/°C | |
| NOCT | 45 ± 2 °C | |
| Bifacial factor | 80 ± 5 % | **tidak dipakai di model** (§14.3) |
| Modul per string | WB01–02: **24**; WB03–10: **26** | `strings_per_wb`, default 26 |

Voc string nominal STC: 55.72 × 24 = 1337.28 V (WB01–02) ; 55.72 × 26 = 1448.72 V (WB03–10), batas sistem 1500 V.

---

## 3. POA — Plane-of-Array Irradiance

### 3.1 Sumber terukur: pyranometer per weather station

5 WS, mapping ke WB (`config/site_geometry.yaml:84-89`):

```
WS-1 → WB08, WB09, WB10      WS-4 → WB03, WB04
WS-2 → WB05, WB07            WS-5 → WB01, WB02
WS-3 → WB06
```

### 3.2 Sumber model: pvlib clear-sky (`poa/pvlib_estimator.py`)

Geometri site (`config/site_geometry.yaml`): lat −0.9912, lon 116.6381, elevasi 85 m, tz Asia/Makassar (WITA), **tilt 10°, azimuth 0° (menghadap utara** — site sedikit di selatan ekuator).

Langkah perhitungan (`estimate()`, baris 212-235):

1. Komponen clear-sky GHI/DNI/DHI — tiga model: `ineichen` (default, Linke turbidity), `simplified_solis`, `haurwitz` (hanya GHI → didekomposisi ke DNI/DHI via ERBS).
2. Transposisi ke bidang panel via `pvlib.irradiance.get_total_irradiance` dengan model **Perez** (default Fase 2; `DEFAULT_TRANSPOSITION_MODEL`, baris 37), butuh `dni_extra` + `airmass`.
3. Albedo: dinamis dari NSRDB TMY xlsx (range ~0.134–0.166, mean 0.153, interval 30-menit) → fallback statis 0.20 bila gagal. Ini *forecast*, bukan pengukuran (tidak ada albedometer di site).
4. POA negatif (matahari di bawah horizon) di-clip ke 0.

### 3.3 Rantai fallback `source="auto"` (`config/m2_config.yaml:58-61`)

Per timestamp, pakai sumber pertama yang non-NaN:

```
pyranometer_per_ws  →  pyranometer_avg  →  pvlib_clearsky_ineichen
```

### 3.4 Filter daylight berbasis fisika

`get_solar_elevation()` (`pvlib_estimator.py:249-274`): apparent solar elevation. Detector memfilter sampel dengan **elevation > 5°** (`filter_mode: "solar_elevation"`), menggantikan heuristik jam (`hour_cutoff_end: 18.0`). Tambahan proteksi sunset: `poa_floor_wm2 = 50` karena pyranometer punya sensor lag saat sunset (residu 100–300 W/m² padahal matahari sudah terbenam).

---

## 4. Tcell — Suhu Sel

### 4.1 Sumber terukur (`cell_temp.py`)

Xlsx 18 kolom: 4 WS × (3 sensor + rata-rata) + overall average. **WS-5 tidak punya sensor Tcell** → WB01/WB02 menumpang ("piggyback") ke WS-4 lewat mapping khusus `ws_to_wb_tcell` (berbeda dari mapping POA):

```
WS-1 → WB08-10   WS-2 → WB05,07   WS-3 → WB06   WS-4 → WB01,02,03,04
```

Reindex ke timestamp kueri pakai `method="nearest"` dengan toleransi **2 menit**.

### 4.2 Fallback model: SAPM (Sandia Array Performance Model)

Rantai `auto`: `measured_per_ws → measured_overall_avg → sapm` (`cell_temp.py:66-70`).

SAPM (`pvlib.temperature.sapm_cell`, dipanggil di `cell_temp.py:504-513`):

```
T_module = POA · exp(a + b·WS_wind) + T_ambient
T_cell   = T_module + (POA / 1000) · ΔT
```

Preset `open_rack_glass_glass` (sesuai panel dual-glass open-rack): **a = −3.47, b = −0.0594, ΔT = 3.0** (`cell_temp.py:75-80`). Input POA dari POAProvider (auto), ambient temp & wind speed per-WS dengan fallback ke rata-rata fleet bila per-WS NaN.

---

## 5. P_expected — Daya Ekspektasi

### 5.1 Per modul (`physics.py:54-81`)

```
P_module(POA, Tcell) = Pmax_STC × (POA / 1000) × (1 + γ/100 × (Tcell − 25))
                     = 625 × (POA/1000) × (1 − 0.0029 × (Tcell − 25))   [Watt]
```

Model linear satu-faktor: skala irradiance × koreksi suhu. Contoh: POA = 1000, Tcell = 55 °C → 625 × 1 × (1 − 0.0029×30) = 570.6 W.

### 5.2 Per string (`physics.py:84-108`)

```
P_string = P_module × n_modules(WB)     n = 24 (WB01-02) | 26 (WB03-10)
```

Untuk level inverter/sistem: dikalikan jumlah string per inverter oleh pemanggil (bukan tanggung jawab PanelSpec).

---

## 6. Kt — Clearness Index (`physics.py:111-162`)

```
Kt = POA_measured / POA_clearsky        (NaN bila POA_clearsky < 1 W/m²)
```

Interpretasi: Kt ≈ 1 cerah; Kt < 1 berawan; Kt > 1 cloud-edge enhancement (jarang, biasanya artefak). Dipakai sebagai pre-screen "clear-sky day".

## 7. ΔP — Delta Power Ratio (`physics.py:165-218`)

```
ΔP_ratio = (P_actual / P_expected) − 1      (NaN bila P_expected < 1 W)
```

P_actual = `Active power(kW)` Huawei × 1000. Interpretasi: ≈ 0 normal; < 0 underperform (soiling/shading/fault); > 0 overperform (drift kalibrasi sensor, cloud-edge, atau P_expected underestimate).

## 8. Integrasi Energi (`physics.py:221-271`)

Riemann sum sederhana:

```
E_kWh = Σ P_kW(t) × dt_jam        dt = 5/60 h = 0.08333 h (sampling 5-menit)
```

`freq_hours` di-autodetect dari median Δt index (butuh DatetimeIndex); NaN di-skip. Cross-check yang dimaksudkan kode: hasil integrasi harus dekat dengan kWh harian STS di `IKN Generation.xlsx`.

---

## 9. PR — Performance Ratio (IEC 61724-1)

### 9.1 Formula inti (`physics.py:274-337`)

```
PR = E_actual / E_nominal
   = E_actual_kWh / (H_POA_kWh/m² × C_kWp)
```

Pembagian dengan `G_STC = 1 kW/m²` implisit: karena G_STC = 1 kW/m², `C/G_STC` secara numerik = `C_kWp`. Guard: H_POA < 0.01 kWh/m² → NaN.

Interpretasi (docstring `physics.py:288-291`):

| PR | Arti |
|---|---|
| 0.75 – 0.85 | normal (tipikal PLTS tropis) |
| < 0.70 | underperforming (soiling/shading/curtailment) |
| > 0.90 | mencurigakan (drift kalibrasi / periode pendek / dingin) |

### 9.2 Rantai perhitungan aktual (notebook Cell PR, baris ~4015-4149)

Inilah yang menghasilkan `outputs/pr_daily_YYYYMMDD.csv`:

1. **Grid 5-menit** untuk rentang analisis; loop 10 WB → `poa_provider.get_poa(ts_5min, WB)` (source auto §3.3).
2. **POA site** = rata-rata aritmetika 10 deret WB: `poa_per_wb_df.mean(axis=1)`.
3. **Insolasi harian** (kWh/m²): `H = Σ(POA_W/m² × 5/60 / 1000)` di-resample per hari.
4. **Energi site** = `total_kwh` dari `IKN Generation.xlsx` (kolom L = Σ STS WB01–10, **metered harian** — bukan hasil integrasi 5-menit).
5. **PR site** = `compute_pr(E_site, H_site, 71500.0)` — kapasitas dari `m2_config.yaml generation.capacity_kwp`.
6. **PR per-WB**: kapasitas = 71500/10 = **7150 kWp seragam per WB**, masing-masing WB pakai insolasi POA-nya sendiri: `PR_wb = E_wb / (H_wb × 7150)`.
7. **Cross-check curtailment (Wave 11)**: hari dengan `PR < 0.65` (`LOW_PR_THRESHOLD`) dicek terhadap `curtailment_flag`:
   - `Yes` → PR rendah dijelaskan curtailment (operasional, bukan fault).
   - `No` → *** investigate *** (potensi fault).
8. CSV final: `pr_site`, `pr_WB01..pr_WB10`, `curtailment_flag`, `deem_dispatch_kwh`.

### 9.3 Semantik data Generation (`generation/loader.py`, `site_geometry.yaml:145-160`)

| Kolom xlsx | Key | Arti |
|---|---|---|
| B–K | `WB01`..`WB10` | STS Generation harian per WB (kWh) |
| L | `total_kwh` | Σ WB01–10 |
| M | `pae_kwh` | Projected Available Energy 00:00–24:00 |
| N | `generation_sts_kwh` | MAX(L, M) ter-gate setpoint busbar (busbar1 WB01–05 < 24300 kW; busbar2 WB06–10 < 25700 kW) |
| O, P | `busbar{1,2}_setpoint_kw` | Setpoint proporsional busbar |
| Q | `deem_dispatch_kwh` | Loss kWh harian yang disepakati bulanan akibat curtailment |
| — | `curtailment_flag` | 'Yes'/'No' — grid operator memerintah cut-off (setpoint busbar tercapai) |

---

## 10. Voc — Open-Circuit Voltage

### 10.1 Voc aktual (estimasi dari data, `voc_estimator.py:34-78`)

Tidak ada pengukuran Voc langsung → diestimasi dari V saat arus mendekati nol (string secara natural open-circuit saat sunrise/sunset):

```
Voc_actual = median( V[ |I| < 0.5 A  AND  V > 10 V ] )     NaN bila sampel < 3
```

### 10.2 Voc nominal (datasheet, `panel_spec.py:190-244`)

```
Voc_module(T) = Voc_STC × (1 + β/100 × (T − 25))          β = −0.25 %/°C
Voc_string(T, WB) = Voc_module(T) × n_modules(WB)
Voc_string_cold = Voc_string(T=10°C, WB)                   skenario cold-morning (Voc max)
```

### 10.3 voc_ratio (dipakai detector)

```
voc_ratio = Voc_actual / Voc_string_nominal
```

Threshold: **> 0.95** → syarat emit `high_R` (M2b); **< 0.85** → indikasi ground fault.

---

## 11. Detector Berbasis Fisika

### 11.1 M2b Peer Z-score / High-R (`peer_zscore.py`, spec 4.2.1 + 4.2.3)

```
mask  = POA > 300 W/m²  (AND POA > floor 50, elevation > 5°, sebelum shutdown inverter)
R_str = V_string[mask] / I_string[mask].clip(lower=0.1)      (resistansi semu, Ohm)
z     = (R_str − median_peer(R_str)) / std_peer(R_str)        peer = sibling string 1 inverter
emit "high_R" jika |z| > 2.5  AND  voc_ratio > 0.95
confidence = min(90%, |z|/4 × 100%)
```

Peer hanya dalam satu inverter (PV1..PV28) — tidak cross-inverter (beda orientasi/MPPT/DC bus). Dual-stat: z_mean dan z_median sebagai cross-check; primary = median.

### 11.2 M2b Open-Circuit (`open_circuit.py`, `m2_config.yaml:88-106`)

```
gate : POA > 700 W/m² (AND floor 50, elevation > 5°)
flag : I_string < 5% × I_q95(peer)
debounce: ≥ 20 langkah 5-menit berurutan → genuine event ; confidence 95%
```

### 11.3 M2b Ground Fault (`ground_fault.py`, `m2_config.yaml:108-120`)

```
gate     : POA > 200 W/m²
absolute : |V_PV_to_ground| > 50 V
adaptive : z(V_to_ground vs fleet) > 3.0
pendukung: voc_ratio < 0.85 ; peer I_z > 2.0
```

Konfirmasi butuh insulation resistance test (tidak tersedia di SCADA).

### 11.4 M2e Availability (`availability.py:129-196`)

```
uptime% = 100 × n_ON / (n_ON + n_DOWN)        status transitional/UNKNOWN dikecualikan
severity: <90 CRITICAL | <95 HIGH | <97 MEDIUM | <99 INFO
```

String-proxy down: P_str < 0.1 kW SAAT median sibling aktif > 1.0 kW dan ≥ 50% sibling aktif, debounce 20 langkah.

### 11.5 M2a Shading (`m2a/shading.py`, config `m2a_shading`)

Per jam-of-day: CV antar-string dan PR-proxy. Sinyal shading = CV_h < 0.5×median(CV) **dan** PR_h < 0.85×median(PR). Klasifikasi asimetri AM/PM (split jam 12, threshold 0.5): `shading_morning` (bayangan timur), `shading_afternoon` (barat), `shading_uniform` (gejala mirip soiling).

### 11.6 M2a Low-Irradiance (`m2a/low_irradiance.py`, config `m2a_low_irradiance`)

Regresi linear PR_proxy vs POA di dua band: low (50–250 W/m²) dan mid (300–800 W/m²), min 30 sampel/band, R² ≥ 0.3. Slope_low < 0 dengan mid OK → `low_irradiance_underperform` (tanda series resistance tinggi); keduanya flagged → `general_underperform` (mirip soiling).

### 11.7 M2a Soiling SRR (`m2a/soiling.py` — skeleton, default OFF)

Metode Stochastic Rate and Recovery (rdtools, Deceglie 2018) atas deret PR harian:

```
pr_daily = E_harian / (insolasi_harian × C_kWp)          (IEC 61724-1, sama dgn §9)
sr, ci   = rdtools.soiling_srr(pr_daily, insolasi, [presipitasi], reps=1000, CL=68.2%)
p_loss   = 1 − sr
payback_hari = biaya_cleaning_IDR / (avg_kWh_30hr × tarif_IDR/kWh × p_loss)
rekomendasi cleaning bila payback < 30 hari
```

Diblokir sampai ≥ 90 hari (rekomendasi 180 hari) baseline PR via `BaselineAccumulator`. Tarif estimasi 1500 IDR/kWh; biaya cleaning belum diisi user.

### 11.8 Preprocessing Hampel (Wave 9, `preprocessing.py`, default ON)

Sebelum detector: Hampel filter pada V/I per string — window 15 sampel (~75 menit), threshold 3.0 MAD-sigma. Mengurangi outlier sensor sebelum masking.

---

## 12. Ringkasan Parameter Kunci (`config/m2_config.yaml`)

| Grup | Parameter | Nilai |
|---|---|---|
| PR | `capacity_kwp` (site) | 71 500 |
| PR | kapasitas per WB (notebook) | 7 150 (= site/10) |
| PR | `LOW_PR_THRESHOLD` (notebook) | 0.65 |
| POA | auto chain | per_ws → avg → ineichen |
| POA | transposisi | perez |
| M2b | `poa_threshold_wm2` / floor | 300 / 50 |
| M2b | `z_threshold` / `voc_ratio_threshold` | 2.5 / 0.95 |
| OC | `poa_threshold_wm2` / `i_ratio_threshold` | 700 / 0.05 |
| OC | debounce / confidence | 20 step / 95% |
| GF | `v_to_ground_abs` / `adaptive_z` / `voc_ratio` | 50 V / 3.0 / 0.85 |
| Daylight | `solar_elevation_min_deg` | 5.0 |
| M2e | severity uptime% | 90/95/97/99 |
| Hampel | window / max_deviation | 15 / 3.0 |
| Sampling | interval | 5 menit (dt = 0.08333 h) |

---

## 13. Verifikasi

Data mentah (`raw data input/`) tidak ada di repo, jadi reproduksi numerik penuh tidak mungkin dari sini. Yang bisa diverifikasi:

**13.1 Konsistensi internal `outputs/pr_daily_20260514.csv` (2026-05-14):**

```
pr_site            = 0.79430
mean(pr_WB01..10)  = 0.79438     selisih 0.00008 ✓
```

Ini sesuai struktur formula: `PR_site = ΣE_wb / (mean(H_wb) × C)` = rata-rata PR per-WB berbobot `H_wb/H̄`. Karena insolasi antar WB hampir sama pada hari itu, PR site ≈ rata-rata aritmetika PR per-WB. Cocok — rantai §9.2 terkonfirmasi konsisten dengan output aktual.

**13.2 Sanity formula P_expected** (smoke test `physics.py:349-363`): STC → 625 W/modul; hot noon (1000 W/m², 55 °C) → 570.6 W; string WB01 = 24×, WB05 = 26×. Konsisten dengan §5.

**13.3 Nilai 2026-05-14**: PR site 0.794 (range normal); terendah WB03 0.661, tertinggi WB06 0.936 (di atas 0.90 — per docstring patut dicurigai drift sensor/POA); `curtailment_flag = No` → tidak ada curtailment hari itu.

---

## 14. Temuan Reverse Engineering (perlu perhatian)

1. **Konflik koefisien suhu**: docstring `physics.py:20` menulis γ = −0.30 %/°C, tapi `panel_spec.yaml` (nilai operatif yang dipakai perhitungan) = **−0.29**. Efek kecil (~0.3 W/modul @55 °C) tapi dokumentasi menyesatkan. Yang benar diikuti runtime: −0.29.
2. **Bug laten di `physics.py:224`** *(sudah diperbaiki)*: `freq_hours: Optional[float]` dipakai tapi `Optional` tidak di-import (hanya `Union`). Tidak meledak saat runtime karena `from __future__ import annotations` (anotasi tidak dievaluasi), tapi akan gagal bila ada tooling yang memanggil `typing.get_type_hints()`. **Fix**: import di `physics.py:26` sudah diubah jadi `from typing import Optional, Union`.
3. **Bifacial gain tidak dimodelkan**: panel bifacial (faktor 80%) tapi P_expected dan kapasitas PR memakai rating front-side STC saja. POA juga hanya front-side. Akibat: P_expected/PR cenderung *underestimate* ekspektasi → PR aktual tampak lebih baik dari seharusnya; ΔP bisa bias positif.
4. **Kapasitas per-WB diasumsikan seragam** (71500/10): padahal WB01–02 pakai string 24 modul vs 26 di WB lain. Bila jumlah string per WB tidak persis mengompensasi, PR per-WB punya bias sistematik kecil antar WB.
5. **PR tidak dikoreksi suhu** (bukan weather-corrected PR' ala IEC 61724-1 lampiran): PR akan terlihat lebih rendah di hari panas tanpa berarti ada fault. Tcell sudah tersedia di pipeline kalau mau upgrade.
6. **Energi vs insolasi beda sumber**: E dari metered STS harian (Generation xlsx), H dari integrasi 5-menit pyranometer/pvlib. Gap meter vs integrasi (clock drift, missing rows pyranometer dengan fallback clear-sky yang *overestimate* insolasi saat berawan) langsung masuk ke PR.
7. **POA site = rata-rata 10 WB** yang dipetakan dari 5 WS → efektifnya rata-rata berbobot jumlah-WB per WS (WS-1 terhitung 3×, WS-3 1×), bukan rata-rata 5 WS murni.
8. **Linear single-factor P_expected**: tidak ada term low-light efficiency, spectral, atau IAM — wajar untuk baseline, tapi ΔP di POA rendah kurang akurat (sebagian dimitigasi gate POA > 300 di detector).

