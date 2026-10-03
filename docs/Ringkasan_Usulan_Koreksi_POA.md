# Ringkasan Usulan Koreksi Sensor POA

**Tanggal:** 2 Oktober 2026 (status diperbarui 3 Oktober 2026) · **Untuk:** pemilik dokumen kalibrasi POA PLTS-IKN ·
**Status:** bahan keputusan; belum ada koreksi yang diterapkan (loader sudah membaca blok `pyranometer.koreksi` sejak
3 Okt 2026, tetapi config belum memuatnya). Draf blok: `docs/Draf_Blok_Koreksi_POA.md`.

**Dasar:** `run_poa_cross_calibration.py`. Tahun 2025 dengan pembanding WS-3/4/5; tahun 2026 dengan pembanding WS-4/5
dan batas kandidat (WS-3 dan WS-2: 11 Agu 2026; WS-5: 11 Jan 2026). **Faktor** = pengali bacaan POA (1 ÷ gain sensor).
Versi Word: `docs/Ringkasan_Usulan_Koreksi_POA.docx`.

| WS | Periode | Faktor | Keyakinan | Syarat sebelum diterapkan |
|---|---|---|---|---|
| WS-3 | 1 Jan – 10 Agu 2026 | **1,060** | **Kokoh** (sama di semua run dan kedua pembanding) | Log O&M mengonfirmasi pembersihan/perbaikan sekitar 10–11 Agu 2026 |
| WS-3 | 11 – 31 Agu 2026 | tanpa koreksi | Data kurang (1 bulan; rel 0,998) | Data Sep 2026 |
| WS-5 | 11 Jan – 31 Agu 2026 | 0,993–0,996 → **tanpa koreksi** | **Kokoh** | Konfirmasi pekerjaan sekitar 11 Jan 2026 |
| WS-5 | 1 – 10 Jan 2026 | indikasi ~1,12 | Data kurang | Konfirmasi yang sama, atau 10 hari ini dibuang |
| WS-2 | 15 Jan – 31 Des 2025 | 0,990 | Sedang (4 bulan sah; versi lintas tahun 0,983) | Tidak mendesak (koreksi 1–2 %) |
| WS-2 | 1 Jan – 28 Feb 2026 | — | Acuan berselisih (rel 0,984; abs 0,916) | — |
| WS-2 | 10 Jun – 10 Agu 2026 | indikasi ~0,90 | Data kurang; sensor diduga miring ~26° | Pemeriksaan lapangan WS-2, lalu ≥ 2 bulan data |
| WS-2 | 11 – 31 Agu 2026 | indikasi ~0,95 | Data kurang | Sama |
| WS-4 | 2026 | 1,000–1,021 | **Peka acuan** (1,000 vs WS-3/4/5; 1,021 vs WS-5 saja) | Sertifikat kalibrasi WS-4 dan WS-5 |
| WS-4, WS-5 | 2025 | — | Data kurang (WS-3/WS-5 sering kosong) | — |
| WS-1 | semua | **jangan dipakai** | Penghalang pukul 11–12 sejak Jun 2026, bacaan nol saat cerah Agu 2025 – Mei 2026, banyak kandidat lompatan | Perbaikan lapangan |

**Keputusan yang diminta**

1. **Pembanding per tahun:** 2025 WS-3/4/5, 2026 WS-4/5. *Rekomendasi: setuju.*
2. **Tanggal batas** (WS-3 dan WS-2: 11 Agu 2026; WS-5: 11 Jan 2026): terima sekarang atau tunggu log O&M. *Rekomendasi:
   tunggu log; tidak menghambat karena koreksi belum diterapkan.*
3. **WS-1** dikeluarkan dari kalibrasi dan pembanding sampai diperbaiki. *Rekomendasi: ya.*
4. **Yang diterapkan lebih dulu:** hanya WS-3 1,060 (1 Jan – 10 Agu 2026), sesudah log O&M mengonfirmasi. WS-5 tidak
   perlu koreksi; yang lain menunggu lapangan, data, atau sertifikat.

**Catatan:** loader sudah bisa menerapkan koreksi (spesifikasi 2026-10-02-penerapan-koreksi-poa-loader); sesudah blok
masuk config, M2f dan kalibrasi derate diulang. Kandidat lompatan
WS-4 pada 11 Jan 2026 (−7,7 %) adalah cermin lompatan WS-5 (acuan WS-4 hanya WS-5), bukan perubahan WS-4. Rincian:
`docs/superpowers/specs/2026-10-02-kalibrasi-silang-poa-pembanding-titik-ubah-mutu-design.md`. Grafik rasio harian per
WS: berkas `…_harian.png` di folder keluaran tiap run.
