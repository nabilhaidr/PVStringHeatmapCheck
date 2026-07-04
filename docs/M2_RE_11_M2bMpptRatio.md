# M2 Reverse Engineering — Iterasi 11: M2bMpptRatio

**Modul**: `pv_pipeline/mppt_ratio.py` (409 baris)
**Class utama**: `M2bMpptRatio(SubModule)` — `name = "M2b_mppt_ratio"`
**Spec referensi**: desain user 2026-06-12 (BUKAN Master Context lama). Sinyal **rasio arus** terhadap median partner **se-MPPT** (`mppt_map` di `config/strings.yaml`), ratio-gated, **bukan** z-score. Default `ratio_threshold = 0.85`, severity dari `ratio_event_median`, confidence `min(90, max(50, (1−rem)·100))`.
**Dependency**: `POAProvider` (`get_poa`, `get_solar_elevation`), `string_config.get_mppt_groups`, `load_empty_pv_map` (core), `open_circuit._find_shutdown_col` / `_wb_from_inverter_id` / `count_debounced_events`, opsional Hampel preprocessing
**Dipanggil di**: pipeline M2 engine, `M2bMpptRatio(poa=prov)` (wired 2026-06-12, lihat `_wire_mppt_ratio_cell4.py`)
**Output sheet Python**: `StringStatus` (+ `PreprocessingAudit` opsional saat Hampel aktif)
**Output Excel workbook**: sheet `Raw_Data_MR`, `Helpers_MR`, `M2b_MpptRatio`, `M2b_MR_StringStatus` di `docs/M2_PV_Performance_Workbook.xlsx` (kini 46 sheet) — build `_extend_m2_workbook_iter11.py`, verify `verify_iter11.py`
**Status verifikasi**: ✅ Python reference cocok dengan literal data workbook (9 PV × 2 MPPT × 26 timestep) + audit formula string per sel + regen 0-diff. ✅ **286 finding nyata** run `m2_findings_20251030.xlsx` 100% konsisten dengan ladder severity + formula confidence + `value==ratio_event_median` + `threshold==0.85`. ⚠️ Live recalc LibreOffice **tidak** dijalankan (binary crash di sandbox) — lihat §7. ⚠️ Recompute dari arus mentah per-string **tidak** dilakukan (data input per-string tidak ada di workspace; hanya `IKN Generation.xlsx` site-level) — verifikasi nyata bertumpu pada konsistensi internal 286 finding (§7).

---

## 1. Gambaran MPPT-Partner Ratio

Detector ini menangkap **string yang arusnya merosot relatif terhadap saudara satu MPPT** — gejala underperform partial (degradasi modul, mismatch, koneksi seri lemah, satu string dalam grup MPPT melemah). Berbeda dari `M2bOpenCircuit` yang membandingkan ke kuantil-95 **seluruh inverter**, detector ini membandingkan tiap string hanya ke **sibling se-MPPT** lewat **rasio arus per timestamp**:

$$\text{ratio}(t) = \frac{I_{\text{string}}(t)}{\max\!\big(\operatorname{median}(I_{\text{partner se-MPPT}}(t)),\,0.01\big)}$$

String sehat punya `ratio ≈ 1` (mengikuti partner se-MPPT). String underperform punya `ratio → 0`. Sebuah langkah dihitung *qualifying* bila `ratio < 0.85` (default) **dan** timestamp lolos gate daylight. Supaya tidak ter-trigger glitch sesaat, qualifying harus **persisten** — minimal `debounce = 20` langkah konsekutif (≈ 100 menit @ cadence 5-menit) baru di-emit sebagai event.

**Kenapa rasio, bukan z-score?** Grup MPPT kecil. `|z|` maksimum dalam grup-N = `(N−1)/√N` → N=2: 0.71, N=4: 1.5, N=5: 1.79 — semuanya di bawah `z_threshold = 2.5`. Z-score **secara matematis tidak feasible** untuk menandai outlier dalam grup sekecil ini, jadi rasio arus langsung adalah perbandingan paling apple-to-apple (string se-MPPT berbagi titik operasi tegangan yang sama). `min_partner_strings = 1` sengaja dipilih supaya grup 2-string (WB01–WB02, model `SUN2000-215KTL-H0`) tetap teranalisis dengan satu partner.

**Komplemen, bukan pengganti** detector inverter-wide:

- `M2bOpenCircuit` (inverter-wide `I_q95`) menangkap fault MPPT-level (semua string satu MPPT mati bersamaan) yang ratio-to-partner lewatkan — degradasi **simetris** satu MPPT penuh memberi `ratio ≈ 1` (semua partner ikut turun) sehingga tidak terdeteksi di sini.
- `M2bPeerZScore` (inverter-wide) tetap menangani sinyal `R_str` + `voc_ratio`.

PV string yang **tidak terdaftar** di `mppt_map`, WB tanpa mapping, atau slot **EMPTY** di-skip (tanpa fallback inverter-wide — itu wilayah detector lain).

Emit → `value = ratio_event_median`, `threshold = ratio_threshold` (0.85), `fault_type = "mppt_partner_underperform"`. Severity & confidence: lihat §2 Langkah 8.

---

## 2. Pipeline `M2bMpptRatio.run()` — Step by Step

### Langkah 1 — Baca config & threshold (baris 82-100)

Semua angka dibaca dari `config["m2b_mppt_ratio"]` dengan fallback ke konstanta modul. **Nilai produksi (YAML) = default kode — tidak ada divergensi** (kontras dengan OpenCircuit, §6):

| Parameter | Config YAML (aktif) | Default kode | Catatan |
|---|---|---|---|
| `poa_threshold_wm2` | 300 | 300 (baris 46) | moderate sun: arus stabil & proporsional |
| `poa_floor_wm2` | 50 | 50 (baris 47) | hard floor sunset/twilight |
| `ratio_threshold` | 0.85 | 0.85 (baris 52) | `I_string < 85%` median partner → qualifying |
| `ratio_high` | 0.50 | 0.50 (baris 53) | `ratio_event_median < 0.50` → HIGH |
| `ratio_critical` | 0.20 | 0.20 (baris 54) | `ratio_event_median < 0.20` → CRITICAL |
| `debounce_consecutive_steps` | 20 | 20 (baris 55) | ≈ 100 menit @ 5-min |
| `min_partner_strings` | 1 | 1 (baris 56) | grup 2-string tetap dianalisis |
| `pv_max` | 28 | 28 | jumlah string per inverter |
| `min_daylight_samples` | 5 | 5 | minimal sampel daylight |
| `filter_mode` | solar_elevation | solar_elevation (baris 50) | gate ephemeris |
| `solar_elevation_min_deg` | 5.0 | 5.0 (baris 51) | matahari di atas 5° |
| `mppt_map_path` | config/strings.yaml | config/strings.yaml (baris 57) | ground-truth grouping |

`enabled=false` → return `[]` (baris 83-84).

### Langkah 2 — Guard kolom & load `mppt_map` (baris 102-122)

Kalau `Inverter_ID` atau `Start Time` tidak ada → `warn` + return `[]` (baris 102-107). Lalu `get_mppt_groups(mppt_map_path, pv_max_allowed=pv_max)` (baris 110-112) → `{WB_id: {mppt_n: [pv_index, ...]}}`. Bila load gagal atau map kosong → `warn` + return `[]` (baris 113-122). **Ini ground-truth grouping** (user-confirmed 2026-06-11): tanpa `mppt_map`, detector tidak punya definisi "partner" dan dimatikan total.

### Langkah 3 — Shutdown col + Hampel opsional (baris 124-136)

`_find_shutdown_col` bila `respect_inverter_shutdown` (baris 124). Bila `preprocessing.enabled` true, jalankan Hampel filter dulu (baris 126-136) dan simpan `PreprocessingAudit`. Default off.

### Langkah 4 — Normalisasi nama kolom I + empty map (baris 138-150)

`_ensure_poa` + resolve `sources` (baris 138-139). Wave 11 hotfix #11: rename `"Input Current"` (Title Case, PV15–PV28) → `"input current"` kanonik (baris 141-147) supaya PV15–PV28 ikut teranalisis. `load_empty_pv_map(config)` (baris 149-150) untuk skip slot kosong.

### Langkah 5 — Loop `source × inverter` (baris 158-173)

Loop luar `poa_source` (multi-source POA), loop dalam `groupby("Inverter_ID")`. Per inverter: `wb_id = _wb_from_inverter_id(...)` (baris 160), ambil `wb_groups = mppt_groups.get(wb_id, {})` — **kalau kosong, skip inverter** (baris 161-163). Ambil `empty_set` (baris 164-166), bersihkan timestamp, gate `min_daylight_samples` (baris 167-173).

### Langkah 6 — POA gate komposit: 3 kondisi di-AND (baris 184-231)

`daylight_mask` adalah **AND dari tiga sub-gate** (identik pola OpenCircuit):

1. **POA** (baris 184-185): `(POA > poa_threshold) & (POA > poa_floor)` → `(POA > 300) & (POA > 50)`.
2. **Waktu/elevasi** (baris 187-213): mode `solar_elevation` → `(elevasi > 5°) & (jam < 18.0)` (Wave 11 hotfix #7, defensive AND dengan hour cutoff). Fallback ke `hour_cutoff` murni bila ephemeris gagal.
3. **Shutdown inverter** (baris 215-224): `ts < Inverter_shutdown_time` (drop sentinel tahun < 2000, skip bila mask buang semua baris).

```
daylight_mask = daylight_poa & daylight_time & daylight_shutdown   (baris 226-229)
```

Gate `min_daylight_samples` diuji lagi setelahnya (baris 230-231). **Sub-gate #2 dan #3 berbasis ephemeris/metadata → tidak direproduksi di Excel statis** (lihat §5.3).

### Langkah 7 — Matriks arus per string (baris 233-242)

```python
i_col_of  = {n: f"PV{n} input current(A)" for n in 1..pv_max if col ada}   # baris 233-237
I_matrix  = group_clean[list(i_col_of.values())].apply(pd.to_numeric, errors="coerce")  # baris 240-242
```

Bila tidak ada kolom arus sama sekali → `continue` (baris 238-239).

### Langkah 8 — Per MPPT → per string: median partner, ratio, debounce, emit (baris 244-355)

Loop `for mppt_n, members in wb_groups.items()` (baris 244). `present` = member yang **bukan** EMPTY dan **punya** kolom arus (baris 245-248). Untuk tiap `pv_n` di MPPT:

```python
if pv_n in empty_set:    # baris 250 — slot EMPTY → artifact "EMPTY", SKIP emit (baris 251-266)
    ...
partners = [m for m in present if m != pv_n]              # baris 269
if len(partners) < min_partner_strings: continue          # baris 270-271
I_string         = I_matrix[i_col_of[pv_n]]               # baris 273
partner_median_ts = I_matrix[[...partners]].median(axis=1, skipna=True)   # baris 274-276
ratio            = I_string / partner_median_ts.clip(lower=0.01)          # baris 277
qualifying       = (ratio < ratio_threshold) & daylight_mask             # baris 278
n_events, total_steps = count_debounced_events(qualifying, debounce_steps)  # baris 279
```

`median(axis=1)` → **median across sibling se-MPPT per baris waktu** (bukan time-series). `.clip(lower=0.01)` mencegah divide-by-zero saat seluruh partner = 0.

Lalu dua statistik ratio (baris 281-288):

- `ratio_median_daylight` = `median(ratio[daylight])` — median sepanjang daylight (informatif).
- `ratio_event_median` = `median(ratio[qualifying])` — **median hanya pada langkah qualifying** → ini yang dipakai severity & `value`.

Emit bila `n_events > 0` (baris 293-301):

```python
severity   = CRITICAL if rem < 0.20 else HIGH if rem < 0.50 else MEDIUM   # baris 295-300
confidence = min(90, max(50, (1 − rem)·100))                              # baris 301
```

(`rem` = `ratio_event_median`.) Emit → `M2Finding(value=rem, threshold=ratio_threshold, fault_type="mppt_partner_underperform", evidence={mppt, partner_strings, n_qualified_events, total_event_steps, ...})` (baris 302-339).

### Langkah 9 — Artifact StringStatus (baris 341-359)

Tiap string (emit/tidak) menambah baris `artifact_rows`: `status = "mppt_partner_underperform" if emitted else "NORMAL"` (slot kosong → `"EMPTY"`), plus `partner_strings`, `ratio_median_daylight`, `ratio_event_median`, `n_qualifying_steps`, `n_debounced_events`, `emitted_finding`, `daylight_samples` (baris 341-355). Hasil → `self.artifacts["StringStatus"]` (baris 357-359).

---

## 3. Worked Example — Numerik Step-by-Step

Skenario sintetis (sheet `Raw_Data_MR`): inverter **WB05-INV05** model `SUN2000-330KTL-H1`, **2 MPPT group** persis seperti `mppt_map` produksi, 26 timestep — 24 daylight (12:00–13:55 @ 5-min, POA = 900) + 2 twilight (18:10 & 18:20, POA = 120). Arus konstan per string (sun di-faktorkan keluar; ratio invarian terhadap irradiance).

| MPPT | String | Arus (A) | Intent |
|---|---|---|---|
| 1 = [1,2,3,4] | PV1 | 13.0 | sehat |
| 1 | PV2 | 12.8 | sehat |
| 1 | PV3 | **10.3** | **MEDIUM underperform** (sustained) |
| 1 | PV4 | **5.6** | **HIGH underperform** (sustained) |
| 2 = [5,6,7,8,9] | PV5 | 13.0 | sehat |
| 2 | PV6 | 12.8 | sehat |
| 2 | PV7 | 12.9 | sehat |
| 2 | PV8 | **0.0** | **CRITICAL** (string mati, bukan slot kosong) |
| 2 | PV9 | 12.85, kecuali 3 langkah glitch (baris 9–11 = 3.0) | glitch sesaat |

### 3.1 Daylight gate (sub-gate POA saja, di Excel)

`daylight = (POA > 300) & (POA > 50)`. Baris noon POA = 900 → 1 (daylight). Twilight POA = 120 → `120 > 300` false → 0. Hasil: **24 / 26 daylight** (2 twilight ter-gate).

### 3.2 Median partner se-MPPT per baris (per string, exclude diri sendiri)

Median dihitung **across partner se-MPPT**, tidak melibatkan string lain di MPPT berbeda. Karena median **mengabaikan** member yang excluded saat menghitung dirinya, satu string mati di grup **tidak** merusak baseline string lain:

**MPPT 1** (member [PV1,2,3,4]):

| String | Partner | Median partner | ratio = I / median |
|---|---|---|---|
| PV1 | PV2,3,4 = 12.8, 10.3, 5.6 | median = **10.3** | 13.0/10.3 = **1.262** → NORMAL |
| PV2 | PV1,3,4 = 13.0, 10.3, 5.6 | median = **10.3** | 12.8/10.3 = **1.243** → NORMAL |
| PV3 | PV1,2,4 = 13.0, 12.8, 5.6 | median = **12.8** | 10.3/12.8 = **0.8047** → qualifying |
| PV4 | PV1,2,3 = 13.0, 12.8, 10.3 | median = **12.8** | 5.6/12.8 = **0.4375** → qualifying |

Perhatikan: median partner PV3 & PV4 = **12.8** (robust) — meski PV4 (5.6) anggota grup, saat menghitung median partner PV3 yang dipakai PV1/PV2/PV4 dan median 3-angka mengambil **nilai tengah** = 12.8, tidak terseret PV4. (Sebaliknya, baseline PV1/PV2 yang sehat justru turun ke 10.3 karena setengah grup degraded — tapi mereka tetap NORMAL, ratio > 1.2.)

**MPPT 2** (member [PV5,6,7,8,9], grup bersih hanya 1 string mati):

| String | Partner | Median partner | ratio |
|---|---|---|---|
| PV5 | PV6,7,8,9 = 12.8, 12.9, 0.0, 12.85 | median = **12.825** | 13.0/12.825 = **1.014** → NORMAL |
| PV6 | 13.0, 12.9, 0.0, 12.85 | median = **12.875** | 12.8/12.875 = **0.994** → NORMAL |
| PV7 | 13.0, 12.8, 0.0, 12.85 | median = **12.825** | 12.9/12.825 = **1.006** → NORMAL |
| PV8 | 13.0, 12.8, 12.9, 12.85 | median = **12.875** | 0.0/12.875 = **0.0** → qualifying |
| PV9 | 13.0, 12.8, 12.9, 0.0 | median = **12.85** | normal 12.85/12.85 = **1.0**; glitch 3.0/12.85 = **0.233** |

String mati PV8 (0.0) **tidak** menyeret baseline PV5/6/7 (median 4-angka membuang 0.0 ke low-end) → grup bersih, semua sehat ber-ratio ≈ 1.

### 3.3 Qualifying, debounce, severity per string

| String | ratio | qualifying steps (daylight) | max run konsekutif | emit (debounce 20) | `ratio_event_median` | severity | confidence |
|---|---|---|---|---|---|---|---|
| PV1 | 1.262 | 0 | 0 | tidak | — | — | — |
| PV2 | 1.243 | 0 | 0 | tidak | — | — | — |
| PV3 | 0.8047 | 24 | 24 | **YA** | 0.8047 | **MEDIUM** | `max(50, 19.5)` = **50** |
| PV4 | 0.4375 | 24 | 24 | **YA** | 0.4375 | **HIGH** | `max(50, 56.25)` = **56.25** |
| PV5–7 | ≈ 1 | 0 | 0 | tidak | — | — | — |
| PV8 | 0.0 | 24 | 24 | **YA** | 0.0 | **CRITICAL** | `min(90, 100)` = **90** |
| PV9 | 1.0 (glitch 0.233 × 3) | 3 | 3 | tidak (3 < 20) | — | — | — |

### 3.4 Keputusan akhir

**PV3** — `ratio 0.805 < 0.85` selama 24 langkah ≥ debounce 20 → **MEDIUM, conf 50**, `value = 0.805`.
**PV4** — `ratio 0.438 < 0.50` → **HIGH, conf 56.25**, `value = 0.438`.
**PV8** — `ratio 0.0 < 0.20` → **CRITICAL, conf 90**, `value = 0.0`.
**PV9** — glitch hanya 3 langkah `< 20` → debounce menelan → **tidak emit** (fungsi utama debounce, lawan awan/sensor sesaat).
**PV1, 2, 5, 6, 7** — `ratio ≈ 1` → NORMAL.

> Untuk fault yang **sustained dengan ratio konstan** (kasus sintetis ini), `ratio_event_median == ratio_median_daylight` (semua langkah daylight qualifying), jadi severity bisa dihitung dari median daylight di sheet statis. Lihat §5.3.

---

## 4. Pemetaan Python → Excel Formula

Kunci translasi: **median partner = `MEDIAN` atas cell partner eksplisit** (keanggotaan MPPT statis dari `mppt_map`, jadi tak perlu exclude dinamis), dan **`emit ⟺ MAX(running_consec) ≥ debounce`** (tanpa array formula, aman LibreOffice).

### 4.1 Sheet `Helpers_MR` — derived per baris (5–30)

| Kolom | Isi | Formula (baris `ri`) |
|---|---|---|
| C | daylight | `=IF(AND(B{ri}>cfg_mr_poa_threshold_wm2,B{ri}>cfg_mr_poa_floor_wm2),1,0)` |
| D–L | median partner PV1–9 | `=MEDIAN(<cell partner se-MPPT>)` mis. PV1 `=MEDIAN(Raw_Data_MR!{c2}{ri},{c3}{ri},{c4}{ri})` |
| M–U | ratio PV1–9 | `=Raw_Data_MR!{rawc}{ri}/MAX({medc}{ri},cfg_mr_partner_clip)` |
| V–AD | qualifying PV1–9 | `=IF(AND({ratc}{ri}<cfg_mr_ratio_threshold,$C{ri}=1),1,0)` |
| AE–AM | running consec PV1–9 | baris pertama `={qc}{ri}`; selanjutnya `=IF({qc}{ri}=1,{cc}{ri-1}+1,0)` |

`MAX({medc},cfg_mr_partner_clip)` mereplikasi `partner_median_ts.clip(lower=0.01)` (baris 277). Kolom median partner per string memuat **hanya cell partner se-MPPT** (PV1→PV2,3,4; PV5→PV6,7,8,9; dst) — meniru `median(axis=1)` atas subset partner (baris 274-276).

### 4.2 Sheet `M2b_MpptRatio` (keputusan, baris 5–13 = PV1–9)

| Kolom | Isi | Formula |
|---|---|---|
| B | mppt | literal (1 untuk PV1–4, 2 untuk PV5–9) |
| C | partner_strings | literal teks (mis. "PV2, PV3, PV4") |
| D | ratio median daylight (= `value`) | `=MEDIAN(Helpers_MR!{ratc}5:{ratc}28)` |
| E | max run konsekutif | `=MAX(Helpers_MR!{cons}5:{cons}30)` |
| F | empty_by_design | literal 1 bila slot kosong, else 0 |
| G | emit | `=IF(AND(E{r}>=cfg_mr_debounce_steps,F{r}=0),1,0)` |
| H | severity | `=IF(G{r}=0,"",IF(D{r}<cfg_mr_ratio_critical,"CRITICAL",IF(D{r}<cfg_mr_ratio_high,"HIGH","MEDIUM")))` |
| I | confidence | `=IF(G{r}=0,"",MIN(90,MAX(50,(1-D{r})*100)))` |
| J | status | `=IF(F{r}=1,"EMPTY",IF(G{r}=1,"mppt_partner_underperform","NORMAL"))` |

Kolom F (empty_by_design) memodelkan skip baris 250: `emit = (max_consec ≥ debounce) AND (bukan empty)`.

### 4.3 Sheet `M2b_MR_StringStatus`

Replika artifact Python (baris 341-355): `poa_source`, `inverter_id`, `wb_id`, `pv_string`, `mppt`, `status` (=`M2b_MpptRatio!J`), `partner_strings`, `ratio_median_daylight` (=D), `ratio_event_median` (=D untuk sustained), `n_qualifying_steps` (=`SUM` kolom qualifying Helpers), `n_debounced_events` (1 bila emit), `emitted_finding` (=G), `daylight_samples` (=`SUM` kolom C Helpers).

### 4.4 Conditional formatting

`CellIsRule` pada kolom severity/status: `CRITICAL` merah, `HIGH` oranye, `MEDIUM` kuning, `mppt_partner_underperform` merah, `NORMAL` hijau, `EMPTY` abu.

---

## 5. Edge Cases & Limitasi Translasi

### 5.1 Median partner robust terhadap satu string mati (baris 274-276)

Ini keunggulan inti vs mean. Saat menghitung ratio PV8 (mati, 0.0), median partner = `MEDIAN(13.0, 12.8, 12.9, 12.85) = 12.875` — **tidak** terseret 0.0 karena PV8 sendiri excluded dari partnernya. Sebaliknya, saat menghitung baseline PV5 (sehat), 0.0-nya PV8 masuk partner tapi median 4-angka membuangnya ke low-end → median 12.825, PV5 tetap NORMAL. **Median sengaja dipilih** supaya satu-dua anggota busuk tidak meracuni baseline grup. Limitasi: bila **≥ 50%** grup degraded simetris, median ikut turun (baseline rusak) → underperform tak terdeteksi (itu domain `M2bOpenCircuit` inverter-wide; lihat §1).

### 5.2 Debounce sebagai filter glitch (PV9)

PV9 glitch 3 langkah membuktikan nilai debounce: `3 < 20` → ditelan. Underperform nyata bertahan ≥ 100 menit; glitch sensor/awan sesaat tidak. Trade-off: underperform yang sembuh < 100 menit tidak ter-flag (by design).

### 5.3 Hal yang berbeda / disederhanakan di Excel

- **Daylight Excel = sub-gate POA saja.** Produksi meng-AND dua gate ephemeris lagi: (a) `elevasi > 5° & jam < 18` dan (b) `ts < shutdown_inverter`. Keduanya butuh efemeris matahari / metadata inverter → **tidak direproduksi di sheet statis**. Demo meng-gate twilight murni lewat POA (120 < 300).
- **`ratio_event_median` → `MEDIAN(ratio daylight)`.** Python mengambil median ratio hanya pada langkah **qualifying** (baris 285-288). Untuk fault sustained ber-ratio konstan (skenario ini), setiap langkah daylight qualifying → kedua median identik. Untuk fault intermiten (ratio naik-turun lintas threshold), keduanya bisa beda; sheet statis tidak memodelkan itu (hanya butuh keputusan emit + severity untuk kasus sustained).
- **`count_debounced_events` → `MAX(running_consec)`.** Ekuivalen untuk *emit ya/tidak* (single sustained run). Python juga melaporkan `n_events` & `total_event_steps` untuk multi-run; Excel hanya butuh "ada run ≥ debounce?".
- **Multi-source POA** (loop baris 158) tidak dimodelkan — demo single source.
- **EMPTY skip** dimodelkan via kolom F; demo tidak memuat slot kosong dalam grup MPPT karena `mppt_map` SUN2000-330 memetakan PV1–28 penuh.

---

## 6. Cross-Check vs Spec & Config

Detector ini **tidak** punya entri di Master Context lama — ia desain baru 2026-06-12 (rasio se-MPPT). Cross-check terhadap docstring modul + config produksi + run nyata:

| Aspek | Docstring / desain | Default kode | Config YAML (aktif) | Verdict |
|---|---|---|---|---|
| Sinyal | ratio I vs median partner se-MPPT | `median(axis=1)` partner | — | ✅ selaras |
| Scope peer | sibling se-MPPT (`mppt_map`) | `get_mppt_groups` | strings.yaml | ✅ selaras |
| ratio_threshold | 0.85 | 0.85 | 0.85 | ✅ selaras |
| Severity ladder | <0.20 CRIT · <0.50 HIGH · else MED | sama | sama | ✅ selaras |
| Confidence | `min(90,max(50,(1−rem)·100))` | sama | — | ✅ selaras |
| Debounce | ≥ 20 langkah | 20 | 20 | ✅ selaras |
| min_partner_strings | 1 (grup 2-string) | 1 | 1 | ✅ selaras |

**Tidak ada divergensi config-vs-default** (berbeda dari OpenCircuit yang POA 200→700 & debounce 2→20). Semua nilai produksi = default kode = docstring. **Rule 12 (fail-loud): tidak ada yang perlu disurface** untuk detector ini.

**Validasi run nyata** (`m2_findings_20251030.xlsx`, src `pyranometer_per_ws`): **286 finding** M2b_mppt_ratio (281 MEDIUM, 5 CRITICAL). Verifikasi otomatis (§7) mengonfirmasi **100% konsisten**: tiap `severity` cocok ladder `ratio_event_median`, tiap `confidence` = `min(90,max(50,(1−rem)·100))`, `value == ratio_event_median`, `threshold == 0.85`, semua `n_qualified_events ≥ 1`. Confidence aktual: semua MEDIUM = 50, semua CRITICAL = 90 — persis prediksi formula. Contoh CRITICAL: `WB06-INV08 / PV20` (MPPT 5 = [19,20,21,22,23]), ratio = 0.0, partner PV21/22/23 sehat → string mati. Contoh MEDIUM: `WB02-INV14 / PV17` (MPPT 9 = [17,18] pada `SUN2000-215`), ratio = 0.799, **partner tunggal PV18** — membuktikan `min_partner_strings = 1` mengaktifkan analisis grup 2-string.

---

## 7. Verification Log

| # | Cek | Metode | Hasil |
|---|---|---|---|
| 1 | Build 4 sheet baru | `_extend_m2_workbook_iter11.py` | 42 → 46 sheet, sheet lama tak berubah ✅ |
| 2 | Audit string formula | `verify_iter11.py` (A) | tiap sel Helpers (5–30) & Decision (5–13) == template ✅ |
| 3 | Recompute numerik | `verify_iter11.py` (B) | median partner, ratio, qualifying, debounce, severity, confidence == proto ✅ |
| 4 | Median partner robust | assert median partner PV8 = 12.875 (buang 0.0), baseline PV5 NORMAL | ✅ |
| 5 | Severity ladder + confidence | PV3 MED/50, PV4 HIGH/56.25, PV8 CRIT/90 | ✅ |
| 6 | Guard debounce (glitch) | PV9 max_consec 3 < 20 → tak emit | ✅ |
| 7 | Reprodusibilitas | restore backup → rebuild → diff | **0 cell-diff** lintas 4 sheet + Config + index ✅ |
| 8 | Config named cells | baca `cfg_mr_*` dari workbook | 300/50/0.85/0.50/0.20/20/1/0.01 ✅ |
| 9 | **Konsistensi 286 finding nyata** | `m2_findings_20251030.xlsx`: severity/confidence/value/threshold vs formula | **286/286 cocok** ✅ |

⚠️ **Live recalc LibreOffice tidak dijalankan** — binary crash di sandbox (pola sama iter4–9). Verifikasi bertumpu pada **(a) audit string formula per sel** + **(b) reimplementasi semantik Excel di Python** + **(c) regen 0-diff** + **(d) konsistensi 286 finding produksi**. Recalc visual Excel tetap disarankan saat file dibuka user.

⚠️ **Recompute dari arus mentah per-string tidak dilakukan** — `raw data input/` hanya berisi `IKN Generation.xlsx` (generation site-level), bukan tabel arus per-string yang memberi makan run 2025-10-30. Verifikasi nyata karena itu memakai **konsistensi internal** 286 finding (output detector vs formula-nya sendiri), bukan rekomputasi end-to-end dari arus. Untuk rekomputasi penuh, jalankan ulang pipeline atas dump CSV per-string sumber.

---

## 8. Rekomendasi Penggunaan Workbook

- Buka `M2b_MpptRatio` untuk verdict per string; warna kolom severity/status langsung terbaca (merah CRITICAL/underperform, kuning MEDIUM, hijau NORMAL).
- Ubah skenario lewat `Raw_Data_MR` (arus/POA) — semua Helpers & keputusan ikut update saat Excel recalc. Coba turunkan PV3 dari 10.3 → 5.0 untuk melihat MEDIUM naik ke HIGH.
- Ubah threshold lewat `Config` (named cell `cfg_mr_*`) — mis. naikkan `cfg_mr_ratio_threshold` ke 0.95 untuk melihat lebih banyak string qualifying, atau turunkan `cfg_mr_debounce_steps` ke 3 supaya glitch PV9 ikut emit.
- `Helpers_MR` kolom median partner memperlihatkan **robustness median**: PV8 mati (0.0) tidak menyeret baseline partnernya.
- **Jangan** pakai workbook untuk reproduksi gate solar-elevation/shutdown — itu ephemeris, di luar cakupan sheet statis (§5.3).

---

## 9. Pertanyaan untuk Iterasi Berikutnya

1. **Severity event-median vs daylight-median**: sheet statis menyamakan keduanya (valid untuk fault sustained). Perlukah sheet demo terpisah memodelkan fault **intermiten** (ratio putus-nyambung lintas 0.85) untuk menstres beda `ratio_event_median` vs `ratio_median_daylight` dan `n_events > 1`?
2. **Grup ≥ 50% degraded**: detector ini buta terhadap degradasi simetris satu MPPT penuh (median ikut turun). Apakah perlu catatan eksplisit di dashboard bahwa kasus itu di-cover `M2bOpenCircuit`, supaya operator tidak salah kira "tidak ada finding = sehat"?
3. **Detector berikut** — semua famili M2b/M2a/M2e kini ber-RE-doc (RE_02..RE_11). Konfirmasi target iterasi 12: revisit detector lama, atau integrasi cross-detector (mis. korelasi MPPT-ratio × open-circuit × peer-zscore per string)?

---

## Sources

- `pv_pipeline/mppt_ratio.py` (409 baris) — full read: `M2bMpptRatio.run()`, median partner se-MPPT, ratio/qualifying/debounce, emit decision, StringStatus artifact, smoke test (`__main__`)
- `pv_pipeline/string_config.py` — `get_mppt_groups`, `mppt_of_pv`, `mppt_siblings_of_pv` (ground-truth grouping dari `mppt_map`)
- `pv_pipeline/open_circuit.py` — `count_debounced_events`, `_find_shutdown_col`, `_wb_from_inverter_id` (di-reuse)
- `pv_pipeline/core.py` — `M2Finding`, `Severity`, `SubModule`, `load_empty_pv_map`
- `config/m2_config.yaml` — section `m2b_mppt_ratio.*` (poa 300, ratio_threshold 0.85, high 0.50, critical 0.20, debounce 20, min_partner 1)
- `config/strings.yaml` — `mppt_map`: `SUN2000-215KTL-H0` (WB01-02, 9 MPPT × 2 string), `SUN2000-330KTL-H1` (WB03-10, 6 MPPT × 4–5 string)
- `_wire_mppt_ratio_cell4.py` — wire-up 7 edit ke Cell 4 pipeline (import, banner, instantiate, submodules list, EXCLUDE_CFG_MAP, summaries loop, audit loop)
- `notebook/20251030.ipynb` (cell 5) — M2 Pipeline UNIFIED dengan `sm_mppt = M2bMpptRatio(poa=poa_provider)` ter-wire
- `m2_findings_20251030.xlsx` — sheet `Findings` (286 M2b_mppt_ratio: 281 MEDIUM + 5 CRITICAL) + `M2b_mppt_ratio_StringStatus` (24 660 baris); validasi konsistensi 286/286
- `docs/_extend_m2_workbook_iter11.py` (build script 4 sheet Iterasi 11, formula reproducible, regen 0-diff) + `docs/verify_iter11.py` (audit string formula + recompute numerik + konsistensi finding nyata)
- `docs/M2_RE_03_M2bPeerZScore.md` (z-score infeasibility grup kecil), `docs/M2_RE_04_M2bOpenCircuit.md` (detector komplementer inverter-wide)
- Verified: audit string formula per sel + Python recompute vs literal workbook + regen 0-diff + 286 finding nyata konsisten (LibreOffice recalc N/A — sandbox crash; recompute arus mentah N/A — data per-string tak tersedia)
