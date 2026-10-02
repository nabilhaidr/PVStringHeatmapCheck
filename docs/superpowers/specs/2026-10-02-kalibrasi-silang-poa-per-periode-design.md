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

### Run kedua: penghalang dari hari cerah (2 Okt 2026, commit `13f759e` + `2ac2ffd`)

Perintah sama. `profil_jam`/`penghalang` kini dari `rasio_cerah` (sampel cerah menurut WS lain, bacaan ≤ 0 dibuang).

- **Penghalang: WS-1 pukul 11 dan 12 (3 bulan masing-masing).** Profil hari cerah WS-1:

  | jam | Jan 2025 – Mei 2026 (13 bulan berdata) | Jun 2026 | Jul 2026 | Agu 2026 |
  |---|---|---|---|---|
  | 11 | 0,97–1,02 | 0,32 | 0,41 | 0,40 |
  | 12 | 0,96–1,10 | 0,40 | 0,41 | 0,39 |

  Jun 2025 (posisi matahari sama dengan Jun 2026): 0,97 / 1,03. **Bayangan baru muncul Jun 2026**, bersamaan dengan data
  WS-1 kosong 1–10 Jun 2026 dan WS-2 kembali 10 Jun 2026. Penilaian "musiman" di run pertama keliru.
- **WS-1 periode 3:** jam 11–12 kini dibuang dari gain relatif (0,959 → 0,962); statusnya berubah dari usulan 1,043 ke
  `perlu_lapangan` (bergeser). Periode 1: 1 bulan sah (sebelumnya 3), karena jam 11–12 ikut dibuang.
- Usulan tersisa: WS-2 0,978 (2025-01-15 – 2026-02-28) dan WS-5 0,992 (2025-01-01 – 2025-06-01). Baris WS lain sama
  dengan run pertama.
- **Bacaan nol:** WS-1 mencatat 0 W/m² saat median WS lain > 500 W/m² (10–14): Okt 2025 11 sampel, Nov 28, Des 24,
  Jan 2026 6, Apr 2, Mei 5. Seluruh sampel cerah Nov–Des 2025 bernilai 0 (logger/sensor mati). Kini dibuang dari
  `rasio_cerah`; jalur sampel stabil sudah membuangnya lewat POA > 300.

### Ayunan WS-3/4/5 di 2026 (2 Okt 2026)

Gain bulanan 2026 dihitung ulang dengan **himpunan pembanding tetap WS-3/4/5** (tanpa WS-1 dan WS-2), kriteria 3 % / 100:

| WS | ayunan, pembanding 5 WS (alat) | ayunan, pembanding tetap WS-3/4/5 |
|---|---|---|
| WS-3 | 0,053 | 0,067 |
| WS-4 | 0,063 | **0,023** |
| WS-5 | 0,067 | **0,040** |

- **WS-4 dan WS-5:** ayunan > 5 % di alat adalah artefak acuan. "Median WS lain" berubah susunan: tanpa WS-2 pada Mar–Jun 2026 (median turun, rasio naik), lalu dengan WS-2 yang +12 % sejak Jun (median naik, rasio turun). Dengan pembanding tetap, keduanya stabil di bawah 5 %.
- **WS-3: lompatan ~+6 % sekitar 10 Agu 2026.** Energi harian 09–15, median sebelum 10 Agu (10 Jun – 9 Agu, 60 hari) vs sesudah (22 hari):

  | pasangan | sebelum | sesudah | lompatan |
  |---|---|---|---|
  | WS-4 / WS-5 | 0,962 | 0,968 | +0,6 % |
  | WS-3 / rata(WS-4, WS-5) | 0,981 | 1,037 | +5,7 % |
  | WS-3 / WS-2 | 0,899 | 0,992 | +10,3 % |
  | WS-4 / WS-2 | 0,901 | 0,932 | +3,5 % |
  | WS-5 / WS-2 | 0,939 | 0,969 | +3,2 % |

  WS-3 membaca ~5–7 % di bawah WS-4/5 sejak Jan 2026 lalu kembali ke ~1,0; pola ini cocok dengan kubah kotor yang dibersihkan, atau sensor yang dikalibrasi ulang/diganti. Pada saat yang sama WS-2 tampak turun ~3 % (WS-4 dan WS-5 sama-sama naik ~3 % terhadapnya; rasio bulanan WS-2 1,12 Jul → 1,08 Agu). Kemungkinan satu kunjungan pemeliharaan.
- **Konsekuensi untuk alat:** lompatan tanpa celah data tidak tertangkap `periode_ws`. Ini bukti pertama bahwa batas periode manual atau deteksi titik ubah (di luar cakupan di bawah) dibutuhkan. Pembanding "median WS lain" juga peka terhadap susunan WS yang ada; pembanding tetap dari WS yang sehat lebih stabil.

## Tambahan: batas periode manual (2 Okt 2026, disetujui pengguna: flag CLI)

Latar: WS-3 melompat ~+6 % sekitar 10 Agu 2026 tanpa celah data (bagian "Ayunan WS-3/4/5"), dan tanggal perbaikan
lapangan nanti juga tidak meninggalkan celah.

- **`periode_ws(poa, *, min_celah_hari=30, batas=None)`**: `batas` = `{ws: [tanggal, …]}`. Tanggal batas = **hari
  pertama periode baru**. Potongan dari batas digabung dengan potongan dari celah. Batas sebelum hari berdata pertama,
  sesudah hari berdata terakhir, atau di dalam celah tidak membuat periode baru (tidak ada periode kosong).
- **CLI:** `--batas WS-3:2026-08-10`, boleh diulang. Nilai yang dipakai dicatat di sheet `Catatan` (butir "batas periode
  manual"; "-" bila tidak ada).
- `kalibrasi_per_periode`, sheet, dan YAML tidak berubah.
- **Uji (sintetis):** batas 2026-02-15 pada WS-2 tanpa celah → 2 periode (1 Jan – 14 Feb, 15 Feb – 30 Apr); batas di
  dalam celah → tetap 2 periode; gain WS-2 melompat 1,0 → 0,8 di tanggal batas tanpa celah → usulan ≈ 1,0 dan ≈ 1,25;
  CLI `--batas WS-3:2026-02-15` → `Kesepakatan` WS-3 periode 1 dan 2, `Catatan` memuat batasnya.

### Run ketiga: batas manual WS-3 (2 Okt 2026, commit `f7d88d8`)

`… --akhir 2026-08-31 --batas WS-3:2026-08-10` → `coba/poa_silang_20261002_batas.txt`; tanpa peringatan atau galat.

| WS-3 | rentang | rel / abs / larik | bulan sah | status |
|---|---|---|---|---|
| 6 | 2026-01-01 – 2026-08-09 | 0,952 / 0,944 / 0,977 | 4 | usulan 1,055 (abs, rel) |
| 7 | 2026-08-10 – 2026-08-31 | 0,999 / 1,007 / – | 1 | perlu_lapangan (data kurang) |

- WS-3 membaca ~5 % rendah Jan – 9 Agu 2026 lalu kembali ~1,0, sesuai lompatan di bagian "Ayunan WS-3/4/5". Usulan
  1,055 untuk periode 6 masuk akal **bila** penyebabnya kubah kotor (koreksi data lama), tetapi tunggu jawaban log
  pembersihan di daftar periksa lapangan sebelum diterapkan.
- Baris WS lain, usulan WS-2 (0,978, sebelum celah) dan WS-5 (0,992, awal 2025), serta penghalang WS-1 [11, 12] tidak
  berubah dari run kedua.

## Di luar cakupan

- ~~Batas periode manual~~ (ditambahkan 2 Okt 2026, lihat bagian sebelumnya).
- Flag CLI untuk `min_celah_hari`.
- Deteksi titik ubah tanpa celah.
- Penerapan `ws_gain_periode` di loader (spesifikasi terpisah sesudah pemeriksaan lapangan).
