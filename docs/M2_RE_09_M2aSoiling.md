# M2 Reverse Engineering — Iterasi 9: M2aSoiling

**Modul**: `pv_pipeline/m2a/soiling.py` (1818 baris per 2026-07-11; 715 baris saat iterasi 9 ditulis) + `pv_pipeline/m2a/cleaning_report.py` (rekap manual cleaning)
**Class utama**: `M2aSoiling(SubModule)` — `name = "M2a_soiling"`
**Sifat**: detektor soiling via **NREL rdtools SRR** (Stochastic Rate & Recovery, Monte-Carlo). `enabled` default `False` dan di config IKN masih `False` untuk pipeline harian, **tetapi sejak 2026-07 modul sudah matang sebagai analysis run standalone** (`run_soiling_analysis.py` + `notebook/M2aSoiling.ipynb`) atas data multi-bulan — run nyata: `soiling_srr_20250103_20260430*.xlsx`. Lihat **§0 Update Modul 2026-07**.
**Spec referensi**: Master Context — soiling/cleaning (Fase 3 Part 2 Task #5); IEC 61724-1 (PR); Deceglie et al. 2018
**Dependency**: `rdtools.soiling.soiling_srr` (auto-install), `POAProvider`, `CellTempProvider` + `PanelSpec` (koreksi suhu), `load_empty_pv_map`
**Output sheet Python**: `EconomicAnalysis` (1-row) + `SoilingRatio` (SR harian + CI) + `CleaningEvents` — ditambah sejak 2026-07: `PRDaily`, `AvailabilityMask`, `ManualCleaning`, `CleaningImpact`, `DirectCleaningImpact`, `DirectCleaningImpactPerString`, `PerInverterSRR` (opt-in), `MonthlySoilingLoss`
**Output Excel workbook**: sheet `Raw_Data_SO`, `Helpers_SO`, `SO_Economics`, `SO_Summary` di `docs/M2_PV_Performance_Workbook.xlsx` (kini **46** sheet setelah iterasi 11; 4 sheet SO tidak berubah)

> ⚠️ **CAVEAT UTAMA — baca dulu.** Inti detektor = `rdtools.soiling_srr()` = **Monte-Carlo 1000-rep** (Stochastic Rate & Recovery) yang mengestimasi *soiling ratio* dari deret PR harian. Itu **black box stokastik — TIDAK bisa formula Excel** (dan rdtools tak tersedia di sandbox). Maka di workbook, **`soiling_ratio (sr)` = INPUT terdokumentasi** (output SRR). Namun **SEMUA lapisan hilir sr direproduksi PENUH & live** dan terverifikasi: PR harian, gate data-sufficiency, dan **seluruh ekonomi cleaning** (p_loss, daily_loss, payback, severity, recommend). Ini struktur sama dengan iForest (skor = black box, sisanya transparan).

**Status verifikasi**: ✅ PR harian + ekonomi (p_loss/daily_loss/payback/severity/confidence) cocok eksak antara proto, recompute Python, audit formula per sel + regen 0-diff. ⚠️ Bukan verifikasi terhadap output SRR rdtools (Monte-Carlo, tak tersedia) — lihat Section 7. ⚠️ Verifikasi tsb. merekam **iterasi 9 (2026-06-12)** — masih valid untuk lapisan PR+ekonomi & 4 sheet SO; ekstensi modul 2026-07 (§0) **belum** dicakup workbook.

---

## 0. Update Modul 2026-07 (pasca-iterasi 9)

Sepuluh commit 2026-07-06..07-10 memperluas modul 715 → 1818 baris. Ringkasan per fitur (kronologis):

1. **Rekap manual cleaning** (185b09c) — `cleaning_report_path` (checklist TRUE per string per tanggal, sheet `STSn`, via `m2a/cleaning_report.py`) + `dc_cable_list_path` (mapping nomor String checklist → nomor PV Huawei untuk WB03–WB10). Artifact `ManualCleaning`.
2. **Analysis run per kelompok WB** (c5a4842) — `wb_filter` / CLI `--wb --capacity-kwp` (referensi: WB01–02 ~13.500 kWp, WB03–10 ~58.000 kWp); manual cleaning dibatasi ke kelompok yang dianalisis.
3. **Knob segmentasi SRR** (d786e77) + **reindex `freq='D'`** sebelum `soiling_srr` (0c178a9) — `rdtools_clean_criterion` (default `"shift"`; opsi `precip_and_shift` dst.), `rdtools_precip_threshold` (0.01 mm), `rdtools_min_interval_length` (7 hari), `rdtools_day_scale` (13). Di iklim monsoon dengan PR berisik, ini mengurangi `NoValidIntervalError`. Plus notebook analysis run `notebook/M2aSoiling.ipynb`.
4. **CleaningImpact** (5473ed9) — kenaikan SR (`sr_gain_pp`, poin persen) per event/interval + klasifikasi cause hujan vs manual (`cleaning_precip_threshold_mm` 1.0; match window ±`cleaning_match_window_days` 3) + analisis per-WB.
5. **DirectCleaningImpact** (6fdcc69) — pre/post PR di sekitar campaign cleaning manual (`direct_impact_window_days` 7, `direct_impact_gap_days` 7), **independen SRR** (tetap terisi walau `NoValidIntervalError`).
6. **Koreksi suhu PR sebelum SRR** (653be85) — `temperature_correction: true` (default), `CF = 1 + (γ/100)(Tcell − 25)` dengan γ dari `temp_coef_pmax_pct_per_c` (−0.29 %/°C Jinko JKM625N; kosong = dari panel_spec). PR = E/(H·C·CF) → depresi suhu musiman hilang, soiling terisolasi; `Temp_Loss` (insolation-weighted, %) eksplisit di `EconomicAnalysis`.
7. **DirectCleaningImpactPerString + PerInverterSRR** (92bd9f1) — per-string (jendela sama dgn #5) dan `soiling_srr` per inverter (~194 unit; MAHAL — opt-in `per_inverter_srr`, `per_inverter_reps` 200).
8. **Mask availability M2e + monthly loss + rekomendasi cleaning + plot sawtooth** (9720195) — `availability_dir` (folder `m2_findings_*.jsonl/.xlsx` output daily notebook) + `availability_min_uptime_pct` (95): inverter-day uptime rendah di-drop dari energi **dan** kapasitasnya keluar dari penyebut PR (capacity factor) → outage parsial tidak terbaca sebagai soiling. Artifacts `AvailabilityMask`, `MonthlySoilingLoss`, `PRDaily` (deret harian untuk plot sawtooth dari xlsx tanpa run ulang), + `build_cleaning_recommendation`.

`fault_type` kini 5: `soiling_detected` / `cleaning_recommended` / `insufficient_data` / `insufficient_dependency` / **`rdtools_error`** (baru). Key tarif di config: `electricity_tariff_idr_per_kwh`.

> Catatan: docstring modul `soiling.py` masih berlabel "SKELETON ONLY (2026-05-23)" — tertinggal dari implementasi aktual. §2 di bawah direvisi ke alur 2026-07; §3–§7 adalah rekaman verifikasi iterasi 9 (tetap valid untuk lapisan yang dicakupnya).

---

## 1. Gambaran Soiling

**Soiling** = penurunan kinerja akibat debu/kotoran menutup panel. Tantangannya: memisahkan penurunan soiling (gradual, lalu pulih saat hujan/cleaning) dari penurunan lain (degradasi, sensor). NREL **SRR (Stochastic Rate and Recovery)** memodelkan deret **PR harian** sebagai segmen-segmen *laju penurunan* (soiling) yang di-*reset* oleh *recovery* (hujan/cleaning), lalu menjalankan **Monte-Carlo 1000 simulasi** untuk mengestimasi **soiling ratio** $sr \in [0,1]$ berbobot-insolasi + interval kepercayaan.

PR harian (IEC 61724-1):

$$\mathrm{PR}_d = \frac{E_d}{H_d \cdot C_\text{kWp}}, \qquad E_d, H_d = \text{integral Riemann harian} = \sum v \cdot \Delta t$$

Dari $sr$, kerugian soiling: $p_\text{loss} = 1 - sr$ (fraksi energi hilang). Lalu **analisis ekonomi cleaning**:

$$\text{daily\_loss} = \overline{\text{kWh}}_\text{harian} \cdot \text{tarif} \cdot p_\text{loss}, \qquad
\text{payback} = \frac{\text{biaya\_cleaning}}{\text{daily\_loss}}$$

Rekomendasi cleaning bila `payback < payback_threshold` (30 hari). **Severity** dari (p_loss, payback):

$$\text{severity} =
\begin{cases}
\text{CRITICAL} & p_\text{loss} \ge 0.10 \text{ dan } \text{payback} < \text{thr}/3 \\
\text{HIGH} & p_\text{loss} \ge 0.05 \text{ dan } \text{payback} < \text{thr} \\
\text{MEDIUM} & p_\text{loss} \ge 0.02 \text{ dan } \text{payback} < 2\,\text{thr} \\
\text{INFO} & \text{lainnya}
\end{cases}$$

**Gate data**: dengan data < `min_days` (90), `run()` hanya meng-emit `insufficient_data` (INFO) dan **melewati** SRR. Di pipeline harian (config IKN `enabled=false`) detektor memang tidak jalan; jalur produksinya adalah **analysis run standalone multi-bulan** via `run_soiling_analysis.py` (lihat §0).

---

## 2. Pipeline `M2aSoiling.run()` — Step by Step (alur 2026-07; `run()` mulai baris 1315)

### Langkah 1 — Opt-in & config

`enabled` default `False` → return `[]`. Knob inti: `min_days=90`, `recommended_days=180`, `capacity_kwp=71500`, `cleaning_cost_idr=0` (**user wajib isi**), `electricity_tariff_idr_per_kwh=1500`, `payback_threshold_days=30`, `precipitation_path=""`, `rdtools_reps=1000`. Knob baru 2026-07: `temperature_correction=true`, `availability_dir`/`availability_min_uptime_pct`, `cleaning_report_path`/`dc_cable_list_path`, `cleaning_match_window_days`/`cleaning_precip_threshold_mm`, `direct_impact_window_days`/`direct_impact_gap_days`, `per_inverter_srr`/`per_inverter_reps`, `wb_filter`, `rdtools_clean_criterion`/`rdtools_precip_threshold`/`rdtools_min_interval_length`/`rdtools_day_scale` (lihat §0).

### Langkah 2 — Mask availability M2e (opsional, baru)

Bila `availability_dir` diisi: baca uptime per inverter-day dari `m2_findings_*.jsonl/.xlsx` → `build_availability_mask` (uptime < 95% → inverter-day masuk mask). Baris ter-mask keluar dari energi harian **dan** kapasitasnya keluar dari penyebut PR hari itu (capacity factor) → outage parsial tidak terbaca sebagai soiling. Artifact `AvailabilityMask`.

### Langkah 3 — Bangun deret PR harian (`_build_daily_series`, temperature-corrected)

Per inverter: daya per-timestamp (`Active power(kW)` → Σ`PV{n} Power` → `V·I/1000`), hormati mask availability. Site-aggregate (sum energy, mean POA). `aggregate_daily` = Riemann sum (`Σ value·Δt`, `Δt=5/60` jam). `temp_correction_factor`: `CF = 1 + (γ/100)(Tcell − 25)`. `compute_daily_pr_series`: **`PR = E/(H·capacity·CF_temp·CF_cap)`** (kedua faktor opsional), filter `0 ≤ PR ≤ 1.5`. Artifact `PRDaily` (energy, insolation, temp_factor, capacity_factor, pr per hari — emit juga saat insufficient_data) + `Temp_Loss` insolation-weighted (%).

### Langkah 4 — Gate data-sufficiency

Bila `n_days < min_days` → emit `insufficient_data` (INFO, value=n_days) + artefak `EconomicAnalysis` status `insufficient_data`, **return**. Jalur ini yang diambil pipeline harian; analysis run multi-bulan (≥90 hari) lolos ke SRR.

### Langkah 5 — rdtools SRR

`_ensure_rdtools()` auto-`pip install rdtools` (gagal → `insufficient_dependency`). `reindex_daily_frequency` ke `freq='D'` dulu (fix 0c178a9), lalu `soiling.soiling_srr(energy_normalized_daily=pr_daily, insolation_daily, precipitation_daily, reps=1000, confidence_level=68.2, clean_criterion=..., precip_threshold=..., min_interval_length=..., day_scale=...)` → `sr`, `sr_ci` (np.ndarray [lower, upper] via `_ci_bounds`), `calc_info`. **Inilah Monte-Carlo black box.** Exception rdtools → finding `rdtools_error`. `p_loss = 1 − sr`.

### Langkah 6 — Ekonomi + severity + emit

`avg_daily_kwh = mean(energy_daily.tail(30))`. `compute_cleaning_payback` → `daily_loss_idr`, `payback_days` (inf bila loss/biaya = 0). `recommend = payback < threshold`. `_severity_from_economics`. `fault_type = cleaning_recommended | soiling_detected`. `confidence = 50 + sr·50`. Artefak `SoilingRatio` (+ `summarize_soiling_profiles`), `CleaningEvents` (klasifikasi cause hujan vs manual), `EconomicAnalysis` (+ Temp_Loss).

### Langkah 7 — Artifacts hilir cleaning (baru)

Bila `cleaning_report_path` diisi: `ManualCleaning` (rekap checklist), `CleaningImpact` (`sr_gain_pp` per event/interval SRR), `DirectCleaningImpact` + `DirectCleaningImpactPerString` (pre/post PR sekitar campaign, independen SRR; kolom `soiling_loss_pct`/`rank_soiling_loss`), `MonthlySoilingLoss`, rekomendasi cleaning (`build_cleaning_recommendation`, string mati ditandai `status=DEAD_OR_OFFLINE` dan keluar dari ranking), dan `PerInverterSRR` bila opt-in.

---

## 3. Worked Example — Numerik (ekonomi LIVE; sr = input)

Skenario: 12 hari demo energi+insolasi (ilustrasi PR harian) + **kalkulator cleaning ROI** dengan 4 nilai `sr` (output SRR sebagai input). `capacity=71500`, `tarif=1500`, `cleaning_cost=50.000.000` (demo), `payback_thr=30`, `n_days_assumed=120` (≥90 → gate `ok`).

### 3.1 PR harian & avg_daily_kwh

`PR_daily[0] = 310000/(5.40·71500) = `**0.80290**. `avg_daily_kwh = AVERAGE(12 hari) = `**314.250 kWh/hari**.

### 3.2 Kalkulator ekonomi (4 skenario sr)

| sr (INPUT) | p_loss | daily_loss (IDR) | payback (hari) | recommend | severity | confidence |
|---|---|---|---|---|---|---|
| 0.850 | 0.150 | 70.706.250 | 0.707 | YES | **CRITICAL** | 92.5 |
| 0.920 | 0.080 | 37.710.000 | 1.326 | YES | **HIGH** | 96.0 |
| 0.970 | 0.030 | 14.141.250 | 3.536 | YES | **MEDIUM** | 98.5 |
| 0.995 | 0.005 | 2.356.875 | 21.215 | YES | INFO | 99.75 |

Contoh `sr=0.92`: `p_loss = 1−0.92 = 0.08`; `daily_loss = 314250·1500·0.08 = 37.710.000 IDR/hari`; `payback = 50.000.000/37.710.000 = 1.33 hari < 30` → **cleaning_recommended**; `p_loss 0.08 ≥ 0.05 ∧ payback 1.33 < 30` → **HIGH**.

Catatan: `sr=0.995` menunjukkan **decoupling** — payback 21 hari `< 30` jadi `recommend=YES`, tapi `p_loss 0.5%` kecil → severity **INFO** (ekonomis layak, tapi kerugian minim). Severity mengukur urgensi, recommend mengukur kelayakan ROI.

### 3.3 Gate data-sufficiency

`n_days_assumed=120 ≥ 90` → `status=ok`. Bila `< 90` → `insufficient_data` dan **SRR + ekonomi tidak dijalankan**.

---

## 4. Pemetaan Python → Excel

Empat sheet. **Semua hilir `sr` = formula live**; `sr` = input (black box SRR).

### 4.1 `Helpers_SO` — PR harian

| Kolom | Formula |
|---|---|
| PR_daily | `=Raw_Data_SO!B{r}/(Raw_Data_SO!C{r}*cfg_so_capacity_kwp)` |
| avg_daily_kwh | `=AVERAGE(B5:B16)` (proxy tail-30) |

### 4.2 `SO_Economics` — kalkulator (sr = input ter-highlight)

| Kolom | Formula |
|---|---|
| data_status | `=IF(n_days<cfg_so_min_days,"insufficient_data","ok")` |
| p_loss | `=1-sr` |
| daily_loss_idr | `=avg_daily_kwh*cfg_so_tariff*p_loss` |
| payback_days | `=IF(AND(daily_loss>0,cfg_so_cleaning_cost>0),cfg_so_cleaning_cost/daily_loss,1E+99)` |
| recommend | `=IF(payback<cfg_so_payback_thr,"YES","no")` |
| fault_type | `=IF(recommend="YES","cleaning_recommended","soiling_detected")` |
| severity | `=IF(AND(p_loss>=0.1,payback<thr/3),"CRITICAL",IF(AND(p_loss>=0.05,payback<thr),"HIGH",IF(AND(p_loss>=0.02,payback<2*thr),"MEDIUM","INFO")))` |
| confidence | `=50+sr*50` |

Conditional formatting mewarnai severity. `SO_Summary` = status + caveat SRR + hitung `recommend`.

---

## 5. Edge Cases & Limitasi Translasi

### 5.1 SRR Monte-Carlo bukan formula Excel (caveat inti)

`soiling_srr` menjalankan 1000 simulasi stokastik (segmentasi laju + recovery, bootstrap CI). Tidak ada cara jujur menaruh ini sebagai formula spreadsheet. Maka **`sr` = input** di `SO_Economics` (di-highlight). Yang **faithful & live**: PR harian, gate, dan seluruh ekonomi hilir `sr`. Untuk `sr` nyata, jalankan modul Python ber-rdtools; workbook ini untuk **transparansi metrik PR + keputusan ekonomi cleaning**.

### 5.2 Status modul + rdtools tak tersedia di sandbox

Saat iterasi 9 ditulis, `soiling.py` adalah skeleton yang blocked pada (a) data ≥90 hari, (b) data presipitasi. **Update 2026-07: keduanya terpenuhi di jalur analysis run** — SRR dijalankan atas data 2025-01-03..2026-04-30 dengan presipitasi harian (`precipitation_daily_plts_ikn.csv`) untuk membedakan cleaning hujan vs manual. `enabled` tetap `false` di config IKN untuk pipeline harian (by design: SRR butuh window multi-bulan, bukan harian). rdtools tetap gagal install di sandbox dokumentasi (seperti sklearn), maka workbook tetap tanpa SRR referensi.

### 5.3 Asumsi demo

- **`n_days_assumed=120`** dipakai agar gate `ok` dan ekonomi terdemonstrasi; window PR demo hanya **12 hari** (ilustrasi metrik, bukan cukup untuk SRR).
- **`avg_daily_kwh = AVERAGE(12 hari)`** sebagai proxy `mean(tail(30))`.
- **`cleaning_cost = 50.000.000 IDR`** (demo). Default config **0** (`USER MUST PROVIDE`) — dengan 0, `payback = ∞` → tak pernah recommend. Ini divergensi yang saya buat **khusus demo** (didokumentasikan).

### 5.4 Reproduksi ekonomi = PENUH

`p_loss`, `daily_loss`, `payback`, `recommend`, `severity`, `confidence`, dan PR harian: semua **eksak** vs `compute_cleaning_payback` / `_severity_from_economics` / `compute_daily_pr_series` (verifikasi selisih < 1 IDR / < 1e-2 hari). Tidak ada approksimasi di lapisan ini.

---

## 6. Cross-Check vs Spec & Config

| Aspek | Kode (`soiling.py`) | Config IKN | Workbook |
|---|---|---|---|
| enabled | default False | **False** (pipeline harian; analysis run standalone = jalur produksi) | — (demo) |
| min_days / payback_thr | 90 / 30 | 90 / 30 | **identik** ✅ |
| capacity_kwp / tariff | 71500 / 1500 | 71500 / 1500 | **identik** ✅ |
| cleaning_cost | 0 (placeholder) | 0 | **50.000.000 (demo)** ⚠️ |
| severity ladder | `_severity_from_economics` | — | **identik** ✅ |
| soiling ratio | rdtools SRR (Monte-Carlo) | — | **INPUT (black box)** ⚠️ |
| temperature_correction (2026-07) | `temp_correction_factor`, γ −0.29 %/°C | **true** | — (belum di workbook) ⚠️ |
| mask availability M2e (2026-07) | `build_availability_mask`, uptime < 95% | `availability_dir` kosong (via `--availability-dir`) | — (belum di workbook) ⚠️ |
| segmentasi SRR (2026-07) | clean_criterion/precip_thr/min_interval/day_scale | shift / 0.01 / 7 / 13 | — (parameter black box) |

**Catatan:**

1. **`enabled=false` di pipeline harian** — berbeda dari iForest/shading/low_irradiance (`enabled=true` di IKN). Jalur produksi soiling = analysis run standalone multi-bulan (§0), bukan pipeline harian.
2. **`cleaning_cost` default 0**: tanpa input user, `daily_loss>0` tapi `cost=0` → `payback=∞` → `recommend=no` selalu. Workbook memakai 50 juta sebagai demo agar payback finite (didokumentasikan §5.3).
3. **Bukan rule §4.2.x**; Fase 3 Part 2 Task #5. Melengkapi `general_underperform` dari M2a Low-Irradiance (Iterasi 7) yang mengarahkan kasus drop-menyeluruh ke analisis soiling ini.

---

## 7. Verification Log

| # | Cek | Metode | Hasil |
|---|---|---|---|
| 1 | Build 4 sheet baru | `_extend_m2_workbook_iter9.py` | 37 → 41 sheet, 35 sheet lama + Config/README append utuh ✅ |
| 2 | Audit string formula | `verify_iter9.py` (A) | Helpers (PR_daily, avg_kwh) + SO_Economics (p_loss/daily_loss/payback/severity) == template ✅ |
| 3 | Recompute numerik | `verify_iter9.py` (B) | PR_daily 0.8029, avg 314250, 4 severity tier (CRIT/HIGH/MED/INFO), payback 0.71–21.2 cocok proto ✅ |
| 4 | Parity smoke | vs `soiling.py __main__` | `payback(0.05,50000,cost 10M)=10M/3.75M` ✅ |
| 5 | Gate sufficiency | n_days vs min_days | 120≥90→ok ; 60<90→insufficient_data ✅ |
| 6 | Reprodusibilitas | restore backup → rebuild → diff | **0 cell-diff** lintas 4 sheet + Config + README ✅ |
| 7 | Sheet lama utuh | diff vs backup | **0 diff** pada 35 sheet sebelumnya ✅ |

⚠️ **Bukan verifikasi terhadap SRR rdtools.** rdtools tak ter-install (seperti sklearn) + LibreOffice crash. Yang diverifikasi: PR harian + lapisan ekonomi (live, eksak). Untuk SRR sebenarnya, jalankan `pv_pipeline/m2a/soiling.py` di environment ber-rdtools dengan ≥90 hari data.

---

## 8. Rekomendasi Penggunaan Workbook

- `SO_Economics` adalah **kalkulator keputusan cleaning**: masukkan `sr` (dari SRR / estimasi), `cleaning_cost`, dan lihat payback + rekomendasi + severity. Ubah `cfg_so_*` di Config.
- `Helpers_SO` menunjukkan PR harian; ganti `Raw_Data_SO` dengan energi+insolasi harian aktual (≥90 hari untuk SRR nyata).
- **Untuk soiling ratio sebenarnya**: jalankan `run_soiling_analysis.py` (opsi `--wb`, `--capacity-kwp`, `--availability-dir`, `--per-inverter-srr`) atau `notebook/M2aSoiling.ipynb` atas data ≥90 hari + presipitasi — sudah dilakukan untuk 2025-01..2026-04. Workbook ini melengkapi sisi **ekonomi & PR**, bukan menggantikan SRR.
- Ingat `enabled=false` di pipeline harian: soiling dianalisis lewat jalur analysis run multi-bulan, bukan per hari.

---

## 9. Pertanyaan untuk Iterasi Berikutnya

Keluarga **M2a lengkap** (shading, soiling, low_irradiance) — bersama M2e, M2b (peer_zscore, open_circuit, ground_fault), dan M2_iforest, ini menutup hampir semua detektor M2. *(Update 2026-07: opsi 1 dan 2 di bawah sudah terlaksana — `M2_Family_Summary.md` dan `M2_RE_10` LSTM-AE; menyusul `M2_RE_11` MpptRatio.)*

1. ~~**Dokumen ringkasan famili M2**~~ — selesai (`M2_Family_Summary.md`).
2. ~~**LSTM-AE**~~ — selesai (`M2_RE_10`); model bahkan sudah dilatih & wired (2026-07-06).
3. Kandidat tersisa: mencakup ekstensi soiling 2026-07 (§0) ke workbook, atau validasi detektor terhadap data aktual.

---

## Sources

- `pv_pipeline/m2a/soiling.py` (1818 baris per 2026-07-11) — `run()`, `_build_daily_series`, `compute_daily_pr_series`, `compute_cleaning_payback`, `_severity_from_economics`, `aggregate_daily`, + fungsi 2026-07: `temp_correction_factor`, `build_availability_mask`, `build_cleaning_impact`, `build_direct_cleaning_impact(_per_string)`, `build_monthly_soiling_loss`, `build_cleaning_recommendation`, `reindex_daily_frequency`
- `pv_pipeline/m2a/cleaning_report.py` — rekap manual cleaning (checklist STSn + mapping DC cable ST→PV)
- `run_soiling_analysis.py` + `notebook/M2aSoiling.ipynb` — runner analysis run standalone; run nyata `coba/soiling_srr_20250103_20260430*.xlsx`
- `pv_pipeline/core.py` — `M2Finding`, `Severity`, `SubModule`, `load_empty_pv_map`
- `config/m2_config.yaml` — `m2a_soiling` (enabled false, min_days 90, capacity 71500, tariff 1500, cleaning_cost 0, payback 30, + knob 2026-07 §0)
- `docs/_extend_m2_workbook_iter9.py` (252 baris) — build script 4 sheet (PR + ekonomi live, regen 0-diff)
- `docs/verify_iter9.py` — audit string formula + recompute numerik (ekonomi) vs proto
- `outputs/proto_iter9.py` — prototipe pengunci angka (deterministic; PR harian + 4 skenario sr)
- `docs/M2_PV_Performance_Workbook.xlsx` — kini 46 sheet; `Raw_Data_SO`, `Helpers_SO`, `SO_Economics`, `SO_Summary`
- rdtools.soiling docs; Deceglie et al. 2018 "Quantifying Soiling Loss Directly from PV Yield"; IEC 61724-1:2021 (PR)
- Verified: audit formula + Python recompute (PR + ekonomi) vs literal workbook + regen 0-diff (SRR rdtools N/A — sr = input, Monte-Carlo)
