# Kalibrasi `m2f.dc_derate_per_wb` — rancangan

Tanggal: 1 Oktober 2026. Status: disetujui pengguna per bagian (arsitektur dan aliran data; model, keputusan, validasi, uji).

## Tujuan

Mengisi `m2f.dc_derate_per_wb` di `config/m2_config.yaml` dengan nilai yang bisa dipertanggungjawabkan, atau menunjukkan dengan data bahwa satu konstanta per WB tidak cukup.

Selama derate kosong (= 1,0), `E_expected` M2f memakai baseline pelat-nama. Akibatnya `unexplained` menyerap rugi baseline: 64–92 % pada run 2026-07-29 dan 2026-08-31, sehingga angka M2f belum layak untuk keputusan biaya.

## Latar (dari data, bukan asumsi)

- **`measured_ratio` ikut langit.** Nilainya adalah median rasio aktual/harapan-mentah per WB per hari, dari sheet `M2f_BaselineCalib`. Kalibrasi lokal 27 Sep: mendung ~0,95–1,06, cerah-kering ~0,81–0,94.
- **Pada POA tinggi yang stabil, kekurangan daya hanya ~1 %.** Batch saturasi Drive 29 Sep (`coba/saturation_check_20250103_20260729.xlsx`, 100 hari): median `r_high_stable` 0,983. Kekurangan besar datang dari sampel TIDAK stabil. Dugaan pemilik dokumen: keterwakilan pyranometer titik terhadap larik.
- **Data multi-hari yang ada:** 42 workbook harian M2f di `F:\Downloads part 2\cek pv\m2f\`, rentang 2026-06-01..2026-07-13 (06-10 tidak ada), dibuat 29–30 Sep 2026.
  - **Versinya campur.** 19 hari pertama dibuat sebelum komit offset POA 5 menit (`59d2151`, 29 Sep 22:13); sisanya sesudah. Semua memuat `poa_fallback_pct`, jadi sesudah `b2495b0`.
  - **Jumlah WB:** 6–10 per hari. WB01/WB02 sebagian hari absen.
- **Bahan lain yang ada lokal:**
  - POA 5 menit per stasiun cuaca: `F:\Downloads part 2\raw data input\POA PLTS IKN 2025.xlsx` dan `... 2026.xlsx`.
  - Hujan harian: `coba/precipitation_daily_plts_ikn.csv` (s.d. 2026-08-31).
- **Jun–Jul 2026 kering.** Hanya 5 hari ≥ 5 mm dan 2 hari ≥ 10 mm. Hari bersih "≥ 10 mm dalam 3 hari" hanya 4, terlalu sedikit untuk regresi. Hari pasca-hujan juga biasanya berawan (memori `rain-data-overcast-confound`).

## Keputusan pengguna

| Hal | Keputusan |
|---|---|
| Pendekatan | Uji dulu; data yang memutuskan konstanta per WB atau model langit |
| Data | 42 workbook yang ada + uji kepekaan versi; batch Colab bulan lain menyusul (hasil diberi label "satu musim") |
| Debu vs langit | Semua hari, dengan kovariat `hari_sejak_hujan`; bukan hanya hari bersih |
| Config | Tidak diubah otomatis; diisi lewat langkah terpisah setelah keputusan disetujui |

## Langkah 0 — uji kepekaan versi (sebelum analisa)

Tujuan: memastikan 42 workbook yang versinya campur boleh dianalisa bersama.

1. Hitung `measured_ratio` per WB untuk **2026-07-01**, memakai telemetri dan POA lokal dan jalur kode M2f yang sama dengan notebook, dua kali: `pyranometer.time_offset_minutes` = 0 dan = 5.
2. Kriteria:
   - median |selisih per WB| < 0,01 → data campur dipakai;
   - bila tidak → hanya hari yang workbook-nya dibuat sesudah 2026-09-29 22:13 yang dipakai (0621..0713, 22 hari), atau batch diulang.
3. Bandingkan juga hasil offset 5 dengan `M2f_BaselineCalib` workbook 20260701 di F:. Bila sama (≤ 0,005 per WB), terbukti Colab memakai kode berpenanda offset untuk hari itu (memori `colab-drive-version-trap`).

Skrip uji ini sekali pakai dan tidak masuk repo. Hasil dan angkanya dicatat di bagian "Hasil" spesifikasi ini.

## Arsitektur

### `pv_pipeline/m2f/derate_calibration.py` (fungsi murni, teruji sintetis)

- **`hari_sejak_hujan(hujan: pd.Series, hari: pd.DatetimeIndex, *, ambang_mm=5.0, maks=30) -> pd.Series`**
  - Jumlah hari sejak hari terakhir dengan hujan ≥ ambang, sebelum atau sama dengan hari itu, dibatasi `maks`.
  - Tanpa hujan sebelumnya dalam data → `maks`.
- **`kt_poa(poa: pd.Series, poa_cerah: pd.Series, elevasi: pd.Series, *, min_elev=15.0) -> float`**
  - Σ POA / Σ POA langit cerah pada sampel dengan elevasi > `min_elev` dan keduanya terisi.
  - NaN bila tak ada sampel.
- **`porsi_kosong(poa: pd.Series, elevasi: pd.Series, *, min_elev=15.0) -> float`**: pecahan sampel ber-elevasi > `min_elev` yang POA-nya NaN.
- **`fit_wb(tabel: pd.DataFrame) -> dict`**
  - Regresi OLS `rasio ~ 1 + (kt − kt_ref) + (mulus − mulus_ref) + hari_sejak_hujan` untuk satu WB. `kt_ref` dan `mulus_ref` = median kolom itu.
  - Keluaran: koefisien `a, b, c, e`, galat baku dan `t` masing-masing, `n`, `iqr_kt`, `iqr_mulus`, `ayunan_langit` = |b|·iqr_kt + |c|·iqr_mulus, dan `rasio_bersih` = median(rasio − e·hari_sejak_hujan).
  - Galat baku dihitung dari (XᵀX)⁻¹·s². Tanpa statsmodels.
- **`putuskan(fit: dict, *, ambang_ayunan=0.03, ambang_t=2.0, min_hari=20) -> dict`**, keluaran `{keputusan, nilai, alasan}`:
  - `n < min_hari` → `"data_kurang"`, nilai NaN.
  - `e > 0` dan `t_e ≥ ambang_t` → `"tercampur"`, nilai NaN (debu dan langit tak terpisah).
  - `ayunan_langit < ambang_ayunan`, atau |t_b| < ambang_t dan |t_c| < ambang_t → `"konstanta"`, nilai = `rasio_bersih`.
  - selain itu → `"model_langit"`, nilai NaN (butuh perubahan kode M2f, spesifikasi terpisah).
- **`validasi(tabel: pd.DataFrame, fits: dict, derate: dict) -> pd.DataFrame`** (`fits` memberi koefisien `e` per WB)
  - Per WB dan tercile Kt: median sisa `1 − (rasio − e·hari_sejak_hujan)/derate`.
  - Bila keputusan "konstanta" benar, beda median antar-tercile ≤ 0,03.

### `run_derate_calibration.py` (CLI di akar repo, pola `run_saturation_check.py`)

```
python run_derate_calibration.py --m2f-dir "F:/Downloads part 2/cek pv/m2f" \
    [--raw-root "F:/Downloads part 2"] \
    [--geometry config/site_geometry.yaml] [--precip coba/precipitation_daily_plts_ikn.csv] \
    [--poa-offset-min 5] [--hanya-sesudah "2026-09-29 22:13"] [--output-dir coba]
```

`--raw-root` diawalkan pada path POA relatif di `site_geometry.yaml`, karena berkas POA ada di F:, bukan di `raw data input` repo.

1. **Rasio.** `rekap_m2f.build_daily_calib` atas workbook harian di `--m2f-dir`, diambil `measured_ratio` dan `n_calib_string_days` per WB per hari. `--hanya-sesudah` menyaring workbook menurut waktu modifikasi berkas (dipakai bila Langkah 0 gagal).
2. **Langit.** Per hari dan WB, memakai `PyranometerLoader` (`config/site_geometry.yaml`, offset dari config atau `--poa-offset-min`) dan `PvlibClearSkyEstimator.from_geometry_yaml` pada indeks 5 menit 06:00–18:00:
   - `kt` = `kt_poa(get_per_ws(idx, wb, fallback_to_avg=False), estimate(idx), get_solar_elevation(idx))`;
   - `mulus` = `run_saturation_check.poa_smooth_share(loader.df[[ws]], hari)` dengan `ws` = stasiun cuaca yang dipetakan ke WB di `ws_to_wb`;
   - `poa_kosong` = `porsi_kosong(...)`.
3. **Debu.** `hari_sejak_hujan` dari CSV hujan.
4. **Saringan.** Buang WB-hari dengan `n_calib_string_days` < 100, `poa_kosong` > 0,5, atau rasio/kt/mulus NaN. Jumlah yang dibuang dilaporkan per alasan.
5. **Fit dan keputusan per WB, lalu validasi.**
6. **Keluaran** `--output-dir/derate_calibration_<awal>_<akhir>.xlsx`:
   - `Harian`: WB-hari, rasio, kt, mulus, poa_kosong, hari_sejak_hujan, dibuang, alasan;
   - `PerWB`: koefisien, t, n, ayunan_langit, rasio_bersih, keputusan, nilai, alasan;
   - `Validasi`;
   - `Catatan`: rentang, jumlah hari, versi data, "satu musim", ambang yang dipakai.
   - PNG rasio terhadap kt per WB.
   - Cetak potongan YAML `dc_derate_per_wb` hanya untuk WB berkeputusan "konstanta". Config tidak ditulis.

## Uji (`tests/unit/test_derate_calibration.py`, sintetis)

- **`hari_sejak_hujan`:** hujan di hari yang sama → 0; ambang tepat 5 mm terhitung; batas `maks`; tanpa hujan sebelumnya → `maks`.
- **`kt_poa`:** POA = 0,7 × langit cerah → 0,7; sampel elevasi ≤ 15° tidak dihitung; NaN diabaikan.
- **`porsi_kosong`:** pecahan NaN yang benar pada elevasi > 15°.
- **`fit_wb`:**
  - data dari `rasio = 0,9 − 0,1·(kt − kt_ref) − 0,002·hari_sejak_hujan` + derau kecil mengembalikan b ≈ −0,1 dan e ≈ −0,002 (toleransi 10 %);
  - `rasio_bersih` ≈ 0,9 bila kt tak bervariasi.
- **`putuskan`:** setiap cabang (`data_kurang`, `tercampur`, `konstanta` lewat ayunan kecil, `konstanta` lewat t kecil, `model_langit`), di kedua sisi ambang 0,03 dan |t| = 2.
- **`validasi`:** derate yang benar memberi sisa ~0 di semua tercile.
- **CLI:** folder workbook sintetis (2 hari, 2 WB) + POA dan hujan sintetis (monkeypatch loader dan estimator). Berkas xlsx keluar dengan keempat sheet, dan config tidak berubah.

## Langkah sesudah keputusan (di luar implementasi ini)

- **Semua WB "konstanta" dan validasi lolos:** pengguna menyetujui, lalu nilai disalin ke `dc_derate_per_wb` dengan komentar sumber (rentang, keputusan, "satu musim"), sebagai komit terpisah.
- **Ada WB "model_langit" atau "tercampur":** buat spesifikasi baru (derate per hari dari indeks langit di M2f, atau data musim lain). Config tetap kosong.
- **Sesudah ada batch Colab musim lain:** jalankan ulang alat ini, lalu tinjau keputusan.

## Hasil

### Langkah 0 (1 Okt 2026)

`coba/langkah0_offset_20260701.py`, telemetri lokal `coba/01072026/CSV Export/20260701.csv`:

| WB | offset 0 | offset 5 | Drive | \|5 − 0\| | \|5 − Drive\| |
|---|---|---|---|---|---|
| WB01 | 0,9648 | 0,9658 | 0,9649 | 0,0010 | 0,0009 |
| WB02 | 0,9585 | 0,9574 | 0,9591 | 0,0011 | 0,0016 |
| WB03 | 1,0538 | 1,0550 | 1,0550 | 0,0011 | 0,0000 |
| WB04 | 1,0431 | 1,0444 | 1,0438 | 0,0013 | 0,0006 |
| WB05 | 0,8933 | 0,8959 | 0,8953 | 0,0026 | 0,0006 |
| WB06 | 1,0537 | 1,0602 | 1,0603 | 0,0065 | 0,0001 |
| WB07 | 0,8788 | 0,8818 | 0,8818 | 0,0029 | 0,0000 |
| WB08 | 1,0569 | 1,0581 | 1,0543 | 0,0012 | 0,0038 |
| WB09 | 1,0509 | 1,0526 | 1,0518 | 0,0016 | 0,0007 |
| WB10 | 1,0304 | 1,0324 | 1,0200 | 0,0019 | **0,0123** |

- **Offset POA:** median |5 − 0| = 0,0014 < 0,01, jadi data versi campur (sebelum/sesudah offset) sah dianalisa bersama.
- **Selisih dengan Drive:** maks 0,0123 (WB10) > 0,005. Penyebabnya BUKAN offset, karena lokal offset 0 dan 5 sama-sama ~1,03. Kemungkinannya beda versi kode Colab atau beda masukan (xlsx mentah Drive vs CSV Export lokal). Workbook 20260701 dibuat 30 Sep 03:16, sesudah batas 29 Sep 22:13, jadi aturan `--hanya-sesudah` tidak menghapus selisih ini.
- **Keputusan pengguna (1 Okt 2026):** pakai semua 42 hari. Selisih ~0,01 di WB10 dan 0,004 di WB08 dicatat sebagai ketidakpastian versi/data, di bawah ambang keputusan 0,03. Keputusan WB10 dan WB08 diberi catatan. Ini menyimpang dari aturan literal rencana, karena aturan itu tidak cocok dengan temuan.

### Run pertama

`python run_derate_calibration.py --m2f-dir "F:/Downloads part 2/cek pv/m2f" --raw-root "F:/Downloads part 2"` (1 Okt 2026) → `coba/derate_calibration_20260601_20260713.xlsx`.

**Catatan run:**
- rentang 2026-06-01..2026-07-13, 42 workbook, 394 WB-hari;
- 27 dibuang, semuanya `poa_kosong > 0,5`;
- offset POA 5 menit, label "satu musim".

| WB | WS | n | b (kt) | t_b | c (mulus) | t_c | e (debu/hari) | t_e | ayunan langit | rasio_bersih | keputusan alat |
|---|---|---|---|---|---|---|---|---|---|---|---|
| WB01 | WS-5 | 38 | −0,241 | −4,6 | 0,193 | 2,5 | 0,0010 | 0,5 | 0,066 | 0,902 | model_langit |
| WB02 | WS-5 | 38 | −0,273 | −5,4 | 0,154 | 2,0 | 0,0012 | 0,6 | 0,066 | 0,873 | model_langit |
| WB03 | WS-4 | 42 | −0,318 | −6,2 | 0,132 | 2,4 | −0,0009 | −0,5 | 0,073 | 0,923 | model_langit |
| WB04 | WS-4 | 42 | −0,285 | −6,6 | 0,147 | 3,2 | −0,0010 | −0,6 | 0,068 | 0,924 | model_langit |
| WB05 | WS-2 | 33 | −0,243 | −5,9 | 0,122 | 1,7 | −0,0043 | −2,6 | 0,078 | 0,763 | model_langit |
| WB06 | WS-3 | 42 | −0,357 | −6,1 | 0,140 | 2,1 | −0,0040 | −1,7 | 0,087 | 0,963 | model_langit |
| WB07 | WS-2 | 33 | −0,389 | −6,5 | 0,024 | 0,2 | −0,0065 | −2,6 | 0,101 | 0,737 | model_langit |
| WB08 | WS-1 | 33 | −0,267 | −1,4 | 0,152 | 1,2 | −0,0117 | −2,8 | 0,051 | 0,997 | konstanta* |
| WB09 | WS-1 | 33 | −0,248 | −1,5 | 0,191 | 1,7 | −0,0094 | −2,5 | 0,054 | 0,977 | konstanta* |
| WB10 | WS-1 | 33 | −0,256 | −1,4 | 0,173 | 1,5 | −0,0058 | −1,5 | 0,052 | 0,955 | konstanta* |

\* **"Konstanta" WB08–10 ditolak oleh validasinya sendiri:** beda sisa antar-tercile Kt 0,075 / 0,117 / 0,080, padahal batasnya ≤ 0,03.
- Keputusan itu muncul hanya karena |t_b| < 2. Nilai `b` sama besar dengan WB lain, tetapi galat bakunya 3,5× lebih besar (0,17–0,19 vs 0,04–0,06).
- Penyebabnya POA **WS-1**: Kt rata-rata 0,56 (stasiun lain 0,68–0,79), sebaran sempit (sd 0,09), dan korelasi dengan stasiun lain 0,88 (antarstasiun lain 0,98–0,99).
- Ini konsisten dengan memori `poa-telemetry-5min-offset`: WS-1 turun 0,36–0,77× median pada pukul 11–12, dan energi hariannya 0,83–0,91× rata-rata.

**Kesimpulan run pertama:**
1. **Satu konstanta per WB tidak cukup untuk WB mana pun.**
   - Rasio turun ~0,24–0,39 per satuan Kt di semua WB, dengan |t| 4,6–6,6 di stasiun yang sehat.
   - Ayunan akibat langit 5–10 %, di atas ambang 3 %.
   - `dc_derate_per_wb` **tetap kosong.**
2. **Perbedaan `rasio_bersih` antar-WB terutama mengikuti stasiun cuaca, bukan modul.**
   - WS-2 (Kt tertinggi, maks 1,02): WB05/07 0,74–0,76.
   - WS-1 (Kt terendah): WB08–10 0,96–1,00.
   - Konstanta per WB akan menyerap bias kalibrasi sensor POA.
3. **Debu:** `e` negatif di 8/10 WB (−0,001 s.d. −0,012 per hari). Positif di WB01/02 tetapi tidak bermakna (t ≤ 0,6). Tidak ada WB "tercampur".
4. **Langkah berikutnya** sesuai bagian "Langkah sesudah keputusan": spesifikasi baru. Calon isinya:
   - koreksi relatif-stasiun cuaca, yaitu kalibrasi silang POA antar-WS pada hari cerah;
   - derate per hari dari indeks langit di M2f;
   - pemeriksaan sensor WS-1.

   Dikerjakan setelah ada data musim lain atau keputusan pemilik dokumen.

## Di luar cakupan

- Mengubah `report.py` atau perhitungan `E_expected` M2f.
- Menjalankan batch Colab.
- `bifacial_gain_per_wb`, yang tetap kosong (tanpa sensor POA belakang tidak terpisahkan dari derate).
