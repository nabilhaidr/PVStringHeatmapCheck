# Ide Artikel & Jurnal — Sistem Analitik Performa PV String dan Soiling Analysis

Status: Draft v1.1 (update hasil run soiling 16 Juli 2026)
Tanggal: 2026-07-17
Basis: kode, konfigurasi, dan hasil run aktual repo `https://github.com/ompltsikn/PVStringHeatmapCheck` per 17 Juli 2026
Penyusun: O&M PLTS IKN

Dokumen ini memetakan ide publikasi (jurnal internasional, jurnal nasional, konferensi, artikel populer) yang **didukung oleh kode dan data yang sudah ada** — bukan wishlist. Tiap ide mencantumkan: pertanyaan riset, kontribusi/kebaruan, bahan yang sudah tersedia (file konkret di repo), yang masih kurang, target venue, dan risiko. Bagian akhir berisi matriks prioritas dan prasyarat sebelum menulis.

---

## 1. Aset yang Sudah Dimiliki (modal publikasi)

### 1.1 Data dan skala

| Aset | Detail | Bukti di repo |
|---|---|---|
| Site skala utilitas | PLTS IKN: 194 inverter, 10 blok WB, 4.470 string aktif, ±71,5 MWp / ±50 MW AC | `config/strings.yaml`, `config/m2_config.yaml` (capacity_kwp 71500) |
| Lokasi unik | Khatulistiwa (lat −0,99°), Kalimantan Timur, iklim hujan tropis (Köppen Af), tilt 10° menghadap **utara**, terrain 65–105 m | `config/site_geometry.yaml` |
| Modul bifacial | Jinko JKM625N Tiger Neo N-type dual-glass 625 Wp, 24–26 modul/string | `config/panel_spec.yaml` |
| Telemetri string 5-menit | V & I per string (PV1–PV28) per inverter, multi-tahun | notebook + raw data input |
| Data lingkungan | POA 5 weather station (2025–2026), Tcell, ambien, angin, albedo NSRDB TMY, **curah hujan harian** | `raw data input/`, `coba/precipitation_daily_plts_ikn.csv` |
| Ground truth cleaning | Checklist cleaning manual per string per tanggal + mapping DC cable | `Report & Schedule Cleaning PLTS IKN.xlsx`, `pv_pipeline/m2a/cleaning_report.py` |
| Curtailment | Setpoint proporsional 2 busbar (24,3 + 25,7 MW), deemed energy (PAE), logika MAX(L,M) | `config/site_geometry.yaml` (generation section) |

### 1.2 Metode dan kode

| Aset | Detail |
|---|---|
| Framework M2 | 10 detektor (availability, open circuit, ground fault, peer z-score, MPPT ratio, shading, low irradiance, soiling, iForest, LSTM-AE) dengan skema severity/confidence/evidence seragam, gating siang/POA, debounce |
| Soiling SRR + ekstensi 2026-07 | rdtools SRR (Deceglie et al. 2018) **plus**: koreksi suhu PR (γ −0,29 %/°C), **mask availability M2e ke penyebut PR** (energi + kapasitas), klasifikasi recovery hujan vs cleaning manual (±3 hari, 1 mm), DirectCleaningImpact pre/post independen SRR, per-string cleaning recommendation, monthly loss, knob segmentasi monsoon (`precip_and_shift`, min_interval 7, day_scale 13) — `pv_pipeline/m2a/soiling.py` (1.906 baris) |
| Baseline accumulator | Kurasi data sehat otomatis: temuan CRITICAL/HIGH dikecualikan dari baseline training | `pv_pipeline/baseline.py` |
| LSTM-AE terlatih | Model `coba/lstm_ae_20260706_084352.pt` (window 24 jam × 96 step 15-menit), wired sebagai sinyal pendukung (input-only) |
| Transparansi/auditability | Workbook Excel 46 sheet yang mereproduksi keputusan tiap detektor dengan formula live (`docs/M2_PV_Performance_Workbook.xlsx`) + 11 dokumen reverse-engineering (`docs/M2_RE_*.md`) |
| Dashboard | Streamlit multi-halaman, halaman Trends sudah di-spec (`docs/HANDOFF-trends.md`) |

### 1.3 Hasil run nyata (siap dianalisis untuk paper)

Run definitif SRR 2025-01-03 s.d. 2026-06-30, ≈18 bulan (`coba/run m2a soiling 16072026/`), CI 68,2%, sawtooth PNG per scope, dan **biaya cleaning riil per blok** (WB01/02: Rp582.500/WB; WB03–10: Rp6.875.000/WB):

| Scope | SR p50 | CI | Soiling loss | Hari valid | Payback (hari) | Severity |
|---|---|---|---|---|---|---|
| WB01 | 0,973 | [0,936–0,992] | 2,7% | 212 | 0,7 | MEDIUM |
| WB02 | 0,981 | [0,973–0,988] | 2,0% | 211 | 1,0 | INFO |
| WB03 | 0,935 | [0,915–0,950] | 6,5% | 336 | 3,3 | HIGH |
| WB04 | 0,918 | [0,830–0,967] | 8,2% | 333 | 2,1 | HIGH |
| WB05 | 0,946 | [0,920–0,963] | 5,4% | 331 | 3,2 | HIGH |
| WB06 | 0,968 | [0,955–0,980] | 3,2% | 324 | 5,0 | MEDIUM |
| WB07 | 0,939 | [0,919–0,957] | 6,1% | 325 | 3,2 | HIGH |
| WB08 | 0,969 | [0,955–0,981] | 3,1% | 322 | 6,1 | MEDIUM |
| WB09 | 0,925 | [0,899–0,948] | 7,5% | 325 | 2,2 | HIGH |
| WB10 | 0,966 | [0,952–0,978] | 3,4% | 323 | 5,7 | MEDIUM |
| Grup WB01–02 | 0,961 | [0,929–0,981] | 3,9% | 214 | 0,5 | MEDIUM |
| Grup WB03–10 | 0,961 | [0,946–0,971] | 3,9% | 344 | 4,6 | MEDIUM |

Pola baru yang layak dianalisis: WB01–02 kini zona terbersih (2,0–2,7%), WB03–10 rentang 3,1–8,2% dengan 5 blok berstatus HIGH; payback < 7 hari di semua scope. Laporan internal run ini sudah ada (`Laporan_M2aSoiling_run16072026.docx` + `Presentasi_M2aSoiling_run16072026.pptx`) — bisa jadi kerangka awal paper.

Catatan penting:

1. Run **site-level blend** (8 Juli) tidak menghasilkan sheet `EconomicAnalysis` — konsisten dengan risiko `NoValidIntervalError` di `prd.md` (interval kering terfragmentasi hujan monsoon); run-run berikutnya memang langsung per-zona. Kegagalan ini **bukan aib — justru temuan metodologis** (lihat J1).
2. **SR bergeser antar window**: vs run 11 Juli (data s.d. 14 Mei), WB01 berubah 6,4% → 2,7% dan WB02 1,1% → 2,0% setelah window diperpanjang ke 30 Juni. Ini wajib dijelaskan di paper (event cleaning/hujan baru di periode tambahan, efek mask availability, atau sensitivitas segmentasi SRR) — lihat prasyarat §8.3.

---

## 2. Posisi Kebaruan (hasil scoping literatur singkat, 11 Jul 2026)

1. **Anggapan umum**: di iklim tropis basah, hujan dianggap "self-cleaning" dan soiling loss dianggap kecil (~3%, mis. studi Kerala melaporkan 3→6% pasca-monsoon tanpa cleaning). Mayoritas studi soiling tropis memakai **kupon kaca / rooftop kecil**, bukan data operasional skala utilitas. Hasil kita: **per-blok 2,0–8,2%** pada plant 71,5 MWp — beberapa blok jauh di atas narasi "hujan cukup".
2. **SRR di iklim monsoon** hampir tidak dibahas: metode SRR lahir dari iklim kering (sawtooth panjang). Masalah interval terfragmentasi + kegagalan site-level + solusi per-zona & `precip_and_shift` adalah cerita metodologis yang belum banyak ditulis. Dalam scoping singkat, tidak ditemukan studi SRR skala utilitas untuk Indonesia/Asia Tenggara (klaim ini perlu dicek ulang lebih ketat saat penulisan).
3. **Availability-masked SRR** (drop energi + kapasitas inverter-day ber-uptime rendah dari penyebut PR agar outage tidak terbaca soiling) — praktik yang jarang dieksplisitkan di literatur; kandidat kontribusi metode, bahkan kandidat kontribusi upstream ke rdtools.
4. **Deteksi fault PV berbasis ML sudah ramai** (VAE, LSTM-AE, GNN; akurasi 80-an %). Angle kita yang membedakan: **kurasi healthy-baseline otomatis oleh detektor rule-based** (menjawab masalah "train on unlabeled healthy data" yang diakui literatur) + posisi ML sebagai sinyal pendukung dalam framework glass-box, bukan pengganti.
5. **Publikasi berbasis data operasional PLTS skala utilitas Indonesia masih jarang** — nilai strategis untuk jurnal nasional dan visibilitas industri.

---

## 3. Ide Jurnal Internasional

### J1 — Soiling di iklim hujan khatulistiwa (PALING SIAP, PRIORITAS 1)

**Judul kerja (EN)**: *"Quantifying soiling losses at a 71.5 MWp equatorial PV plant in a rainforest climate: an availability-masked, temperature-corrected stochastic rate-and-recovery approach"*
**Judul kerja (ID)**: Kuantifikasi kerugian soiling PLTS 71,5 MWp di iklim hujan khatulistiwa dengan SRR terkoreksi suhu dan ketersediaan.

**Pertanyaan riset**:
1. Berapa kerugian soiling aktual di iklim yang dianggap "self-cleaning"? Apakah homogen antar zona dalam satu site?
2. Adaptasi apa yang dibutuhkan SRR agar bekerja di iklim monsoon (interval kering pendek, PR berisik)?
3. Apakah cleaning manual masih ekonomis di iklim hujan tinggi — dan di zona mana?

**Kontribusi**:
- Angka soiling operasional ≈18 bulan (Jan 2025–Jun 2026), per-WB dengan CI, di segmen iklim yang kurang terwakili (Af, khatulistiwa) — heterogenitas antar blok 2,0–8,2% pada site yang sama adalah temuan tersendiri: WB01–02 terbersih (2,0–2,7%), 5 blok WB03–10 berstatus HIGH (hipotesis penjelas yang bisa diuji: tinggi panel WB01–02 70 cm vs WB03–10 50–250 cm, frekuensi cleaning per zona, jarak ke jalan/area konstruksi IKN, terrain).
- Protokol SRR untuk iklim basah: kegagalan site-level blend → analisis per cleaning-zone; kriteria `precip_and_shift` + min_interval 7 + day_scale 13; reindex harian.
- Mask availability M2e ke penyebut PR (energi + kapasitas) — mencegah bias outage→soiling.
- Validasi silang recovery: klasifikasi hujan vs manual (data curah hujan + checklist cleaning) dan **DirectCleaningImpact** pre/post yang independen dari SRR.
- Ekonomi cleaning riil (tarif, biaya, payback per zona) + rekomendasi prioritas per string.

**Sketsa abstrak (EN, draft)**: *Soiling is commonly assumed negligible in rainforest climates due to frequent rain cleaning. Using 18 months of 5-minute operational data from a 71.5 MWp PV plant near the equator (East Kalimantan, Indonesia), we quantify soiling with the stochastic rate-and-recovery (SRR) method extended with module-temperature-corrected daily PR and an inverter-availability mask that removes low-uptime inverter-days from both energy and capacity. Site-level SRR fails due to rain-fragmented soiling intervals; zone-level analysis with a precipitation-aware clean criterion yields P50 soiling losses of 2.0–8.2% across ten inverter blocks. Manual-cleaning records and rainfall data allow classification of recovery events and independent pre/post validation. With per-block cleaning costs, payback stays below one week in every block (0.5–6 days). The results challenge the rain-cleaning assumption for utility-scale plants in the humid tropics and provide a reusable SRR protocol for monsoon climates.*

**Yang sudah ada**: seluruh kode + hasil (§1.3), sawtooth PNG per scope, presipitasi harian, checklist cleaning, dokumen metode (`docs/M2_RE_09_M2aSoiling.md`), laporan & presentasi internal run 16 Juli (kerangka awal paper).
**Yang masih kurang**:
- Analisis sensitivitas knob SRR (day_scale, min_interval, criterion) — 1–2 minggu komputasi + analisis.
- **Analisis stabilitas antar-window** — jelaskan pergeseran SR saat window diperpanjang (WB01 6,4% → 2,7% antara run 11 Jul dan 16 Jul); dekomposisi: event baru vs mask availability vs segmentasi.
- Uji hipotesis heterogenitas antar WB (tinggi panel, jarak sumber debu, curah hujan per WS) — data sebagian ada.
- Perbandingan dengan metode alternatif (fixed rate / loss factor sederhana) sebagai baseline.
- Izin publikasi data dari pemilik aset; keputusan anonimisasi.
**Target venue** (urutan ambisi): IEEE Journal of Photovoltaics atau Progress in Photovoltaics → **Solar Energy (realistis, cocok)** → EPJ Photovoltaics / Renewable Energy (fallback solid). Konferensi pendamping: **PVPMC workshop** (komunitas rdtools — sangat tepat untuk ekstensi availability-mask), IEEE PVSC, EU PVSEC.
**Risiko penolakan umum**: satu site saja (mitigasi: 10 blok sebagai sub-unit + 2 musim); CI lebar di beberapa WB (WB04); tidak ada albedometer (tidak kritis untuk SRR berbasis PR).

### J2 — Framework M2: diagnosa string skala armada dari telemetri inverter saja (PRIORITAS 2)

**Judul kerja (EN)**: *"A glass-box multi-detector framework for string-level diagnostics of utility-scale PV using inverter telemetry alone: design and 194-inverter case study"*

**Pertanyaan riset**: seberapa jauh diagnosa string-level (open circuit, ground fault, high-R, MPPT mismatch, shading, availability) bisa dicapai **tanpa hardware tambahan** (hanya data 5-menit inverter), dengan false-positive terkontrol dan keputusan yang bisa diaudit?

**Kontribusi**:
- Arsitektur 10 detektor dengan skema temuan seragam (severity/confidence/evidence), gating kondisi + debounce, cross-check curtailment, dan baseline accumulator.
- **Auditability sebagai fitur riset**: tiap detektor direproduksi dalam workbook Excel formula-live (46 sheet) — pendekatan "glass-box" yang jarang; kontras dengan tren black-box ML.
- Studi kasus 194 inverter / 4.470 string dengan statistik temuan multi-bulan.

**Yang masih kurang (kritis)**: **validasi ground-truth** — precision/recall terhadap temuan O&M terkonfirmasi. Tanpa ini, reviewer akan menolak klaim akurasi. Butuh kampanye labeling 2–3 bulan bersama tim O&M (ambil sampel temuan → verifikasi lapangan → catat). Juga perbandingan dengan minimal 1 metode pembanding.
**Target venue**: Renewable Energy / Solar Energy → IET Renewable Power Generation / IEEE Access (fallback: Energies). Applied Energy bila analisis kerugian energi/ekonomi diperkuat.
**Risiko**: dianggap "engineering report" bila tanpa evaluasi kuantitatif — ground truth adalah kuncinya.

### J3 — LSTM-AE dengan kurasi baseline otomatis (PRIORITAS 3, setelah evaluasi)

**Judul kerja (EN)**: *"Label-free training of LSTM autoencoders for intermittent PV string fault detection via rule-based healthy-data curation"*

**Angle**: bukan "AE baru", melainkan **pipeline kurasinya** — baseline accumulator mengecualikan inverter-day/string-day yang di-flag detektor rule-based sehingga AE dilatih pada data yang benar-benar sehat, tanpa label manual. Ablation yang menjual: AE dilatih **dengan vs tanpa** kurasi → tunjukkan degradasi performa bila data latih terkontaminasi fault.

**Yang sudah ada**: model terlatih (2026-07-06), window-error artifact harian (`M2b_intermittent_WindowErrors`), baseline accumulator, data multi-bulan.
**Yang masih kurang**: evaluasi kuantitatif terhadap event nyata (butuh ground truth dari J2 atau dari temuan detektor lain sebagai proxy-label), ablation study, kalibrasi threshold (saat ini 1.0).
**Target venue**: Solar Energy / Engineering Applications of Artificial Intelligence / IEEE Access. Lebih realistis: **konferensi dulu** (PVSC/regional) → versi jurnal setelah evaluasi matang.
**Catatan jujur**: ruang ini padat (VAE/GNN/hybrid 2024–2025); tanpa ablation kurasi yang bersih, kontribusi terlihat inkremental.

### J4 — PR di bawah curtailment (bisa berdiri sendiri atau digabung ke J2)

**Judul kerja (EN)**: *"Disentangling grid curtailment from underperformance in daily performance ratio accounting of a busbar-limited 50 MW plant"*

**Angle**: plant dibatasi setpoint proporsional per busbar; energi tercatat memakai MAX(actual, deemed). Bagaimana menghitung PR yang adil dan mengklasifikasi hari PR-rendah (fault vs curtailment)? Relevan luas untuk pasar berkembang dengan curtailment jaringan. Bahan: logika generation loader + cross-check di `pv_pipeline/generation`, `physics`.
**Kekurangan**: kontribusi teoretis tipis bila sendirian — pertimbangkan sebagai **section kuat di J2** atau short communication / technical note (mis. di Energy for Sustainable Development).

### J5 — Bifacial menghadap utara di khatulistiwa (TAHAN DULU)

Menarik (tilt 10° face-north di lat −1°, albedo NSRDB tanpa albedometer), tapi model irradiance sisi belakang eksplisit **out of scope** di kode saat ini (`prd.md` §5). Simpan sebagai future work atau kolaborasi dengan grup pemodelan; jangan jadikan prioritas sekarang.

---

## 4. Jurnal Nasional (SINTA) — Bahasa Indonesia

### N1 — Implementasi sistem
**Judul kerja**: "Sistem analitik performa string PLTS skala utilitas berbasis Python open-source: studi kasus PLTS IKN 50 MW". Isi: arsitektur pipeline, 10 detektor, workflow Colab/Drive, contoh temuan. Kandidat venue (cek scope & akreditasi terkini sebelum submit): Jurnal Nasional Teknik Elektro dan Teknologi Informasi (JNTETI/UGM), Jurnal Rekayasa Elektrika (Unsyiah), jurnal energi terbarukan LIPI/BRIN. Nilai: diseminasi nasional cepat, portofolio, umpan balik sebelum versi internasional.

### N2 — Ekonomi cleaning tropis
**Judul kerja**: "Analisis keekonomian pembersihan modul PLTS di iklim tropis basah: payback berbasis soiling ratio". Angka riil (tarif 1.500 IDR/kWh, biaya cleaning aktual per blok, payback 0,5–6 hari) — menarik untuk jurnal teknik/energi maupun manajemen aset. Bisa ditulis paralel dengan J1 tanpa saling memakan (fokus ekonomi vs metodologi).

---

## 5. Konferensi

| Venue | Kecocokan | Materi |
|---|---|---|
| **PVPMC (PV Performance Modeling Collaborative)** | Sangat tinggi — komunitas rdtools/NREL; format workshop ramah praktisi | Availability-masked SRR + protokol monsoon (inti J1) |
| IEEE PVSC / EU PVSEC | Tinggi; area soiling & O&M analytics aktif | Versi ringkas J1 atau J2 |
| Konferensi IEEE/nasional Indonesia (mis. ICSEEA, i-TREC — verifikasi jadwal) | Sedang-tinggi; audiens regional | N1/N2, J2 |
| Strategi | Konferensi → extended version ke jurnal (umum dan diterima) | — |

---

## 6. Artikel Populer / Industri

| Ide | Hook | Outlet kandidat |
|---|---|---|
| A1 — "Soiling in the rain belt" | Kontra-intuitif: plant di iklim hujan khatulistiwa tetap kehilangan 2–8% per blok karena debu; hujan ternyata tidak merata membersihkan | PV Magazine, pv-tech (EN); versi ID di media energi nasional |
| A2 — "Dari 45 MB Excel per hari menjadi daftar prioritas cleaning" | Cerita transformasi kerja O&M dengan Python open-source, tanpa server | LinkedIn/Medium (EN+ID) — juga berfungsi sebagai portofolio tim |
| A3 — "Fault atau curtailment?" | Salah diagnosa paling mahal di PLTS ber-curtailment; bagaimana cross-check otomatis menghindarkannya | LinkedIn/Medium, majalah asosiasi energi |
| A4 — Kontribusi upstream rdtools | Usulkan availability-mask & pengalaman monsoon sebagai GitHub discussion/PR ke rdtools; kredibilitas + sitasi komunitas | github.com/NREL/rdtools |

Artikel populer bisa terbit **sebelum** jurnal (tidak dianggap prior publication selama tidak memuat detail metodologi/hasil lengkap) — tapi tetap minta izin pemilik aset dulu.

---

## 7. Matriks Prioritas

| # | Ide | Kesiapan data | Effort tulis+analisis | Impact | Rekomendasi waktu |
|---|---|---|---|---|---|
| 1 | J1 Soiling tropis | ●●●●● (hasil+CI+ekonomi riil per blok) | 2–3 bulan | Tinggi | Mulai sekarang; submit Q4 2026 |
| 2 | A1 + A4 | ●●●●● | 1–2 minggu | Sedang (visibilitas) | Segera setelah izin |
| 3 | PVPMC/PVSC abstrak | ●●●●○ | 2–4 minggu | Sedang-tinggi | Sesuai deadline CFP |
| 4 | N1 / N2 | ●●●●○ | 3–6 minggu | Sedang (nasional) | Paralel dengan J1 |
| 5 | J2 Framework M2 | ●●○○○ (butuh ground truth) | 4–6 bulan (termasuk kampanye validasi) | Tinggi | Mulai kampanye labeling sekarang; submit 2027 |
| 6 | J3 LSTM-AE | ●●○○○ (butuh evaluasi+ablation) | 3–5 bulan | Sedang | Setelah proxy-label dari J2 |
| 7 | J4 Curtailment-PR | ●●●○○ | 1–2 bulan | Sedang | Gabung ke J2, atau technical note |
| 8 | J5 Bifacial | ●○○○○ | — | — | Tahan (out of scope kode) |

---

## 8. Prasyarat Sebelum Menulis (checklist)

1. **Izin & kepemilikan data** — persetujuan tertulis pemilik/operator aset untuk publikasi data operasional; putuskan anonimisasi ("a 71.5 MWp plant in East Kalimantan") vs nama terbuka. Tentukan authorship & afiliasi.
2. **Kunci dataset publikasi** — run definitif: `coba/run m2a soiling 16072026/` (data 2025-01-03..2026-06-30, CI lengkap, biaya cleaning riil per blok). Jangan campur dengan run lama: run 8 Juli menghasilkan `sr_ci_lower/upper` kosong (sebelum perbaikan `_ci_bounds` di `soiling.py`), dan run 11 Juli memakai window lebih pendek (s.d. 14 Mei) dengan hasil berbeda.
3. **Analisis sensitivitas & stabilitas** — variasikan `day_scale`, `min_interval_length`, `clean_criterion`, `availability_min_uptime_pct`; laporkan kestabilan SR. Tambahkan **stabilitas antar-window**: jalankan SRR pada window bergeser/berlapis (mis. per 3 bulan penambahan) dan jelaskan pergeseran WB01 6,4% → 2,7% antara run 11 Jul dan 16 Jul.
4. **Uji hipotesis heterogenitas WB** — regresikan soiling loss per WB terhadap tinggi panel, curah hujan WS terdekat, jarak sumber debu (perlu data jarak; sebagian dari layout site).
5. **Kampanye ground truth (untuk J2/J3)** — protokol: sampel temuan per detektor → verifikasi lapangan O&M → label confirmed/false; target ≥ 50–100 event.
6. **Reproducibility statement** — kode sudah rapi (YAML config, pytest, dokumentasi RE); putuskan apakah pv_pipeline akan di-open-source (membuka opsi software paper di JOSS, dan memperkuat J1/J2).
7. **Cek etika jurnal** — satu dataset boleh melahirkan beberapa paper selama kontribusinya berbeda (J1 metodologi soiling ≠ N2 ekonomi ≠ J2 framework); hindari salami slicing dengan menjaga pemisahan yang jelas.

## 9. Yang TIDAK Boleh Diklaim (integritas ilmiah)

- Akurasi deteksi (precision/recall) — belum ada validasi lapangan sistematis.
- iForest sebagai detektor teruji — statusnya eksperimental/belum dikalibrasi.
- LSTM-AE sebagai detektor produksi — baru terlatih, input-only, belum dievaluasi kuantitatif.
- Albedo terukur — yang ada forecast NSRDB TMY.
- Soiling site-level tunggal — run site blend gagal interval; laporkan per-zona apa adanya.
- Kerugian soiling "tahunan" — data ≈18 bulan dengan hari valid 211–344 per scope; nyatakan periode eksplisit.
- Angka soiling sebagai nilai tetap — SR bergeser antar window run (WB01 6,4% → 2,7%); selalu sebutkan window data dan versi run yang dipakai.

---

## Referensi kunci (dari scoping 2026-07-11)

- Deceglie et al. 2018, "Quantifying Soiling Loss Directly from PV Yield", IEEE J. Photovoltaics 8(2) — dasar SRR ([rdtools](https://rdtools.readthedocs.io/en/stable/))
- [IEA-PVPS T13 Soiling Losses Report (2022)](https://iea-pvps.org/wp-content/uploads/2023/01/IEA-PVPS-T13-21-2022-REPORT-Soiling-Losses-PV-Plants.pdf) — konteks global
- [MDPI Sustainability review soiling & mitigasi (2023)](https://www.mdpi.com/2071-1050/15/24/16669); [MDPI Energies analisis faktor lingkungan](https://www.mdpi.com/1996-1073/16/1/45)
- [EPJ PV: peta soiling Eropa dengan efek pembersihan hujan parsial (2026)](https://www.epj-pv.org/articles/epjpv/full_html/2026/01/pv20250058/pv20250058.html) — pembanding metodologi rain-cleaning
- [Studi Kerala PRCLM (tropis basah, rooftop)](https://masujournal.org/view_journal.php?id=720) — pembanding angka 3–6%
- Deteksi fault ML pembanding: [VAE monitoring](https://www.sciencedirect.com/science/article/abs/pii/S019689042400606X), [hybrid AE+RF (MDPI 2025)](https://www.mdpi.com/2673-4117/6/10/254), [PVeSight string anomaly (2025)](https://www.sciencedirect.com/science/article/pii/S2468502X25000269)
- Internal: `prd.md`, `docs/M2_RE_09_M2aSoiling.md`, `docs/M2_Family_Summary.md`, hasil run definitif `coba/run m2a soiling 16072026/` (+ pembanding window: `coba/test run m2asoiling 11072026/`), `Laporan_M2aSoiling_run16072026.docx`
