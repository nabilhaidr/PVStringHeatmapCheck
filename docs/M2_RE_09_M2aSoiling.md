# M2 Reverse Engineering — Iterasi 9: M2aSoiling (SKELETON)

**Modul**: `pv_pipeline/m2a/soiling.py` (715 baris)
**Class utama**: `M2aSoiling(SubModule)` — `name = "M2a_soiling"`
**Sifat**: detektor soiling via **NREL rdtools SRR** (Stochastic Rate & Recovery, Monte-Carlo). **SKELETON** — `enabled` default `False`, **dan di config IKN juga `False`** (belum produksi; blocked pada data ≥90 hari + presipitasi).
**Spec referensi**: Master Context — soiling/cleaning (Fase 3 Part 2 Task #5); IEC 61724-1 (PR); Deceglie et al. 2018
**Dependency**: `rdtools.soiling.soiling_srr` (auto-install), `POAProvider`, `load_empty_pv_map`
**Output sheet Python**: `EconomicAnalysis` (1-row) + `SoilingRatio` (SR harian + CI) + `CleaningEvents` (dari rdtools)
**Output Excel workbook**: sheet `Raw_Data_SO`, `Helpers_SO`, `SO_Economics`, `SO_Summary` di `docs/M2_PV_Performance_Workbook.xlsx` (kini 41 sheet)

> ⚠️ **CAVEAT UTAMA — baca dulu.** Inti detektor = `rdtools.soiling_srr()` = **Monte-Carlo 1000-rep** (Stochastic Rate & Recovery) yang mengestimasi *soiling ratio* dari deret PR harian. Itu **black box stokastik — TIDAK bisa formula Excel** (dan rdtools tak tersedia di sandbox). Maka di workbook, **`soiling_ratio (sr)` = INPUT terdokumentasi** (output SRR). Namun **SEMUA lapisan hilir sr direproduksi PENUH & live** dan terverifikasi: PR harian, gate data-sufficiency, dan **seluruh ekonomi cleaning** (p_loss, daily_loss, payback, severity, recommend). Ini struktur sama dengan iForest (skor = black box, sisanya transparan).

**Status verifikasi**: ✅ PR harian + ekonomi (p_loss/daily_loss/payback/severity/confidence) cocok eksak antara proto, recompute Python, audit formula per sel + regen 0-diff. ⚠️ Bukan verifikasi terhadap output SRR rdtools (Monte-Carlo, tak tersedia) — lihat Section 7.

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

**Status skeleton**: dengan data < `min_days` (90), `run()` hanya meng-emit `insufficient_data` (INFO) dan **melewati** SRR — itu perilaku nyata saat ini (config IKN `enabled=false`).

---

## 2. Pipeline `M2aSoiling.run()` — Step by Step

### Langkah 1 — Opt-in & config (baris 440-458)

`enabled` default `False` → return `[]`. Bila aktif: `min_days=90`, `recommended_days=180`, `capacity_kwp=71500`, `cleaning_cost_idr=0` (**user wajib isi**), `electricity_tariff_idr=1500`, `payback_threshold=30`, `precipitation_path=""`, `rdtools_reps=1000`.

### Langkah 2 — Bangun deret PR harian (baris 480-488, `_build_daily_series` 377-438)

Per inverter: daya per-timestamp (`Active power(kW)` → Σ`PV{n} Power` → `V·I/1000`). Site-aggregate (sum energy, mean POA). `aggregate_daily` (baris 220-238) = Riemann sum (`Σ value·Δt`, `Δt=5/60` jam). `compute_daily_pr_series` (baris 241-259): `PR = E/(H·capacity)`, filter `0 ≤ PR ≤ 1.5`.

### Langkah 3 — Gate data-sufficiency (baris 491-527) ← perilaku utama skeleton

Bila `n_days < min_days` → emit `insufficient_data` (INFO, value=n_days) + artefak `EconomicAnalysis` status `insufficient_data`, **return**. Untuk data IKN saat ini (window pendek), inilah jalur yang diambil.

### Langkah 4 — rdtools SRR (baris 529-605)

`_ensure_rdtools()` auto-`pip install rdtools` (gagal → `insufficient_dependency`). `soiling.soiling_srr(energy_normalized_daily=pr_daily, insolation_daily, precipitation_daily, reps=1000, confidence_level=68.2)` → `sr`, `sr_ci`, `calc_info`. **Inilah Monte-Carlo black box.** `p_loss = 1 − sr`.

### Langkah 5 — Ekonomi + severity + emit (baris 607-694)

`avg_daily_kwh = mean(energy_daily.tail(30))`. `compute_cleaning_payback` (baris 286-301) → `daily_loss_idr`, `payback_days` (inf bila loss/biaya = 0). `recommend = payback < threshold`. `_severity_from_economics` (baris 262-283). `fault_type = cleaning_recommended | soiling_detected`. `confidence = 50 + sr·50`. Artefak `SoilingRatio`, `CleaningEvents`, `EconomicAnalysis`.

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

### 3.3 Gate skeleton

`n_days_assumed=120 ≥ 90` → `status=ok`. Bila `< 90` → `insufficient_data` dan **SRR + ekonomi tidak dijalankan** (jalur nyata saat ini).

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

### 5.2 Status SKELETON + rdtools tak tersedia

`soiling.py` adalah skeleton: `enabled=false` di config IKN, blocked pada (a) data ≥90 hari (rec 180) via `BaselineAccumulator`, (b) data presipitasi (BMKG) untuk membedakan cleaning hujan vs manual (penting di monsoon tropis — tanpa presipitasi, semua reset dianggap manual = false positive). rdtools juga gagal install di sandbox (seperti sklearn). Maka tak ada SRR referensi untuk dibandingkan.

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
| enabled | default False | **False** (skeleton) | — (demo) |
| min_days / payback_thr | 90 / 30 | 90 / 30 | **identik** ✅ |
| capacity_kwp / tariff | 71500 / 1500 | 71500 / 1500 | **identik** ✅ |
| cleaning_cost | 0 (placeholder) | 0 | **50.000.000 (demo)** ⚠️ |
| severity ladder | `_severity_from_economics` | — | **identik** ✅ |
| soiling ratio | rdtools SRR (Monte-Carlo) | — | **INPUT (black box)** ⚠️ |

**Catatan:**

1. **Skeleton, belum produksi** — `enabled=false`. Berbeda dari iForest/shading/low_irradiance (`enabled=true` di IKN).
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
- **Untuk soiling ratio sebenarnya**: kumpulkan ≥90 hari (≥180 ideal) via `BaselineAccumulator` + data presipitasi BMKG, lalu jalankan modul Python ber-rdtools. Workbook ini melengkapi sisi **ekonomi & PR**, bukan menggantikan SRR.
- Ingat `enabled=false`: detektor ini skeleton; aktifkan hanya setelah data cukup.

---

## 9. Pertanyaan untuk Iterasi Berikutnya

Keluarga **M2a lengkap** (shading, soiling, low_irradiance) — bersama M2e, M2b (peer_zscore, open_circuit, ground_fault), dan M2_iforest, ini menutup hampir semua detektor M2. Pilihan berikutnya:

1. **Dokumen ringkasan famili M2** — satu peta: tiap detektor → sinyal → fault_type → severity → **status reproduksibilitas Excel** (penuh / approksimasi / input-only). Berguna sebagai indeks workbook 41-sheet.
2. **LSTM-AE** (yang sempat ditunda di Iterasi 7) — autoencoder PyTorch; kendala seperti iForest/SRR (jaringan terlatih, error rekonstruksi = black box). Pendekatan dokumentasi + caveat.
3. Detektor M2 lain yang belum tercakup, atau revisit/perdalam yang sudah ada?

---

## Sources

- `pv_pipeline/m2a/soiling.py` (715 baris) — full read: `run()`, `_build_daily_series`, `compute_daily_pr_series`, `compute_cleaning_payback`, `_severity_from_economics`, `aggregate_daily`
- `pv_pipeline/core.py` — `M2Finding`, `Severity`, `SubModule`, `load_empty_pv_map`
- `config/m2_config.yaml` — `m2a_soiling` (enabled false, min_days 90, capacity 71500, tariff 1500, cleaning_cost 0, payback 30)
- `docs/_extend_m2_workbook_iter9.py` (252 baris) — build script 4 sheet (PR + ekonomi live, regen 0-diff)
- `docs/verify_iter9.py` — audit string formula + recompute numerik (ekonomi) vs proto
- `outputs/proto_iter9.py` — prototipe pengunci angka (deterministic; PR harian + 4 skenario sr)
- `docs/M2_PV_Performance_Workbook.xlsx` — 41 sheet; `Raw_Data_SO`, `Helpers_SO`, `SO_Economics`, `SO_Summary`
- rdtools.soiling docs; Deceglie et al. 2018 "Quantifying Soiling Loss Directly from PV Yield"; IEC 61724-1:2021 (PR)
- Verified: audit formula + Python recompute (PR + ekonomi) vs literal workbook + regen 0-diff (SRR rdtools N/A — sr = input, Monte-Carlo)
