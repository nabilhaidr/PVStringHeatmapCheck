# Pembanding Tetap, Usulan Titik Ubah, Mutu Data — Rencana Implementasi

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alat kalibrasi silang POA tahan terhadap stasiun bermasalah: acuan tetap, kandidat lompatan tanpa celah, dan
mutu data per stasiun di setiap run.

**Architecture:** Fungsi murni baru/diperluas di `pv_pipeline/poa/kalibrasi_silang.py`; CLI
`run_poa_cross_calibration.py` meneruskan `--pembanding`, menulis sheet `TitikUbah`, dan memperluas `Catatan`.

**Tech Stack:** Python, pandas, numpy, pytest. Spesifikasi:
`docs/superpowers/specs/2026-10-02-kalibrasi-silang-poa-pembanding-titik-ubah-mutu-design.md`.

## Global Constraints

- Default tanpa `--pembanding` = perilaku sekarang (semua WS lain).
- Titik ubah: jendela 14 hari, ambang 5 %, min 10 hari per sisi; hanya disarankan, tidak diterapkan.
- Uji hanya memakai data sintetis. Suite lengkap dijalankan sendirian (memori).

---

### Task 1: Pembanding tetap

**Files:** `pv_pipeline/poa/kalibrasi_silang.py`, `run_poa_cross_calibration.py`,
`tests/unit/test_poa_kalibrasi_silang.py`.

- [ ] **Step 1: Uji gagal** (kelas baru `TestPembanding`):

```python
class TestPembanding:
    def test_acuan_tetap_meredam_ayunan_palsu(self):
        """WS-2 hilang lalu kembali bias: median 'semua WS lain' bergeser, pembanding tetap tidak."""
        p = _poa(hari=120, gains=(0.95, 1.0, 0.95, 1.0, 1.05))
        p.loc["2026-02-01":"2026-02-28", "WS-2"] = np.nan
        p.loc["2026-03-01":, "WS-2"] *= 1.12
        st = sampel_stabil(p)
        semua = gain_relatif(rasio_ke_median(p, st), pd.DataFrame(columns=["ws", "jam"])).set_index("ws")
        assert bool(semua.loc["WS-4", "bergeser"])                      # prasyarat: skenario memang mengayun
        tetap = gain_relatif(rasio_ke_median(p, st, pembanding=["WS-3", "WS-4", "WS-5"]),
                             pd.DataFrame(columns=["ws", "jam"])).set_index("ws")
        assert not bool(tetap.loc["WS-4", "bergeser"]) and tetap.loc["WS-4", "ayunan_bulanan"] < 0.01

    def test_sepakati_normalisasi_abs_ke_pembanding(self):
        absolut = pd.DataFrame({"ws": ["WS-1", "WS-2", "WS-3", "WS-4", "WS-5"],
                                "gain": [1.1, 1.2, 0.98, 1.0, 1.02], "n": 500})
        s = sepakati(_rel(), absolut, None, pembanding=["WS-3", "WS-4", "WS-5"]).set_index("ws")
        assert s.loc["WS-4", "gain_abs"] == pytest.approx(1.0)
```

- [ ] **Step 2:** jalankan → FAIL (`unexpected keyword argument 'pembanding'`).
- [ ] **Step 3: Implementasi:**

```python
def _acuan_ws(kolom, ws, pembanding=None) -> list:
    """Stasiun acuan untuk ``ws``: ``pembanding`` tanpa ws itu sendiri, atau semua WS lain."""
    return [c for c in (pembanding or kolom) if c != ws and c in kolom]
```

  - `rasio_ke_median(..., pembanding=None)`: `lain = p[_acuan_ws(p.columns, ws, pembanding)]`.
  - `rasio_cerah(..., pembanding=None)`: idem.
  - `sepakati(..., pembanding=None)`: `a = a / (a[a.index.isin(pembanding)].median() if pembanding else a.median())`.
  - `kalibrasi_per_periode(..., pembanding=None)`: `sepakati(..., pembanding=pembanding)`.
  - CLI: `--pembanding` (string dipisah koma; kosong = None) → keempat pemanggil; `Catatan` butir "pembanding"
    (`", ".join(pemb)` atau "semua WS lain").
- [ ] **Step 4:** uji modul lolos. **Step 5:** commit `feat(poa): pembanding tetap (--pembanding)`.

### Task 2: Usulan titik ubah

**Files:** sama.

- [ ] **Step 1: Uji gagal** (`TestTitikUbah`):

```python
class TestTitikUbah:
    def test_lompatan_tanpa_celah_disarankan(self):
        """WS-3 ~10 Agu 2026: lompatan tanpa celah harus muncul sebagai kandidat --batas."""
        p = _poa()
        p.loc["2026-02-15":, "WS-2"] *= 0.9
        tu = titik_ubah(rasio_harian(p), periode_ws(p))
        assert list(zip(tu["ws"], tu["tanggal"])) == [("WS-2", pd.Timestamp("2026-02-15"))]
        assert tu["lompatan"].iloc[0] == pytest.approx(-0.10, abs=0.005)

    def test_tanpa_lompatan_kosong(self):
        assert titik_ubah(rasio_harian(_poa()), periode_ws(_poa())).empty

    def test_lompatan_di_batas_celah_sudah_periode(self):
        p = _poa(hari=120)
        p.loc["2026-02-01":"2026-03-12", "WS-2"] = np.nan
        p.loc["2026-03-13":, "WS-2"] *= 0.9
        assert titik_ubah(rasio_harian(p), periode_ws(p)).empty
```

- [ ] **Step 2:** FAIL (import). **Step 3: Implementasi** `rasio_harian` dan `titik_ubah` sesuai spesifikasi:

```python
def rasio_harian(poa, *, pembanding=None, jam=("09:00", "15:00"), min_sampel=40) -> pd.DataFrame:
    p = poa.where((poa > 0.0) & (poa <= POA_MAKS))
    p = p.iloc[p.index.indexer_between_time(*jam)]
    hasil = {}
    for ws in p.columns:
        lain = p[_acuan_ws(p.columns, ws, pembanding)]
        acuan = lain.median(axis=1).where(lain.notna().sum(axis=1) >= 2)
        ada = p[ws].notna() & acuan.notna()
        hasil[ws] = (p[ws].where(ada).resample("D").sum(min_count=min_sampel)
                     / acuan.where(ada).resample("D").sum(min_count=min_sampel))
    return pd.DataFrame(hasil)


def titik_ubah(rasio_hari, periode, *, jendela_hari=14, ambang=0.05, min_hari=10) -> pd.DataFrame:
    w = pd.Timedelta(days=jendela_hari)
    baris = []
    for p in periode.itertuples(index=False):
        r = rasio_hari.loc[p.mulai:p.akhir, p.ws].dropna()
        nilai = {}
        for d in r.index:
            a, b = r[(r.index >= d - w) & (r.index < d)], r[(r.index >= d) & (r.index < d + w)]
            if len(a) >= min_hari and len(b) >= min_hari:
                nilai[d] = (a.median(), b.median())
        lompat = pd.Series({d: b / a - 1.0 for d, (a, b) in nilai.items()}, dtype=float)
        calon = lompat[lompat.abs() >= ambang]
        if calon.empty:
            continue
        kelompok = (calon.index.to_series().diff() > w).cumsum()
        for _, g in calon.groupby(kelompok.to_numpy()):
            puncak = g.index[np.isclose(g.abs(), g.abs().max(), rtol=0.0, atol=1e-9)]
            d = puncak[len(puncak) // 2]
            baris.append({"ws": p.ws, "periode": p.periode, "tanggal": d, "sebelum": nilai[d][0],
                          "sesudah": nilai[d][1], "lompatan": float(lompat[d])})
    return pd.DataFrame(baris, columns=["ws", "periode", "tanggal", "sebelum", "sesudah", "lompatan"])
```

  - CLI: `per = periode_ws(poa, batas=batas)`; `tu = titik_ubah(rasio_harian(poa, pembanding=pemb), per)`; sheet
    `TitikUbah` sebelum `Catatan`; cetak `# kandidat titik ubah (periksa dulu; bukan batas otomatis):` lalu
    `--batas WS-n:YYYY-MM-DD` per baris. Uji CLI lama: daftar sheet jadi sembilan.
- [ ] **Step 4:** uji modul lolos. **Step 5:** commit `feat(poa): usulan titik ubah tanpa celah (sheet TitikUbah)`.

### Task 3: Mutu data di `Catatan`

- [ ] **Step 1: Uji gagal:**

```python
class TestMutuData:
    def test_nol_saat_cerah_dan_hari_kosong(self):
        """Logger mati (WS-1 Okt 2025-Mei 2026) dan hari kosong harus terlihat di setiap run."""
        p = _poa(hari=20)
        p.loc["2026-01-05", "WS-1"] = 0.0
        p.loc["2026-01-10", "WS-3"] = np.nan
        m = mutu_data(p).set_index("ws")
        assert m.loc["WS-1", "nol_saat_cerah"] == 49                   # 10:00-14:00 inklusif, langit cerah
        assert m.loc["WS-3", "hari_kosong"] == 1 and m.loc["WS-4", "nol_saat_cerah"] == 0
```

  dan uji CLI: `Catatan` memuat butir "sampel nol saat WS lain cerah (10-14, > 500 W/m2) per WS" berisi `"WS-1: 49"`
  untuk POA sintetis yang sama.
- [ ] **Step 3: Implementasi:**

```python
def mutu_data(poa, *, cerah_min=500.0, jam=("10:00", "14:00")) -> pd.DataFrame:
    hari = poa.notna().groupby(poa.index.normalize()).any()
    siang = poa.iloc[poa.index.indexer_between_time(*jam)]
    baris = []
    for ws in poa.columns:
        lain = siang.drop(columns=ws).median(axis=1)
        baris.append({"ws": ws, "hari_kosong": int((~hari[ws]).sum()),
                      "nol_saat_cerah": int(((siang[ws] == 0.0) & (lain > cerah_min)).sum()),
                      "galat": int(((poa[ws] < 0.0) | (poa[ws] > POA_MAKS)).sum())})
    return pd.DataFrame(baris, columns=["ws", "hari_kosong", "nol_saat_cerah", "galat"])
```

  CLI: baris galat total diganti tiga baris per WS (format `"WS-1: 49; WS-2: 0; …"`); variabel `galat` lama dihapus.
- [ ] **Step 4:** uji modul + suite lengkap (sendirian). **Step 5:** commit `feat(poa): mutu data per WS di Catatan`.

### Task 4: Run nyata dan catat

```bash
python run_poa_cross_calibration.py --raw-root "F:/Downloads part 2" --m2f-dir "F:/Downloads part 2/cek pv/m2f" --akhir 2026-08-31 --pembanding WS-3,WS-4,WS-5
```

Harapan: kandidat WS-3 sekitar 2026-08-10; WS-4/WS-5 tidak lagi "bergeser" karena acuan. Catat `Kesepakatan`,
`TitikUbah`, dan mutu data di bagian "Hasil" spesifikasi; commit.
