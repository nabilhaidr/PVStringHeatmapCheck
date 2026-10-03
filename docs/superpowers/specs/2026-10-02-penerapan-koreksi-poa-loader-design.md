# Penerapan koreksi POA di loader — rancangan

Tanggal: 2 Oktober 2026. Status: disetujui pengguna (letak di `PyranometerLoader`; pengisi = rata-rata WS terkoreksi;
WS-1 lewat `ws_dikecualikan`); **diimplementasikan 3 Oktober 2026, config belum diubah** (blok `pyranometer.koreksi`
belum ada, jadi semua hasil masih mentah). Isi koreksi (angka, tanggal) **belum** diputuskan; spesifikasi ini tidak
bergantung padanya. Konteks: `docs/Ringkasan_Usulan_Koreksi_POA.md`.

## Tujuan

Loader POA bisa menerapkan koreksi sensor yang sudah diputuskan pemilik dokumen: faktor per (WS, periode), jam
penghalang per WS dan periode, dan WS yang dikeluarkan sementara. Semua modul yang memakai POA terukur (M2f, soiling,
low-irradiance, shading, detektor m2b, kalibrasi derate) otomatis memakai POA terkoreksi; alat kalibrasi silang tetap
membaca POA mentah.

## Latar (dari kode)

- Hampir semua pemakai memuat POA lewat `PyranometerLoader.from_geometry_yaml`, langsung atau via
  `POAProvider.from_yaml`: M2f (`m2f/report.py`), `m2a/soiling.py`, `m2a/shading.py`, `m2a/low_irradiance.py`,
  `peer_zscore.py`, `open_circuit.py`, `mppt_ratio.py`, `ground_fault.py`, `iforest.py`.
- Membuat loader langsung (konstruktor): `run_derate_calibration._muat_poa` (dipakai juga
  `run_poa_cross_calibration.py`), `run_saturation_check.py`, `string_yield_report.py`, notebook probe Drive.
- `get_per_ws(..., fallback_to_avg=True)` mengisi NaN per WS dari kolom `avg` xlsx ("Rata-rata WS 1 - WS 5"), yang
  dihitung dari bacaan mentah: bias WS-2, bayangan WS-1, dan bacaan nol ikut masuk ke pengisi.
- CLI kalibrasi silang mencetak usulan sebagai `ws_gain_periode: … gain: 1.060`, padahal angkanya faktor (1 ÷ gain).

## Keputusan pengguna

| Hal | Keputusan |
|---|---|
| Letak | `PyranometerLoader`, sekali saat dimuat |
| Pengisi sampel yang dibuang | `avg` dihitung ulang dari WS terkoreksi yang tidak dibuang |
| WS-1 | kunci `ws_dikecualikan` berperiode; penghalang jam tetap kunci terpisah |
| Config | tidak diubah sampai pengguna menyetujui isi koreksi |

## Format config (`config/site_geometry.yaml`, blok `pyranometer.koreksi`)

Contoh bentuk (angka dan tanggal di bawah **bukan keputusan**):

```yaml
pyranometer:
  koreksi:
    aktif: false                      # true hanya sesudah keputusan pemilik dokumen
    sumber: "docs/Ringkasan_Usulan_Koreksi_POA.md; keputusan <tanggal>"
    ws_faktor_periode:                # faktor = pengali bacaan (1 / gain sensor)
      WS-3:
        - {mulai: 2026-01-01, akhir: 2026-08-10, faktor: 1.060}
    ws_jam_penghalang:                # jam (0-23) yang dibuang pada rentang tanggal
      WS-1:
        - {mulai: 2026-06-01, akhir: null, jam: [11, 12]}
    ws_dikecualikan:                  # seluruh bacaan WS dibuang pada rentang tanggal
      WS-1:
        - {mulai: 2025-10-01, akhir: null, alasan: "penghalang + bacaan nol; tunggu perbaikan lapangan"}
```

- Tanggal `mulai`/`akhir` inklusif (hari penuh, waktu lokal naif seperti indeks loader); `akhir: null` = terbuka.
- Blok tidak ada, atau `aktif: false` → loader identik dengan sekarang (tidak ada perubahan perilaku).

## Arsitektur

### `pv_pipeline/poa/loader.py`

- Konstruktor `PyranometerLoader(..., koreksi: dict | None = None)`. `None` = mentah (default untuk pemakai konstruktor
  langsung).
- `from_geometry_yaml(geometry_path, *, koreksi: bool = True)` membaca `pyranometer.koreksi`; diteruskan ke konstruktor
  hanya bila `koreksi=True` dan blok `aktif: true`.
- Urutan penerapan, sesudah offset waktu dan sebelum apa pun:
  1. `self.df_mentah` = salinan `self.df` (kolom WS + `avg` xlsx) untuk audit dan pemakai yang butuh mentah;
  2. kalikan bacaan WS dengan `faktor` pada rentang tiap entri `ws_faktor_periode`;
  3. jadikan NaN bacaan WS pada jam × rentang tiap entri `ws_jam_penghalang`;
  4. jadikan NaN seluruh bacaan WS pada rentang tiap entri `ws_dikecualikan`;
  5. hitung ulang `avg` = rata-rata kolom WS terkoreksi yang tidak NaN pada timestamp itu (NaN bila tak ada satu pun).
- Validasi saat dimuat (`ValueError`, pesan menyebut entri): label WS dikenal (WS-1..WS-5); `mulai` ≤ `akhir`;
  `faktor` dalam (0,8; 1,25]; `jam` bilangan bulat 0–23; entri sejenis untuk satu WS tidak tumpang tindih; `aktif`
  bertipe bool.
- Audit:
  - `self.koreksi` (blok yang dipakai, atau None) dan `self.ringkasan_koreksi` (DataFrame `ws, jenis, mulai, akhir,
    nilai, n_sampel` — jumlah sampel tak-NaN yang tersentuh);
  - `get_per_ws` menambah `series.attrs["koreksi_aktif"]` (bool) di samping `fallback_filled`.
- `get_per_ws(fallback_to_avg=True)` tidak berubah logikanya; karena `avg` sudah dihitung ulang, pengisi kini bersih.

### Pemakai yang membuat loader langsung

- `run_derate_calibration._muat_poa(geometry, raw_root, offset, *, koreksi: bool = True)`: membaca blok koreksi dari
  geometri seperti `from_geometry_yaml`; `run_derate_calibration.py` memakai default (terkoreksi) dan mencatat
  `koreksi aktif` + `sumber` di sheet catatan.
- `run_poa_cross_calibration.py` memanggil `_muat_poa(..., koreksi=False)`: kalibrasi silang **selalu** atas data
  mentah (kalau tidak, ia mengukur koreksinya sendiri). `Catatan` mencatat "POA mentah (koreksi tidak diterapkan)".
- Tinjauan 3 Oktober 2026 (disetujui pengguna):
  - `run_saturation_check.py` **terkoreksi** (rasio daya/POA di iradiansi tinggi; bias WS terbaca sebagai kekurangan
    daya), status dicetak di baris pembuka;
  - `pv_pipeline/string_yield_report.py` **terkoreksi** (kurva POA per WB), metadata `poa_koreksi`; sejak 3 Oktober
    juga menerapkan `pyranometer.time_offset_minutes` (metadata `poa_offset_minutes`; bentuk daftar per berkas
    ditolak loader dan tercatat di `poa_read_errors`);
  - `run_poa_offset_check.py` **mentah** (`from_geometry_yaml(..., koreksi=False)`): diagnostik sensor, seperti
    kalibrasi silang;
  - notebook Drive Probe: tidak diubah (cabang `from_geometry_yaml` ikut config; `POA_XLSX` sengaja mentah; peringkat
    variabilitas hari tidak peka faktor beberapa persen).

### Penyelarasan cetakan CLI kalibrasi silang

- `ws_gain_periode` → `ws_faktor_periode`, `gain:` → `faktor:`; dicetak di bawah `pyranometer:` → `koreksi:` dengan
  indentasi config, sehingga bisa disalin apa adanya.
- `ws_jam_penghalang` dicetak dalam bentuk entri config: `- {mulai: <isi>, akhir: <isi>, jam: [11, 12]}` (alat tidak
  menaksir tanggal mulai penghalang).

## Uji (`tests/unit/test_poa_loader.py`, xlsx sintetis dari `conftest.py`)

- Tanpa blok / `aktif: false` → `df` sama persis dengan mentah; `ringkasan_koreksi` kosong.
- Faktor: bacaan WS dalam rentang (batas inklusif) dikali faktor; di luar rentang tidak berubah.
- Penghalang: hanya jam yang disebut, hanya pada rentang tanggal, menjadi NaN.
- Dikecualikan: seluruh bacaan WS pada rentang NaN; `get_per_ws` untuk WB yang dipetakan ke WS itu terisi dari `avg`
  hitung ulang (rata-rata WS lain terkoreksi), bukan dari `avg` xlsx.
- `avg` dihitung ulang memakai nilai terkoreksi dan melewati WS yang dibuang.
- Validasi: faktor 1,5 → `ValueError`; periode tumpang tindih → `ValueError`; WS tak dikenal → `ValueError`.
- `from_geometry_yaml(koreksi=False)` → mentah walau `aktif: true`.
- CLI kalibrasi silang memanggil `_muat_poa` dengan `koreksi=False` (uji CLI yang ada: lambda monkeypatch menerima
  `**kw`); cetakan memakai `ws_faktor_periode`/`faktor:`.

## Langkah sesudah implementasi (di luar spesifikasi ini)

1. Pemilik dokumen memutuskan isi koreksi (ringkasan: pembanding, tanggal batas, WS-1, faktor yang diterapkan).
2. Blok `pyranometer.koreksi` ditulis ke `config/site_geometry.yaml` dengan persetujuan pengguna; `aktif: true`.
3. Jalankan ulang M2f (Colab/Drive memakai repo terbaru), kalibrasi derate, lalu kalibrasi silang (mentah) untuk
   memastikan usulan berikutnya tidak berubah karena koreksi.

## Di luar cakupan

- Model bayangan per jam (penghalang diperlakukan sebagai data hilang, bukan dikoreksi).
- Impor otomatis keluaran CLI kalibrasi silang ke config.
- Koreksi Tcell atau sensor cuaca lain.
