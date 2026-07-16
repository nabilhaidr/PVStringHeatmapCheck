# M2 Reverse Engineering — Iterasi 5: M2bGroundFault

**Modul**: `pv_pipeline/ground_fault.py` (644 baris)
**Class utama**: `M2bGroundFault(SubModule)` — `name = "M2b_ground_fault"` *(label iterasi user "M2c"; internal kode tetap `M2b_ground_fault` — lihat §5.6)*
**Spec referensi**: Master Context §4.2.3 — *Ground fault (partial)*: `I_string` tinggi abnormal **AND** `Voc` turun signifikan (`voc_ratio < 0.85`); butuh insulation-resistance test untuk konfirmasi (tidak tersedia di SCADA)
**Dependency**: `POAProvider` (`get_poa`, `get_solar_elevation`), `PanelSpec` (`voc_at_cell_temp`, `modules_per_string`), `CellTempProvider`, `voc_estimator.estimate_voc_at_low_current`, `load_empty_pv_map`
**Output sheet Python**: `InverterEvents` (per-inverter, flagged-only) + `StringStatus` (per-PV: NORMAL | ground_fault | EMPTY) + `PreprocessingAudit` (opsional)
**Output Excel workbook**: sheet `Raw_Data_GF`, `Helpers_GF`, `GF_StringMetrics`, `M2c_GroundFault`, `M2c_GF_StringStatus` di `docs/M2_PV_Performance_Workbook.xlsx` (25 sheet saat iterasi 5; kini 46)
**Status verifikasi**: ✅ Python reference cocok dengan literal data workbook (4 inverter × 6 string) + audit formula string per sel + regen 0-diff. ⚠️ Live recalc LibreOffice **tidak** dijalankan (binary crash di sandbox) — lihat Section 7.

---

## 1. Gambaran Ground-Fault

Detector ini mendeteksi **partial ground fault** — sebagian string bocor ke tanah (insulasi rusak / konektor basah), yang menurunkan tegangan open-circuit (`Voc` drop karena sebagian modul ter-bypass) sering disertai arus abnormal. Berbeda dari detector lain yang per-string, ini berbasis **kolom V-to-ground inverter** (`Voltage between PV– and the ground(V)`, kolom khas Huawei) dan menghasilkan **finding per-inverter**, menunjuk *worst string* (voc_ratio terendah) sebagai tersangka.

Logikanya **triple-signal cross-check** — tiga sinyal independen, di-OR-kan untuk menentukan emit, dan dikombinasikan untuk confidence:

1. **`absolute`** — `MAX|V_to_ground|(daylight) > 50 V`. Sinyal hardware langsung: tegangan-ke-tanah besar = kebocoran.
2. **`adaptive`** — `|V_gnd_median − V_gnd_fleet_median| / max(V_gnd_fleet_std, 0.01) > 3`. Inverter yang menyimpang >3σ dari median fleet.
3. **`spec_4.2.3`** — ADA string dengan `voc_ratio < 0.85` **AND** `i_z(peer) > 2.0`. Ini persis aturan spec §4.2.3 (Voc drop + arus abnormal tinggi).

**Confidence** ditentukan oleh kombinasi yang menyala:

$$
\text{conf} =
\begin{cases}
90\% & \text{spec} \wedge (\text{absolute} \vee \text{adaptive}) \\
80\% & \text{spec saja} \\
80\% & \text{absolute} \wedge \text{adaptive} \\
70\% & \text{absolute saja} \\
60\% & \text{adaptive saja}
\end{cases}
$$

**Severity** = `CRITICAL` bila `confidence ≥ 80`, selain itu `HIGH`. Finding: `value = |V_gnd_median|`, `threshold = 50`, `fault_type = "ground_fault"`, `pv_string = worst`.

Inti filosofinya: spec hanya mensyaratkan Voc-drop + arus tinggi (yang butuh insulation test untuk konfirmasi), tetapi implementasi memanfaatkan kolom V-to-ground Huawei sebagai **proxy hardware** (sinyal absolute & adaptive) — sehingga implementasi adalah **superset** dari spec (lihat §6).

---

## 2. Pipeline `M2bGroundFault.run()` — Step by Step

### Langkah 1 — Baca config & threshold (baris 139-153)

Semua dibaca dari `config["m2b_ground_fault"]`. Berbeda dari M2bOpenCircuit, **semua nilai config = default kode = spec** (tidak ada divergensi threshold — lihat §6):

| Parameter | Config = default | Peran |
|---|---|---|
| `poa_threshold_wm2` | 200 | daylight gate utama |
| `poa_floor_wm2` | 50 | hard floor sunset |
| `v_to_ground_abs_threshold_v` | 50 | trigger absolute |
| `adaptive_z_threshold` | 3.0 | trigger adaptive (σ) |
| `voc_ratio_threshold` | 0.85 | trigger spec (Voc drop) |
| `i_high_z_threshold` | 2.0 | trigger spec (arus z) |
| `pv_max` | 28 | string per inverter |
| `min_daylight_samples` | 5 | minimal sampel daylight |

### Langkah 2 — Guard kolom + cari kolom V-to-ground (baris 155-170)

Butuh `Inverter_ID` & `Start Time` (else return `[]`). Kolom V-to-ground dicari via kandidat: `"Voltage between PV– and the ground(V)"` (en-dash U+2013, default Huawei), fallback ASCII hyphen, lalu *loose search* (kolom mengandung "ground"+"voltage"). Bila tak ada → return `[]`.

### Langkah 3 — Hampel preprocessing opsional (baris 172-204)

Bila `preprocessing.enabled` (config produksi: **true**), V/I per-PV **dan** kolom V-to-ground di-clean Hampel. Audit disimpan ke `PreprocessingAudit`. *(Catatan: config produksi mengaktifkan ini; demo Excel tidak memodelkan Hampel.)*

### Langkah 4 — Statistik fleet V-to-ground (baris 233-238)

```python
v_gnd_all          = to_numeric(combined_df[v_gnd_col]).dropna()      # SEMUA inverter, daylight-agnostic
v_gnd_fleet_median = v_gnd_all.median()
v_gnd_fleet_std    = v_gnd_all.std() if std>0 else 1.0
```

Dihitung lintas **seluruh fleet** (semua baris, semua inverter) — basis sinyal adaptive. **Ini tidak direproduksi penuh di Excel statis** (lihat §5.1).

### Langkah 5 — Loop `source × inverter` + POA gate komposit (baris 240-318)

Sama seperti M2bOpenCircuit: `daylight_mask = daylight_poa & daylight_time & daylight_shutdown` (tiga sub-gate di-AND, baris 313-316). Sub-gate solar-elevation/shutdown berbasis ephemeris (Wave 11 hotfix #5/#6/#7).

### Langkah 6 — Sinyal absolute & adaptive per inverter (baris 320-330)

```python
v_gnd_daylight  = v_gnd_series[daylight_mask].dropna()      # butuh >=3
v_gnd_max_abs   = v_gnd_daylight.abs().max()
v_gnd_median    = v_gnd_daylight.median()
v_gnd_adaptive_z = abs(v_gnd_median - v_gnd_fleet_median) / max(v_gnd_fleet_std, 0.01)
flagged_absolute = v_gnd_max_abs > v_abs_threshold          # 50
flagged_adaptive = v_gnd_adaptive_z > adaptive_z_threshold  # 3
```

### Langkah 7 — Voc nominal dari Tcell (baris 332-342)

`tcell_mean` (daylight mean, fallback 25°C) → `voc_per_module = panel.voc_at_cell_temp(tcell)` → `voc_string_nominal = voc_per_module × modules_per_string`. Untuk WB05 (26 modul) @ 30°C: `55.72 × (1 − 0.0025·5) × 26 = ` **1430.61 V**.

### Langkah 8 — Sinyal spec per string: voc_ratio + peer i_z (baris 344-394)

```python
for i_col in i_cols:                                   # iterasi semua string
    voc_actual = estimate_voc_at_low_current(V_string, I_string)   # median V saat |I|<0.5 & V>10
    if isnan(voc_actual): continue                     # slot kosong (V=0) ter-skip di sini
    voc_ratio  = voc_actual / voc_string_nominal
    peer_median_ts = I_matrix[peer_cols].median(axis=1)            # median sibling per timestamp (exclude self)
    peer_std   = peer_daylight.std() or 0.01
    i_z        = (median(I_string_daylight) - median(peer_daylight)) / peer_std
    if voc_ratio < worst_voc_ratio: worst = (pv_n, voc_ratio, i_z) # track worst
    if voc_ratio < 0.85 and i_z > 2.0: spec_flags.append(...)
flagged_spec = len(spec_flags) > 0
```

### Langkah 9 — Kombinasi, confidence, emit + artifact (baris 395-575)

`triggered` = daftar sinyal yang menyala. `_confidence_for(triggered)` (baris 121-136) → matriks 90/80/80/70/60. `severity = CRITICAL if conf≥80 else HIGH`. Finding per-inverter dengan `pv_string = PV{worst}` (baris 441-484). Artifact: `StringStatus` fan-out semua PV (status = status inverter; empty → top-up `EMPTY`, baris 505-540), `InverterEvents` flagged-only (baris 569-571).

---

## 3. Worked Example — Numerik Step-by-Step

Skenario sintetis (sheet `Raw_Data_GF`): 4 inverter WB05, 6 string, tiap inverter 3 baris dawn (06:00–06:10, `I≈0.2` → estimasi Voc) + 6 baris noon (12:00–12:25, POA=900 → daylight). `voc_string_nominal = 1430.61 V`. Fleet `median=0, std=8` (INPUT representatif — §5.1).

| Inverter | V-to-ground | String khusus | Intent |
|---|---|---|---|
| WB05-INV01 | ≈ +2 V | semua sehat (PV5 kosong) | NORMAL |
| WB05-INV02 | ≈ −80 V | semua sehat (PV5 kosong) | absolute + adaptive |
| WB05-INV03 | ≈ +1 V | PV3 fault (V dawn 1100, I noon 14.2) | spec_4.2.3 |
| WB05-INV04 | ≈ −90 V | PV3 fault | spec + absolute + adaptive |

### 3.1 Sinyal V-to-ground (absolute, adaptive)

| Inverter | `MAX|V_gnd|` | `>50?` | `V_gnd_median` | `adaptive_z = |med−0|/8` | `>3?` |
|---|---|---|---|---|---|
| INV01 | 2.0 | ✗ | 2.0 | 0.25 | ✗ |
| INV02 | 80.0 | ✓ | −80.0 | 10.00 | ✓ |
| INV03 | 1.0 | ✗ | 1.0 | 0.125 | ✗ |
| INV04 | 90.0 | ✓ | −90.0 | 11.25 | ✓ |

### 3.2 Sinyal spec (Voc ratio + peer i_z), string PV3 di INV03

`voc_actual` = median V dawn (`I=0.2<0.5`, `V=1100>10`) = **1100 V**. `voc_ratio = 1100 / 1430.61 = `**0.7689** `< 0.85` ✓.

Peer i_z: `I_PV3_median = 14.2`, peer (median 5 string lain per baris) = 13.0, `peer_std = 0.10954` (dari jitter ±0.1 deterministik). 

$$i_z = \frac{14.2 - 13.0}{0.10954} = \mathbf{10.95} > 2.0 \;\checkmark$$

String sehat: `voc_ratio = 1430/1430.61 = 0.9996`, `i_z = 0` (arus = peer). Hanya PV3 yang spec-flag.

### 3.3 Keputusan akhir per inverter

| Inverter | triggered_by | confidence | severity | worst PV |
|---|---|---|---|---|
| INV01 | — | — | NORMAL | PV1 |
| INV02 | absolute + adaptive | **80%** | CRITICAL | PV1 |
| INV03 | spec_4.2.3 | **80%** | CRITICAL | **PV3** |
| INV04 | absolute + adaptive + spec_4.2.3 | **90%** | CRITICAL | **PV3** |

`worst PV` = voc_ratio terendah. Pada INV02 (semua sehat 0.9996) → PV1 (kemunculan pertama). Pada INV03/INV04 → PV3 (0.7689).

---

## 4. Pemetaan Python → Excel Formula

Lima sheet baru. Voc nominal mereuse named cell `voc_string_26_calc` (PanelSpec, Iterasi 3); estimator Voc mereuse `cfg_i_threshold_a`/`cfg_min_voc_v`.

### 4.1 `Helpers_GF` — per-row (36 baris)

| Kolom | Isi | Formula |
|---|---|---|
| D | daylight | `=IF(AND(C{r}>cfg_gf_poa_threshold_wm2,C{r}>cfg_gf_poa_floor_wm2),1,0)` |
| F–K | voc_cand PV1–6 | `=IF(AND(ABS(Raw_Data_GF!{Icol}{r})<cfg_i_threshold_a,Raw_Data_GF!{Vcol}{r}>cfg_min_voc_v),Raw_Data_GF!{Vcol}{r},"")` |
| L–Q | peer_med_I PV1–6 | `=MEDIAN(`5 kolom arus *selain* PVk`)` (exclude-self per baris, persis `I_matrix[peer_cols].median(axis=1)`) |
| R | abs_Vgnd | `=ABS(E{r})` (untuk `MAX|V_gnd|` tanpa array formula) |

### 4.2 `GF_StringMetrics` — per (inverter, string) (24 baris)

| Kolom | Isi | Formula |
|---|---|---|
| D | voc_actual | `=IFERROR(MEDIAN(Helpers_GF!{vocCand} blok inverter),"")` (hanya dawn non-blank) |
| F | voc_ratio | `=IFERROR(D/E,"")`, `E = voc_string_26_calc` |
| G | I_median_daylight | `=MEDIAN(Raw_Data_GF!{Icol} blok noon)` |
| H | peer_med_daylight | `=MEDIAN(Helpers_GF!{peer} blok noon)` |
| I | peer_std_daylight | `=IF(STDEV(...)=0,0.01,STDEV(...))` (clamp 0.01 persis kode) |
| J | i_z | `=IFERROR((G−H)/I,"")` |
| K | spec_flag | `=IF(C=1,0,IF(AND(F<cfg_gf_voc_ratio_threshold,J>cfg_gf_i_high_z_threshold),1,0))` |

### 4.3 `M2c_GroundFault` — keputusan per inverter (4 baris)

| Kolom | Isi | Formula |
|---|---|---|
| B | V_gnd_max_abs | `=MAX(Helpers_GF!R blok noon)` |
| C | V_gnd_median | `=MEDIAN(Raw_Data_GF!D blok noon)` |
| D | adaptive_z | `=ABS(C−cfg_gf_fleet_v_gnd_median)/MAX(cfg_gf_fleet_v_gnd_std,0.01)` |
| E/F | flag absolute/adaptive | `=IF(B>cfg_gf_v_abs_threshold,1,0)` · `=IF(D>cfg_gf_adaptive_z_threshold,1,0)` |
| G | flag spec | `=IF(SUM(GF_StringMetrics!K blok inverter)>0,1,0)` |
| H | triggered_by | `=MID(IF(E=1,"+absolute","")&IF(F=1,"+adaptive","")&IF(G=1,"+spec_4.2.3",""),2,99)` |
| I | confidence | `=IF(AND(G=1,OR(E=1,F=1)),90,IF(G=1,80,IF(AND(E=1,F=1),80,IF(E=1,70,IF(F=1,60,0)))))` |
| J | severity | `=IF(I>=80,"CRITICAL",IF(I>0,"HIGH","-"))` |
| K/L | worst PV / ratio | `=INDEX(...,MATCH(MIN(voc_ratio blok),...,0))` · `=MIN(GF_StringMetrics!F blok)` |
| M | status | `=IF(OR(E=1,F=1,G=1),"ground_fault","NORMAL")` |

### 4.4 `M2c_GF_StringStatus` + conditional formatting

Replika artifact: status di-fan-out dari keputusan inverter (`=M2c_GroundFault!M`), `is_worst_string` (`=IF(M2c_GroundFault!K="PVk",TRUE,FALSE)`), empty → literal `"EMPTY"`. `CellIsRule`: ground_fault merah, NORMAL hijau, EMPTY abu.

---

## 5. Edge Cases & Limitasi Translasi

### 5.1 Fleet stats sebagai INPUT — dan kenapa adaptive sulit menyala (paling penting)

Kode menghitung `fleet_median`/`fleet_std` dari **seluruh baris semua inverter** (baris 234). Sebuah workbook demo tidak bisa memuat ~200 inverter IKN, jadi fleet median/std disediakan sebagai **named cell INPUT** (`cfg_gf_fleet_v_gnd_median=0`, `cfg_gf_fleet_v_gnd_std=8`) yang merepresentasikan fleet nyata. Per-inverter `adaptive_z` tetap dihitung persis rumus kode.

**Insight analitik penting**: sinyal `adaptive` secara matematis *sulit menyala untuk satu inverter outlier*. Bila satu inverter (fraksi sampel `p`) menyimpang sedangkan sisanya ≈0, z-score-nya **dibatasi** oleh

$$z_{\max} = \frac{1}{\sqrt{p\,(1-p)}}.$$

Untuk `N` inverter berbobot sama dengan 1 outlier (`p = 1/N`), $z_{\max} = N/\sqrt{N-1}$ — jadi butuh **N ≥ 8–10 inverter** agar satu fault menembus 3σ. Dengan kata lain `adaptive` adalah detektor "outlier langka di antara banyak inverter normal", dan bisa **self-masked** bila banyak inverter rusak bersamaan (mereka saling menggembungkan `fleet_std`). Inilah alasan demo memakai fleet-stat representatif daripada memaksa fleet sintetis besar.

### 5.2 Spec i_z — peer-median exclude-self direproduksi penuh

Berbeda dari fleet stats, `i_z` **direproduksi persis** di Excel: kolom `peer_med_I` (Helpers_GF) menghitung `MEDIAN` dari 5 string lain per baris (exclude-self, identik `I_matrix[peer_cols].median(axis=1)`), lalu `STDEV` daylight dengan clamp `0.01`. Jitter arus ±0.1 deterministik memberi `peer_std = 0.10954` (bukan degenerasi 0). Verifikasi: `i_z = 10.954` cocok antara proto, recompute, dan formula.

### 5.3 Slot PV kosong di-skip (Wave 11 hotfix #10)

Slot kosong (Huawei lapor `V=0, I=0`). Di loop spec, `estimate_voc` mengembalikan NaN (karena `V=0 < min_voc 10`) → string di-skip otomatis (tidak jadi worst, tidak spec-flag). Di `StringStatus` loop di-skip eksplisit (baris 409), lalu di-top-up dengan `status="EMPTY"` (baris 521). Excel: kolom `is_empty` + status literal `"EMPTY"` untuk PV5 (INV01/INV02).

### 5.4 Yang disederhanakan di Excel

- **Daylight Excel = sub-gate POA saja** (baris noon). Produksi meng-AND solar-elevation>5° & shutdown inverter (ephemeris) — tak direproduksi statis.
- **Voc nominal pakai Tcell tetap 30°C** (PanelSpec `tcell_dummy_c`). Produksi `CellTempProvider` per timestamp.
- **Multi-source POA fan-out** (5 source) tidak dimodelkan — demo single source.

### 5.5 Observasi kode (fail-loud, Rule 12)

Smoke test `__main__` (baris 637-641) merujuk artifact `"GroundFaultEvents"`, tetapi `run()` meng-emit `"InverterEvents"` (baris 571). Akibatnya `art` selalu `None` dan blok print itu di-skip — **dead/stale debug code**, tidak memengaruhi output produksi. Layak dirapikan tapi bukan bug fungsional.

### 5.6 Penamaan: "M2c" (user) vs `M2b_ground_fault` (kode)

Label iterasi yang Anda pakai adalah "M2c Ground Fault", tetapi nama internal kode/config adalah **`M2b_ground_fault`** (class `M2bGroundFault`); tidak ada detector terpisah bernama "M2c" di codebase. Sheet Excel diberi prefiks `M2c_` mengikuti label Anda, sedangkan dokumentasi mempertahankan `M2b_ground_fault` agar dapat ditelusuri ke source. Konfirmasikan konvensi penamaan yang Anda inginkan.

---

## 6. Cross-Check vs Master Context Spec

| Aspek | Spec §4.2.3 | Default kode | Config (aktif) | Verdict |
|---|---|---|---|---|
| Voc drop | `voc_ratio < 0.85` | 0.85 | 0.85 | ✅ selaras |
| Arus abnormal | "tinggi abnormal" | `i_z > 2.0` | 2.0 | ✅ selaras (dikuantifikasi) |
| POA gate | (implisit daylight) | 200 | 200 | ✅ selaras |
| V-to-ground absolute | — *(spec: butuh insulation test)* | 50 V | 50 | ⚠️ **tambahan** (proxy) |
| V-to-ground adaptive | — | 3σ | 3.0 | ⚠️ **tambahan** (proxy) |
| Confidence | 95% (high_R sibling); ground fault tak eksplisit | matriks 90/80/70/60 | sama | ⚠️ lebih kaya |

**Catatan utama:**

1. **Tidak ada divergensi threshold** (berbeda dari Iterasi 4): semua nilai config = default kode = spec. Yang dipakai runtime sama dengan yang tertulis.
2. **Implementasi = superset spec.** Spec §4.2.3 hanya mensyaratkan `Voc<0.85 + arus tinggi` (= trigger `spec_4.2.3`) dan menyatakan butuh *insulation-resistance test* yang **tidak ada di SCADA**. Implementasi memanfaatkan kolom **V-to-ground Huawei** sebagai proxy hardware → menambah trigger `absolute` & `adaptive`, lalu menggabungkan via matriks confidence.
3. **System Overview menyederhanakan.** `M2_..._System_Overview.md` (baris 235) menulis "absolute/adaptive/spec → 80/70/60%" — ini **tidak lengkap** vs matriks kode (hilang tier 90% untuk `spec+(abs|adp)` dan 80% untuk `abs+adp`). Ground truth = `_confidence_for` (baris 121-136).

---

## 7. Verification Log

| # | Cek | Metode | Hasil |
|---|---|---|---|
| 1 | Build 5 sheet baru | `_extend_m2_workbook_iter5.py` | 20 → 25 sheet, 19 sheet lama + Config/README append, urutan utuh ✅ |
| 2 | Audit string formula | `verify_iter5.py` (A) | Helpers (36×6 voc_cand+peer), StringMetrics (24), Decision (4) == template ✅ |
| 3 | Recompute numerik | `verify_iter5.py` (B) | triple-signal, matriks confidence, severity, worst-string cocok proto ✅ |
| 4 | Angka eksak | spot-check | voc_nominal 1430.611; PV3 voc_ratio 0.7689; i_z 10.954; healthy 0.9996 ✅ |
| 5 | Guard empty-PV | assert PV5 empty di-skip | ✅ tidak jadi worst / spec |
| 6 | Reprodusibilitas | restore backup → rebuild → diff | **0 cell-diff** lintas 5 sheet + Config + README ✅ |
| 7 | Sheet lama utuh | diff vs backup (excl Config/README) | **0 diff** pada 19 sheet sebelumnya ✅ |

⚠️ **Live recalc LibreOffice tidak dijalankan** (binary crash di sandbox); `formulas`/`pycel` tak ter-install (offline). Verifikasi bertumpu pada **(a) audit string formula per sel + (b) reimplementasi semantik Excel di Python + (c) regen 0-diff**. Recalc visual Excel tetap disarankan saat user membuka file.

---

## 8. Rekomendasi Penggunaan Workbook

- Buka `M2c_GroundFault` untuk verdict per-inverter; warna status (merah=ground_fault, hijau=NORMAL) langsung terbaca. Kolom `triggered_by` & `confidence` menjelaskan *kenapa*.
- `GF_StringMetrics` menampilkan Voc & i_z per string — tabel diagnosis untuk menelusuri *worst string*.
- Ubah threshold via `Config` named cell `cfg_gf_*` — mis. turunkan `cfg_gf_v_abs_threshold` untuk melihat lebih banyak inverter ter-flag absolute.
- **Fleet median/std** (`cfg_gf_fleet_v_gnd_median/std`) adalah INPUT — ganti dengan statistik fleet nyata Anda agar `adaptive_z` realistis (§5.1).
- **Jangan** pakai workbook untuk reproduksi gate solar-elevation/shutdown atau fleet-stat penuh — di luar cakupan sheet statis.

---

## 9. Pertanyaan untuk Iterasi Berikutnya

1. **Konvensi penamaan** (§5.6): pakai `M2c_` (label Anda) atau `M2b_ground_fault` (kode) untuk sheet & dokumen ke depan?
2. **Fleet adaptive** (§5.1): apakah perlu varian demo dengan fleet sintetis besar (≥10 inverter) supaya `adaptive` menyala dari data, atau cukup pendekatan fleet-stat-INPUT sekarang?
3. **Detector selanjutnya** — M2a (shading / soiling / low_irradiance), M2_iforest (anomaly), atau LSTM-AE? Konfirmasikan target Iterasi 6.

---

## Sources

- `pv_pipeline/ground_fault.py` (644 baris) — full read: `M2bGroundFault.run()`, `_confidence_for`, triple-signal, emit, top-up/fan-out artifact
- `pv_pipeline/voc_estimator.py` (157 baris) — `estimate_voc_at_low_current` (median V saat |I|<0.5 & V>10, ≥3 sampel)
- `pv_pipeline/core.py` — `M2Finding`, `Severity`, `SubModule`, `load_empty_pv_map`
- `config/m2_config.yaml` — section `m2b_ground_fault.*` (v_abs 50, adaptive_z 3, voc_ratio 0.85, i_high_z 2, poa 200)
- `config/panel_spec.yaml` — Jinko JKM625N: voc_stc 55.72, temp_coef_voc −0.25%/°C, WB05 26 modul/string
- `docs/_extend_m2_workbook_iter5.py` (393 baris) — build script 5 sheet Iterasi 5 (formula reproducible, regen 0-diff)
- `docs/verify_iter5.py` — Python reference: audit string formula per sel + recompute numerik vs proto
- `outputs/proto_iter5.py` — prototipe pengunci angka (deterministic; INV01-04, triple-signal, confidence matrix)
- `docs/M2_PV_Performance_Workbook.xlsx` — 25 sheet; `Raw_Data_GF`, `Helpers_GF`, `GF_StringMetrics`, `M2c_GroundFault`, `M2c_GF_StringStatus`
- Master Context §4.2.3 (Ground fault: I tinggi + Voc<0.85; insulation test tak tersedia di SCADA)
- Verified: audit string formula per sel + Python recompute vs literal workbook + regen 0-diff (LibreOffice recalc N/A — sandbox crash)
