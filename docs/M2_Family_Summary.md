# M2 — Ringkasan Famili Detektor & Indeks Workbook

**Tujuan**: satu peta untuk seluruh kerja reverse-engineering M2 — tiap detektor → sinyal → `fault_type` → severity → **status reproduksibilitas Excel**. Berfungsi sebagai indeks untuk `docs/M2_PV_Performance_Workbook.xlsx` (kini **42 sheet**, termasuk tab `M2_Index`) dan 10 dokumen RE.
**Cakupan**: 8 detektor M2 aktif + M2bIntermittent (LSTM-AE, ML skeleton) — Iterasi 2–10. Sumber kebenaran: `pv_pipeline/*.py`, `config/m2_config.yaml`, dan tiap `docs/M2_RE_0X_*.md`.
**Tanggal**: 2026-05-30.

---

## 1. Peta Detektor (8 aktif + 1 ML skeleton)

| # | Detector | Sinyal utama | `fault_type` | Severity (ringkas) | Sheet keputusan | Status Excel |
|---|---|---|---|---|---|---|
| 1 | **M2eAvailability** | uptime inverter & string (downtime menit) | *(severity-based)* | <90 CRIT · <95 HIGH · <97 MED · <99 INFO · ≥99 NORMAL | `M2e_Availability` | **PENUH** |
| 2 | **M2bPeerZScore** | `R_str=V/I` → z-score antar-string + `voc_ratio` | `high_R` | `|z|>3.5` HIGH · `|z|>2.5` & voc>0.95 MED | `M2b_PeerZScore` | **PENUH** |
| 3 | **M2bOpenCircuit** | `I/I_q95 < 5%` (across siblings) + debounce 20 (~100 mnt) | `open_circuit` | CRITICAL (conf 95%) | `M2b_OpenCircuit` | **PENUH** |
| 4 | **M2bGroundFault** | `|V_to_ground|` absolute · adaptive-z vs fleet · `voc_ratio`<0.85 & `I_z`>2 | `ground_fault` | spec+(abs\|adp)=90 · spec/abs+adp=80 · abs=70 · adp=60 | `M2c_GroundFault` | **PENUH\*** |
| 5 | **M2IForest** | IsolationForest 5-fitur (V, I, V_dev, I_dev, R) | `iforest_anomaly` | kuartil rank flagged: CRIT/HIGH/MED/INFO | `IF_Anomaly` | **APPROKSIMASI** |
| 6 | **M2aLowIrradiance** | OLS slope `PR_proxy` vs POA, band low [50,250] & mid [300,800] | `low_irradiance_underperform` / `general_underperform` | `|slope_low|·r²` → CRIT/HIGH/MED | `M2a_LowIrradiance` | **PENUH** |
| 7 | **M2aShading** | CV antar-string per jam + PR-proxy + asimetri AM/PM | `shading_morning` / `_afternoon` / `_uniform` | `0.7·frac + 0.3·asym` → CRIT/HIGH/MED | `M2a_Shading` | **PENUH** |
| 8 | **M2aSoiling** | rdtools SRR (Monte-Carlo) → ekonomi payback cleaning | `soiling_detected` / `cleaning_recommended` / `insufficient_data` | `(p_loss, payback)` → CRIT/HIGH/MED/INFO | `SO_Economics` | **HILIR PENUH** |
| 9 | **M2bIntermittent** (LSTM-AE) | LSTM Autoencoder; reconstruction error window 24-jam (96×15-min) | `intermittent` | MEDIUM (conf 70) | *(tak ada — input-only)* | **INPUT-ONLY** |

---

## 2. Taksonomi Status Reproduksibilitas Excel

Satu kontribusi utama proyek ini adalah memilah **mana yang benar-benar bisa direproduksi sebagai formula spreadsheet** dan mana yang tidak — secara jujur, dengan caveat eksplisit.

**PENUH** — Direproduksi **eksak** sebagai formula live. Recompute Python independen cocok dengan source ≈ 0 selisih, dan formula Excel (mis. `PERCENTILE`, `MEDIAN`, `STDEVP`, `SLOPE`/`SUMPRODUCT`, `IFS`) mereplikasi rumus persis. Berlaku untuk: **M2e, M2bPeerZScore, M2bOpenCircuit, M2aLowIrradiance, M2aShading**.

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

**8. M2aSoiling** (`m2a/soiling.py`, `M2_RE_09`) — **SKELETON** (`enabled=false`). Inti = rdtools `soiling_srr()` (SRR Monte-Carlo 1000-rep) → soiling ratio; butuh ≥90 hari + presipitasi BMKG. Excel mereproduksi **PR harian + kalkulator ekonomi cleaning** (payback ROI) live; `soiling_ratio` = input. Sheet: `Helpers_SO`, `SO_Economics`, `SO_Summary`.

**9. M2bIntermittent (LSTM-AE)** (`lstm_ae.py` + `training_data.py`, `M2_RE_10`) — **SKELETON, BLOCKED** (`enabled=False`, butuh baseline ≥3 bulan). LSTM Autoencoder dilatih pada hari NORMAL; window 24-jam (96×15-min, fitur arus PV1..28). Anomali = `reconstruction_error > μ+3σ` → `intermittent` (MEDIUM, conf 70). **INPUT-ONLY**: jaringan PyTorch = black box → **tak ada sheet** (lapisan data-pipeline diverifikasi dengan kode asli, neural net didokumentasikan). Detektor paling tidak-Excel di famili.

---

## 4. Inventaris 42 Sheet Workbook

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

Konvensi: tiap detektor punya `Raw_Data_*` (data dummy, paste-over), `Helpers_*` (jembatan formula per-baris), sheet keputusan, dan StringStatus/Summary (replika artefak Python). `Config` = single source of truth threshold (named cells `cfg_*`).

---

## 5. Metodologi Verifikasi (konsisten Iterasi 4–9)

Setiap iterasi mengikuti alur yang sama, **de-risking sebelum membangun**:

1. **`proto_iterN.py`** — kunci angka EXACT (deterministic) yang meniru source, **sebelum** menyentuh Excel.
2. **`_extend_..._iterN.py`** — append sheet (formula live), append README/Config (idempotent), assert sheet lama tak berubah.
3. **`verify_iterN.py`** — **(A) audit string formula per sel** (formula me-refer sel yang benar) + **(B) recompute numerik** dari literal workbook (angka benar), assert == proto.
4. **Regen 0-diff** — restore backup → rebuild → diff (determinisme).
5. **Sheet lama utuh** — diff vs backup (append tak merusak iterasi sebelumnya).

⚠️ **Caveat metodologi**: LibreOffice **crash** di sandbox dan library evaluator (`formulas`/`pycel`) **tak ter-install** (offline). Maka verifikasi = **audit formula + Python reference + determinisme**, **bukan** live-recalc Excel. Recalc visual disarankan saat user membuka file. Karena mayoritas formula adalah fungsi Excel standar, kepercayaan reproduksi tinggi.

---

## 6. Detektor M2 — Belum Aktif / Belum Bermodul

| Detector | Modul | Status | Catatan reproduksibilitas |
|---|---|---|---|
| **M2bIntermittent (LSTM-AE)** | `lstm_ae.py` | Skeleton, **BLOCKED** (≥3 bln baseline) — kini **terdokumentasi** (`M2_RE_10`, peta §1 #9) | PyTorch autoencoder; **INPUT-ONLY** (black box, tak ada sheet) |
| **M2c Microcrack** | *(belum ada)* | Tak ada modul | Butuh kampanye EL imaging (YOLOv8) + IV tracer |
| **M2d Bifacial Backside** | *(belum ada)* | Tak ada modul | Butuh sensor rear-POA (≥4/row, IEC TS 60904-1-2) |
| **M2f Loss Attribution** | *(belum ada)* | Tak ada modul | Pareto + SHAP loss decomposition |

---

## 7. Indeks Dokumen

- `docs/M2_Reverse_Engineering_Phase1_System_Overview.md` — overview sistem M2 (Iterasi 1)
- `docs/M2_RE_03_M2bPeerZScore.md` · `M2_RE_04_M2bOpenCircuit.md` · `M2_RE_05_M2cGroundFault.md`
- `docs/M2_RE_06_M2_iForest.md` · `M2_RE_07_M2aLowIrradiance.md` · `M2_RE_08_M2aShading.md` · `M2_RE_09_M2aSoiling.md` · `M2_RE_10_M2bIntermittent.md`
- **`docs/M2_Family_Summary.md`** — dokumen ini (peta + indeks)
- `docs/M2_PV_Performance_Workbook.xlsx` — 42 sheet (tab `M2_Index` = ringkasan ini di dalam workbook)
- Tiap dokumen RE punya `Sources` + `Verification Log` masing-masing.

---

## 8. Catatan Penutup

Sepuluh iterasi (2–10) menghasilkan: **8 detektor aktif + M2bIntermittent (LSTM-AE skeleton) ter-reverse-engineer**, **42-sheet workbook** reproducible, dan **10 dokumen** (9 deep-dive + overview) + ringkasan ini. Setiap angka di workbook dapat ditelusuri ke source Python, dengan status reproduksibilitas yang jujur (penuh / input-representatif / hilir-penuh / approksimasi / input-only). **Source code `pv_pipeline/*` dan `config/*` tidak pernah dimodifikasi** — hanya analisis, workbook, dan dokumentasi.

Yang dapat dikerjakan berikutnya: memvalidasi detektor yang sudah ada terhadap data Huawei aktual (paste-over di sheet `Raw_Data_*` lalu recalc), atau menunggu baseline ≥3 bulan untuk benar-benar melatih LSTM-AE. Detektor sisa (M2c Microcrack, M2d Bifacial, M2f Loss-Attribution) butuh hardware/modul baru.
