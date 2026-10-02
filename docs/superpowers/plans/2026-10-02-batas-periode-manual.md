# Batas Periode Manual — Rencana Implementasi

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Periode kalibrasi silang POA bisa dipotong di tanggal yang diberikan pengguna, tidak hanya di celah data.

**Architecture:** `periode_ws` mendapat parameter `batas={ws: [tanggal]}` yang digabung dengan potongan celah. CLI
`run_poa_cross_calibration.py` meneruskan `--batas WS-n:YYYY-MM-DD` (boleh diulang) dan mencatatnya di `Catatan`.

**Tech Stack:** Python, pandas, numpy, pytest. Spesifikasi:
`docs/superpowers/specs/2026-10-02-kalibrasi-silang-poa-per-periode-design.md`, bagian "Tambahan: batas periode manual".

## Global Constraints

- Tanggal batas = hari pertama periode baru; batas di luar rentang data atau di dalam celah tidak membuat periode kosong.
- Sumber batas hanya flag CLI (tanpa berkas config).
- Uji hanya memakai data sintetis.

---

### Task 1: `periode_ws(..., batas=...)`

**Files:**
- Modify: `pv_pipeline/poa/kalibrasi_silang.py` (`periode_ws`)
- Test: `tests/unit/test_poa_kalibrasi_silang.py` (`TestPeriodeWs`, `TestKalibrasiPerPeriode`)

**Interfaces:**
- Produces: `periode_ws(poa, *, min_celah_hari: int = 30, batas: dict | None = None) -> pd.DataFrame` (kolom tetap
  `ws, periode, mulai, akhir`).

- [ ] **Step 1: Uji gagal** — di `TestPeriodeWs`:

```python
    def test_batas_manual_memotong_tanpa_celah(self):
        """WS-3 ~10 Agu 2026: lompatan tanpa celah data (kubah dibersihkan?); batas manual memotong periode."""
        per = periode_ws(_poa(hari=120), batas={"WS-2": ["2026-02-15"]})
        ws2 = per[per["ws"] == "WS-2"]
        assert list(ws2["mulai"]) == [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-02-15")]
        assert list(ws2["akhir"]) == [pd.Timestamp("2026-02-14"), pd.Timestamp("2026-04-30")]
        assert (per["ws"] == "WS-1").sum() == 1

    def test_batas_di_dalam_celah_tidak_menambah_periode(self):
        p = _poa(hari=120)
        p.loc["2026-02-01":"2026-03-12", "WS-2"] = np.nan
        assert (periode_ws(p, batas={"WS-2": ["2026-02-20"]})["ws"] == "WS-2").sum() == 2
```

  dan di `TestKalibrasiPerPeriode`:

```python
    def test_lompatan_tanpa_celah_dengan_batas_manual(self):
        """Tanpa batas, satu periode mencampur sensor sebelum dan sesudah pemeliharaan."""
        p = _poa(hari=151, gains=(1.0, 1.0, 1.05, 1.0, 1.0))
        p.loc["2026-03-15":, "WS-2"] *= 0.8
        stabil = sampel_stabil(p)
        rasio = rasio_ke_median(p, stabil)
        cerah = _poa(hari=151, gains=(1.0,) * 5)["WS-1"]
        h = kalibrasi_per_periode(p, stabil, rasio, penghalang(profil_jam(rasio)), cerah,
                                  periode_ws(p, batas={"WS-2": ["2026-03-15"]})).set_index(["ws", "periode"])
        assert h.loc[("WS-2", 1), "usulan"] == pytest.approx(1.0, rel=0.01)
        assert h.loc[("WS-2", 2), "usulan"] == pytest.approx(1 / 0.8, rel=0.01)
```

- [ ] **Step 2:** `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -q -k batas` → FAIL (`unexpected keyword
  argument 'batas'`).

- [ ] **Step 3: Implementasi** — di `periode_ws`, tambah `batas: dict | None = None` dan ganti perhitungan `putus`:

```python
        putus = set(np.flatnonzero(np.diff(tgl.to_numpy()) > np.timedelta64(min_celah_hari, "D")).tolist())
        for d in (batas or {}).get(ws, []):
            # Batas = hari pertama periode baru; di luar data atau di dalam celah tidak menambah potongan.
            k = int(tgl.searchsorted(pd.Timestamp(d).normalize()))
            if 0 < k < len(tgl):
                putus.add(k - 1)
        putus = np.array(sorted(putus), dtype=int)
```

  Docstring: tambahkan kalimat tentang `batas`.

- [ ] **Step 4:** `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -q` → semua lolos.

- [ ] **Step 5: Commit** — `feat(poa): periode_ws batas manual -- potong periode tanpa celah data`.

### Task 2: CLI `--batas`

**Files:**
- Modify: `run_poa_cross_calibration.py`
- Test: `tests/unit/test_poa_kalibrasi_silang.py`

- [ ] **Step 1: Uji gagal:**

```python
def test_cli_batas_manual_memotong_periode(tmp_path, monkeypatch):
    """Tanggal pemeliharaan tanpa celah data (WS-3 ~10 Agu 2026) masuk lewat --batas."""
    _pasang(monkeypatch, _poa().assign(avg=0.0))
    cli.main(["--mulai", "2026-01-01", "--akhir", "2026-03-31", "--output-dir", str(tmp_path),
              "--batas", "WS-3:2026-02-15"])
    x = pd.ExcelFile(tmp_path / "poa_cross_calibration_20260101_20260331.xlsx")
    k = x.parse("Kesepakatan")
    assert list(k.loc[k["ws"] == "WS-3", "periode"]) == [1, 2]
    assert x.parse("Catatan").set_index("butir").loc["batas periode manual", "nilai"] == "WS-3:2026-02-15"
```

- [ ] **Step 2:** jalankan → FAIL (`unrecognized arguments: --batas`).

- [ ] **Step 3: Implementasi:**

```python
    ap.add_argument("--batas", action="append", default=[], metavar="WS-n:YYYY-MM-DD",
                    help="hari pertama periode baru tanpa celah data (boleh diulang)")
    ...
    batas: dict = {}
    for b in a.batas:
        ws, tgl = b.split(":", 1)
        batas.setdefault(ws, []).append(tgl)
    sep = kalibrasi_per_periode(poa, stabil, rasio, hal, cerah, periode_ws(poa, batas=batas), kal, loader.wb_to_ws,
                                min_sampel=a.min_sampel)
```

  `Catatan`: tambah butir `"batas periode manual"` dengan nilai `"; ".join(a.batas) or "-"`. Docstring Usage: tambah
  `[--batas WS-3:2026-08-10]`.

- [ ] **Step 4:** uji modul lolos; suite lengkap lolos (jalankan sendirian — memori).

- [ ] **Step 5: Commit** — `feat(poa): CLI --batas untuk batas periode manual`.

- [ ] **Step 6: Run nyata** dengan `--batas WS-3:2026-08-10 --akhir 2026-08-31`; catat baris WS-3 di bagian Hasil
  spesifikasi; commit.
