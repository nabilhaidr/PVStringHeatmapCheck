# Handoff: Halaman "Trends" Dashboard M2 (SolarYieldPro)

> Ringkasan mandiri untuk melanjutkan pekerjaan di chat baru / Codex.
> Dibuat 2026-07-08.

## Konteks & Tujuan
Menambahkan **halaman dashboard Streamlit baru "Trends"** yang menampilkan **grafik tren harian** (deret waktu per `source_date`) untuk semua detector M2 dan metrik perhitungannya. Halaman deep-dive yang ada (`detectors.py`) hanya menampilkan snapshot agregat satu range — tanpa dimensi waktu. Halaman Trends menjawab "apakah kondisi memburuk/membaik dari hari ke hari".

**Proyek:** `C:\Users\nabil\Downloads\SolarYieldPro-main\kodingan pv string` (branch `master`, Python, pandas/Altair/Streamlit).

## Status Saat Ini
- ✅ **Spec ditulis:** `docs/superpowers/specs/2026-07-08-dashboard-m2-trends-design.md`
- ✅ **Rencana implementasi ditulis:** `docs/superpowers/plans/2026-07-08-dashboard-m2-trends.md` — berisi kode lengkap tiap task (TDD).
- ⬜ **Belum ada kode diimplementasi.** Kedua dokumen di atas **belum di-commit** (masih untracked).
- **Langkah berikutnya:** eksekusi rencana. Rekomendasi: **Inline Execution** (semua task saling bergantung pada file `trends.py` yang sama, skop kecil).

## Keputusan Desain
Dipilih pendekatan **"Halaman Trends terkurasi"** (bukan generik): metrik fisik yang benar per detector, dipetakan ke nama sheet & kolom yang **sudah diverifikasi dari kode**.

**Arsitektur** (ikuti pola halaman existing persis):
- `data/trends.py` — modul agregasi **murni** (TANPA import Streamlit), unit-testable.
- `pages/trends.py` — UI Streamlit (import `altair`/`streamlit` lazy di dalam fungsi, pakai `# noqa: WPS433`).
- `pages/5_Trends.py` — wrapper tipis (`from ...pages.trends import main; main()`).

**Sumber data:** pakai `cached_findings_range(start, end)` yang SUDAH ADA (`pv_pipeline.dashboard.data.cache`). Tiap baris sheet sudah punya kolom `source_date` (ditambah oleh `concat_findings_range`) = sumbu-X tren. **Tidak ada layer data Drive baru.** Default range 30 hari.

## Fakta Terverifikasi (penting untuk implementasi)
Nama sheet = pola `{submodule.name}_{artifact}` (`core.py:265`, limit 31 char):

| Detector | Sheet | Kolom metrik |
|---|---|---|
| Semua | `Findings` | `sub_module`, `severity` (count per hari) |
| Soiling | `M2a_soiling_EconomicAnalysis` | `soiling_ratio`, `sr_ci_lower`, `sr_ci_upper`, `recommend_cleaning` |
| Intermittent (LSTM-AE) | `M2b_intermittent_WindowErrors` | `error_ratio` (+ punya `date` internal ≠ `source_date`), threshold=1.0 |
| MpptRatio | `M2b_mppt_ratio_StringStatus` | `ratio_median_daylight`, `status` |
| IForest | `M2_iforest_AnomalySummary` | `flagged_pct` |
| Shading | `M2a_shading_ShadingSummary` | `n_suspicious` |
| LowIrradiance | `M2a_low_irradiance_LowIrradianceSummary` | **WIDE**: kolom `normal`/`low_irradiance_underperform`/`general_underperform`/`skipped` |
| PeerZ / OpenCircuit / GroundFault | `M2b_peer_zscore_StringStatus` / `M2b_open_circuit_StringStatus` / `M2b_ground_fault_StringStatus` | `status` |

**2 bentuk data khusus:** (a) `LowIrradianceSummary` wide-format → perlu `melt`; (b) `WindowErrors` harus group by `source_date`, BUKAN kolom `date` internal.

## 4 Task (semua TDD, commit lokal per task — TIDAK push)
1. **`data/trends.py` fungsi inti** + `tests/unit/dashboard/test_trends.py` (8 test): `findings_counts_per_day`, `status_counts_per_day`, `numeric_metric_per_day(df, value_col, aggs=("mean","max","median"))`.
2. **`data/trends.py` fungsi khusus** + 5 test: `soiling_ratio_per_day`, `wide_counts_per_day(df, count_cols)`.
3. **UI** `pages/trends.py` + `pages/5_Trends.py` (verifikasi = smoke import + `pytest tests/unit/dashboard/`).
4. **+1 baris** di `pages/detectors.py` `DETECTOR_SHEETS`: `"Intermittent": ["M2b_intermittent_WindowErrors"]` (setelah baris `MpptRatio`).

**Kontrak semua fungsi agregasi:** sheet/kolom hilang atau input kosong → kembalikan **DataFrame kosong, JANGAN raise** (UI tampilkan `st.info` "detector tidak aktif").

## Konvensi Kodebase (wajib diikuti)
- Test: `python -m pytest tests/unit/dashboard/test_trends.py -v`.
- Data-module (`data/*.py`) = murni & unit-tested; page (`pages/*.py`) = tidak diunit-test, import Streamlit lazy.
- Commit conventional (`feat:`, `test:`), **lokal saja**. User push manual ke 2 remote (nabilhaidr + ompltsikn) saat diminta.
- CLAUDE.md: simplicity first, surgical changes, TDD, jangan dispatch subagent tanpa diminta.
- ⚠️ Ada **fact-forcing gate hook**: sebelum Bash pertama & sebelum Write file baru, harus menyajikan fakta dulu (request user, apa yang diverifikasi/diproduksi, file pemanggil, dsb).

## Untuk Mulai
Buka rencana di `docs/superpowers/plans/2026-07-08-dashboard-m2-trends.md` dan kerjakan Task 1 Step 1. Semua kode (test + implementasi) sudah tertulis lengkap di sana.
