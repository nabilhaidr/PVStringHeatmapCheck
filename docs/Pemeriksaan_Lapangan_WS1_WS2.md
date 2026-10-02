# Pemeriksaan Lapangan Sensor POA WS-2 dan WS-1

**Tanggal:** 2026-10-02
**Untuk:** tim O&M / weather station PLTS-IKN
**Versi isian (Word):** `docs/Pemeriksaan_Lapangan_WS1_WS2.docx`, dengan kolom "Hasil", identitas kunjungan, dan kotak
catatan. Isinya harus sama dengan berkas ini.
**Kenapa:** sejak Jun 2026 dua sensor POA (pyranometer bidang modul) membaca berbeda dari stasiun lain. Akibatnya energi
harapan M2f salah: WB05/WB07 (WS-2) tampak ~20 % di bawah armada padahal arus dan tegangan string mereka normal, dan
WB08–WB10 (WS-1) tampak lebih baik dari sebenarnya. Koreksi di kode tidak bisa dipilih dengan aman sebelum penyebab
fisiknya diketahui. Rincian analisa: `docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md`, bagian "Koreksi".

Nilai yang benar menurut config: sensor POA **miring 10° (WS-1 dan WS-4: 9°), menghadap utara (azimut 0°)**, sama
dengan modul.

---

## 1. WS-2 (melayani WB05 dan WB07)

### Yang terlihat di data

- Data WS-2 **kosong Mar – awal Jun 2026** (Mar 31, Apr 30, Mei 31, Jun 9 hari tanpa data).
- Sebelum celah, WS-2 membaca 0,99–1,03 × median stasiun lain. **Sesudah kembali: 1,08–1,14** (Jun 1,14; Jul 1,12;
  Agu 1,08).
- Bentuk hariannya berubah. Rasio WS-2 ÷ median WS-3/4/5 di hari cerah, musim yang sama:

  | pukul | 7 | 8 | 10 | 12 | 14 | 15 | 16 |
  |---|---|---|---|---|---|---|---|
  | Jun–Agu 2025 | 1,15 | 1,08 | 1,02 | 0,99 | 0,98 | 0,92 | 0,87 |
  | Jun–Agu 2026 | 1,26 | 1,20 | 1,09 | 1,06 | 1,04 | 1,07 | 1,12 |

- Model geometri paling cocok: **2025 ≈ miring 4°, 2026 ≈ miring 26° menghadap ~utara, ditambah gain ~+8 %.** Angka
  26° adalah petunjuk, bukan ukuran (kecocokan model sedang).

### Dugaan

Sensor dipasang ulang atau diganti saat celah Mar–Jun 2026, dengan kemiringan lebih curam dan/atau konstanta
sensitivitas yang berbeda di logger.

### Yang diperiksa

| # | Butir | Cara | Catat |
|---|---|---|---|
| 1 | Kemiringan sensor POA | inklinometer/aplikasi di permukaan dudukan sensor | derajat |
| 2 | Arah hadap | kompas di sisi dudukan, arah turunnya bidang | derajat dari utara |
| 3 | Kerataan dudukan (gelembung, bila ada) | lihat waterpass sensor | ya/tidak |
| 4 | Merek, tipe, **nomor seri** sensor | label sensor | teks + foto |
| 5 | Apakah sensor diganti / dipindah | tanya tim O&M, log pekerjaan Mar–Jun 2026 | tanggal, siapa |
| 6 | **Konstanta sensitivitas** di logger (µV per W/m²) | layar/konfigurasi logger | angka; cocokkan dengan sertifikat kalibrasi seri itu |
| 7 | Sertifikat kalibrasi sensor terpasang | dokumen | foto/salinan |
| 8 | Kebersihan kubah, embun, retak | visual | foto |
| 9 | Jam logger vs jam acuan | bandingkan dengan jam HP (waktu jaringan) | selisih menit |
| 10 | Benda yang membayangi pagi/sore | foto ke timur dan barat dari posisi sensor | foto |

Bila memungkinkan, ukur juga butir 1, 2, dan 6 di **satu stasiun pembanding (WS-3 atau WS-4)**. Analisa memakai median
WS-3/4/5 sebagai acuan, jadi acuannya juga perlu dipastikan benar.

---

## 2. WS-1 (melayani WB08, WB09, WB10)

### Yang terlihat di data

- Di hari cerah Jun–Agu 2026 (54 hari), WS-1 **turun ke 0,34–0,45 × stasiun lain antara pukul 11:24 dan 12:49 WITA**.
  Tepinya tajam: 0,80 pada 11:19 lalu 0,39 pada 11:24; 0,39 pada 12:49 lalu 0,73 pada 12:54.
- Di luar jendela itu WS-1 hanya ~3 % di bawah stasiun lain.
- Pada jendela itu matahari bergerak dari **azimut 43° (timur laut), elevasi 60°** ke **azimut 336° (utara-barat laut),
  elevasi 67°**. Sinar langsung tertutup, cahaya baur tetap masuk.
- Energi harian WS-1 Jun–Agu 2026 jadi 0,76–0,80 × stasiun lain (Jan–Feb 2026: 0,95–0,96).
- **Bayangan ini baru muncul Jun 2026.** Profil hari cerah pukul 11–12 bernilai ~1,0 setiap bulan dari Jan 2025 sampai
  Mei 2026, termasuk **Jun 2025** (0,97 / 1,03), saat posisi matahari sama persis dengan Jun 2026. Sejak Jun 2026:
  0,32–0,41.
- Waktunya bersamaan dengan **data WS-1 kosong 1–10 Jun 2026** dan **WS-2 kembali terpasang 10 Jun 2026**.
- Terpisah dari bayangan: WS-1 mencatat **0 W/m² di siang cerah** pada Okt 2025 – Mei 2026 (seluruh sampel cerah
  Nov–Des 2025). Ini sensor/logger mati, bukan bayangan.

### Dugaan

Ada benda **di atas sensor dan sedikit ke arah utara**, lebarnya sekitar 65° azimut dilihat dari sensor, pada
ketinggian sudut ~60–70°. Benda ini sangat dekat dan tinggi. Contohnya lengan tiang, panel surya kecil pemasok logger,
kotak logger, antena, penangkal petir, atau sensor lain. Benda itu (atau sensornya yang dipindah) kemungkinan besar
dipasang saat pekerjaan sekitar **1–10 Jun 2026**, kemungkinan dalam kegiatan yang sama dengan pemasangan ulang WS-2.

### Yang diperiksa

| # | Butir | Cara | Catat |
|---|---|---|---|
| 1 | Benda di atas/utara sensor POA | berdiri di posisi sensor, lihat ke utara-atas | jenis benda + foto |
| 2 | Jarak horizontal dan beda tinggi benda terhadap sensor | meteran | cm |
| 3 | Bukti langsung bayangan | bila sempat, kunjungi pukul 11:30–12:30 di hari cerah; foto bayangan di sensor | foto + jam |
| 4 | Kemiringan dan arah hadap sensor | sama dengan WS-2 butir 1–2 | derajat |
| 5 | Kapan benda itu dipasang; pekerjaan apa di WS-1 pada 1–10 Jun 2026 | tanya tim O&M, log pekerjaan | tanggal, siapa |
| 6 | Kenapa logger mencatat 0 W/m² siang hari Okt 2025 – Mei 2026 | log alarm logger, kabel sensor, catu daya | penyebab, tanggal perbaikan |

---

## 3. Sesudah kunjungan

- Kirim isian tabel dan foto ke tim analitik.
- **Bila kemiringan/arah WS-2 salah:** perbaiki di lapangan. Data Jun 2026 sampai tanggal perbaikan diberi gain per
  periode lewat alat kalibrasi silang (`run_poa_cross_calibration.py`, kalibrasi per periode).
- **Bila konstanta sensitivitas salah:** perbaiki di logger; koreksi data lama adalah satu faktor = konstanta lama ÷
  konstanta benar.
- **Bila penghalang WS-1 ketemu:** pindahkan benda atau sensornya. Data sejak Jun 2026 di jam itu ditandai, bukan
  dikoreksi dengan satu faktor (bayangan bergantung jam dan musim). Alat kalibrasi silang sudah menandai
  `ws_jam_penghalang: WS-1: [11, 12]`.
- Sampai semua ini selesai, hasil M2f, soiling, dan derate sejak Jun 2026 jangan dibandingkan antar-WB yang memakai
  stasiun cuaca berbeda.
