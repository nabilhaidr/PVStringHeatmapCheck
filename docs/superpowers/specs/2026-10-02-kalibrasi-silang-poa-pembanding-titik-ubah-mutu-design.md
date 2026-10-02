# Kalibrasi silang POA: pembanding tetap, usulan titik ubah, mutu data — rancangan

Tanggal: 2 Oktober 2026. Status: disetujui pengguna (rancangan utuh; titik ubah jendela 14 hari, ambang 5 %).
Lanjutan dari `2026-10-01-kalibrasi-silang-poa-design.md` dan `2026-10-02-kalibrasi-silang-poa-per-periode-design.md`.

## Latar

- **Acuan bergeser:** "median stasiun lain" berubah susunan ketika WS-2 hilang (Mar–Jun 2026) lalu kembali +12 %.
  Ayunan WS-4/WS-5 0,063/0,067 turun ke 0,023/0,040 dengan pembanding tetap WS-3/4/5.
- **Lompatan tanpa celah:** WS-3 +5,7 % sekitar 10 Agu 2026 baru ketahuan lewat analisa tangan; `--batas` perlu
  tanggal dari pengguna.
- **Mutu data tersembunyi:** WS-1 mencatat 0 W/m² di siang cerah Okt 2025 – Mei 2026; `Catatan` hanya memuat satu angka
  galat total.

## Arsitektur (`pv_pipeline/poa/kalibrasi_silang.py`)

### 1. Pembanding tetap

- Pembantu `_acuan_ws(kolom, ws, pembanding)`: stasiun acuan untuk `ws` = `pembanding` tanpa `ws` sendiri (yang ada di
  `kolom`), atau semua WS lain bila `pembanding` None.
- `rasio_ke_median(..., pembanding=None)`, `rasio_cerah(..., pembanding=None)`: memakai `_acuan_ws`. Syarat ≥ 2
  pembanding tetap.
- `sepakati(..., pembanding=None)`: `gain_abs` dinormalkan ke median `gain_abs` stasiun pembanding (semua WS bila None).
- `kalibrasi_per_periode(..., pembanding=None)`: diteruskan ke `sepakati`.
- Default None = perilaku sekarang.

### 2. Usulan titik ubah

- `rasio_harian(poa, *, pembanding=None, jam=("09:00", "15:00"), min_sampel=40) -> DataFrame` (indeks hari, kolom WS):
  energi harian WS ÷ energi harian acuan (median stasiun acuan, ≥ 2 ada) pada sampel yang sama; bacaan ≤ 0 atau > 1400
  dibuang; hari dengan < `min_sampel` sampel = NaN.
- `titik_ubah(rasio_hari, periode, *, jendela_hari=14, ambang=0.05, min_hari=10) -> DataFrame` berkolom
  `ws, periode, tanggal, sebelum, sesudah, lompatan`:
  - per (WS, periode), untuk tiap hari d: `sebelum` = median rasio di [d − 14 hari, d), `sesudah` = median di
    [d, d + 14 hari), keduanya ≥ `min_hari` hari; `lompatan` = sesudah ÷ sebelum − 1;
  - hari dengan |lompatan| ≥ ambang dikelompokkan (jarak antar-hari ≤ jendela = satu kelompok); per kelompok diambil
    **tengah dataran** |lompatan| maksimum;
  - jendela tidak melintasi batas periode, jadi celah yang sudah memotong periode tidak muncul lagi.

### 3. Mutu data

- `mutu_data(poa, *, cerah_min=500.0, jam=("10:00", "14:00")) -> DataFrame` berkolom
  `ws, hari_kosong, nol_saat_cerah, galat`:
  - `hari_kosong`: hari dalam rentang tanpa satu pun sampel;
  - `nol_saat_cerah`: sampel = 0 pada jam 10–14 saat median stasiun lain > `cerah_min` W/m²;
  - `galat`: sampel < 0 atau > 1400.

### CLI (`run_poa_cross_calibration.py`)

- `--pembanding WS-3,WS-4,WS-5` (default kosong = semua WS lain) diteruskan ke `rasio_ke_median`, `rasio_cerah`,
  `kalibrasi_per_periode`, dan `rasio_harian`; dicatat di `Catatan` ("pembanding").
- Sheet baru **`TitikUbah`** (sebelum `Catatan`; kosong bila tak ada kandidat). Kandidat dicetak sebagai saran
  `--batas WS-n:YYYY-MM-DD` dengan label "periksa dulu; bukan batas otomatis". Tidak ada batas yang diterapkan otomatis.
- `Catatan`: baris "sampel galat (<0 atau >1400)" total diganti tiga baris per WS: "hari kosong per WS",
  "sampel nol saat WS lain cerah (10-14, > 500 W/m2) per WS", "sampel galat (<0 atau >1400) per WS".

## Uji (sintetis)

- **Pembanding:** WS-2 kosong sebulan lalu kembali ×1,12; WS-4 dengan pembanding WS-3/4/5 tidak `bergeser` (ayunan
  < 0,01), padahal dengan acuan semua WS ia bergeser (prasyarat skenario). `sepakati` dengan pembanding menormalkan
  `gain_abs` ke median pembanding.
- **Titik ubah:** WS-2 ×0,9 mulai 2026-02-15 → satu kandidat WS-2 2026-02-15, lompatan ≈ −0,10; tanpa lompatan → kosong;
  lompatan tepat di batas celah 40 hari → kosong.
- **Mutu data:** WS-1 nol sehari penuh → `nol_saat_cerah` = 49 (10:00–14:00 inklusif); WS-3 kosong sehari → `hari_kosong`
  1.
- **CLI:** sembilan sheet termasuk `TitikUbah`; `Catatan` memuat baris pembanding dan mutu data per WS.

## Perbaikan saat verifikasi (2 Okt 2026)

Run nyata pertama memperlihatkan dua cacat `titik_ubah`; keduanya diperbaiki dengan uji gagal lebih dulu:

1. **Lompatan berlawanan arah digabung.** Turun lalu naik dalam ≤ 14 hari jadi satu kelompok, dan hanya yang terbesar
   dilaporkan. Kini kelompok dipisah bila tanda lompatan berbalik.
2. **Jam penghalang ikut dalam rasio harian.** Bayangan WS-1 yang berubah mengikuti matahari terbaca sebagai lompatan
   (+13 % pada 28 Jun 2026). Kini `rasio_harian(..., jam_penghalang=...)` membuang jam penghalang WS itu, konsisten dengan
   `gain_relatif`; CLI meneruskan sheet `Penghalang`.

Keterbatasan yang tersisa: lompatan yang bertepatan dengan celah < 30 hari (penurunan WS-1 saat kosong 1–10 Jun 2026)
tidak terdeteksi, karena jendela sebelumnya kurang dari 10 hari berdata.

## Hasil (2 Okt 2026)

`python run_poa_cross_calibration.py --raw-root "F:/Downloads part 2" --m2f-dir "F:/Downloads part 2/cek pv/m2f"
--akhir 2026-08-31 --pembanding WS-3,WS-4,WS-5` → `coba/poa_silang_20261002_pembanding.txt`; tanpa peringatan atau galat.

**Kesepakatan, dibanding acuan semua WS:**

| WS | periode | acuan semua WS | pembanding WS-3/4/5 |
|---|---|---|---|
| WS-2 | 2025-01-15 – 2026-02-28 | usulan 0,978 | usulan 0,983 |
| WS-3 | 2026-01-01 – 2026-08-31 (tanpa `--batas`) | bergeser | bergeser (rel 0,945 / abs 0,957 / larik 0,977) |
| WS-4 | 2025-01-12 – 2026-08-31 | bergeser | **usulan 1,000** (abs, larik, rel) |
| WS-5 | 2026-01-01 – 2026-08-31 | bergeser | usulan 0,996 (abs, larik; rel 1,048) |

- WS-4 sehat; ayunannya artefak acuan.
- Catatan: WS-3 sendiri ada di pembanding padahal membaca ~5 % rendah Jan – 9 Agu 2026. Median (WS-3, WS-4) yang menjadi
  acuan WS-5 ikut rendah, sehingga rel WS-5 1,048 berselisih dengan abs/larik. Pembanding yang benar-benar sehat untuk 2026
  mungkin hanya WS-4 dan WS-5; ini keputusan pengguna.

**Kandidat titik ubah (`TitikUbah`):**

| WS | tanggal | lompatan | penilaian |
|---|---|---|---|
| WS-3 | 2026-08-11 | +7,9 % | lompatan yang diketahui (taksiran tangan ~10 Agu) |
| WS-2 | 2026-08-11 | −5,6 % | sebagian karena acuan ikut naik saat WS-3 melompat; terhadap WS-4/WS-5 masing-masing ~3 % |
| WS-5 | 2026-01-11 | +8,2 % | **baru**: 10 hari pertama sesudah kosong Okt–Des 2025 ~8 % rendah. Ditanyakan ke lapangan |
| WS-1 | 10 kandidat, Mar 2025 – Agu 2026 | ±5–11 % | WS-1 tidak stabil (tepi bayangan, bacaan nol, celah); jangan dipakai sampai diperbaiki |

**Mutu data (`Catatan`, 2025-01-01..2026-08-31):**

| WS | hari kosong | sampel nol saat WS lain cerah | sampel galat |
|---|---|---|---|
| WS-1 | 108 | 91 | 0 |
| WS-2 | 117 | 0 | 1 |
| WS-3 | 296 | 2 | 1 |
| WS-4 | 13 | 0 | 0 |
| WS-5 | 165 | 16 | 1 |

## Di luar cakupan

- Menerapkan kandidat titik ubah otomatis.
- Memilih pembanding otomatis.
