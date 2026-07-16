# M2 Reverse Engineering — Iterasi 10: M2bIntermittent (LSTM-AE)

**Modul**: `pv_pipeline/lstm_ae.py` (625 baris) + `pv_pipeline/training_data.py` (502 baris) + `train_lstm_ae.py` (246 baris, root repo)
**Class utama**: `M2bIntermittentDetector(SubModule)` — `name = "M2b_intermittent"`
**Sifat**: detektor **deep-learning** (LSTM Autoencoder, PyTorch). **TERLATIH & TER-WIRE (update 2026-07-11)** — model dilatih via `train_lstm_ae.py` (Sprint 4) dari baseline CSV; artifacts `lstm_ae_20260706_084352.pt/.json`. Di Cell 4 template notebook, `M2bIntermittentDetector(model_path=..., meta_path=..., enabled=True)` sejak 2026-07-06 (commit 19e3bf2) — submodule ke-10 pipeline. Default konstruktor tetap `enabled=False` (opt-in eksplisit).
**Spec referensi**: Master Context §4.2.3 — *intermittent fault* (pergeseran pola halus: konektor longgar, partial-shading bergeser)
**Dependency**: `torch` (lazy-import ~200MB), `pv_pipeline.training_data` (`build_day_windows`, `fit_normalization`; `SequenceBuilder` kini hanya jalur legacy), `BaselineAccumulator` (Sprint 3.3)
**Output**: `M2Finding(fault_type="intermittent", severity=MEDIUM, confidence=70)` per window ber-error tinggi + artifact **`WindowErrors`** (sheet xlsx: error rekonstruksi SEMUA window per inverter-hari — ranking harian & tren mendekati threshold; sejak 2026-07-07, commit 4b2bce7)
**Output Excel workbook**: **TIDAK ADA** — detektor ini **input-only** (jaringan PyTorch terlatih = black box; lihat §4 & §5). `WindowErrors` adalah artifact output Python, bukan reproduksi formula.
**Status verifikasi**: ✅ Lapisan **data-pipeline** (resample → window → normalisasi → split) dijalankan dengan kode ASLI — angka nyata terverifikasi (catatan: worked example §3 memakai `SequenceBuilder` sliding-window, jalur training lama; training & inferensi kini day-grid `build_day_windows`, lihat §2). ⚠️ Train + reconstruction error = **dokumentasi** (torch tak tersedia di sandbox; ~200MB).

> ⚠️ **CAVEAT UTAMA.** Ini detektor yang **paling tidak bisa direproduksi di Excel** dari seluruh famili M2. Nilai detektor SELURUHNYA ada di **bobot jaringan saraf terlatih** (encoder/decoder LSTM, ~puluhan ribu parameter) — tidak ada formula spreadsheet yang masuk akal untuk forward-pass apalagi training. Berbeda dari iForest (bisa approksimasi MAD) atau SRR-soiling (ekonomi hilir bisa live), di sini **tidak ada lapisan hilir yang bermakna tanpa modelnya**. Maka **tidak ada sheet workbook**; iterasi ini murni dokumentasi reverse-engineering.

---

## 1. Gambaran LSTM-AE

**LSTM-AE = LSTM Autoencoder** — gabungan LSTM (jaringan untuk deret waktu) + Autoencoder (jaringan yang memampatkan lalu merekonstruksi inputnya sendiri). Targetnya: **intermittent fault** — gangguan putus-nyambung yang **halus** di mana nilai sesaat masih "dalam batas wajar" tetapi **pola temporal 24 jam** sedikit tidak konsisten. Rule/threshold sulit menangkapnya; perlu model yang belajar "bentuk hari normal".

**Prinsipnya:** latih model **hanya pada data NORMAL**. Karena ada *bottleneck* sempit, model hanya bisa merekonstruksi pola yang sudah dipelajari (hari normal). Pola **abnormal yang belum pernah dilihat** → rekonstruksi jelek → **reconstruction error besar** → flag.

Satu **window** = **96 timestep × n_fitur** = 24 jam @ 15-menit, fitur = arus `PV1..PV28` per inverter. Error per window:

$$e = \frac{1}{96 \cdot F}\sum_{t=1}^{96}\sum_{f=1}^{F}(\hat{x}_{t,f} - x_{t,f})^2 \qquad (\text{MSE rekonstruksi})$$

Ambang anomali dari distribusi error di training set NORMAL:

$$\text{threshold} = \mu_\text{error} + 3\,\sigma_\text{error}$$

Window dengan `e > threshold` → emit `intermittent` (MEDIUM, confidence 70).

---

## 2. Pipeline — Step by Step

### Bagian A — Data (`training_data.py`)

1. **BaselineLoader** (baris 58-144): baca daily parquet `baseline/{YYYY-MM}/*.parquet` (output Sprint 3.3 `BaselineAccumulator` — hanya hari **NORMAL** yang disimpan). `load_range`/`load_all` → concat DataFrame.
2. **SequenceBuilder** (sliding window `window_size=96`, `stride=1`, per `Inverter_ID`): (a) `resample` 5-min → **15-min** (mean/median/last) per inverter; (b) pilih kolom fitur `PV{1..28} input current(A)`; (c) `build_sequences` → `(n_windows, 96, n_features)` + metadata per window. **Sejak 2026-07 ini jalur legacy** — training & inferensi memakai `build_day_windows`.
2b. **build_day_windows** (baru, 2026-07): grid harian penuh 00:00..23:45 (**96 slot** @15-min) per inverter-hari; slot kosong/malam diisi **0.0 A** (baseline hanya menyimpan ~12 jam operasional — malam memang 0 A) → **1 window per inverter-hari**. Dipakai `train_lstm_ae.py` (training) dan `build_inference_windows` (inferensi) supaya distribusi input match.
3. **fit_normalization** (baris 327-342): z-score per-fitur (`mean`/`std` di-*fit* di training), `transform` saat inferensi.
4. **train_val_test_split** (baris 345-385): **temporal** (kronologis, no shuffle) 70/15/15 — penting untuk time-series.

### Bagian B — Model (`lstm_ae.py`)

5. **build_lstm_autoencoder** (baris 77-126):
   - **Encoder** `nn.LSTM(n_features → hidden 64, num_layers 2, dropout 0.1)` → ambil hidden state terakhir `h_n[-1]` = **bottleneck** (64-dim).
   - **Decoder**: bottleneck di-*repeat* 96× → `nn.LSTM(64 → 64)` → `Linear(64 → n_features)` → rekonstruksi `(batch, 96, n_features)`.
6. **train_lstm_ae** (baris 142-230): loss **MSE**, Adam `lr=1e-3`, `batch=32`, `epochs=50`, **early-stopping** `patience=5` di val_loss.
7. **compute_reconstruction_errors** (baris 238-268): per-window `mean((recon−x)²)` → `(n_windows,)`.
8. **compute_anomaly_threshold** (baris 271-278): `mean + 3·std` dari error training NORMAL.
9. **save_model_artifacts** (baris 286-319): simpan bobot `.pt` + meta `.json` (threshold, feature_cols, norm stats).
10. **`train_lstm_ae.py`** (root repo, Sprint 4 — 2026-07-05): baca baseline **CSV** `baseline/{YYYY-MM}/{YYYY-MM-DD}.csv` (Drive/Colab; `BaselineLoader` hanya baca parquet, file di Drive mayoritas CSV) → `build_day_windows` per inverter-hari → split temporal 70/15/15 → train (default `epochs=50`, `batch=32`, `patience=5`, `hidden=64`, `layers=2`) → `threshold = μ + σ·std` (`--sigma` default 3.0) → simpan `models/{name}_{timestamp}.pt/.json`. **Run nyata: `lstm_ae_20260706_084352`.**

### Bagian C — Inferensi (`M2bIntermittentDetector.run`)

Wiring Cell 4 template (sejak 2026-07-06): `M2bIntermittentDetector(model_path=".../lstm_ae_20260706_084352.pt", meta_path="....json", enabled=True)`. Default konstruktor `enabled=False` → skip. Bila aktif & artifacts ada: `build_inference_windows(combined_df, feature_cols)` — day-grid 96 slot + night-fill 0 A, preprocessing **sama dengan training** (`SequenceBuilder` sliding-window butuh ≥96 step ter-resample sehingga menghasilkan **0 window** pada data harian ~12 jam operasional — alasan penggantian) → `norm_stats.transform` → `compute_reconstruction_errors` → artifact **`WindowErrors`** (`build_window_errors_df`: date, inverter_id, reconstruction_error, threshold, error_ratio, flagged, window_std — SEMUA window, sortir error desc). Untuk tiap window `err > threshold` → emit `M2Finding(fault_type="intermittent", severity=MEDIUM, value=err, confidence=70)`, evidence `{reconstruction_error, threshold, window_std, ...}` (+ cross-check `window_std` vs `high_std_threshold=0.5`).

---

## 3. Worked Example — Data Pipeline (kode ASLI, terverifikasi)

Jaringan tak bisa dijalankan (torch N/A), tapi **lapisan data dijalankan dengan `SequenceBuilder` asli**. Baseline sintetis: 3 inverter × 5 hari × 6 PV @ 5-min.

| Tahap | Hasil (nyata dari `pv_pipeline.training_data`) |
|---|---|
| Baseline mentah | 4.320 baris (3 inv × 5 hari × 288 ts @5min) |
| Resample 5→15-min | 1.440 baris (per inverter 5×96 = 480 ts) |
| `build_sequences` (window 96, stride 1) | **(1155, 96, 6)** — n_windows × 96 timestep × 6 fitur |
| n_windows per inverter | 5×96 − 96 + 1 = **385** → ×3 inverter = **1155** |
| Normalisasi z-score | mean ≈ 0.0000, std ≈ 1.0000 (per-fitur) |
| Split temporal 70/15/15 | train **808** · val **173** · test **174** |

**Demonstrasi ambang** (error rekonstruksi sintetis, karena model nyata = PyTorch): `threshold = μ + 3σ = 0.0202 + 3·0.0040 = `**0.0322**. Window test dengan `error = 0.060 > 0.0322` → **flag intermittent (MEDIUM, conf 70)**.

Insight penting dari angka ini: **1 hari = 96 timestep = 1 panjang-window**. Sliding window stride-1 berarti `N_hari × 96 − 95` window per inverter — makin banyak hari, makin banyak contoh (5 hari → 385 window/inverter; **90 hari → ~8.545 window/inverter**). Inilah dasar kuantitatif kebutuhan baseline panjang (§5).

---

## 4. Pemetaan Python → Excel — TIDAK ADA (input-only)

Detektor ini **tidak punya sheet workbook**, by design:

- **Model = bobot terlatih.** Forward-pass LSTM (gerbang input/forget/output, cell state, 2 layer × encoder+decoder + linear) atas 96 timestep tidak punya padanan formula spreadsheet yang masuk akal. **Training** (backprop, Adam, 50 epoch) jelas mustahil di Excel.
- **Tidak ada lapisan hilir bermakna.** Pada iForest, severity/percentile bisa live (skor=input); pada soiling, ekonomi bisa live (sr=input). Di sini, **seluruh nilai detektor ADA di model** — tanpa model, tidak ada angka untuk dihitung.
- **Yang Excel-trivial tapi tak berguna sendiri**: aritmetika data-prep — resample (`AVERAGE` per 15-min) dan z-score (`(x−mean)/std`). Itu hanya pra-pemrosesan; tanpa jaringan, tidak menghasilkan deteksi. Maka tidak dibuat sheet.

Status reproduksibilitas Excel: **INPUT-ONLY / N/A** — kategori terkuat dari taksonomi (`M2_Family_Summary` §2), melampaui APPROKSIMASI (iForest) dan HILIR-PENUH (soiling).

---

## 5. Edge Cases, Limitasi & Kenapa Baseline ≥3 Bulan

### 5.1 Kenapa butuh baseline ≥3 bulan (~90 hari) — pertanyaan inti

1. **Jaringan saraf butuh banyak contoh agar tidak overfit.** Model ~puluhan ribu parameter (hidden 64 × 2 layer × encoder+decoder+linear). Data sedikit → menghafal, bukan generalisasi. Dari §3: 90 hari → ~8.500 window/inverter × banyak inverter = puluhan-ratusan ribu contoh — baru memadai.

2. **Baseline harus mencakup SELURUH ragam kondisi "normal".** Output PV bervariasi karena cuaca (cerah/berawan/hujan), musim, suhu, soiling. IKN (near-ekuator, **monsun tropis**) punya ayunan basah/kering besar. Latih 2 minggu → model hanya lihat irisan sempit → **hari normal-tapi-beda (berawan / pergeseran musim) salah-tuduh anomali = false-positive membanjir**. 3 bulan mulai menangkap variasi cuaca/musim → konsep "normal" kokoh.

3. **Threshold `μ+3σ` butuh distribusi error stabil.** Mean/std error dari data sedikit = berisik → ambang tak andal. Banyak data → statistik error stabil.

4. **Baseline = HANYA hari NORMAL.** `BaselineAccumulator` hanya menyimpan hari yang lolos sebagai NORMAL (sebagian terbuang: fault/curtailment/data hilang). Maka butuh **waktu kalender** cukup panjang agar terkumpul jumlah hari-NORMAL bersih yang memadai.

5. **Split temporal butuh rentang.** Train/val/test kronologis (70/15/15) butuh cukup hari agar tiap split tetap mewakili variasi kondisi (early-stopping & evaluasi bermakna).

Singkatnya: ≥3 bulan = **minimum viable** untuk (a) cukup contoh tanpa overfit, (b) cukup **ragam normal** agar tak salah-tuduh, (c) statistik ambang stabil. Idealnya ≈1 tahun (semua musim).

### 5.2 Status & dependensi — UPDATE 2026-07

Ketiga prasyarat cold-start kini **terpenuhi**: (i) baseline terakumulasi (CSV harian di Drive), (ii) pipeline training Sprint 4 dijalankan (`train_lstm_ae.py`, Colab), (iii) artifacts tersimpan (`lstm_ae_20260706_084352.pt/.json`) dan ter-wire `enabled=True` di Cell 4 template. Default konstruktor tetap `enabled=False` — tanpa `model_path`/`meta_path`, `run()` skip dengan warning. torch lazy-install (~200MB) — tetap gagal di sandbox dokumentasi ini.

### 5.3 Catatan desain

- Severity tetap **MEDIUM** (bukan ladder) — intermittent = sinyal lemah/butuh konfirmasi. `confidence=70`.
- `window_std` cross-check (evidence) — membantu memilah error-tinggi karena pola-beda vs noise.
- Resample 5→15-min mengurangi noise frekuensi-tinggi & ukuran (288→96 ts/hari), selaras spec window 24-jam.

---

## 6. Cross-Check vs Spec & Famili

| Aspek | Kode (`lstm_ae.py`) | Catatan |
|---|---|---|
| Spec | §4.2.3 intermittent fault | pola halus, butuh ML |
| fault_type | `intermittent` | per-window, inverter-aggregate |
| severity / confidence | MEDIUM / 70 | tetap (bukan ladder) |
| window | 96 ts @15-min (24 jam) | `build_day_windows` day-grid + night-fill 0 (training & inferensi) |
| arsitektur | LSTM-AE 64-hidden, 2-layer | encoder→bottleneck→decoder |
| threshold | `μ+3σ` error NORMAL | per training set (`--sigma` default 3.0) |
| Excel | **INPUT-ONLY (tak ada sheet)** | black box terlatih; `WindowErrors` = artifact Python |
| status | **TERLATIH** (`lstm_ae_20260706_084352`), wired Cell 4 | default konstruktor `enabled=False` (opt-in) |

Posisi di famili M2: melengkapi detektor rule (yang menangkap fault eksplisit) dengan deteksi **anomali pola halus** yang lolos rule. Bersama M2_iForest, ini satu dari dua detektor ML; LSTM-AE lebih spesifik ke **struktur temporal** (urutan 24-jam), iForest ke **outlier multivariat per-titik**.

---

## 7. Verification Log

| # | Cek | Metode | Hasil |
|---|---|---|---|
| 1 | Data-pipeline asli | jalankan `SequenceBuilder`/`fit_normalization`/`train_val_test_split` dari `pv_pipeline.training_data` | sequences (1155, 96, 6); resample 5→15min; z-score mean≈0/std≈1; split 808/173/174 ✅ |
| 2 | Hitung window | `5×96−96+1 = 385`/inv × 3 = 1155 | cocok shape ✅ |
| 3 | Threshold konsep | `μ+3σ` pada error sintetis | 0.0322; window 0.060 > threshold → flag ✅ |
| 4 | Arsitektur & default | baca `lstm_ae.py` | hidden 64, 2-layer, MSE, Adam 1e-3, threshold 3σ, MEDIUM/conf 70 ✅ |

⚠️ **Train + reconstruction error TIDAK dijalankan** — torch (~200MB) tak ter-install di sandbox. Yang diverifikasi: **lapisan data (kode asli, angka nyata)** + pembacaan arsitektur. Untuk melatih/inferensi nyata: environment ber-torch + baseline ≥3 bulan.

---

## 8. Rekomendasi (update 2026-07)

- Model **sudah dilatih & aktif** (`lstm_ae_20260706_084352`). Prioritas sekarang: **validasi threshold pada hari-fault berlabel** dan monitor false-positive-rate lintas musim (model baru melihat baseline sampai awal Juli 2026 — konsep "normal"-nya belum mencakup semua musim).
- **Retrain berkala** saat baseline bertambah (idealnya sampai mencakup 6–12 bulan); bandingkan `resample_method` (mean/median/last) saat retrain.
- Pakai artifact **`WindowErrors`** untuk ranking harian dan memantau inverter yang mendekati threshold sebelum benar-benar terflag.
- Karena tak ada sheet Excel, gunakan dokumen ini + `M2_Family_Summary` sebagai referensi; integrasi ke workbook **tidak disarankan** (akan menyesatkan seolah bisa direkalkulasi).

---

## 9. Penutup

LSTM-AE kini bagian dari **10 submodule** pipeline Cell 4 (bukan lagi skeleton — terlatih 2026-07-06 dan wired `enabled=True` di template). Ia tetap menandai batas reproduksibilitas: **murni input-only**. Sisa yang belum ada modul (M2c Microcrack EL/IV, M2d Bifacial, M2f Loss-Attribution) butuh hardware/modul baru, di luar cakupan reverse-engineering kode saat ini.

Langkah lanjut yang mungkin: (1) validasi model terhadap hari-fault berlabel + monitor FP lintas musim; (2) validasi detektor existing terhadap data Huawei aktual (paste-over `Raw_Data_*`); (3) retrain berkala seiring baseline bertambah.

---

## Sources

- `pv_pipeline/lstm_ae.py` (625 baris) — full read: `build_lstm_autoencoder`, `train_lstm_ae`, `compute_reconstruction_errors`, `compute_anomaly_threshold`, `build_inference_windows`, `build_window_errors_df`, `M2bIntermittentDetector`
- `pv_pipeline/training_data.py` (502 baris) — full read: `BaselineLoader`, `SequenceBuilder`, `build_day_windows`, `fit_normalization`, `train_val_test_split`
- `train_lstm_ae.py` (246 baris, Sprint 4) — training dari baseline CSV; artifacts run nyata `lstm_ae_20260706_084352.pt/.json`
- `notebook/20260209stringmap_v1.5.ipynb` Cell 4 — wiring `enabled=True` (commit 19e3bf2, 2026-07-06)
- `pv_pipeline/core.py` — `M2Finding`, `Severity`, `SubModule`
- `outputs/proto_iter10.py` — worked example data-pipeline (kode ASLI dijalankan: 1155 window, split 808/173/174)
- `docs/M2_Reverse_Engineering_Phase1_System_Overview.md` — konteks sistem (status LSTM-AE di sana ikut diupdate 2026-07-11)
- Master Context §4.2.3 (intermittent fault); IEC 61724 (PR context)
- Verified: lapisan data-pipeline (kode asli, angka nyata) + pembacaan arsitektur. Neural net = dokumentasi (torch N/A). **Tidak ada sheet Excel — input-only.**
