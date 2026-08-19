# M2f Loss Attribution & Pareto Analysis Design

**Tanggal:** 2026-08-11
**Status:** Disetujui untuk perencanaan implementasi
**Lokasi output:** `outputs/` (workbook + PNG)

## Tujuan

Mengkuantifikasi berapa kWh energi yang hilang **per penyebab** pada sisi DC,
per (string, hari), sehingga dapat diurutkan menjadi Pareto untuk prioritisasi
tindakan dan perhitungan ROI cleaning/maintenance.

Modul ini mengisi gap yang sudah tercatat di `docs/M2_Family_Summary.md` bagian
6 ("M2f Loss Attribution -- belum ada modul") dan roadmap `prd.md` Phase 2
("Add complete loss waterfall") serta Phase 3 ("Add stronger residual
attribution engine").

## Keputusan desain

**Baseline closure = POA terukur + Tcell terukur.** `E_expected` dihitung dari
`physics.compute_p_expected_per_string(POA_ws, Tcell_ws, spec, wb)`. Rugi cuaca
dan rugi termal tereliminasi *by construction*, sehingga waterfall hanya berisi
rugi yang dapat ditindak -- persis yang dibutuhkan untuk keputusan ROI.

**Atribusi sekuensial berbasis counterfactual, bukan SHAP.** Tiap kategori
mengklaim energi menurut urutan prioritas tetap, dari ledger per (string,
timestamp). Deterministik, tertutup, dan dapat diaudit.

Alternatif yang ditolak:

- **SHAP untuk alokasi kWh.** Kekeliruan dimensi: SHAP menjelaskan output
  sebuah model dalam satuan model tersebut. Tidak ada model yang memprediksi
  kWh loss per string di repo ini; IsolationForest menghasilkan skor anomali
  dan LSTM-AE menghasilkan reconstruction error, keduanya tak berdimensi
  energi. Nilai SHAP menjumlah ke `(prediksi - base value)`, bukan ke energi,
  sehingga identitas closure rusak. Ditunda; boleh dipakai kelak sebagai alat
  diagnosa terpisah untuk membedah bucket `unexplained`, tidak pernah di dalam
  waterfall kWh.
- **`weather_irradiance` sebagai batang Pareto.** Akan selalu peringkat 1
  dengan selisih jauh dan menutup garis kumulatif 80% sendirian, sehingga
  kategori yang dapat ditindak tenggelam. Cuaca tidak dapat di-maintenance.
  Dengan baseline POA terukur yang dipilih di atas, cuaca sudah tereliminasi
  by construction sehingga tidak muncul sebagai batang maupun baris.
- **Baseline clear-sky pvlib.** Paling lengkap secara fisik tetapi membuat
  Pareto didominasi cuaca, sehingga tidak dapat dipakai untuk prioritisasi
  maintenance.

## Ruang lingkup

### Termasuk

- Neraca energi sisi DC per (string, hari) dengan identitas closure yang
  ditegakkan.
- Estimator kWh per kategori untuk `availability_outage`, `dc_cable_fault`,
  `soiling`, `shading`, `low_irradiance_eff`.
- Kalibrasi koefisien gain bifacial per WB.
- Ledger klaim yang mencegah double-count antar detektor.
- Workbook Excel multi-sheet mengikuti pola `M2Engine.write_xlsx_multi`.
- Grafik waterfall dan diagram Pareto sebagai `matplotlib.figure.Figure`.

### Tidak termasuk

- **Rugi inverter dan rugi trafo.** Keduanya hidup di rantai M1 -> M3 yang
  terpisah dan tidak dapat diatribusikan per string. Rugi inverter sudah
  terukur langsung (`Active power(kW)` / `Total input power(kW)`, ~98,6% pada
  sampel 2026-05-13) dan tidak memerlukan atribusi.
- **Rugi kabel MV dan POI 20 kV.** Tidak ada data meter POI di repo.
- **`microcrack` (M2c) dan `bifacial_underperf` (M2d).** Tidak ada modul, dan
  tidak dapat dibangun tanpa instrumen baru: EL imaging + IV tracer untuk
  microcrack, sensor rear-POA (>=4/row, IEC TS 60904-1-2) untuk bifacial.
  Keduanya tetap dideklarasikan di `loss_waterfall` dengan nilai `None`
  (bukan `0.0`) supaya kontribusinya jatuh jujur ke `unexplained` alih-alih
  menyamar sebagai "tidak ada rugi".
- SHAP dan dependensi `shap`.

## Titik ukur dan identitas closure

Unit atribusi adalah **(string, hari)** -- resolusi tempat seluruh detektor
M2a-M2e bekerja dan tempat data DC per string tersedia. Rollup: string ->
inverter -> WB -> site.

    E_expected(str,t) = compute_p_expected_per_string(POA_ws(t), Tcell_ws(t),
                                                     spec, wb) * g_bifacial(wb) * dt
    E_actual(str,t)   = PV{n} Power(kW)(t) * dt          # fallback V*I/1000
    L_total(str,hari) = sum_t E_expected - sum_t E_actual

Invarian yang mengunci seluruh modul:

    sum(semua kategori) + unexplained == L_total     per (str, hari)

Kesamaan ini eksak dalam toleransi `1e-6` kWh absolut per (string, hari). Bila
tidak sama, itu bug -- bukan "cukup dekat".

`L_total` boleh negatif (string melebihi ekspektasi). Dalam kasus itu tidak ada
kategori yang boleh mengklaim nilai negatif; seluruh selisih masuk ke
`unexplained` sebagai residual negatif.

## Baseline dan kalibrasi bifacial

`compute_pmax_per_module` memakai **POA depan saja**, sedangkan modul
Jinko JKM625N adalah bifacial (`panel_spec.yaml` -> `bifacial_factor_pct: 80`).
Prediksi: `E_expected` akan sistematis *under-estimate*, sehingga string sehat
menghasilkan `L_total` negatif dan seluruh waterfall bias.

Penanganan: koefisien gain bifacial `g_bifacial(wb)`, satu skalar per WB,
dikalibrasi dari string sehat pada hari clear-sky (`Kt` mendekati 1) memakai
data NORMAL yang sudah dikumpulkan `BaselineAccumulator` di `baseline/`.
Kriteria kalibrasi: median `L_total` string sehat pada hari clear-sky
mendekati nol. Hasilnya disimpan sebagai konstanta di config, bukan model.

Ini sekaligus menyiapkan jalan untuk `bifacial_underperf` bila sensor rear-POA
terpasang kelak: deviasi terhadap `g_bifacial` terkalibrasi menjadi sinyalnya.

Verifikasi hipotesis under-estimate ini **wajib dilakukan pada run pertama**
dengan data POA nyata; hipotesis ini belum teruji karena `raw data input/`
kosong di working tree saat spec ditulis.

## Ledger klaim dan urutan prioritas

`LossLedger` memelihara sisa energi yang belum diklaim per (string, timestamp).
Tiap kategori mengklaim menurut urutan tetap di bawah; energi yang sudah
diklaim tidak dapat diklaim lagi oleh kategori berprioritas lebih rendah.

| # | Kategori | Counterfactual | Sumber detektor |
|---|---|---|---|
| 1 | `availability_outage` | `E_expected` sepanjang interval mati | `availability.py` |
| 2 | `dc_cable_fault` | `(I_sibling_median - I_string) * V * dt` | `peer_zscore`, `open_circuit`, `ground_fault`, `mppt_ratio` |
| 3 | `shading` | median sibling **per jam** pada jam ter-flag | `m2a/shading` |
| 4 | `soiling` | `p_loss` * energi tersisa setelah 1-3 | `m2a/soiling` |
| 5 | `low_irradiance_eff` | defisit ter-fit pada pita POA [50,250] | `m2a/low_irradiance` |
| 6 | `microcrack`, `bifacial_underperf` | -- | `None` |
| 7 | `unexplained` | sisa ledger | -- |

Alasan urutan, bukan selera:

- **Availability lebih dulu.** Saat string mati, tidak ada rugi lain yang
  berlaku pada jendela itu.
- **Fault keras sebelum yang lunak.** Open circuit dan ground fault mengunci
  energinya sendiri.
- **Shading sebelum soiling.** SRR menyerap apa saja yang turun perlahan. Bila
  dibalik, rugi shading akan diklaim sebagai rugi soiling dan ROI cleaning
  menjadi *overstated* -- padahal angka itulah dasar keputusan biaya.

## Prasyarat: artefak deret waktu di detektor

Kategori 2, 3, dan 5 membutuhkan array per-timestamp yang saat ini dihitung di
dalam detektor tetapi **tidak dipersistensi** -- hanya skor akhirnya yang
keluar. Tiap detektor berikut perlu tambahan satu artifact deret waktu berisi
nilai aktual, nilai counterfactual, dan mask interval ter-flag:

`peer_zscore.py`, `open_circuit.py`, `ground_fault.py`, `mppt_ratio.py`,
`m2a/shading.py`, `m2a/low_irradiance.py`.

Perubahan per file kecil dan aditif (tidak mengubah findings atau severity yang
ada), tetapi menyentuh enam file. Ini bagian dari lingkup.

`m2a/soiling.py` tidak memerlukan perubahan: `energy_lost_kwh_est` dan
`energy_recovered_kwh_per_day` per string sudah tersedia.

## Struktur modul

    pv_pipeline/m2f/
      __init__.py
      baseline.py     # E_expected per (string,ts) + kalibrasi bifacial per WB
      ledger.py       # LossLedger: klaim, tegakkan closure, residual
      estimators.py   # satu fungsi per kategori -> klaim ke ledger
      pareto.py       # ranking desc, kumulatif %, garis 80%, vital-few
      plots.py        # figure waterfall + figure Pareto
      report.py       # xlsx multi-sheet via pola M2Engine.write_xlsx_multi

## Model data hasil

| Sheet | Isi |
|---|---|
| `M2f_Waterfall` | per WB per bulan: kWh dan % tiap kategori, terurut prioritas |
| `M2f_Pareto` | terurut kWh desc: kWh, %, % kumulatif, flag vital-few |
| `M2f_PerString` | per string per kategori, untuk targeting ROI |
| `M2f_Closure` | audit: `L_total`, jumlah klaim, residual absolut dan % |
| `M2f_BifacialCalib` | `g_bifacial` per WB, jumlah hari dan string yang dipakai |

## Grafik

Ditempatkan di `pv_pipeline/m2f/plots.py`. Fungsi mengembalikan
`matplotlib.figure.Figure` dan **tidak menulis file**, mengikuti pola
`pv_pipeline/viz.py`. Builder notebook yang memanggil
`fig.savefig(path, dpi=150)`, mengikuti pola
`output_string/_build_string_intraday_notebook.py`.

Backend `matplotlib` + `seaborn`, keduanya sudah ada di `requirements.txt`.
Tidak ada dependensi baru.

### Waterfall

    build_loss_waterfall_figure(waterfall_df, *, scope, period_label) -> Figure

- Batang terminal `E_expected` di kiri dan `E_actual` di kanan (warna netral),
  dengan batang turun antar keduanya, satu per kategori, **berurutan
  prioritas** -- bukan urutan besaran. Urutan prioritas adalah inti metodenya
  dan harus terbaca dari grafik.
- Garis konektor antar batang supaya rantai pengurangan terbaca.
- Label nilai kWh dan % terhadap `E_expected` di tiap batang.
- Residual `unexplained` diberi arsiran (hatch) untuk membedakannya dari
  kategori yang benar-benar teratribusi.
- Bila `L_total` negatif (string atau agregat melebihi ekspektasi), batang
  digambar naik dengan warna berbeda, bukan dipaksa nol.
- `microcrack` dan `bifacial_underperf` tidak digambar sebagai batang; catatan
  kaki menyatakan keduanya terlipat ke dalam `unexplained` karena belum ada
  instrumen.

### Pareto

    build_pareto_figure(pareto_df, *, scope, period_label) -> Figure

- Batang kWh terurut menurun pada sumbu kiri, garis kumulatif % pada sumbu
  kanan, garis horizontal di 80%.
- Batang di atas garis 80% (vital few) diberi warna berbeda; jumlah kategori
  vital-few dianotasi.
- `unexplained` **tetap digambar** tetapi diberi arsiran dan **dikecualikan
  dari daftar vital-few yang dapat ditindak**. Porsinya dilaporkan di judul
  sebagai metrik kualitas model: residual besar berarti atribusinya lemah,
  dan menyembunyikannya akan menutupi justru sinyal itu.

### Perilaku umum kedua grafik

- `scope` menerima `site`, `wb`, atau `inverter`; judul menyusun sendiri dari
  `scope` dan `period_label`.
- Palet aman untuk buta warna, urutan warna deterministik.
- Data kosong menghasilkan figure berisi pesan "tidak ada data", **tidak**
  melempar exception -- grafik dipanggil dari notebook batch.

## Konfigurasi

Section `m2f` baru di `config/m2_config.yaml`:

- `enabled` (default `false`, opt-in mengikuti pola detektor lain)
- `attribution_order` -- daftar kategori; eksplisit supaya urutan prioritas
  dapat diaudit dan diuji, bukan tersembunyi di kode
- `bifacial_gain_per_wb` -- hasil kalibrasi
- `clearsky_kt_min` -- ambang hari clear-sky untuk kalibrasi
- `residual_warn_pct` -- ambang residual yang memicu finding INFO

## Penanganan kegagalan

- POA atau Tcell tidak tersedia untuk suatu (WB, hari): hari itu dilewati dan
  dicatat di `M2f_Closure`, bukan diperlakukan sebagai rugi nol.
- Detektor tidak menghasilkan artefak deret waktu: kategorinya diisi `None`
  dan kontribusinya jatuh ke `unexplained`, konsisten dengan perlakuan M2c/M2d.
- Residual melebihi `residual_warn_pct`: emit `M2Finding` severity INFO dengan
  `fault_type="weak_attribution"`. Ini metrik kualitas, bukan kegagalan.

## Strategi pengujian

Tes menguji maksud, bukan sekadar perilaku (Rule 9).

- **Closure**: `sum(kategori) + residual == L_total` per (string, hari) dalam
  toleransi `1e-6` kWh absolut. Invarian yang membuat seluruh angka layak
  dipercaya.
- **Anti-double-count**: string sintetis dengan **hanya** shading -> `soiling`
  mengklaim **0**. Tes ini yang melindungi angka ROI cleaning.
- **Prioritas**: string mati sehari penuh -> `availability` mengklaim 100%,
  seluruh kategori lain 0.
- **Over-performance**: string melebihi ekspektasi -> residual negatif, tidak
  ada kategori mengklaim nilai negatif.
- **Bifacial**: setelah kalibrasi, string sehat pada hari clear-sky memiliki
  `L_total` mendekati nol.
- **Kategori terkunci**: `microcrack` dan `bifacial_underperf` bernilai `None`,
  bukan `0.0`, dan tidak mengurangi residual.
- **Grafik**: mengembalikan `Figure` tanpa menulis file; jumlah batang
  waterfall sama dengan jumlah kategori ditambah dua terminal; garis kumulatif
  Pareto berakhir di 100%; data kosong menghasilkan figure, bukan exception.

## Pentahapan

- **v1** -- `availability_outage`, `dc_cable_fault`, `soiling`, `unexplained`,
  plus kedua grafik. Closure sudah berlaku penuh dengan empat kategori;
  sisanya masuk residual secara jujur.
- **v2** -- `shading` dan `low_irradiance_eff` dipindahkan keluar dari residual.
- **v3** -- `microcrack` dan `bifacial_underperf`; terkunci sampai tersedia EL
  imaging + IV tracer dan sensor rear-POA.

## Kriteria penerimaan

1. Identitas closure berlaku eksak untuk seluruh (string, hari) pada rentang uji.
2. Tes anti-double-count dan tes prioritas lulus.
3. `g_bifacial` terkalibrasi, dan median `L_total` string sehat pada hari
   clear-sky mendekati nol.
4. Workbook memuat kelima sheet, dan `M2f_Closure` melaporkan residual
   agregat site.
5. Kedua grafik tergenerasi untuk scope `site` dan `wb`, dan tetap
   menghasilkan figure pada input kosong.
6. Enam detektor mendapat artefak deret waktu tanpa perubahan pada findings
   atau severity yang sudah ada -- diverifikasi lewat suite tes existing.

## Asumsi dan risiko terbuka

- **Gain bifacial belum terverifikasi.** Besarannya baru dapat dikalibrasi
  saat run pertama dengan data POA nyata. Bila `L_total` string sehat tetap
  jauh dari nol setelah kalibrasi, baseline perlu ditinjau ulang sebelum
  angka waterfall dipakai untuk keputusan.
- **Tilt WB01-WB02 belum dikonfirmasi.** `config/site_geometry.yaml:28` hanya
  mengonfirmasi WB03-WB10 dipasang 10 derajat. Bila WB01-02 berbeda,
  `E_expected` untuk 49 inverter (porsi terbesar armada) bias, dan bias itu
  masuk ke bucket rugi terbesar.
- **Kalibrasi pyranometer.** Error POA mengalir ke `E_expected`, yaitu penyebut
  seluruh persentase loss.
- **Drift dokumentasi.** Docstring `pv_pipeline/physics.py:21` menyebut temp
  coef -0.30 %/C sedangkan `config/panel_spec.yaml` memakai -0.29. Kode membaca
  yaml sehingga perilakunya benar; hanya docstring yang basi. Dicatat, tidak
  diubah dalam lingkup ini.
- **Nama sheet legacy.** Sheet `M2c_GroundFault` dan file
  `docs/M2_RE_05_M2cGroundFault.md` memakai awalan M2c, sedangkan
  `core.py:136` mendaftarkan detektornya sebagai `M2b_ground_fault` dan
  taksonomi `M2_Family_Summary.md` memakai M2c untuk Microcrack. Kosmetik,
  tetapi berpotensi membingungkan karena modul ini memakai nama kategori
  sebagai kunci. Dicatat untuk pembersihan terpisah.
