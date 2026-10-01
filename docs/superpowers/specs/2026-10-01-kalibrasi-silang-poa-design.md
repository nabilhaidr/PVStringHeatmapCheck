# Kalibrasi silang POA antar-stasiun cuaca — rancangan

Tanggal: 1 Oktober 2026. Status: disetujui pengguna per bagian (tujuan; acuan; arsitektur dan aliran data; aturan, kasus tepi, uji).

## Tujuan

Mengukur bias amplitudo sensor POA tiap stasiun cuaca (WS-1..WS-5) dan jam-jam penghalangnya, lalu mengusulkan faktor koreksi hanya bila acuan-acuan independen sepakat.

Bias POA terbawa ke semua modul yang memakai POA terukur: `E_expected` M2f, soiling M2a, dan detektor low-irradiance. Pada kalibrasi derate 1 Okt 2026, bias ini muncul sebagai `rasio_bersih` yang mengikuti stasiun cuaca, bukan modul.

Tahap ini **laporan dulu**. Menerapkan koreksi di loader adalah spesifikasi terpisah setelah usulan disetujui.

## Latar (dari data)

- **Kalibrasi derate 2026-10-01** (`docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md`, 42 hari Jun–Jul 2026):
  - Kt POA rata-rata per stasiun cuaca: WS-1 0,56; WS-2 0,79 (maks 1,02); WS-3 0,69; WS-4 0,68; WS-5 0,72.
  - Korelasi Kt harian WS-1 dengan stasiun lain 0,88, sedangkan antarstasiun lain 0,98–0,99.
  - `rasio_bersih` per WB mengikuti stasiun cuaca: WS-2 (WB05/07) 0,74–0,76; WS-1 (WB08–10) 0,96–1,00.
- **Memori `poa-telemetry-5min-offset`:** WS-1 turun ke 0,36–0,77× median pada pukul 11–12 di tiga hari (dugaan penghalang); energi harian WS-1 0,83–0,91× rata-rata 5 stasiun cuaca.
- **Memori `poa-ws-data-gaps`:** hari POA kosong WS-3 304 (2025), WS-5 171 (2025), WS-2 102 (2026).
- **Data:** `F:\Downloads part 2\raw data input\POA PLTS IKN 2025.xlsx` dan `... 2026.xlsx`, 5 menit, kolom `POA Irradiance (W/m2) WS 1..5`.
- **Loader:** `PyranometerLoader` belum punya faktor koreksi per stasiun cuaca.

## Keputusan pengguna

| Hal | Keputusan |
|---|---|
| Tujuan | Laporan dulu; koreksi di loader lewat langkah terpisah setelah disetujui |
| Acuan | Tiga acuan: median stasiun cuaca lain (utama), POA langit cerah pvlib (absolut), larik lewat `measured_ratio` M2f (independen dari sensor) |
| Aturan usulan | Faktor diusulkan hanya bila ≥ 2 acuan sepakat dalam ±3 %; bila tidak, "perlu pemeriksaan lapangan" |

## Arsitektur

### `pv_pipeline/poa/kalibrasi_silang.py` (fungsi murni, teruji sintetis)

Masukan POA berupa `DataFrame` 5 menit berkolom `WS-1..WS-5` (seperti `PyranometerLoader.df` tanpa `avg`), berindeks waktu lokal.

- **`sampel_stabil(poa: pd.DataFrame, *, toleransi=0.02, poa_min=300.0, jam=("09:00", "15:00")) -> pd.DataFrame`** (bool, bentuk sama)
  - Benar bila POA > `poa_min`, dalam jendela jam, dan simpangan |POA − rata-rata kedua tetangga| / POA < `toleransi` di sampel itu dan di kedua tetangganya.
  - Definisinya sama dengan "stabil" di `pv_pipeline/m2f/saturation.py`.
  - POA < 0 atau > 1400 W/m² dianggap galat sensor dan tidak stabil.
- **`rasio_ke_median(poa: pd.DataFrame, stabil: pd.DataFrame, *, min_pembanding=2) -> pd.DataFrame`**
  - Per sampel dan stasiun cuaca: POA_WS ÷ median POA stasiun LAIN yang stabil di sampel itu.
  - NaN bila stasiun itu tidak stabil atau pembandingnya < `min_pembanding`.
  - Stasiun yang kosong tidak diisi rata-rata situs.
- **`gain_bulanan(rasio: pd.DataFrame, *, min_sampel=200) -> pd.DataFrame`**: per stasiun cuaca per bulan, kolom `ws, bulan, n, median, iqr, alasan`. Bila n < `min_sampel`: median NaN, alasan "data tipis".
- **`profil_jam(rasio: pd.DataFrame) -> pd.DataFrame`**: per stasiun cuaca, bulan, dan jam (09..14), median rasio jam itu dibagi median rasio SELURUH sampel stasiun itu di bulan itu (kolom `ws, bulan, jam, n, profil`). Penghalang tampak sebagai profil < 1 pada jam tertentu.
- **`penghalang(profil: pd.DataFrame, *, ambang=0.10, min_bulan=3) -> pd.DataFrame`**: pasangan (stasiun cuaca, jam) yang profilnya < 1 − `ambang` pada ≥ `min_bulan` bulan.
- **`gain_relatif(rasio: pd.DataFrame, jam_penghalang: pd.DataFrame, *, min_sampel=200, ambang_geser=0.05) -> pd.DataFrame`**
  - Per stasiun cuaca: median rasio selama rentang tanpa jam penghalang, dengan kolom `ws, gain, n, ayunan_bulanan, bergeser`.
  - `ayunan_bulanan` = maks − min dari median bulanan yang sah. `bergeser` = ayunan > `ambang_geser`.
- **`gain_absolut(poa: pd.DataFrame, poa_cerah: pd.Series, stabil: pd.DataFrame, *, kt_min=0.75) -> pd.DataFrame`**: per stasiun cuaca, median POA_WS ÷ POA langit cerah pada sampel stabil saat MEDIAN Kt seluruh WS ≥ `kt_min`. Kecerahan dinilai dari median semua WS, bukan dari rasio WS itu sendiri, supaya sensor yang membaca jauh terlalu rendah tidak tersaring keluar; perbaikan saat rencana ditulis, 1 Okt 2026. Kolom `ws, gain, n`.
- **`gain_larik(kalibrasi_harian: pd.DataFrame, wb_to_ws: dict) -> pd.DataFrame`**
  - `kalibrasi_harian` berkolom `date, wb_id, measured_ratio`.
  - Per hari: median situs ÷ rasio WB. Per stasiun cuaca: median atas WB dan hari yang dipetakan. Kolom `ws, gain, n`.
  - Bila larik setara, nilai ini sebanding dengan gain sensornya: POA tinggi → harapan tinggi → rasio rendah.
- **`sepakati(rel: pd.DataFrame, absolut: pd.DataFrame, larik: pd.DataFrame | None, *, tol=0.03) -> pd.DataFrame`**
  - Per stasiun cuaca, kolom `ws, gain_rel, gain_abs, gain_larik, bergeser, status, usulan, alasan`.
  - Untuk perbandingan, `gain_abs` dinormalkan ke median `gain_abs` semua stasiun, karena langit cerah pvlib punya bias bersama (kekeruhan, albedo).
  - `usulan_koreksi`: ≥ 2 acuan berpasangan dalam ±`tol`, dan tidak `bergeser`. `usulan` = 1 ÷ median acuan yang sepakat, yaitu faktor pengali POA.
  - Selain itu `perlu_lapangan`, dengan alasan (acuan berselisih, atau gain bergeser antarbulan).

### `run_poa_cross_calibration.py` (CLI di akar repo, pola `run_derate_calibration.py`)

```
python run_poa_cross_calibration.py --raw-root "F:/Downloads part 2" \
    [--mulai 2025-01-01] [--akhir 2026-07-31] [--m2f-dir "F:/Downloads part 2/cek pv/m2f"] \
    [--geometry config/site_geometry.yaml] [--output-dir coba]
```

1. **Muat POA** lewat `PyranometerLoader`. Path POA relatif diawali `--raw-root`, dan offset waktu diambil dari config, seperti `run_derate_calibration._muat_poa`. Potong ke rentang, lalu buang kolom `avg`.
2. **Hitung** `sampel_stabil` → `rasio_ke_median` → `gain_bulanan`, `profil_jam`, `penghalang` → `gain_relatif`.
3. **Acuan absolut.** POA langit cerah dari `PvlibClearSkyEstimator.from_geometry_yaml(geometry, load_albedo_provider=False)` pada indeks POA, lalu `gain_absolut`.
4. **Acuan larik** (bila `--m2f-dir`). `rekap_m2f.build_daily_calib` atas workbook harian, lalu `gain_larik` dengan `ws_to_wb` dari geometri.
5. **Kesepakatan.** `sepakati`.
6. **Keluaran** `--output-dir/poa_cross_calibration_<mulai>_<akhir>.xlsx`:
   - sheet `Bulanan`, `ProfilJam`, `Penghalang`, `Relatif`, `Absolut`, `Larik` (kosong bila tanpa `--m2f-dir`), `Kesepakatan`, `Catatan`;
   - `Catatan` berisi rentang, sampel stabil per stasiun cuaca, sampel galat dibuang, ambang, dan offset POA.
   - PNG: gain bulanan per stasiun cuaca, dan profil jam per stasiun cuaca.
   - Usulan YAML dicetak ke layar dengan label "BELUM diterapkan":
     - `pyranometer.ws_gain` untuk stasiun berstatus `usulan_koreksi`;
     - `pyranometer.ws_jam_penghalang` dari `Penghalang`.
   - Kedua kunci itu belum dibaca loader. Config tidak ditulis.

## Uji (`tests/unit/test_poa_kalibrasi_silang.py`, sintetis)

- **Gain yang ditanam kembali.** Lima stasiun cuaca dari kurva langit cerah sintetis yang sama, dengan gain 1,0 / 0,8 / 1,05 / 1,0 / 1,0. `rasio_ke_median` + `gain_bulanan` mengembalikan 0,8 dan 1,05 (±1 %).
- **Penghalang.**
  - Penurunan 40 % pukul 11–12 di satu stasiun selama 3 bulan → `penghalang` menandai (stasiun itu, 11) dan `gain_relatif` stasiun itu ~1,0.
  - Penurunan yang sama hanya 1 bulan → tidak ditandai.
- **Awan.** Lonjakan di satu stasiun pada satu sampel → sampel itu dan tetangganya tidak stabil.
- **Data tipis.** Bulan dengan < 200 sampel stabil → median NaN, alasan "data tipis".
- **Bergeser.** Gain bulanan 1,0 lalu 0,9 → `bergeser` benar.
- **Acuan lain.**
  - `gain_absolut`: POA = 0,8 × langit cerah → 0,8; sampel dengan rasio < `kt_min` tidak dihitung.
  - `gain_larik`: rasio WB sintetis = 0,9 ÷ gain_WS → mengembalikan gain_WS yang ditanam.
- **`sepakati`:**
  - tiga acuan sepakat → `usulan_koreksi`, `usulan` = 1 ÷ gain;
  - dua sepakat dan satu menyimpang → `usulan_koreksi`;
  - semua berselisih > 3 % → `perlu_lapangan`;
  - sepakat tetapi `bergeser` → `perlu_lapangan`;
  - `larik` None → hanya dua acuan.
- **CLI.** POA dan langit cerah sintetis (monkeypatch loader dan estimator), tanpa `--m2f-dir`. Delapan sheet ada, sheet `Larik` kosong, dan config tidak berubah.

## Langkah sesudah laporan (di luar implementasi ini)

1. **Jalankan pada 2025-01..2026-07** dengan `--m2f-dir`, lalu catat `Kesepakatan` dan `Penghalang` di bagian "Hasil".
2. **Pengguna memutuskan:**
   - menerapkan `ws_gain`/`ws_jam_penghalang` (spesifikasi baru: loader membaca dan menerapkan, berikut dampaknya ke M2a/M2f); dan/atau
   - pemeriksaan lapangan stasiun cuaca berstatus `perlu_lapangan`, termasuk penghalang WS-1.
3. **Sesudah koreksi diterapkan:** ulangi kalibrasi derate (`run_derate_calibration.py`).

## Hasil

### Run pertama (1 Okt 2026)

`python run_poa_cross_calibration.py --raw-root "F:/Downloads part 2" --m2f-dir "F:/Downloads part 2/cek pv/m2f"` → `coba/poa_cross_calibration_20250101_20260731.xlsx`.

**Kesepakatan (keluaran alat, BELUM layak diterapkan; lihat catatan):**

| WS | gain_rel | gain_abs (dinormalkan) | gain_larik | status alat | usulan |
|---|---|---|---|---|---|
| WS-1 | 0,951 | 0,968 | 0,993 | usulan_koreksi | 1,033 |
| WS-2 | 1,017 | 1,034 | **1,205** | usulan_koreksi (rel+abs) | 0,975 |
| WS-3 | 0,958 | 0,959 | 0,977 | usulan_koreksi | 1,043 |
| WS-4 | 1,001 | 1,000 | 0,980 | usulan_koreksi | 1,000 |
| WS-5 | 1,039 | 1,011 | 1,008 | usulan_koreksi | 0,990 |

- **Penghalang:** tidak ada. Profil jam 11 WS-1 sesekali turun (Mei 2025 0,87; Mei 2026 0,89; Jun 2025 0,93), tetapi tidak 3 bulan.
- **Catatan run:**
  - sampel stabil per WS hanya WS-1 1.460, WS-2 2.273, WS-3 1.388, WS-4 2.851, WS-5 2.156 (±5 % sampel 09–15 dalam 19 bulan);
  - sampel galat 3;
  - offset POA 5 menit;
  - acuan larik: 42 workbook Jun–Jul 2026 (n 42–126 WB-hari per WS).
- **Gain bulanan:** hanya **2026-01** yang memenuhi ≥ 200 sampel per WS (0,963 / 1,022 / 0,949 / 1,006 / 1,054). Bulan lain "data tipis" (9–14 bulan per WS).

**Pemeriksaan kewajaran — usulan TIDAK layak diterapkan:**
1. **Data bulanan tidak cukup.** Satu bulan sah membuat `ayunan_bulanan` = 0, sehingga `bergeser` = False. Pemeriksaan pergeseran sebenarnya tidak bisa dilakukan, padahal alat tetap mengusulkan. **Cacat logika:** bila < 2 bulan sah, status semestinya `perlu_lapangan` ("data bulanan kurang"), bukan `usulan_koreksi`.
2. **Kesepakatan berantai.** WS-1 disebut "abs, larik, rel" sepakat, padahal rel–larik berselisih 0,042 > 0,03. Gabungan pasangan membuat ketiganya dihitung. **Cacat logika:** semestinya hanya acuan yang SALING dalam toleransi yang dihitung.
3. **Kriteria stabil sangat ketat untuk langit IKN.** Stabil di sampel dan kedua tetangganya, di ≥ 3 WS sekaligus, hanya terpenuhi ±5 % waktu. Ambang tidak diubah demi hasil; pelonggaran adalah keputusan pengguna.

**Temuan yang tetap kuat:**
- **WB05/WB07 (WS-2) ~20 % di bawah armada, bukan karena sensor WS-2.** Dua acuan sensor sepakat WS-2 hampir tanpa bias (1,017 / 1,034), sedangkan acuan larik 1,205. Jadi `rasio_bersih` rendah WB05/07 di kalibrasi derate (0,74–0,76) berasal dari larik atau model (kWp/jumlah string di config, debu, ketersediaan, dan lain-lain), bukan sensor. Perlu diselidiki tersendiri.
- **WS-1 hanya ~5 % di bawah stasiun lain pada sampel stabil** (0,951 relatif, 0,968 absolut). Kt WS-1 yang rendah di Jun–Jul (rata-rata 0,56 vs 0,68–0,79) jadi bukan bias amplitudo sederhana. Kemungkinannya perilaku saat langit tak stabil, celah data di dalam hari, atau penghalang sesekali.

**Usulan YAML dari alat (BELUM diterapkan; JANGAN diterapkan sebelum kedua cacat logika diperbaiki dan data bulanan cukup):** `pyranometer.ws_gain` WS-1 1,033; WS-2 0,975; WS-3 1,043; WS-4 1,000; WS-5 0,990.

## Di luar cakupan

- Membaca atau menerapkan `ws_gain` di `PyranometerLoader`, M2a, atau M2f.
- Koreksi tilt/azimut sensor; memindahkan sensor.
- POA belakang (bifacial).
