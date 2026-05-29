# M2 Reverse Engineering — Iterasi 3: M2bPeerZScore

**Modul**: `pv_pipeline/peer_zscore.py` (591 baris)
**Class utama**: `M2bPeerZScore(SubModule)` — `name = "M2b_peer_zscore"`
**Spec referensi**: Master Context §4.2.1 POA-gated Z-score · §4.2.3 High-R rule · datasheet Jinko JKM625N (`PanelSpec`)
**Dependency**: `pv_pipeline/voc_estimator.py` (`estimate_voc_at_low_current`), `POAProvider`, `PanelSpec`, `CellTempProvider`
**Dipanggil di**: notebook Cell 4, `sm_b = M2bPeerZScore(poa=prov, panel=panel, cell_temp=ct)`
**Output sheet Python**: `StringStatus` (+ `GateFailureSummary`, `PreprocessingAudit` diagnostik)
**Output Excel workbook**: sheet `PanelSpec`, `Raw_Data_M2b`, `Meteo_Dummy`, `Helpers_M2b`, `M2b_PeerZScore`, `M2b_StringStatus`, `M2b_StatComparison`, `Hampel_Preprocessing` di `docs/M2_PV_Performance_Workbook.xlsx`
**Status verifikasi**: ✅ Python reference cocok dengan input data workbook (4 PV aktif × 12 timestep noon) + static formula audit. ⚠️ Live recalc LibreOffice **tidak** dijalankan (binary crash di sandbox) — lihat Section 7.

---

## 1. Gambaran Peer Z-score

M2bPeerZScore mendeteksi **string dengan resistansi abnormal tinggi** (high-R: koneksi longgar, korosi MC4, busur DC, solder retak) dengan membandingkan *apparent resistance* tiap PV string terhadap saudara-saudaranya.

Konsep inti — **apparent resistance per string**:

$$R_{str}(t) = \frac{V_{string}(t)}{\max(I_{string}(t),\ 0.1)}$$

Saat satu string punya koneksi bermasalah, arusnya turun (resistansi seri naik) sementara tegangannya relatif bertahan → $R_{str}$ string itu melonjak dibanding saudara sehat. Detektor meng-flag lonjakan ini via Z-score.

Dua keputusan desain penting:

1. **Peer scope = sibling strings di SATU inverter** (PV1..PV28 dari `Inverter_ID` yang sama), **bukan** cross-inverter. Alasan (docstring baris 13-14): orientasi panel, MPPT controller, dan DC bus tiap inverter berbeda, jadi membandingkan antar-inverter akan membandingkan apel dengan jeruk.

2. **High-R butuh dua syarat (Spec §4.2.3)** agar tidak salah tuduh ground fault:

   $$\text{emit high\_R} \iff (|z| > 2.5) \ \wedge\ (\text{voc\_ratio} > 0.95)$$

   `voc_ratio = voc_actual / voc_string_nominal`. Voc yang masih ~normal (>0.95) mengkonfirmasi semua modul masih ada di rangkaian (bukan ground fault yang memotong sebagian string). Jadi z-score tinggi **plus** Voc normal = high-R; z-score tinggi **dengan** Voc drop = indikasi ground fault (rule terpisah, Iterasi berikut).

Output: severity `HIGH` jika $|z|>3.5$ else `MEDIUM`; confidence $=\min(90,\ |z|/4 \times 100)$ persen.

---

## 2. Pipeline `M2bPeerZScore.run()` — Step by Step

Method `run(combined_df, config)` (baris 112-524). Saya turunkan tiap langkah; worked example numerik ada di Section 3.

### Langkah 1 — Baca config & threshold (baris 113-127)

Semua override dari `config["m2b"]`, default di konstanta modul:

| Param | Default | Sumber konstanta |
|---|---|---|
| `poa_threshold_wm2` | 300.0 | `DEFAULT_POA_THRESHOLD_WM2` |
| `poa_floor_wm2` | 50.0 | `DEFAULT_POA_FLOOR_WM2` (sunset fix 2026-05-16) |
| `hour_cutoff_end` | 18.0 | `DEFAULT_HOUR_CUTOFF_END` |
| `z_threshold` | 2.5 | `DEFAULT_Z_THRESHOLD` |
| `voc_ratio_threshold` | 0.95 | `DEFAULT_VOC_RATIO_THRESHOLD` |
| `stat_method` | "median" | `DEFAULT_STAT_METHOD` ("mean"\|"median"\|"both") |
| `pv_max` | 28 | — |
| `min_daylight_samples` | 10 | — |
| `min_peer_strings` | 3 | — |

### Langkah 2 — Validasi kolom & Hampel preprocessing opsional (baris 131-150)

Jika `Inverter_ID` atau `Start Time` hilang → `warnings.warn` + return `[]`. Lalu, kalau `config["preprocessing"]["enabled"]` (default `False`, Wave 9 A/B flag), jalankan `apply_hampel_to_pv_dataframe` (rolling median + MAD outlier removal) sebelum analisis. Default mati → demo Hampel di workbook bersifat ilustratif (Section 5.3).

### Langkah 3 — Normalisasi nama kolom V/I (baris 155-164)

Wave 11 hotfix #11: kolom Title Case (`PV15 Input Voltage` untuk PV15-28 di beberapa file Huawei) di-rename ke lowercase canonical `PV15 input voltage` supaya regex PV1..PV28 menangkap semua.

### Langkah 4 — Loop `source × inverter` (baris 191-192)

Outer loop = `poa_source` (multi-source fan-out, default 5 source). Inner loop = `groupby("Inverter_ID")`. Semua langkah berikut per (source, inverter).

### Langkah 5 — POA gate komposit (baris 206-287)

Tiga mask digabung dengan AND:

```
mask_poa_main = (POA > poa_threshold) AND (POA > poa_floor)      # spec §4.2.1 + sanity
mask_time     = (solar_elevation > 5°) AND (hour < 18.0)         # Fase 2 fisik; fallback hour_cutoff
mask_shutdown = (timestamp < inverter_shutdown_time)             # kalau kolom tersedia & bukan sentinel
mask_poa      = mask_poa_main & mask_time & mask_shutdown
```

Jika `mask_poa.sum() < min_daylight_samples` (10) → `continue` (gate `g3`). Catatan: `mask_shutdown` punya dua hotfix anti-sentinel (Wave 11 #5/#6) untuk membuang datetime "never shutdown" (tahun <2000) dan "0:00:00" yang kalau tidak difilter akan membuang semua data.

### Langkah 6 — Voc_string_nominal dari Tcell (baris 289-303)

```
tcell_mean        = mean(Tcell) di daylight samples (fallback 25°C)
voc_per_module    = panel.voc_at_cell_temp(tcell_mean)         # 55.72·(1 + (-0.25/100)·(Tcell-25))
modules_per_string= panel.modules_per_string(wb_id)            # WB05 → 26
voc_string_nominal= voc_per_module × modules_per_string
```

### Langkah 7 — R_str & voc_actual per string (baris 305-327)

Untuk tiap `pv_n` di 1..pv_max, **skip kalau ∈ empty_pv_map** (Wave 11 #10):

```
I_clip   = I.clip(lower=0.1)
R_t      = (V / I_clip).where(mask_poa)
R_valid  = R_t.dropna()
if len(R_valid) < min_daylight_samples // 2:  continue          # butuh ≥5 sampel
r_str_per_string[pv_n] = median(R_valid)
voc_actual_per_string[pv_n] = estimate_voc_at_low_current(V, I) # median V saat |I|<0.5 & V>10
```

### Langkah 8 — Fleet statistik (baris 329-339)

```
if len(r_str_per_string) < min_peer_strings (3):  continue       # gate g5
r_mean_fleet   = mean(r_values)
r_median_fleet = median(r_values)
r_std_fleet    = std(r_values)                                   # pandas ddof=1 (sample std)
if r_std_fleet < 1e-6:  continue                                 # gate g6 (semua string identik)
```

### Langkah 9 — Z-score, voc_ratio, keputusan emit (baris 342-419)

Per string:

```
z_mean   = (R_str − r_mean_fleet)   / r_std_fleet
z_median = (R_str − r_median_fleet) / r_std_fleet
z_primary= z_median (stat_method="median") | z_mean ("mean") | argmax|·| ("both")

flagged_by_mean   = |z_mean|   > 2.5
flagged_by_median = |z_median| > 2.5
flagged           = flagged_by_mean OR flagged_by_median          # ← gabungan, baris 363
voc_ratio = voc_actual / voc_string_nominal
voc_ok    = voc_ratio > 0.95
emit      = flagged AND voc_ok                                    # Spec §4.2.3
```

Kalau `emit`: `confidence = min(90, |z_primary|/4·100)`, `severity = HIGH if |z_primary|>3.5 else MEDIUM`, timestamp = last daylight sample (Wave 11 #9). Semua string (emit atau tidak) dicatat ke artifact `StringStatus`.

> **Catatan penting** (di-detailkan di Section 5.2): syarat `flagged` di Python adalah **OR** dari `z_mean` dan `z_median`, sedangkan severity/confidence pakai `z_primary`.

---

## 3. Worked Example — Numerik Step-by-Step

**Dummy data** (`Raw_Data_M2b`, 1 inverter `WB05-INV05`, 24 timestep × 5 PV): 12 timestep **sunrise** (06:00–06:55, untuk estimasi Voc) + 12 timestep **noon** (12:00–12:55, untuk R_str). `empty_pv_map = {WB05-INV05: [5]}` → **PV5 EMPTY**.

Threshold (sheet `Config`): `poa_threshold=300, poa_floor=50, i_clip_floor=0.1, z_threshold=2.5, z_high=3.5, voc_ratio_threshold=0.95, stat_method=median, i_threshold_voc=0.5, min_voc=10`.

### 3.1 POA gate

POA dari `Meteo_Dummy`:

| Window | timestep | POA (W/m²) | `mask_poa` |
|---|---|---|---|
| Sunrise | t0–t11 (06:00–06:55) | 30.0 → 80.0 | **0** (POA < 300, gagal gate) |
| Noon | t12–t23 (12:00–12:55) | 847.6 → 912.6 | **1** (POA > 300, lulus) |

→ **12 dari 24** timestep lulus POA gate. R_str hanya dihitung di 12 noon samples.

### 3.2 R_str per string (median noon samples)

Contoh satu timestep, t=12 (noon), $R_t = V / \max(I, 0.1)$:

| PV | V (V) | I (A) | $R_t$ |
|---|---|---|---|
| PV1 | 1205.31 | 13.015 | 92.61 |
| PV2 | 1202.20 | 12.994 | 92.52 |
| PV3 | 1238.40 | **5.248** | **235.98** |
| PV4 | 1205.72 | 13.045 | 92.43 |
| PV5 | 0 | 0 | EMPTY (skip) |

**Signature high-R PV3 jelas**: arus kolaps ke ~5.2 A (saudara ~13 A) sementara tegangan **naik** ke ~1238 V (saudara ~1203 V) → $R$ melonjak ~2.6×. Median 12 noon samples:

| PV | $R_{str}$ median | n_R_samples |
|---|---|---|
| PV1 | **92.42** | 12 |
| PV2 | **92.46** | 12 |
| PV3 | **237.94** | 12 |
| PV4 | **92.34** | 12 |
| PV5 | EMPTY | — |

### 3.3 Fleet stats & Z-score (n = 4 aktif)

PV5 EMPTY dikecualikan → **4 peer strings aktif**:

$$\bar{R} = 128.79,\quad \tilde{R}_{med} = 92.44,\quad s = 72.77\ (\text{ddof}=1)$$

Z-score PV3 (string fault):

$$z_{mean} = \frac{237.94 - 128.79}{72.77} = \mathbf{1.50}, \qquad z_{median} = \frac{237.94 - 92.44}{72.77} = \mathbf{2.00}$$

`stat_method="median"` → `z_primary = z_median = 2.00`.

### 3.4 Voc_actual & voc_ratio

Dari window sunrise (|I| < 0.5 A, V > 10 → open-circuit), median V:

| PV | voc_actual (V) | voc_string_nominal (V) | voc_ratio |
|---|---|---|---|
| PV1 | 1467.7 | 1430.61 | 1.026 |
| PV3 | 1467.9 | 1430.61 | **1.026** |

`voc_string_nominal` = `voc_per_module(30°C)` × 26 = 55.0235 × 26 = **1430.61 V**. voc_ratio PV3 = 1.026 > 0.95 → **voc_ok = True** (mengkonfirmasi BUKAN ground fault: semua 26 modul masih nyambung).

### 3.5 Keputusan akhir

| PV | $z_{mean}$ | $z_{median}$ | $\lvert z\rvert>2.5$ | voc_ok | **EMIT** |
|---|---|---|---|---|---|
| PV1 | −0.50 | 0.00 | False | True | **No** |
| PV2 | −0.50 | 0.00 | False | True | **No** |
| PV3 | 1.50 | **2.00** | **False** | True | **No** ⚠️ |
| PV4 | −0.50 | 0.00 | False | True | **No** |

**Hasil: 0 finding di-emit, meskipun PV3 adalah fault asli.** Semua string status `NORMAL` di `StringStatus`. Ini bukan bug — ini limitasi statistik n kecil, dibuktikan di Section 5.1.

---

## 4. Pemetaan Python → Excel Formula

Lokasi: `docs/M2_PV_Performance_Workbook.xlsx`, sheet `Helpers_M2b` & `M2b_PeerZScore`.

### 4.1 Sheet `Helpers_M2b` — derived per row

| Kolom | Formula Excel | Ekuivalen Python |
|---|---|---|
| `POA` (D) | `=IFERROR(VLOOKUP(B,Meteo_Dummy!$A$5:$B$n,2,FALSE),0)` | `self.poa.get_poa(...)` |
| `mask_poa` (E) | `=IF(AND(D>cfg_poa_threshold_wm2, D>cfg_poa_floor_wm2),1,0)` | `(poa>thr) & (poa>floor)` |
| `PVn_R` (I/M/Q/U/Y) | `=IF(E=1, Vn/MAX(In,cfg_i_clip_floor_a), "")` | `(V/I.clip(0.1)).where(mask_poa)` |
| `PVn_voc_cand` (J/N/R/V/Z) | `=IF(AND(ABS(In)<cfg_i_threshold_a, Vn>cfg_min_voc_v), Vn, "")` | mask `|I|<0.5 & V>10` |

### 4.2 Sheet `M2b_PeerZScore`

Section A (per-PV, baris 6–10):

| Kolom | Formula Excel | Ekuivalen Python |
|---|---|---|
| `R_str` (B) | `=IF(is_empty=1,"EMPTY", IFERROR(MEDIAN(Helpers_M2b!R_range),""))` | `median(R_valid)` |
| `voc_actual` (C) | `=IF(is_empty=1,"EMPTY", IFERROR(MEDIAN(Helpers_M2b!voc_range),""))` | `estimate_voc_at_low_current` |
| `voc_nominal` (D) | `=voc_string_26_calc` | `voc_per_module × 26` |
| `voc_ratio` (E) | `=IFERROR(C/D,"")` | `voc_actual / voc_string_nominal` |

Section B (fleet stats, baris 15–17) — operasi atas `B6:B10` (text "EMPTY" otomatis diabaikan AVERAGE/MEDIAN/STDEV):

| Metric | Formula | Catatan |
|---|---|---|
| `rstr_fleet_mean` | `=AVERAGE(B6:B10)` | named cell |
| `rstr_fleet_median` | `=MEDIAN(B6:B10)` | named cell |
| `rstr_fleet_std` | `=STDEV(B6:B10)` | **STDEV** (ddof=1), bukan STDEV.S — kompatibilitas LibreOffice lama |

Section C (keputusan, baris 22–26):

| Kolom | Formula Excel | Ekuivalen Python |
|---|---|---|
| `z_mean` (B) | `=IFERROR((R_str−rstr_fleet_mean)/rstr_fleet_std,"")` | `(R−mean)/std` |
| `z_median` (C) | `=IFERROR((R_str−rstr_fleet_median)/rstr_fleet_std,"")` | `(R−median)/std` |
| `z_primary` (D) | `=IF(cfg_stat_method="median",C, IF(...,"mean",B, IF(ABS(B)>=ABS(C),B,C)))` | pemilihan per `stat_method` |
| `flagged_by_z` (E) | `=IF(ABS(D)>cfg_z_threshold,1,0)` | ⚠️ lihat 5.2 (Python pakai OR) |
| `voc_ok` (F) | `=IFERROR(IF(voc_ratio>cfg_voc_ratio_threshold,1,0),0)` | `voc_ratio > 0.95` |
| `emit` (G) | `=IF(AND(E=1,F=1),1,0)` | `flagged AND voc_ok` |
| `severity` (I) | `=IF(G=1,IF(ABS(D)>cfg_z_high_threshold,"HIGH","MEDIUM"),"")` | `HIGH if |z|>3.5` |
| `confidence` (J) | `=IF(G=1,MIN(90,ABS(D)/4*100),"")` | `min(90, |z|/4·100)` |

### 4.3 Conditional formatting

Sheet `M2b_PeerZScore` kolom severity (I) di-warna via `CellIsRule`: HIGH (oranye), MEDIUM (kuning). Sheet `M2b_StringStatus` kolom status di-warna `high_R` (merah), `NORMAL` (hijau), `EMPTY` (abu).

---

## 5. Edge Cases & Limitasi Translasi

### 5.1 Limitasi n kecil — KENAPA fault asli PV3 TIDAK terdeteksi (paling penting)

Ini temuan utama Iterasi 3. PV3 jelas fault (R ~2.6× saudara) tapi `EMIT=No`. **Bukan error** — ini batas matematis statistik dengan sedikit peer.

**Bukti analitik.** Untuk pola "(n−1) string sehat identik bernilai $a$ + 1 string fault bernilai $b$", dengan sample std (ddof=1), bisa diturunkan **bentuk tertutup eksak**:

$$z_{median} = \frac{b - a}{s} = \sqrt{n}, \qquad z_{mean} = \frac{b - \bar{R}}{s} = \frac{n-1}{\sqrt{n}}$$

Turunannya: $\bar{R} = \frac{(n-1)a + b}{n}$, dan $s^2 = \frac{(b-a)^2}{n}$ → $s = \frac{b-a}{\sqrt{n}}$. Maka $z_{median} = (b-a)/s = \sqrt{n}$ — **independen terhadap besar fault** ($a$, $b$ hilang). Ini varian bound Shiffler (1988) untuk z relatif median.

Konsekuensinya, ambang deteksi $z > 2.5$ butuh:

$$\sqrt{n} > 2.5 \iff n > 6.25 \iff n \ge 7\ \text{peer aktif}$$

| n peer | $z_{median} = \sqrt{n}$ | $z_{mean} = (n-1)/\sqrt{n}$ | Deteksi ($z>2.5$)? |
|---|---|---|---|
| **4** (dummy) | **2.00** | **1.50** | ❌ tidak mungkin |
| 5 | 2.24 | 1.79 | ❌ |
| 7 | 2.65 | 2.27 | ✅ (z_median) |
| 9 | 3.00 | 2.67 | ✅ keduanya |
| **28** (produksi) | **5.29** | **5.10** | ✅✅ jelas |

Dummy kita **n = 4** (PV5 EMPTY) → $z_{median} = \sqrt{4} = 2.00 < 2.5$. **Sekuat apapun fault PV3, z_median mentok di 2.00.** Detektor secara matematis buta terhadap single-string fault saat n=4.

Di produksi IKN tiap inverter punya **~28 string** → $z_{median} = \sqrt{28} = 5.29 \gg 2.5$ → fault yang sama terdeteksi tegas (diverifikasi: 27×92 + 1×238 → z_median = 5.292). **Detektor benar; skenario demo-nya yang di bawah resolusi statistik.**

> ⚠️ **Koreksi catatan iterasi sebelumnya**: framing "limitasi n=5" kurang tepat. n efektif = **4** (PV5 EMPTY dikecualikan dari fleet stats baik di Python maupun Excel). Angka $(n-1)/\sqrt{n}\approx1.79$ adalah ceiling **z_mean** untuk n=5 — bukan z_median dan bukan n=4. Nilai akurat: z_mean(PV3)=1.50, z_median(PV3)=2.00.

Bound $\sqrt{n}$ di atas berlaku untuk pola ideal (saudara identik). Data riil punya sebaran saudara yang sedikit menggeser $s$, tapi orde besarannya tetap: deteksi single-outlier butuh n ≳ 7.

### 5.2 Formula Excel berbeda dari Python (ekuivalen untuk kasus high_R)

`flagged` Excel (kolom E) = `ABS(z_primary) > 2.5` (hanya z_primary). Python (baris 363) = `flagged_by_mean OR flagged_by_median` (gabungan kedua z). 

Untuk high-R single-outlier, $z_{median} = \sqrt{n} \ge z_{mean} = (n-1)/\sqrt{n}$ selalu (karena $n \ge n-1$), dan `stat_method="median"` → `z_primary = z_median` = suku pengikat. Jadi gerbang Excel **ekuivalen** dengan OR Python di use-case high_R. Keduanya baru bisa beda kalau `stat_method="mean"` sementara hanya z_median yang lewat ambang, atau pada low-outlier — skenario di luar high-R. **Rekomendasi opsional** (hardening fidelity): ganti kolom E ke `=IF(OR(ABS(B)>cfg_z_threshold, ABS(C)>cfg_z_threshold),1,0)`. Output dummy tidak berubah (keduanya 0).

### 5.3 Hal yang berbeda / disederhanakan di Excel

| Aspek | Python | Excel workbook |
|---|---|---|
| Multi-source POA fan-out | loop 5 source | 1 source (`pyranometer_avg`) — demo |
| Hampel preprocessing | `apply_hampel_to_pv_dataframe` (pvanalytics) | sheet `Hampel_Preprocessing` pakai **AVEDEV** (mean-abs-dev) sebagai proxy MAD, demo mekanisme |
| `mask_shutdown` anti-sentinel | 2 hotfix (tahun<2000, midnight) | tidak dimodelkan (dummy tak punya kolom shutdown) |
| solar_elevation filter | pvlib `get_solar_elevation` | disederhanakan ke POA threshold saja |
| Gate failure diagnostics | `GateFailureSummary` artifact | tidak ada |

**Caveat Hampel (sheet `Hampel_Preprocessing`)**: array formula `=MEDIAN(IF(window>0,ABS(window−median)))` butuh entry CSE dan **gagal recalc tanpa CSE di LibreOffice**. Diganti `=AVEDEV(window)` (mean-absolute-deviation) — universal & non-array. Konstanta skala 1.4826 (MAD→σ untuk distribusi normal) tetap di kolom F; untuk AVEDEV strict konsisten ~1.2533. Sheet ini **demo mekanisme**, bukan filter produksi.

---

## 6. Cross-Check vs Master Context Spec

| Aspek | Master Context §4.2 spec | Implementasi `peer_zscore.py` | Match? |
|---|---|---|---|
| POA mask | POA > 300 | `(POA>300) & (POA>50) & elev>5° & hour<18` | ✅ superset (sunset hardening) |
| R_str | `V[mask] / I[mask].clip(0.1)` | identik | ✅ exact |
| z_score | `(R − R.median()) / R.std()` | z_median identik; +z_mean cross-check | ✅ exact + ekstra |
| High-R emit | `|z|>2.5 AND voc_ratio>0.95` | `flagged AND voc_ok` | ✅ exact |
| Confidence | `min(90%, |z|/4·100%)` | identik | ✅ exact |
| Severity HIGH | `|z|>3.5` | identik | ✅ exact |
| Peer scope | (tak eksplisit di spec) | sibling same-inverter | ✅ justifikasi fisik (docstring) |
| std type | (tak eksplisit) | pandas `.std()` ddof=1 = Excel `STDEV` | ✅ konsisten |

Tidak ditemukan deviasi logika dari spec. Detektor adalah implementasi setia §4.2.1 + §4.2.3 dengan tambahan robustness (sunset/shutdown fix) dan cross-check z_mean.

---

## 7. Verification Log

Saya jalankan verifikasi independen sebelum publikasi (skeptis-first; **tidak** mengandalkan recalc black-box):

1. **Python reference** (`outputs/verify_iter3.py`): baca data literal `Raw_Data_M2b` + `Meteo_Dummy` dari workbook (source of truth), replikasi persis operasi `peer_zscore.py` (R_str, fleet stats, z, voc_ratio).
2. **Static formula audit**: baca string formula tiap cell dari workbook tersimpan → konfirmasi wiring (range, named cell, urutan operasi) cocok dengan Python. Karena formula me-mirror operasi atas input yang sama, nilai computed Excel = nilai Python by construction.
3. **Bukti analitik**: turunan tertutup $z_{median}=\sqrt{n}$, $z_{mean}=(n-1)/\sqrt{n}$ diverifikasi numerik.
4. **Reproducibility**: regen penuh `_build_m2_workbook.py` → `_extend_m2_workbook_iter3.py` dari nol, diff cell-level vs workbook patched → **0 beda di 16 sheet**.

| Kasus uji | Python ref | Excel (static audit) | Match |
|---|---|---|---|
| R_str PV1/PV2/PV3/PV4 | 92.42 / 92.46 / 237.94 / 92.34 | `MEDIAN(Helpers R_range)` per PV | ✅ |
| Fleet mean / median / std (n=4) | 128.79 / 92.44 / 72.77 | `AVERAGE/MEDIAN/STDEV(B6:B10)` | ✅ |
| z_mean / z_median PV3 | 1.50 / 2.00 | `(R−mean)/std`, `(R−median)/std` | ✅ |
| voc_ratio PV3 | 1.026 (1467.9/1430.61) | `C/D` | ✅ |
| Keputusan emit semua PV | 0 emit (z<2.5) | `AND(E,F)` → 0 | ✅ |
| z_median produksi (n=28) | 5.292 = √28 | (analitik) | ✅ |
| PV5 EMPTY → blank decision | dikecualikan pre-loop | D/E/F/G = blank | ✅ (fix bug #VALUE!) |

⚠️ **Caveat jujur**: LibreOffice headless **gagal** (DeploymentException / SIGKILL) di sandbox meski memori cukup; live recalc **tidak** dijalankan. Verifikasi di atas via Python ref + static audit lebih informatif daripada recalc black-box, tapi user **sebaiknya buka workbook di Excel/LibreOffice desktop** untuk konfirmasi visual final.

🐛 **Bug ditemukan & diperbaiki saat double-check**: baris PV5 (EMPTY) di Section C sempat `#VALUE!` karena `R_str="EMPTY"` (text) masuk `ABS()`. Karena PV5 EMPTY harus tanpa keputusan, cell D26/E26/F26/G26 di-blank (sinkron dengan Python yang skip empty PV sebelum z-logic). Fix dibakar ke build script (reproducible).

---

## 8. Rekomendasi Penggunaan Workbook

1. **Ganti dummy dengan data aktual**:
   - `Raw_Data_M2b`: paste `PVn input voltage(V)` / `PVn input current(A)` dari `combined_df`; perluas PV5→PV28.
   - `Meteo_Dummy`: ganti POA dummy dengan 1 hari POA real dari `raw data input/POA PLTS IKN 2026.xlsx`.
   - `Config`: sesuaikan `stat_method`, `z_threshold` sesuai produksi.

2. **Sadari batas n peer**: deteksi single-string high-R butuh **≥7 peer aktif** ($\sqrt{n}>2.5$). Inverter dengan banyak slot EMPTY/missing data akan kehilangan sensitivitas. Pertimbangkan logging n_active per inverter.

3. **Naikkan fidelity gerbang** (opsional, 5.2): kolom E → `OR(ABS(z_mean), ABS(z_median)) > threshold` agar identik dengan `flagged` Python.

4. **Hampel hanya demo**: sheet `Hampel_Preprocessing` ilustrasi mekanisme (AVEDEV proxy). Preprocessing produksi tetap di `preprocessing.py` (pvanalytics, default OFF).

5. **voc_ratio sebagai pembeda**: voc_ratio normal (>0.95) = high-R; voc_ratio drop = arah ground fault (Iterasi berikut). Jangan emit high-R tanpa cek Voc.

---

## 9. Pertanyaan untuk Iterasi Berikutnya (M2bOpenCircuit)

Rekomendasi: **Iterasi 4 = M2bOpenCircuit** — paling kritis produksi (799 CRITICAL persisten di IKN). Sebelum mulai:

1. **Threshold open-circuit**: config `m2b_open_circuit` punya `poa=700, i_ratio=0.05, debounce=20, confidence=95`. Konfirmasi makna `i_ratio=0.05` (arus < 5% nominal = open) dan apakah dibandingkan ke Imp datasheet atau ke median saudara?
2. **Debounce 20**: persistence 20 timestep (~1j40m @5min) — sama seperti M2e produksi. Perlu dummy dengan run panjang untuk demo debounce. Berapa interval sampling produksi (5 vs 10 menit)?
3. **Hubungan dengan high-R**: open-circuit (I→0, R→∞) vs high-R (I turun, R naik) — apakah satu finding bisa men-trigger keduanya, atau ada prioritas/mutual-exclusion?
4. **POA 700 vs 300**: kenapa open-circuit pakai POA threshold lebih tinggi (700)? Untuk memastikan iradiasi cukup agar string sehat pasti berarus?

---

## Sources

- `pv_pipeline/peer_zscore.py` (591 baris) — full read: `M2bPeerZScore.run()`, gate logic, z-score, emit decision
- `pv_pipeline/voc_estimator.py` (157 baris) — `estimate_voc_at_low_current` (median V saat |I|<0.5 & V>10, min 3 sampel)
- `config/m2_config.yaml` — section `m2b.*` (poa_threshold, z_threshold, voc_ratio_threshold, stat_method) + `m2b_open_circuit`
- `docs/_extend_m2_workbook_iter3.py` (877 baris) — build script 8 sheet Iterasi 3 (formula reproducible)
- `docs/_build_m2_workbook.py` — base builder Iterasi 2 (8 sheet); chain base→extend diverifikasi 0-diff
- `docs/M2_PV_Performance_Workbook.xlsx` — 16 sheet; `Raw_Data_M2b`, `Helpers_M2b`, `M2b_PeerZScore`, dll
- `outputs/verify_iter3.py` — Python reference + bukti analitik ($z_{median}=\sqrt{n}$, Shiffler bound)
- Master Context §4.2.1 (POA-gated Z-score), §4.2.3 (High-R + Ground Fault rules)
- Verified: Python ref vs workbook input data + static formula audit + regen 0-diff (LibreOffice recalc N/A — sandbox crash)
