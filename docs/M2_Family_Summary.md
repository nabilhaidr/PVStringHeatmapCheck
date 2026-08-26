# M2 — Ringkasan Famili Detektor & Indeks Workbook

**Tujuan**: satu peta untuk seluruh kerja reverse-engineering M2 — tiap detektor → sinyal → `fault_type` → severity → **status reproduksibilitas Excel**. Berfungsi sebagai indeks untuk `docs/M2_PV_Performance_Workbook.xlsx` (kini **46 sheet**, termasuk tab `M2_Index`) dan 11 dokumen RE.
**Cakupan**: 10 detektor M2 — Iterasi 2–11 (termasuk M2bMpptRatio, iterasi 11, dan M2bIntermittent LSTM-AE yang kini **terlatih**). Sumber kebenaran: `pv_pipeline/*.py`, `config/m2_config.yaml`, dan tiap `docs/M2_RE_XX_*.md`.
**Tanggal**: 2026-05-30 · **Update terakhir**: 2026-07-11 (+MpptRatio/iter-11; LSTM-AE terlatih & wired; ekstensi soiling 2026-07; 42→46 sheet).

---

## 1. Peta Detektor (10 detektor)

| # | Detector | Sinyal utama | `fault_type` | Severity (ringkas) | Sheet keputusan | Status Excel |
|---|---|---|---|---|---|---|
| 1 | **M2eAvailability** | uptime inverter & string (downtime menit) | *(severity-based)* | <90 CRIT · <95 HIGH · <97 MED · <99 INFO · ≥99 NORMAL | `M2e_Availability` | **PENUH** |
| 2 | **M2bPeerZScore** | `R_str=V/I` → z-score antar-string + `voc_ratio` | `high_R` | `|z|>3.5` HIGH · `|z|>2.5` & voc>0.95 MED | `M2b_PeerZScore` | **PENUH** |
| 3 | **M2bOpenCircuit** | `I/I_q95 < 5%` (across siblings) + debounce 20 (~100 mnt) | `open_circuit` | CRITICAL (conf 95%) | `M2b_OpenCircuit` | **PENUH** |
| 4 | **M2bGroundFault** | `|V_to_ground|` absolute · adaptive-z vs fleet · `voc_ratio`<0.85 & `I_z`>2 | `ground_fault` | spec+(abs\|adp)=90 · spec/abs+adp=80 · abs=70 · adp=60 | `M2c_GroundFault` | **PENUH\*** |
| 5 | **M2IForest** | IsolationForest 5-fitur (V, I, V_dev, I_dev, R) | `iforest_anomaly` | kuartil rank flagged: CRIT/HIGH/MED/INFO | `IF_Anomaly` | **APPROKSIMASI** |
| 6 | **M2aLowIrradiance** | OLS slope `PR_proxy` vs POA, band low [50,250] & mid [300,800] | `low_irradiance_underperform` / `general_underperform` | `|slope_low|·r²` → CRIT/HIGH/MED | `M2a_LowIrradiance` | **PENUH** |
| 7 | **M2aShading** | CV antar-string per jam + PR-proxy + asimetri AM/PM | `shading_morning` / `_afternoon` / `_uniform` | `0.7·frac + 0.3·asym` → CRIT/HIGH/MED | `M2a_Shading` | **PENUH** |
| 8 | **M2aSoiling** | rdtools SRR (Monte-Carlo), PR temperature-corrected + mask availability M2e → ekonomi payback cleaning | `soiling_detected` / `cleaning_recommended` / `insufficient_data` / `rdtools_error` | `(p_loss, payback)` → CRIT/HIGH/MED/INFO | `SO_Economics` | **HILIR PENUH** |
| 9 | **M2bIntermittent** (LSTM-AE) | LSTM Autoencoder **terlatih** (2026-07-06); reconstruction error window 24-jam (96×15-min, day-grid) | `intermittent` | MEDIUM (conf 70) | *(tak ada — input-only; artifact `WindowErrors`)* | **INPUT-ONLY** |
| 10 | **M2bMpptRatio** | rasio arus string vs median partner **se-MPPT** (`ratio < 0.85`) + debounce 20 (~100 mnt) | `mppt_partner_underperform` | `ratio_event_median` <0.20 CRIT · <0.50 HIGH · else MED | `M2b_MpptRatio` | **PENUH** |

---

## 2. Taksonomi Status Reproduksibilitas Excel

Satu kontribusi utama proyek ini adalah memilah **mana yang benar-benar bisa direproduksi sebagai formula spreadsheet** dan mana yang tidak — secara jujur, dengan caveat eksplisit.

**PENUH** — Direproduksi **eksak** sebagai formula live. Recompute Python independen cocok dengan source ≈ 0 selisih, dan formula Excel (mis. `PERCENTILE`, `MEDIAN`, `STDEVP`, `SLOPE`/`SUMPRODUCT`, `IFS`) mereplikasi rumus persis. Berlaku untuk: **M2e, M2bPeerZScore, M2bOpenCircuit, M2aLowIrradiance, M2aShading, M2bMpptRatio** (iter 11 — verifikasi via konsistensi internal 286 finding nyata; lihat caveat di `M2_RE_11` §7).

**PENUH\*** — Matematika per-inverter **live & eksak**, dengan **satu input representatif**: `M2bGroundFault` menghitung `adaptive_z = |median−fleet_median|/fleet_std`, tetapi statistik fleet dihitung lintas ~200 inverter (tak praktis di demo). Maka `fleet_v_gnd_median/std` = INPUT terdokumentasi. (Catatan analitik: sinyal adaptive secara matematis sulit menyala untuk satu outlier — z dibatasi `N/√(N−1)`.)

**HILIR PENUH** — Semua lapisan **hilir** sebuah nilai = live & eksak; nilai itu sendiri = INPUT black-box. `M2aSoiling`: PR harian + seluruh ekonomi (p_loss, daily_loss, payback, severity) live; `soiling_ratio` dari rdtools **SRR Monte-Carlo** = input (tak bisa formula Excel).

**APPROKSIMASI** — Struktur **faithful** (fitur identik, per-inverter, flagging contamination, severity kuartil, confidence) tetapi **skornya proxy**. `M2IForest`: skor = MAX robust-z (MAD), **bukan** path-length IsolationForest asli → akan menandai **sampel berbeda**. Pakai untuk transparansi logika, bukan pengganti detektor produksi.

**INPUT-ONLY** — Nilai detektor **seluruhnya** ada di bobot jaringan terlatih; tak ada formula spreadsheet untuk forward-pass/training, dan **tak ada lapisan hilir bermakna** tanpa modelnya. `M2bIntermittent` (LSTM-AE, PyTorch) → **tak ada sheet** sama sekali. Kategori terkuat dari taksonomi. Lihat `M2_RE_10`.

> **Mengapa pembedaan ini penting**: tiga detektor ML/stokastik (iForest, SRR di soiling, LSTM-AE) memakai model terlatih/stokastik yang **secara prinsip** tidak bisa jadi formula spreadsheet. Menandainya jujur mencegah workbook disalahartikan sebagai pengganti pipeline Python.

---

## 3. Capsule per Detektor

**1. M2eAvailability** (`availability.py`, Iterasi 2) — Availability hybrid inverter+string dari uptime. Severity dari `uptime_pct` via `IFS()` ke threshold Config. Satu-satunya detektor **tanpa `fault_type`** (murni severity). Sheet: `Helpers_M2e`, `M2e_Availability`, `M2e_AllStrings`, `Findings_Summary`. Repro PENUH.

**2. M2bPeerZScore** (`peer_zscore.py`, `M2_RE_03`) — High-R via z-score `R_str=V/I` antar-string + konfirmasi `voc_ratio` (Voc normal >0.95 = high-R, bukan ground fault). Voc nominal dari datasheet Jinko (PanelSpec, Tcell). **Insight**: pada n kecil, fault genuine bisa **tidak** terdeteksi (sifat detektor, bukan limitasi Excel). Sheet: `Helpers_M2b`, `M2b_PeerZScore`, `M2b_StringStatus`, `M2b_StatComparison`, `PanelSpec`. Repro PENUH.

**3. M2bOpenCircuit** (`open_circuit.py`, `M2_RE_04`) — `I_q95` per-timestamp across siblings (`PERCENTILE`), `ratio = I/I_q95 < 5%`, debounce 20 langkah konsekutif (≈100 mnt) — ekuivalen `MAX(running_consec) ≥ debounce` (tanpa array formula). Guard skip slot PV kosong (Wave 11 #10 cegah 910/1709 false-positive). **Divergensi**: config POA=700 (spec/default 200), debounce=20 (default 2). Repro PENUH.

**4. M2bGroundFault** (`ground_fault.py`, `M2_RE_05`) — Triple-signal cross-check: V-to-ground absolute (>50V), adaptive (>3σ fleet), spec §4.2.3 (`voc_ratio<0.85` & `i_z>2`). Confidence matriks 90/80/70/60; CRITICAL jika ≥80 else HIGH. Implementasi = **superset spec** (V-to-ground sebagai proxy uji insulasi yang tak ada di SCADA). Nama kode `M2b_ground_fault` (label user "M2c"). Repro PENUH\* (fleet-stat input). Sheet: `Helpers_GF`, `GF_StringMetrics`, `M2c_GroundFault`, `M2c_GF_StringStatus`.

**5. M2IForest** (`iforest.py`, `M2_RE_06`) — sklearn `IsolationForest` (100 pohon, contamination 0.01, seed 42) per inverter; 5 fitur. **Opt-in, di-exclude dari Findings** (noisy). Excel = **approksimasi MAD** transparan (skor ≠ forest asli). sklearn tak tersedia di sandbox. Sheet: `Features_IF`, `IF_Anomaly`, `IF_Summary`.

**6. M2aLowIrradiance** (`m2a/low_irradiance.py`, `M2_RE_07`) — OLS `PR_proxy = P/POA` vs POA di dua band; `slope_low<0` = Rs tinggi (respons low-light buruk). Cross-check band-mid **memisahkan** low-light-spesifik (→ drone thermography) dari menyeluruh (→ soiling). OLS = `SLOPE`/`INTERCEPT`/`RSQ` → repro eksak via `SUMPRODUCT`. Sheet: `Helpers_LI`, `M2a_LowIrradiance`, `LI_Summary`.

**7. M2aShading** (`m2a/shading.py`, `M2_RE_08`) — Whole-array shading: **CV rendah** (penurunan seragam antar-string) + **PR rendah** per jam, lalu **asimetri diurnal AM/PM** membedakan terrain shadow (asimetris) dari soiling/awan (uniform) — memanfaatkan geometri IKN (hadap utara, near-ekuator → AM≈PM normal). Gate hanya jam+POA (tanpa ephemeris). Repro PENUH. Sheet: `Helpers_SH`, `SH_Hourly`, `M2a_Shading`.

**8. M2aSoiling** (`m2a/soiling.py`, `M2_RE_09`) — `enabled=false` di pipeline harian, tapi sejak 2026-07 **matang sebagai analysis run standalone** (`run_soiling_analysis.py`; run nyata 2025-01..2026-04). Inti = rdtools `soiling_srr()` (SRR Monte-Carlo 1000-rep) → soiling ratio, kini dengan **PR temperature-corrected**, **mask availability M2e**, rekap manual cleaning, CleaningImpact/DirectCleaningImpact, MonthlySoilingLoss, PerInverterSRR opt-in (lihat `M2_RE_09` §0). Excel mereproduksi **PR harian + kalkulator ekonomi cleaning** (payback ROI) live; `soiling_ratio` = input; ekstensi 2026-07 belum di workbook. Sheet: `Helpers_SO`, `SO_Economics`, `SO_Summary`.

**9. M2bIntermittent (LSTM-AE)** (`lstm_ae.py` + `training_data.py` + `train_lstm_ae.py`, `M2_RE_10`) — **TERLATIH & WIRED (2026-07)**: model `lstm_ae_20260706_084352` dilatih dari baseline CSV (Sprint 4), `enabled=True` di Cell 4 template. LSTM Autoencoder dilatih pada hari NORMAL; window 24-jam day-grid (96×15-min + night-fill 0, fitur arus PV1..28). Anomali = `reconstruction_error > μ+3σ` → `intermittent` (MEDIUM, conf 70); artifact `WindowErrors` (semua window, untuk ranking harian). **INPUT-ONLY**: jaringan PyTorch = black box → **tak ada sheet** reproduksi. Detektor paling tidak-Excel di famili.

**10. M2bMpptRatio** (`mppt_ratio.py`, `M2_RE_11`, iter 11) — String underperform relatif **sibling se-MPPT** (grup dari `mppt_map` di `strings.yaml`): `ratio(t) = I_string/median(I_partner)`, qualifying bila `< 0.85` + gate daylight, debounce 20 langkah (~100 mnt). Severity dari `ratio_event_median` (<0.20 CRIT, <0.50 HIGH, else MED); confidence `min(90, max(50, (1−rem)·100))`. Z-score tak feasible di grup kecil (|z| max = (N−1)/√N). Repro PENUH. Sheet: `Raw_Data_MR`, `Helpers_MR`, `M2b_MpptRatio`, `M2b_MR_StringStatus`.

---

## 4. Inventaris 46 Sheet Workbook

| Grup | Sheet |
|---|---|
| **Indeks / Inti** | `M2_Index` (tab pertama), `README`, `Config` |
| **M2e** (iter 2) | `Raw_Data`, `EmptyPVMap`, `Helpers_M2e`, `M2e_Availability`, `M2e_AllStrings`, `Findings_Summary` |
| **M2b PeerZScore** (iter 3) | `PanelSpec`, `Raw_Data_M2b`, `Meteo_Dummy`, `Helpers_M2b`, `M2b_PeerZScore`, `M2b_StringStatus`, `M2b_StatComparison`, `Hampel_Preprocessing` |
| **M2b OpenCircuit** (iter 4) | `Raw_Data_OC`, `Helpers_OC`, `M2b_OpenCircuit`, `M2b_OC_StringStatus` |
| **M2b GroundFault** (iter 5) | `Raw_Data_GF`, `Helpers_GF`, `GF_StringMetrics`, `M2c_GroundFault`, `M2c_GF_StringStatus` |
| **M2 iForest** (iter 6) | `Raw_Data_IF`, `Features_IF`, `IF_Anomaly`, `IF_Summary` |
| **M2a LowIrradiance** (iter 7) | `Raw_Data_LI`, `Helpers_LI`, `M2a_LowIrradiance`, `LI_Summary` |
| **M2a Shading** (iter 8) | `Raw_Data_SH`, `Helpers_SH`, `SH_Hourly`, `M2a_Shading` |
| **M2a Soiling** (iter 9) | `Raw_Data_SO`, `Helpers_SO`, `SO_Economics`, `SO_Summary` |
| **M2b MpptRatio** (iter 11) | `Raw_Data_MR`, `Helpers_MR`, `M2b_MpptRatio`, `M2b_MR_StringStatus` |

Konvensi: tiap detektor punya `Raw_Data_*` (data dummy, paste-over), `Helpers_*` (jembatan formula per-baris), sheet keputusan, dan StringStatus/Summary (replika artefak Python). `Config` = single source of truth threshold (named cells `cfg_*`).

---

## 5. Metodologi Verifikasi (konsisten Iterasi 4–11)

Setiap iterasi mengikuti alur yang sama, **de-risking sebelum membangun**:

1. **`proto_iterN.py`** — kunci angka EXACT (deterministic) yang meniru source, **sebelum** menyentuh Excel.
2. **`_extend_..._iterN.py`** — append sheet (formula live), append README/Config (idempotent), assert sheet lama tak berubah.
3. **`verify_iterN.py`** — **(A) audit string formula per sel** (formula me-refer sel yang benar) + **(B) recompute numerik** dari literal workbook (angka benar), assert == proto.
4. **Regen 0-diff** — restore backup → rebuild → diff (determinisme).
5. **Sheet lama utuh** — diff vs backup (append tak merusak iterasi sebelumnya).

⚠️ **Caveat metodologi**: LibreOffice **crash** di sandbox dan library evaluator (`formulas`/`pycel`) **tak ter-install** (offline). Maka verifikasi = **audit formula + Python reference + determinisme**, **bukan** live-recalc Excel. Recalc visual disarankan saat user membuka file. Karena mayoritas formula adalah fungsi Excel standar, kepercayaan reproduksi tinggi.

---

## 6. Detektor M2 — Belum Bermodul

*(M2bIntermittent LSTM-AE keluar dari tabel ini per 2026-07: model terlatih & wired — lihat peta §1 #9 dan `M2_RE_10`.)*

| Detector | Modul | Status | Catatan reproduksibilitas |
|---|---|---|---|
| **M2c Microcrack** | *(belum ada)* | Tak ada modul | Butuh kampanye EL imaging (YOLOv8) + IV tracer |
| **M2d Bifacial Backside** | *(belum ada)* | Tak ada modul | Butuh sensor rear-POA (≥4/row, IEC TS 60904-1-2) |

*(M2f Loss Attribution keluar dari tabel ini per 2026-08: modul shipped di
`pv_pipeline/m2f/` dan terangkai lewat `notebook/m2f_loss_attribution.ipynb`.
Catatan: implementasinya **tidak** memakai SHAP — deskripsi lama "Pareto + SHAP
loss decomposition" keliru. SHAP menjelaskan output model dalam satuan model
itu, dan nilai SHAP menjumlah ke `(prediksi - base value)`, bukan ke energi,
sehingga identitas closure rusak bila dicampur ke waterfall kWh. Yang dipakai:
atribusi sekuensial berbasis counterfactual lewat `LossLedger`. Lihat
`docs/superpowers/specs/2026-08-11-m2f-loss-attribution-design.md`.)*

---

## 7. Indeks Dokumen

- `docs/M2_Reverse_Engineering_Phase1_System_Overview.md` — overview sistem M2 (Iterasi 1)
- `docs/M2_RE_02_M2eAvailability.md` · `M2_RE_03_M2bPeerZScore.md` · `M2_RE_04_M2bOpenCircuit.md` · `M2_RE_05_M2cGroundFault.md`
- `docs/M2_RE_06_M2_iForest.md` · `M2_RE_07_M2aLowIrradiance.md` · `M2_RE_08_M2aShading.md` · `M2_RE_09_M2aSoiling.md` · `M2_RE_10_M2bIntermittent.md` · `M2_RE_11_M2bMpptRatio.md`
- **`docs/M2_Family_Summary.md`** — dokumen ini (peta + indeks)
- `docs/M2_PV_Performance_Workbook.xlsx` — 46 sheet (tab `M2_Index` = ringkasan ini di dalam workbook)
- Tiap dokumen RE punya `Sources` + `Verification Log` masing-masing.

---

## 8. Catatan Penutup

Sepuluh iterasi (2–11) menghasilkan: **10 detektor ter-reverse-engineer** (termasuk LSTM-AE yang kini terlatih & wired), **46-sheet workbook** reproducible, dan **11 dokumen** (10 deep-dive + overview) + ringkasan ini. Setiap angka di workbook dapat ditelusuri ke source Python, dengan status reproduksibilitas yang jujur (penuh / input-representatif / hilir-penuh / approksimasi / input-only). **Source code `pv_pipeline/*` dan `config/*` tidak pernah dimodifikasi** — hanya analisis, workbook, dan dokumentasi.

Yang dapat dikerjakan berikutnya: memvalidasi detektor yang sudah ada terhadap data Huawei aktual (paste-over di sheet `Raw_Data_*` lalu recalc), memvalidasi model LSTM-AE terhadap hari-fault berlabel, atau membawa ekstensi soiling 2026-07 (`M2_RE_09` §0) ke workbook. Detektor sisa (M2c Microcrack, M2d Bifacial) butuh hardware baru. M2f Loss-Attribution sudah shipped per 2026-08 (§6) dan tidak lagi masuk kategori itu; yang ia tunggu adalah data pengukuran POA dan Tcell, bukan hardware baru — lihat `docs/M2f_Permintaan_Data_Pengukuran.md`.
