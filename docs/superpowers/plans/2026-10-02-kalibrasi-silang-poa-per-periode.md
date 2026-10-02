# Kalibrasi Silang POA per Periode — Rencana Implementasi

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gain dan kesepakatan acuan POA dihitung per (WS, periode), dengan periode dipotong di celah data ≥ 30 hari.

**Architecture:** Dua fungsi murni baru di `pv_pipeline/poa/kalibrasi_silang.py`. `periode_ws` memotong rentang tiap WS di
celah data. `kalibrasi_per_periode` memanggil `gain_relatif`, `gain_absolut`, `gain_larik`, dan `sepakati` yang sudah ada
pada jendela tiap periode. CLI `run_poa_cross_calibration.py` memakai keduanya untuk sheet `Kesepakatan` dan usulan YAML.

**Tech Stack:** Python, pandas, numpy, pytest. Spesifikasi:
`docs/superpowers/specs/2026-10-02-kalibrasi-silang-poa-per-periode-design.md`.

## Global Constraints

- Celah minimal pemotong periode: 30 hari tanpa data (`min_celah_hari=30`); tanpa flag CLI, tanpa batas manual.
- Fungsi lama (`sampel_stabil` … `sepakati`) tidak diubah.
- Config dan loader tidak diubah; usulan hanya dicetak dengan label "BELUM diterapkan".
- Uji hanya memakai data sintetis (kebijakan NSSE: data IKN tetap lokal).
- Jangan commit `ide_artikel_jurnal - v1 backup.md`.

---

### Task 1: `periode_ws`

**Files:**
- Modify: `pv_pipeline/poa/kalibrasi_silang.py` (tambah fungsi sesudah `gain_larik`)
- Test: `tests/unit/test_poa_kalibrasi_silang.py`

**Interfaces:**
- Produces: `periode_ws(poa: pd.DataFrame, *, min_celah_hari: int = 30) -> pd.DataFrame` berkolom
  `ws, periode, mulai, akhir` (`periode` int 1..n per WS; `mulai`/`akhir` `pd.Timestamp` tengah malam).

- [ ] **Step 1: Tulis uji yang gagal** — tambahkan `periode_ws` ke impor di atas berkas uji, lalu kelas ini sesudah
  `TestGainLarik`:

```python
class TestPeriodeWs:
    def test_celah_40_hari_memotong(self):
        """Sensor yang dilepas lalu dipasang ulang bisa kembali dengan orientasi lain: dua periode."""
        p = _poa(hari=120)                                         # 2026-01-01..2026-04-30
        p.loc["2026-02-01":"2026-03-12", "WS-2"] = np.nan          # 40 hari kosong
        per = periode_ws(p)
        ws2 = per[per["ws"] == "WS-2"]
        assert list(ws2["periode"]) == [1, 2]
        assert list(ws2["mulai"]) == [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-03-13")]
        assert list(ws2["akhir"]) == [pd.Timestamp("2026-01-31"), pd.Timestamp("2026-04-30")]
        assert (per["ws"] == "WS-1").sum() == 1

    def test_celah_20_hari_tidak_memotong(self):
        """Celah singkat (gangguan logger) belum tentu berarti sensor berubah."""
        p = _poa(hari=120)
        p.loc["2026-02-01":"2026-02-20", "WS-2"] = np.nan
        assert (periode_ws(p)["ws"] == "WS-2").sum() == 1

    def test_kosong_di_awal_bukan_periode(self):
        p = _poa(hari=120)
        p.loc[:"2026-02-15", "WS-3"] = np.nan
        ws3 = periode_ws(p).query("ws == 'WS-3'")
        assert list(ws3["mulai"]) == [pd.Timestamp("2026-02-16")]
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -q -k Periode`
Expected: FAIL — `ImportError: cannot import name 'periode_ws'`.

- [ ] **Step 3: Implementasi minimal** — sesudah `gain_larik`:

```python
def periode_ws(poa: pd.DataFrame, *, min_celah_hari: int = 30) -> pd.DataFrame:
    """Potong rentang tiap WS di celah >= ``min_celah_hari`` hari tanpa data.

    Sensor yang dilepas lalu dipasang ulang (WS-2, Mar-Jun 2026) bisa kembali dengan
    orientasi atau kalibrasi lain; gain atas rentang gabungan mencampur keduanya.
    Hari kosong di awal/akhir rentang bukan periode.
    """
    hari = poa.notna().groupby(poa.index.normalize()).any()
    baris = []
    for ws in poa.columns:
        tgl = hari.index[hari[ws].to_numpy()]
        if not len(tgl):
            continue
        # N hari kosong di antara dua hari berdata = selisih tanggal N + 1 hari.
        putus = np.flatnonzero(np.diff(tgl.to_numpy()) > np.timedelta64(min_celah_hari, "D"))
        awal, akhir = np.r_[0, putus + 1], np.r_[putus, len(tgl) - 1]
        for i, (a, b) in enumerate(zip(awal, akhir), start=1):
            baris.append({"ws": ws, "periode": i, "mulai": tgl[a], "akhir": tgl[b]})
    return pd.DataFrame(baris, columns=["ws", "periode", "mulai", "akhir"])
```

- [ ] **Step 4: Jalankan, pastikan lolos**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -q`
Expected: semua lolos (25 lama + 3 baru).

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/poa/kalibrasi_silang.py tests/unit/test_poa_kalibrasi_silang.py
git commit -m "feat(poa): periode_ws -- potong rentang WS di celah data >= 30 hari"
```

---

### Task 2: `kalibrasi_per_periode`

**Files:**
- Modify: `pv_pipeline/poa/kalibrasi_silang.py` (tambah fungsi sesudah `sepakati`)
- Test: `tests/unit/test_poa_kalibrasi_silang.py`

**Interfaces:**
- Consumes: `periode_ws` (Task 1); `gain_relatif`, `gain_absolut`, `gain_larik`, `sepakati` (sudah ada).
- Produces: `kalibrasi_per_periode(poa, stabil, rasio, jam_penghalang, poa_cerah, periode, kalibrasi_harian=None,
  wb_to_ws=None, *, min_sampel=200, tol=0.03) -> pd.DataFrame` berkolom `ws, periode, mulai, akhir, gain_rel,
  gain_abs, gain_larik, n_bulan_sah, bergeser, status, usulan, alasan`.

- [ ] **Step 1: Tulis uji yang gagal** — tambahkan `kalibrasi_per_periode` ke impor, lalu kelas ini sesudah
  `TestSepakati`:

```python
class TestKalibrasiPerPeriode:
    def test_sensor_dipasang_ulang_dua_usulan(self):
        """Satu faktor untuk seluruh rentang mencampur dua sensor; per periode masing-masing benar."""
        p = _poa(hari=151, gains=(1.0, 1.0, 1.05, 1.0, 1.0))      # 2026-01-01..2026-05-31
        p.loc["2026-03-01":"2026-04-10", "WS-2"] = np.nan          # 41 hari kosong
        p.loc["2026-04-11":, "WS-2"] *= 0.8                        # kembali 0,8
        stabil = sampel_stabil(p)
        rasio = rasio_ke_median(p, stabil)
        cerah = _poa(hari=151, gains=(1.0,) * 5)["WS-1"]
        h = kalibrasi_per_periode(p, stabil, rasio, penghalang(profil_jam(rasio)), cerah,
                                  periode_ws(p)).set_index(["ws", "periode"])
        assert h.loc[("WS-2", 1), "usulan"] == pytest.approx(1.0, rel=0.01)
        assert h.loc[("WS-2", 2), "usulan"] == pytest.approx(1 / 0.8, rel=0.01)
        assert h.loc[("WS-2", 2), "mulai"] == pd.Timestamp("2026-04-11")
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -q -k PerPeriode`
Expected: FAIL — `ImportError: cannot import name 'kalibrasi_per_periode'`.

- [ ] **Step 3: Implementasi minimal** — sesudah `sepakati`:

```python
KOLOM_PERIODE = ["ws", "periode", "mulai", "akhir", "gain_rel", "gain_abs", "gain_larik", "n_bulan_sah",
                 "bergeser", "status", "usulan", "alasan"]


def kalibrasi_per_periode(poa: pd.DataFrame, stabil: pd.DataFrame, rasio: pd.DataFrame,
                          jam_penghalang: pd.DataFrame, poa_cerah: pd.Series, periode: pd.DataFrame,
                          kalibrasi_harian=None, wb_to_ws=None, *, min_sampel: int = 200,
                          tol: float = 0.03) -> pd.DataFrame:
    """``sepakati`` per (WS, periode); ketiga acuan dihitung pada jendela periode itu.

    ``gain_absolut`` dihitung untuk SEMUA WS di jendela yang sama, supaya normalisasi
    median di ``sepakati`` tidak mencampur waktu.
    """
    baris = []
    for p in periode.itertuples(index=False):
        j = slice(p.mulai, p.akhir + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
        rel = gain_relatif(rasio.loc[j, [p.ws]], jam_penghalang, min_sampel=min_sampel)
        absolut = gain_absolut(poa.loc[j], poa_cerah, stabil.loc[j])
        larik = None
        if kalibrasi_harian is not None:
            tgl = pd.to_datetime(kalibrasi_harian["date"])
            k = kalibrasi_harian[(tgl >= p.mulai) & (tgl <= p.akhir)]
            larik = gain_larik(k, wb_to_ws) if len(k) else None
        s = sepakati(rel, absolut, larik, tol=tol).iloc[0]
        baris.append({"ws": p.ws, "periode": p.periode, "mulai": p.mulai, "akhir": p.akhir,
                      **{k: s[k] for k in KOLOM_PERIODE[4:]}})
    return pd.DataFrame(baris, columns=KOLOM_PERIODE)
```

- [ ] **Step 4: Jalankan, pastikan lolos**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -q`
Expected: semua lolos (29).

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/poa/kalibrasi_silang.py tests/unit/test_poa_kalibrasi_silang.py
git commit -m "feat(poa): kalibrasi_per_periode -- sepakati per (WS, periode)"
```

---

### Task 3: CLI memakai kalibrasi per periode, lalu run nyata

**Files:**
- Modify: `run_poa_cross_calibration.py` (impor, `main`, cetak YAML, docstring)
- Test: `tests/unit/test_poa_kalibrasi_silang.py` (uji CLI yang ada)
- Modify: `docs/superpowers/specs/2026-10-02-kalibrasi-silang-poa-per-periode-design.md` (bagian Hasil)

**Interfaces:**
- Consumes: `periode_ws`, `kalibrasi_per_periode` (Task 1–2).

- [ ] **Step 1: Uji gagal** — di `test_cli_delapan_sheet_usulan_ws2_tanpa_mengubah_config`, sesudah
  `s = x.parse("Kesepakatan").set_index("ws")` tambahkan:

```python
    assert (s["periode"] == 1).all()                                   # tanpa celah: satu periode per WS
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -q -k cli_delapan`
Expected: FAIL — `KeyError: 'periode'`.

- [ ] **Step 3: Implementasi** di `run_poa_cross_calibration.py`:

  Impor: ganti `sepakati` dengan `kalibrasi_per_periode, periode_ws` (urut abjad).

  Di `main`, ganti blok dari `estimator = ...` sampai `sep = sepakati(rel, absolut, larik)` dengan:

```python
    estimator = PvlibClearSkyEstimator.from_geometry_yaml(a.geometry, load_albedo_provider=False)
    cerah = estimator.estimate(poa.index)
    absolut = gain_absolut(poa, cerah, stabil)
    kal = larik = None
    if a.m2f_dir:
        kal = build_daily_calib([load_day(p) for _, p in discover_m2f_xlsx(a.m2f_dir)])
        larik = gain_larik(kal, loader.wb_to_ws)
    sep = kalibrasi_per_periode(poa, stabil, rasio, hal, cerah, periode_ws(poa), kal, loader.wb_to_ws,
                                min_sampel=a.min_sampel)
```

  Di string ambang `Catatan`, tambahkan `"; periode dipotong di celah >= 30 hari"` di akhir.

  Ganti cetak usulan `ws_gain` dengan:

```python
        if len(usul):
            print("  ws_gain_periode:")
            for ws, g in usul.groupby("ws"):
                print(f"    {ws}:")
                for r in g.itertuples():
                    print(f"      - {{mulai: {r.mulai:%Y-%m-%d}, akhir: {r.akhir:%Y-%m-%d}, gain: {r.usulan:.3f}}}")
```

  Docstring modul: tambahkan kalimat "Gain dan kesepakatan dihitung per (WS, periode); periode dipotong di celah data
  >= 30 hari (docs/superpowers/specs/2026-10-02-kalibrasi-silang-poa-per-periode-design.md)." dan ganti
  `pyranometer.ws_gain` dengan `pyranometer.ws_gain_periode`.

- [ ] **Step 4: Jalankan uji modul lalu suite lengkap**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -q` → semua lolos (29).
Run: `python -m pytest -q -p no:cacheprovider` → lolos, skip hanya yang lama (1).

- [ ] **Step 5: Commit kode**

```bash
git add run_poa_cross_calibration.py tests/unit/test_poa_kalibrasi_silang.py
git commit -m "feat(poa): CLI kalibrasi silang per periode -- Kesepakatan per (WS, periode)"
```

- [ ] **Step 6: Run nyata**

```bash
python run_poa_cross_calibration.py --raw-root "F:/Downloads part 2" --m2f-dir "F:/Downloads part 2/cek pv/m2f" --akhir 2026-08-31
```

Expected: WS-2 punya ≥ 2 periode (batas 1 Mar – 9 Jun 2026); periode sesudah celah tidak memakai bulan pra-celah.
Catat tabel `Kesepakatan` di bagian "Hasil" spesifikasi (tambahkan bagian itu), termasuk usulan yang muncul dan
penilaian kewajarannya terhadap temuan lapangan yang belum ada.

- [ ] **Step 7: Commit hasil**

```bash
git add docs/superpowers/specs/2026-10-02-kalibrasi-silang-poa-per-periode-design.md
git commit -m "docs(spec): hasil kalibrasi silang POA per periode 2025-01..2026-08"
```
