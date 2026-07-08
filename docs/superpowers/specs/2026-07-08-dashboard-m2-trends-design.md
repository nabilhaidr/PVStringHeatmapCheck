# Halaman "Trends" — Grafik Tren Harian Semua Detector M2

**Tanggal:** 2026-07-08
**Status:** Disetujui (menunggu review spec tertulis)

## Tujuan

Menambahkan satu halaman dashboard baru yang menampilkan **tren harian** (deret waktu
per `source_date`) untuk semua detector M2 dan metrik perhitungannya. Halaman deep-dive
(`detectors.py`) yang ada sekarang hanya menampilkan snapshot agregat satu range —
tidak ada dimensi waktu. Halaman Trends menjawab pertanyaan "apakah kondisi memburuk
atau membaik dari hari ke hari".

## Non-tujuan (YAGNI)

- Tidak menambah layer data baru ke Google Drive. Halaman memakai
  `cached_findings_range(start, end)` yang sudah ada.
- Tidak mengubah cache, gdrive, atau halaman lain (selain 1 baris di `detectors.py`).
- Tidak ada forecasting/prediksi tren — murni menampilkan data historis yang ada.
- Tidak ada export/download baru di tahap ini (dataframe sudah bisa dilihat).

## Arsitektur

Mengikuti pola halaman yang sudah ada persis:

```
pages/5_Trends.py          # wrapper tipis: from ...pages.trends import main; main()
pages/trends.py            # UI Streamlit: date picker, section per detector, Altair charts
data/trends.py             # modul agregasi MURNI (tanpa import streamlit) -> unit-testable
```

Pemisahan `data/trends.py` dari UI penting supaya logika agregasi bisa diunit-test tanpa
menjalankan Streamlit (pola sama seperti `data/loader.py` vs `pages/*.py`).

## Sumber data

`cached_findings_range(start, end)` mengembalikan `LoadResult.sheets`: dict
`{sheet_name: DataFrame}` hasil `concat_findings_range`, di mana **setiap baris sudah
punya kolom `source_date`** (tanggal artifact xlsx asalnya). Inilah sumbu-X tren.

Default range: **30 hari** (`end = today`, `start = today - 29`). Halaman lain memakai
7 hari, tapi tren butuh riwayat lebih panjang. Caption memperingatkan bahwa load pertama
mengunduh 1 xlsx per hari (bisa lambat) dan menyarankan tombol "Refresh data".

## Nama sheet (terverifikasi dari kode)

Sheet di-emit dengan pola `{submodule.name}_{artifact}` (core.py:265, limit 31 char):

| Detector | Sheet | Metrik tren |
|---|---|---|
| **Semua** | `Findings` | count temuan per `sub_module` × `severity` per hari |
| Soiling | `M2a_soiling_EconomicAnalysis` | `soiling_ratio` + pita CI (`sr_ci_lower`/`sr_ci_upper`), penanda hari `recommend_cleaning` |
| Intermittent (LSTM-AE) | `M2b_intermittent_WindowErrors` | max & mean `error_ratio` per hari, garis threshold=1.0, drill-down per `inverter_id` |
| MpptRatio | `M2b_mppt_ratio_StringStatus` | median `ratio_median_daylight` + jumlah string `status="mppt_partner_underperform"` |
| IForest | `M2_iforest_AnomalySummary` | rata-rata `flagged_pct` plant + drill-down per `inverter_id` |
| Shading | `M2a_shading_ShadingSummary` | total `n_suspicious` per hari (long-format, kolom `n_suspicious`) |
| LowIrradiance | `M2a_low_irradiance_LowIrradianceSummary` | count per klasifikasi (WIDE-format, lihat catatan) |
| PeerZ | `M2b_peer_zscore_StringStatus` | count/% string per `status` |
| OpenCircuit | `M2b_open_circuit_StringStatus` | count/% string per `status` |
| GroundFault | `M2b_ground_fault_StringStatus` | count/% string per `status` |
| Availability | `Findings` (via sub_module) | tercakup di section "Semua" |

Semua nama kolom di atas sudah diverifikasi langsung di file detector masing-masing.

### Catatan bentuk data khusus

- **`LowIrradianceSummary` WIDE-format:** satu baris per hari dengan kolom
  `normal`, `low_irradiance_underperform`, `general_underperform`, `skipped`
  (low_irradiance.py:352). Butuh `melt` count-columns → long sebelum di-plot.
  Ini beda dari sheet `StringStatus` yang sudah long dengan kolom `status`.
- **`WindowErrors`** punya kolom `date` (Timestamp internal) DAN `source_date`
  (tanggal artifact). Untuk tren pakai `source_date` demi konsistensi dengan sheet
  lain — encode ini secara eksplisit di test.

## Modul agregasi `data/trends.py`

Karena 4 detector (PeerZ, OpenCircuit, GroundFault, dan pola dasar) berbagi bentuk
`StringStatus` dengan kolom `status`, satu fungsi shared menangani semuanya. Total ~5
fungsi murni:

1. `findings_counts_per_day(findings_df) -> DataFrame[source_date, sub_module, severity, count]`
   — pivot untuk section ringkasan semua detector.
2. `status_counts_per_day(df, status_col="status") -> DataFrame[source_date, status, count, pct]`
   — dipakai PeerZ, OpenCircuit, GroundFault (dan MpptRatio untuk jumlah underperform).
3. `numeric_metric_per_day(df, value_col, agg=("mean","max","median")) -> DataFrame[source_date, <agg cols>]`
   — dipakai MpptRatio (`ratio_median_daylight`), IForest (`flagged_pct`),
   Intermittent (`error_ratio`).
4. `soiling_ratio_per_day(econ_df) -> DataFrame[source_date, soiling_ratio, sr_ci_lower, sr_ci_upper, recommend_cleaning]`
   — passthrough ringan + koersi tipe untuk pita CI.
5. `wide_counts_per_day(df, count_cols) -> DataFrame[source_date, classification, count]`
   — melt untuk LowIrradianceSummary.

**Kontrak error handling (setiap fungsi):** jika sheet tidak ada di dict, atau kolom
yang dibutuhkan hilang, atau DataFrame kosong → kembalikan **DataFrame kosong**, tidak
pernah raise. UI memutuskan menampilkan `st.info("Detector X tidak aktif untuk range
ini")` saat hasil kosong.

## UI `pages/trends.py`

- `st.set_page_config` + `require_auth()` + judul (pola halaman lain).
- `pick_date_range` dengan default 30 hari + tombol "Refresh data" → `clear_dashboard_cache()`.
- `result.errors` → `st.error` per item.
- `result.missing_dates` → satu caption ("N hari tanpa artifact: …") supaya gap pada
  garis tren jelas penyebabnya (bukan bug).
- Satu section per detector (pakai `st.subheader` atau `st.tabs`), setiap section
  memanggil fungsi agregasi lalu render `st.altair_chart` (line/area). Hasil kosong →
  `st.info`.
- Chart pakai **Altair** (sudah dipakai di `detector_tab.py`), sumbu-X `source_date:T`.

## Perubahan di luar halaman baru

Satu baris di `detectors.py` `DETECTOR_SHEETS` (detectors.py:13): tambah
`"Intermittent": ["M2b_intermittent_WindowErrors"]` supaya halaman deep-dive yang ada
juga mencakup LSTM-AE (saat ini tidak ada tab-nya). Ini melengkapi cakupan "semua
detector".

## Testing

`tests/unit/dashboard/test_trends.py` (pola `test_loader.py`), satu grup test per fungsi
agregasi:

- **Happy path:** DataFrame sintetis multi-`source_date` → assert nilai agregat benar
  per hari (mis. 2 hari, count/median/max sesuai hitungan manual).
- **Intent yang di-encode (Rule 9):** agregasi HARUS group by `source_date` (tanggal
  artifact), bukan timestamp internal sheet. Test `WindowErrors` sengaja memberi `date`
  internal berbeda dari `source_date` dan meng-assert hasil mengikuti `source_date`.
- **Ketahanan:** sheet hilang / kolom hilang / DataFrame kosong → kembalikan DataFrame
  kosong, tidak raise.
- **Wide melt:** `wide_counts_per_day` menghasilkan long dengan jumlah baris =
  hari × jumlah count_col.

## Ringkasan skop

| File | Aksi |
|---|---|
| `pv_pipeline/dashboard/data/trends.py` | baru (~5 fungsi agregasi murni) |
| `pv_pipeline/dashboard/pages/trends.py` | baru (UI) |
| `pv_pipeline/dashboard/pages/5_Trends.py` | baru (wrapper) |
| `tests/unit/dashboard/test_trends.py` | baru |
| `pv_pipeline/dashboard/pages/detectors.py` | +1 baris (tab Intermittent) |

Tidak menyentuh data layer Drive, cache, gdrive, atau halaman lain.
