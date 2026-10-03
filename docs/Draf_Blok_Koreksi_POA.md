# Draf Blok `pyranometer.koreksi` — untuk ditinjau

**Tanggal:** 3 Oktober 2026 · **Status:** DRAF. Blok ini **belum** ada di `config/site_geometry.yaml`; semua hasil
masih memakai POA mentah. · **Sumber:** `docs/Ringkasan_Usulan_Koreksi_POA.md` (keputusan 3 dan 4) ·
**Format:** `docs/superpowers/specs/2026-10-02-penerapan-koreksi-poa-loader-design.md` (loader membacanya sejak 3 Okt).

## Blok yang diusulkan

Disalin apa adanya ke bawah kunci `pyranometer:` yang sudah ada (`xlsx_path`, `sheet`, `time_offset_minutes` tidak
berubah), sesudah keputusan:

```yaml
  koreksi:
    aktif: true
    sumber: "docs/Ringkasan_Usulan_Koreksi_POA.md (2 Okt 2026); keputusan <tanggal>"
    ws_faktor_periode:                # faktor = pengali bacaan (1 / gain sensor)
      WS-3:
        - {mulai: 2026-01-01, akhir: 2026-08-10, faktor: 1.060}
    ws_dikecualikan:                  # seluruh bacaan WS dibuang; WB-nya diisi rata-rata WS lain yang terkoreksi
      WS-1:
        - {mulai: 2025-01-12, akhir: null, alasan: "penghalang 11-12 sejak Jun 2026; bacaan nol saat cerah Agu 2025-Mei 2026; tunggu perbaikan lapangan"}
```

| Entri | Dasar | Syarat sebelum `aktif: true` |
|---|---|---|
| WS-3 ×1,060, 1 Jan – 10 Agu 2026 | Kokoh: sama di semua run dan kedua pembanding | Log O&M mengonfirmasi pembersihan/perbaikan WS-3 sekitar 10–11 Agu 2026 (tanggal `akhir`) |
| WS-1 dikecualikan sejak 12 Jan 2025 | "Jangan dipakai" untuk semua periode | Tidak ada; dicabut (isi `akhir`) sesudah perbaikan lapangan |

`mulai` WS-1 = bacaan pertama WS-1 di data (12 Jan 2025), artinya "semua". Bila 2025 ingin dipertahankan, `mulai`
paling lambat **2025-08-26**: bacaan nol pertama saat WS lain cerah jatuh pada tanggal itu (bukan 1 Okt 2025 seperti
contoh di spesifikasi). Pada 2025 bedanya kecil (lihat efek di bawah).

## Validasi atas data nyata

Blok di atas dimuat lewat jalur yang sama dengan pemakai (`run_derate_calibration._muat_poa`, geometri sementara di
luar repo; config tidak diubah), data POA 2025-01 s.d. 2026-08:

- Validasi loader lolos (faktor dalam (0,8; 1,25], tanggal, label WS, tanpa tumpang tindih).
- Sampel tersentuh: WS-3 **31.190**; WS-1 **54.150** (seluruh bacaan WS-1).

Median POA terkoreksi ÷ mentah per WB, pukul 09–15 saat POA mentah > 300 W/m²:

| WB (WS) | 2025 | 1 Jan – 10 Agu 2026 | 11 Agu 2026 – |
|---|---|---|---|
| WB06 (WS-3) | 1,000 | **1,060** | 1,000 |
| WB08–10 (WS-1 → rata-rata WS lain) | 1,000 | **1,035** | **1,070** |
| WB01–02 (WS-5), WB03–04 (WS-4), WB05/07 (WS-2) | 1,000 | 1,000 | 1,000 |
| Pengisi `avg` (dihitung ulang) | 1,000 | 1,024 | 1,021 |

Selain itu, **91 sampel / 48 hari** saat WS-1 membaca **nol** sementara WS lain cerah (> 500 W/m², pukul 10–14;
26 Agu 2025 – 26 Mei 2026) ikut terganti. Bacaan nol bukan data kosong, jadi tanpa pengecualian ia tidak pernah diisi
pengisi.

## Dampak ke pemakai

- **M2f, soiling, shading, low-irradiance, detektor m2b** (`fallback_to_avg` bawaan = `True`): WB08–10 memakai
  rata-rata WS-2..5 terkoreksi; WB06 naik 6 % pada Jan – 10 Agu 2026.
- **Kalibrasi derate dan cek saturasi** (`fallback_to_avg=False`): WB08–10 tidak punya POA → hari-harinya tersaring
  sebagai POA kosong. Itu disengaja: tidak ada sensor yang sah untuk WB itu.
- **Kalibrasi silang dan cek offset waktu:** tetap membaca mentah (diagnostik sensor).
- Catatan/metadata tiap keluaran menyebut `koreksi POA aktif (<sumber>)`.

## Yang sengaja tidak ada di draf

- **WS-5** (kokoh tanpa koreksi), **WS-4** (peka acuan; butuh sertifikat), **WS-2** semua periode (data kurang; tunggu
  pemeriksaan lapangan).
- **Akibatnya:** bias WS-2 sesudah 10 Jun 2026 tetap ada di WB05/07, dan masuk ke pengisi WB08–10 sebesar **+1,6 %**
  (median pengisi ÷ rata-rata WS-3/4/5, 10 Jun – 31 Agu 2026). Bila itu tidak bisa diterima sebelum WS-2 diperiksa,
  pilihannya: tambahkan `WS-2: [{mulai: 2026-06-10, akhir: null}]` ke `ws_dikecualikan` (bukan usulan kokoh;
  keputusan terpisah).
- **WS-5 1 – 10 Jan 2026** (indikasi ~1,12): bisa dibuang lewat `ws_dikecualikan`, menunggu konfirmasi pekerjaan
  11 Jan 2026.
- **`ws_jam_penghalang`** tidak perlu: WS-1 sudah dikecualikan seluruhnya.

## Langkah sesudah keputusan

1. Log O&M mengonfirmasi tanggal WS-3 (10–11 Agu 2026); bila tanggalnya lain, ubah `akhir`.
2. Persetujuan pemilik dokumen → blok ditulis ke `config/site_geometry.yaml` dengan tanggal keputusan di `sumber`.
3. Jalankan ulang M2f (Colab/Drive dengan repo terbaru), kalibrasi derate, dan cek saturasi; kalibrasi silang (mentah)
   untuk memastikan usulan berikutnya tidak berubah karena koreksi.
