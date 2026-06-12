# M2 Reverse Engineering — Iterasi 7: M2aLowIrradiance

**Modul**: `pv_pipeline/m2a/low_irradiance.py` (516 baris)
**Class utama**: `M2aLowIrradiance(SubModule)` — `name = "M2a_low_irradiance"`
**Sifat**: detektor **fisika/statistik** (regresi linier), **opt-in** (`enabled` default `False`; di config IKN `True`).
**Spec referensi**: Master Context — underperformance low-light vs soiling (Fase 3 Part 2 Task #6; di luar rule §4.2.x utama)
**Dependency**: `POAProvider` (`get_poa`, `get_solar_elevation`), `load_empty_pv_map`
**Output sheet Python**: `LowIrradianceFit` (per inverter) + `LowIrradianceSummary` (hitung klasifikasi)
**Output Excel workbook**: sheet `Raw_Data_LI`, `Helpers_LI`, `M2a_LowIrradiance`, `LI_Summary` di `docs/M2_PV_Performance_Workbook.xlsx` (kini 33 sheet)
**Status verifikasi**: ✅ **Reproduksi PENUH (bukan approksimasi)** — OLS dua band cocok eksak antara proto, recompute Python, dan formula `SUMPRODUCT` Excel + audit formula per sel + regen 0-diff. ⚠️ Live recalc LibreOffice tidak dijalankan (crash) — lihat Section 7.

---

## 1. Gambaran Low-Irradiance

Detektor ini menjawab pertanyaan: *apakah sebuah inverter berkinerja buruk **khusus saat irradiance rendah**?* Modul dengan **resistansi seri (Rs) tinggi** kehilangan tegangan secara proporsional terhadap arus — efeknya paling terasa di POA rendah (respons cahaya-redup buruk), sementara di POA tinggi mungkin masih normal.

Metrik intinya **PR-proxy** (performance-ratio relatif, tanpa butuh kapasitas per-inverter):

$$\text{PR}_\text{proxy} = \frac{P_\text{inv}}{\text{POA}}, \qquad P_\text{inv} = \sum_n P_{\text{PV}n}$$

Per inverter, sampel daylight dibagi dua **band POA**: low $[50,250]$ dan mid $[300,800]$ W/m². Di tiap band, regresi linier OLS `PR_proxy = a + b·POA`. **Slope $b$** adalah sinyalnya:

- $b_\text{low} < 0$ → PR-proxy **turun** saat POA naik di rentang rendah = tanda Rs tinggi / underperformance cahaya-redup.
- Cross-check $b_\text{mid}$ membedakan akar masalah (disambiguasi vs soiling):

| $b_\text{low}$ | $b_\text{mid}$ | Klasifikasi | Interpretasi & aksi |
|---|---|---|---|
| $< 0$ | $\ge 0$ | `low_irradiance_underperform` | Rs tinggi spesifik low-light; mid normal → drone thermography |
| $< 0$ | $< 0$ | `general_underperform` | Drop seragam → soiling / drift sensor / degradasi kabel → M2a Soiling |
| $\ge 0$ | — | `normal` | tidak ada finding |

**Severity** dari intensitas slope × kualitas fit: `score = |slope_low|·clamp(r²_low)`; ≥8e-4 CRITICAL, ≥4e-4 HIGH, ≥1e-4 MEDIUM. Emit bila klasifikasi ≠ normal **dan** $r^2_\text{low} \ge 0.3$. `confidence = 50 + r²_low·50`.

Inilah detektor **paling Excel-faithful** sejauh ini: OLS adalah `SLOPE`/`INTERCEPT`/`RSQ` standar — direproduksi **eksak** (bukan approksimasi seperti iForest).

---

## 2. Pipeline `M2aLowIrradiance.run()` — Step by Step

### Langkah 1 — Opt-in & config (baris 308-327)

`enabled` default `False` → return `[]`. Bila aktif: `poa_low_range=[50,250]`, `poa_mid_range=[300,800]`, `min_low_samples=30`, `min_mid_samples=30`, `slope_threshold=0.0`, `r_squared_min=0.3`, `hour_range=[6,18]`.

### Langkah 2 — Guard, normalisasi, providers (baris 329-348)

Butuh `Inverter_ID`/`Start Time`. Normalisasi kolom V/I Title-Case→lowercase, load empty-PV map, init POA.

### Langkah 3 — Per inverter: gate hour + daylight (baris 355-389)

PV aktif = `1..pv_max` minus empty. Filter `hour_range` lalu `_build_gate_mask` (baris 250-306): `POA>0 & (solar_elev>5° AND jam<18) & sebelum shutdown`. Ambil sampel & POA yang lolos.

### Langkah 4 — P_inv & PR-proxy (baris 391-397)

`build_inverter_power_series` (baris 125-150): per timestamp, `P_inv = nansum` daya PV — pakai kolom `PV{n} Power(kW)` bila ada, else `V·I/1000`. Lalu `PR_proxy = P_inv/POA` (POA>0).

### Langkah 5 — Band split + regresi OLS (baris 399-407)

```python
low_mask = (poa >= 50) & (poa <= 250)
mid_mask = (poa >= 300) & (poa <= 800)
slope_low, intercept_low, r2_low, n_low = linear_regression_slope(poa[low_mask], pr[low_mask])
slope_mid, ...                          = linear_regression_slope(poa[mid_mask], pr[mid_mask])
```

`linear_regression_slope` (baris 153-182) adalah OLS standar:

$$b = \frac{\sum (x-\bar x)(y-\bar y)}{\sum (x-\bar x)^2}, \quad a = \bar y - b\bar x, \quad r^2 = 1 - \frac{\sum (y-\hat y)^2}{\sum (y-\bar y)^2}$$

### Langkah 6 — Gate sampel, klasifikasi, severity (baris 409-431)

Bila `n_low < 30` → `insufficient_data`, skip. Else `classify_underperformance` (baris 185-203) + `_severity_from_slope` (baris 206-224).

### Langkah 7 — Emit (baris 451-491)

Skip bila `normal` atau `r²_low < 0.3`. Finding per inverter (`pv_string=None`): `value=slope_low`, `threshold=0`, `fault_type=classification`, `confidence=50+r²_low·50`. Artefak `LowIrradianceFit` + `LowIrradianceSummary`.

---

## 3. Worked Example — Numerik

Skenario (sheet `Raw_Data_LI`): 2 inverter, tiap inverter **32 sampel band-low** (POA 60–245) + **32 band-mid** (POA 310–790) — keduanya ≥ min 30. `P_inv` disediakan langsung (= Σ daya PV); `PR_proxy = P_inv/POA` dihitung di Helpers.

### 3.1 Regresi dua band

| Inverter | slope_low | r²_low | n_low | slope_mid | r²_mid | n_mid |
|---|---|---|---|---|---|---|
| WB05-INV01 | **−0.000531** | 0.9980 | 32 | +0.000008 | 0.4446 | 32 |
| WB05-INV02 | **−0.000931** | 0.9993 | 32 | −0.000112 | 0.9933 | 32 |

### 3.2 Klasifikasi & severity

**INV01**: `slope_low < 0` **dan** `slope_mid ≥ 0` → **`low_irradiance_underperform`**. `score = |−0.000531|·0.998 = 0.00053` → ∈[4e-4, 8e-4) → **HIGH**. `confidence = 50+0.998·50 = 99.9`.

**INV02**: `slope_low < 0` **dan** `slope_mid < 0` → **`general_underperform`** (drop seragam → arahkan ke M2a Soiling). `score = |−0.000931|·0.999 = 0.00093` ≥ 8e-4 → **CRITICAL**. `confidence = 100.0`.

Keduanya emit (klasifikasi ≠ normal, `r²_low ≥ 0.3`). Disambiguasi inilah kontribusi utama detektor: INV01 = masalah hardware low-light spesifik (drone scan), INV02 = masalah menyeluruh (kemungkinan soiling).

---

## 4. Pemetaan Python → Excel

Empat sheet, **semua formula live**. OLS direproduksi via `SUMPRODUCT` dengan mask (inverter × band) — **tanpa array formula** (aman LibreOffice), identik `SLOPE`/`INTERCEPT`/`RSQ`.

### 4.1 `Helpers_LI` (128 baris)

| Kolom | Formula |
|---|---|
| pr_proxy | `=Raw_Data_LI!D{r}/Raw_Data_LI!C{r}` (= P_inv/POA) |
| in_low | `=IF(AND(POA>=cfg_li_poa_low_min,POA<=cfg_li_poa_low_max),1,0)` |
| in_mid | `=IF(AND(POA>=cfg_li_poa_mid_min,POA<=cfg_li_poa_mid_max),1,0)` |

### 4.2 `M2a_LowIrradiance` — regresi SUMPRODUCT + keputusan

Sum antara (kolom P–AA) per inverter, mask = `(inverter=X)·in_band`:

$$S_x=\sum m\,x, \quad S_y=\sum m\,y, \quad S_{xx}^{raw}=\sum m\,x^2, \quad S_{xy}^{raw}=\sum m\,xy, \quad S_{yy}^{raw}=\sum m\,y^2, \quad n=\sum m$$

Lalu (bentuk tercentang, identik OLS):

$$\text{slope} = \frac{S_{xy}^{raw} - S_x S_y/n}{S_{xx}^{raw} - S_x^2/n}, \qquad r^2 = \frac{(S_{xy}^{raw} - S_x S_y/n)^2}{(S_{xx}^{raw} - S_x^2/n)(S_{yy}^{raw} - S_y^2/n)}$$

| Kolom | Formula (low band; mid analog) |
|---|---|
| n_lo (P) | `=SUMPRODUCT((Helpers!A=A{r})*Helpers!in_low)` |
| Sxy_lo (T) | `=SUMPRODUCT((..=A)*in_low*POA*pr)` |
| slope_low (B) | `=(T-Q*R/P)/(S-Q*Q/P)` |
| r2_low (D) | `=(T-Q*R/P)^2/((S-Q*Q/P)*(U-R*R/P))` |
| classification (I) | `=IF(n_low<min,"insufficient_data",IF(AND(slope_low<thr,slope_mid>=thr),"low_irradiance_underperform",IF(AND(slope_low<thr,slope_mid<thr),"general_underperform","normal")))` |
| score (N) | `=ABS(slope_low-thr)*MAX(0,MIN(1,r2_low))` |
| severity (J) | `=IF(slope_low>=thr,"INFO",IF(score>=0.0008,"CRITICAL",IF(score>=0.0004,"HIGH",IF(score>=0.0001,"MEDIUM","INFO"))))` |
| emit (K) | `=IF(AND(class<>"normal",class<>"insufficient_data",r2_low>=cfg_li_r2_min),1,0)` |
| confidence (L) | `=50+r2_low*50` |

Conditional formatting mewarnai severity. `LI_Summary` = `COUNTIF` per klasifikasi + `SUM(emit)`.

---

## 5. Edge Cases & Limitasi Translasi

### 5.1 Reproduksi PENUH (kontras dengan iForest)

Berbeda dari Iterasi 6 (iForest = approksimasi MAD), detektor ini **direproduksi eksak**. OLS = `SLOPE`/`INTERCEPT`/`RSQ`; verifikasi numerik menunjukkan slope/intercept/r² Excel-form **identik** dengan `linear_regression_slope` (selisih < 5e-6). Tidak ada caveat metodologis pada skornya.

### 5.2 Hal yang disederhanakan di Excel

- **P_inv disediakan langsung** di `Raw_Data_LI`. Production menjumlah `PV{n} Power(kW)` (atau `V·I/1000`) per timestamp; penjumlahan itu trivial dan tidak mengubah regresi. PR-proxy = P_inv/POA tetap dihitung live.
- **Daylight gate = semua sampel** (di-anggap lolos POA>0 & solar-elev>5° & jam<18 & shutdown). Gate solar-elev/shutdown berbasis ephemeris → tak direplika statis (sama seperti iterasi lain).
- **Data sintetis**: 32+32 sampel per band dikonstruksi (POA tidak mengikuti kurva-waktu mulus) untuk mengontrol jumlah sampel band & slope demonstrasi. Di lapangan, distribusi band datang dari kurva POA harian.
- **`min_low_samples=30`** dihormati (demo 32/band). Bila < 30, klasifikasi `insufficient_data` (tak emit) — formula `classification` sudah meng-handle.

### 5.3 Catatan fisika

Slope NEGATIF di band low adalah sinyal *kontra-intuitif tapi benar*: PR-proxy = P/POA seharusnya **stabil/naik** saat POA naik untuk modul sehat (efisiensi low-light baik). Slope negatif = modul kehilangan PR justru saat butuh (Rs tinggi). Cross-check mid-band mencegah salah-tuduh: drop di KEDUA band bukan spesifik low-light (→ soiling).

---

## 6. Cross-Check vs Spec & Config

| Aspek | Kode (`low_irradiance.py`) | Config (aktif) | Workbook |
|---|---|---|---|
| band low / mid | [50,250] / [300,800] | sama | **identik** ✅ |
| slope_threshold | 0.0 | 0.0 | **identik** ✅ |
| r_squared_min | 0.3 | 0.3 | **identik** ✅ |
| min_low_samples | 30 | 30 | **identik** ✅ |
| OLS | `linear_regression_slope` | — | **reproduksi eksak** ✅ |
| severity ladder | 8e-4/4e-4/1e-4 × r² | — | **identik** ✅ |

**Catatan:**

1. **Tidak ada divergensi** config vs default vs workbook — semua selaras.
2. **Bukan rule §4.2.x utama.** Ini detektor "Fase 3 Part 2 Task #6", melengkapi famili M2a. Docstring eksplisit: `general_underperform` → diserahkan ke **M2a Soiling** (Task #5) yang butuh ≥6 bulan data + presipitasi.
3. **Disambiguasi adalah nilai utama.** Banyak detektor menandai "underperform"; ini memisahkan *low-light-spesifik* (Rs tinggi, fixable per-modul) dari *menyeluruh* (sistemik).

---

## 7. Verification Log

| # | Cek | Metode | Hasil |
|---|---|---|---|
| 1 | Build 4 sheet baru | `_extend_m2_workbook_iter7.py` | 29 → 33 sheet, 27 sheet lama + Config/README append utuh ✅ |
| 2 | Audit string formula | `verify_iter7.py` (A) | Helpers (pr_proxy/in_low/in_mid) + Decision (SUMPRODUCT, slope/r², classification, emit) == template ✅ |
| 3 | Recompute numerik | `verify_iter7.py` (B) | slope_low −0.000531/−0.000931, r²_low 0.998/0.999, klasifikasi & severity cocok proto ✅ |
| 4 | OLS = SUMPRODUCT-form | proto cross-check | slope/intercept/r² Excel-form == `linear_regression_slope` (selisih < 5e-6) ✅ |
| 5 | Reprodusibilitas | restore backup → rebuild → diff | **0 cell-diff** lintas 4 sheet + Config + README ✅ |
| 6 | Sheet lama utuh | diff vs backup | **0 diff** pada 27 sheet sebelumnya ✅ |

⚠️ Live recalc LibreOffice tidak dijalankan (crash sandbox). Verifikasi via audit formula + recompute Python + 0-diff regen. Karena OLS = fungsi Excel standar, kepercayaan reproduksi tinggi; recalc visual tetap disarankan.

---

## 8. Rekomendasi Penggunaan Workbook

- `M2a_LowIrradiance` menampilkan slope/r² kedua band + klasifikasi berwarna severity. `Helpers_LI` menampilkan PR-proxy & band membership.
- Ganti `Raw_Data_LI` dengan POA & P_inv aktual (≥30 sampel/band per inverter) → semua regresi update saat recalc.
- Ubah band/threshold via `Config` (`cfg_li_*`).
- Untuk inverter `general_underperform`, lanjutkan ke analisis soiling (butuh data time-series panjang).

---

## 9. Pertanyaan untuk Iterasi Berikutnya

1. **Iterasi 8 — M2a Shading** (Excel-friendly: CV per jam + PR-proxy + asimetri diurnal AM/PM). Lanjut ke sana?
2. **Iterasi 9 — M2a Soiling**: ini **skeleton** yang memanggil `rdtools.soiling_srr()` (Monte-Carlo 1000-rep, butuh ≥90 hari data + presipitasi). Kendalanya seperti iForest (stokastik + lib eksternal) — hanya bagian ekonomi (payback = biaya/loss) yang Excel-friendly. Mau pendekatan dokumentasi+approksimasi+caveat, atau dokumentasi murni?
3. Apakah perlu satu **dokumen ringkasan famili M2** setelah semua detektor selesai (peta detektor → fault → severity)?

---

## Sources

- `pv_pipeline/m2a/low_irradiance.py` (516 baris) — full read: `run()`, `linear_regression_slope`, `classify_underperformance`, `_severity_from_slope`, `build_inverter_power_series`, `_build_gate_mask`
- `pv_pipeline/core.py` — `M2Finding`, `Severity`, `SubModule`, `load_empty_pv_map`
- `config/m2_config.yaml` — `m2a_low_irradiance` (band [50,250]/[300,800], slope_threshold 0, r_squared_min 0.3, min 30)
- `docs/_extend_m2_workbook_iter7.py` (280 baris) — build script 4 sheet (regresi SUMPRODUCT live, regen 0-diff)
- `docs/verify_iter7.py` — audit string formula + recompute OLS vs proto
- `outputs/proto_iter7.py` — prototipe pengunci angka (deterministic, OLS dua band, SUMPRODUCT == linreg)
- `docs/M2_PV_Performance_Workbook.xlsx` — 33 sheet; `Raw_Data_LI`, `Helpers_LI`, `M2a_LowIrradiance`, `LI_Summary`
- Verified: audit formula + Python recompute vs literal workbook + regen 0-diff (reproduksi PENUH, bukan approksimasi)
