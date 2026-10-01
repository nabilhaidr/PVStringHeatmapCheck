# Kalibrasi Silang POA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Laporan bias amplitudo dan jam penghalang tiap sensor POA (WS-1..WS-5), dengan usulan faktor koreksi hanya bila ≥ 2 dari 3 acuan sepakat. Loader dan config tidak diubah.

**Architecture:** Fungsi murni di `pv_pipeline/poa/kalibrasi_silang.py` (sampel stabil, rasio ke median stasiun lain, gain bulanan/profil jam/penghalang, acuan absolut dan larik, kesepakatan). CLI tipis `run_poa_cross_calibration.py` memuat POA lewat `run_derate_calibration._muat_poa`, menulis workbook dan PNG, lalu mencetak usulan YAML.

**Tech Stack:** Python 3.13, numpy, pandas, PyYAML, matplotlib, pvlib (lewat `PvlibClearSkyEstimator`); pytest.

**Spesifikasi:** `docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md`.

## Global Constraints

- Tidak ada dependensi baru.
- Uji hanya data sintetis; tidak membaca berkas di F: atau `coba/`.
- `config/*.yaml` dan `pv_pipeline/poa/loader.py` TIDAK diubah.
- Ambang (dari spesifikasi):
  - sampel stabil: simpangan < 0,02, POA > 300 W/m², pukul 09:00–15:00;
  - POA < 0 atau > 1400 W/m² = galat;
  - minimal 2 pembanding; minimal 200 sampel per bulan;
  - penghalang: profil < 0,90 pada ≥ 3 bulan;
  - bergeser: ayunan bulanan > 0,05;
  - kesepakatan: ±0,03;
  - sangat cerah: Kt ≥ 0,75.
- Faktor usulan = 1 ÷ gain (pengali POA).
- Gaya repo: nama dan docstring berbahasa Indonesia, alasan di docstring.
- Uji di `tests/unit/`, dijalankan dari akar repo.
- **Perbaikan atas spesifikasi** (dicatat di spesifikasi pada Task 3): sampel "sangat cerah" untuk `gain_absolut` ditentukan oleh **median Kt seluruh stasiun cuaca** di sampel itu, bukan oleh rasio stasiun itu sendiri. Kalau tidak, sensor yang membaca jauh terlalu rendah ikut tersaring keluar.

## Struktur berkas

| Berkas | Tanggung jawab |
|---|---|
| Create `pv_pipeline/poa/kalibrasi_silang.py` | Semua fungsi murni |
| Create `run_poa_cross_calibration.py` | CLI: POA → analisa → xlsx/PNG + usulan YAML |
| Create `tests/unit/test_poa_kalibrasi_silang.py` | Uji fungsi + CLI |
| Modify `docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md` | Perbaikan `gain_absolut` (Task 3), bagian "Hasil" (Task 5) |

---

### Task 1: `sampel_stabil` dan `rasio_ke_median`

**Files:**
- Create: `pv_pipeline/poa/kalibrasi_silang.py`
- Test: `tests/unit/test_poa_kalibrasi_silang.py`

**Interfaces:**
- Produces:
  - `sampel_stabil(poa: pd.DataFrame, *, toleransi=0.02, poa_min=300.0, jam=("09:00", "15:00")) -> pd.DataFrame` (bool, bentuk sama)
  - `rasio_ke_median(poa: pd.DataFrame, stabil: pd.DataFrame, *, min_pembanding=2) -> pd.DataFrame` (float, bentuk sama)
  - konstanta `POA_MAKS = 1400.0`
  - `poa` berkolom `WS-1..WS-5`, berindeks waktu 5 menit.

- [ ] **Step 1: Write the failing tests** (`tests/unit/test_poa_kalibrasi_silang.py`)

```python
"""Uji kalibrasi silang POA (docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md)."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.poa.kalibrasi_silang import rasio_ke_median, sampel_stabil

_GAIN = (1.0, 0.8, 1.05, 1.0, 1.0)


def _poa(hari=90, gains=_GAIN, mulai="2026-01-01"):
    """Kurva langit cerah mulus yang sama untuk semua WS, dikali gain sensor."""
    idx = pd.date_range(mulai, periods=hari * 288, freq="5min")
    jam = (idx.hour + idx.minute / 60.0).to_numpy()
    dasar = np.clip(1000.0 * np.sin(np.pi * (jam - 6.0) / 12.0), 0.0, None)
    return pd.DataFrame({f"WS-{i + 1}": dasar * g for i, g in enumerate(gains)}, index=idx)


class TestSampelStabil:
    def test_kurva_mulus_stabil_hanya_di_jendela(self):
        s = sampel_stabil(_poa(hari=2))
        assert s.loc["2026-01-01 12:00"].all()
        assert not s.loc["2026-01-01 08:00"].any()     # di luar 09-15
        assert not s.loc["2026-01-01 16:00"].any()

    def test_lonjakan_awan_tidak_stabil(self):
        """Tepi awan di satu stasiun tidak boleh terbaca sebagai bias sensor."""
        p = _poa(hari=1)
        t = pd.Timestamp("2026-01-01 12:00")
        p.loc[t, "WS-2"] *= 1.3
        s = sampel_stabil(p)["WS-2"]
        for dt in (-5, 0, 5):
            assert not s.loc[t + pd.Timedelta(minutes=dt)]
        assert s.loc[t + pd.Timedelta(minutes=30)]

    def test_poa_galat_tidak_stabil(self):
        p = _poa(hari=1)
        p.loc["2026-01-01 11:00", "WS-1"] = 1500.0
        assert not sampel_stabil(p).loc["2026-01-01 11:00", "WS-1"]


class TestRasioKeMedian:
    def test_gain_yang_ditanam_kembali(self):
        """Sensor yang membaca 20 % terlalu rendah harus tampak 0,8 terhadap stasiun lain."""
        p = _poa(hari=3)
        r = rasio_ke_median(p, sampel_stabil(p))
        assert r["WS-2"].median() == pytest.approx(0.8, rel=0.01)
        assert r["WS-3"].median() == pytest.approx(1.05, rel=0.01)

    def test_pembanding_kurang_nan(self):
        """Dua stasiun saja: tiap stasiun hanya punya satu pembanding -> tidak dinilai."""
        p = _poa(hari=1)[["WS-1", "WS-2"]]
        assert rasio_ke_median(p, sampel_stabil(p)).isna().all().all()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pv_pipeline.poa.kalibrasi_silang'`.

- [ ] **Step 3: Write minimal implementation** (`pv_pipeline/poa/kalibrasi_silang.py`)

```python
"""Kalibrasi silang POA antar-stasiun cuaca -- bias amplitudo sensor dan jam penghalang.

Rancangan: ``docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md``.

Tidak ada sensor POA "benar" di lokasi, jadi bias tiap WS diukur terhadap tiga
acuan: median stasiun lain (sampel yang stabil di semua stasiun yang
dibandingkan, supaya bayangan awan lokal tak terbaca sebagai bias), POA langit
cerah pvlib, dan larik itu sendiri lewat ``measured_ratio`` M2f. Faktor koreksi
hanya diusulkan bila >= 2 acuan sepakat.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

POA_MAKS = 1400.0


def sampel_stabil(poa: pd.DataFrame, *, toleransi: float = 0.02, poa_min: float = 300.0,
                  jam: tuple = ("09:00", "15:00")) -> pd.DataFrame:
    """Benar bila POA mulus di sampel itu dan kedua tetangganya, > ``poa_min``, dalam jendela ``jam``.

    "Mulus" = |POA - rata-rata kedua tetangga| / POA < ``toleransi`` -- definisi
    yang sama dengan ``pv_pipeline.m2f.saturation``. POA < 0 atau > 1400 = galat.
    """
    p = poa.where((poa >= 0.0) & (poa <= POA_MAKS))
    dev = (p - (p.shift(1) + p.shift(-1)) / 2.0).abs() / p
    mulus = dev < toleransi
    stabil = mulus & mulus.shift(1, fill_value=False) & mulus.shift(-1, fill_value=False)
    dalam = np.zeros(len(p), dtype=bool)
    dalam[p.index.indexer_between_time(*jam)] = True
    jendela = pd.DataFrame(np.repeat(dalam[:, None], p.shape[1], axis=1), index=p.index, columns=p.columns)
    return stabil & (p > poa_min) & jendela


def rasio_ke_median(poa: pd.DataFrame, stabil: pd.DataFrame, *, min_pembanding: int = 2) -> pd.DataFrame:
    """POA_WS / median POA stasiun LAIN yang stabil di sampel yang sama.

    NaN bila WS itu tak stabil atau pembandingnya < ``min_pembanding``. WS
    yang kosong tidak diisi rata-rata situs: isi rata-rata menarik rasio ke 1.
    """
    p = poa.where(stabil)
    hasil = {}
    for ws in p.columns:
        lain = p.drop(columns=ws)
        hasil[ws] = (p[ws] / lain.median(axis=1, skipna=True)).where(lain.notna().sum(axis=1) >= min_pembanding)
    return pd.DataFrame(hasil, index=p.index)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/poa/kalibrasi_silang.py tests/unit/test_poa_kalibrasi_silang.py
git commit -m "feat(poa): sampel stabil dan rasio ke median stasiun lain"
```

---

### Task 2: `gain_bulanan`, `profil_jam`, `penghalang`, `gain_relatif`

**Files:**
- Modify: `pv_pipeline/poa/kalibrasi_silang.py`
- Test: `tests/unit/test_poa_kalibrasi_silang.py`

**Interfaces:**
- Consumes: `sampel_stabil`, `rasio_ke_median` (Task 1).
- Produces:
  - `gain_bulanan(rasio, *, min_sampel=200) -> DataFrame[ws, bulan, n, median, iqr, alasan]` (`bulan` = "YYYY-MM")
  - `profil_jam(rasio) -> DataFrame[ws, bulan, jam, n, profil]`
  - `penghalang(profil, *, ambang=0.10, min_bulan=3) -> DataFrame[ws, jam, n_bulan]`
  - `gain_relatif(rasio, jam_penghalang, *, min_sampel=200, ambang_geser=0.05) -> DataFrame[ws, gain, n, ayunan_bulanan, bergeser]`

- [ ] **Step 1: Write the failing tests**

Ganti baris impor di kepala berkas uji menjadi:

```python
from pv_pipeline.poa.kalibrasi_silang import (
    gain_bulanan, gain_relatif, penghalang, profil_jam, rasio_ke_median, sampel_stabil,
)
```

Tambahkan di akhir berkas:

```python
def _rasio(p):
    return rasio_ke_median(p, sampel_stabil(p))


def _turunkan(p, ws, jam, faktor, sampai=None):
    """POA ``ws`` dikali ``faktor`` pada jam ``jam`` (penghalang), hingga tanggal ``sampai``."""
    m = p.index.hour == jam
    if sampai is not None:
        m &= p.index < pd.Timestamp(sampai)
    p.loc[m, ws] *= faktor
    return p


class TestGainBulanan:
    def test_tiga_bulan_gain_kembali(self):
        g = gain_bulanan(_rasio(_poa()))
        ws2 = g[g["ws"] == "WS-2"]
        assert list(ws2["bulan"]) == ["2026-01", "2026-02", "2026-03"]
        assert ws2["median"].to_numpy() == pytest.approx([0.8] * 3, rel=0.01)

    def test_bulan_data_tipis_nan(self):
        g = gain_bulanan(_rasio(_poa(hari=2)))          # ~146 sampel stabil per WS < 200
        assert g["median"].isna().all() and set(g["alasan"]) == {"data tipis"}


class TestPenghalang:
    def test_penurunan_jam_11_tiga_bulan_ditandai(self):
        """Penghalang bergantung jam; satu faktor gain tak bisa memperbaikinya."""
        r = _rasio(_turunkan(_poa(), "WS-3", 11, 0.6))
        hal = penghalang(profil_jam(r))
        assert list(zip(hal["ws"], hal["jam"])) == [("WS-3", 11)]
        g = gain_relatif(r, hal).set_index("ws")
        assert g.loc["WS-3", "gain"] == pytest.approx(1.05, rel=0.01)

    def test_satu_bulan_tidak_ditandai(self):
        """Satu bulan berawan di jam tertentu bukan penghalang tetap."""
        r = _rasio(_turunkan(_poa(), "WS-3", 11, 0.6, sampai="2026-02-01"))
        assert penghalang(profil_jam(r)).empty


class TestGainRelatif:
    def test_gain_bergeser_antar_bulan_ditandai(self):
        """Sensor yang mengotor/berubah kalibrasi tidak boleh diberi satu faktor."""
        p = _poa()
        p.loc[p.index >= "2026-02-01", "WS-2"] *= 0.9       # WS-2: 0,80 Januari -> 0,72 sejak Februari
        r = _rasio(p)
        g = gain_relatif(r, penghalang(profil_jam(r))).set_index("ws")
        assert bool(g.loc["WS-2", "bergeser"]) and not bool(g.loc["WS-1", "bergeser"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -v`
Expected: FAIL — `ImportError: cannot import name 'gain_bulanan'`.

- [ ] **Step 3: Write minimal implementation** (tambahkan ke `pv_pipeline/poa/kalibrasi_silang.py`)

```python
KOLOM_BULANAN = ["ws", "bulan", "n", "median", "iqr", "alasan"]


def gain_bulanan(rasio: pd.DataFrame, *, min_sampel: int = 200) -> pd.DataFrame:
    """Median rasio per WS per bulan; < ``min_sampel`` sampel -> NaN, alasan "data tipis"."""
    baris = []
    for ws in rasio.columns:
        s = rasio[ws].dropna()
        for bulan, g in s.groupby(s.index.to_period("M")):
            cukup = len(g) >= min_sampel
            baris.append({"ws": ws, "bulan": str(bulan), "n": int(len(g)),
                          "median": float(g.median()) if cukup else np.nan,
                          "iqr": float(g.quantile(0.75) - g.quantile(0.25)) if cukup else np.nan,
                          "alasan": "" if cukup else "data tipis"})
    return pd.DataFrame(baris, columns=KOLOM_BULANAN)


def profil_jam(rasio: pd.DataFrame) -> pd.DataFrame:
    """Median rasio per jam / median rasio seluruh sampel WS itu di bulan itu."""
    baris = []
    for ws in rasio.columns:
        s = rasio[ws].dropna()
        for bulan, g in s.groupby(s.index.to_period("M")):
            acuan = g.median()
            for jam, h in g.groupby(g.index.hour):
                baris.append({"ws": ws, "bulan": str(bulan), "jam": int(jam), "n": int(len(h)),
                              "profil": float(h.median() / acuan)})
    return pd.DataFrame(baris, columns=["ws", "bulan", "jam", "n", "profil"])


def penghalang(profil: pd.DataFrame, *, ambang: float = 0.10, min_bulan: int = 3) -> pd.DataFrame:
    """(WS, jam) yang profilnya < 1 - ``ambang`` pada >= ``min_bulan`` bulan."""
    turun = profil[profil["profil"] < 1.0 - ambang]
    hit = turun.groupby(["ws", "jam"])["bulan"].nunique().reset_index(name="n_bulan")
    return hit[hit["n_bulan"] >= min_bulan].reset_index(drop=True)


def gain_relatif(rasio: pd.DataFrame, jam_penghalang: pd.DataFrame, *, min_sampel: int = 200,
                 ambang_geser: float = 0.05) -> pd.DataFrame:
    """Median rasio selama rentang tanpa jam penghalang; ``bergeser`` bila median bulanan berayun > ambang."""
    baris = []
    for ws in rasio.columns:
        s = rasio[ws].dropna()
        buang = set(jam_penghalang.loc[jam_penghalang["ws"] == ws, "jam"]) if len(jam_penghalang) else set()
        s = s[~s.index.hour.isin(sorted(buang))]
        bulanan = gain_bulanan(s.to_frame(ws), min_sampel=min_sampel)["median"].dropna()
        ayunan = float(bulanan.max() - bulanan.min()) if len(bulanan) else np.nan
        baris.append({"ws": ws, "gain": float(s.median()) if len(s) >= min_sampel else np.nan,
                      "n": int(len(s)), "ayunan_bulanan": ayunan,
                      "bergeser": bool(np.isfinite(ayunan) and ayunan > ambang_geser)})
    return pd.DataFrame(baris, columns=["ws", "gain", "n", "ayunan_bulanan", "bergeser"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/poa/kalibrasi_silang.py tests/unit/test_poa_kalibrasi_silang.py
git commit -m "feat(poa): gain bulanan, profil jam, penghalang, gain relatif"
```

---

### Task 3: `gain_absolut`, `gain_larik`, `sepakati`

**Files:**
- Modify: `pv_pipeline/poa/kalibrasi_silang.py`
- Modify: `docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md` (definisi sangat cerah di `gain_absolut`)
- Test: `tests/unit/test_poa_kalibrasi_silang.py`

**Interfaces:**
- Consumes: `sampel_stabil` (Task 1), keluaran `gain_relatif` (Task 2).
- Produces:
  - `gain_absolut(poa, poa_cerah: pd.Series, stabil, *, kt_min=0.75) -> DataFrame[ws, gain, n]`
  - `gain_larik(kalibrasi_harian: DataFrame[date, wb_id, measured_ratio], wb_to_ws: dict) -> DataFrame[ws, gain, n]`
  - `sepakati(rel, absolut, larik | None, *, tol=0.03) -> DataFrame[ws, gain_rel, gain_abs, gain_larik, bergeser, status, usulan, alasan]`, dengan `status` ∈ {`usulan_koreksi`, `perlu_lapangan`}

- [ ] **Step 1: Write the failing tests**

Ganti baris impor di kepala berkas uji menjadi:

```python
from pv_pipeline.poa.kalibrasi_silang import (
    gain_absolut, gain_bulanan, gain_larik, gain_relatif, penghalang, profil_jam,
    rasio_ke_median, sampel_stabil, sepakati,
)
```

Tambahkan di akhir berkas:

```python
class TestGainAbsolut:
    def test_poa_delapan_persepuluh_langit_cerah(self):
        p = _poa(hari=2, gains=(0.8,) * 5)
        cerah = _poa(hari=2, gains=(1.0,) * 5)["WS-1"]
        g = gain_absolut(p, cerah, sampel_stabil(p)).set_index("ws")
        assert g.loc["WS-1", "gain"] == pytest.approx(0.8, rel=0.01)

    def test_hari_berawan_tidak_dihitung(self):
        """Kekeruhan dan awan merusak acuan absolut; hanya saat sangat cerah yang dipakai."""
        p = _poa(hari=2, gains=(0.5,) * 5)
        cerah = _poa(hari=2, gains=(1.0,) * 5)["WS-1"]
        assert gain_absolut(p, cerah, sampel_stabil(p))["n"].eq(0).all()

    def test_sensor_rendah_tidak_tersaring(self):
        """Kecerahan dinilai dari median semua WS, jadi WS yang membaca 0,7 tetap terukur."""
        p = _poa(hari=2, gains=(1.0, 0.7, 1.0, 1.0, 1.0))
        cerah = _poa(hari=2, gains=(1.0,) * 5)["WS-1"]
        g = gain_absolut(p, cerah, sampel_stabil(p)).set_index("ws")
        assert g.loc["WS-2", "gain"] == pytest.approx(0.7, rel=0.01)


class TestGainLarik:
    def test_rasio_wb_mengembalikan_gain_sensor(self):
        """POA tinggi -> harapan tinggi -> rasio aktual/harapan rendah: larik adalah standar transfer."""
        gain_ws = dict(zip(["WS-1", "WS-2", "WS-3", "WS-4", "WS-5"], _GAIN))
        wb_to_ws = {f"WB{i:02d}": ws for i, ws in enumerate(
            ["WS-1", "WS-1", "WS-2", "WS-2", "WS-3", "WS-3", "WS-4", "WS-4", "WS-5", "WS-5"], start=1)}
        k = pd.DataFrame([{"date": d, "wb_id": wb, "measured_ratio": 0.9 / gain_ws[ws]}
                          for d in pd.date_range("2026-06-01", periods=3) for wb, ws in wb_to_ws.items()])
        g = gain_larik(k, wb_to_ws).set_index("ws")["gain"]
        assert g["WS-2"] == pytest.approx(0.8, rel=0.01) and g["WS-3"] == pytest.approx(1.05, rel=0.01)


def _rel(gain_ws2=0.8, bergeser=False):
    return pd.DataFrame({"ws": ["WS-1", "WS-2", "WS-3", "WS-4", "WS-5"],
                         "gain": [1.0, gain_ws2, 1.05, 1.0, 1.0], "n": 1000,
                         "ayunan_bulanan": 0.01, "bergeser": [False, bergeser, False, False, False]})


def _acuan(ws2):
    return pd.DataFrame({"ws": ["WS-1", "WS-2", "WS-3", "WS-4", "WS-5"],
                         "gain": [1.0, ws2, 1.05, 1.0, 1.0], "n": 500})


class TestSepakati:
    def test_tiga_acuan_sepakat_usulan_kebalikan_gain(self):
        s = sepakati(_rel(), _acuan(0.8), _acuan(0.8)).set_index("ws")
        assert s.loc["WS-2", "status"] == "usulan_koreksi"
        assert s.loc["WS-2", "usulan"] == pytest.approx(1 / 0.8)

    def test_dua_sepakat_satu_menyimpang(self):
        s = sepakati(_rel(), _acuan(0.8), _acuan(0.9)).set_index("ws")
        assert s.loc["WS-2", "status"] == "usulan_koreksi"

    def test_semua_berselisih_perlu_lapangan(self):
        """Tanpa dua acuan yang sepakat, faktor koreksi bisa memindahkan bias, bukan menghapusnya."""
        s = sepakati(_rel(0.8), _acuan(0.85), _acuan(0.9)).set_index("ws")
        assert s.loc["WS-2", "status"] == "perlu_lapangan" and np.isnan(s.loc["WS-2", "usulan"])

    def test_sepakat_tetapi_bergeser_perlu_lapangan(self):
        s = sepakati(_rel(bergeser=True), _acuan(0.8), _acuan(0.8)).set_index("ws")
        assert s.loc["WS-2", "status"] == "perlu_lapangan"

    def test_tanpa_larik_dua_acuan(self):
        s = sepakati(_rel(), _acuan(0.8), None).set_index("ws")
        assert s.loc["WS-2", "status"] == "usulan_koreksi" and np.isnan(s.loc["WS-2", "gain_larik"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -v`
Expected: FAIL — `ImportError: cannot import name 'gain_absolut'`.

- [ ] **Step 3: Write minimal implementation** (tambahkan ke `pv_pipeline/poa/kalibrasi_silang.py`; tambahkan `import itertools` di blok impor pustaka standar)

```python
def gain_absolut(poa: pd.DataFrame, poa_cerah: pd.Series, stabil: pd.DataFrame, *,
                 kt_min: float = 0.75) -> pd.DataFrame:
    """Median POA_WS / POA langit cerah pada sampel stabil di saat langit sangat cerah.

    "Sangat cerah" dinilai dari MEDIAN Kt seluruh WS di sampel itu (>= ``kt_min``),
    bukan dari rasio WS itu sendiri: sensor yang membaca jauh terlalu rendah
    tidak boleh tersaring keluar dari pengukuran biasnya.
    """
    c = poa_cerah.reindex(poa.index).to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        kt_situs = poa.median(axis=1).to_numpy(dtype=float) / c
        cerah = kt_situs >= kt_min
        baris = []
        for ws in poa.columns:
            r = poa[ws].to_numpy(dtype=float) / c
            m = cerah & stabil[ws].to_numpy(dtype=bool) & np.isfinite(r)
            baris.append({"ws": ws, "gain": float(np.median(r[m])) if m.any() else np.nan, "n": int(m.sum())})
    return pd.DataFrame(baris, columns=["ws", "gain", "n"])


def gain_larik(kalibrasi_harian: pd.DataFrame, wb_to_ws: dict) -> pd.DataFrame:
    """Per WS: median atas WB-hari dari (median rasio situs hari itu / rasio WB).

    Bila larik setara, rasio aktual/harapan WB berbanding terbalik dengan gain
    sensor POA-nya: nilai ini sebanding dengan gain sensor, relatif terhadap armada.
    """
    k = kalibrasi_harian.dropna(subset=["measured_ratio"]).copy()
    k["ws"] = k["wb_id"].astype(str).str.upper().map({str(w).upper(): s for w, s in wb_to_ws.items()})
    k = k.dropna(subset=["ws"])
    k["g"] = k.groupby("date")["measured_ratio"].transform("median") / k["measured_ratio"]
    out = k.groupby("ws")["g"].agg(gain="median", n="size").reset_index()
    return out[["ws", "gain", "n"]]


def sepakati(rel: pd.DataFrame, absolut: pd.DataFrame, larik, *, tol: float = 0.03) -> pd.DataFrame:
    """Usulan faktor (1/gain) bila >= 2 acuan sepakat dalam ``tol`` dan gain tak bergeser.

    ``gain_abs`` dinormalkan ke median semua WS: langit cerah pvlib punya bias
    bersama (kekeruhan, albedo) yang bukan milik satu sensor.
    """
    a = absolut.set_index("ws")["gain"]
    a = a / a.median()
    lr = larik.set_index("ws")["gain"] if larik is not None and len(larik) else pd.Series(dtype=float)
    baris = []
    for r in rel.itertuples(index=False):
        nilai = {"rel": r.gain, "abs": a.get(r.ws, np.nan), "larik": lr.get(r.ws, np.nan)}
        ada = {k: float(v) for k, v in nilai.items() if np.isfinite(v)}
        sepakat = set()
        for (k1, v1), (k2, v2) in itertools.combinations(ada.items(), 2):
            if abs(v1 - v2) <= tol:
                sepakat |= {k1, k2}
        dasar = {"ws": r.ws, "gain_rel": nilai["rel"], "gain_abs": nilai["abs"], "gain_larik": nilai["larik"],
                 "bergeser": bool(r.bergeser)}
        if len(sepakat) >= 2 and not r.bergeser:
            g = float(np.median([ada[k] for k in sepakat]))
            baris.append({**dasar, "status": "usulan_koreksi", "usulan": 1.0 / g,
                          "alasan": "sepakat: " + ", ".join(sorted(sepakat))})
        else:
            alasan = ("gain bergeser antar-bulan" if len(sepakat) >= 2 else
                      f"acuan berselisih > {tol}: " + ", ".join(f"{k} {v:.3f}" for k, v in ada.items()))
            baris.append({**dasar, "status": "perlu_lapangan", "usulan": np.nan, "alasan": alasan})
    return pd.DataFrame(baris, columns=["ws", "gain_rel", "gain_abs", "gain_larik", "bergeser",
                                        "status", "usulan", "alasan"])
```

Lalu di spesifikasi, ganti kalimat `gain_absolut` "... pada sampel stabil dengan rasio ≥ `kt_min` (hari sangat cerah)" menjadi "... pada sampel stabil saat MEDIAN Kt seluruh WS ≥ `kt_min` (bukan rasio WS itu sendiri, supaya sensor yang membaca jauh terlalu rendah tidak tersaring keluar)".

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -v`
Expected: 19 passed (5 + 5 + 3 absolut + 1 larik + 5 sepakati).

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/poa/kalibrasi_silang.py tests/unit/test_poa_kalibrasi_silang.py docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md
git commit -m "feat(poa): acuan absolut dan larik, aturan kesepakatan kalibrasi silang"
```

---

### Task 4: CLI `run_poa_cross_calibration.py`

**Files:**
- Create: `run_poa_cross_calibration.py`
- Test: `tests/unit/test_poa_kalibrasi_silang.py`

**Interfaces:**
- Consumes:
  - semua fungsi Task 1–3 dan `POA_MAKS`;
  - `run_derate_calibration._muat_poa(geometry, raw_root, offset) -> (loader, offset)`, dengan `loader.df` (kolom `WS-1..5`, `avg`) dan `loader.wb_to_ws`;
  - `PvlibClearSkyEstimator.from_geometry_yaml(path, load_albedo_provider=False).estimate(idx)`;
  - `rekap_m2f.discover_m2f_xlsx`, `load_day`, `build_daily_calib`.
- Produces: `main(argv=None) -> None`.

- [ ] **Step 1: Write the failing test** (tambahkan `from pathlib import Path` dan `import run_poa_cross_calibration as cli` ke blok impor di kepala berkas; uji di akhir berkas)

```python
class _Loader:
    def __init__(self):
        self.df = _poa().assign(avg=0.0)
        self.wb_to_ws = {"WB08": "WS-1", "WB05": "WS-2"}


class _Langit:
    def estimate(self, idx):
        return _poa(gains=(1.0,) * 5)["WS-1"].reindex(idx)


def test_cli_delapan_sheet_usulan_ws2_tanpa_mengubah_config(tmp_path, monkeypatch):
    """Usulan hanya dicetak; config dan loader baru berubah lewat spesifikasi terpisah."""
    monkeypatch.setattr(cli, "_muat_poa", lambda geometry, raw_root, offset: (_Loader(), 5.0))
    monkeypatch.setattr(cli.PvlibClearSkyEstimator, "from_geometry_yaml",
                        classmethod(lambda cls, *a, **k: _Langit()))
    config = Path("config/site_geometry.yaml")
    sebelum = config.read_bytes()

    cli.main(["--mulai", "2026-01-01", "--akhir", "2026-03-31", "--output-dir", str(tmp_path)])

    x = pd.ExcelFile(tmp_path / "poa_cross_calibration_20260101_20260331.xlsx")
    assert x.sheet_names == ["Bulanan", "ProfilJam", "Penghalang", "Relatif", "Absolut", "Larik",
                             "Kesepakatan", "Catatan"]
    assert x.parse("Larik").empty
    s = x.parse("Kesepakatan").set_index("ws")
    assert s.loc["WS-2", "status"] == "usulan_koreksi"
    assert s.loc["WS-2", "usulan"] == pytest.approx(1 / 0.8, rel=0.01)
    assert config.read_bytes() == sebelum
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -k cli -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'run_poa_cross_calibration'`.

- [ ] **Step 3: Write minimal implementation** (`run_poa_cross_calibration.py`)

```python
"""Kalibrasi silang POA antar-stasiun cuaca: laporan bias sensor dan jam penghalang.

Rancangan: docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md

Usage:
    python run_poa_cross_calibration.py --raw-root "F:/Downloads part 2" \
        [--mulai 2025-01-01] [--akhir 2026-07-31] [--m2f-dir "F:/Downloads part 2/cek pv/m2f"]

Config dan loader TIDAK diubah: usulan pyranometer.ws_gain / ws_jam_penghalang
dicetak (BELUM dibaca loader) untuk diputuskan pemilik dokumen.
"""
from __future__ import annotations

import argparse
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from pv_pipeline.poa.kalibrasi_silang import (  # noqa: E402
    POA_MAKS, gain_absolut, gain_bulanan, gain_larik, gain_relatif, penghalang, profil_jam,
    rasio_ke_median, sampel_stabil, sepakati,
)
from pv_pipeline.poa.pvlib_estimator import PvlibClearSkyEstimator  # noqa: E402
from rekap_m2f import build_daily_calib, discover_m2f_xlsx, load_day  # noqa: E402
from run_derate_calibration import _muat_poa  # noqa: E402


def _gambar(bulanan: pd.DataFrame, profil: pd.DataFrame, path: str) -> None:
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4))
    for ws, g in bulanan.dropna(subset=["median"]).groupby("ws"):
        a1.plot(g["bulan"], g["median"], marker="o", label=ws)
    a1.axhline(1.0, color="grey", lw=0.8)
    a1.set_title("Gain bulanan (vs median WS lain)")
    a1.tick_params(axis="x", rotation=90)
    a1.legend(fontsize=8)
    for ws, g in profil.groupby("ws"):
        m = g.groupby("jam")["profil"].median()
        a2.plot(m.index, m.values, marker="o", label=ws)
    a2.axhline(0.9, color="red", lw=0.8, ls="--")
    a2.set_title("Profil per jam (median antar-bulan)")
    a2.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Kalibrasi silang POA antar-stasiun cuaca.")
    ap.add_argument("--raw-root", default=".", help="awalan path POA relatif di site_geometry.yaml")
    ap.add_argument("--mulai", default="2025-01-01")
    ap.add_argument("--akhir", default="2026-07-31")
    ap.add_argument("--m2f-dir", default=None, help="folder workbook M2f harian untuk acuan larik")
    ap.add_argument("--geometry", default=os.path.join("config", "site_geometry.yaml"))
    ap.add_argument("--output-dir", default="coba")
    a = ap.parse_args(argv)

    loader, offset = _muat_poa(a.geometry, a.raw_root, None)
    kolom = [c for c in loader.df.columns if str(c).startswith("WS-")]
    poa = loader.df.loc[a.mulai:f"{a.akhir} 23:59:59", kolom]
    galat = int(((poa < 0) | (poa > POA_MAKS)).sum().sum())

    stabil = sampel_stabil(poa)
    rasio = rasio_ke_median(poa, stabil)
    bulanan = gain_bulanan(rasio)
    profil = profil_jam(rasio)
    hal = penghalang(profil)
    rel = gain_relatif(rasio, hal)
    estimator = PvlibClearSkyEstimator.from_geometry_yaml(a.geometry, load_albedo_provider=False)
    absolut = gain_absolut(poa, estimator.estimate(poa.index), stabil)
    larik = None
    if a.m2f_dir:
        kal = build_daily_calib([load_day(p) for _, p in discover_m2f_xlsx(a.m2f_dir)])
        larik = gain_larik(kal, loader.wb_to_ws)
    sep = sepakati(rel, absolut, larik)

    awal, akhir = pd.Timestamp(a.mulai), pd.Timestamp(a.akhir)
    catatan = pd.DataFrame({"butir": [
        "rentang", "sampel stabil per WS", "sampel galat (<0 atau >1400)", "offset POA (menit)",
        "acuan larik", "ambang"], "nilai": [
        f"{awal:%Y-%m-%d}..{akhir:%Y-%m-%d}",
        "; ".join(f"{ws}: {int(n)}" for ws, n in stabil.sum().items()), galat, offset,
        a.m2f_dir or "-",
        "stabil 2 %; POA > 300; 09-15; min 2 pembanding; 200 sampel/bulan; penghalang 10 % x 3 bulan; "
        "bergeser 5 %; sepakat 3 %; Kt sangat cerah 0,75"]})
    os.makedirs(a.output_dir, exist_ok=True)
    dasar = os.path.join(a.output_dir, f"poa_cross_calibration_{awal:%Y%m%d}_{akhir:%Y%m%d}")
    with pd.ExcelWriter(dasar + ".xlsx") as w:
        bulanan.to_excel(w, sheet_name="Bulanan", index=False)
        profil.to_excel(w, sheet_name="ProfilJam", index=False)
        hal.to_excel(w, sheet_name="Penghalang", index=False)
        rel.to_excel(w, sheet_name="Relatif", index=False)
        absolut.to_excel(w, sheet_name="Absolut", index=False)
        (larik if larik is not None else pd.DataFrame(columns=["ws", "gain", "n"])).to_excel(
            w, sheet_name="Larik", index=False)
        sep.to_excel(w, sheet_name="Kesepakatan", index=False)
        catatan.to_excel(w, sheet_name="Catatan", index=False)
    _gambar(bulanan, profil, dasar + ".png")

    print(f"[poa-silang] {awal:%Y-%m-%d}..{akhir:%Y-%m-%d} -> {dasar}.xlsx")
    print(sep.round(3).to_string(index=False))
    usul = sep[sep["status"] == "usulan_koreksi"]
    if len(usul) or len(hal):
        print("\n# usulan (BELUM diterapkan; loader belum membaca kunci ini):\npyranometer:")
        if len(usul):
            print("  ws_gain:")
            for r in usul.itertuples():
                print(f"    {r.ws}: {r.usulan:.3f}")
        if len(hal):
            print("  ws_jam_penghalang:")
            for ws, g in hal.groupby("ws"):
                print(f"    {ws}: {sorted(int(j) for j in g['jam'])}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_poa_kalibrasi_silang.py -v`
Expected: 20 passed.

- [ ] **Step 5: Jalankan seluruh suite**

Run: `python -m pytest tests -q`
Expected: seluruh suite lulus (1206 sebelumnya + 20 baru; 1 dilewati).

- [ ] **Step 6: Commit**

```bash
git add run_poa_cross_calibration.py tests/unit/test_poa_kalibrasi_silang.py
git commit -m "feat(poa): CLI run_poa_cross_calibration -- laporan bias sensor, tanpa mengubah config"
```

---

### Task 5: Run pada 2025-01..2026-07, catat hasil, serahkan keputusan

**Files:**
- Modify: `docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md` (bagian "Hasil")
- Keluaran lokal (di-gitignore): `coba/poa_cross_calibration_20250101_20260731.xlsx` dan `.png`

- [ ] **Step 1: Jalankan** (F: tersambung; memori bebas cukup untuk POA 2025+2026)

```bash
python run_poa_cross_calibration.py --raw-root "F:/Downloads part 2" --m2f-dir "F:/Downloads part 2/cek pv/m2f"
```

Expected: tabel `Kesepakatan` tercetak dan berkas xlsx + PNG tertulis.

- [ ] **Step 2: Periksa kewajaran sebelum mencatat**

- Sampel stabil per stasiun cuaca: WS-3 dan WS-5 2025 tipis (memori `poa-ws-data-gaps`), jadi bulan-bulan itu harus muncul sebagai "data tipis".
- WS-1 diharapkan menunjukkan gain < 1 dan/atau penghalang pukul 11. Bila tidak, catat sebagai temuan, jangan ubah ambang.
- Acuan larik hanya mencakup Jun–Jul 2026; bandingkan dengan gain bulanan Jun–Jul, bukan dengan median 2025–2026.

- [ ] **Step 3: Catat di spesifikasi dan commit**

Di bagian "Hasil", ganti "Diisi saat implementasi." dengan:
- tabel `Kesepakatan` (ws, gain_rel, gain_abs, gain_larik, bergeser, status, usulan, alasan);
- `Penghalang`;
- ringkasan gain bulanan per stasiun cuaca (rentang, bulan "data tipis");
- isi `Catatan`;
- potongan YAML usulan dengan label "BELUM diterapkan".

```bash
git add docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md
git commit -m "docs(spec): hasil kalibrasi silang POA 2025-01..2026-07"
```

- [ ] **Step 4: Serahkan keputusan ke pengguna**

Laporkan status per stasiun cuaca. Jangan ubah config atau loader. Langkah selanjutnya mengikuti bagian "Langkah sesudah laporan" di spesifikasi.
