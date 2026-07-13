# Notebook String Yield, Power, dan Irradiance

**Tanggal:** 2026-07-13
**Status:** Disetujui user; siap diimplementasikan
**Lokasi:** `output_string/`

## Tujuan

Membuat notebook Colab terpisah untuk memeriksa satu PV string pada rentang
tanggal yang dapat diubah. Notebook mengunduh hanya file sumber yang dibutuhkan,
menghitung yield string harian dari telemetri power 5-menit, membandingkan kurva
power dengan POA irradiance, menampilkan kedua grafik di notebook, lalu membuat
satu workbook Excel yang dapat diunduh.

## Keputusan pengguna

- Pendekatan akses data: inventaris folder Google Drive publik lalu unduh selektif
  berdasarkan tanggal, bukan mount Drive dan bukan unduh seluruh folder.
- Folder CSV harian:
  `https://drive.google.com/drive/folders/1f_KrPuqfZJTE5I9cVQiyp65QrHbkF3Iw?usp=sharing`.
- Folder raw-data POA:
  `https://drive.google.com/drive/folders/1y37AsVViI7IL1tCRhvAGEjiq9wWnfu_e?usp=drive_link`.
- Pemilihan string memakai satu nilai `WBxx-INVxx-PVxx`.
- Rentang tanggal bersifat inklusif dan memakai format `yyyy-mm-dd`.
- Notebook serta workbook hasil berada di root repo `output_string/`.
- Cell terakhir menyediakan download workbook melalui Colab.
- Missing power tidak diisi, diinterpolasi, atau diekstrapolasi.
- Notebook dan workbook sama-sama menampilkan grafik yield harian per tanggal.
- Notebook dan workbook sama-sama menampilkan grafik power-versus-irradiance
  dengan waktu sebagai sumbu-X dan irradiance pada sumbu-Y sekunder.

## Non-tujuan

- Tidak menjalankan detector M2, PR, SRR, cleaning recommendation, atau baseline.
- Tidak menghitung yield inverter, WB, atau site; hanya satu PV string per run.
- Tidak mengestimasi energi pada slot telemetri yang hilang.
- Tidak memakai Google Drive mount sehingga tidak bergantung pada DriveFS.
- Tidak mengubah file CSV atau POA sumber.
- Tidak membuat dashboard interaktif atau batch report untuk banyak string.

## Deliverables

| File | Aksi |
|---|---|
| `pv_pipeline/string_yield_report.py` | Baru: fungsi murni dan orkestrasi laporan yang dapat diuji |
| `tests/unit/test_string_yield_report.py` | Baru: unit test kontrak parsing, seleksi, integrasi, status, dan workbook |
| `output_string/_build_string_yield_notebook.py` | Baru: builder notebook nbformat 4.5 |
| `output_string/String_Yield_Power_Irradiance.ipynb` | Baru: notebook Colab untuk pengguna |
| `output_string/_smoke_string_yield_notebook.py` | Baru: smoke test offline dengan data sintetis |
| `.gitignore` | Tambah pola untuk workbook runtime di `output_string/` |

Workbook runtime bernama:

`output_string/string_yield_<WBxx-INVxx>_<PVn>_<yyyymmdd>_<yyyymmdd>.xlsx`

`PVxx` pada input dinormalisasi menjadi `PVn` dalam kolom dan nama file, misalnya
`WB05-INV01-PV03` menjadi inverter `WB05-INV01` dan string `PV3`.

## Cell konfigurasi

Notebook menyediakan satu cell konfigurasi yang hanya perlu diedit pengguna:

```python
URL_CSV = "https://drive.google.com/drive/folders/1f_KrPuqfZJTE5I9cVQiyp65QrHbkF3Iw?usp=sharing"
URL_RAW_DATA_INPUT = "https://drive.google.com/drive/folders/1y37AsVViI7IL1tCRhvAGEjiq9wWnfu_e?usp=drive_link"

PV_STRING = "WB05-INV01-PV03"
START_DATE = "2026-05-01"
END_DATE = "2026-05-14"
```

Validasi konfigurasi:

- `PV_STRING` harus cocok dengan `WB\d{2}-INV\d{2}-PV\d{1,2}` secara
  case-insensitive; PV valid 1..28.
- `START_DATE` dan `END_DATE` harus tanggal ISO yang valid.
- `START_DATE <= END_DATE`.
- Kedua URL harus berupa folder Google Drive.

## Inventaris dan download selektif

Notebook memasang `gdown>=6.0.0`, menginventarisasi folder publik dalam format
JSON, lalu memilih URL file yang nama basenya cocok dengan kebutuhan run.

### CSV string

- File harian diharapkan bernama tepat `YYYYMMDD.csv`.
- Daftar tanggal yang dibutuhkan dibangun dari seluruh tanggal kalender pada
  rentang inklusif.
- Hanya file dengan nama yang cocok tanggal tersebut yang diunduh ke direktori
  temporer Colab.
- File yang tidak ditemukan dicatat, bukan diganti dengan tanggal lain.
- Setelah dibaca, isi `Start Time` tetap divalidasi terhadap tanggal pada nama
  file. Baris di luar tanggal file diberi peringatan dan hanya baris dalam rentang
  konfigurasi yang dipakai.

### POA irradiance

- Tahun yang dibutuhkan diturunkan dari `START_DATE..END_DATE`.
- Untuk setiap tahun hanya file `POA PLTS IKN YYYY.xlsx` yang diunduh.
- Sheet yang dibaca adalah `POA PLTS IKN`.
- Timestamp sumber adalah kolom `Date time` dengan interval nominal 5 menit.
- Jika salah satu file tahun tidak ditemukan, proses tetap dapat menghasilkan
  yield dari CSV, tetapi POA periode tersebut kosong dan status/peringatan wajib
  muncul di notebook dan `Metadata`.

Kegagalan inventaris, permission, atau download seluruh input yang dibutuhkan
ditampilkan dengan nama folder/file dan exception asli. Workbook tidak boleh
ditulis bila tidak ada satu pun CSV yang berhasil dimuat.

## Normalisasi pilihan string dan telemetri

`PV_STRING` dipecah menjadi:

- `wb_id`, contoh `WB05`;
- `inverter_id`, contoh `WB05-INV01`;
- `pv_number`, contoh `3`;
- `pv_label`, contoh `PV3`.

Kontrak CSV:

- Timestamp: `Start Time`.
- Inverter: `Inverter_ID`; bila tidak ada tetapi `ManageObject` tersedia, gunakan
  `pv_pipeline.transformations.add_inverter_id`.
- Power utama: `PVn Power(kW)`.
- Fallback power: `PVn input voltage(V) * PVn input current(A) / 1000`, memakai
  pencocokan case-insensitive agar kolom `Input Voltage/Current` title-case juga
  terbaca.

Hanya baris `Inverter_ID == inverter_id` yang dipakai. Nilai power dikonversi ke
numerik; nilai negatif dipertahankan sebagai bukti sumber dan diberi peringatan,
bukan diam-diam dijadikan nol.

## Grid waktu 5-menit

- `Start Time` diparse sebagai timestamp naive WITA, konsisten dengan loader POA
  repo saat ini.
- Timestamp duplikat untuk string yang sama dirata-ratakan dan jumlah duplikat
  dicatat di `Metadata`.
- Dibuat grid lengkap dari `START_DATE 00:00` sampai `END_DATE 23:55` dengan
  frekuensi 5 menit.
- Power direindex ke grid tanpa fill/interpolasi.
- POA dipilih dari weather station yang dipetakan ke WB melalui
  `config/site_geometry.yaml:ws_to_wb` dan diselaraskan nearest dengan tolerance
  2 menit, mengikuti `PyranometerLoader`.
- Bila POA weather station terpilih kosong pada suatu slot, fallback rata-rata
  lima WS mengikuti perilaku loader repo dan jumlah slot fallback dicatat.

## Perhitungan yield harian

Untuk setiap tanggal:

```text
string_yield_kwh = sum(power_kw_valid * 5/60)
```

Ini adalah Riemann sum pada interval nominal 5 menit dan selaras dengan
`compute_active_power_integration_kwh` di `pv_pipeline.physics`.

Tidak ada estimasi energi untuk slot power yang hilang. Ringkasan berisi seluruh
tanggal kalender dalam rentang, dengan kolom:

| Kolom | Arti |
|---|---|
| `date` | Tanggal kalender |
| `string_yield_kwh` | Yield teramati; kosong bila tidak ada power valid |
| `valid_power_samples` | Jumlah slot power valid |
| `expected_samples` | Selalu 288 untuk satu hari penuh 5-menit |
| `coverage_pct` | `valid_power_samples / 288 * 100` |
| `missing_power_samples` | `288 - valid_power_samples` |
| `poa_valid_samples` | Jumlah slot POA valid |
| `source_csv` | Nama CSV harian atau kosong |
| `status` | Status kelengkapan hari |

Status harian:

- `COMPLETE`: CSV ada dan 288 slot power valid.
- `PARTIAL`: CSV ada dan 1..287 slot power valid; yield adalah yield teramati.
- `NO_STRING_DATA`: CSV ada tetapi string/power terpilih tidak punya slot valid.
- `MISSING_CSV`: CSV tanggal tersebut tidak ditemukan atau gagal dibaca.

Yield `NO_STRING_DATA` dan `MISSING_CSV` disimpan kosong, bukan nol.

## Grafik notebook

Notebook menampilkan dua cell grafik terpisah setelah pengolahan:

1. **Grafik yield harian per tanggal.** Line chart dengan marker; tanggal pada
   sumbu-X dan `string_yield_kwh` pada sumbu-Y. Titik kosong tetap menjadi gap
   sehingga missing day tidak tampak sebagai nol produksi.
2. **Grafik power versus irradiance 5-menit.** Waktu pada sumbu-X, power string
   kW pada sumbu-Y kiri, dan POA irradiance W/m2 pada sumbu-Y kanan. Legenda kedua
   axis digabung dan judul menyertakan string serta rentang tanggal.

Kedua grafik memakai DataFrame yang sama dengan sumber workbook sehingga tidak
ada jalur perhitungan kedua.

## Workbook Excel

Workbook dibuat dengan dependency Python yang sudah menjadi konvensi repo
(`openpyxl`) dan memiliki empat sheet berikut.

### `Ringkasan_Harian`

- Tabel seluruh tanggal dan kolom ringkasan di atas.
- Format tanggal `yyyy-mm-dd`, yield `0.000`, dan coverage `0.0%` secara numerik.
- Freeze header dan autofilter.
- Grafik yield harian per tanggal tertanam di sisi kanan/bawah tabel.

### `Data_5Menit`

- Kolom `timestamp`, `inverter_id`, `pv_string`, `power_kw`, `poa_wm2`,
  `source_csv`, `poa_source`, dan `data_status`.
- Mencakup seluruh grid 5-menit pada rentang inklusif agar gap terlihat sebagai
  sel kosong.
- Freeze header dan autofilter.

### `Grafik`

- Salinan grafik yield harian per tanggal.
- Combo line chart power-versus-irradiance dengan power di axis utama dan POA di
  axis sekunder.
- Data grafik mereferensikan sheet sumber, bukan nilai statis yang diduplikasi.

### `Metadata`

- Nilai konfigurasi, URL sumber, string canonical, tanggal run, rumus yield,
  interval, WB-to-WS mapping, file yang berhasil/yang hilang, jumlah duplikat,
  jumlah fallback POA, dan daftar peringatan.
- Metadata tidak berisi token, cookies, atau credential.

Workbook disimpan langsung ke `output_string/`. Setelah ekspor sukses, notebook
memverifikasi keberadaan keempat sheet dan membuka ulang workbook sebelum cell
download memanggil `google.colab.files.download(...)`.

## Struktur notebook

Notebook terdiri dari satu cell markdown dan delapan cell code:

0. **Markdown:** tujuan, rumus, sumber, output, dan cara menjalankan.
1. **Setup:** temukan repo root, install/upgrade `gdown>=6.0.0`, import dependency,
   dan buat `output_string/`.
2. **Konfigurasi input:** dua URL, `PV_STRING`, `START_DATE`, `END_DATE`.
3. **Inventaris + download selektif:** daftar file, pilihan tanggal/tahun,
   download ke temp, dan laporan missing source.
4. **Load + proses:** parsing string, normalisasi CSV, grid 5-menit, POA, dan
   ringkasan yield harian.
5. **Grafik yield harian:** display line chart yield per tanggal.
6. **Grafik power vs irradiance:** display dual-axis 5-menit.
7. **Ekspor + verifikasi Excel:** tulis empat sheet dan chart, buka ulang, lalu
   cetak lokasi/ukuran/status.
8. **Download Colab:** download file bila `google.colab` tersedia; di Jupyter
   lokal hanya tampilkan path.

Setiap cell prasyarat fail-loud dengan pesan cell mana yang harus dijalankan.

## Error handling dan observability

- Tidak ada `except Exception: pass`.
- Kesalahan konfigurasi menghentikan run sebelum network I/O.
- Inventaris menampilkan jumlah file ditemukan, dipilih, berhasil diunduh, dan
  hilang.
- CSV gagal baca dicatat per tanggal. Run lanjut bila masih ada minimal satu CSV
  valid, dan tanggal gagal diberi status `MISSING_CSV`.
- String tidak ditemukan di semua CSV menghentikan run sebelum workbook ditulis.
- Missing POA tidak menggagalkan yield, tetapi grafik POA memiliki gap dan
  peringatan tersimpan.
- Ringkasan akhir mencetak `requested_days`, `loaded_csv_days`, `missing_csv_days`,
  `complete_days`, `partial_days`, dan output path.

## Pengujian

Implementasi mengikuti TDD. Unit test ditulis dan dijalankan gagal sebelum fungsi
produksi ditambahkan.

### Unit test fungsi murni

- Parsing `WB05-INV01-PV03` menjadi komponen canonical; tolak format dan PV di
  luar 1..28.
- Parsing tanggal dan tolak start setelah end.
- Seleksi inventory hanya `YYYYMMDD.csv` dalam rentang dan file POA tahun terkait.
- Power memakai kolom `PVn Power(kW)` bila tersedia dan fallback V*I bila tidak.
- Filter inverter/string case-insensitive dan normalisasi mixed-case Huawei.
- Deduplikasi timestamp memakai mean.
- Grid 5-menit mempertahankan gap tanpa fill.
- 12 sampel 10 kW menghasilkan 10 kWh sesuai kontrak repo.
- Missing slot menghasilkan yield teramati dan status `PARTIAL`.
- Missing CSV dan no-string-data menghasilkan yield kosong dengan status berbeda.
- POA memakai mapping WB-to-WS dan fallback avg sesuai loader.

### Unit test workbook

- Empat sheet tepat dan berurutan.
- `Ringkasan_Harian` memuat seluruh tanggal dan nilai yield yang diharapkan.
- `Data_5Menit` memuat grid lengkap serta sel kosong untuk gap.
- Workbook memiliki grafik yield harian dan combo power/POA.
- Metadata menyimpan parameter dan daftar missing files tanpa credential.
- Workbook dapat dibuka ulang dengan `openpyxl`.

### Builder dan smoke notebook

- Builder menghasilkan notebook nbformat 4.5 dengan 9 cell sesuai urutan.
- Smoke offline tidak melakukan network call: memakai inventory, CSV, dan POA
  sintetis pada folder temp.
- Smoke menjalankan cell proses, kedua cell grafik dengan backend `Agg`, ekspor,
  dan verifikasi workbook.
- Test memastikan notebook dapat di-run ulang tanpa memakai state output run
  sebelumnya.

## Kriteria selesai

- Pengguna hanya perlu mengubah empat nilai input utama di cell konfigurasi.
- Hanya CSV tanggal yang diminta dan POA tahun terkait yang diunduh.
- Yield harian benar untuk data 5-menit dan tidak mengestimasi missing power.
- Grafik yield harian tampil di notebook dan workbook.
- Grafik power-versus-irradiance dual-axis tampil di notebook dan workbook.
- Workbook berada di `output_string/`, lolos pemeriksaan sheet/chart/nilai, dan
  dapat diunduh dari cell terakhir di Colab.
- Targeted tests dan smoke notebook lulus tanpa network.
