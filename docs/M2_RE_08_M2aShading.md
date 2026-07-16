# M2 Reverse Engineering — Iterasi 8: M2aShading

**Modul**: `pv_pipeline/m2a/shading.py` (565 baris)
**Class utama**: `M2aShading(SubModule)` — `name = "M2a_shading"`
**Sifat**: detektor **fisika/statistik** (CV + PR-proxy + asimetri diurnal), **opt-in** (`enabled` default `False`; di config IKN `True`, `exclude_from_findings_sheet=True`).
**Spec referensi**: Master Context — whole-inverter shading (Fase 3 Part 2 Task #4; di luar rule §4.2.x utama)
**Dependency**: `POAProvider` (`get_poa`), `load_empty_pv_map`
**Output sheet Python**: `HourlyMetrics` (per inverter×jam) + `ShadingSummary` (per inverter)
**Output Excel workbook**: sheet `Raw_Data_SH`, `Helpers_SH`, `SH_Hourly`, `M2a_Shading` di `docs/M2_PV_Performance_Workbook.xlsx` (37 sheet saat iterasi 8; kini 46)
**Status verifikasi**: ✅ **Reproduksi PENUH** — CV/PR/asimetri/severity cocok eksak antara proto, recompute Python, dan formula Excel (`STDEVP`/`MEDIAN`/`AVERAGE`/`SUMPRODUCT`) + audit formula per sel + regen 0-diff. ⚠️ Live recalc LibreOffice tidak dijalankan (crash) — lihat Section 7.

---

## 1. Gambaran Shading

Detektor ini mendeteksi **shading seluruh-inverter** (bayangan terrain/bangunan, pita awan/kabut) — bukan shading parsial satu string (itu ranah M2b peer_zscore/open_circuit). Dua sinyal per **jam-of-day** digabung:

**1. CV (coefficient of variation) per jam** — variasi daya **antar string PV** dalam jam itu:

$$\mathrm{CV}_h = \operatorname{median}_t\left(\frac{\sigma_\text{pop}\big(P_1(t),\dots,P_n(t)\big)}{\overline{P}(t)}\right)$$

**CV rendah = underperformance seragam** lintas string (konsisten dengan shading seluruh-array). CV tinggi = campur, lebih cocok shading parsial (deteksi M2b).

**2. PR-proxy per jam** — daya inverter ternormalisasi POA:

$$\mathrm{PR}_h = \frac{\overline{P_\text{inv}}}{\overline{\mathrm{POA}}} \quad (\text{rata-rata dalam jam})$$

PR lebih rendah dari median harian = underperforming. **Jam mencurigakan** bila keduanya rendah:

$$\text{suspicious}_h := \big(\mathrm{CV}_h < 0.5\,\widetilde{\mathrm{CV}}\big) \;\wedge\; \big(\mathrm{PR}_h < 0.85\,\widetilde{\mathrm{PR}}\big)$$

dengan $\widetilde{\cdot}$ = median lintas jam (referensi per-inverter per-hari).

### Konfirmasi via asimetri diurnal

PLTS-IKN di lintang −0.99 (sedikit selatan ekuator), semua panel **hadap utara** (azimut 0, tilt 10°). Saat langit cerah, performa normal kira-kira **simetris AM vs PM**. Maka bila jam-mencurigakan terkonsentrasi **asimetris** (AM-saja / PM-saja), itu konsisten dengan **bayangan terrain** (matahari terhalang dari satu sisi hari); bila simetris, lebih cocok soiling/awan persisten (ranah M2a-Soiling).

$$\text{asymmetry} = \frac{|N_\text{am} - N_\text{pm}|}{\max(N_\text{am} + N_\text{pm},\,1)}, \qquad
\text{fault} =
\begin{cases}
\text{shading\_uniform} & \text{asymmetry} < 0.5 \\
\text{shading\_morning} & N_\text{am} > N_\text{pm} \\
\text{shading\_afternoon} & N_\text{pm} \ge N_\text{am}
\end{cases}$$

**Severity** = `score = 0.7·frac + 0.3·asymmetry` (frac = jam-mencurigakan/total jam): ≥0.6 CRITICAL, ≥0.4 HIGH, ≥0.2 MEDIUM. `confidence = 50 + asymmetry·50`.

---

## 2. Pipeline `M2aShading.run()` — Step by Step

### Langkah 1 — Opt-in & config (baris 334-354)

`enabled` default `False` → return `[]`. Bila aktif: `poa_threshold=100`, `hour_range=[6,18]`, `cv_low_multiplier=0.5`, `pr_low_multiplier=0.85`, `min_samples_per_hour=5`, `min_hours_for_analysis=4`, `am_pm_split_hour=12`, `asymmetry_threshold=0.5`.

### Langkah 2 — Per inverter: gate jam + POA (baris 380-421)

PV aktif = `1..pv_max` minus empty. Filter `hour_range` (`6 ≤ jam < 18`), lalu POA gate (`POA > 100`). **Catatan: gate shading lebih sederhana** — hanya jam + POA, **tanpa** solar-elevation/shutdown (berbeda dari open_circuit/ground_fault/low_irradiance).

### Langkah 3 — Matriks daya & metrik per jam (baris 423-430, `compute_hourly_metrics` 171-264)

`build_pv_power_matrix` (baris 133-168): `p_mat` (N_ts × N_pv) dari `PV{n} Power(kW)` (atau `V·I/1000`); `inv_total = nansum`. Per jam: CV per timestamp (`std/mean` antar PV finite & >0, butuh ≥2 PV), lalu **median** atas timestamp; `pr_proxy = mean(inv)/mean(POA)`; skip jam bila sampel < 5. Butuh ≥4 jam.

### Langkah 4 — Referensi & suspicious (baris 432-446)

`cv_median`/`pr_median` = median lintas jam. `cv_threshold = 0.5·cv_median`, `pr_threshold = 0.85·pr_median`. `suspicious = (cv < cv_threshold) & (pr_proxy < pr_threshold)`.

### Langkah 5 — Asimetri & klasifikasi (baris 448-461)

`n_am`/`n_pm` = jam-mencurigakan sebelum/sesudah `am_pm_split=12`. `classify_shading` (baris 267-284) + `_severity_from_counts` (baris 287-308).

### Langkah 6 — Emit (baris 463-542)

Satu finding **per jam mencurigakan** (`pv_string=None`, inverter-aggregate): `value=pr_proxy`, `threshold=pr_threshold`, `fault_type`, `confidence=50+asymmetry·50`. Artefak `HourlyMetrics` + `ShadingSummary`.

---

## 3. Worked Example — Numerik

Skenario (sheet `Raw_Data_SH`): 1 inverter, 6 PV, **8 jam** (08:00–15:00), 6 timestamp/jam = 48 baris. Jam **8/9/10 di-shade**: daya turun seragam (faktor antar-string nyaris sama → CV rendah) dan PR rendah.

### 3.1 Metrik per jam

| Jam | AM/PM | cv_hour | pr_proxy | suspicious |
|---|---|---|---|---|
| 8 | AM | **0.01291** | **0.01000** | ✓ |
| 9 | AM | 0.01291 | 0.01000 | ✓ |
| 10 | AM | 0.01291 | 0.01000 | ✓ |
| 11 | AM | 0.06455 | 0.02000 | ✗ |
| 12–15 | PM | 0.06455 | 0.02000 | ✗ |

`cv_median = 0.06455` → `cv_threshold = 0.5·0.06455 = 0.03227`. `pr_median = 0.020` → `pr_threshold = 0.85·0.020 = 0.017`. Jam shaded: `CV 0.01291 < 0.03227` **dan** `PR 0.010 < 0.017` → suspicious.

### 3.2 Asimetri & keputusan

`n_suspicious = 3`, semua AM → `N_am = 3, N_pm = 0`. `asymmetry = |3−0|/3 = 1.0 ≥ 0.5` dan `N_am > N_pm` → **`shading_morning`** (bayangan terrain sisi timur). `frac = 3/8 = 0.375`, `score = 0.7·0.375 + 0.3·1.0 = 0.5625` → **HIGH**. `confidence = 50 + 1.0·50 = 100`.

Asimetri inilah pembeda kunci: 3 jam mencurigakan terkonsentrasi pagi = bayangan terrain timur, **bukan** soiling (yang akan simetris AM≈PM → `shading_uniform`).

---

## 4. Pemetaan Python → Excel

Empat sheet, **semua formula live & faithful** (CV via `STDEVP`/`AVERAGE`, median jam via `MEDIAN`, agregat via `AVERAGE`/`SUMPRODUCT`).

### 4.1 `Helpers_SH` (per timestamp, 48 baris)

| Kolom | Formula |
|---|---|
| inv_total | `=SUM(Raw_Data_SH!E{r}:J{r})` |
| CV_ts | `=STDEVP(Raw_Data_SH!E{r}:J{r})/AVERAGE(Raw_Data_SH!E{r}:J{r})` |

### 4.2 `SH_Hourly` (per jam, 8 baris)

| Kolom | Formula |
|---|---|
| AM/PM | `=IF(hour<cfg_sh_am_pm_split,"AM","PM")` |
| cv_hour | `=MEDIAN(Helpers_SH!E{b0}:E{b1})` (blok 6 ts/jam) |
| pr_proxy | `=AVERAGE(Helpers_SH!inv)/MAX(AVERAGE(Helpers_SH!POA),1e-6)` |
| suspicious | `=IF(AND(cv_hour<M2a_Shading!$B$7,pr_proxy<M2a_Shading!$B$8),1,0)` |

### 4.3 `M2a_Shading` (keputusan; tabel metric/value)

| Metric | Formula |
|---|---|
| cv_median (B5) | `=MEDIAN(SH_Hourly!C5:C12)` |
| cv_threshold (B7) | `=cfg_sh_cv_mult*B5` |
| pr_threshold (B8) | `=cfg_sh_pr_mult*B6` |
| n_suspicious (B10) | `=SUM(SH_Hourly!H5:H12)` |
| n_am (B11) | `=SUMPRODUCT(SH_Hourly!H5:H12*(SH_Hourly!A5:A12<cfg_sh_am_pm_split))` |
| asymmetry (B13) | `=ABS(B11-B12)/MAX(B11+B12,1)` |
| fault_type (B14) | `=IF(B10=0,"no_shading",IF(B13<cfg_sh_asymmetry_thr,"shading_uniform",IF(B11>B12,"shading_morning","shading_afternoon")))` |
| score (B16) | `=B15*0.7+B13*0.3` (B15 = frac) |
| severity (B17) | `=IF(B10=0,"NORMAL",IF(B16>=0.6,"CRITICAL",IF(B16>=0.4,"HIGH",IF(B16>=0.2,"MEDIUM","INFO"))))` |
| confidence (B18) | `=50+B13*50` |

Conditional formatting mewarnai severity. Dependensi silang (cv_hour → cv_median → threshold → suspicious → n_suspicious) tidak sirkular.

---

## 5. Edge Cases & Limitasi Translasi

### 5.1 Gate faithful (tanpa ephemeris)

Berbeda dari open_circuit/ground_fault/low_irradiance, gate shading **hanya** `hour_range` + `POA>threshold` — **tidak ada** solar-elevation/shutdown. Maka demo (semua POA>100, jam 8–15 ∈ [6,18]) **sepenuhnya faithful** terhadap gate; tidak ada caveat ephemeris.

### 5.2 CV per-timestamp konstan dalam jam (demo)

`CV` adalah **median atas timestamp** dari CV-antar-string. Karena CV scale-invariant, demo memakai faktor antar-string $f$ yang **sama** untuk 6 timestamp dalam jam (hanya level yang bervariasi per-ts) → CV_ts identik → median = CV itu. Formula `MEDIAN` **faithful**; di data nyata $f$ bervariasi per-ts (shading berkedip / awan) sehingga median berperan robust. Demo menyederhanakan ini ke nilai konstan untuk reproduktifitas bersih.

### 5.3 Daya per-PV vs sumber

`P_inv` dihitung **dari** daya per-PV (`SUM(PV1..6)`) di sheet — bukan disediakan langsung (lebih faithful dari Iterasi 7). Production memakai `PV{n} Power(kW)` atau `V·I/1000`; CV memakai hanya daya **finite & >0** (demo semua positif, jadi `STDEVP` atas 6 sel = benar; bila ada slot 0/kosong, production mengecualikannya — `STDEVP` Excel memasukkan 0).

### 5.4 Yang disederhanakan

- **1 inverter, 1 hari**. Production loop semua inverter, window per-hari.
- **POA disediakan** di Raw_Data (production query provider).
- **6 PV** (demo) vs `pv_max=28`.

### 5.5 Mengapa CV (bukan rata-rata) untuk shading

Shading seluruh-array menurunkan **semua** string bersama → variasi antar-string mengecil (CV turun). Sebaliknya satu string rusak (open-circuit/high-R) → variasi antar-string membesar (CV naik). Jadi CV **rendah** memisahkan shading-uniform dari fault-parsial — yang terakhir ditangani M2b. Inilah alasan detektor beroperasi di level agregat inverter.

---

## 6. Cross-Check vs Spec & Config

| Aspek | Kode (`shading.py`) | Config (aktif) | Workbook |
|---|---|---|---|
| cv_low_multiplier | 0.5 | 0.5 | **identik** ✅ |
| pr_low_multiplier | 0.85 | 0.85 | **identik** ✅ |
| am_pm_split / asymmetry_thr | 12.0 / 0.5 | sama | **identik** ✅ |
| CV per jam | `median(std/mean)` | — | **reproduksi eksak** ✅ |
| severity | `0.7·frac + 0.3·asym` | — | **identik** ✅ |

**Catatan:**

1. **Tidak ada divergensi** config vs default vs workbook.
2. **Bukan rule §4.2.x utama** (Fase 3 Part 2 Task #4). Docstring eksplisit memisahkan: shading parsial satu string → M2b peer_zscore/open_circuit; shading-uniform simetris → kemungkinan soiling → **M2a Soiling** (Task #5).
3. **`exclude_from_findings_sheet=True`** (config IKN) — finding shading ke artefak saja, tidak ke Findings utama. Workbook mereplikasi pemisahan ini (sheet `SH_*`/`M2a_Shading` terpisah).
4. **Asimetri diurnal = kontribusi unik**: memanfaatkan geometri IKN (hadap utara, near-ekuator → AM≈PM normal) untuk membedakan terrain shadow (asimetris) dari soiling/awan (uniform).

---

## 7. Verification Log

| # | Cek | Metode | Hasil |
|---|---|---|---|
| 1 | Build 4 sheet baru | `_extend_m2_workbook_iter8.py` | 33 → 37 sheet, 31 sheet lama + Config/README append utuh ✅ |
| 2 | Audit string formula | `verify_iter8.py` (A) | Helpers (inv_total/CV_ts), SH_Hourly (cv_hour/pr/suspicious), M2a (median/asym/severity) == template ✅ |
| 3 | Recompute numerik | `verify_iter8.py` (B) | cv 0.01291/0.06455, pr 0.010/0.020, n_susp 3, asym 1.0, shading_morning HIGH cocok proto ✅ |
| 4 | Parity STDEVP | `np.std(ddof=0)` == `sqrt(mean dev²)` | ✅ (`STDEVP` populasi) |
| 5 | Reprodusibilitas | restore backup → rebuild → diff | **0 cell-diff** lintas 4 sheet + Config + README ✅ |
| 6 | Sheet lama utuh | diff vs backup | **0 diff** pada 31 sheet sebelumnya ✅ |

⚠️ Live recalc LibreOffice tidak dijalankan (crash sandbox). Verifikasi via audit formula + recompute Python + 0-diff regen. Fungsi Excel standar (`STDEVP`/`MEDIAN`/`AVERAGE`/`SUMPRODUCT`) → kepercayaan reproduksi tinggi.

---

## 8. Rekomendasi Penggunaan Workbook

- `M2a_Shading` menampilkan keputusan (fault_type + severity berwarna + asimetri). `SH_Hourly` menampilkan cv/pr/suspicious per jam; `Helpers_SH` CV per timestamp.
- Ganti `Raw_Data_SH` dengan daya per-PV & POA aktual (≥4 jam, ≥5 sampel/jam) → semua agregat update saat recalc.
- Ubah multiplier/threshold via `Config` (`cfg_sh_*`).
- `shading_uniform` (simetris) → lanjut ke analisis soiling; `shading_morning/afternoon` → cek obstruksi terrain sisi timur/barat.

---

## 9. Pertanyaan untuk Iterasi Berikutnya

1. **Iterasi 9 — M2a Soiling** menutup keluarga M2a, tapi ini **skeleton** yang memanggil `rdtools.soiling_srr()` (Stochastic Rate & Recovery, Monte-Carlo 1000-rep, butuh ≥90 hari data harian + presipitasi). Kendala seperti iForest: stokastik + lib eksternal → **tidak bisa formula Excel live**; hanya analisis ekonomi (payback = biaya_cleaning / loss_harian) yang Excel-friendly. Mau pendekatan dokumentasi+approksimasi ekonomi+caveat (seperti iForest), atau dokumentasi murni?
2. Setelah M2a lengkap, perlukah **dokumen ringkasan famili M2** (peta semua detektor → fault → severity → reproduksibilitas Excel)?
3. Apakah ada detektor M2 lain yang ingin diprioritaskan (mis. LSTM-AE yang sempat ditunda)?

---

## Sources

- `pv_pipeline/m2a/shading.py` (565 baris) — full read: `run()`, `compute_hourly_metrics`, `classify_shading`, `_severity_from_counts`, `build_pv_power_matrix`
- `pv_pipeline/core.py` — `M2Finding`, `Severity`, `SubModule`, `load_empty_pv_map`
- `config/m2_config.yaml` — `m2a_shading` (cv_mult 0.5, pr_mult 0.85, am_pm_split 12, asymmetry_threshold 0.5, poa_threshold 100)
- `docs/_extend_m2_workbook_iter8.py` (279 baris) — build script 4 sheet (CV/PR/asimetri live, regen 0-diff)
- `docs/verify_iter8.py` — audit string formula + recompute numerik vs proto
- `outputs/proto_iter8.py` — prototipe pengunci angka (deterministic; CV scale-invariant, shading_morning)
- `docs/M2_PV_Performance_Workbook.xlsx` — 37 sheet; `Raw_Data_SH`, `Helpers_SH`, `SH_Hourly`, `M2a_Shading`
- Verified: audit formula + Python recompute vs literal workbook + regen 0-diff (reproduksi PENUH, bukan approksimasi)
