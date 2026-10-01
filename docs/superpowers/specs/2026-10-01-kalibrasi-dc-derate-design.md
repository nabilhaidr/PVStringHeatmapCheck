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
- **`validasi(tabel: pd.DataFrame, derate: dict) -> pd.DataFrame`**
  - Per WB dan tercile Kt: median sisa `1 − (rasio − e·hari_sejak_hujan)/derate`.
  - Bila keputusan "konstanta" benar, beda median antar-tercile ≤ 0,03.

### `run_derate_calibration.py` (CLI di akar repo, pola `run_saturation_check.py`)

```
python run_derate_calibration.py --m2f-dir "F:/Downloads part 2/cek pv/m2f" \
    [--geometry config/site_geometry.yaml] [--precip coba/precipitation_daily_plts_ikn.csv] \
    [--poa-offset-min 5] [--hanya-sesudah "2026-09-29 22:13"] [--output-dir coba]
```

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

Diisi saat implementasi: angka Langkah 0 (selisih offset 0 vs 5 per WB, perbandingan dengan workbook 20260701), lalu ringkasan run pertama (`PerWB`: keputusan dan nilai per WB).

## Di luar cakupan

- Mengubah `report.py` atau perhitungan `E_expected` M2f.
- Menjalankan batch Colab.
- `bifacial_gain_per_wb`, yang tetap kosong (tanpa sensor POA belakang tidak terpisahkan dari derate).
