# Kalibrasi dc_derate_per_wb Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dari 42 workbook M2f harian, putuskan per WB dengan data apakah `m2f.dc_derate_per_wb` cukup satu konstanta (dan berapa nilainya) atau butuh model langit.

**Architecture:** Langkah 0 (skrip sekali pakai) mengukur kepekaan `measured_ratio` terhadap offset POA, supaya data versi campur sah dipakai. Fungsi murni di `pv_pipeline/m2f/derate_calibration.py` menghitung indeks langit/debu, regresi per WB, keputusan, dan validasi. CLI tipis `run_derate_calibration.py` merangkai rasio dari workbook, POA dan langit cerah, serta hujan, lalu menulis workbook hasil. Config tidak diubah.

**Tech Stack:** Python 3.13, numpy, pandas, PyYAML, matplotlib (sudah dipakai repo), pvlib (lewat `PvlibClearSkyEstimator`); pytest.

**Spesifikasi:** `docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md`.

## Global Constraints

- Tidak ada dependensi baru. Regresi memakai numpy (tanpa statsmodels).
- Uji hanya data sintetis; tidak membaca berkas di F: atau `coba/`.
- `config/m2_config.yaml` TIDAK diubah oleh kode maupun oleh rencana ini.
- Ambang (dari spesifikasi):
  - keputusan: ayunan langit 0,03; |t| = 2,0; minimal 20 hari sah per WB;
  - saringan: `n_calib_string_days` ≥ 100, `poa_kosong` ≤ 0,5;
  - hujan pembersih ≥ 5 mm, `hari_sejak_hujan` maks 30;
  - elevasi matahari minimal 15° untuk Kt dan `poa_kosong`.
- Model per WB: `rasio = a + b·(kt − kt_ref) + c·(mulus − mulus_ref) + e·hari_sejak_hujan`, dengan `kt_ref` dan `mulus_ref` = median.
- Keluaran diberi label "satu musim" bila rentang data < 3 bulan.
- Gaya repo: nama dan docstring berbahasa Indonesia, alasan di docstring.
- Uji di `tests/unit/`, dijalankan dari akar repo: `python -m pytest tests/unit/test_derate_calibration.py`.
- **Penyimpangan dari spesifikasi** (dicatat di spesifikasi pada Task 1):
  - CLI mendapat `--raw-root`, karena berkas POA ada di `F:\Downloads part 2\raw data input`, bukan di `raw data input` repo;
  - `validasi` menerima `fits`, karena butuh koefisien `e` per WB.

## Struktur berkas

| Berkas | Tanggung jawab |
|---|---|
| Create `pv_pipeline/m2f/derate_calibration.py` | `hari_sejak_hujan`, `kt_poa`, `porsi_kosong`, `fit_wb`, `putuskan`, `validasi` |
| Create `run_derate_calibration.py` | CLI: workbook → tabel harian → fit → keputusan → xlsx/PNG + potongan YAML |
| Create `tests/unit/test_derate_calibration.py` | Uji fungsi murni + CLI |
| Modify `docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md` | Bagian "Hasil" + dua penyimpangan di atas |
| Scratch (tidak di-commit) `coba/langkah0_offset_20260701.py` | Langkah 0 |

---

### Task 1: Langkah 0 — kepekaan `measured_ratio` terhadap offset POA

**Files:**
- Create (scratch, tidak di-commit; `coba/` di-gitignore): `coba/langkah0_offset_20260701.py`
- Modify: `docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md` (bagian "Hasil", baris CLI, dan signature `validasi`)

**Interfaces:**
- Produces: keputusan memakai 42 hari atau hanya 22 hari (`--hanya-sesudah "2026-09-29 22:13"` di Task 5).

- [ ] **Step 1: Tulis skrip Langkah 0**

```python
"""Langkah 0: kepekaan measured_ratio 2026-07-01 terhadap offset POA 0 vs 5 menit.

Sekali pakai (coba/, di-gitignore). Jalankan dari akar repo. Pola sama dengan
kalibrasi lokal 27 Sep: geometri sementara dengan path diarahkan ke F:,
providers disuntikkan ke M2fLossAttribution.
"""
import re
import sys
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")

from pv_pipeline.cell_temp import CellTempProvider  # noqa: E402
from pv_pipeline.m2_config import load_m2_config  # noqa: E402
from pv_pipeline.m2f.report import M2fLossAttribution  # noqa: E402
from pv_pipeline.panel_spec import PanelSpec  # noqa: E402
from pv_pipeline.poa.provider import POAProvider  # noqa: E402

SINI = Path(__file__).resolve().parent
EXT = "F:/Downloads part 2/raw data input/"
geo_src = Path("config/site_geometry.yaml").read_text(encoding="utf-8").replace('"raw data input/', f'"{EXT}')

cfg = load_m2_config("config/m2_config.yaml")
cfg["m2f"]["enabled"] = True
cfg["m2f"]["deficit_frames"] = None
cfg["m2f"]["p_loss_by_month"] = {}

df = pd.read_csv("coba/01072026/CSV Export/20260701.csv", low_memory=False)
df["Start Time"] = pd.to_datetime(df["Start Time"], errors="coerce")

hasil = {}
for off in (0, 5):
    geo = SINI / f"geo_offset{off}.yaml"
    geo.write_text(re.sub(r"time_offset_minutes:\s*[-0-9.]+", f"time_offset_minutes: {off}", geo_src),
                   encoding="utf-8")
    providers = {
        "poa": POAProvider.from_yaml(str(geo)),
        "tcell": CellTempProvider.from_geometry_yaml(str(geo)),
        "spec": PanelSpec.from_yaml(cfg["panel"]["spec_path"]),
    }
    M2fLossAttribution._load_providers = staticmethod(lambda config, p=providers: (p, None))
    sm = M2fLossAttribution()
    sm.run(df.copy(), cfg)
    hasil[f"offset{off}"] = sm.artifacts["M2f_BaselineCalib"].set_index("wb_id")["measured_ratio"]

drive = pd.read_excel(r"F:\Downloads part 2\cek pv\m2f\m2f_loss_attribution_20260701.xlsx",
                      sheet_name="M2f_BaselineCalib").set_index("wb_id")["measured_ratio"]
t = pd.DataFrame(hasil).assign(drive=drive)
t["selisih_offset"] = (t["offset5"] - t["offset0"]).abs()
t["selisih_drive"] = (t["offset5"] - t["drive"]).abs()
print(t.round(4).to_string())
print(f"median |offset5 - offset0| = {t['selisih_offset'].median():.4f}; "
      f"maks |offset5 - drive| = {t['selisih_drive'].max():.4f}")
```

- [ ] **Step 2: Jalankan dan baca hasilnya**

Run: `python coba/langkah0_offset_20260701.py`
Expected: tabel 10 WB dan dua angka ringkasan. Butuh F: tersambung dan memori bebas ≥ ~2 GB (berkas POA 2026 berukuran 166 MB).

- [ ] **Step 3: Putuskan menurut kriteria spesifikasi**

- **median |offset5 − offset0| < 0,01:** data campur dipakai; Task 5 tanpa `--hanya-sesudah`.
- **≥ 0,01:** Task 5 memakai `--hanya-sesudah "2026-09-29 22:13"` (22 hari).
- **maks |offset5 − drive| ≤ 0,005:** Colab terbukti memakai offset untuk hari itu. Bila tidak, catat sebagai versi tak pasti, dan Task 5 tetap memakai `--hanya-sesudah`.

- [ ] **Step 4: Catat di spesifikasi dan commit**

Di bagian "Hasil" spesifikasi, ganti kalimat "Diisi saat implementasi: ..." dengan tabel 10 WB dari Step 2, kedua angka ringkasan, dan keputusan Step 3.

Di bagian CLI spesifikasi:
- tambahkan `[--raw-root "F:/Downloads part 2"]` pada contoh perintah;
- tambahkan butir: "`--raw-root` diawalkan pada path POA relatif di `site_geometry.yaml` (berkas POA ada di F:)".

Ganti baris signature `validasi(tabel: pd.DataFrame, derate: dict) -> pd.DataFrame` menjadi `validasi(tabel: pd.DataFrame, fits: dict, derate: dict) -> pd.DataFrame` (butuh `e` per WB dari `fits`).

```bash
git add docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md
git commit -m "docs(spec): Langkah 0 kalibrasi derate -- kepekaan offset POA 1 Jul"
```

---

### Task 2: Indeks debu dan langit — `hari_sejak_hujan`, `kt_poa`, `porsi_kosong`

**Files:**
- Create: `pv_pipeline/m2f/derate_calibration.py`
- Test: `tests/unit/test_derate_calibration.py`

**Interfaces:**
- Produces:
  - `hari_sejak_hujan(hujan: pd.Series, hari: pd.DatetimeIndex, *, ambang_mm: float = 5.0, maks: int = 30) -> pd.Series` (int, berindeks `hari`)
  - `kt_poa(poa: pd.Series, poa_cerah: pd.Series, elevasi: pd.Series, *, min_elev: float = 15.0) -> float`
  - `porsi_kosong(poa: pd.Series, elevasi: pd.Series, *, min_elev: float = 15.0) -> float`
  - Ketiga deret `kt_poa` / `porsi_kosong` berindeks sama (urutan sampel sama).

- [ ] **Step 1: Write the failing tests** (`tests/unit/test_derate_calibration.py`)

```python
"""Uji kalibrasi dc_derate_per_wb (docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md)."""
import math

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.derate_calibration import hari_sejak_hujan, kt_poa, porsi_kosong

_IDX = pd.date_range("2026-06-01 06:00", "2026-06-01 18:00", freq="5min")


def _hujan(nilai: dict) -> pd.Series:
    s = pd.Series(nilai, dtype=float)
    s.index = pd.to_datetime(s.index)
    return s


def _elev() -> pd.Series:
    """Matahari naik dari 0 ke 60 derajat lalu turun: pagi/sore di bawah 15 derajat."""
    jam = (_IDX.hour + _IDX.minute / 60.0).to_numpy()
    return pd.Series(np.clip(60.0 * np.sin(np.pi * (jam - 6.0) / 12.0), 0.0, None), index=_IDX)


class TestHariSejakHujan:
    def test_hujan_hari_itu_nol_lalu_bertambah(self):
        """Debu menumpuk sejak hujan pembersih terakhir; hujan kecil tidak membersihkan."""
        h = _hujan({"2026-06-01": 0.0, "2026-06-02": 12.0, "2026-06-03": 0.0, "2026-06-04": 1.0})
        r = hari_sejak_hujan(h, pd.DatetimeIndex(["2026-06-02", "2026-06-03", "2026-06-04"]))
        assert list(r) == [0, 1, 2]

    def test_ambang_tepat_5_mm_terhitung(self):
        h = _hujan({"2026-06-01": 5.0, "2026-06-02": 4.9})
        assert list(hari_sejak_hujan(h, pd.DatetimeIndex(["2026-06-03"]))) == [2]

    def test_dibatasi_maks(self):
        h = _hujan({"2026-01-01": 30.0})
        assert list(hari_sejak_hujan(h, pd.DatetimeIndex(["2026-06-01"]), maks=30)) == [30]

    def test_tanpa_hujan_sebelumnya_maks(self):
        """Hujan SESUDAH hari itu tidak boleh dihitung membersihkan."""
        h = _hujan({"2026-06-05": 20.0})
        assert list(hari_sejak_hujan(h, pd.DatetimeIndex(["2026-06-01"]))) == [30]


class TestKtPoa:
    def test_poa_tujuh_persepuluh_langit_cerah(self):
        cerah = pd.Series(900.0, index=_IDX)
        assert kt_poa(0.7 * cerah, cerah, _elev()) == pytest.approx(0.7)

    def test_sampel_elevasi_rendah_tidak_dihitung(self):
        """POA pagi/sore dangkal didominasi sudut datang dan bayangan antar-baris, bukan langit."""
        e = _elev()
        cerah = pd.Series(900.0, index=_IDX)
        poa = (0.7 * cerah).where(e > 15.0, 5000.0)
        assert kt_poa(poa, cerah, e) == pytest.approx(0.7)

    def test_nan_diabaikan(self):
        e = _elev()
        cerah = pd.Series(900.0, index=_IDX)
        poa = 0.7 * cerah
        poa.iloc[60:80] = np.nan
        assert kt_poa(poa, cerah, e) == pytest.approx(0.7)


class TestPorsiKosong:
    def test_pecahan_nan_pada_siang(self):
        """WB yang POA-nya diisi dari rata-rata stasiun cuaca lain tidak punya indeks langit sendiri."""
        e = _elev()
        poa = pd.Series(500.0, index=_IDX)
        siang = np.flatnonzero(e.to_numpy() > 15.0)
        poa.iloc[siang[: len(siang) // 4]] = np.nan
        assert porsi_kosong(poa, e) == pytest.approx((len(siang) // 4) / len(siang))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_derate_calibration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pv_pipeline.m2f.derate_calibration'`.

- [ ] **Step 3: Write minimal implementation** (`pv_pipeline/m2f/derate_calibration.py`)

```python
"""Kalibrasi ``m2f.dc_derate_per_wb`` dari banyak hari -- rasio harian vs langit dan debu.

Rancangan: ``docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md``.

``measured_ratio`` (sheet ``M2f_BaselineCalib``) ikut langit: ~1,0 saat
mendung, 0,81-0,94 saat cerah-kering. Satu konstanta per WB hanya sah bila
rasio tidak bergantung pada langit setelah efek debu dipisahkan. Model per WB::

    rasio = a + b (kt - kt_ref) + c (mulus - mulus_ref) + e hari_sejak_hujan

``e`` memisahkan debu (hari sejak hujan >= 5 mm) dari langit; tanpa kovariat
itu hari pasca-hujan, yang biasanya juga berawan, menyatukan keduanya.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

KOLOM_FIT = ("rasio", "kt", "mulus", "hari_sejak_hujan")


def hari_sejak_hujan(hujan: pd.Series, hari: pd.DatetimeIndex, *, ambang_mm: float = 5.0,
                     maks: int = 30) -> pd.Series:
    """Hari sejak hujan >= ``ambang_mm`` terakhir (pada atau sebelum hari itu), dibatasi ``maks``.

    ``hujan``: mm per hari, berindeks tanggal. Tanpa hujan pembersih sebelumnya -> ``maks``.
    """
    basah = pd.DatetimeIndex(hujan.index[hujan.to_numpy(dtype=float) >= ambang_mm]).normalize()
    hasil = []
    for h in pd.DatetimeIndex(hari).normalize():
        lalu = basah[basah <= h]
        hasil.append(maks if len(lalu) == 0 else min(maks, int((h - lalu.max()).days)))
    return pd.Series(hasil, index=pd.DatetimeIndex(hari), name="hari_sejak_hujan", dtype=int)


def kt_poa(poa: pd.Series, poa_cerah: pd.Series, elevasi: pd.Series, *, min_elev: float = 15.0) -> float:
    """Sigma POA / Sigma POA langit cerah pada sampel elevasi > ``min_elev`` yang keduanya terisi."""
    p, c, e = (np.asarray(x, dtype=float) for x in (poa, poa_cerah, elevasi))
    m = (e > min_elev) & np.isfinite(p) & np.isfinite(c)
    total = c[m].sum()
    return float(p[m].sum() / total) if m.any() and total > 0 else float("nan")


def porsi_kosong(poa: pd.Series, elevasi: pd.Series, *, min_elev: float = 15.0) -> float:
    """Pecahan sampel elevasi > ``min_elev`` yang POA-nya NaN."""
    p, e = np.asarray(poa, dtype=float), np.asarray(elevasi, dtype=float)
    siang = e > min_elev
    return float(np.isnan(p[siang]).mean()) if siang.any() else float("nan")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_derate_calibration.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/m2f/derate_calibration.py tests/unit/test_derate_calibration.py
git commit -m "feat(m2f): indeks debu dan langit untuk kalibrasi derate"
```

---

### Task 3: `fit_wb`, `putuskan`, `validasi`

**Files:**
- Modify: `pv_pipeline/m2f/derate_calibration.py`
- Test: `tests/unit/test_derate_calibration.py`

**Interfaces:**
- Consumes: `KOLOM_FIT` (Task 2).
- Produces:
  - `fit_wb(tabel: pd.DataFrame) -> dict` dengan kunci `n, kt_ref, mulus_ref, iqr_kt, iqr_mulus, a, b, c, e, se_a, se_b, se_c, se_e, t_a, t_b, t_c, t_e, ayunan_langit, rasio_bersih`. `tabel` berkolom `KOLOM_FIT`.
  - `putuskan(fit: dict, *, ambang_ayunan: float = 0.03, ambang_t: float = 2.0, min_hari: int = 20) -> dict` dengan kunci `keputusan` (`"data_kurang" | "tercampur" | "konstanta" | "model_langit"`), `nilai` (float, NaN kecuali konstanta), `alasan` (str).
  - `validasi(tabel: pd.DataFrame, fits: dict, derate: dict) -> pd.DataFrame` dengan kolom `wb_id, tercile_kt, n, sisa_median, beda_antar_tercile`. `tabel` berkolom `wb_id` + `KOLOM_FIT`.

- [ ] **Step 1: Write the failing tests**

Ganti baris impor di kepala `tests/unit/test_derate_calibration.py` menjadi:

```python
from pv_pipeline.m2f.derate_calibration import (
    fit_wb, hari_sejak_hujan, kt_poa, porsi_kosong, putuskan, validasi,
)
```

Lalu tambahkan di akhir berkas:

```python
def _tabel(n=42, *, a=0.9, b=-0.1, c=0.0, e=-0.002, derau=0.002, kt_tetap=False, seed=1):
    rng = np.random.default_rng(seed)
    kt = np.full(n, 0.6) if kt_tetap else rng.uniform(0.3, 0.9, n)
    mulus = rng.uniform(0.0, 0.5, n)
    hsh = rng.integers(0, 20, n).astype(float)
    rasio = (a + b * (kt - np.median(kt)) + c * (mulus - np.median(mulus)) + e * hsh
             + rng.normal(0.0, derau, n))
    return pd.DataFrame({"rasio": rasio, "kt": kt, "mulus": mulus, "hari_sejak_hujan": hsh})


class TestFitWb:
    def test_koefisien_yang_ditanam_kembali(self):
        """Bila regresi tak mengembalikan efek langit/debu yang ditanam, keputusan berikutnya tak bermakna."""
        f = fit_wb(_tabel())
        assert f["n"] == 42
        assert f["b"] == pytest.approx(-0.1, rel=0.1)
        assert f["e"] == pytest.approx(-0.002, rel=0.1)

    def test_rasio_bersih_menghapus_debu(self):
        """Derate = rasio pada kondisi baru hujan; soiling punya estimator sendiri di M2f."""
        f = fit_wb(_tabel(kt_tetap=True, b=0.0))
        assert f["rasio_bersih"] == pytest.approx(0.9, abs=0.002)

    def test_data_terlalu_sedikit_koefisien_nan(self):
        f = fit_wb(_tabel(n=3))
        assert f["n"] == 3 and math.isnan(f["b"]) and math.isnan(f["rasio_bersih"])


def _fit(**ubah):
    f = {"n": 42, "a": 0.9, "b": -0.05, "c": 0.0, "e": -0.001, "t_b": -5.0, "t_c": 0.5, "t_e": -3.0,
         "ayunan_langit": 0.05, "rasio_bersih": 0.91}
    f.update(ubah)
    return f


class TestPutuskan:
    def test_data_kurang(self):
        r = putuskan(_fit(n=19))
        assert r["keputusan"] == "data_kurang" and math.isnan(r["nilai"])

    def test_debu_positif_bermakna_berarti_tercampur(self):
        """Rasio yang NAIK seiring debu menumpuk mustahil secara fisika: langit dan debu tak terpisah."""
        assert putuskan(_fit(e=0.002, t_e=2.0))["keputusan"] == "tercampur"

    def test_debu_positif_tak_bermakna_bukan_tercampur(self):
        assert putuskan(_fit(e=0.002, t_e=1.9))["keputusan"] != "tercampur"

    @pytest.mark.parametrize("ayunan, harap", [(0.0299, "konstanta"), (0.03, "model_langit")])
    def test_ambang_ayunan_langit(self, ayunan, harap):
        assert putuskan(_fit(ayunan_langit=ayunan))["keputusan"] == harap

    @pytest.mark.parametrize("t_b, harap", [(-1.99, "konstanta"), (-2.0, "model_langit")])
    def test_ambang_t_langit(self, t_b, harap):
        """Ayunan besar tetapi tak bermakna secara statistik tetap konstanta."""
        assert putuskan(_fit(t_b=t_b))["keputusan"] == harap

    def test_konstanta_bernilai_rasio_bersih(self):
        r = putuskan(_fit(ayunan_langit=0.01))
        assert r["keputusan"] == "konstanta" and r["nilai"] == 0.91


class TestValidasi:
    def test_derate_benar_sisa_nol_di_tiap_tercile(self):
        """Konstanta yang benar tidak meninggalkan tren langit pada sisa."""
        t = _tabel(b=0.0, derau=0.0005).assign(wb_id="WB03")
        fits = {"WB03": fit_wb(t)}
        v = validasi(t, fits, {"WB03": fits["WB03"]["rasio_bersih"]})
        assert set(v["tercile_kt"]) == {"rendah", "sedang", "tinggi"}
        assert v["sisa_median"].abs().max() < 0.005
        assert v["beda_antar_tercile"].max() < 0.005
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_derate_calibration.py -v`
Expected: FAIL — `ImportError: cannot import name 'fit_wb'`.

- [ ] **Step 3: Write minimal implementation** (tambahkan ke `pv_pipeline/m2f/derate_calibration.py`)

```python
_NAN = float("nan")


def fit_wb(tabel: pd.DataFrame) -> dict:
    """Regresi OLS ``rasio ~ 1 + (kt - kt_ref) + (mulus - mulus_ref) + hari_sejak_hujan`` satu WB.

    Galat baku dari (X'X)^-1 s^2 (tanpa statsmodels). ``rasio_bersih`` = median
    rasio yang dikoreksi ke hari baru hujan: kandidat derate.
    """
    t = tabel.dropna(subset=list(KOLOM_FIT))
    n = len(t)
    kt, mulus = t["kt"].to_numpy(float), t["mulus"].to_numpy(float)
    hasil = {"n": n, "kt_ref": float(np.median(kt)) if n else _NAN,
             "mulus_ref": float(np.median(mulus)) if n else _NAN,
             "iqr_kt": float(np.subtract(*np.percentile(kt, [75, 25]))) if n else _NAN,
             "iqr_mulus": float(np.subtract(*np.percentile(mulus, [75, 25]))) if n else _NAN}
    nama = ("a", "b", "c", "e")
    if n <= len(nama):
        hasil.update({k: _NAN for k in nama}, **{f"se_{k}": _NAN for k in nama}, **{f"t_{k}": _NAN for k in nama},
                     ayunan_langit=_NAN, rasio_bersih=_NAN)
        return hasil
    hsh = t["hari_sejak_hujan"].to_numpy(float)
    X = np.column_stack([np.ones(n), kt - hasil["kt_ref"], mulus - hasil["mulus_ref"], hsh])
    y = t["rasio"].to_numpy(float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    sisa = y - X @ beta
    s2 = float(sisa @ sisa) / (n - X.shape[1])
    se = np.sqrt(np.clip(np.diag(np.linalg.pinv(X.T @ X)) * s2, 0.0, None))
    for k, b, s in zip(nama, beta, se):
        hasil[k], hasil[f"se_{k}"] = float(b), float(s)
        hasil[f"t_{k}"] = float(b / s) if s > 0 else (0.0 if b == 0 else float(np.sign(b)) * np.inf)
    hasil["ayunan_langit"] = abs(hasil["b"]) * hasil["iqr_kt"] + abs(hasil["c"]) * hasil["iqr_mulus"]
    hasil["rasio_bersih"] = float(np.median(y - hasil["e"] * hsh))
    return hasil


def putuskan(fit: dict, *, ambang_ayunan: float = 0.03, ambang_t: float = 2.0, min_hari: int = 20) -> dict:
    """Konstanta per WB atau model langit -- aturan tertulis di spesifikasi."""
    if fit["n"] < min_hari:
        return {"keputusan": "data_kurang", "nilai": _NAN, "alasan": f"{fit['n']} hari sah < {min_hari}"}
    if fit["e"] > 0 and fit["t_e"] >= ambang_t:
        return {"keputusan": "tercampur", "nilai": _NAN,
                "alasan": f"rasio naik seiring debu (e = {fit['e']:.4f}, t = {fit['t_e']:.1f})"}
    if fit["ayunan_langit"] < ambang_ayunan or (abs(fit["t_b"]) < ambang_t and abs(fit["t_c"]) < ambang_t):
        return {"keputusan": "konstanta", "nilai": float(fit["rasio_bersih"]),
                "alasan": f"ayunan langit {fit['ayunan_langit']:.3f}; t_b {fit['t_b']:.1f}, t_c {fit['t_c']:.1f}"}
    return {"keputusan": "model_langit", "nilai": _NAN,
            "alasan": f"ayunan langit {fit['ayunan_langit']:.3f} >= {ambang_ayunan}; "
                      f"t_b {fit['t_b']:.1f}, t_c {fit['t_c']:.1f}"}


def validasi(tabel: pd.DataFrame, fits: dict, derate: dict) -> pd.DataFrame:
    """Sisa median ``1 - (rasio - e hari_sejak_hujan)/derate`` per WB dan tercile kt.

    Konstanta yang benar: beda median antar-tercile <= 0,03.
    """
    nama = np.array(["rendah", "sedang", "tinggi"])
    baris = []
    for wb, d in derate.items():
        t = tabel[tabel["wb_id"] == wb].dropna(subset=list(KOLOM_FIT))
        if t.empty or not np.isfinite(d):
            continue
        sisa = 1.0 - (t["rasio"] - fits[wb]["e"] * t["hari_sejak_hujan"]) / d
        kode = pd.qcut(t["kt"], 3, labels=False, duplicates="drop")
        label = kode.map(lambda i: nama[int(i)] if kode.max() == 2 else f"q{int(i)}")
        for lab, g in sisa.groupby(label):
            baris.append({"wb_id": wb, "tercile_kt": lab, "n": int(len(g)), "sisa_median": float(g.median())})
    v = pd.DataFrame(baris, columns=["wb_id", "tercile_kt", "n", "sisa_median"])
    v["beda_antar_tercile"] = v.groupby("wb_id")["sisa_median"].transform(lambda s: s.max() - s.min())
    return v
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_derate_calibration.py -v`
Expected: 20 passed (8 Task 2 + 3 fit + 8 putuskan + 1 validasi).

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/m2f/derate_calibration.py tests/unit/test_derate_calibration.py
git commit -m "feat(m2f): regresi, keputusan, dan validasi kalibrasi derate per WB"
```

---

### Task 4: CLI `run_derate_calibration.py`

**Files:**
- Create: `run_derate_calibration.py`
- Test: `tests/unit/test_derate_calibration.py`

**Interfaces:**
- Consumes:
  - semua fungsi Task 2–3;
  - `rekap_m2f.discover_m2f_xlsx(dir) -> List[(Timestamp, path)]`, `rekap_m2f.load_day(path) -> dict`, `rekap_m2f.build_daily_calib(days) -> DataFrame` (kolom `date, wb_id, ..., measured_ratio, n_calib_string_days, ...`);
  - `run_saturation_check.poa_smooth_share(poa_df, day) -> float`;
  - `PyranometerLoader(xlsx_path, sheet, ws_to_wb, time_offset_minutes)` dengan `.df` (kolom `WS-n`), `.wb_to_ws`, `.get_per_ws(idx, wb, fallback_to_avg=False)`;
  - `PvlibClearSkyEstimator.from_geometry_yaml(path, load_albedo_provider=False)` dengan `.estimate(idx)` dan `.get_solar_elevation(idx)`.
- Produces:
  - `_muat_poa(geometry: str, raw_root: str, offset: float | None) -> tuple[PyranometerLoader, float]`
  - `bangun_harian(rasio, loader, estimator, hujan) -> pd.DataFrame` (kolom `date, wb_id, rasio, n_calib, kt, mulus, poa_kosong, hari_sejak_hujan, alasan, dibuang`)
  - `main(argv=None) -> None`

- [ ] **Step 1: Write the failing test** (tambahkan ke akhir `tests/unit/test_derate_calibration.py`; impor `from pathlib import Path` dan `import run_derate_calibration as cli` masuk ke blok impor di kepala berkas)

```python
def _tulis_hari(folder, hari: str, rasio: dict):
    """Workbook harian minimal dengan sheet seperti M2Engine.write_xlsx_multi (lihat test_rekap_m2f)."""
    ymd = hari.replace("-", "")
    calib = pd.DataFrame([{"wb_id": wb, "g_bifacial": 1.0, "dc_derate": 1.0, "measured_ratio": r,
                           "n_calib_string_days": 450, "n_strings": 450, "n_days": 1} for wb, r in rasio.items()])
    with pd.ExcelWriter(folder / f"m2f_loss_attribution_{ymd}.xlsx") as w:
        pd.DataFrame({"x": [1]}).to_excel(w, sheet_name="Findings", index=False)
        pd.DataFrame([{"label": "E_expected", "delta_kwh": 100.0, "kind": "terminal"},
                      {"label": "E_actual", "delta_kwh": 90.0, "kind": "terminal"}]).to_excel(
            w, sheet_name="M2f_Waterfall", index=False)
        pd.DataFrame([{"string_id": "WB03-INV01-PV3", "day": pd.Timestamp(hari), "category": "unexplained",
                       "loss_kwh": 10.0}]).to_excel(w, sheet_name="M2f_PerString", index=False)
        pd.DataFrame([{"string_id": "WB03-INV01-PV3", "day": pd.Timestamp(hari), "l_total_kwh": 10.0,
                       "skipped_reason": None}]).to_excel(w, sheet_name="M2f_Closure", index=False)
        calib.to_excel(w, sheet_name="M2f_BaselineCalib", index=False)


class _Loader:
    def __init__(self):
        self.df = pd.DataFrame({"WS-1": 560.0}, index=pd.date_range("2026-06-01", "2026-06-03", freq="5min"))
        self.wb_to_ws = {"WB03": "WS-1", "WB04": "WS-1"}

    def get_per_ws(self, idx, wb, fallback_to_avg=False):
        return pd.Series(560.0, index=idx)


class _Langit:
    def estimate(self, idx):
        return pd.Series(800.0, index=idx)

    def get_solar_elevation(self, idx):
        return pd.Series(45.0, index=idx)


def test_cli_menulis_empat_sheet_tanpa_mengubah_config(tmp_path, monkeypatch):
    """Config hanya boleh diisi lewat langkah terpisah yang disetujui pemilik dokumen."""
    m2f = tmp_path / "m2f"
    m2f.mkdir()
    _tulis_hari(m2f, "2026-06-01", {"WB03": 0.90, "WB04": 0.95})
    _tulis_hari(m2f, "2026-06-02", {"WB03": 0.88, "WB04": 0.93})
    hujan = tmp_path / "hujan.csv"
    pd.DataFrame({"date": ["2026-05-31", "2026-06-01", "2026-06-02"],
                  "precipitation_mm": [12.0, 0.0, 0.0]}).to_csv(hujan, index=False)
    monkeypatch.setattr(cli, "_muat_poa", lambda geometry, raw_root, offset: (_Loader(), 5.0))
    monkeypatch.setattr(cli.PvlibClearSkyEstimator, "from_geometry_yaml",
                        classmethod(lambda cls, *a, **k: _Langit()))
    config = Path("config/m2_config.yaml")
    sebelum = config.read_bytes()

    cli.main(["--m2f-dir", str(m2f), "--precip", str(hujan), "--output-dir", str(tmp_path)])

    x = pd.ExcelFile(tmp_path / "derate_calibration_20260601_20260602.xlsx")
    assert x.sheet_names == ["Harian", "PerWB", "Validasi", "Catatan"]
    assert set(x.parse("PerWB")["keputusan"]) == {"data_kurang"}
    harian = x.parse("Harian")
    assert harian["kt"].round(3).eq(0.7).all()
    assert list(harian["hari_sejak_hujan"]) == [1, 1, 2, 2]
    assert config.read_bytes() == sebelum
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_derate_calibration.py -k cli -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'run_derate_calibration'`.

- [ ] **Step 3: Write minimal implementation** (`run_derate_calibration.py`)

```python
"""Kalibrasi m2f.dc_derate_per_wb: rasio harian M2f vs langit dan debu.

Rancangan: docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md

Usage:
    python run_derate_calibration.py --m2f-dir "F:/Downloads part 2/cek pv/m2f" \
        --raw-root "F:/Downloads part 2" [--hanya-sesudah "2026-09-29 22:13"]

Config TIDAK diubah: potongan YAML dicetak hanya untuk WB berkeputusan
"konstanta", untuk disalin setelah disetujui.
"""
from __future__ import annotations

import argparse
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from pv_pipeline.m2f.derate_calibration import (  # noqa: E402
    fit_wb, hari_sejak_hujan, kt_poa, porsi_kosong, putuskan, validasi,
)
from pv_pipeline.poa.loader import PyranometerLoader  # noqa: E402
from pv_pipeline.poa.pvlib_estimator import PvlibClearSkyEstimator  # noqa: E402
from rekap_m2f import build_daily_calib, discover_m2f_xlsx, load_day  # noqa: E402
from run_saturation_check import poa_smooth_share  # noqa: E402

MIN_N_CALIB = 100
MAKS_POA_KOSONG = 0.5


def _muat_poa(geometry: str, raw_root: str, offset):
    """PyranometerLoader dari geometri; path POA relatif diawali ``raw_root``."""
    with open(geometry, "r", encoding="utf-8") as fp:
        geo = yaml.safe_load(fp) or {}
    pyr = geo.get("pyranometer") or {}
    paths = pyr["xlsx_path"] if isinstance(pyr["xlsx_path"], list) else [pyr["xlsx_path"]]
    paths = [p if os.path.isabs(p) else os.path.join(raw_root, p) for p in paths]
    off = float(pyr.get("time_offset_minutes", 0.0) if offset is None else offset)
    loader = PyranometerLoader(paths, sheet=str(pyr.get("sheet", "POA PLTS IKN")),
                               ws_to_wb=geo.get("ws_to_wb") or {}, time_offset_minutes=off)
    return loader, off


def bangun_harian(rasio: pd.DataFrame, loader, estimator, hujan: pd.Series) -> pd.DataFrame:
    """Satu baris per WB-hari: rasio M2f + indeks langit (stasiun cuaca WB itu) + debu + saringan."""
    baris = []
    for hari, g in rasio.groupby("date", sort=True):
        hari = pd.Timestamp(hari).normalize()
        idx = pd.date_range(hari + pd.Timedelta(hours=6), hari + pd.Timedelta(hours=18), freq="5min")
        cerah, elev = estimator.estimate(idx), estimator.get_solar_elevation(idx)
        for r in g.itertuples(index=False):
            wb = str(r.wb_id).upper()
            poa = loader.get_per_ws(idx, wb, fallback_to_avg=False)
            ws = loader.wb_to_ws.get(wb)
            baris.append({
                "date": hari, "wb_id": wb, "rasio": float(r.measured_ratio),
                "n_calib": int(r.n_calib_string_days), "kt": kt_poa(poa, cerah, elev),
                "mulus": poa_smooth_share(loader.df[[ws]], hari) if ws in loader.df.columns else np.nan,
                "poa_kosong": porsi_kosong(poa, elev),
            })
    t = pd.DataFrame(baris)
    t["hari_sejak_hujan"] = hari_sejak_hujan(hujan, pd.DatetimeIndex(t["date"])).to_numpy()
    t["alasan"] = np.select(
        [t["n_calib"] < MIN_N_CALIB, t["poa_kosong"] > MAKS_POA_KOSONG,
         t[["rasio", "kt", "mulus"]].isna().any(axis=1)],
        [f"n_calib < {MIN_N_CALIB}", f"poa_kosong > {MAKS_POA_KOSONG}", "nilai kosong"], default="")
    t["dibuang"] = t["alasan"] != ""
    return t


def _gambar(sah: pd.DataFrame, path: str) -> None:
    """Rasio terhadap Kt per WB; warna = hari sejak hujan (debu)."""
    wbs = sorted(sah["wb_id"].unique())
    kolom = min(5, len(wbs))
    baris = int(np.ceil(len(wbs) / kolom))
    fig, ax = plt.subplots(baris, kolom, figsize=(3.2 * kolom, 2.8 * baris), squeeze=False,
                           sharex=True, sharey=True)
    for a, wb in zip(ax.ravel(), wbs):
        g = sah[sah["wb_id"] == wb]
        a.scatter(g["kt"], g["rasio"], s=12, c=g["hari_sejak_hujan"], cmap="viridis")
        a.set_title(wb, fontsize=9)
    for a in ax.ravel()[len(wbs):]:
        a.axis("off")
    fig.supxlabel("Kt POA")
    fig.supylabel("measured_ratio")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Kalibrasi m2f.dc_derate_per_wb dari workbook M2f harian.")
    ap.add_argument("--m2f-dir", required=True)
    ap.add_argument("--raw-root", default=".", help="awalan path POA relatif di site_geometry.yaml")
    ap.add_argument("--geometry", default=os.path.join("config", "site_geometry.yaml"))
    ap.add_argument("--precip", default=os.path.join("coba", "precipitation_daily_plts_ikn.csv"))
    ap.add_argument("--poa-offset-min", type=float, default=None)
    ap.add_argument("--hanya-sesudah", default=None, help="'YYYY-MM-DD HH:MM': workbook dimodifikasi sesudahnya")
    ap.add_argument("--output-dir", default="coba")
    a = ap.parse_args(argv)

    found = discover_m2f_xlsx(a.m2f_dir)
    if a.hanya_sesudah:
        batas = pd.Timestamp(a.hanya_sesudah).timestamp()
        found = [(d, p) for d, p in found if os.path.getmtime(p) >= batas]
    if not found:
        raise SystemExit(f"[derate] tidak ada workbook harian di {a.m2f_dir!r}")
    rasio = build_daily_calib([load_day(p) for _, p in found])
    loader, offset = _muat_poa(a.geometry, a.raw_root, a.poa_offset_min)
    estimator = PvlibClearSkyEstimator.from_geometry_yaml(a.geometry, load_albedo_provider=False)
    hujan = pd.read_csv(a.precip, parse_dates=["date"]).set_index("date")["precipitation_mm"]

    harian = bangun_harian(rasio, loader, estimator, hujan)
    sah = harian[~harian["dibuang"]]
    fits = {wb: fit_wb(g) for wb, g in sah.groupby("wb_id")}
    per_wb = pd.DataFrame([{"wb_id": wb, **f, **putuskan(f)} for wb, f in sorted(fits.items())])
    derate = {r.wb_id: r.nilai for r in per_wb.itertuples() if r.keputusan == "konstanta"}
    val = validasi(sah, fits, derate)

    awal, akhir = pd.Timestamp(rasio["date"].min()), pd.Timestamp(rasio["date"].max())
    bulan = (akhir.year - awal.year) * 12 + akhir.month - awal.month + 1
    dibuang = harian.loc[harian["dibuang"], "alasan"].value_counts()
    catatan = pd.DataFrame({"butir": [
        "rentang", "workbook", "WB-hari", "WB-hari dibuang", "offset POA (menit)", "label musim",
        "ambang", "hanya_sesudah"], "nilai": [
        f"{awal:%Y-%m-%d}..{akhir:%Y-%m-%d}", len(found), len(harian),
        "; ".join(f"{k}: {v}" for k, v in dibuang.items()) or "0",
        offset, "satu musim" if bulan < 3 else f"{bulan} bulan",
        f"ayunan 0.03; |t| 2; min 20 hari; n_calib {MIN_N_CALIB}; poa_kosong {MAKS_POA_KOSONG}; hujan 5 mm; maks 30",
        a.hanya_sesudah or "-"]})
    os.makedirs(a.output_dir, exist_ok=True)
    dasar = os.path.join(a.output_dir, f"derate_calibration_{awal:%Y%m%d}_{akhir:%Y%m%d}")
    with pd.ExcelWriter(dasar + ".xlsx") as w:
        harian.to_excel(w, sheet_name="Harian", index=False)
        per_wb.to_excel(w, sheet_name="PerWB", index=False)
        val.to_excel(w, sheet_name="Validasi", index=False)
        catatan.to_excel(w, sheet_name="Catatan", index=False)
    if len(sah):
        _gambar(sah, dasar + ".png")

    print(f"[derate] {len(found)} workbook, {len(harian)} WB-hari "
          f"({int(harian['dibuang'].sum())} dibuang) -> {dasar}.xlsx")
    print(per_wb[["wb_id", "n", "b", "t_b", "c", "t_c", "e", "t_e", "ayunan_langit", "rasio_bersih",
                  "keputusan"]].round(4).to_string(index=False))
    if derate:
        print("\n# usulan (BELUM diterapkan) -- salin setelah disetujui:\ndc_derate_per_wb:")
        for wb, v in sorted(derate.items()):
            print(f"  {wb}: {v:.3f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_derate_calibration.py -v`
Expected: 21 passed.

- [ ] **Step 5: Jalankan seluruh suite**

Run: `python -m pytest tests -q`
Expected: seluruh suite lulus (jumlah sebelumnya + 21; 1 dilewati seperti sebelumnya).

- [ ] **Step 6: Commit**

```bash
git add run_derate_calibration.py tests/unit/test_derate_calibration.py
git commit -m "feat(m2f): CLI run_derate_calibration -- rasio harian vs langit dan debu, tanpa mengubah config"
```

---

### Task 5: Run pada 42 hari, catat hasil, serahkan keputusan

**Files:**
- Modify: `docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md` (bagian "Hasil")
- Keluaran lokal (di-gitignore): `coba/derate_calibration_<awal>_<akhir>.xlsx` dan `.png`

**Interfaces:**
- Consumes: CLI Task 4 dan keputusan Task 1 (pakai `--hanya-sesudah` atau tidak).

- [ ] **Step 1: Jalankan** (F: tersambung; perintah sesuai keputusan Task 1)

```bash
python run_derate_calibration.py --m2f-dir "F:/Downloads part 2/cek pv/m2f" --raw-root "F:/Downloads part 2"
```

Bila Task 1 memutuskan hanya 22 hari:

```bash
python run_derate_calibration.py --m2f-dir "F:/Downloads part 2/cek pv/m2f" --raw-root "F:/Downloads part 2" --hanya-sesudah "2026-09-29 22:13"
```

Expected: tabel `PerWB` tercetak dan berkas `coba/derate_calibration_*.xlsx` + `.png` tertulis.

- [ ] **Step 2: Periksa kewajaran sebelum mencatat**

- Jumlah WB-hari dibuang per alasan wajar: `poa_kosong` > 0,5 hanya di hari stasiun cuaca padam.
- Tanda `e` mayoritas negatif.
- Bila ada WB `tercampur`, catat. Jangan ubah ambang demi hasil.

- [ ] **Step 3: Catat di spesifikasi dan commit**

Di bagian "Hasil", di bawah catatan Langkah 0, tambahkan:
- tabel `PerWB` (wb_id, n, b, t_b, c, t_c, e, t_e, ayunan_langit, rasio_bersih, keputusan);
- ringkasan `Validasi` (beda_antar_tercile per WB);
- isi sheet `Catatan`;
- potongan YAML usulan bila ada WB "konstanta", dengan label "BELUM diterapkan".

```bash
git add docs/superpowers/specs/2026-10-01-kalibrasi-dc-derate-design.md
git commit -m "docs(spec): hasil kalibrasi derate Jun-Jul 2026 -- keputusan per WB"
```

- [ ] **Step 4: Serahkan keputusan ke pengguna**

Laporkan keputusan per WB. Jangan ubah `config/m2_config.yaml`. Langkah selanjutnya mengikuti bagian "Langkah sesudah keputusan" di spesifikasi:
- semua konstanta → salin dengan persetujuan;
- ada `model_langit` atau `tercampur` → spesifikasi baru.
