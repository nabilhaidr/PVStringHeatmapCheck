# M2 Reverse Engineering — Iterasi 6: M2IForest (Isolation Forest)

**Modul**: `pv_pipeline/iforest.py` (508 baris)
**Class utama**: `M2IForest(SubModule)` — `name = "M2_iforest"`
**Sifat**: detektor **machine-learning** (sklearn `IsolationForest`), bukan rule fisika. **Opt-in** (`enabled` default `False`); di config IKN di-set `True` tapi `exclude_from_findings_sheet=True`.
**Dependency**: `sklearn.ensemble.IsolationForest` (auto-install via `_ensure_sklearn`), `POAProvider`, `load_empty_pv_map`
**Output sheet Python**: `AnomalyScores` (per-(inv, PV, ts) skor+flag) + `AnomalySummary` (per-inverter)
**Output Excel workbook**: sheet `Raw_Data_IF`, `Features_IF`, `IF_Anomaly`, `IF_Summary` di `docs/M2_PV_Performance_Workbook.xlsx` (kini 29 sheet)

> ⚠️ **CAVEAT UTAMA — baca dulu.** Detektor asli adalah **IsolationForest terlatih (100 pohon, contamination 0.01, seed 42)**. Skornya = panjang-lintasan ensembel = **black box terlatih yang TIDAK bisa direproduksi sebagai formula Excel**. sklearn juga **tidak tersedia di sandbox** (butuh scipy). Maka sheet Excel di sini adalah **APPROKSIMASI TRANSPARAN berbasis MAD** (sesuai rencana System Overview), yang **meniru STRUKTUR** detektor (fitur identik, per-inverter, flag fraksi contamination, severity kuartil, confidence) **tetapi memakai skor yang berbeda** (MAX robust-z, bukan path-length). **Approksimasi ini akan menandai sampel yang BERBEDA dari iForest asli.** Pakai untuk transparansi logika & severity, bukan sebagai pengganti detektor produksi. Detail di §5.

**Status verifikasi**: ✅ Fitur + skor MAD-approx + severity/confidence cocok antara proto, recompute Python, dan audit formula per sel + regen 0-diff. ⚠️ Bukan verifikasi terhadap output IsolationForest asli (sklearn N/A) — lihat Section 7.

---

## 1. Gambaran Isolation Forest

`IsolationForest` adalah detektor anomali **unsupervised**. Idenya: anomali itu *jarang dan berbeda*, sehingga **mudah diisolasi**. Algoritmanya membangun banyak pohon biner acak — pada tiap node pilih fitur acak dan nilai split acak antara min–max — lalu memartisi data sampai tiap titik terisolasi. **Titik anomali terisolasi dalam lebih sedikit split** (lintasan $h(x)$ pendek); titik normal butuh banyak split (lintasan panjang).

Skor anomali untuk sampel $x$ pada dataset ukuran $n$:

$$s(x,n) = 2^{-E[h(x)]/c(n)}, \qquad c(n) = 2H(n-1) - \frac{2(n-1)}{n}, \quad H(i)=\ln i + 0.5772$$

dengan $E[h(x)]$ = rata-rata panjang lintasan $x$ lintas semua pohon, dan $c(n)$ = panjang lintasan rata-rata BST sebagai normalisasi. $s \to 1$ berarti sangat anomali; $s \lesssim 0.5$ berarti normal. (sklearn `score_samples` mengembalikan versi ter-shift di mana **lebih negatif = lebih anomali**; `predict` memakai `contamination` untuk menetapkan ambang dan menandai fraksi terbawah.)

**Per inverter**, detektor membangun matriks fitur 5-dimensi per (PV, timestamp) daylight, melatih satu `IsolationForest`, lalu menandai `contamination` fraksi paling anomali. **Severity** dipetakan dari **kuartil rank dalam himpunan flagged** (bukan dari skor absolut), dan **confidence** dari intensitas rank.

Karena ini ML murni (bukan aturan spec §4.2.x), detektor **opt-in** dan di IKN **di-eksklusi dari sheet Findings utama** (`exclude_from_findings_sheet=True`) — sebab `contamination=0.01` bisa meng-emit ribuan finding/hari yang membanjiri Findings & merusak data baseline. Hasilnya hanya muncul di sheet artefak `AnomalyScores`/`AnomalySummary`.

---

## 2. Pipeline `M2IForest.run()` — Step by Step

### Langkah 1 — Opt-in gate & config (baris 304-318)

`enabled` default `False` → return `[]` (baris 306-308). Bila aktif, baca: `contamination=0.01`, `n_estimators=100`, `random_state=42`, `min_daylight_samples=30`, `poa_threshold_wm2=50` (gate rendah — iForest belajar dari daylight lebih luas), `include_r_string=True`, `include_sibling_dev=True`.

### Langkah 2 — Guard + ensure sklearn (baris 320-343)

Butuh `Inverter_ID`/`Start Time`. `_ensure_sklearn()` auto-`pip install scikit-learn` bila belum ada (baris 93-103). Normalisasi kolom V/I Title-Case→lowercase, load empty-PV map, init POA provider.

### Langkah 3 — Per inverter: gate daylight (baris 349-364)

Loop `groupby("Inverter_ID")`. PV aktif = `1..pv_max` minus empty slots. Gate `_build_gate_mask` (baris 242-302): `mask_poa & mask_time & mask_shutdown` — pola sama dengan detektor lain (POA>50, solar-elev>5° AND jam<18, sebelum shutdown). Butuh `≥30` sampel daylight.

### Langkah 4 — Bangun matriks fitur (baris 366-372, fungsi `build_feature_matrix` 134-201)

Per (PV, timestamp) daylight, vektor 5-dimensi:

| Fitur | Definisi |
|---|---|
| $V$ | tegangan string |
| $I$ | arus string |
| $V_{dev}$ | $V - \operatorname{median}(V_\text{sibling}@ts)$ |
| $I_{dev}$ | $I - \operatorname{median}(I_\text{sibling}@ts)$ |
| $R$ | $V / \max(I, 0.1)$ (proxy resistansi) |

Matriks $X$ berukuran $(N_\text{PV-aktif} \times N_\text{ts-daylight}, 5)$.

### Langkah 5 — Train + score (baris 376-413)

```python
iforest = IsolationForest(contamination, n_estimators, random_state=42)
iforest.fit(X)
scores = iforest.score_samples(X)   # tinggi = normal
preds  = iforest.predict(X)         # -1 anomali, +1 inlier
flagged = preds == -1
threshold_score = max(scores[flagged])           # batas keputusan
rank_pct = rank_dalam_flagged(by score asc) / (n_flagged-1) * 100   # 0 = paling anomali
```

### Langkah 6 — Severity, confidence, emit (baris 437-484)

`_severity_from_quartile(rank_pct)` (baris 204-216): `≤25→CRITICAL`, `≤50→HIGH`, `≤75→MEDIUM`, `>75→INFO`. `confidence = 100 − rank_pct·0.5` (rentang 50–100). Finding per sampel flagged: `value=score`, `threshold=threshold_score`, `fault_type="iforest_anomaly"`, evidence `{V,I,V_dev,I_dev,R,score,percentile}`. Artefak `AnomalyScores` (semua sampel) + `AnomalySummary` (per inverter).

---

## 3. Worked Example — Approksimasi MAD (bukan iForest asli)

> Karena sklearn tak tersedia, contoh memakai **skor approksimasi MAD** $A$ (§4), bukan skor IsolationForest. Severity & confidence dihitung dengan rumus **identik** detektor.

Skenario (sheet `Raw_Data_IF`): 1 inverter WB05-INV01, 6 PV, 14 timestamp noon (84 sampel). Baseline sehat $V\approx1200$, $I\approx10$ (jitter per-string kecil agar MAD>0). Anomali di-inject: PV3@12:25 & 12:30 (arus drop 3.0/4.0 → $R$ melonjak), PV5@12:45 ($V$ drop ke 1100 → $V_{dev}$ besar), PV2@12:10 (arus 12.6 → sedang).

### 3.1 Skor & ambang

`contamination=0.05` (demo; default produksi 0.01) → `threshold_A = PERCENTILE(A, 0.95) = `**2.1131**. Flag `A ≥ threshold` → **6 sampel** (boundary + ties → 7.1%).

### 3.2 Himpunan flagged, severity & confidence

| PV | jam | fitur menonjol | $A$ | rank_pct | severity | confidence |
|---|---|---|---|---|---|---|
| PV3 | 12:25 | $I$=3.0, $R$=400 | **314.99** | 0 | **CRITICAL** | 100 |
| PV3 | 12:30 | $I$=4.0, $R$=300 | 203.15 | 20 | **CRITICAL** | 90 |
| PV5 | 12:45 | $V_{dev}$=−99.6 | 112.08 | 40 | **HIGH** | 80 |
| PV2 | 12:10 | $I_{dev}$=+2.4 | 32.96 | 60 | **MEDIUM** | 70 |
| PV6 | 12:00 | $V_{dev}$=−0.9 | 2.113 | 80 | INFO | 60 |
| PV6 | 13:05 | $V_{dev}$=−0.9 | 2.113 | 80 | INFO | 60 |

Dua baris INFO (PV6) adalah **sampel paling-ekstrem-tapi-normal** yang ikut tertandai semata karena `contamination` memaksa fraksi tetap — **persis perilaku iForest asli** (selalu menandai ~`contamination`% walau semuanya normal). Severity kuartil membedakan anomali nyata (CRITICAL) dari "normal yang ikut terjaring" (INFO).

---

## 4. Pemetaan Python → Excel (approksimasi)

Empat sheet, **semua formula live** (tidak ada nilai pre-computed; skor MAD bisa direkalkulasi Excel). Fitur & severity/confidence **faithful**; skor $A$ adalah proxy MAD.

### 4.1 `Features_IF` — fitur identik detektor (84 baris)

| Kolom | Formula |
|---|---|
| $V$, $I$ | `=Raw_Data_IF!{Vcol/Icol}{r}` |
| $V_{dev}$ | `=Raw_Data_IF!{Vcol}{r}-MEDIAN(Raw_Data_IF!D{r}:I{r})` |
| $I_{dev}$ | `=Raw_Data_IF!{Icol}{r}-MEDIAN(Raw_Data_IF!J{r}:O{r})` |
| $R$ | `=Raw_Data_IF!{Vcol}{r}/MAX(Raw_Data_IF!{Icol}{r},cfg_if_i_floor)` |

Blok statistik (kolom U–X) per fitur: `median=MEDIAN(col)`, `MAD=MEDIAN(absdev_col)`, `scale=MAX(1.4826·MAD, ε)`. Robust-z: $z_f = \text{absdev}_f / \text{scale}_f$. Skor agregat $A = \operatorname{MAX}(z_V, z_I, z_{Vdev}, z_{Idev}, z_R)$.

$$z_f = \frac{|x_f - \operatorname{median}_f|}{1.4826 \cdot \operatorname{MAD}_f}, \qquad \operatorname{MAD}_f = \operatorname{median}\big(|x_f - \operatorname{median}_f|\big)$$

### 4.2 `IF_Anomaly` — keputusan (84 baris)

| Kolom | Formula |
|---|---|
| threshold_A | `=PERCENTILE(Features_IF!S5:S88,1-cfg_if_contamination)` |
| flag | `=IF(A>=threshold,"anomaly","normal")` |
| rank_within | `=IF(flag="anomaly",COUNTIFS(A_range,">"&A,A_range,">="&threshold),"")` |
| rank_pct | `=rank_within/(n_flagged-1)*100` |
| severity | `=IF(pct<=25,"CRITICAL",IF(pct<=50,"HIGH",IF(pct<=75,"MEDIUM","INFO")))` |
| confidence | `=100-rank_pct*0.5` |

`n_flagged=COUNTIF(flag,"anomaly")`. Severity & confidence **faithful** ke `_severity_from_quartile` dan rumus confidence detektor. Conditional formatting mewarnai severity.

### 4.3 `IF_Summary`

Mirror `AnomalySummary`: `n_samples`, `n_flagged`, `flagged_pct`, `threshold_A`, `max_A`, `min_A` per inverter.

---

## 5. Edge Cases & Limitasi Translasi (yang TERPENTING di iterasi ini)

### 5.1 Kenapa iForest asli tidak bisa jadi formula Excel

Skor iForest = $2^{-E[h(x)]/c(n)}$ di mana $E[h(x)]$ adalah rata-rata panjang lintasan lintas **100 pohon biner acak terlatih**. Untuk merekalkulasi di Excel, seseorang harus meng-encode struktur split semua 100 pohon (ribuan node) — tidak praktis dan tidak "live". Karena itu **tidak ada cara jujur** menaruh IsolationForest sebagai formula spreadsheet. (System Overview line 330/359 sudah mengantisipasi ini: "Excel akan jadi approximation MAD-based + caveat eksplisit".)

### 5.2 Approksimasi MAD ≠ iForest — menandai sampel berbeda

Approksimasi memakai $A = \max_f z_f$ (MAD-z univariat per fitur, lalu MAX). Ini menangkap "ekstrem pada salah satu sumbu" tetapi **tidak menangkap interaksi multivariat** yang dilihat iForest (mis. kombinasi $V$ normal + $I$ normal tapi $R$ ganjil relatif terhadap struktur cluster). Maka **himpunan sampel yang ditandai bisa berbeda** dari iForest asli. Yang **faithful**: struktur per-inverter, flagging fraksi contamination, pemetaan severity kuartil, dan rumus confidence. Yang **approx**: skor itu sendiri.

### 5.3 sklearn tidak tersedia di sandbox

`_ensure_sklearn` mengandalkan `pip install scikit-learn` saat runtime. Di sandbox ini install gagal (timeout; scipy tak ada). Maka tak ada skor IsolationForest referensi untuk dibandingkan — verifikasi terbatas pada approksimasi MAD (yang reproducible).

### 5.4 `contamination` selalu menandai fraksi tetap

Baik iForest asli maupun approksimasi menandai ~`contamination`% **walau semua sampel normal** (lihat dua baris INFO §3.2). Severity kuartil memitigasi: sampel "normal yang ikut terjaring" jatuh ke INFO/MEDIUM, anomali nyata ke CRITICAL. Penting: jangan tafsirkan "ada finding" sebagai "pasti ada fault" — `exclude_from_findings_sheet=True` ada justru karena noise ini.

### 5.5 Yang disederhanakan

- **1 inverter, daylight = baris noon** (gate POA saja; solar-elev/shutdown ephemeris tak direplika).
- **Demo `contamination=0.05`** (vs produksi 0.01) supaya ada cukup flagged untuk mendemonstrasikan 4 tingkat severity pada 84 sampel.
- Skor real `n_estimators=100`, `random_state=42` didokumentasikan di Config (baris `real_*`) tapi tak dipakai approksimasi.

---

## 6. Cross-Check vs Spec & System Overview

| Aspek | Spec §4.2.x | Kode (`iforest.py`) | Workbook (approx) |
|---|---|---|---|
| Sumber aturan | — (iForest = Fase 3 ML, di luar spec rule fisika) | sklearn IsolationForest | MAD-approx (proxy) |
| Fitur | — | V, I, V_dev, I_dev, R | **identik** ✅ |
| contamination | — | 0.01 (produksi) | 0.05 (demo), 0.01 didok. |
| severity | — | kuartil rank (`_severity_from_quartile`) | **identik** ✅ |
| confidence | — | $100 - \text{pct}\cdot0.5$ | **identik** ✅ |
| skor | — | path-length ensembel (2^…) | **MAX robust-z (BERBEDA)** ⚠️ |

**Catatan:**

1. **iForest bukan bagian spec §4.2.x.** Ia tambahan ML "Fase 3 Part 2" untuk menangkap anomali yang lolos aturan fisika. Tidak ada threshold spec untuk dibandingkan; ground truth = kode + `DEFAULT_M2_CONFIG["m2_iforest"]`.
2. **`exclude_from_findings_sheet=True`** (config IKN): finding iForest hanya ke artefak, tidak ke Findings utama, dan tidak men-trigger auto-skip baseline. Workbook ini mereplikasi pemisahan itu (sheet `IF_*` terpisah).
3. **System Overview line 330/359** sudah menyatakan rencana approksimasi MAD untuk Excel — iterasi ini mengeksekusi rencana itu dengan caveat eksplisit.

---

## 7. Verification Log

| # | Cek | Metode | Hasil |
|---|---|---|---|
| 1 | Build 4 sheet baru | `_extend_m2_workbook_iter6.py` | 25 → 29 sheet, 23 sheet lama + Config/README append utuh ✅ |
| 2 | Audit string formula | `verify_iter6.py` (A) | Features (fitur/absdev/z/A) + IF_Anomaly (flag/rank/severity/confidence) == template ✅ |
| 3 | Recompute numerik | `verify_iter6.py` (B) | threshold 2.1131, A=314.99/203.15/112.08/32.96/2.11, 4 tingkat severity, confidence 50–100 cocok proto ✅ |
| 4 | Parity fitur | vs `tests/unit/test_iforest.py` | $R(1200,0.05)=12000$ (nilai test eksplisit) ✅ |
| 5 | Reprodusibilitas | restore backup → rebuild → diff | **0 cell-diff** lintas 4 sheet + Config + README ✅ |
| 6 | Sheet lama utuh | diff vs backup | **0 diff** pada 23 sheet sebelumnya ✅ |

⚠️ **Bukan verifikasi terhadap IsolationForest asli.** sklearn tak ter-install (scipy N/A) + LibreOffice crash. Yang diverifikasi: konsistensi internal **approksimasi MAD** (audit formula + recompute Python + 0-diff). Untuk membandingkan dengan iForest sebenarnya, jalankan `pv_pipeline/iforest.py` di environment ber-sklearn.

---

## 8. Rekomendasi Penggunaan Workbook

- Sheet `IF_Anomaly` menampilkan flag + severity berwarna; `Features_IF` menampilkan fitur & z per fitur untuk menelusuri *kenapa* sebuah sampel ekstrem.
- **Jangan** anggap sheet ini sebagai keluaran detektor produksi. Untuk skor nyata, jalankan modul Python dengan sklearn; workbook ini untuk memahami **fitur, struktur flagging, dan pemetaan severity**.
- Ubah `cfg_if_contamination` di Config untuk melihat efek ambang (lebih besar → lebih banyak flagged).
- Ingat `exclude_from_findings_sheet`: di pipeline nyata, anomali iForest sengaja dipisah dari Findings utama karena noisy.

---

## 9. Pertanyaan untuk Iterasi Berikutnya

1. **LSTM-AE (Iterasi 7)** akan menghadapi keterbatasan yang **sama atau lebih berat** (PyTorch autoencoder; error rekonstruksi dari jaringan terlatih — tak bisa formula Excel, torch juga kemungkinan tak tersedia). Apakah Anda ingin pendekatan yang sama (dokumentasi algoritma + approksimasi transparan + caveat), atau cukup **dokumentasi murni** (tanpa sheet Excel) untuk detektor ML?
2. Untuk iForest: perlukah saya membuat **catatan terpisah** yang menjalankan sklearn nyata bila Anda menyediakan environment ber-sklearn, agar bisa membandingkan approksimasi MAD vs skor iForest asli secara kuantitatif?
3. Setelah ML detectors, kembali ke **M2a (shading/soiling/low_irradiance)** yang Excel-friendly untuk melengkapi famili fisika?

---

## Sources

- `pv_pipeline/iforest.py` (508 baris) — full read: `M2IForest.run()`, `build_feature_matrix`, `_severity_from_quartile`, `_build_gate_mask`
- `tests/unit/test_iforest.py` (414 baris) — encode intent: fitur (V_dev, R=V/max(I,0.1)), severity kuartil, confidence ∈ [50,100], reproducible seed 42
- `pv_pipeline/m2_config.py` — `DEFAULT_M2_CONFIG["m2_iforest"]` (contamination 0.01, n_estimators 100, random_state 42, poa_threshold 50)
- `config/m2_config.yaml` — `m2_iforest` (enabled true, exclude_from_findings_sheet true)
- `docs/_extend_m2_workbook_iter6.py` (298 baris) — build script 4 sheet approx (formula live, regen 0-diff)
- `docs/verify_iter6.py` — audit string formula + recompute numerik (approksimasi MAD) vs proto
- `outputs/proto_iter6.py` — prototipe pengunci angka (deterministic, MAD-approx)
- `docs/M2_Reverse_Engineering_Phase1_System_Overview.md` line 330/359 — rencana approksimasi MAD-based untuk iForest Excel
- `docs/M2_PV_Performance_Workbook.xlsx` — 29 sheet; `Raw_Data_IF`, `Features_IF`, `IF_Anomaly`, `IF_Summary`
- Liu, Ting, Zhou (2008) "Isolation Forest" — definisi $s(x,n)=2^{-E[h(x)]/c(n)}$, $c(n)=2H(n-1)-2(n-1)/n$
- Verified: audit formula + Python recompute vs literal workbook + regen 0-diff (sklearn N/A — approksimasi MAD, bukan iForest asli)
