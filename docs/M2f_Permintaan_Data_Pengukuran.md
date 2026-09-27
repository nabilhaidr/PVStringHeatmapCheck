# Permintaan Data Pengukuran untuk M2f (Loss Attribution)

**Tanggal:** 2026-08-26
**Untuk:** tim yang memegang ekspor SCADA / weather station PLTS-IKN
**Kenapa:** modul M2f (`pv_pipeline/m2f/`) sudah lengkap, teruji, dan terangkai
lewat `notebook/m2f_loss_attribution.ipynb`, tetapi **belum menghasilkan satu
angka pun** karena dua berkas pengukuran di bawah tidak ada di working tree.

> **Status 2026-09-27: terpenuhi.** Ketiga berkas ada di
> `F:\Downloads part 2\raw data input` dan di Drive, dengan nama, sheet, dan
> kolom persis seperti di bawah (POA 2025/2026: 105.120 baris per tahun; Tcell:
> terisi 2025-01-01 s.d. **2026-08-31**). Run pertama (2026-08-31) menilai
> 4.470/4.470 string-hari dengan POA dan Tcell terukur. Yang masih kurang:
> sertifikat kalibrasi pyranometer (bagian 4), dan Tcell sesudah 2026-08-31.

Selama keduanya absen, notebook tetap jalan dan menghasilkan workbook berskema
benar — tetapi setiap string ditandai `skipped_reason="provider_unavailable"`
dan seluruh selnya kosong. Itu perilaku yang disengaja: lebih baik kosong
daripada menampilkan angka berbasis model yang dilabeli hasil pengukuran.

---

## Yang dibutuhkan

### 1. POA Irradiance (WAJIB)

| | |
|---|---|
| **Nama berkas** | `POA PLTS IKN 2025.xlsx` dan `POA PLTS IKN 2026.xlsx` |
| **Letak** | `raw data input/` |
| **Nama sheet** | `POA PLTS IKN` |
| **Interval** | 5 menit |
| **Zona waktu** | WITA (UTC+8), naive — tanpa penanda timezone di sel |

Kolom yang harus ada, dengan ejaan persis:

- `Date time`
- `POA Irradiance (W/m2) WS 1`
- `POA Irradiance (W/m2) WS 2`
- `POA Irradiance (W/m2) WS 3`
- `POA Irradiance (W/m2) WS 4`
- `POA Irradiance (W/m2) WS 5`
- `Rata-rata WS 1 - WS 5`

Satuan W/m². Lima weather station memetakan ke workblock lewat `ws_to_wb` di
`config/site_geometry.yaml:97-102`.

### 2. PV Module Temperature / Tcell (WAJIB)

| | |
|---|---|
| **Nama berkas** | `PV Module Temperature PLTS IKN.xlsx` |
| **Letak** | `raw data input/` |
| **Nama sheet** | `PV Module Temp` |
| **Interval** | 5 menit |
| **Zona waktu** | WITA (UTC+8), naive |

Layout 18 kolom, berurutan:

| Kolom | Isi |
|---|---|
| A | Datetime |
| B, C, D | WS-1 sensor 1, 2, 3 |
| E | Average WS-1 |
| F, G, H | WS-2 sensor 1, 2, 3 |
| I | Average WS-2 |
| J, K, L | WS-3 sensor 1, 2, 3 |
| M | Average WS-3 |
| N, O, P | WS-4 sensor 1, 2, 3 |
| Q | Average WS-4 |
| R | Overall Average (rata-rata 4 WS-avg) |

Satuan °C. WS-5 tidak punya sensor Tcell — WB01/WB02 menumpang ke WS-4 lewat
`ws_to_wb_tcell` (`config/site_geometry.yaml:107-111`), jadi tidak perlu kolom
WS-5.

### 3. Rentang tanggal

Minimal **30 hari berturut-turut** yang beririsan dengan data inverter 5-menit
yang sudah tersedia, supaya hasilnya representatif terhadap variasi cuaca.
Idealnya **12 bulan penuh**, karena:

- kalibrasi gain bifacial butuh hari clear-sky yang cukup banyak;
- analisis soiling (yang memberi `p_loss_by_month` ke M2f) bekerja per bulan;
- musim kering dan musim hujan berperilaku sangat berbeda di lokasi ini.

### 4. Sertifikat kalibrasi pyranometer (PENTING, bukan opsional)

Tanggal kalibrasi terakhir tiap pyranometer WS-1..WS-5. Bukan untuk dibaca
kode, tetapi untuk menilai kepercayaan angkanya: **error POA mengalir langsung
ke `E_expected`, yang merupakan penyebut setiap persentase rugi di seluruh
laporan.** Pyranometer dengan kalibrasi lama bisa memberi bias ±3–5%, dan bias
itu akan muncul sebagai rugi yang tampak nyata di waterfall.

---

## Yang TIDAK dibutuhkan sekarang

- **Ambient temperature, wind speed, wind direction.** Hanya dipakai model SAPM
  sebagai fallback Tcell. Kalau Tcell terukur tersedia, ketiganya tidak
  diperlukan untuk M2f.

  > **Sudah ditutup:** `m2f.tcell_source` (default `measured_per_ws`) membuat
  > M2f meminta Tcell terukur secara eksplisit, jadi berkas cuaca tidak lagi
  > bisa diam-diam menggantikan Tcell lewat model SAPM.

- **Data meter POI 20 kV.** Di luar lingkup M2f v1 (rantai M1→M3 terpisah).
- **Albedo.** Sudah ada sebagai forecast NSRDB.

---

## Verifikasi setelah berkas tiba

Jalankan `notebook/m2f_loss_attribution.ipynb` dan periksa berurutan:

1. **Sheet `M2f_Closure`** — kolom `skipped_reason` harus kosong untuk
   mayoritas baris. Kalau masih `provider_unavailable`, berkasnya tidak
   terbaca (nama/sheet/kolom tidak cocok). Kalau `poa_or_tcell_missing`,
   berkasnya terbaca tetapi cakupannya di bawah ambang — periksa
   `poa_coverage_pct` dan `tcell_coverage_pct` di baris yang sama.
2. **Kalibrasi derate DC.** Baca kolom `measured_ratio` di sheet
   `M2f_BaselineCalib` (median aktual / harapan-mentah per WB, hanya timestamp
   ON tanpa curtailment) dan salin ke `m2f.dc_derate_per_wb` — **hanya dari
   run hari bersih**. `bifacial_gain_per_wb` dibiarkan kosong: tanpa POA
   belakang, gain bifacial tidak terpisahkan dari derate.
   *Temuan 2026-09-27:* rasio ini bervariasi 0,09–0,31 antar hari per WB
   (mendung ~1,0; cerah-kering ~0,85), jadi belum ada nilai yang layak diisi
   — kumpulkan rasio dari banyak hari lewat batch dulu.
3. **Tilt WB01–WB02.** *Terjawab sebagian 2026-09-27:* `Pyranometer Sensor
   Data.xlsx` (sheet `POA`) mencatat WS-5 terpasang di WB02-INV12-ST06 pada
   tilt 10°, sebidang dengan modulnya — bukti kuat WB01–02 juga ~10°. WS-1 dan
   WS-4 tercatat 9°, bukan 10°.
4. **Besar `unexplained` di sheet `M2f_Pareto`.** Ia akan mendominasi di v1
   karena menyerap shading, low-irradiance, microcrack, bifacial, dan
   ground-fault sekaligus. Itu keadaan yang diharapkan; besarnya adalah ukuran
   seberapa mendesak v2 (estimator `shading` dan `low_irradiance_eff`).

---

## Referensi kode

Konfigurasi path dan layout: `config/site_geometry.yaml`.
Pembaca berkas: `pv_pipeline/poa/loader.py`, `pv_pipeline/cell_temp.py`.
Konsumen: `pv_pipeline/m2f/report.py`, lewat `POAProvider` dan
`CellTempProvider`.
