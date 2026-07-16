# M2 Reverse Engineering — Iterasi 4: M2bOpenCircuit

**Modul**: `pv_pipeline/open_circuit.py` (522 baris)
**Class utama**: `M2bOpenCircuit(SubModule)` — `name = "M2b_open_circuit"`
**Spec referensi**: Master Context §4.2.3 — *Open circuit / blown fuse*: `I_string < 5% × I_q95` saat `POA > 200 W/m²`, confidence 95%
**Dependency**: `POAProvider` (`get_poa`, `get_solar_elevation`), `load_empty_pv_map` (core), opsional Hampel preprocessing
**Dipanggil di**: pipeline M2 engine, `M2bOpenCircuit(poa=prov)`
**Output sheet Python**: `StringStatus` (+ `PreprocessingAudit` opsional saat Hampel aktif)
**Output Excel workbook**: sheet `Raw_Data_OC`, `Helpers_OC`, `M2b_OpenCircuit`, `M2b_OC_StringStatus` di `docs/M2_PV_Performance_Workbook.xlsx` (20 sheet saat iterasi 4; kini 46)
**Status verifikasi**: ✅ Python reference cocok dengan literal data workbook (5 PV × 26 timestep) + audit formula string per sel + regen 0-diff. ⚠️ Live recalc LibreOffice **tidak** dijalankan (binary crash di sandbox) — lihat Section 7.

---

## 1. Gambaran Open-Circuit

Detector ini menangkap **string yang arusnya kolaps relatif terhadap saudara satu inverter** — gejala open-circuit, fuse putus, atau konektor lepas. Logikanya per-string membandingkan arus string terhadap kuantil-95 arus *seluruh sibling string di inverter yang sama, per timestamp*:

$$\text{ratio}(t) = \frac{I_{\text{string}}(t)}{\max\!\big(I_{q95}(t),\,0.01\big)}, \qquad I_{q95}(t) = \operatorname{PERCENTILE}_{0.95}\big(I_{\text{PV1}}(t),\dots,I_{\text{PV}n}(t)\big)$$

String sehat punya `ratio ≈ 1` (mengikuti fleet). String open-circuit punya `ratio → 0`. Sebuah langkah dihitung *qualifying* bila `ratio < 0.05` **dan** timestamp itu lolos gate daylight. Supaya tidak ter-trigger glitch sesaat, qualifying harus **persisten** — minimal `debounce = 20` langkah konsekutif (≈ 100 menit pada cadence 5-menitan) baru di-emit sebagai event.

Ini detector paling produksi-kritis di IKN (799 CRITICAL persisten pada run nyata). Karena itu dua guard penting menempel di sini: **debounce panjang** (lawan noise) dan **skip slot PV kosong** (lawan false-positive dari slot yang Huawei laporkan sebagai 0 A — lihat §5.1).

Emit → `Severity.CRITICAL`, `fault_type = "open_circuit"`, `confidence = 95`, `value = ratio_median` (median ratio sepanjang daylight), `threshold = 0.05`.

---

## 2. Pipeline `M2bOpenCircuit.run()` — Step by Step

### Langkah 1 — Baca config & threshold (baris 112-126)

Semua angka dibaca dari `config["m2b_open_circuit"]` dengan fallback ke konstanta modul. Nilai produksi (config YAML) vs default kode:

| Parameter | Config YAML (aktif) | Default kode | Catatan |
|---|---|---|---|
| `poa_threshold_wm2` | **700** | `200` (baris 29) | divergen — lihat §6 |
| `poa_floor_wm2` | 50 | 50 (baris 30) | hard floor sunset/twilight |
| `i_ratio_threshold` | 0.05 | 0.05 (baris 35) | selaras spec (5%) |
| `debounce_consecutive_steps` | **20** | `2` (baris 36) | divergen — lihat §6 |
| `confidence_pct` | 95 | 95 (baris 37) | selaras |
| `pv_max` | 28 | 28 | jumlah string per inverter |
| `min_peer_strings` | 3 | 3 | minimal sibling untuk q95 valid |
| `min_daylight_samples` | 5 | 5 | minimal sampel daylight |
| `filter_mode` | solar_elevation | solar_elevation (baris 33) | gate ephemeris |
| `solar_elevation_min_deg` | 5.0 | 5.0 (baris 34) | matahari di atas 5° |

### Langkah 2 — Guard kolom & Hampel opsional (baris 131-150)

Kalau `Inverter_ID` atau `Start Time` tidak ada → `warn` + return `[]` (baris 131-136). Bila `preprocessing.enabled` true, jalankan Hampel filter dulu (baris 141-148) dan simpan `PreprocessingAudit`. Default off.

### Langkah 3 — Normalisasi nama kolom I (baris 155-166)

Wave 11 hotfix #11: Huawei pakai casing campuran — PV1-PV14 `"PV{n} input current(A)"` (lowercase), PV15-PV28 `"PV{n} Input Current(A)"` (Title Case). Semua di-rename ke lowercase kanonik supaya PV15-PV28 ikut teranalisis.

### Langkah 4 — Load empty-PV map sekali (baris 176-177)

`load_empty_pv_map(config)` → `{INV_ID_upper: [pv_indices_kosong]}` dari `config/strings.yaml`. Dipakai di main loop untuk **skip** slot kosong (baris 285).

### Langkah 5 — Loop `source × inverter` (baris 182-183)

Loop luar `poa_source` (multi-source POA), loop dalam `groupby("Inverter_ID")`. Per inverter ambil daftar slot kosong (`_inv_empty_set`), bersihkan timestamp, gate `min_daylight_samples` (baris 189-190).

### Langkah 6 — POA gate komposit: 3 kondisi di-AND (baris 195-265)

Inilah inti gating. `daylight_mask` adalah **AND dari tiga sub-gate**:

1. **POA** (baris 210): `(POA > poa_threshold) & (POA > poa_floor)` → `(POA > 700) & (POA > 50)`.
2. **Waktu/elevasi** (baris 212-244): mode `solar_elevation` → `(elevasi_matahari > 5°) & (jam < 18.0)` (Wave 11 hotfix #7, defensive AND dengan hour cutoff). Fallback ke `hour_cutoff` murni bila ephemeris gagal.
3. **Shutdown inverter** (baris 246-262): `ts < Inverter_shutdown_time` (drop sentinel tahun < 2000, skip bila mask buang semua baris).

```
daylight_mask = daylight_poa & daylight_time & daylight_shutdown   (baris 264)
```

Gate `min_daylight_samples` diuji lagi setelahnya (baris 266-267). **Sub-gate #2 dan #3 berbasis ephemeris/metadata → tidak direproduksi di Excel statis** (lihat §5.3).

### Langkah 7 — `I_q95` per timestamp across siblings (baris 269-279)

Kolom arus tersedia dikumpulkan (`i_cols`, gate `min_peer_strings`). Lalu:

```python
I_matrix      = group_clean[i_cols].apply(pd.to_numeric, errors="coerce")   # baris 277
I_q95_per_ts  = I_matrix.quantile(0.95, axis=1)                              # baris 279
```

`axis=1` → kuantil **across sibling strings per baris waktu**, bukan time-series. `quantile` pakai interpolasi linear, identik dengan Excel `PERCENTILE`/`PERCENTILE.INC` (dibuktikan numerik di §3.2 & §7). Slot kosong (PV5) **tetap masuk** matriks q95 sebagai 0 — harmless karena ada di low-end distribusi (§5.1).

### Langkah 8 — Per string: ratio, qualifying, debounce, emit (baris 281-348)

```python
if pv_n in _inv_empty_set:        # baris 285 — SKIP slot kosong
    continue
ratio       = I_string / I_q95_per_ts.clip(lower=0.01)              # baris 290
qualifying  = (ratio < i_ratio_threshold) & daylight_mask           # baris 291
n_events, total_steps = count_debounced_events(qualifying, debounce_steps)  # baris 292
emitted     = n_events > 0                                          # baris 312
```

`count_debounced_events` (baris 60-87) menghitung jumlah *run* True dengan panjang ≥ `debounce_steps`, plus tail group. `emitted = n_events > 0`. Bila emit → `M2Finding(severity=CRITICAL, value=ratio_median, threshold=0.05, fault_type="open_circuit", confidence=95, evidence={...})` (baris 313-348).

### Langkah 9 — Artifact StringStatus + top-up + fan-out (baris 350-432)

Tiap string (termasuk yang tidak emit) menambah baris ke `artifact_rows` dengan `status = "open_circuit" if emitted else "NORMAL"`, plus median & hitungan (baris 350-364). Lalu **top-up** seluruh inventori PV (baris 366-401): slot kosong → `status="EMPTY"`, slot tanpa data → `"NORMAL"`. **Fan-out** placeholder NORMAL bila main loop kosong (baris 403-428, supaya sheet `StringStatus` selalu muncul). Hasil akhir `self.artifacts["StringStatus"]` (baris 430-432).

---

## 3. Worked Example — Numerik Step-by-Step

Skenario sintetis (sheet `Raw_Data_OC`): inverter **WB05-INV05**, 5 string, 26 timestep — 24 daylight (12:00–13:55 @ 5-min, POA = 900) + 2 twilight (18:10 & 18:20, POA = 120).

| String | Profil arus (A) | Intent |
|---|---|---|
| PV1 | 13.0 | sehat |
| PV2 | 12.8 | sehat |
| PV3 | 0.10 | **open-circuit genuine** (sustained sepanjang daylight) |
| PV4 | 12.9, kecuali 3 langkah glitch (baris 9–11 = 0.05) | glitch sesaat |
| PV5 | 0.0 | **slot KOSONG** (Huawei lapor 0) |

### 3.1 Daylight gate (sub-gate POA saja, di Excel)

`daylight = (POA > 700) & (POA > 50)`. Baris noon POA = 900 → 1 (daylight). Twilight POA = 120 → `120 > 700` false → 0. Hasil: **24 / 26 daylight** (2 twilight ter-gate). ✔ cocok proto & verify.

### 3.2 `I_q95` per baris (PERCENTILE.INC across 5 string)

Baris noon, arus terurut = `[0.0, 0.10, 12.8, 12.9, 13.0]`. Posisi `0.95 × (5−1) = 3.8` → antara indeks 3 (12.9) dan 4 (13.0):

$$I_{q95} = 12.9 + 0.8 \times (13.0 - 12.9) = \mathbf{12.98}$$

Baris glitch (PV4 = 0.05), terurut = `[0.0, 0.05, 0.10, 12.8, 13.0]` → `12.8 + 0.8 × (13.0 − 12.8) = ` **12.96**. ✔ cocok.

### 3.3 Ratio, qualifying, debounce per string

| String | ratio_median (daylight) | max run konsekutif | `n_events` (debounce 20) | Emit |
|---|---|---|---|---|
| PV1 | 13.0/12.98 = **1.0015** | 0 | 0 | tidak |
| PV2 | 12.8/12.98 = **0.9861** | 0 | 0 | tidak |
| PV3 | 0.10/12.98 = **0.0077** | **24** | 1 | **YA** |
| PV4 | 12.9/12.98 = **0.9938** | 3 (glitch) | 0 | tidak (3 < 20) |
| PV5 | 0.0/12.98 = 0.0 | 24 | (1) | **skip (EMPTY)** |

### 3.4 Keputusan akhir

**PV3** — `ratio_median = 0.008 < 0.05` selama 24 langkah ≥ debounce 20 → **CRITICAL, conf 95%**, `value = ratio_median ≈ 0.008`.
**PV4** — glitch hanya 3 langkah `< 20` → debounce menelan → **tidak emit** (justru fungsi utama debounce).
**PV5** — meski `ratio = 0` qualifying penuh (run 24), slot kosong di-skip (baris 285) → tidak emit, di top-up jadi `status = "EMPTY"`.
**PV1, PV2** — `ratio ≈ 1` → NORMAL.

> Catatan: kalau PV5 *tidak* di-skip, run 24 ≥ 20 akan meng-emit CRITICAL palsu. Inilah guard Wave 11 hotfix #10 (§5.1).

---

## 4. Pemetaan Python → Excel Formula

Kunci translasi: **`emit ⟺ MAX(running_consec) ≥ debounce`** — ekuivalen dengan `count_debounced_events > 0` untuk kasus single-run, dan diimplementasi tanpa array formula (aman LibreOffice).

### 4.1 Sheet `Helpers_OC` — derived per baris (5–30)

| Kolom | Isi | Formula (baris `ri`) |
|---|---|---|
| C | daylight | `=IF(AND(B{ri}>cfg_oc_poa_threshold_wm2,B{ri}>cfg_oc_poa_floor_wm2),1,0)` |
| D | `I_q95` | `=PERCENTILE(Raw_Data_OC!C{ri}:G{ri},0.95)` |
| E–I | ratio PV1–5 | `=Raw_Data_OC!{rawc}{ri}/MAX($D{ri},cfg_oc_iq95_clip)` |
| J–N | qualifying PV1–5 | `=IF(AND({ratc}{ri}<cfg_oc_i_ratio_threshold,$C{ri}=1),1,0)` |
| O–S | running consec PV1–5 | baris pertama `={qc}{ri}`; selanjutnya `=IF({qc}{ri}=1,{cc}{ri-1}+1,0)` |

`MAX($D, cfg_oc_iq95_clip)` mereplikasi `I_q95_per_ts.clip(lower=0.01)` (baris 290).

### 4.2 Sheet `M2b_OpenCircuit` (keputusan, baris 5–9 = PV1–5)

| Kolom | Isi | Formula |
|---|---|---|
| B | I median daylight | `=MEDIAN(Raw_Data_OC!{rawc}5:{rawc}28)` |
| C | I_q95 median daylight | `=MEDIAN(Helpers_OC!D5:D28)` |
| D | ratio median (= `value`) | `=MEDIAN(Helpers_OC!{ratc}5:{ratc}28)` |
| E | max run konsekutif | `=MAX(Helpers_OC!{cons}5:{cons}30)` |
| F | empty_by_design | literal 1 untuk PV5, else 0 |
| G | emit | `=IF(AND(E{r}>=cfg_oc_debounce_steps,F{r}=0),1,0)` |
| H | status | `=IF(F{r}=1,"EMPTY",IF(G{r}=1,"open_circuit","NORMAL"))` |
| I/J/K | severity / confidence / message | CRITICAL · `cfg_oc_confidence_pct` · teks |

Kolom F (empty_by_design) memodelkan skip baris 285: 
`emit = (max_consec ≥ debounce) AND (bukan empty)`.

### 4.3 Sheet `M2b_OC_StringStatus`

Replika artifact Python (baris 350-364): `poa_source`, `inverter_id`, `wb_id`, `pv_string`, `status` (=`M2b_OpenCircuit!H`), median (=B/C/D), `n_qualifying_steps` (=`SUM` kolom qual Helpers), `n_debounced_events` (=G), `emitted_finding`, `daylight_samples` (=`SUM` kolom C Helpers).

### 4.4 Conditional formatting

`CellIsRule` pada kolom status: `"open_circuit"` merah, `"NORMAL"` hijau, `"EMPTY"` abu — di `M2b_OpenCircuit!H5:H9` dan `M2b_OC_StringStatus`.

---

## 5. Edge Cases & Limitasi Translasi

### 5.1 Skip slot PV kosong — guard false-positive terpenting (baris 284-286)

Slot PV yang secara fisik kosong tetap dilaporkan Huawei sebagai **0 A**. Tanpa skip: `ratio = 0/I_q95 = 0 < 0.05` qualifying penuh sepanjang daylight → run ≥ debounce → **CRITICAL palsu**. Komentar source (baris 173-175) mencatat dampak nyata: **910 dari 1709 CRITICAL** pada run 2026-05-14 adalah false-positive slot kosong sebelum guard ini. Di Excel: kolom `F` (empty_by_design) = 1 untuk PV5 → `emit = AND(...,F=0)` menelannya, `status = "EMPTY"`. Demo membuktikan PV5 punya `max_consec = 24 ≥ 20` namun **tidak** emit.

`I_q95` **tetap** menyertakan 0 dari slot kosong (baris 277 pakai semua `i_cols`). Harmless: 0 ada di low-end, kuantil-95 menarik dari high-end, jadi q95 nyaris tak terpengaruh (12.98 dengan PV5=0).

### 5.2 Debounce sebagai filter glitch (PV4)

PV4 glitch 3 langkah membuktikan nilai debounce: `3 < 20` → ditelan. Ini sengaja — open-circuit nyata bertahan ≥ 100 menit, glitch sensor/awan sesaat tidak. Trade-off: open-circuit yang sembuh < 100 menit tidak ter-flag (by design).

### 5.3 Hal yang berbeda / disederhanakan di Excel

- **Daylight Excel = sub-gate POA saja.** Produksi meng-AND dua gate ephemeris lagi: (a) `elevasi_matahari > 5° & jam < 18` dan (b) `ts < shutdown_inverter`. Keduanya butuh efemeris matahari / metadata inverter → **tidak direproduksi di sheet statis**. Demo meng-gate twilight murni lewat POA (120 < 700), cukup untuk mendemonstrasikan mekanika.
- **`count_debounced_events` → MAX(running_consec).** Ekuivalen untuk *emit ya/tidak* (single sustained run). Untuk multi-run terpisah, Python menghitung `n_events` & `total_steps`; Excel hanya butuh "ada run ≥ debounce?" (`MAX ≥ debounce`), yang cukup untuk keputusan emit.
- **`last_qual_ts`** (baris 301-310, Wave 11 hotfix #9) tidak dimodelkan — hanya mempengaruhi `Finding.timestamp`, bukan keputusan emit.
- **Multi-source POA** (loop baris 182) tidak dimodelkan — demo single source.

---

## 6. Cross-Check vs Master Context Spec

| Aspek | Spec §4.2.3 | Default kode | Config YAML (aktif) | Verdict |
|---|---|---|---|---|
| Rasio arus | `I < 5% I_q95` | 0.05 | 0.05 | ✅ selaras |
| POA threshold | `> 200 W/m²` | 200 (baris 29) | **700** | ⚠️ **divergen** |
| Debounce | (tidak disebut eksplisit) | 2 (baris 36) | **20** | ⚠️ **divergen** dari default |
| Confidence | 95% | 95 | 95 | ✅ selaras |
| Scope q95 | across sibling, per ts | `axis=1` | — | ✅ selaras |

**Dua divergensi yang harus disurface (Rule 12 fail-loud):**

1. **POA 200 → 700.** Spec & default kode pakai 200; konfigurasi produksi menaikkan ke 700 W/m². Efek: gate lebih ketat, hanya jam terang penuh dianalisis → mengurangi false-positive saat irradiance rendah, tapi bisa melewatkan open-circuit di pagi/sore berawan. Yang dipakai runtime = **700** (config menang atas default).
2. **Debounce 2 → 20.** Default kode 2 langkah (≈10 menit); produksi 20 langkah (≈100 menit). Jauh lebih konservatif — sengaja untuk situs 799-CRITICAL agar hanya fault yang benar-benar persisten lolos. Yang dipakai runtime = **20**.

Keduanya **bukan bug** — config sengaja meng-override default. Tapi siapa pun yang membaca spec/docstring (200, dan debounce default 2) akan salah memprediksi perilaku runtime tanpa membaca YAML. Direkomendasikan: sinkronkan docstring §spec dengan nilai produksi, atau beri catatan eksplisit.

---

## 7. Verification Log

| # | Cek | Metode | Hasil |
|---|---|---|---|
| 1 | Build 4 sheet baru | `_extend_m2_workbook_iter4.py` | 16 → 20 sheet, sheet lama tak berubah ✅ |
| 2 | Audit string formula | `verify_iter4.py` (A) | tiap sel Helpers (baris 5–30) & Decision (5–9) == template ✅ |
| 3 | Recompute numerik | `verify_iter4.py` (B) | I_q95 noon 12.98, glitch 12.96; PV3 emit, PV4/PV5 tidak ✅ |
| 4 | PERCENTILE.INC parity | `np.quantile(...,method="linear")` vs manual interp | 12.98 = 12.98 ✅ |
| 5 | Guard empty-PV | assert `F9=1` & PV5 run 24 ≥ 20 tapi tak emit | ✅ skip terbukti material |
| 6 | Reprodusibilitas | restore backup → rebuild → diff | **0 cell-diff** lintas 4 sheet + Config + README ✅ |
| 7 | Config named cells | baca `cfg_oc_*` dari workbook | 700/50/0.05/20/95/0.01 ✅ |

⚠️ **Live recalc LibreOffice tidak dijalankan** — binary crash di sandbox. Library evaluator (`formulas`, `pycel`) tidak ter-install (pip timeout / offline). Verifikasi karena itu bertumpu pada **(a) audit string formula per sel** (menjamin formula me-refer sel yang benar) **+ (b) reimplementasi semantik Excel di Python** (menjamin angka benar) **+ (c) regen 0-diff** (menjamin determinisme). Ketiganya lolos. Recalc visual Excel tetap disarankan saat file dibuka user.

---

## 8. Rekomendasi Penggunaan Workbook

- Buka `M2b_OpenCircuit` untuk verdict per string; warna kolom status langsung terbaca (merah = open_circuit, abu = EMPTY).
- Ubah skenario lewat `Raw_Data_OC` (arus/POA) — semua Helpers & keputusan ikut update saat Excel recalc.
- Ubah threshold lewat `Config` (named cell `cfg_oc_*`) — mis. turunkan `cfg_oc_debounce_steps` ke 3 untuk melihat glitch PV4 ikut emit.
- Untuk audit slot kosong: kolom `F` (empty_by_design) di `M2b_OpenCircuit` adalah toggle guard Wave 11 hotfix #10.
- **Jangan** pakai workbook untuk reproduksi gate solar-elevation/shutdown — itu ephemeris, di luar cakupan sheet statis (§5.3).

---

## 9. Pertanyaan untuk Iterasi Berikutnya

1. **Sinkronisasi spec vs config**: apakah docstring §4.2.3 (POA 200) dan default kode (debounce 2) perlu di-update agar mencerminkan produksi (700 / 20), atau biarkan config sebagai single source of truth?
2. **Detector mana selanjutnya** — M2c Ground Fault, atau M2e (sudah di workbook) revisit? Konfirmasikan target Iterasi 5.
3. Apakah perlu sheet demo terpisah yang memodelkan **multi-run** open-circuit (string yang putus-nyambung) untuk menstres `count_debounced_events` `n_events > 1`?

---

## Sources

- `pv_pipeline/open_circuit.py` (522 baris) — full read: `M2bOpenCircuit.run()`, `count_debounced_events`, POA gate komposit, emit decision, top-up/fan-out artifact
- `pv_pipeline/core.py` — `M2Finding`, `Severity`, `SubModule`, `load_empty_pv_map`
- `config/m2_config.yaml` — section `m2b_open_circuit.*` (poa_threshold 700, i_ratio 0.05, debounce 20, confidence 95, filter_mode solar_elevation)
- `tests/unit/test_open_circuit.py` (264 baris) — encode intent: PV7 real-fault flagged, sunset bukan false-positive, evidence fields, StringStatus artifact, solar_elevation mode
- `docs/_extend_m2_workbook_iter4.py` (312 baris) — build script 4 sheet Iterasi 4 (formula reproducible, regen 0-diff)
- `docs/verify_iter4.py` — Python reference: audit string formula per sel + recompute numerik vs proto
- `outputs/proto_iter4.py` — prototipe pengunci angka (I_q95 12.98, PV3 emit, PV4 glitch suppressed, PV5 EMPTY skip)
- `docs/M2_PV_Performance_Workbook.xlsx` — 20 sheet; `Raw_Data_OC`, `Helpers_OC`, `M2b_OpenCircuit`, `M2b_OC_StringStatus`
- Master Context §4.2.3 (open circuit / blown fuse: I < 5% I_q95 @ POA > 200, conf 95%)
- Verified: audit string formula per sel + Python recompute vs literal workbook + regen 0-diff (LibreOffice recalc N/A — sandbox crash)
