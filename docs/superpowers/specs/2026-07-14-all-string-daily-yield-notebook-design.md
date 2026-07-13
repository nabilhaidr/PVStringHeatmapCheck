# All-String Daily Yield Notebook Design

**Tanggal:** 2026-07-14
**Status:** Disetujui untuk perencanaan implementasi
**Lokasi output:** `output_string/`

## Tujuan

Menyediakan notebook Colab terpisah yang mengunduh hanya CSV harian dalam
rentang tanggal inklusif, menghitung yield harian seluruh PV string yang
terdeteksi, lalu menghasilkan satu workbook Excel yang dapat langsung diunduh.

Notebook baru mengikuti pola operasional
`output_string/String_Yield_Power_Irradiance.ipynb`, tetapi tidak memerlukan
pilihan satu string, data POA, grafik, atau ekspor data 5-menit.

## Keputusan desain

Implementasi memakai pipeline power-only khusus all-string. Pipeline ini
memakai ulang kontrak validasi tanggal, inventaris Google Drive, dan download
selektif yang sudah teruji, tanpa membuat `build_report_data()` memiliki mode
bersyarat all-string.

Alternatif yang ditolak:

1. Memanggil laporan single-string berulang untuk setiap string. Pendekatan ini
   membaca CSV dan POA berkali-kali serta melakukan pekerjaan yang tidak
   dibutuhkan.
2. Menambahkan `selection=None` ke pipeline single-string. Pendekatan ini
   mencampur kontrak POA, grafik, nama workbook, dan bentuk data 5-menit dengan
   laporan yang berbeda.

## Ruang lingkup

### Termasuk

- Konfigurasi URL folder CSV dan rentang tanggal.
- Inventaris folder Drive publik dengan `gdown` JSON.
- Download hanya file `YYYYMMDD.csv` yang diminta.
- Ekstraksi power seluruh string dari setiap CSV yang dibaca satu kali.
- Perhitungan yield harian teramati tanpa imputasi.
- Rekap matriks tanggal x string dan detail audit per string-hari.
- Workbook Excel tiga sheet, verifikasi setelah disimpan, dan download Colab.
- Unit test serta smoke test notebook offline.

### Tidak termasuk

- Irradiance/POA dan folder Raw Data Input.
- Grafik notebook atau grafik Excel.
- Data power 5-menit di workbook.
- Imputasi, interpolasi, estimasi, atau ekstrapolasi power yang hilang.
- Inventaris string dari konfigurasi plant di luar CSV yang dipilih.
- Perubahan perilaku notebook single-string yang sudah ada.

## Artefak yang direncanakan

| File | Tanggung jawab |
| --- | --- |
| `pv_pipeline/all_string_yield_report.py` | Download CSV-only, ekstraksi seluruh string, agregasi harian, dan workbook |
| `tests/unit/test_all_string_yield_report.py` | Kontrak unit dan workbook all-string |
| `output_string/_build_all_string_yield_notebook.py` | Builder deterministik notebook nbformat 4.5 |
| `output_string/All_String_Daily_Yield.ipynb` | Notebook yang dijalankan pengguna di Colab |
| `output_string/_smoke_all_string_yield_notebook.py` | Eksekusi offline cell notebook dengan input sintetis |

Helper umum yang sudah ada di `pv_pipeline/string_yield_report.py` dapat dipakai
ulang. Kontrak single-string tidak diubah kecuali perubahan kecil benar-benar
diperlukan untuk berbagi fungsi yang sudah ada.

## Konfigurasi notebook

Cell konfigurasi hanya berisi tiga nilai yang diedit pengguna:

```python
URL_CSV = "https://drive.google.com/drive/folders/1f_KrPuqfZJTE5I9cVQiyp65QrHbkF3Iw?usp=sharing"
START_DATE = "2026-05-01"
END_DATE = "2026-05-14"
```

Aturan validasi:

- `URL_CSV` harus berupa URL folder publik `https://drive.google.com/drive/folders/...`.
- `START_DATE` dan `END_DATE` harus memakai format `yyyy-mm-dd`.
- Rentang tanggal bersifat inklusif dan `START_DATE <= END_DATE`.
- Notebook memakai zona waktu data sebagaimana tersimpan di CSV. Timestamp
  naive diperlakukan sebagai waktu lokal WITA tanpa konversi zona waktu.

## Inventaris dan download selektif

1. Notebook menginventarisasi satu folder `URL_CSV` menggunakan `gdown>=6.0.0`
   dengan keluaran JSON.
2. Nama file dicocokkan berdasarkan basename yang persis sama dengan
   `YYYYMMDD.csv` untuk setiap tanggal yang diminta.
3. Duplikat basename yang diminta dianggap ambigu dan menggagalkan proses.
4. File yang tidak termasuk rentang tanggal tidak diunduh.
5. File terpilih disimpan ke direktori sementara per run; workbook tetap
   disimpan ke `output_string/` pada root repo.
6. Query URL Drive tidak disimpan ke metadata workbook. Hanya URL folder
   canonical tanpa query yang disimpan.

Tanggal tanpa file dan kegagalan download dicatat. Proses masih boleh membuat
workbook jika sedikitnya satu CSV dapat dibaca dan sedikitnya satu string dapat
dideteksi.

## Definisi semua string

"Semua string" adalah gabungan pasangan inverter dan nomor PV yang memiliki
sedikitnya satu sampel power numerik valid pada salah satu CSV terbaca dalam
rentang tanggal.

- Identitas inverter diambil dari `Inverter_ID` secara case-insensitive.
- Jika `Inverter_ID` tidak tersedia, `ManageObject` dinormalisasi melalui helper
  proyek yang sudah ada.
- Identitas canonical berbentuk `WBxx-INVxx-PVn`.
- Nomor PV dibatasi pada `1..28`, sama dengan laporan single-string.
- String diurutkan alami berdasarkan nomor WB, inverter, lalu PV; bukan urutan
  leksikografis.
- String yang tidak pernah memiliki satu pun sampel valid sepanjang periode
  tidak dimasukkan karena tidak dapat dibedakan dari string yang tidak terpasang.
- Setelah union ditemukan, setiap string memperoleh satu baris detail untuk
  setiap tanggal yang diminta, termasuk tanggal sebelum atau sesudah kemunculan
  pertamanya.

## Ekstraksi power

Setiap CSV dibaca sekali. Data 5-menit dari satu file langsung diringkas menjadi
statistik harian per string sebelum file berikutnya dibaca; frame 5-menit lintas
hari tidak digabung atau dipertahankan di memori. Ekstraksi mempertahankan
semantik single-string:

1. Kolom waktu dicari sebagai `Start Time` secara case-insensitive.
2. Baris bertanggal selain tanggal yang dinyatakan oleh nama file dibuang dan
   jumlahnya dicatat.
3. Kolom `PVn Power(kW)` diprioritaskan bila tersedia.
4. Jika kolom power langsung untuk PV tersebut tidak tersedia, power dihitung
   dari pasangan `PVn Voltage(V) * PVn Current(A) / 1000`.
5. Jika kolom power langsung tersedia tetapi nilainya kosong, pipeline tidak
   menggantinya dengan V x I. Ini mempertahankan sumber data yang eksplisit.
6. Timestamp invalid dibuang.
7. Sampel duplikat untuk string dan timestamp yang sama dirata-ratakan.
8. Power negatif dipertahankan, dihitung dalam yield, dan dilaporkan sebagai
   peringatan.
9. Timestamp tidak dibulatkan ke grid. Hanya sampel tepat pada slot 5-menit yang
   masuk ke coverage dan integrasi.
10. Nilai power `inf`/`-inf`, termasuk overflow V x I, dibuang dan jumlahnya
    dicatat sebagai sampel non-finite.

## Perhitungan yield harian

Untuk setiap tanggal dan string digunakan grid `00:00` sampai `23:55` dengan
interval lima menit dan 288 slot yang diharapkan.

```text
string_yield_kwh = sum(power_kw_valid * 5/60)
```

- Sampel hilang tetap hilang.
- Yield parsial adalah yield teramati, bukan estimasi yield satu hari penuh.
- Nilai nol yang benar-benar teramati tetap ditulis sebagai angka `0`.
- Yield kosong ditulis sebagai sel kosong, bukan angka nol.

Status detail:

| Status | Arti |
| --- | --- |
| `COMPLETE` | Tepat 288 slot power valid |
| `PARTIAL` | Terdapat 1 sampai 287 slot power valid |
| `NO_STRING_DATA` | CSV valid tersedia, tetapi string tidak memiliki sampel valid |
| `MISSING_CSV` | File tidak ditemukan atau gagal diunduh |
| `CSV_READ_ERROR` | File lokal tersedia tetapi tidak dapat dibaca/divalidasi |

## Model data hasil

### Detail harian

Urutan kolom bersifat kontraktual:

1. `date`
2. `pv_string`
3. `inverter_id`
4. `pv_label`
5. `string_yield_kwh`
6. `valid_power_samples`
7. `expected_samples`
8. `coverage_pct`
9. `missing_power_samples`
10. `source_csv`
11. `status`

Setiap kombinasi tanggal x string muncul tepat satu kali.

### Rekap lebar

Rekap lebar dibentuk dari detail harian:

- Kolom pertama `date`.
- Kolom berikutnya satu kolom per `pv_string` canonical.
- Nilai adalah `string_yield_kwh`.
- `PARTIAL` tetap memiliki nilai yield teramati.
- `NO_STRING_DATA`, `MISSING_CSV`, dan `CSV_READ_ERROR` menghasilkan sel kosong.

## Workbook Excel

Nama file deterministik:

```text
output_string/all_string_yield_<yyyymmdd>_<yyyymmdd>.xlsx
```

Urutan sheet:

1. **`Rekap_Yield_kWh`**
   - Matriks tanggal x string.
   - Freeze pane pada header dan tanggal.
   - Filter aktif.
   - Format tanggal `yyyy-mm-dd` dan yield `0.000`.
2. **`Detail_Harian`**
   - Data panjang sesuai kontrak kolom.
   - Freeze pane, filter, serta format tanggal, yield, dan coverage numerik.
3. **`Metadata`**
   - Konfigurasi canonical, tanggal pembuatan WITA, rumus, interval, jumlah
      string, daftar file terbaca, tanggal/file hilang, download/read errors,
      wrong-date rows, duplikat, power negatif/non-finite, sumber power, dan
      peringatan.
   - Nilai panjang dipecah menjadi cell maksimum 30.000 karakter agar tidak
     terpotong oleh batas Excel; query dan fragment URL Google Drive dihapus
     dari diagnostik sebelum ditulis.

Workbook mula-mula ditulis ke file sementara. Verifikasi memastikan file tidak
kosong, urutan dan nama sheet tepat, header sesuai kontrak, tanggal rekap tepat
sama dengan rentang inklusif, detail tepat sama dengan produk Cartesian
`tanggal x string`, metadata tersedia, dan tidak ada cell metadata melampaui
batas Excel. Hanya workbook yang lolos verifikasi yang dipindahkan secara atomik
ke nama output final; cell download menolak file tanpa sentinel verifikasi.

Excel membatasi satu sheet pada 1.048.576 baris dan 16.384 kolom. Setelah union
string diketahui, pipeline menghitung ukuran `Detail_Harian` dan
`Rekap_Yield_kWh` sebelum membentuk produk tanggal x string. Jika batas akan
terlampaui, proses gagal dengan jumlah baris/kolom serta maksimum hari yang
diizinkan untuk jumlah string terdeteksi; pengguna harus memperpendek rentang.

## Susunan notebook

Notebook terdiri dari satu cell markdown dan enam cell kode:

1. **Pendahuluan:** tujuan, rumus, input, output, dan urutan eksekusi.
2. **Setup:** install/upgrade `gdown`, temukan atau clone repo publik, siapkan
   `output_string/` dan direktori input sementara.
3. **Konfigurasi:** tiga nilai input dan validasi.
4. **Inventaris + download:** tampilkan jumlah inventory, file terpilih,
   berhasil, hilang, dan error.
5. **Proses:** hitung union string dan detail/rekap; tampilkan ringkasan status
   dan preview rekap.
6. **Ekspor + verifikasi:** tulis workbook, buka ulang, tampilkan path, sheet,
   ukuran, jumlah tanggal, dan jumlah string.
7. **Download Colab:** panggil `google.colab.files.download(...)`; di Jupyter
   lokal hanya tampilkan path.

Cell gagal dengan pesan eksplisit bila cell prasyarat belum dijalankan.

## Penanganan kegagalan

- URL atau tanggal tidak valid: gagal sebelum inventaris.
- Inventory folder gagal: gagal tanpa membuat workbook parsial yang tidak dapat
  diaudit.
- Sebagian file hilang/gagal download/gagal dibaca: lanjut jika union string
  masih dapat dibangun; status dan metadata mencatat kekurangan tersebut.
- Tidak ada CSV yang dapat dibaca: gagal dengan `RuntimeError`.
- Tidak ada string dengan sampel power valid: gagal dengan `RuntimeError`.
- Produk tanggal x string atau jumlah kolom melampaui batas Excel: gagal sebelum
  materialisasi detail dengan rekomendasi rentang maksimum.
- Workbook gagal diverifikasi: cell ekspor gagal dan cell download tidak boleh
  mengunduh file tersebut.

## Strategi pengujian

Implementasi mengikuti TDD. Tes harus lebih dulu gagal karena perilaku belum
tersedia, kemudian dibuat lulus dengan implementasi minimum.

Unit test mencakup:

- Pemilihan hanya CSV tanggal yang diminta.
- Deteksi beberapa inverter dan PV dengan urutan alami.
- Prioritas power langsung dan fallback V x I.
- Union string lintas tanggal dan tepat satu baris per tanggal x string.
- Rumus yield tanpa imputasi serta status complete/partial/missing/no-data/read-error.
- Timestamp salah tanggal, invalid, dan duplikat.
- Retensi power negatif beserta metadata peringatan.
- Bentuk rekap lebar, sel kosong versus nol, dan kontrak workbook.
- Sanitasi URL/metadata sensitif.
- Builder menghasilkan notebook nbformat 4.5 dengan cell serta default literal
  yang tepat.

Smoke test offline memakai minimal dua string dan dua tanggal, mengganti fungsi
download dengan input sintetis lokal, mengeksekusi cell proses sampai ekspor,
membuka ulang workbook, dan memastikan rerun tidak menambah sheet atau baris.

Verifikasi akhir menjalankan test file baru, smoke notebook, lalu seluruh
`python -m pytest` tanpa menyembunyikan test yang gagal atau dilewati.

## Kriteria penerimaan

- Pengguna hanya perlu mengubah `URL_CSV`, `START_DATE`, dan `END_DATE`.
- Notebook mengunduh hanya CSV dalam rentang yang diminta.
- Setiap CSV terbaca diproses satu kali untuk seluruh string.
- Data 5-menit diringkas per file dan tidak digabung lintas hari di memori.
- Rekap memuat seluruh tanggal dan seluruh string terdeteksi dengan urutan alami.
- Yield parsial tidak dinormalisasi menjadi satu hari penuh.
- Kekurangan data dapat dilacak melalui `Detail_Harian` dan `Metadata`.
- Workbook tersimpan di `output_string/`, lolos verifikasi buka-ulang, dan dapat
  diunduh dari Colab.
- Notebook single-string yang ada dan seluruh test suite tetap lulus.
