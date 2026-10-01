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
| Kriteria stabil CLI (2 Okt 2026) | Dilonggarkan dari 2 % / 200 sampel per bulan ke 3 % / 100 (syarat tetangga tetap). Default fungsi pustaka tidak berubah; ambang lama tersedia lewat `--toleransi 0.02 --min-sampel 200`. Dasar: uji kepekaan di bagian Hasil |

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
  - Per stasiun cuaca: median rasio selama rentang tanpa jam penghalang, dengan kolom `ws, gain, n, n_bulan_sah, ayunan_bulanan, bergeser`.
  - `ayunan_bulanan` = maks − min dari median bulanan yang sah; NaN bila `n_bulan_sah` < 2, karena satu bulan memberi ayunan 0 yang menipu. `bergeser` = ayunan > `ambang_geser`.
- **`gain_absolut(poa: pd.DataFrame, poa_cerah: pd.Series, stabil: pd.DataFrame, *, kt_min=0.75) -> pd.DataFrame`**: per stasiun cuaca, median POA_WS ÷ POA langit cerah pada sampel stabil saat MEDIAN Kt seluruh WS ≥ `kt_min`. Kecerahan dinilai dari median semua WS, bukan dari rasio WS itu sendiri, supaya sensor yang membaca jauh terlalu rendah tidak tersaring keluar; perbaikan saat rencana ditulis, 1 Okt 2026. Kolom `ws, gain, n`.
- **`gain_larik(kalibrasi_harian: pd.DataFrame, wb_to_ws: dict) -> pd.DataFrame`**
  - `kalibrasi_harian` berkolom `date, wb_id, measured_ratio`.
  - Per hari: median situs ÷ rasio WB. Per stasiun cuaca: median atas WB dan hari yang dipetakan. Kolom `ws, gain, n`.
  - Bila larik setara, nilai ini sebanding dengan gain sensornya: POA tinggi → harapan tinggi → rasio rendah.
- **`sepakati(rel: pd.DataFrame, absolut: pd.DataFrame, larik: pd.DataFrame | None, *, tol=0.03, min_bulan=2) -> pd.DataFrame`**
  - Per stasiun cuaca, kolom `ws, gain_rel, gain_abs, gain_larik, n_bulan_sah, bergeser, status, usulan, alasan`.
  - Untuk perbandingan, `gain_abs` dinormalkan ke median `gain_abs` semua stasiun, karena langit cerah pvlib punya bias bersama (kekeruhan, albedo).
  - Acuan yang sepakat = himpunan terbesar yang SEMUA pasangannya dalam ±`tol`; bila seri, yang sebarannya terkecil. Pasangan tidak digabung berantai: rel–abs dan abs–larik yang masing-masing lolos tidak membuat rel–larik sepakat (perbaikan 1 Okt 2026).
  - `usulan_koreksi`: `n_bulan_sah` ≥ `min_bulan`, ≥ 2 acuan saling sepakat, dan tidak `bergeser`. `usulan` = 1 ÷ median acuan yang sepakat, yaitu faktor pengali POA.
  - Selain itu `perlu_lapangan`, dengan alasan diperiksa berurutan: data bulanan kurang (perbaikan 1 Okt 2026), acuan berselisih, atau gain bergeser antarbulan.

### `run_poa_cross_calibration.py` (CLI di akar repo, pola `run_derate_calibration.py`)

```
python run_poa_cross_calibration.py --raw-root "F:/Downloads part 2" \
    [--mulai 2025-01-01] [--akhir 2026-07-31] [--m2f-dir "F:/Downloads part 2/cek pv/m2f"] \
    [--toleransi 0.03] [--min-sampel 100] \
    [--geometry config/site_geometry.yaml] [--output-dir coba]
```

`--toleransi` diteruskan ke `sampel_stabil`, `--min-sampel` ke `gain_bulanan` dan `gain_relatif`; keduanya dicatat di sheet `Catatan`.

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
- **Bergeser.** Gain bulanan 1,0 lalu 0,9 → `bergeser` benar. Hanya satu bulan sah → `n_bulan_sah` 1 dan `ayunan_bulanan` NaN.
- **Acuan lain.**
  - `gain_absolut`: POA = 0,8 × langit cerah → 0,8; sampel dengan rasio < `kt_min` tidak dihitung.
  - `gain_larik`: rasio WB sintetis = 0,9 ÷ gain_WS → mengembalikan gain_WS yang ditanam.
- **`sepakati`:**
  - tiga acuan sepakat → `usulan_koreksi`, `usulan` = 1 ÷ gain;
  - dua sepakat dan satu menyimpang → `usulan_koreksi`;
  - semua berselisih > 3 % → `perlu_lapangan`;
  - sepakat tetapi `bergeser` → `perlu_lapangan`;
  - `larik` None → hanya dua acuan;
  - sepakat tetapi hanya 1 bulan sah → `perlu_lapangan`, "data bulanan kurang";
  - kasus WS-1 run pertama (rel 0,951, abs 0,968, larik 0,993) → hanya (abs, rel) yang sepakat, `usulan` = 1 ÷ 0,9595.
- **CLI.** POA dan langit cerah sintetis (monkeypatch loader dan estimator), tanpa `--m2f-dir`. Delapan sheet ada, sheet `Larik` kosong, dan config tidak berubah.
- **CLI, kriteria stabil.** Dua hari (~146 sampel stabil): median NaN pada `--min-sampel 200`, sah pada default 100. Riak ~2,5 % antar-sampel: tak stabil pada `--toleransi 0.02`, stabil pada default 3 %. `Catatan` memuat ambang yang dipakai.

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

**Temuan run pertama (dua butir di bawah DIKOREKSI 2 Okt 2026, lihat "Koreksi: WB05/WB07 dan sensor yang berubah"):**
- ~~**WB05/WB07 (WS-2) ~20 % di bawah armada, bukan karena sensor WS-2.**~~ SALAH. Gain 1,017 / 1,034 adalah median sampel stabil 2025-01..2026-07 yang didominasi periode SEBELUM WS-2 kosong; perubahan sensor sesudah Jun 2026 tertutup olehnya.
- ~~**WS-1 hanya ~5 % di bawah stasiun lain**~~ berlaku untuk periode gabungan saja; di Jun–Agu 2026 WS-1 ~20 % di bawah.

### Koreksi: WB05/WB07 dan sensor yang berubah (2 Okt 2026)

**Larik WB05/WB07 normal** (42 workbook M2f Jun–Jul 2026, besaran yang tak memakai POA):
- arus string (`M2b_open_circuit_StringStatus`), median relatif armada WB03–10: WB05 0,974 / 0,991 (median / q95), WB07 0,996 / 0,999; WB lain 0,976–1,030. Sebaran per string dan per inverter normal, tak ada inverter yang anjlok;
- tegangan operasi (`M2b_peer_zscore_StringStatus`, 11 hari): WB05 1.105 V, WB07 1.110 V, WB03–10 lain 1.106–1.110 V. Jumlah modul per string setara;
- config sama dengan WB03–10 lain: 26 modul/string, inverter 330 kW. Harapan per string tidak memakai `capacity_kwp_per_wb`.

**WS-2 berubah sesudah kosong.** Energi harian 08–16 tiap WS ÷ median WS lain (hanya sampel saat kelima WS ada):

| bulan | WS-1 | WS-2 | WS-3 | WS-4 | WS-5 |
|---|---|---|---|---|---|
| 2025-01..03 | 0,90–0,92 | 1,01–1,03 | 1,01–1,02 | 0,96–0,98 | 1,03–1,05 |
| 2026-01..02 | 0,95–0,96 | 0,99 | 0,95–0,97 | 1,04 | 1,07–1,08 |
| 2026-06 | 0,76 | **1,14** | 0,99 | 0,96 | 1,05 |
| 2026-07 | 0,79 | **1,12** | 0,99 | 0,97 | 1,04 |
| 2026-08 | 0,80 | **1,08** | 1,05 | 0,96 | 1,02 |

- WS-2 kosong Mar – awal Jun 2026 (hari tanpa data: Mar 31, Apr 30, Mei 31, Jun 9). Sesudah kembali, ia membaca +8..14 % dari stasiun lain; sebelumnya ±2 %. Dugaan: dipasang ulang/diganti dengan orientasi atau kalibrasi berbeda (perlu dicek di lapangan). Per jam, Jun–Jul: +17 % pukul 8, +3..10 % pukul 9–15.
- WS-1 juga turun ke 0,76–0,80 di Jun–Agu 2026 (sebelumnya 0,90–0,96), konsisten dengan penghalang pukul 11–12 (profil 0,48–0,61) dan Kt rendah di kalibrasi derate.
- Suhu sel WS-2 normal (±2 °C dari WS lain pada 1/5/10 Jul): bukan penyebab.

**Uji POA bersama** (`coba/wb0507_poa_bersama_20260701.py`, M2f 1 Jul 2026 dengan satu POA untuk semua WB):

| | WB03/04/06 | WB05/07 | WB08–10 | sebaran WB03–10 |
|---|---|---|---|---|
| POA per WS (asli) | 1,04–1,06 | 0,88–0,90 | 1,03–1,06 | 17 % |
| median 5 WS | 1,01–1,02 | 0,94–0,96 | 0,93–0,95 | 10 % |
| median WS-3..5 | 1,02–1,04 | 0,96–0,97 | 0,94–0,97 | 10 % |

Sebagian besar selisih WB05/07 hilang saat POA disamakan. Sisa ~6 % di hari itu tidak tampak di arus string (1 Jul: WB05/07 1,00 vs WB03/04/06 1,01–1,02); diduga dari masker kalibrasi M2f (run lokal tanpa data set point tidak membuang jam plafon). Belum diselidiki.

**Kesimpulan:**
1. `rasio_bersih` rendah WB05/07 (0,74–0,76) terutama artefak POA WS-2 sesudah Jun 2026, bukan larik, config, debu, atau ketersediaan.
2. Kalibrasi silang atas rentang gabungan tidak sah untuk WS yang berubah di tengah rentang. Gain harus per periode (sebelum/sesudah celah), dan pemeriksaan `bergeser` hanya bekerja bila ada ≥ 2 bulan sah di tiap periode.
3. Harapan M2f Jun 2026 dst. untuk WB05/07 terlalu tinggi ~10–14 % dan untuk WB08–10 terlalu rendah. Hasil M2f/soiling/derate per WB untuk periode itu jangan dibandingkan antar-WS tanpa koreksi.
4. Perlu pemeriksaan lapangan WS-2 (kemiringan, azimut, kebersihan, nomor seri sensor pengganti) dan WS-1 (penghalang).

**Usulan YAML dari alat (BELUM diterapkan; JANGAN diterapkan sebelum kedua cacat logika diperbaiki dan data bulanan cukup):** `pyranometer.ws_gain` WS-1 1,033; WS-2 0,975; WS-3 1,043; WS-4 1,000; WS-5 0,990.

### Run kedua, sesudah kedua cacat diperbaiki (1 Okt 2026, commit `5eb5c83`)

Perintah dan data sama. Nilai gain tidak berubah. Kelima WS kini `perlu_lapangan`, dengan alasan "data bulanan kurang: 1 bulan sah < 2". **Tidak ada usulan `ws_gain` yang dicetak**, dan usulan run pertama gugur.

Acuan yang SALING sepakat, seandainya data bulanan cukup (dihitung tangan dari tabel di atas, tol 0,03):

| WS | sepakat | sebaran | faktor hipotetis |
|---|---|---|---|
| WS-1 | abs, rel (rel–larik 0,042) | 0,017 | 1,042 (run pertama 1,033) |
| WS-2 | abs, rel | 0,017 | 0,975 |
| WS-3 | ketiganya | 0,019 | 1,043 |
| WS-4 | ketiganya | 0,021 | 1,000 |
| WS-5 | abs, larik (rel–larik 0,031) | 0,003 | 0,991 (run pertama 0,990) |

Syarat sebelum ada usulan yang sah: ≥ 2 bulan dengan ≥ 200 sampel stabil per WS. Syarat itu bisa dipenuhi dengan data yang lebih panjang, atau dengan kriteria stabil yang dilonggarkan (keputusan pengguna, butir 3 di atas).

### Pelonggaran kriteria stabil (2 Okt 2026, commit `21cb8fc`)

Pengguna menyetujui pelonggaran. Uji kepekaan 2025-01..2026-07 (pergeseran = |median bulanan − median bulanan kriteria ketat| pada bulan yang sama):

| kriteria | sampel stabil | bulan sah per WS (min 200 / 100) | pergeseran maks / median |
|---|---|---|---|
| 2 % + tetangga (lama) | 10.128 | 1 / 4–6 | – |
| **3 % + tetangga (dipilih)** | 15.413 | 2–5 / **6–9** | **0,008 / 0,002** |
| 5 % + tetangga | 24.483 | 5–8 / 7–11 | 0,033 / 0,003 |
| 2 % tanpa tetangga | 27.280 | 5–8 / 7–11 | 0,021 / 0,004 |
| 3 % tanpa tetangga | 35.234 | 6–10 / 9–13 | 0,039 / 0,007 |

Dipilih yang terlonggar dengan pergeseran ≤ 1 %. Hanya varian terlonggar yang menangkap Jun–Jul 2026 untuk WS-2 (1,117 / 1,091).

**Run ketiga, 2025-01..2026-07, kriteria 3 % / 100:**

| WS | gain_rel | gain_abs | gain_larik | bulan sah | bergeser | status |
|---|---|---|---|---|---|---|
| WS-1 | 0,949 | 0,970 | 0,993 | 7 | ya | perlu_lapangan |
| WS-2 | 1,017 | 1,032 | 1,205 | 6 | tidak | usulan_koreksi 0,976 (abs, rel) |
| WS-3 | 0,960 | 0,961 | 0,977 | 5 | ya | perlu_lapangan |
| WS-4 | 1,005 | 1,000 | 0,980 | 9 | ya | perlu_lapangan |
| WS-5 | 1,039 | 1,014 | 1,008 | 9 | ya | perlu_lapangan |

- Pemeriksaan `bergeser` kini bekerja: empat WS berayun > 5 % antar-bulan, jadi satu faktor per WS tidak sah untuk rentang ini.
- **Usulan WS-2 0,976 JANGAN diterapkan.** Keenam bulan sahnya berasal dari periode SEBELUM sensor berubah (lihat "Koreksi"); acuan larik Jun–Jul (1,205) sudah menunjukkan selisihnya. Alat belum tahu tentang periode: ia memperlakukan rentang sebagai satu sensor yang sama.

**Run periode sesudah celah, 2026-06-10..2026-08-31, kriteria 3 % / 100:**
- sampel stabil per WS per bulan: Jun 21–47, Jul 23–39, Agu 101–141. Hanya Agustus yang sah, sehingga kelima WS `perlu_lapangan` ("data bulanan kurang");
- gain sampel stabil: WS-2 rel 1,066 / abs 1,061 (sebelum celah 1,017 / 1,032), WS-1 0,969 / 0,975, WS-3 0,981 / 1,000, WS-4 0,979 / 0,994, WS-5 1,003 / 1,002.

**Kesimpulan pelonggaran:**
1. Pelonggaran bekerja secara mekanis: rentang gabungan kini punya 5–9 bulan sah per WS.
2. Untuk periode yang penting (WS-2 sesudah Jun 2026), sampel stabil tetap terlalu sedikit; Jun–Jul hampir tanpa langit stabil. Usulan sah untuk periode itu butuh data Sep 2026 dst. atau pemeriksaan lapangan WS-2.
3. Kalibrasi per periode (dipisah di celah data panjang) belum ada di alat. Saat ini caranya menjalankan CLI dengan `--mulai`/`--akhir` per periode.

## Di luar cakupan

- Membaca atau menerapkan `ws_gain` di `PyranometerLoader`, M2a, atau M2f.
- Koreksi tilt/azimut sensor; memindahkan sensor.
- POA belakang (bifacial).
