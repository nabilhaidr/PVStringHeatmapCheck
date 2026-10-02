# Kalibrasi silang POA per periode — rancangan

Tanggal: 2 Oktober 2026. Status: disetujui pengguna (batas periode otomatis dari celah; celah minimal 30 hari; rancangan
utuh). Lanjutan dari `docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md`.

## Tujuan

Menghitung gain dan kesepakatan acuan per (stasiun cuaca, periode), dengan periode dipotong di celah data panjang.
Kalibrasi atas rentang gabungan tidak sah untuk WS yang berubah di tengah rentang.

## Latar (dari data)

- **WS-2** kosong 1 Mar – 9 Jun 2026 (101 hari). Sebelumnya ia membaca 0,99–1,03 × median WS lain; sesudahnya 1,08–1,14.
  Dalam musim yang sama, taksiran orientasinya berubah dari miring ~4° (2025) ke ~26° (2026).
- **Run 2 Okt 2026, kriteria 3 % / 100, rentang 2025-01..2026-07:** WS-2 diusulkan 0,976 dari 6 bulan sah, dan
  semuanya berasal dari periode SEBELUM celah. Acuan larik Jun–Jul (1,205) sudah menunjukkan selisihnya, tapi alat tidak
  tahu tentang periode.
- **Celah ≥ 30 hari (2025-01..2026-08):**
  - WS-1: 19 Jun – 23 Jul 2025 (35) dan 25 Jul – 25 Agu 2025 (32), diselingi 1 hari berdata;
  - WS-2: 1 Mar – 9 Jun 2026 (101);
  - WS-3: 31, 55, 61, 37, 68 hari sepanjang 2025, diselingi pulau data 1 hari;
  - WS-4: tidak ada (hanya 11 hari kosong di awal data);
  - WS-5: 2 Jun – 6 Jul 2025 (35) dan 1 Okt – 31 Des 2025 (92).

## Keputusan pengguna

| Hal | Keputusan |
|---|---|
| Batas periode | Otomatis dari celah data; tanpa batas manual |
| Celah minimal | 30 hari tanpa data |
| Hasil gabungan | Diganti hasil per periode (tidak disimpan berdampingan) |

## Arsitektur

### `pv_pipeline/poa/kalibrasi_silang.py` (dua fungsi baru; fungsi lama tidak diubah)

- **`periode_ws(poa: pd.DataFrame, *, min_celah_hari: int = 30) -> pd.DataFrame`**
  - Kolom `ws, periode, mulai, akhir`; `mulai`/`akhir` bertipe `pd.Timestamp` tengah malam (tanggal hari berdata pertama
    dan terakhir periode itu).
  - Hari "berdata" = WS itu punya ≥ 1 nilai non-NaN di hari itu.
  - Deretan ≥ `min_celah_hari` hari tanpa data **di antara** dua hari berdata memotong periode. Hari kosong di awal atau
    akhir rentang tidak membuat periode.
  - `periode` bernomor 1..n per WS, urut waktu. WS tanpa data sama sekali tidak muncul.
- **`kalibrasi_per_periode(poa, stabil, rasio, jam_penghalang, poa_cerah, periode, kalibrasi_harian=None, wb_to_ws=None,
  *, min_sampel=200, tol=0.03) -> pd.DataFrame`**
  - Untuk setiap baris `periode`, jendela = `mulai` 00:00 s.d. `akhir` 23:59:59:
    - `rel` = `gain_relatif(rasio.loc[jendela, [ws]], jam_penghalang, min_sampel=min_sampel)`;
    - `absolut` = `gain_absolut(poa.loc[jendela], poa_cerah, stabil.loc[jendela])` untuk SEMUA WS, supaya normalisasi
      median di `sepakati` berasal dari jendela yang sama;
    - `larik` = `gain_larik(kalibrasi_harian` yang `date`-nya di dalam jendela`, wb_to_ws)`, atau `None` bila
      `kalibrasi_harian` None atau tak ada hari di jendela;
    - `sepakati(rel, absolut, larik, tol=tol)`; ambil baris WS itu.
  - Kolom keluaran: `ws, periode, mulai, akhir` lalu kolom `sepakati` (`gain_rel, gain_abs, gain_larik, n_bulan_sah,
    bergeser, status, usulan, alasan`).
  - Periode tanpa sampel stabil menghasilkan `n_bulan_sah` 0 dan `perlu_lapangan` ("data bulanan kurang") lewat aturan
    `sepakati` yang sudah ada; tidak ada logika khusus untuk pulau data pendek.

### `run_poa_cross_calibration.py`

- Sesudah `sampel_stabil`, `rasio_ke_median`, `penghalang`, dan POA langit cerah: `periode = periode_ws(poa)`, lalu
  `sep = kalibrasi_per_periode(...)` dengan `--min-sampel` yang ada dan `kalibrasi_harian` dari `--m2f-dir` (None bila
  tanpa).
- Sheet `Kesepakatan` = `sep` (baris per WS × periode). Delapan sheet tetap sama. `Relatif`, `Absolut`, dan `Larik` tetap
  atas seluruh rentang, sebagai rujukan.
- Usulan YAML dicetak dengan label "BELUM diterapkan":

  ```yaml
  pyranometer:
    ws_gain_periode:
      WS-2:
        - {mulai: 2026-06-10, akhir: 2026-08-31, gain: 0.940}   # contoh bentuk, bukan hasil
    ws_jam_penghalang: ...   # tidak berubah
  ```

- Penghalang tetap dihitung atas seluruh rentang. Config dan loader tidak diubah.

## Uji (`tests/unit/test_poa_kalibrasi_silang.py`, sintetis)

- **`periode_ws`:**
  - WS-2 kosong 40 hari di tengah → 2 periode dengan `mulai`/`akhir` tepat;
  - WS kosong 20 hari di tengah → 1 periode;
  - hari kosong di awal rentang → bukan periode, `mulai` = hari berdata pertama.
- **`kalibrasi_per_periode`:** WS-2 bergain 1,0 pada Jan–Feb, kosong 40 hari, lalu 0,8 pada pertengahan Apr–Mei (≥ 2
  bulan sah per periode). Periode 1 `usulan` ≈ 1,0 dan periode 2 ≈ 1,25. Inilah alasan fitur ini: satu faktor untuk
  seluruh rentang mencampur kedua sensor.
- **CLI:** uji yang ada tetap lolos (tanpa celah = 1 periode per WS); ditambah `Kesepakatan` punya kolom `periode` = 1.

## Hasil

### Run pertama (2 Okt 2026, commit `482d1d7`)

`python run_poa_cross_calibration.py --raw-root "F:/Downloads part 2" --m2f-dir "F:/Downloads part 2/cek pv/m2f" --akhir 2026-08-31`
→ `coba/poa_cross_calibration_20250101_20260831.xlsx` (kriteria stabil 3 % / 100).

| WS | periode | rentang | rel / abs / larik | bulan sah | status |
|---|---|---|---|---|---|
| WS-1 | 1 | 2025-01-12 – 2025-06-18 | 0,880 / 0,960 / – | 3 | perlu_lapangan (berselisih) |
| WS-1 | 2 | 2025-07-24 (1 hari) | – | 0 | perlu_lapangan (data kurang) |
| WS-1 | 3 | 2025-08-26 – 2026-08-31 | 0,959 / 0,959 / 0,993 | 5 | usulan 1,043 (abs, rel) |
| WS-2 | 1 | 2025-01-15 – 2026-02-28 | 1,015 / 1,029 / – | 6 | usulan 0,978 (abs, rel) |
| WS-2 | 2 | 2026-06-10 – 2026-08-31 | 1,066 / 1,061 / 1,205 | 1 | perlu_lapangan (data kurang) |
| WS-3 | 1 | 2025-01-12 – 2025-03-14 | 1,013 / 1,014 / – | 1 | perlu_lapangan (data kurang) |
| WS-3 | 2–5 | pulau data 2025 (1–22 hari) | – | 0 | perlu_lapangan (data kurang) |
| WS-3 | 6 | 2026-01-01 – 2026-08-31 | 0,953 / 0,957 / 0,977 | 5 | perlu_lapangan (bergeser) |
| WS-4 | 1 | 2025-01-12 – 2026-08-31 | 1,002 / 1,000 / 0,980 | 10 | perlu_lapangan (bergeser) |
| WS-5 | 1 | 2025-01-01 – 2025-06-01 | 1,016 / 1,000 / – | 4 | usulan 0,992 (abs, rel) |
| WS-5 | 2 | 2025-07-07 – 2025-09-30 | – / 1,000 / – | 0 | perlu_lapangan (data kurang) |
| WS-5 | 3 | 2026-01-01 – 2026-08-31 | 1,050 / 1,000 / 1,008 | 6 | perlu_lapangan (bergeser) |

- Penghalang: tidak ada yang lolos ambang 10 % × 3 bulan.
- Hari M2f untuk acuan larik hanya Jun–Jul 2026, jadi kolom larik hanya terisi pada periode yang mencakupnya.

**Penilaian kewajaran (pemeriksaan lapangan belum ada):**
1. **WS-2 kini dipisah dengan benar.** Usulan 0,978 hanya untuk periode sebelum celah. Periode sesudah celah (rel 1,066 / abs 1,061) menunggu ≥ 2 bulan sah atau hasil lapangan. Usulan gabungan 0,976 yang menyesatkan (run 2 Okt di spesifikasi 2026-10-01) tidak muncul lagi.
2. **WS-1 periode 3: usulan 1,043 hanya untuk amplitudo.** Ia sejalan dengan taksiran orientasi (gain 0,966 di luar jam bayangan, 1/0,966 = 1,035). Bayangan pukul 11:24–12:49 di bulan matahari-utara (Jun–Agu 2026) TIDAK tertangkap `penghalang`, karena sampel stabil di bulan-bulan itu sedikit. Usulan ini jangan diterapkan sebelum penghalang WS-1 diperiksa (`docs/Pemeriksaan_Lapangan_WS1_WS2.md`).
3. **WS-3, WS-4, WS-5 (2026):** gain berayun > 5 % antar-bulan, sehingga satu faktor per periode tidak sah. Penyebab ayunan belum diselidiki (musim, kebersihan kubah, atau acuan yang ikut bergeser).
4. Pulau data pendek (WS-1 periode 2, WS-3 periode 2–5) muncul sebagai baris `perlu_lapangan` tanpa logika khusus, sesuai rancangan.

## Di luar cakupan

- Batas periode manual (misalnya tanggal perbaikan lapangan yang tidak meninggalkan celah).
- Flag CLI untuk `min_celah_hari`.
- Deteksi titik ubah tanpa celah.
- Penerapan `ws_gain_periode` di loader (spesifikasi terpisah sesudah pemeriksaan lapangan).
