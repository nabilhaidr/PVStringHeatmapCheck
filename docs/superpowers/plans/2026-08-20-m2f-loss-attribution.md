# M2f Loss Attribution & Pareto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Membangun M2f v1 — atribusi rugi energi DC ke `availability_outage`, `dc_cable_fault`, `soiling`, dan `unexplained` per (string, hari), plus tabel Pareto, grafik waterfall, dan diagram Pareto.

**Architecture:** `LossLedger` menyimpan energi belum-terklaim per timestamp untuk satu (string, hari). Estimator dipanggil berurutan prioritas dan mengklaim dari ledger; energi yang sudah diklaim tidak bisa diklaim ulang, sehingga double-count mustahil secara struktural, bukan secara konvensi. `E_expected` dihitung dari POA terukur + Tcell terukur lewat `physics.compute_p_expected_per_string`, dikali koefisien gain bifacial per WB. Residual jatuh otomatis ke `unexplained`.

**Tech Stack:** Python 3, pandas, numpy, matplotlib, seaborn, openpyxl, pytest. Tidak ada dependensi baru — semuanya sudah di `requirements.txt`.

**Spec:** `docs/superpowers/specs/2026-08-11-m2f-loss-attribution-design.md`

> **Status (2026-08-25):** Task 1-8 sudah shipped dan lulus tes
> (`pv_pipeline/m2f/ledger.py`, `baseline.py`, `deficit.py`, `estimators.py`,
> `pareto.py`, `plots.py`, plus perubahan aditif di `peer_zscore.py`,
> `open_circuit.py`, `mppt_ratio.py`). Dokumen ini sudah disinkronkan ke kode
> yang shipped -- termasuk beberapa defek yang ditemukan review setelah draft
> awal tiap task (ditandai catatan "2026-08-25" di tiap task terkait). **Task
> 9 (`report.py`, orchestrator) BELUM dikerjakan** dan tertunda menunggu
> `raw data input/PV Module Temperature PLTS IKN.xlsx`: tanpa berkas itu,
> `CellTempProvider` raise `FileNotFoundError`, yang akan membuat tes Task 9
> gagal -- atau, lebih berbahaya, lulus VAKUM lewat jalur
> `provider_unavailable` tanpa benar-benar menguji closure. Lihat catatan di
> kepala Task 9 untuk detail dan lima kendala tambahan hasil review yang
> harus dipenuhi implementasi itu.

## Global Constraints

- **Lingkup rencana ini = v1 saja.** Kategori `shading` dan `low_irradiance_eff` (v2), serta `microcrack` dan `bifacial_underperf` (v3) TIDAK diimplementasikan di sini. Keduanya jatuh ke `unexplained` — itu perilaku yang benar, bukan bug.
- **Identitas closure wajib:** `sum(klaim) + residual == L_total` per (string, hari), toleransi `1e-6` kWh absolut. Ini invarian, bukan target.
- **Urutan prioritas tetap:** `availability_outage` → `dc_cable_fault` → `soiling` → `unexplained`. Urutan dibaca dari config `m2f.attribution_order`, tidak di-hardcode di logika klaim.
- `microcrack` dan `bifacial_underperf` bernilai `None`, BUKAN `0.0`.
- **Modul library tidak menulis file gambar.** `plots.py` mengembalikan `matplotlib.figure.Figure`; `savefig` adalah tanggung jawab pemanggil (notebook). Pola `pv_pipeline/viz.py`.
- **Kriteria penerimaan #6 di spec menyebut enam detektor; v1 hanya menyentuh tiga.** `m2a/shading.py` dan `m2a/low_irradiance.py` baru diperlukan di v2 dan TIDAK diubah di sini. `ground_fault.py` sempat masuk lingkup Task 3 lalu di-revert ke keadaan pre-M2f-nya (lihat catatan di Task 3): fault-nya inverter-level, sehingga counterfactual within-inverter (median sibling se-inverter) degenerate, dan counterfactual fleet-median lintas-inverter mencampur dua plant dengan tilt berbeda serta 24 vs 26 modul per string. Rugi `ground_fault` jatuh ke `unexplained` di v1, bukan diklaim.
- **Perubahan pada 3 detektor m2b harus aditif.** Tidak boleh mengubah findings, severity, atau artifact `StringStatus` yang sudah ada. Suite tes existing (`tests/unit/test_peer_zscore.py`, `test_open_circuit.py`, `test_mppt_ratio.py`) harus tetap hijau tanpa diubah.
- Tes di `tests/unit/test_m2f_*.py`, mengikuti konvensi `pytest.ini` (`testpaths = tests`, `python_files = test_*.py`).
- **Commit LOCAL saja** (`git commit`, tanpa push). User yang push ke 2 remote (nabilhaidr + ompltsikn) saat diminta.
- Pesan commit conventional dalam Bahasa Indonesia, mengikuti riwayat repo (`feat(m2f): ...`, `test(m2f): ...`).
- Console Windows cp1252 — jalankan script verifikasi dengan `python -X utf8` bila mencetak karakter non-ASCII. Untuk matplotlib headless, `matplotlib.use("Agg")` sebelum import pyplot.

---

### Task 1: LossLedger

Fondasi seluruh modul. Struktur data murni tanpa dependensi ke detektor mana pun, sehingga invarian closure bisa diuji terisolasi.

**Files:**
- Create: `pv_pipeline/m2f/__init__.py`
- Create: `pv_pipeline/m2f/ledger.py`
- Test: `tests/unit/test_m2f_ledger.py`

**Interfaces:**
- Consumes: tidak ada (task pertama).
- Produces:
  - `CLOSURE_TOLERANCE_KWH: float = 1e-6`
  - `LOCKED_CATEGORIES: List[str]`
  - `CLAIMABLE_CATEGORIES: List[str]`
  - `LossLedger(string_id: str, day: pd.Timestamp, e_expected: np.ndarray, e_actual: np.ndarray, index: Optional[pd.DatetimeIndex] = None)`
  - `LossLedger.l_total() -> float`
  - `LossLedger.remaining() -> np.ndarray`
  - `LossLedger.claim(category: str, amount_kwh_per_ts: np.ndarray | pd.Series) -> float`
  - `LossLedger.residual() -> float`
  - `LossLedger.totals() -> Dict[str, Optional[float]]`
  - `LossLedger.assert_closure() -> None`

> **Catatan (2026-08-25):** blok kode di bawah adalah draft AWAL Task 1. Review pertama menemukan dua defek nyata pada draft ini -- kategori tak dikenal diterima diam-diam (energi hilang dari laporan tanpa jejak) dan NaN di-coerce ke 0.0 alih-alih di-raise (closure "lulus" pada state yang sudah korup) -- lalu menambah proteksi lain sekaligus (whitelist `CLAIMABLE_CATEGORIES` di `claim()`, normalisasi `day` ke tengah malam DI constructor bukan didokumentasikan sebagai tanggung jawab caller, penyimpanan `index: Optional[pd.DatetimeIndex]` supaya `claim()` bisa menerima `pd.Series` dan memvalidasi alignment-nya, dan `l_total()` yang disederhanakan tanpa `np.nansum` ganda karena constructor sudah menolak NaN). Blok di bawah SUDAH diperbarui mengikuti hasil review itu -- **jangan** reproduksi versi draft mana pun; ini adalah kode final yang ter-commit.

- [ ] **Step 1: Tulis tes yang gagal**

Buat `tests/unit/test_m2f_ledger.py`:

```python
"""Tes LossLedger: invarian closure dan pencegahan double-count."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.ledger import CLOSURE_TOLERANCE_KWH, LossLedger


def _ledger(expected, actual):
    return LossLedger(
        string_id="WB03-INV01-PV5",
        day=pd.Timestamp("2026-05-13"),
        e_expected=np.array(expected, dtype=float),
        e_actual=np.array(actual, dtype=float),
    )


def test_l_total_is_expected_minus_actual():
    led = _ledger([1.0, 2.0, 3.0], [0.5, 2.0, 1.0])
    assert led.l_total() == pytest.approx(2.5)


def test_claim_reduces_remaining_so_next_category_cannot_reclaim():
    # WHY: inti anti-double-count. Kalau energi yang sama bisa diklaim dua
    # kategori, angka ROI cleaning jadi overstated dan keputusan biaya salah.
    led = _ledger([2.0, 2.0], [0.0, 0.0])
    first = led.claim("availability_outage", np.array([2.0, 2.0]))
    second = led.claim("dc_cable_fault", np.array([2.0, 2.0]))
    assert first == pytest.approx(4.0)
    assert second == pytest.approx(0.0)


def test_claim_is_clipped_to_remaining_per_timestamp():
    # Klaim berlebih di satu timestamp tidak boleh "meminjam" dari timestamp lain.
    led = _ledger([1.0, 5.0], [0.0, 0.0])
    claimed = led.claim("dc_cable_fault", np.array([10.0, 1.0]))
    assert claimed == pytest.approx(2.0)
    assert led.remaining().tolist() == pytest.approx([0.0, 4.0])


def test_negative_claim_is_rejected():
    led = _ledger([1.0], [0.0])
    with pytest.raises(ValueError, match="negatif"):
        led.claim("soiling", np.array([-0.5]))


def test_closure_holds_after_claims():
    # WHY: invarian yang membuat seluruh angka waterfall layak dipercaya.
    led = _ledger([3.0, 4.0, 1.0], [1.0, 1.0, 1.0])
    led.claim("availability_outage", np.array([1.0, 0.0, 0.0]))
    led.claim("dc_cable_fault", np.array([0.0, 2.0, 0.0]))
    totals = led.totals()
    claimed = sum(
        v for k, v in totals.items()
        if k != "unexplained" and v is not None
    )
    assert claimed + totals["unexplained"] == pytest.approx(
        led.l_total(), abs=CLOSURE_TOLERANCE_KWH
    )
    led.assert_closure()


def test_over_performance_gives_negative_residual_and_zero_claims():
    # WHY: string bifacial bisa melebihi ekspektasi. Residual harus menyerap
    # kelebihan itu; tidak boleh ada kategori mengklaim nilai negatif.
    led = _ledger([1.0, 1.0], [2.0, 2.0])
    claimed = led.claim("availability_outage", np.array([1.0, 1.0]))
    assert claimed == pytest.approx(0.0)
    assert led.residual() == pytest.approx(-2.0)
    led.assert_closure()


def test_locked_categories_are_none_not_zero():
    # WHY: 0.0 berarti "sudah diukur, tidak ada rugi". None berarti "belum ada
    # instrumen". Membedakan keduanya mencegah klaim palsu di laporan.
    led = _ledger([1.0], [0.0])
    totals = led.totals()
    assert totals["microcrack"] is None
    assert totals["bifacial_underperf"] is None


def test_unattempted_category_is_none_not_zero():
    # WHY: kategori yang estimatornya tidak pernah jalan berarti TIDAK TERUKUR.
    # Melaporkannya 0.0 akan terbaca "sudah dicek, aman".
    led = _ledger([1.0], [0.0])
    assert led.totals()["dc_cable_fault"] is None


def test_attempted_but_empty_claim_is_zero_not_none():
    # Estimator jalan dan tidak menemukan rugi -> 0.0, bukan None.
    led = _ledger([1.0], [1.0])
    led.claim("dc_cable_fault", np.array([0.0]))
    assert led.totals()["dc_cable_fault"] == pytest.approx(0.0)


def test_claiming_locked_category_raises():
    led = _ledger([1.0], [0.0])
    with pytest.raises(ValueError, match="terkunci"):
        led.claim("microcrack", np.array([1.0]))
```

- [ ] **Step 2: Jalankan tes, pastikan gagal**

Run: `python -m pytest tests/unit/test_m2f_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pv_pipeline.m2f'`

- [ ] **Step 3: Tulis implementasi minimal**

Buat `pv_pipeline/m2f/__init__.py`:

```python
"""M2f: Loss Attribution & Pareto Analysis.

Spec: docs/superpowers/specs/2026-08-11-m2f-loss-attribution-design.md
"""
from pv_pipeline.m2f.ledger import (
    CLAIMABLE_CATEGORIES,
    CLOSURE_TOLERANCE_KWH,
    LOCKED_CATEGORIES,
    LossLedger,
)

__all__ = [
    "CLAIMABLE_CATEGORIES",
    "CLOSURE_TOLERANCE_KWH",
    "LOCKED_CATEGORIES",
    "LossLedger",
]
```

Buat `pv_pipeline/m2f/ledger.py`:

```python
"""LossLedger: akuntansi klaim energi per (string, hari).

Tiap kategori mengklaim energi menurut urutan prioritas. Energi yang sudah
diklaim tidak dapat diklaim ulang, sehingga double-count antar detektor
tercegah secara struktural. Sisa yang tak terklaim menjadi ``unexplained``.

Invarian: ``sum(klaim) + residual == l_total`` dalam toleransi
``CLOSURE_TOLERANCE_KWH``.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd


CLOSURE_TOLERANCE_KWH: float = 1e-6

# Kategori tanpa instrumen. Dilaporkan None (bukan 0.0) supaya kontribusinya
# jatuh jujur ke `unexplained` alih-alih menyamar sebagai "tidak ada rugi".
LOCKED_CATEGORIES: List[str] = ["microcrack", "bifacial_underperf"]

# Kategori yang punya estimator di v1.
CLAIMABLE_CATEGORIES: List[str] = [
    "availability_outage",
    "dc_cable_fault",
    "soiling",
]


class LossLedger:
    """Sisa energi belum-terklaim untuk satu (string, hari).

    Parameters
    ----------
    string_id : str
        Identitas string, mis. ``"WB03-INV01-PV5"``.
    day : pd.Timestamp
        Tanggal (dinormalisasi ke tengah malam oleh konstruktor ini).
    e_expected, e_actual : np.ndarray
        Energi per timestamp (kWh), panjang sama.
    index : pd.DatetimeIndex, optional
        Timestamp per elemen ``e_expected``/``e_actual``. Bila diberikan,
        ``claim()`` menerima ``pd.Series`` dan memvalidasi indexnya sejajar
        dengan ini -- tanpanya, dua Series panjang sama tapi urutan/isi
        timestamp berbeda bisa ter-align diam-diam secara posisional.
    """

    def __init__(
        self,
        string_id: str,
        day: pd.Timestamp,
        e_expected: np.ndarray,
        e_actual: np.ndarray,
        index: Optional[pd.DatetimeIndex] = None,
    ):
        expected = np.asarray(e_expected, dtype=float)
        actual = np.asarray(e_actual, dtype=float)
        if expected.shape != actual.shape:
            raise ValueError(
                f"[m2f] e_expected {expected.shape} != e_actual {actual.shape}"
            )
        # NaN di sini berarti bug upstream (Task 2 seharusnya sudah
        # .fillna(0.0)), bukan data normal. Menolak di sini -- alih-alih
        # membiarkannya menjalar lewat _remaining -> claim() -> residual()
        # -- supaya bug muncul sebagai crash, bukan sebagai closure yang
        # diam-diam "lolos" karena `nan > tolerance` selalu False.
        if np.any(np.isnan(expected)) or np.any(np.isnan(actual)):
            raise ValueError(
                f"[m2f] NaN pada e_expected/e_actual untuk {string_id} {day}: "
                "seharusnya sudah di-fillna(0.0) sebelum sampai ke ledger."
            )
        self.string_id = string_id
        self.day = pd.Timestamp(day).normalize()
        self.e_expected = expected
        self.e_actual = actual
        # Hanya rugi positif yang dapat diklaim. Timestamp dengan
        # over-performance tidak menyediakan energi untuk diklaim siapa pun.
        self._remaining = np.maximum(expected - actual, 0.0)
        self._claims: Dict[str, float] = {}
        if index is None:
            self.index: Optional[pd.DatetimeIndex] = None
        else:
            index = pd.DatetimeIndex(index)
            if len(index) != len(expected):
                raise ValueError(
                    f"[m2f] panjang index {len(index)} != e_expected "
                    f"{len(expected)} untuk {string_id} {self.day}."
                )
            self.index = index

    def l_total(self) -> float:
        """Total rugi (kWh). Boleh negatif bila string melebihi ekspektasi."""
        # np.nansum di draft awal murni defensif -- constructor sudah
        # menolak NaN di atas, jadi cabang itu tidak pernah tercapai.
        # Disederhanakan supaya tidak ada dua penjaga yang bertentangan makna
        # (fail-loud di constructor vs diam-diam menelan NaN di sini).
        return float(self.e_expected.sum() - self.e_actual.sum())

    def remaining(self) -> np.ndarray:
        """Energi belum terklaim per timestamp (kWh), selalu >= 0."""
        return self._remaining.copy()

    def claim(self, category: str, amount_kwh_per_ts) -> float:
        """Klaim energi untuk ``category``, dipotong ke sisa per timestamp.

        ``amount_kwh_per_ts`` boleh berupa ``pd.Series`` (indexnya divalidasi
        sejajar dengan ``self.index`` -- lihat parameter ``index`` di
        constructor) atau array biasa (diperlakukan posisional, seperti
        sebelumnya).

        Returns
        -------
        float
            kWh yang benar-benar terklaim (bisa lebih kecil dari yang diminta).
        """
        if category in LOCKED_CATEGORIES:
            raise ValueError(
                f"[m2f] {category!r} terkunci (tidak ada instrumen), tidak boleh klaim."
            )
        if category not in CLAIMABLE_CATEGORIES:
            # Kategori tak dikenal (mis. typo "dc_cabel_fault") tidak boleh
            # diam-diam diterima: totals() hanya mengiterasi CLAIMABLE_CATEGORIES
            # + LOCKED_CATEGORIES, jadi klaim ini akan hilang dari laporan
            # padahal residual() sudah memotongnya dari l_total.
            raise ValueError(
                f"[m2f] {category!r} bukan kategori yang dikenal (lihat "
                "CLAIMABLE_CATEGORIES); klaim ditolak untuk cegah energi hilang diam-diam."
            )
        if isinstance(amount_kwh_per_ts, pd.Series):
            if self.index is None:
                raise ValueError(
                    f"[m2f] klaim {category!r} berupa pd.Series tapi ledger "
                    f"{self.string_id} {self.day} dibuat tanpa `index` -- tidak "
                    "ada acuan untuk memvalidasi alignment timestamp."
                )
            if not amount_kwh_per_ts.index.equals(self.index):
                raise ValueError(
                    f"[m2f] index klaim {category!r} untuk {self.string_id} "
                    f"{self.day} tidak sejajar dengan index ledger; klaim "
                    "ditolak untuk cegah dua Series panjang sama tapi "
                    "timestamp berbeda ter-align diam-diam secara posisional."
                )
            amount = amount_kwh_per_ts.to_numpy(dtype=float)
        else:
            amount = np.asarray(amount_kwh_per_ts, dtype=float)
        # NaN berarti string ini tidak bisa dievaluasi sama sekali (mis.
        # kolom tegangan hilang upstream di deficit_to_kwh). Coercion
        # nan_to_num(nan=0.0) di draft awal akan melaporkan "dicek, aman"
        # padahal sebenarnya "tidak bisa dicek" -- raise, seperti constructor
        # sudah menolak NaN di e_expected/e_actual (lihat baris atas).
        if np.any(np.isnan(amount)):
            raise ValueError(
                f"[m2f] NaN pada klaim {category!r} untuk {self.string_id} "
                f"{self.day}: string tidak bisa dievaluasi untuk kategori ini; "
                "ini harus dilaporkan 'tidak terukur', bukan disamarkan jadi "
                "0.0 kWh."
            )
        amount = np.nan_to_num(amount, posinf=0.0, neginf=0.0)
        if np.any(amount < 0.0):
            raise ValueError(
                f"[m2f] klaim negatif untuk {category!r}; klaim harus >= 0."
            )
        granted = np.minimum(amount, self._remaining)
        self._remaining = self._remaining - granted
        total = float(granted.sum())
        self._claims[category] = self._claims.get(category, 0.0) + total
        return total

    def residual(self) -> float:
        """Sisa yang tidak terjelaskan (kWh). Boleh negatif."""
        return self.l_total() - float(sum(self._claims.values()))

    def totals(self) -> Dict[str, Optional[float]]:
        """Peta kategori -> kWh. Kategori terkunci bernilai ``None``."""
        out: Dict[str, Optional[float]] = {}
        for cat in CLAIMABLE_CATEGORIES:
            # None = estimator tidak pernah dijalankan (mis. detektornya tidak
            # menghasilkan artefak). 0.0 = dijalankan dan tidak menemukan rugi.
            # Membedakan keduanya mencegah "tidak terukur" terbaca "aman".
            out[cat] = float(self._claims[cat]) if cat in self._claims else None
        for cat in LOCKED_CATEGORIES:
            out[cat] = None
        out["unexplained"] = self.residual()
        return out

    def assert_closure(self) -> None:
        """Raise bila identitas closure dilanggar.

        Catatan: ``residual()`` didefinisikan sebagai ``l_total() -
        sum(claims)``, jadi drift yang dicek di sini secara aljabar selalu
        tereduksi ke ``abs(0)``. Fungsi ini TIDAK membuktikan atribusi
        benar -- ini murni pengecekan bahwa `_claims` dan `residual()`
        masih konsisten (mis. tidak ada NaN yang menyelinap). Proteksi
        anti-double-count yang sesungguhnya datang dari pemotongan
        per-timestamp di ``remaining()``/``claim()``, bukan dari sini.
        """
        claimed = float(sum(self._claims.values()))
        drift = abs(claimed + self.residual() - self.l_total())
        if drift > CLOSURE_TOLERANCE_KWH:
            raise AssertionError(
                f"[m2f] closure dilanggar untuk {self.string_id} {self.day}: "
                f"drift={drift:.3e} kWh > {CLOSURE_TOLERANCE_KWH:.1e}"
            )
```

- [ ] **Step 4: Jalankan tes, pastikan lulus**

Run: `python -m pytest tests/unit/test_m2f_ledger.py -v`
Expected: PASS, 20 tes -- 10 dari draft awal (Step 1) ditambah 10 dari review pertama, menguji tepat proteksi baru di atas: kategori tak dikenal ditolak, NaN pada konstruksi/klaim ditolak (bukan di-coerce), `day` dinormalisasi ke tengah malam, `claim()` menerima `pd.Series` dan menolak index yang tidak sejajar (shuffled/disjoint/tanpa `index` ledger), dan array biasa masih diterima posisional seperti sebelumnya.

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/m2f/__init__.py pv_pipeline/m2f/ledger.py tests/unit/test_m2f_ledger.py
git commit -m "feat(m2f): LossLedger dengan invarian closure dan anti-double-count"
```

---

### Task 2: Baseline E_expected + kalibrasi bifacial

**Files:**
- Create: `pv_pipeline/m2f/baseline.py`
- Test: `tests/unit/test_m2f_baseline.py`

**Interfaces:**
- Consumes: `pv_pipeline.physics.compute_p_expected_per_string(poa_wm2, tcell_c, panel_spec, wb_id)` (mengembalikan Watt per string); `pv_pipeline.panel_spec.PanelSpec.from_yaml(path)`.
- Produces:
  - `DEFAULT_FREQ_HOURS: float = 5.0 / 60.0`
  - `compute_expected_energy_kwh(poa_wm2, tcell_c, panel_spec, wb_id, *, bifacial_gain=1.0, freq_hours=DEFAULT_FREQ_HOURS) -> pd.Series`
  - `compute_actual_energy_kwh(power_kw, *, freq_hours=DEFAULT_FREQ_HOURS) -> pd.Series`
  - `calibrate_bifacial_gain(expected_kwh_per_string, actual_kwh_per_string, *, min_strings=3) -> float`

- [ ] **Step 1: Tulis tes yang gagal**

Buat `tests/unit/test_m2f_baseline.py`:

```python
"""Tes baseline M2f: konversi daya->energi dan kalibrasi gain bifacial."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.baseline import (
    DEFAULT_FREQ_HOURS,
    calibrate_bifacial_gain,
    compute_actual_energy_kwh,
    compute_expected_energy_kwh,
)
from pv_pipeline.panel_spec import PanelSpec


@pytest.fixture
def spec():
    return PanelSpec.from_yaml("config/panel_spec.yaml")


def test_expected_energy_at_stc_matches_nameplate(spec):
    # Pada STC (1000 W/m2, 25 C), 26 modul x 625 W = 16,25 kW.
    # Satu interval 5 menit -> 16,25 * (5/60) kWh.
    idx = pd.date_range("2026-05-13 12:00", periods=1, freq="5min")
    poa = pd.Series([1000.0], index=idx)
    tcell = pd.Series([25.0], index=idx)
    out = compute_expected_energy_kwh(poa, tcell, spec, "WB03")
    assert out.iloc[0] == pytest.approx(16.25 * DEFAULT_FREQ_HOURS)


def test_expected_energy_uses_per_wb_module_count(spec):
    # WB01 = 24 modul, WB03 = 26 modul. Rasio harus 24/26.
    idx = pd.date_range("2026-05-13 12:00", periods=1, freq="5min")
    poa = pd.Series([1000.0], index=idx)
    tcell = pd.Series([25.0], index=idx)
    wb01 = compute_expected_energy_kwh(poa, tcell, spec, "WB01").iloc[0]
    wb03 = compute_expected_energy_kwh(poa, tcell, spec, "WB03").iloc[0]
    assert wb01 / wb03 == pytest.approx(24.0 / 26.0)


def test_bifacial_gain_scales_expected_linearly(spec):
    idx = pd.date_range("2026-05-13 12:00", periods=1, freq="5min")
    poa = pd.Series([800.0], index=idx)
    tcell = pd.Series([45.0], index=idx)
    base = compute_expected_energy_kwh(poa, tcell, spec, "WB03", bifacial_gain=1.0)
    lifted = compute_expected_energy_kwh(poa, tcell, spec, "WB03", bifacial_gain=1.05)
    assert lifted.iloc[0] == pytest.approx(base.iloc[0] * 1.05)


def test_actual_energy_is_riemann_sum():
    idx = pd.date_range("2026-05-13 12:00", periods=3, freq="5min")
    power = pd.Series([12.0, 12.0, 6.0], index=idx)
    out = compute_actual_energy_kwh(power)
    assert out.sum() == pytest.approx(30.0 * DEFAULT_FREQ_HOURS)


def test_actual_energy_treats_nan_as_zero():
    # WHY: sampel hilang berarti energi tidak tercatat. Selisihnya harus
    # muncul sebagai rugi yang diatribusikan, bukan disembunyikan.
    idx = pd.date_range("2026-05-13 12:00", periods=3, freq="5min")
    power = pd.Series([12.0, np.nan, 6.0], index=idx)
    out = compute_actual_energy_kwh(power)
    assert out.sum() == pytest.approx(18.0 * DEFAULT_FREQ_HOURS)


def test_calibrate_bifacial_gain_is_median_ratio():
    # WHY: E_expected memakai POA depan saja, sedangkan modulnya bifacial.
    # Tanpa kalibrasi, string sehat tampak "rugi" negatif dan seluruh
    # waterfall bias.
    expected = pd.Series([100.0, 100.0, 100.0], index=["a", "b", "c"])
    actual = pd.Series([104.0, 106.0, 108.0], index=["a", "b", "c"])
    assert calibrate_bifacial_gain(expected, actual) == pytest.approx(1.06)


def test_calibrate_bifacial_gain_ignores_zero_expected():
    expected = pd.Series([100.0, 0.0, 100.0], index=["a", "b", "c"])
    actual = pd.Series([105.0, 50.0, 105.0], index=["a", "b", "c"])
    assert calibrate_bifacial_gain(expected, actual, min_strings=2) == pytest.approx(1.05)


def test_calibrate_bifacial_gain_refuses_thin_sample():
    # WHY: gain dari 1-2 string bukan kalibrasi, itu kebetulan.
    expected = pd.Series([100.0, 100.0], index=["a", "b"])
    actual = pd.Series([105.0, 105.0], index=["a", "b"])
    with pytest.raises(ValueError, match="minimal 3 string"):
        calibrate_bifacial_gain(expected, actual, min_strings=3)
```

- [ ] **Step 2: Jalankan tes, pastikan gagal**

Run: `python -m pytest tests/unit/test_m2f_baseline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pv_pipeline.m2f.baseline'`

- [ ] **Step 3: Tulis implementasi minimal**

Buat `pv_pipeline/m2f/baseline.py`:

```python
"""Baseline energi M2f: E_expected dari POA+Tcell terukur, dan E_actual.

`E_expected` memakai ``physics.compute_p_expected_per_string`` yang hanya
memperhitungkan POA DEPAN, sedangkan modul Jinko JKM625N bifacial. Koefisien
``bifacial_gain`` per WB mengoreksi under-estimate itu; dikalibrasi dari string
sehat pada hari clear-sky (lihat :func:`calibrate_bifacial_gain`).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pv_pipeline.panel_spec import PanelSpec
from pv_pipeline.physics import compute_p_expected_per_string


# Sampling PLTS-IKN = 5 menit.
DEFAULT_FREQ_HOURS: float = 5.0 / 60.0


def compute_expected_energy_kwh(
    poa_wm2: pd.Series,
    tcell_c: pd.Series,
    panel_spec: PanelSpec,
    wb_id: str,
    *,
    bifacial_gain: float = 1.0,
    freq_hours: float = DEFAULT_FREQ_HOURS,
) -> pd.Series:
    """Energi harapan per timestamp (kWh) untuk satu PV string.

    ``compute_p_expected_per_string`` mengembalikan Watt per string; dibagi
    1000 menjadi kW, dikali ``freq_hours`` menjadi kWh.
    """
    p_watt = compute_p_expected_per_string(poa_wm2, tcell_c, panel_spec, wb_id)
    if not isinstance(p_watt, pd.Series):
        p_watt = pd.Series(p_watt, index=poa_wm2.index)
    kwh = (p_watt / 1000.0) * float(freq_hours) * float(bifacial_gain)
    kwh = kwh.fillna(0.0).clip(lower=0.0)
    kwh.name = "e_expected_kwh"
    return kwh


def compute_actual_energy_kwh(
    power_kw: pd.Series,
    *,
    freq_hours: float = DEFAULT_FREQ_HOURS,
) -> pd.Series:
    """Energi aktual per timestamp (kWh) dari daya DC string (kW).

    NaN diperlakukan sebagai 0 kWh: sampel hilang berarti energi tidak
    tercatat, dan selisihnya akan muncul sebagai rugi yang harus diatribusikan
    -- bukan disembunyikan.
    """
    kwh = pd.to_numeric(power_kw, errors="coerce").fillna(0.0) * float(freq_hours)
    kwh.name = "e_actual_kwh"
    return kwh


def calibrate_bifacial_gain(
    expected_kwh_per_string: pd.Series,
    actual_kwh_per_string: pd.Series,
    *,
    min_strings: int = 3,
) -> float:
    """Median rasio aktual/harapan pada string sehat di hari clear-sky.

    Parameters
    ----------
    expected_kwh_per_string, actual_kwh_per_string : pd.Series
        Total kWh per string, di-index oleh string_id. ``expected`` dihitung
        dengan ``bifacial_gain=1.0``.
    min_strings : int, default 3
        Jumlah string minimum. Di bawah ini kalibrasi ditolak -- gain dari
        satu-dua string adalah kebetulan, bukan kalibrasi.
    """
    expected, actual = expected_kwh_per_string.align(
        actual_kwh_per_string, join="inner"
    )
    valid = expected > 0.0
    n_valid = int(valid.sum())
    if n_valid < min_strings:
        raise ValueError(
            f"[m2f] kalibrasi bifacial butuh minimal {min_strings} string dengan "
            f"expected > 0; hanya ada {n_valid}."
        )
    ratio = actual[valid] / expected[valid]
    return float(np.median(ratio.to_numpy(dtype=float)))
```

- [ ] **Step 4: Jalankan tes, pastikan lulus**

Run: `python -m pytest tests/unit/test_m2f_baseline.py -v`
Expected: PASS, 8 tes.

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/m2f/baseline.py tests/unit/test_m2f_baseline.py
git commit -m "feat(m2f): baseline E_expected/E_actual dan kalibrasi gain bifacial"
```

---

### Task 3: Artefak deret waktu di 3 detektor m2b

> **Catatan (2026-08-25):** `ground_fault.py` awalnya masuk lingkup task ini
> (draft di bawah masih menyebutnya "4 detektor"). Sebuah fix belakangan
> me-revert `ground_fault.py` ke keadaan pre-M2f-nya: fault-nya inverter-level,
> sehingga counterfactual within-inverter (median sibling se-inverter)
> degenerate -- tidak ada "sibling sehat" untuk dibandingkan saat seluruh
> inverter kena ground fault -- dan counterfactual fleet-median lintas-inverter
> mencampur dua plant dengan tilt berbeda serta 24 vs 26 modul per string,
> jadi bukan counterfactual yang valid. Rugi `ground_fault` jatuh ke
> `unexplained` di v1. **Task ini HANYA menyentuh `peer_zscore.py`,
> `open_circuit.py`, dan `mppt_ratio.py`.**

Ketiga detektor sudah menghitung arus aktual dan arus counterfactual per timestamp di dalam `run()`, tetapi hanya menyimpan skor akhirnya. M2f butuh deret waktunya. Perubahan bersifat aditif: satu artifact baru per detektor, dengan skema identik.

**Files:**
- Create: `pv_pipeline/m2f/deficit.py`
- Modify: `pv_pipeline/peer_zscore.py:523` (tambah blok setelah baris ini)
- Modify: `pv_pipeline/open_circuit.py:432` (tambah blok setelah baris ini)
- Modify: `pv_pipeline/mppt_ratio.py:358` (tambah blok setelah baris ini)
- Test: `tests/unit/test_m2f_deficit.py`, `tests/unit/test_m2f_detector_deficit.py` (file terpisah, khusus mem-pin aritmetika kW dan flagged mask `open_circuit`/`mppt_ratio` -- fixture existing kedua detektor itu current-only, tanpa kolom voltage, jadi tidak ada tes lama yang memverifikasi `actual_kw`/`counterfactual_kw` atau bahwa `flagged` mengikuti mask yang sudah ter-debounce).

**Interfaces:**
- Consumes: `DEFAULT_FREQ_HOURS` (Task 2).
- Produces:
  - `DEFICIT_COLUMNS: List[str]` = `["poa_source", "timestamp", "inverter_id", "pv_string", "actual_kw", "counterfactual_kw", "flagged"]` -- `poa_source` WAJIB: tiap detektor loop di 5 POA source dan flag mask-nya berbeda per source, jadi tanpa kolom ini `(inverter_id, pv_string, timestamp)` bukan key unik.
  - `build_deficit_frame(timestamps, poa_source, inverter_id, pv_string, actual_kw, counterfactual_kw, flagged) -> pd.DataFrame`
  - `deficit_to_kwh(frame, *, freq_hours) -> pd.Series` — indexed by timestamp, kWh >= 0 pada baris `flagged`; TAPI baris `flagged` yang gap-nya NaN (mis. kolom tegangan hilang upstream) dipertahankan sebagai NaN, bukan diisi 0.0 -- lihat `LossLedger.claim()` (Task 1), yang me-raise pada NaN alih-alih menelannya.
  - `reduce_deficit_frames(frames, *, poa_source, index, freq_hours=DEFAULT_FREQ_HOURS) -> pd.Series` -- gabung artefak beberapa detektor jadi satu Series kWh per timestamp: saring ke `poa_source` yang diminta, konversi tiap frame ke kWh, ambil MAKSIMUM elemen-per-elemen antar detektor (dua detektor menandai fisik yang sama menjelaskan SATU rugi, bukan dua -- menjumlahkan akan melipatgandakannya), lalu `reindex` ke `index` penuh ledger dengan `fill_value=0.0`. **TIDAK ADA** di draft awal task ini; ditambahkan karena production `emit_all_sources: true` (5 POA source) membuat ketiga detektor bersama menghasilkan hingga 15 frame per string, dan `.reindex()` polos atasnya melempar `ValueError: cannot reindex on an axis with duplicate labels`.

Artefak ini **TIDAK** masuk `self.artifacts` (channel Excel
`M2Engine.write_xlsx_multi`, tanpa try/except): pada volume produksi (5 POA
source x ribuan string x ratusan timestamp) baris defisit per detektor bisa
melampaui limit 1.048.576 baris per sheet pandas/Excel dan menggagalkan
seluruh workbook harian. Sebagai gantinya, tiap detektor menyimpannya di
`self.deficit_frames: List[pd.DataFrame]` -- channel terpisah, di-reset di
awal `run()`, di-`extend()` (bukan assign ulang) di akhir loop per string.

- [ ] **Step 1: Tulis tes yang gagal**

Buat `tests/unit/test_m2f_deficit.py`:

```python
"""Tes skema artefak deret waktu defisit dan konversinya ke kWh."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.baseline import DEFAULT_FREQ_HOURS
from pv_pipeline.m2f.deficit import (
    build_deficit_frame,
    deficit_to_kwh,
    reduce_deficit_frames,
)


def _frame(actual, counterfactual, flagged, poa_source="pyranometer"):
    idx = pd.date_range("2026-05-13 12:00", periods=len(actual), freq="5min")
    return build_deficit_frame(
        timestamps=idx,
        poa_source=poa_source,
        inverter_id="WB03-INV01",
        pv_string="PV5",
        actual_kw=np.array(actual, dtype=float),
        counterfactual_kw=np.array(counterfactual, dtype=float),
        flagged=np.array(flagged, dtype=bool),
    )


def test_frame_has_exact_schema():
    # WHY: dibandingkan ke daftar literal, bukan ke DEFICIT_COLUMNS itu sendiri
    # -- kalau dibandingkan ke konstanta yang sama yang dipakai
    # build_deficit_frame untuk mem-filter kolom, tes ini tidak pernah bisa
    # gagal walau skema berubah diam-diam.
    frame = _frame([1.0], [2.0], [True])
    assert list(frame.columns) == [
        "poa_source", "timestamp", "inverter_id", "pv_string",
        "actual_kw", "counterfactual_kw", "flagged",
    ]


def test_deficit_counts_only_flagged_timestamps():
    # WHY: defisit di luar jendela ter-flag bukan milik detektor ini. Kalau
    # ikut diklaim, kategori lain kehilangan energinya.
    frame = _frame([1.0, 1.0], [3.0, 3.0], [True, False])
    kwh = deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)
    assert kwh.sum() == pytest.approx(2.0 * DEFAULT_FREQ_HOURS)


def test_negative_deficit_is_clipped_to_zero():
    # String yang melampaui counterfactual bukan "rugi negatif".
    frame = _frame([5.0], [3.0], [True])
    kwh = deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)
    assert kwh.sum() == pytest.approx(0.0)


def test_kwh_is_indexed_by_timestamp():
    frame = _frame([1.0, 1.0], [2.0, 2.0], [True, True])
    kwh = deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)
    assert isinstance(kwh.index, pd.DatetimeIndex)
    assert len(kwh) == 2


def test_missing_column_raises():
    frame = _frame([1.0], [2.0], [True]).drop(columns=["flagged"])
    with pytest.raises(KeyError, match="flagged"):
        deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)


def test_flagged_nan_gap_is_preserved_not_zeroed():
    # WHY: NaN pada actual_kw/counterfactual_kw di baris ter-flag berarti
    # string tidak bisa dievaluasi sama sekali (mis. kolom tegangan hilang
    # upstream di open_circuit.py/mppt_ratio.py), BUKAN "dicek, tidak ada
    # rugi". Mengisi 0.0 di sini akan membuat dc_cable_fault melaporkan
    # 0.0 kWh untuk string yang sebenarnya tak terukur. INI MENGGANTI
    # `test_nan_deficit_is_zero` dari draft awal -- kontraknya dibalik oleh
    # review: NaN pada baris flagged sekarang TETAP NaN, tidak di-fillna(0.0).
    frame = _frame([np.nan], [3.0], [True])
    kwh = deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)
    assert pd.isna(kwh.iloc[0])


def test_unflagged_nan_gap_is_still_zero():
    # Timestamp tak ter-flag dijamin 0.0 walau gapnya NaN -- itu memang
    # bukan kandidat rugi kategori ini, beda dari kasus flagged-tapi-NaN.
    frame = _frame([np.nan], [3.0], [False])
    kwh = deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)
    assert kwh.iloc[0] == pytest.approx(0.0)


def test_reduce_deficit_frames_takes_max_not_sum_across_detectors():
    # WHY: dua detektor menandai fisik yang sama pada timestamp yang sama
    # menjelaskan SATU rugi, bukan dua -- summing akan melipatgandakannya.
    idx = pd.date_range("2026-05-13 12:00", periods=2, freq="5min")
    frame_a = _frame([1.0, 1.0], [4.0, 4.0], [True, True])  # gap = 3.0
    frame_b = _frame([1.0, 1.0], [3.0, 3.0], [True, True])  # gap = 2.0
    reduced = reduce_deficit_frames(
        [frame_a, frame_b], poa_source="pyranometer", index=idx
    )
    expected_per_ts = 3.0 * DEFAULT_FREQ_HOURS  # max(3.0, 2.0), bukan 5.0
    assert reduced.sum() == pytest.approx(2 * expected_per_ts)


def test_reduce_deficit_frames_selects_only_given_poa_source():
    idx = pd.date_range("2026-05-13 12:00", periods=2, freq="5min")
    frame_pyra = _frame([1.0, 1.0], [3.0, 3.0], [True, True], poa_source="pyranometer")
    frame_sat = _frame([1.0, 1.0], [9.0, 9.0], [True, True], poa_source="satellite")
    reduced = reduce_deficit_frames(
        [frame_pyra, frame_sat], poa_source="pyranometer", index=idx
    )
    assert reduced.sum() == pytest.approx(2 * 2.0 * DEFAULT_FREQ_HOURS)


def test_reduce_deficit_frames_handles_duplicate_timestamps_without_raising():
    # WHY: production emit_all_sources=True x 3 detektor -> union defisit
    # mentah membawa hingga 15 baris per timestamp per string; `.reindex()`
    # polos pada itu melempar
    # `ValueError: cannot reindex on an axis with duplicate labels`.
    idx = pd.date_range("2026-05-13 12:00", periods=3, freq="5min")
    frame_a = _frame([0.0, 0.0, 0.0], [2.0, 2.0, 2.0], [True, True, True])
    frame_b = _frame([0.0, 0.0, 0.0], [2.0, 2.0, 2.0], [True, True, True])
    reduced = reduce_deficit_frames(
        [frame_a, frame_b], poa_source="pyranometer", index=idx
    )
    assert len(reduced) == 3
    assert reduced.sum() == pytest.approx(3 * 2.0 * DEFAULT_FREQ_HOURS)


def test_reduce_deficit_frames_reindexes_with_zero_fill():
    idx = pd.date_range("2026-05-13 12:00", periods=4, freq="5min")
    frame = _frame([1.0], [3.0], [True])  # hanya 1 timestamp dari 4
    reduced = reduce_deficit_frames([frame], poa_source="pyranometer", index=idx)
    assert len(reduced) == 4
    assert reduced.iloc[1:].tolist() == [0.0, 0.0, 0.0]


def test_reduce_deficit_frames_empty_list_returns_zero_series():
    idx = pd.date_range("2026-05-13 12:00", periods=2, freq="5min")
    reduced = reduce_deficit_frames([], poa_source="pyranometer", index=idx)
    assert reduced.tolist() == [0.0, 0.0]


def test_reduce_deficit_frames_all_nan_at_timestamp_is_preserved():
    # WHY: kalau SEMUA detektor tidak bisa mengevaluasi satu timestamp (mis.
    # kolom tegangan hilang di semua), hasilnya harus tetap NaN -- bukan
    # di-fillna ke 0.0, supaya "tidak terukur" tidak menyamar jadi "aman"
    # setelah lolos ke claim_dc_cable_fault -> ledger.claim().
    idx = pd.date_range("2026-05-13 12:00", periods=1, freq="5min")
    frame_a = _frame([np.nan], [3.0], [True])
    frame_b = _frame([np.nan], [5.0], [True])
    reduced = reduce_deficit_frames(
        [frame_a, frame_b], poa_source="pyranometer", index=idx
    )
    assert pd.isna(reduced.iloc[0])
```

- [ ] **Step 2: Jalankan tes, pastikan gagal**

Run: `python -m pytest tests/unit/test_m2f_deficit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pv_pipeline.m2f.deficit'`

- [ ] **Step 3: Tulis `deficit.py`**

Buat `pv_pipeline/m2f/deficit.py`:

```python
"""Skema artefak deret waktu defisit, dipakai bersama oleh 3 detektor m2b.

Detektor m2b sudah menghitung arus aktual dan arus counterfactual (median
sibling / median partner MPPT) per timestamp, tetapi hanya menyimpan skor
akhirnya. M2f butuh deret waktunya untuk mengklaim energi ke ledger.
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd

from pv_pipeline.m2f.baseline import DEFAULT_FREQ_HOURS


DEFICIT_COLUMNS: List[str] = [
    "poa_source",
    "timestamp",
    "inverter_id",
    "pv_string",
    "actual_kw",
    "counterfactual_kw",
    "flagged",
]


def build_deficit_frame(
    timestamps,
    poa_source: str,
    inverter_id: str,
    pv_string: str,
    actual_kw: np.ndarray,
    counterfactual_kw: np.ndarray,
    flagged: np.ndarray,
) -> pd.DataFrame:
    """Rakit satu frame defisit dengan skema tetap ``DEFICIT_COLUMNS``.

    ``poa_source`` wajib disertakan -- setiap detektor loop di 5 POA source
    dan flag mask-nya berbeda per source, jadi tanpa kolom ini
    ``(inverter_id, pv_string, timestamp)`` bukan key unik.
    """
    idx = pd.DatetimeIndex(timestamps)
    frame = pd.DataFrame(
        {
            "poa_source": str(poa_source),
            "timestamp": idx,
            "inverter_id": str(inverter_id),
            "pv_string": str(pv_string),
            "actual_kw": np.asarray(actual_kw, dtype=float),
            "counterfactual_kw": np.asarray(counterfactual_kw, dtype=float),
            "flagged": np.asarray(flagged, dtype=bool),
        }
    )
    return frame[DEFICIT_COLUMNS]


def deficit_to_kwh(frame: pd.DataFrame, *, freq_hours: float) -> pd.Series:
    """Defisit energi (kWh) per timestamp, hanya pada baris ``flagged``.

    Defisit negatif (string melampaui counterfactual) dipotong ke nol -- itu
    bukan rugi, dan bukan urusan kategori ini.

    Timestamp yang TIDAK ``flagged`` dijamin 0.0 -- itu memang bukan kandidat
    rugi kategori ini. Timestamp yang ``flagged`` TAPI gapnya NaN (mis. kolom
    tegangan hilang upstream sehingga ``actual_kw``/``counterfactual_kw`` tak
    terhitung) dipertahankan sebagai NaN, BUKAN diisi 0.0 -- string itu tidak
    bisa dievaluasi sama sekali, dan mengisi 0.0 di sini akan melaporkan
    "dicek, aman" padahal sebenarnya "tidak bisa dicek". Batas yang benar-
    benar menegakkan ini ada di ``LossLedger.claim()``, yang me-raise pada
    NaN alih-alih menelannya.
    """
    missing = [c for c in DEFICIT_COLUMNS if c not in frame.columns]
    if missing:
        raise KeyError(f"[m2f] frame defisit kehilangan kolom {missing}.")
    actual = pd.to_numeric(frame["actual_kw"], errors="coerce")
    counterfactual = pd.to_numeric(frame["counterfactual_kw"], errors="coerce")
    gap = (counterfactual - actual).clip(lower=0.0)
    gap = gap.where(frame["flagged"].astype(bool), 0.0)
    out = pd.Series(
        (gap * float(freq_hours)).to_numpy(dtype=float),
        index=pd.DatetimeIndex(frame["timestamp"]),
        name="deficit_kwh",
    )
    return out


def reduce_deficit_frames(
    frames: List[pd.DataFrame],
    *,
    poa_source: str,
    index: pd.DatetimeIndex,
    freq_hours: float = DEFAULT_FREQ_HOURS,
) -> pd.Series:
    """Gabung frame defisit dari beberapa detektor jadi satu Series kWh.

    Production ``emit_all_sources: true`` dengan 5 POA source berarti ketiga
    detektor m2b bersama-sama menghasilkan hingga 15 frame per string (3
    detektor x 5 source). Menyatukan semuanya lewat ``.reindex()`` polos
    melempar ``ValueError: cannot reindex on an axis with duplicate labels``,
    dan ``groupby(level=0).sum()`` menghitung dua kali energi fisik yang sama
    saat dua detektor menandai string yang sama pada timestamp yang sama.

    Langkah: (1) buang baris yang bukan ``poa_source`` yang diminta -- sisa
    per detektor lalu punya satu baris per timestamp; (2) konversi tiap frame
    ke kWh lewat :func:`deficit_to_kwh`; (3) ambil MAKSIMUM elemen-per-elemen
    antar detektor -- dua detektor menandai fisik yang sama menjelaskan SATU
    rugi, bukan dua, jadi menjumlahkan akan melipatgandakannya; (4) reindex ke
    ``index`` (timeline penuh ledger), isi 0.0 untuk timestamp yang tak
    tercakup detektor manapun.
    """
    idx = pd.DatetimeIndex(index)
    per_detector: List[pd.Series] = []
    for frame in frames:
        subset = frame[frame["poa_source"] == poa_source]
        if subset.empty:
            continue
        per_detector.append(deficit_to_kwh(subset, freq_hours=freq_hours))

    if not per_detector:
        return pd.Series(0.0, index=idx, dtype=float, name="deficit_kwh")

    combined = pd.concat(per_detector, axis=1)
    # max(skipna=True): kalau SEMUA detektor NaN pada satu timestamp (mis.
    # kolom tegangan hilang di semua), hasilnya tetap NaN -- dipertahankan
    # (bukan di-fillna ke 0.0) supaya "tidak bisa dievaluasi" tidak menyamar
    # jadi "dicek, aman", sejalan dengan deficit_to_kwh di atas. reindex di
    # bawah hanya mengisi 0.0 pada timestamp yang SAMA SEKALI tak tercakup
    # detektor manapun (bukan pada NaN yang sudah ada).
    reduced = combined.max(axis=1, skipna=True)
    reduced = reduced.reindex(idx, fill_value=0.0)
    reduced.name = "deficit_kwh"
    return reduced.astype(float)
```

- [ ] **Step 4: Jalankan tes, pastikan lulus**

Run: `python -m pytest tests/unit/test_m2f_deficit.py -v`
Expected: PASS, 14 tes.

- [ ] **Step 5: Sambungkan ke 3 detektor**

Tambahkan import di bagian atas ketiga file, setelah import `pv_pipeline.core` yang sudah ada:

```python
from pv_pipeline.m2f.deficit import build_deficit_frame
```

Di setiap `run()`, inisialisasi `self.deficit_frames: List[pd.DataFrame] = []` di `__init__` (bukan `self.artifacts` -- lihat alasan channel-Excel di atas), dan **reset** di awal `run()` bersebelahan dengan inisialisasi `artifact_rows` yang sudah ada:

```python
        # self.artifacts["StringStatus"] di bawah sudah di-ASSIGN ulang tiap
        # run(), tapi deficit_frames di-EXTEND -- tanpa reset ini, run() kedua
        # pada instance yang sama menduplikasi deficit_frames walau
        # artefaknya sendiri tidak berubah.
        self.deficit_frames = []
        deficit_rows: list = []
```

Di dalam loop per-string, tepat sebelum `artifact_rows.append({...})` yang sudah ada, tambahkan (ganti nama variabel arus/tegangan/mask dengan yang sudah ada di scope masing-masing detektor — lihat pemetaan di bawah):

```python
        deficit_rows.append(build_deficit_frame(
            timestamps=ts_clean,
            poa_source=poa_source,
            inverter_id=str(inverter_id),
            pv_string=f"PV{pv_n}",
            actual_kw=(i_string * v_string / 1000.0).to_numpy(),
            counterfactual_kw=(i_counterfactual * v_string / 1000.0).to_numpy(),
            flagged=flag_mask,
        ))
```

Lalu emit, tepat setelah baris `self.artifacts["StringStatus"] = pd.DataFrame(...)` yang sudah ada:

```python
        self.deficit_frames.extend(deficit_rows)
```

Pemetaan `i_counterfactual` dan `flag_mask` per detektor:

| File | Baris emit | `i_counterfactual` | `flag_mask` |
|---|---|---|---|
| `peer_zscore.py` | 523 | median arus sibling se-inverter (exclude diri sendiri) per timestamp | `mask_poa AND should_emit_per_spec` -- BUKAN cuma `flagged`: `should_emit_per_spec` adalah gate konfirmasi voc_ok (spec 4.2.3); string yang dilaporkan NORMAL (gagal gate itu) tidak boleh diklaim kWh-nya |
| `open_circuit.py` | 432 | `I_q95` sibling per timestamp (sudah dihitung per spec 4.2.3) | `_debounced_qualifying_mask(qualifying, debounce_steps)` -- mask event yang SUDAH lolos debounce, bukan `qualifying` mentah |
| `mppt_ratio.py` | 358 | median arus partner se-MPPT per timestamp | `_debounced_qualifying_mask(qualifying, debounce_steps)`, sama seperti `open_circuit.py` -- production `debounce_steps=20` (~100 menit) supaya dip 5-menit terisolasi di cloud edge tidak diklaim kWh-nya |

`ground_fault.py` TIDAK disentuh -- lihat catatan revert di atas.

- [ ] **Step 6: Verifikasi detektor lama tidak berubah**

Run: `python -m pytest tests/unit/test_peer_zscore.py tests/unit/test_open_circuit.py tests/unit/test_ground_fault.py tests/unit/test_mppt_ratio.py -v`
Expected: PASS — semua tes existing tetap hijau tanpa satupun diubah. Kalau ada yang merah, perubahannya tidak aditif dan implementasinya harus diperbaiki — bukan tesnya yang disesuaikan. (`test_ground_fault.py` tetap dijalankan sebagai regression check meski filenya tidak diubah oleh task ini.)

- [ ] **Step 7: Commit**

```bash
git add pv_pipeline/m2f/deficit.py tests/unit/test_m2f_deficit.py tests/unit/test_m2f_detector_deficit.py pv_pipeline/peer_zscore.py pv_pipeline/open_circuit.py pv_pipeline/mppt_ratio.py
git commit -m "feat(m2f): artefak deret waktu defisit di 3 detektor m2b"
```

---

### Task 4: Estimator availability_outage

Kategori prioritas pertama. Paling sederhana dan paling pasti: saat string mati, seluruh `E_expected` pada jendela itu hilang.

**Files:**
- Create: `pv_pipeline/m2f/estimators.py`
- Test: `tests/unit/test_m2f_estimators.py`

**Interfaces:**
- Consumes: `LossLedger.claim`, `LossLedger.remaining` (Task 1).
- Produces: `claim_availability_outage(ledger: LossLedger, *, down_mask: np.ndarray) -> float`

- [ ] **Step 1: Tulis tes yang gagal**

Buat `tests/unit/test_m2f_estimators.py`:

```python
"""Tes estimator M2f: tiap kategori mengklaim dari ledger sesuai counterfactual."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.estimators import claim_availability_outage
from pv_pipeline.m2f.ledger import LossLedger


def _ledger(expected, actual):
    return LossLedger(
        string_id="WB03-INV01-PV5",
        day=pd.Timestamp("2026-05-13"),
        e_expected=np.array(expected, dtype=float),
        e_actual=np.array(actual, dtype=float),
    )


def test_availability_claims_full_expected_during_downtime():
    led = _ledger([2.0, 2.0, 2.0], [0.0, 2.0, 2.0])
    claimed = claim_availability_outage(led, down_mask=np.array([True, False, False]))
    assert claimed == pytest.approx(2.0)


def test_availability_claims_nothing_when_string_never_down():
    led = _ledger([2.0, 2.0], [1.0, 1.0])
    claimed = claim_availability_outage(led, down_mask=np.array([False, False]))
    assert claimed == pytest.approx(0.0)


def test_full_day_outage_claims_everything_leaving_no_residual():
    # WHY: string mati sehari penuh harus 100% masuk availability. Kalau ada
    # sisa, kategori lain akan mengklaim energi dari jendela yang sebenarnya
    # sudah dijelaskan sepenuhnya.
    led = _ledger([2.0, 3.0, 1.0], [0.0, 0.0, 0.0])
    claimed = claim_availability_outage(led, down_mask=np.array([True, True, True]))
    assert claimed == pytest.approx(6.0)
    assert led.residual() == pytest.approx(0.0)
    led.assert_closure()


def test_availability_cannot_claim_more_than_actual_shortfall():
    # Ditandai down tapi string tetap produksi penuh -> tidak ada yang diklaim.
    led = _ledger([2.0], [2.0])
    claimed = claim_availability_outage(led, down_mask=np.array([True]))
    assert claimed == pytest.approx(0.0)


def test_down_mask_length_mismatch_raises():
    led = _ledger([2.0, 2.0], [0.0, 0.0])
    with pytest.raises(ValueError, match="panjang"):
        claim_availability_outage(led, down_mask=np.array([True]))
```

- [ ] **Step 2: Jalankan tes, pastikan gagal**

Run: `python -m pytest tests/unit/test_m2f_estimators.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pv_pipeline.m2f.estimators'`

- [ ] **Step 3: Tulis implementasi minimal**

Buat `pv_pipeline/m2f/estimators.py`:

```python
"""Estimator kWh per kategori. Tiap fungsi mengklaim dari LossLedger.

Dipanggil berurutan prioritas oleh orchestrator. Karena ledger memotong klaim
ke sisa yang tersedia, kategori berprioritas lebih rendah otomatis hanya
melihat energi yang belum dijelaskan.
"""
from __future__ import annotations

import numpy as np

from pv_pipeline.m2f.ledger import LossLedger


def claim_availability_outage(
    ledger: LossLedger,
    *,
    down_mask: np.ndarray,
) -> float:
    """Klaim seluruh sisa rugi pada timestamp saat string tercatat mati.

    Counterfactual: bila string hidup, ia akan menghasilkan ``E_expected``.
    Prioritas pertama karena saat string mati, tidak ada penyebab lain yang
    berlaku pada jendela itu.
    """
    mask = np.asarray(down_mask, dtype=bool)
    remaining = ledger.remaining()
    if mask.shape != remaining.shape:
        raise ValueError(
            f"[m2f] panjang down_mask {mask.shape} != ledger {remaining.shape}"
        )
    return ledger.claim("availability_outage", np.where(mask, remaining, 0.0))
```

- [ ] **Step 4: Jalankan tes, pastikan lulus**

Run: `python -m pytest tests/unit/test_m2f_estimators.py -v`
Expected: PASS, 5 tes.

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/m2f/estimators.py tests/unit/test_m2f_estimators.py
git commit -m "feat(m2f): estimator availability_outage"
```

---

### Task 5: Estimator dc_cable_fault

**Files:**
- Modify: `pv_pipeline/m2f/estimators.py` (tambah fungsi kedua di akhir file)
- Modify: `tests/unit/test_m2f_estimators.py` (tambah tes di akhir file)

**Interfaces:**
- Consumes: `deficit_to_kwh` (Task 3), `LossLedger.claim` (Task 1), `claim_availability_outage` (Task 4).
- Produces: `claim_dc_cable_fault(ledger: LossLedger, *, deficit_kwh: np.ndarray) -> float`

- [ ] **Step 1: Tulis tes yang gagal**

Ubah baris import di `tests/unit/test_m2f_estimators.py` menjadi:

```python
from pv_pipeline.m2f.estimators import claim_availability_outage, claim_dc_cable_fault
```

Tambahkan di akhir file:

```python
def test_dc_cable_fault_claims_the_deficit():
    led = _ledger([3.0, 3.0], [1.0, 3.0])
    claimed = claim_dc_cable_fault(led, deficit_kwh=np.array([2.0, 0.0]))
    assert claimed == pytest.approx(2.0)


def test_dc_cable_fault_claims_only_what_availability_left_behind():
    # WHY: string mati lalu ditandai fault pada timestamp yang sama. Tanpa
    # ledger, keduanya mengklaim energi yang sama dan total loss jadi 2x.
    led = _ledger([2.0, 2.0], [0.0, 0.0])
    claim_availability_outage(led, down_mask=np.array([True, False]))
    claimed = claim_dc_cable_fault(led, deficit_kwh=np.array([2.0, 2.0]))
    assert claimed == pytest.approx(2.0)
    led.assert_closure()


def test_dc_cable_fault_deficit_larger_than_remaining_is_capped():
    led = _ledger([1.0], [0.5])
    claimed = claim_dc_cable_fault(led, deficit_kwh=np.array([10.0]))
    assert claimed == pytest.approx(0.5)


def test_dc_cable_fault_length_mismatch_raises():
    led = _ledger([1.0, 1.0], [0.0, 0.0])
    with pytest.raises(ValueError, match="panjang"):
        claim_dc_cable_fault(led, deficit_kwh=np.array([1.0]))
```

- [ ] **Step 2: Jalankan tes, pastikan gagal**

Run: `python -m pytest tests/unit/test_m2f_estimators.py -v -k dc_cable`
Expected: FAIL — `ImportError: cannot import name 'claim_dc_cable_fault'`

- [ ] **Step 3: Tulis implementasi minimal**

Tambahkan di akhir `pv_pipeline/m2f/estimators.py`:

```python
def claim_dc_cable_fault(
    ledger: LossLedger,
    *,
    deficit_kwh: np.ndarray,
) -> float:
    """Klaim defisit arus terhadap sibling/partner pada jendela ter-flag.

    Counterfactual: string yang sehat akan mengalirkan arus setara median
    sibling se-inverter (atau median partner se-MPPT). Selisihnya, dikali
    tegangan dan durasi, adalah energi yang hilang akibat fault DC.

    ``deficit_kwh`` berasal dari :func:`pv_pipeline.m2f.deficit.deficit_to_kwh`
    atas gabungan artefak ketiga detektor m2b (lihat
    :func:`pv_pipeline.m2f.deficit.reduce_deficit_frames`, Task 3).
    """
    deficit = np.asarray(deficit_kwh, dtype=float)
    remaining = ledger.remaining()
    if deficit.shape != remaining.shape:
        raise ValueError(
            f"[m2f] panjang deficit_kwh {deficit.shape} != ledger {remaining.shape}"
        )
    return ledger.claim("dc_cable_fault", np.maximum(deficit, 0.0))
```

- [ ] **Step 4: Jalankan tes, pastikan lulus**

Run: `python -m pytest tests/unit/test_m2f_estimators.py -v`
Expected: PASS, 9 tes.

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/m2f/estimators.py tests/unit/test_m2f_estimators.py
git commit -m "feat(m2f): estimator dc_cable_fault dari defisit sibling"
```

---

### Task 6: Estimator soiling

**Files:**
- Modify: `pv_pipeline/m2f/estimators.py` (tambah fungsi ketiga di akhir file)
- Modify: `tests/unit/test_m2f_estimators.py` (tambah tes di akhir file)

**Interfaces:**
- Consumes: `LossLedger.claim` (Task 1), `claim_availability_outage` (Task 4), `claim_dc_cable_fault` (Task 5).
- Produces: `claim_soiling(ledger: LossLedger, *, p_loss: float, e_expected_kwh_per_ts: np.ndarray) -> float`

> **Catatan (2026-08-25):** draft awal task ini mengklaim `p_loss * ledger.remaining()` -- salah denominator. `p_loss` dari rdtools SRR (`MonthlySoilingLoss.p_loss_pct / 100`, lihat `pv_pipeline/m2a/soiling.py:549`) adalah fraksi dari energi BASELINE bersih (`E_expected`), BUKAN fraksi dari sisa yang belum terklaim di ledger. Worked example dari review: `E_expected=100, E_actual=95, p_loss=0.03` -> rugi soiling SEHARUSNYA `0.03 * 100 = 3.0` kWh; formula lama menghasilkan `0.03 * remaining(5.0) = 0.15` kWh -- under-atribusi 20x yang jatuh ke `unexplained` dan mengorupsi ROI cleaning. Fix: hitung counterfactual absolut `p_loss * e_expected_kwh_per_ts` per timestamp, lalu serahkan ke `ledger.claim()` yang memotongnya ke sisa yang tersedia -- sama seperti dua estimator lain di modul ini. Signature `claim_soiling` karena itu WAJIB menerima `e_expected_kwh_per_ts` sebagai keyword.

- [ ] **Step 1: Tulis tes yang gagal**

Ubah baris import di `tests/unit/test_m2f_estimators.py` menjadi:

```python
from pv_pipeline.m2f.estimators import (
    claim_availability_outage,
    claim_dc_cable_fault,
    claim_soiling,
)
```

Tambahkan di akhir file:

```python
def test_soiling_claims_p_loss_times_e_expected_not_remaining():
    # WHY: p_loss dari rdtools SRR adalah fraksi dari E_expected (baseline
    # clean-sky), BUKAN fraksi dari sisa yang belum terklaim di ledger.
    # p_loss=0.25 dari E_expected=10.0 -> 2.5 kWh, bukan 0.25 * remaining(4.0)
    # = 1.0 kWh seperti versi lama.
    led = _ledger([10.0], [6.0])
    claimed = claim_soiling(
        led, p_loss=0.25, e_expected_kwh_per_ts=np.array([10.0])
    )
    assert claimed == pytest.approx(2.5)


def test_soiling_worked_example_wrong_denominator_regression():
    # WHY: worked example dari review -- E_expected=100, E_actual=95,
    # p_loss=0.03 -> rugi soiling SEHARUSNYA 3.0 kWh (0.03 * E_expected).
    # Versi lama mengklaim 0.03 * remaining(5.0) = 0.15 kWh, under-atribusi
    # 20x yang jatuh ke unexplained dan mengorupsi ROI cleaning.
    led = _ledger([100.0], [95.0])
    claimed = claim_soiling(
        led, p_loss=0.03, e_expected_kwh_per_ts=np.array([100.0])
    )
    assert claimed == pytest.approx(3.0)


def test_soiling_claims_after_higher_priority_categories():
    # WHY: SRR menyerap apa saja yang turun perlahan. Kalau soiling mengklaim
    # sebelum fault, rugi fault dihitung sebagai rugi soiling dan ROI cleaning
    # jadi overstated -- padahal angka itu dasar keputusan biaya.
    led = _ledger([10.0, 10.0], [0.0, 8.0])
    claim_dc_cable_fault(led, deficit_kwh=np.array([10.0, 0.0]))
    claimed = claim_soiling(
        led, p_loss=0.5, e_expected_kwh_per_ts=np.array([10.0, 10.0])
    )
    # remaining setelah dc_cable_fault = [0.0, 2.0]; p_loss*E_expected =
    # [5.0, 5.0]; ledger memotong ke sisa -> granted = [0.0, 2.0].
    assert claimed == pytest.approx(2.0)
    led.assert_closure()


def test_soiling_with_zero_p_loss_claims_nothing():
    led = _ledger([10.0], [5.0])
    claimed = claim_soiling(led, p_loss=0.0, e_expected_kwh_per_ts=np.array([10.0]))
    assert claimed == pytest.approx(0.0)


def test_soiling_p_loss_out_of_range_raises():
    led = _ledger([10.0], [5.0])
    with pytest.raises(ValueError, match="p_loss"):
        claim_soiling(led, p_loss=1.5, e_expected_kwh_per_ts=np.array([10.0]))
    with pytest.raises(ValueError, match="p_loss"):
        claim_soiling(led, p_loss=-0.1, e_expected_kwh_per_ts=np.array([10.0]))


def test_soiling_length_mismatch_raises():
    led = _ledger([1.0, 1.0], [0.0, 0.0])
    with pytest.raises(ValueError, match="panjang"):
        claim_soiling(led, p_loss=0.5, e_expected_kwh_per_ts=np.array([1.0]))


def test_fully_explained_string_leaves_soiling_at_zero():
    # WHY: proksi untuk tes anti-double-count di spec. Ketika kategori
    # berprioritas lebih tinggi sudah menjelaskan seluruh rugi, soiling tidak
    # boleh mengklaim apa pun -- berapa pun p_loss dari SRR.
    led = _ledger([4.0, 4.0], [0.0, 4.0])
    claim_dc_cable_fault(led, deficit_kwh=np.array([4.0, 0.0]))
    claimed = claim_soiling(
        led, p_loss=0.9, e_expected_kwh_per_ts=np.array([4.0, 4.0])
    )
    assert claimed == pytest.approx(0.0)
    assert led.residual() == pytest.approx(0.0)
```

- [ ] **Step 2: Jalankan tes, pastikan gagal**

Run: `python -m pytest tests/unit/test_m2f_estimators.py -v -k soiling`
Expected: FAIL — `ImportError: cannot import name 'claim_soiling'`

- [ ] **Step 3: Tulis implementasi minimal**

Tambahkan di akhir `pv_pipeline/m2f/estimators.py`:

```python
def claim_soiling(
    ledger: LossLedger,
    *,
    p_loss: float,
    e_expected_kwh_per_ts: np.ndarray,
) -> float:
    """Klaim ``p_loss * e_expected_kwh_per_ts`` sebagai rugi soiling.

    ``p_loss`` adalah fraksi rugi soiling insolation-weighted dari rdtools SRR
    (``M2aSoiling`` artifact ``MonthlySoilingLoss.p_loss_pct / 100``) --
    fraksi dari energi BASELINE (clean, ``E_expected``), BUKAN fraksi dari
    sisa yang belum terklaim di ledger. Counterfactual absolutnya adalah
    ``p_loss * e_expected_kwh_per_ts`` per timestamp; ledger yang memotongnya
    ke sisa yang tersedia, sama seperti dua estimator lain di modul ini.

    Prioritas keempat, setelah availability dan fault: SRR menyerap apa saja
    yang menurun perlahan, jadi ia hanya boleh melihat energi yang belum
    diklaim kategori berprioritas lebih tinggi.
    """
    p = float(p_loss)
    if not (0.0 <= p <= 1.0):
        raise ValueError(f"[m2f] p_loss harus di [0, 1], dapat {p}.")
    e_expected = np.asarray(e_expected_kwh_per_ts, dtype=float)
    remaining = ledger.remaining()
    if e_expected.shape != remaining.shape:
        raise ValueError(
            f"[m2f] panjang e_expected_kwh_per_ts {e_expected.shape} != "
            f"ledger {remaining.shape}"
        )
    return ledger.claim("soiling", p * e_expected)
```

- [ ] **Step 4: Jalankan tes, pastikan lulus**

Run: `python -m pytest tests/unit/test_m2f_estimators.py -v`
Expected: PASS, 17 tes -- termasuk `test_dc_cable_fault_clips_negative_deficit_before_claiming`, tes regresi tambahan untuk Task 5 yang dilampirkan review yang sama.

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/m2f/estimators.py tests/unit/test_m2f_estimators.py
git commit -m "feat(m2f): estimator soiling dari p_loss SRR"
```

---

### Task 7: Tabel Pareto

**Files:**
- Create: `pv_pipeline/m2f/pareto.py`
- Test: `tests/unit/test_m2f_pareto.py`

**Interfaces:**
- Consumes: `LossLedger.totals()` (Task 1).
- Produces:
  - `PARETO_COLUMNS: List[str]` = `["category", "loss_kwh", "pct", "cum_pct", "actionable", "vital_few"]`
  - `VITAL_FEW_THRESHOLD_PCT: float = 80.0`
  - `NON_ACTIONABLE: List[str] = ["unexplained"]`
  - `build_pareto_table(totals: Dict[str, Optional[float]]) -> pd.DataFrame`

- [ ] **Step 1: Tulis tes yang gagal**

Buat `tests/unit/test_m2f_pareto.py`:

```python
"""Tes tabel Pareto: urutan, kumulatif, dan penandaan vital-few."""
import pandas as pd
import pytest

from pv_pipeline.m2f.pareto import PARETO_COLUMNS, build_pareto_table


def _totals(**kwargs):
    base = {
        "availability_outage": 0.0,
        "dc_cable_fault": 0.0,
        "soiling": 0.0,
        "microcrack": None,
        "bifacial_underperf": None,
        "unexplained": 0.0,
    }
    base.update(kwargs)
    return base


def test_schema_and_descending_order():
    table = build_pareto_table(
        _totals(availability_outage=10.0, dc_cable_fault=50.0, soiling=40.0)
    )
    assert list(table.columns) == PARETO_COLUMNS
    assert table["category"].tolist()[:3] == [
        "dc_cable_fault", "soiling", "availability_outage",
    ]


def test_cumulative_reaches_100_percent():
    table = build_pareto_table(
        _totals(availability_outage=25.0, dc_cable_fault=50.0, soiling=25.0)
    )
    assert table["cum_pct"].iloc[-1] == pytest.approx(100.0)


def test_locked_categories_are_excluded_from_table():
    # WHY: None berarti belum terukur. Menampilkannya sebagai batang 0 kWh
    # akan terbaca "sudah dicek, aman" -- justru kebalikan dari maksudnya.
    table = build_pareto_table(_totals(dc_cable_fault=10.0))
    assert "microcrack" not in table["category"].tolist()
    assert "bifacial_underperf" not in table["category"].tolist()


def test_unexplained_is_present_but_not_actionable():
    # WHY: menyembunyikan residual akan menutupi sinyal bahwa atribusinya lemah.
    table = build_pareto_table(_totals(dc_cable_fault=10.0, unexplained=90.0))
    row = table.set_index("category").loc["unexplained"]
    assert row["loss_kwh"] == pytest.approx(90.0)
    assert bool(row["actionable"]) is False
    assert bool(row["vital_few"]) is False


def test_vital_few_covers_categories_up_to_80_percent():
    table = build_pareto_table(
        _totals(dc_cable_fault=60.0, soiling=25.0, availability_outage=15.0)
    ).set_index("category")
    assert bool(table.loc["dc_cable_fault", "vital_few"]) is True
    assert bool(table.loc["soiling", "vital_few"]) is True
    assert bool(table.loc["availability_outage", "vital_few"]) is False


def test_all_zero_losses_give_zero_pct_not_nan():
    table = build_pareto_table(_totals())
    assert table["pct"].notna().all()
    assert table["pct"].sum() == pytest.approx(0.0)


def test_vital_few_survives_when_unexplained_dominates():
    # WHY: konfigurasi v1 -- unexplained menyerap shading, low-irradiance,
    # microcrack, bifacial, dan ground-fault sekaligus, jadi residual > 80%
    # adalah keadaan YANG DIHARAPKAN, bukan yang patologis. Kalau cum_pct
    # dihitung atas SEMUA baris (termasuk unexplained), unexplained yang jadi
    # baris pertama (90%) langsung menembus ambang 80% dan satu-satunya
    # kategori actionable (dc_cable_fault) tidak pernah sempat masuk --
    # vital_few kosong permanen padahal ada rugi actionable yang bisa
    # ditindak. INI MENGGANTI kontrak draft awal, di mana cum_pct dihitung
    # atas semua baris termasuk unexplained (lihat catatan di Step 3).
    table = build_pareto_table(
        _totals(dc_cable_fault=10.0, unexplained=90.0)
    ).set_index("category")
    assert bool(table.loc["dc_cable_fault", "vital_few"]) is True
    assert bool(table.loc["unexplained", "vital_few"]) is False


def test_negative_residual_does_not_break_percentages():
    # String melebihi ekspektasi -> residual negatif. Persentase dihitung
    # terhadap total rugi POSITIF supaya tetap terbaca.
    table = build_pareto_table(_totals(dc_cable_fault=10.0, unexplained=-4.0))
    assert table["pct"].notna().all()
    assert table.set_index("category").loc["dc_cable_fault", "pct"] == pytest.approx(100.0)
```

- [ ] **Step 2: Jalankan tes, pastikan gagal**

Run: `python -m pytest tests/unit/test_m2f_pareto.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pv_pipeline.m2f.pareto'`

- [ ] **Step 3: Tulis implementasi minimal**

Buat `pv_pipeline/m2f/pareto.py`:

```python
"""Tabel Pareto dari total ledger: urut menurun, kumulatif, vital-few."""
from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd


PARETO_COLUMNS: List[str] = [
    "category",
    "loss_kwh",
    "pct",
    "cum_pct",
    "actionable",
    "vital_few",
]

VITAL_FEW_THRESHOLD_PCT: float = 80.0

# `unexplained` bukan target tindakan -- ia metrik kualitas atribusi.
NON_ACTIONABLE: List[str] = ["unexplained"]


def build_pareto_table(totals: Dict[str, Optional[float]]) -> pd.DataFrame:
    """Susun tabel Pareto dari peta kategori -> kWh.

    Kategori bernilai ``None`` (terkunci, belum ada instrumen) dibuang: batang
    0 kWh akan terbaca "sudah dicek, aman", justru kebalikan dari maksudnya.

    Persentase dihitung terhadap total rugi POSITIF, supaya residual negatif
    (string melebihi ekspektasi) tidak membuat pembagi mengecil atau nol.
    """
    rows = [
        {"category": cat, "loss_kwh": float(val)}
        for cat, val in totals.items()
        if val is not None
    ]
    table = pd.DataFrame(rows, columns=["category", "loss_kwh"])
    # kind="stable": quicksort default pandas tidak stabil, jadi kategori
    # yang seri (loss_kwh sama) bisa bertukar urutan antar run -- ambang
    # vital-few dan urutan chart harus deterministik.
    table = table.sort_values(
        "loss_kwh", ascending=False, kind="stable"
    ).reset_index(drop=True)

    denom = float(table.loc[table["loss_kwh"] > 0.0, "loss_kwh"].sum())
    if denom <= 0.0:
        table["pct"] = 0.0
    else:
        table["pct"] = table["loss_kwh"] / denom * 100.0

    table["actionable"] = ~table["category"].isin(NON_ACTIONABLE)
    # cum_pct dan ambang 80% dihitung hanya atas baris actionable: di v1
    # `unexplained` menyerap shading, low-irradiance, microcrack, bifacial
    # dan ground-fault sekaligus, jadi residual > 80% adalah keadaan YANG
    # DIHARAPKAN, bukan yang patologis. Mengumulasikan seluruh baris
    # (termasuk unexplained) membuat ambang 80% habis oleh unexplained
    # sendiri sebelum kategori actionable manapun sempat dipertimbangkan --
    # vital_few jadi kosong permanen. Baris non-actionable tidak menambah
    # apa pun ke kumulatif (kontribusinya 0), jadi cum_pct-nya sama dengan
    # baris actionable terakhir sebelum dia -- tetap monoton untuk chart.
    # CATATAN: draft awal task ini menghitung `table["cum_pct"] =
    # table["pct"].cumsum()` atas SEMUA baris -- itulah bug yang diperbaiki
    # di sini (lihat test_vital_few_survives_when_unexplained_dominates).
    actionable_pct = table["pct"].where(table["actionable"], 0.0)
    table["cum_pct"] = actionable_pct.cumsum()

    # Vital few = kategori actionable yang dapat ditindak sampai kumulatif
    # (actionable-only) menembus 80%. Baris pertama yang menembus ambang ikut
    # masuk (konvensi Pareto).
    crossed = table["cum_pct"] >= VITAL_FEW_THRESHOLD_PCT
    first_crossing = crossed.idxmax() if crossed.any() else len(table) - 1
    within = table.index <= first_crossing
    table["vital_few"] = within & table["actionable"] & (table["loss_kwh"] > 0.0)

    return table[PARETO_COLUMNS]
```

> **Catatan (2026-08-25):** karena `cum_pct` sekarang berarti "kumulatif % dari
> porsi actionable", ia **tidak lagi mencapai 100%** ketika `unexplained`
> bukan nol -- `test_cumulative_reaches_100_percent` di Step 1 tetap lulus
> hanya karena skenarionya kebetulan tidak menyisakan `unexplained` (25+50+25
> = 100%, unexplained=0.0). Jangan jadikan tes itu bukti bahwa `cum_pct`
> selalu berakhir di 100% -- lihat bagian "Strategi pengujian" di spec yang
> sudah dikoreksi untuk poin ini.

- [ ] **Step 4: Jalankan tes, pastikan lulus**

Run: `python -m pytest tests/unit/test_m2f_pareto.py -v`
Expected: PASS, 8 tes.

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/m2f/pareto.py tests/unit/test_m2f_pareto.py
git commit -m "feat(m2f): tabel Pareto dengan kumulatif dan penandaan vital-few"
```

---

### Task 8: Grafik waterfall dan Pareto

**Files:**
- Create: `pv_pipeline/m2f/plots.py`
- Test: `tests/unit/test_m2f_plots.py`

**Interfaces:**
- Consumes: `build_pareto_table` output dan `VITAL_FEW_THRESHOLD_PCT` (Task 7); `LossLedger.totals()` (Task 1).
- Produces:
  - `WATERFALL_COLUMNS: List[str] = ["label", "delta_kwh", "kind"]`
  - `build_waterfall_table(totals: Dict[str, Optional[float]], attribution_order: List[str], *, e_expected_kwh: float) -> pd.DataFrame` — `kind` bernilai `"terminal"`, `"loss"`, atau `"gain"`. `e_expected_kwh` WAJIB (keyword-only): lihat catatan di Step 3 untuk alasannya.
  - `build_loss_waterfall_figure(waterfall_df, *, scope: str, period_label: str, close_after_show: bool = False) -> Figure`
  - `build_pareto_figure(pareto_df, *, scope: str, period_label: str, close_after_show: bool = False) -> Figure`

- [ ] **Step 1: Tulis tes yang gagal**

Buat `tests/unit/test_m2f_plots.py`:

```python
"""Tes grafik M2f: mengembalikan Figure, tidak menulis file, tahan input kosong."""
import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import pytest
from matplotlib.figure import Figure

from pv_pipeline.m2f.pareto import build_pareto_table
from pv_pipeline.m2f.plots import (
    build_loss_waterfall_figure,
    build_pareto_figure,
    build_waterfall_table,
)


ORDER = ["availability_outage", "dc_cable_fault", "soiling", "unexplained"]


def _totals(**kwargs):
    base = {
        "availability_outage": 0.0,
        "dc_cable_fault": 0.0,
        "soiling": 0.0,
        "microcrack": None,
        "bifacial_underperf": None,
        "unexplained": 0.0,
    }
    base.update(kwargs)
    return base


def test_waterfall_table_keeps_priority_order_not_magnitude_order():
    # WHY: urutan prioritas adalah inti metodenya. Kalau grafik mengurutkan
    # menurut besaran, pembaca kehilangan informasi kenapa soiling kecil
    # (karena fault sudah mengklaim lebih dulu).
    table = build_waterfall_table(
        _totals(availability_outage=1.0, dc_cable_fault=9.0, soiling=2.0),
        attribution_order=ORDER,
        e_expected_kwh=100.0,
    )
    labels = table["label"].tolist()
    assert labels[0] == "E_expected"
    assert labels[-1] == "E_actual"
    assert labels[1:-1] == ORDER


def test_waterfall_marks_terminals_and_losses():
    table = build_waterfall_table(
        _totals(soiling=5.0), attribution_order=ORDER, e_expected_kwh=50.0
    )
    kinds = table.set_index("label")["kind"]
    assert kinds["E_expected"] == "terminal"
    assert kinds["E_actual"] == "terminal"
    assert kinds["soiling"] == "loss"


def test_waterfall_marks_negative_residual_as_gain():
    # String melebihi ekspektasi -> batang naik, bukan dipaksa nol.
    table = build_waterfall_table(
        _totals(unexplained=-3.0), attribution_order=ORDER, e_expected_kwh=20.0
    )
    assert table.set_index("label").loc["unexplained", "kind"] == "gain"


def test_waterfall_actual_terminal_reflects_real_energy_not_forced_zero():
    # WHY: draft awal memakai sum(klaim) sebagai tinggi batang E_expected,
    # sehingga E_actual (dihitung sebagai sisa berjalan) selalu jatuh persis
    # ke 0.0 -- identitas aljabar, bukan energi aktual sungguhan. Pembaca
    # grafik akan salah membaca kedua batang terminal sebagai kWh produksi
    # absolut. E_actual harus sama dengan e_expected_kwh dikurangi total
    # delta seluruh kategori, dan TIDAK boleh nol di sini.
    totals = _totals(
        availability_outage=1.0, dc_cable_fault=9.0, soiling=2.0, unexplained=3.0
    )
    table = build_waterfall_table(
        totals, attribution_order=ORDER, e_expected_kwh=100.0
    )
    by_label = table.set_index("label")
    category_delta_sum = table.loc[table["label"].isin(ORDER), "delta_kwh"].sum()
    assert by_label.loc["E_expected", "delta_kwh"] == pytest.approx(100.0)
    assert by_label.loc["E_actual", "delta_kwh"] == pytest.approx(
        100.0 - category_delta_sum
    )
    assert by_label.loc["E_actual", "delta_kwh"] != pytest.approx(0.0)


def test_waterfall_figure_returns_figure_without_writing_files(tmp_path, monkeypatch):
    # WHY: harus benar-benar gagal bila kode memanggil savefig -- pengecekan
    # lama hanya melihat isi tmp_path tanpa pernah chdir/pass ke dalamnya,
    # jadi tidak memberi proteksi regresi sama sekali untuk kontrak
    # "tidak menulis file".
    def _raise_if_saved(self, *args, **kwargs):
        raise AssertionError("build_loss_waterfall_figure tidak boleh memanggil savefig")

    monkeypatch.setattr(Figure, "savefig", _raise_if_saved)

    table = build_waterfall_table(
        _totals(dc_cable_fault=5.0, soiling=3.0),
        attribution_order=ORDER,
        e_expected_kwh=40.0,
    )
    fig = build_loss_waterfall_figure(table, scope="site", period_label="2026-05")
    assert isinstance(fig, Figure)
    assert not list(tmp_path.iterdir())


def test_pareto_figure_returns_figure():
    table = build_pareto_table(_totals(dc_cable_fault=60.0, soiling=40.0))
    fig = build_pareto_figure(table, scope="wb", period_label="WB03 2026-05")
    assert isinstance(fig, Figure)


def test_pareto_figure_reports_residual_share_in_title():
    # WHY: porsi unexplained adalah metrik kualitas model dan harus terlihat
    # tanpa membuka workbook.
    table = build_pareto_table(_totals(dc_cable_fault=30.0, unexplained=70.0))
    fig = build_pareto_figure(table, scope="site", period_label="2026-05")
    assert "70" in fig.axes[0].get_title()


def test_empty_input_returns_figure_instead_of_raising():
    # WHY: grafik dipanggil dari notebook batch; satu WB tanpa data tidak
    # boleh menghentikan seluruh run.
    empty = pd.DataFrame(columns=["label", "delta_kwh", "kind"])
    fig = build_loss_waterfall_figure(empty, scope="site", period_label="2026-05")
    assert isinstance(fig, Figure)

    empty_pareto = pd.DataFrame(
        columns=["category", "loss_kwh", "pct", "cum_pct", "actionable", "vital_few"]
    )
    fig2 = build_pareto_figure(empty_pareto, scope="site", period_label="2026-05")
    assert isinstance(fig2, Figure)


def test_close_after_show_closes_figure_but_keeps_it_usable():
    # WHY: fungsi ini dipanggil dari loop batch notebook atas banyak
    # kombinasi WB/period; tanpa jalur penutupan, figure menumpuk di
    # registry global pyplot (kebocoran memori). close_after_show meniru
    # pola pv_pipeline/viz.py untuk mencegah itu -- tapi Figure yang
    # dikembalikan harus tetap dapat dipakai pemanggil (mis. savefig).
    table = build_waterfall_table(
        _totals(soiling=5.0), attribution_order=ORDER, e_expected_kwh=50.0
    )
    fig = build_loss_waterfall_figure(
        table, scope="site", period_label="2026-05", close_after_show=True
    )
    assert isinstance(fig, Figure)
    assert fig.number not in plt.get_fignums()

    pareto_table = build_pareto_table(_totals(dc_cable_fault=60.0, soiling=40.0))
    fig2 = build_pareto_figure(
        pareto_table, scope="wb", period_label="WB03 2026-05", close_after_show=True
    )
    assert isinstance(fig2, Figure)
    assert fig2.number not in plt.get_fignums()
```

- [ ] **Step 2: Jalankan tes, pastikan gagal**

Run: `python -m pytest tests/unit/test_m2f_plots.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pv_pipeline.m2f.plots'`

- [ ] **Step 3: Tulis implementasi minimal**

Buat `pv_pipeline/m2f/plots.py`:

> **Catatan (2026-08-25):** dua review mengubah kontrak fungsi di file ini
> sejak draft awal. (1) `build_waterfall_table` draft awal memakai
> `sum(klaim)` sebagai tinggi batang `E_expected` -- identitas aljabar yang
> membuat `E_actual` (dihitung sebagai sisa berjalan) SELALU jatuh persis ke
> 0.0, bukan energi aktual sungguhan; fix-nya menambah parameter
> keyword-only WAJIB `e_expected_kwh` yang berisi energi ekspektasi riil.
> (2) Ketiga fungsi (`build_waterfall_table` tidak termasuk -- hanya dua figure
> builder dan `_empty_figure`) mendapat parameter `close_after_show: bool =
> False` supaya loop batch notebook atas banyak WB/period tidak menumpuk
> figure di registry global pyplot; `Figure` yang dikembalikan tetap dapat
> dipakai penuh (mis. `fig.savefig(...)`) walau sudah di-`plt.close()`.
> Blok di bawah SUDAH final -- jangan reproduksi versi draft.

```python
"""Grafik M2f: waterfall rugi dan diagram Pareto.

Fungsi mengembalikan ``matplotlib.figure.Figure`` dan TIDAK menulis file --
``savefig`` adalah tanggung jawab pemanggil (notebook). Parameter
``close_after_show`` meniru pola ``pv_pipeline/viz.py`` (hemat memori untuk
batch): beda dari ``viz.py``, fungsi di sini tidak pernah memanggil
``plt.show()`` sendiri -- konvensinya murni mengembalikan Figure, showing
tetap tanggung jawab pemanggil juga.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.figure import Figure

from pv_pipeline.m2f.pareto import VITAL_FEW_THRESHOLD_PCT


WATERFALL_COLUMNS: List[str] = ["label", "delta_kwh", "kind"]

# Palet aman untuk buta warna (Okabe-Ito).
COLOR_TERMINAL = "#0072B2"
COLOR_LOSS = "#D55E00"
COLOR_GAIN = "#009E73"
COLOR_RESIDUAL = "#999999"
COLOR_VITAL = "#D55E00"
COLOR_TRIVIAL = "#56B4E9"
COLOR_CUM = "#0072B2"

_EMPTY_MESSAGE = "tidak ada data"


def build_waterfall_table(
    totals: Dict[str, Optional[float]],
    attribution_order: List[str],
    *,
    e_expected_kwh: float,
) -> pd.DataFrame:
    """Susun tabel waterfall: terminal, kategori berurutan prioritas, terminal.

    Urutan mengikuti ``attribution_order``, BUKAN besaran. Urutan prioritas
    adalah inti metodenya dan harus terbaca dari grafik.

    ``delta_kwh`` pada baris ``E_expected`` berisi ``e_expected_kwh`` (energi
    ekspektasi riil, BUKAN jumlah klaim -- versi lama memakai sum(klaim)
    sebagai tinggi batang, yang membuat E_actual selalu jatuh ke 0.0 karena
    dikonstruksi sebagai identitas aljabar, bukan energi aktual sungguhan).
    ``delta_kwh`` pada baris ``E_actual`` berisi ``e_expected_kwh`` dikurangi
    total delta seluruh kategori -- energi aktual riil.
    """
    rows = [{
        "label": "E_expected",
        "delta_kwh": float(e_expected_kwh),
        "kind": "terminal",
    }]
    total_delta = 0.0
    for cat in attribution_order:
        val = totals.get(cat)
        if val is None:
            continue
        val = float(val)
        total_delta += val
        rows.append({
            "label": cat,
            "delta_kwh": val,
            "kind": "gain" if val < 0.0 else "loss",
        })
    rows.append({
        "label": "E_actual",
        "delta_kwh": float(e_expected_kwh) - total_delta,
        "kind": "terminal",
    })
    return pd.DataFrame(rows, columns=WATERFALL_COLUMNS)


def _empty_figure(title: str, *, close_after_show: bool = False) -> Figure:
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.text(0.5, 0.5, _EMPTY_MESSAGE, ha="center", va="center", fontsize=14)
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    if close_after_show:
        plt.close(fig)
    return fig


def build_loss_waterfall_figure(
    waterfall_df: pd.DataFrame,
    *,
    scope: str,
    period_label: str,
    close_after_show: bool = False,
) -> Figure:
    """Waterfall dari E_expected ke E_actual, berurutan prioritas atribusi.

    close_after_show : bool
        Bila True, panggil ``plt.close(fig)`` sebelum kembali -- mencegah
        figure menumpuk di registry global pyplot saat dipanggil dalam loop
        batch notebook (banyak WB/period). Figure yang dikembalikan tetap
        dapat dipakai penuh (mis. ``fig.savefig(...)``) walau sudah
        di-close -- ``plt.close`` hanya melepas referensinya dari pyplot,
        bukan menghapus objeknya.
    """
    title = f"Waterfall rugi energi DC - {scope} - {period_label}"
    if waterfall_df is None or waterfall_df.empty:
        return _empty_figure(title, close_after_show=close_after_show)

    labels = waterfall_df["label"].tolist()
    deltas = waterfall_df["delta_kwh"].to_numpy(dtype=float)
    kinds = waterfall_df["kind"].tolist()

    e_expected = float(deltas[0])
    fig, ax = plt.subplots(figsize=(11, 6))

    running = e_expected
    for i, (label, delta, kind) in enumerate(zip(labels, deltas, kinds)):
        if kind == "terminal":
            height = e_expected if label == "E_expected" else running
            ax.bar(i, height, color=COLOR_TERMINAL)
            ax.text(i, height, f"{height:,.0f}", ha="center", va="bottom", fontsize=8)
            continue
        bottom = running - max(delta, 0.0)
        color = COLOR_RESIDUAL if label == "unexplained" else (
            COLOR_GAIN if kind == "gain" else COLOR_LOSS
        )
        hatch = "//" if label == "unexplained" else None
        ax.bar(i, abs(delta), bottom=bottom, color=color, hatch=hatch)
        pct = (delta / e_expected * 100.0) if e_expected else 0.0
        ax.text(
            i, bottom + abs(delta),
            f"{delta:,.0f}\n({pct:.1f}%)",
            ha="center", va="bottom", fontsize=8,
        )
        # Garis konektor supaya rantai pengurangan terbaca.
        ax.plot([i - 0.4, i + 0.4], [running, running], color="0.4", lw=0.8)
        running -= delta

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel("Energi (kWh)")
    ax.set_title(title)
    fig.text(
        0.01, 0.01,
        "microcrack dan bifacial_underperf belum ada instrumen; "
        "kontribusinya terlipat ke dalam unexplained.",
        fontsize=7, color="0.35",
    )
    fig.tight_layout()
    if close_after_show:
        plt.close(fig)
    return fig


def build_pareto_figure(
    pareto_df: pd.DataFrame,
    *,
    scope: str,
    period_label: str,
    close_after_show: bool = False,
) -> Figure:
    """Batang kWh menurun + garis kumulatif % + garis ambang 80%.

    close_after_show : bool
        Bila True, panggil ``plt.close(fig)`` sebelum kembali -- sama seperti
        pada :func:`build_loss_waterfall_figure`, mencegah figure menumpuk
        di memori saat dipanggil dalam loop batch.
    """
    base_title = f"Pareto rugi energi DC - {scope} - {period_label}"
    if pareto_df is None or pareto_df.empty:
        return _empty_figure(base_title, close_after_show=close_after_show)

    residual = pareto_df.loc[pareto_df["category"] == "unexplained", "pct"]
    residual_pct = float(residual.iloc[0]) if len(residual) else 0.0
    title = f"{base_title} | unexplained {residual_pct:.0f}%"

    categories = pareto_df["category"].tolist()
    values = pareto_df["loss_kwh"].to_numpy(dtype=float)
    cum = pareto_df["cum_pct"].to_numpy(dtype=float)

    fig, ax = plt.subplots(figsize=(11, 6))
    colors = [
        COLOR_RESIDUAL if cat == "unexplained"
        else (COLOR_VITAL if vital else COLOR_TRIVIAL)
        for cat, vital in zip(categories, pareto_df["vital_few"].tolist())
    ]
    bars = ax.bar(range(len(categories)), values, color=colors)
    for bar, cat in zip(bars, categories):
        if cat == "unexplained":
            bar.set_hatch("//")

    ax.set_xticks(range(len(categories)))
    ax.set_xticklabels(categories, rotation=30, ha="right")
    ax.set_ylabel("Rugi energi (kWh)")

    ax2 = ax.twinx()
    ax2.plot(range(len(categories)), cum, color=COLOR_CUM, marker="o", lw=1.5)
    ax2.axhline(VITAL_FEW_THRESHOLD_PCT, color="0.4", ls="--", lw=1.0)
    ax2.set_ylabel("Kumulatif (%)")
    ax2.set_ylim(0, 105)

    n_vital = int(pareto_df["vital_few"].sum())
    ax.set_title(title)
    ax.annotate(
        f"{n_vital} kategori vital-few (dapat ditindak)",
        xy=(0.02, 0.94), xycoords="axes fraction", fontsize=9,
    )
    fig.tight_layout()
    if close_after_show:
        plt.close(fig)
    return fig
```

- [ ] **Step 4: Jalankan tes, pastikan lulus**

Run: `python -m pytest tests/unit/test_m2f_plots.py -v`
Expected: PASS, 9 tes.

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/m2f/plots.py tests/unit/test_m2f_plots.py
git commit -m "feat(m2f): grafik waterfall dan diagram Pareto"
```

---

### Task 9: Orchestrator M2fLossAttribution, config, dan workbook

> **STATUS (2026-08-25): BELUM DIKERJAKAN.** Task 1-8 sudah shipped dan
> ter-commit (`pv_pipeline/m2f/ledger.py`, `baseline.py`, `deficit.py`,
> `estimators.py`, `pareto.py`, `plots.py` semuanya ada dan lulus tes).
> `report.py` sengaja ditunda: `M2fLossAttribution._load_providers` akan
> memanggil `CellTempProvider.from_geometry_yaml(...)`, dan `CellTempProvider`
> **raise `FileNotFoundError`** tanpa berkas
> `raw data input/PV Module Temperature PLTS IKN.xlsx` -- yang tidak ada di
> working tree saat catatan ini ditulis. Tanpa berkas itu, tes Step 1 di
> bawah akan GAGAL (bukan lulus) pada setiap kasus yang seharusnya menembus
> `_load_providers`, ATAU -- lebih berbahaya -- lulus VAKUM lewat jalur
> `provider_unavailable` (seluruh string tercatat `skipped_reason`, closure
> "berlaku" karena tidak ada baris yang dicek sama sekali). Penulis Task 9
> WAJIB memverifikasi berkas itu tersedia (atau mem-mock `CellTempProvider`
> secara eksplisit di tes) sebelum mempercayai hasil PASS/FAIL suite ini.
>
> Bagian di bawah ini masih berupa RENCANA (belum kode yang shipped) --
> beda dari Task 1-8 di atas, yang blok kodenya sudah diperbarui mengikuti
> hasil review dan boleh disalin langsung. Rencana Task 9 di bawah SUDAH
> dikoreksi mengikuti review yang sama terhadap draft-draft sebelumnya, dan
> memuat lima kendala tambahan (lihat kotak "Kendala WAJIB" di bawah) yang
> tidak ada di draft plan yang lebih tua -- ini poin paling penting di
> seluruh dokumen ini karena kode ini akan benar-benar ditulis dari sini.

Menyatukan semuanya menjadi `SubModule` yang bisa dijalankan `M2Engine`.

**Files:**
- Create: `pv_pipeline/m2f/report.py`
- Modify: `pv_pipeline/m2f/__init__.py` (ekspor `M2fLossAttribution`)
- Modify: `config/m2_config.yaml` (tambah section `m2f` di akhir file)
- Modify: `pv_pipeline/core.py:137` (tambah entri ke `DEFAULT_SUBMODULE_TO_CFG_KEY`)
- Test: `tests/unit/test_m2f_report.py`

**Interfaces:**
- Consumes: `LossLedger` (Task 1); `compute_expected_energy_kwh`, `compute_actual_energy_kwh`, `DEFAULT_FREQ_HOURS` (Task 2); `deficit_to_kwh`, `reduce_deficit_frames` (Task 3 -- **BUKAN** `TIMESERIES_DEFICIT_SHEET`, konstanta itu tidak pernah ada di `deficit.py` yang shipped); `claim_availability_outage` (Task 4); `claim_dc_cable_fault` (Task 5); `claim_soiling(..., e_expected_kwh_per_ts=...)` (Task 6, signature berubah); `build_pareto_table` (Task 7); `build_waterfall_table(..., e_expected_kwh=...)` (Task 8, signature berubah); `pv_pipeline.core.SubModule`, `M2Finding`, `Severity`; `pv_pipeline.poa.provider.POAProvider`; `pv_pipeline.cell_temp.CellTempProvider`; `pv_pipeline.panel_spec.PanelSpec`; `pv_pipeline.transformations.add_inverter_id`, `add_pv_power_columns`.
- Produces:
  - `M2fLossAttribution(SubModule)` dengan `name = "M2f_loss_attribution"`
  - `M2fLossAttribution.run(combined_df: pd.DataFrame, config: dict) -> List[M2Finding]`
  - `M2fLossAttribution._load_providers(config) -> Tuple[Optional[dict], Optional[str]]`
  - `M2fLossAttribution._iter_string_days(df) -> Iterator[Tuple[str, str, pd.Timestamp, pd.DataFrame, str]]`
  - `PER_STRING_COLUMNS`, `CLOSURE_COLUMNS`, `BIFACIAL_COLUMNS`
  - `artifacts`: `M2f_Waterfall`, `M2f_Pareto`, `M2f_PerString`, `M2f_Closure`, `M2f_BifacialCalib`

> **Kendala WAJIB untuk Task 9 (dari review terakhir terhadap draft ini):**
>
> 1. **Series, bukan `np.asarray`, ke estimator.** `claim_availability_outage`/
>    `claim_dc_cable_fault`/`claim_soiling` (Task 4-6) semuanya meng-coerce
>    argumennya ke `np.ndarray` polos di baris pertama badan fungsi sebelum
>    memanggil `ledger.claim(...)` -- jadi proteksi alignment `pd.Series` yang
>    ditambahkan ke `LossLedger.claim()` di Task 1 **TIDAK aktif** kalau
>    dipanggil lewat ketiga wrapper ini apa adanya. Task 9 tetap WAJIB
>    membawa hasil `reduce_deficit_frames`/`compute_expected_energy_kwh` sebagai
>    `pd.Series` ber-`DatetimeIndex` selama mungkin di kode orchestrator-nya
>    sendiri (reindex lewat `.reindex(idx, fill_value=0.0)` yang index-aware,
>    BUKAN slicing posisional) dan mengonstruksi `LossLedger(..., index=idx)`
>    dengan index riil -- supaya kesalahan alignment gagal keras di titik
>    reindex itu sendiri, walau proteksi di dalam `ledger.claim()` sendiri saat
>    ini masih dorman di balik ketiga wrapper tsb.
> 2. **Detektor tanpa artefak defisit -> JANGAN panggil estimatornya sama
>    sekali.** Bila `config["m2f"].get("deficit_frames")` kosong/`None` untuk
>    seluruh run (mis. ketiga detektor m2b dimatikan atau tidak dijalankan),
>    `claim_dc_cable_fault` TIDAK dipanggil untuk string manapun -- kategori
>    `dc_cable_fault` tetap `None` di `ledger.totals()` ("tidak pernah diukur"),
>    bukan `0.0` ("diukur, tidak ada rugi"). Memanggilnya dengan `0.0` akan
>    mendaftarkan kategori itu dan melaporkan "sudah dicek, aman" untuk
>    sesuatu yang sebenarnya tidak pernah diukur. (Bila `deficit_frames`
>    tersedia tapi `reduce_deficit_frames` mengembalikan nol murni untuk satu
>    string tertentu, itu beda kasus -- detektornya JALAN dan legitimately
>    tidak menemukan apa-apa, jadi klaim `0.0` di situ sudah benar.)
> 3. **Sama untuk soiling: bulan tanpa data SRR harus tetap `None`, bukan
>    `p_loss=0.0`.** Bila `cfg["p_loss_by_month"]` tidak punya entri untuk
>    `day.strftime("%Y-%m")`, JANGAN panggil `claim_soiling` sama sekali untuk
>    string-hari itu -- jangan `.get(key, 0.0)` lalu tetap memanggil.
> 4. **Gate POA/Tcell pakai ambang cakupan, bukan `isna().all()`.** Draft lama
>    men-skip hanya bila SELURUH timestamp NaN. Cakupan sebagian (mis. 40%
>    NaN) lolos gate itu, lalu `compute_expected_energy_kwh` men-`fillna(0.0)`
>    tiap timestamp NaN -- `E_expected` diam-diam menyusut untuk jam-jam yang
>    tak tercakup dan `L_total` ikut menyusut tanpa tercatat di mana pun. Ganti
>    dengan ambang cakupan (mis. `coverage = poa.notna().mean()`; skip bila
>    `coverage < cfg.get("poa_coverage_min_pct", 80.0) / 100.0`, sama untuk
>    tcell) dan catat cakupan aktualnya di `M2f_Closure` sebagai bagian dari
>    `skipped_reason` atau kolom baru, supaya cakupan parsial yang LOLOS gate
>    tetap terlihat di audit, bukan cuma yang di-skip total.
> 5. **Akumulasi `E_expected` site-level.** `build_waterfall_table` (Task 8)
>    sekarang WAJIB menerima `e_expected_kwh` (bukan lagi `sum(klaim)`).
>    Task 9 harus menjumlahkan `e_exp.sum()` tiap string-hari yang BENAR-BENAR
>    diproses (bukan yang di-skip) ke akumulator site-level, lalu
>    meneruskannya sebagai `e_expected_kwh=site_e_expected_kwh` saat memanggil
>    `build_waterfall_table(site_totals, cfg["attribution_order"],
>    e_expected_kwh=site_e_expected_kwh)`.
> 6. **`cum_pct` di `M2f_Pareto` tidak lagi berarti "kumulatif dari 100%
>    total".** Sejak Task 7, `cum_pct` adalah kumulatif dari porsi
>    *actionable* saja (lihat catatan di Task 7) -- ia TIDAK mencapai 100%
>    ketika `unexplained` bukan nol, dan di v1 `unexplained` menyerap shading,
>    low-irradiance, microcrack, bifacial, DAN ground-fault sekaligus, jadi
>    residual besar adalah keadaan yang diharapkan. Jangan menulis assertion
>    atau dokumentasi yang mengasumsikan `cum_pct` berakhir di 100%.

- [ ] **Step 1: Tulis tes yang gagal**

Buat `tests/unit/test_m2f_report.py`:

```python
"""Tes orchestrator M2f: closure end-to-end, artefak, dan gating config."""
import pandas as pd
import pytest

from pv_pipeline.m2f.ledger import CLOSURE_TOLERANCE_KWH
from pv_pipeline.m2f.report import M2fLossAttribution


def _config(enabled=True, **overrides):
    # deficit_frames dan p_loss_by_month SENGAJA tidak diisi di sini: itu
    # menegakkan kendala #2/#3 di atas ("tanpa artefak -> estimator TIDAK
    # dipanggil") secara default, bukan lewat kebetulan fixture. Tes yang
    # butuh dc_cable_fault/soiling terisi harus mengisinya eksplisit.
    cfg = {
        "poa": {"site_geometry_path": "config/site_geometry.yaml"},
        "panel": {"spec_path": "config/panel_spec.yaml"},
        "m2f": {
            "enabled": enabled,
            "attribution_order": [
                "availability_outage", "dc_cable_fault", "soiling", "unexplained",
            ],
            "bifacial_gain_per_wb": {"WB03": 1.05},
            "clearsky_kt_min": 0.9,
            "poa_coverage_min_pct": 80.0,
            "residual_warn_pct": 30.0,
            "deficit_frames": None,
            "p_loss_by_month": {},
        },
    }
    cfg["m2f"].update(overrides)
    return cfg


def _combined_df():
    idx = pd.date_range("2026-05-13 08:00", periods=4, freq="5min")
    return pd.DataFrame([
        {
            "Start Time": ts,
            "Inverter_ID": "WB03-INV01",
            "PV5 Power(kW)": 4.0,
            "PV5 input voltage(V)": 1200.0,
            "PV5 input current(A)": 3.33,
            "Inverter status": "On-grid",
        }
        for ts in idx
    ])


def test_disabled_by_default_emits_nothing():
    sm = M2fLossAttribution()
    assert sm.run(_combined_df(), _config(enabled=False)) == []
    assert sm.artifacts == {}


def test_emits_all_five_artifacts_when_enabled():
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    for sheet in (
        "M2f_Waterfall", "M2f_Pareto", "M2f_PerString",
        "M2f_Closure", "M2f_BifacialCalib",
    ):
        assert sheet in sm.artifacts, f"artifact {sheet} hilang"


def test_closure_holds_for_every_string_day_row():
    # WHY: invarian yang membuat seluruh angka waterfall layak dipercaya.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    closure = sm.artifacts["M2f_Closure"]
    scored = closure[closure["skipped_reason"].isna()]
    drift = (
        scored["claimed_kwh"] + scored["residual_kwh"] - scored["l_total_kwh"]
    ).abs()
    assert (drift <= CLOSURE_TOLERANCE_KWH).all()


def test_locked_categories_absent_from_pareto():
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    cats = sm.artifacts["M2f_Pareto"]["category"].tolist()
    assert "microcrack" not in cats
    assert "bifacial_underperf" not in cats


def test_high_residual_emits_weak_attribution_finding():
    # WHY: residual besar berarti atribusinya lemah. Itu harus muncul sebagai
    # sinyal, bukan diam-diam lolos sebagai angka yang tampak rapi.
    sm = M2fLossAttribution()
    findings = sm.run(_combined_df(), _config(residual_warn_pct=0.0))
    assert any(f.fault_type == "weak_attribution" for f in findings)


def test_closure_sheet_has_skipped_reason_column():
    # WHY: hari tanpa POA bukan "hari tanpa rugi". Menganggapnya nol akan
    # menurunkan angka rugi secara palsu, jadi alasannya harus tercatat.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    assert "skipped_reason" in sm.artifacts["M2f_Closure"].columns
```

- [ ] **Step 2: Jalankan tes, pastikan gagal**

Run: `python -m pytest tests/unit/test_m2f_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pv_pipeline.m2f.report'`

- [ ] **Step 3: Tulis orchestrator**

Buat `pv_pipeline/m2f/report.py` dengan kerangka berikut, lalu isi bagian yang ditandai sesuai langkah bernomor di bawahnya:

```python
"""Orchestrator M2f: rakit ledger per (string, hari), klaim berurutan
prioritas, lalu emit waterfall, Pareto, dan audit closure.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from pv_pipeline.cell_temp import CellTempProvider
from pv_pipeline.core import M2Finding, Severity, SubModule
from pv_pipeline.m2f.baseline import (
    DEFAULT_FREQ_HOURS,
    compute_actual_energy_kwh,
    compute_expected_energy_kwh,
)
from pv_pipeline.m2f.deficit import deficit_to_kwh, reduce_deficit_frames
from pv_pipeline.m2f.estimators import (
    claim_availability_outage,
    claim_dc_cable_fault,
    claim_soiling,
)
from pv_pipeline.m2f.ledger import LossLedger
from pv_pipeline.m2f.pareto import build_pareto_table
from pv_pipeline.m2f.plots import build_waterfall_table
from pv_pipeline.panel_spec import PanelSpec
from pv_pipeline.poa.provider import POAProvider
from pv_pipeline.transformations import add_inverter_id, add_pv_power_columns


PER_STRING_COLUMNS: List[str] = ["string_id", "day", "category", "loss_kwh"]
CLOSURE_COLUMNS: List[str] = [
    "string_id", "day", "l_total_kwh", "claimed_kwh",
    "residual_kwh", "residual_pct", "poa_coverage_pct", "tcell_coverage_pct",
    "skipped_reason",
]
BIFACIAL_COLUMNS: List[str] = ["wb_id", "g_bifacial", "n_strings", "n_days"]


class M2fLossAttribution(SubModule):
    """Atribusi rugi energi DC ke kategori penyebab, per (string, hari)."""

    name = "M2f_loss_attribution"

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        cfg = config.get("m2f") or {}
        if not cfg.get("enabled", False):
            return []

        order: List[str] = list(cfg.get("attribution_order") or [])
        gains: Dict[str, float] = dict(cfg.get("bifacial_gain_per_wb") or {})
        # .get(key) TANPA default 0.0 -- bulan/detektor yang absen dari dict
        # ini harus tetap None setelah lookup (kendala #3), bukan diam-diam
        # jadi 0.0 sebelum sempat dicek.
        p_loss_by_month: Dict[str, float] = dict(cfg.get("p_loss_by_month") or {})
        deficit_frames: Optional[List[pd.DataFrame]] = cfg.get("deficit_frames")
        poa_source: str = str(cfg.get("poa_source", "pyranometer"))
        coverage_min = float(cfg.get("poa_coverage_min_pct", 80.0)) / 100.0
        warn_pct = float(cfg.get("residual_warn_pct", 30.0))

        df = combined_df
        if "Inverter_ID" not in df.columns:
            df = add_inverter_id(df)
        if not any(str(c).endswith(" Power(kW)") for c in df.columns):
            df, _ = add_pv_power_columns(df)

        providers, provider_error = self._load_providers(config)

        per_string_rows: List[dict] = []
        closure_rows: List[dict] = []
        site_totals: Dict[str, Optional[float]] = {}
        site_e_expected_kwh: float = 0.0  # kendala #5

        for string_id, wb_id, day, group, pv_label in self._iter_string_days(df):
            # --- langkah 4: coverage gate + baseline + ledger (Series, index=idx) --
            # --- langkah 5: klaim berurutan `order`, skip estimator bila None --
            # --- langkah 6: assert_closure + akumulasi (termasuk E_expected) --
            pass

        # --- langkah 7: rakit kelima artifact (waterfall pakai site_e_expected_kwh) --
        # --- langkah 8: emit finding weak_attribution ------------------------
        return []
```

Kedua helper yang dipanggil kerangka di atas, sebagai method `M2fLossAttribution`:

```python
    @staticmethod
    def _load_providers(config: dict):
        """Muat POA/Tcell/PanelSpec sekali. Kembalikan (providers, error).

        Kegagalan tidak melempar: seluruh string dicatat sebagai skipped
        supaya kelima artifact tetap ter-emit dengan skema yang benar.
        """
        try:
            geometry = config["poa"]["site_geometry_path"]
            return (
                {
                    "poa": POAProvider.from_yaml(geometry),
                    "tcell": CellTempProvider.from_geometry_yaml(geometry),
                    "spec": PanelSpec.from_yaml(config["panel"]["spec_path"]),
                },
                None,
            )
        except Exception as err:  # noqa: BLE001 - dicatat, bukan ditelan
            return None, f"provider_unavailable: {err}"

    @staticmethod
    def _iter_string_days(df: pd.DataFrame):
        """Yield (string_id, wb_id, day, group, pv_label) per string per hari.

        ``group`` ber-index DatetimeIndex terurut, siap dipakai provider POA.
        """
        frame = df.copy()
        frame["_ts"] = pd.to_datetime(frame["Start Time"], errors="coerce")
        frame = frame.dropna(subset=["_ts", "Inverter_ID"])
        power_cols = [c for c in frame.columns if str(c).endswith(" Power(kW)")]
        for inverter_id, inv_rows in frame.groupby("Inverter_ID", sort=True):
            wb_id = str(inverter_id)[:4].upper()
            for day, day_rows in inv_rows.groupby(inv_rows["_ts"].dt.normalize()):
                ordered = day_rows.sort_values("_ts").set_index("_ts")
                for col in power_cols:
                    pv_label = str(col).replace(" Power(kW)", "")
                    if ordered[col].notna().sum() == 0:
                        continue
                    yield (
                        f"{inverter_id}-{pv_label}",
                        wb_id,
                        pd.Timestamp(day),
                        ordered,
                        pv_label,
                    )
```

Isi bagian bertanda dengan urutan berikut (angka mengacu ke komentar
`# --- langkah N` di kerangka di atas):

1. `cfg = config.get("m2f") or {}`. Bila `not cfg.get("enabled", False)`: `return []` tanpa menyentuh `self.artifacts`.
2. Bila kolom `Inverter_ID` belum ada, panggil `add_inverter_id(combined_df)`. Bila kolom `PV{n} Power(kW)` belum ada, panggil `add_pv_power_columns(...)`.
3. Muat provider sekali di awal:
   `poa_provider = POAProvider.from_yaml(config["poa"]["site_geometry_path"])`,
   `tcell_provider = CellTempProvider.from_geometry_yaml(config["poa"]["site_geometry_path"])`,
   `spec = PanelSpec.from_yaml(config["panel"]["spec_path"])`.
   Bungkus dengan `try/except Exception` — bila gagal, catat seluruh string sebagai `skipped_reason="provider_unavailable"` dan tetap emit kelima artifact (kosong tapi berskema). **Ini jalur yang akan dipicu tanpa `raw data input/PV Module Temperature PLTS IKN.xlsx` -- lihat catatan blocker di atas; jangan biarkan tes lulus lewat jalur ini tanpa disadari.**
4. Untuk tiap (inverter, PV string, hari):
   - `idx = group.index` sebagai `pd.DatetimeIndex` (sudah di-set oleh `_iter_string_days`); `wb_id` dari 4 karakter pertama `Inverter_ID`.
   - `poa = poa_provider.get_poa(idx, wb_id)`, `tcell = tcell_provider.get_tcell(idx, wb_id)` -- keduanya `pd.Series` ber-index `idx`.
   - **Kendala #4 (coverage gate):** `poa_coverage = float(poa.notna().mean())`, `tcell_coverage = float(tcell.notna().mean())`. Bila salah satu `< coverage_min` (default 80%, dari `cfg["poa_coverage_min_pct"]`): tambahkan baris ke `closure_rows` dengan `skipped_reason="poa_or_tcell_missing"`, `poa_coverage_pct`/`tcell_coverage_pct` terisi (bukan kosong -- ini yang membuat cakupan PARSIAL yang lolos gate tetap terlihat di audit), `l_total_kwh/claimed_kwh/residual_kwh = float("nan")`; `continue`. **JANGAN** `poa.isna().all()` -- itu meloloskan cakupan sebagian, yang lalu di-`fillna(0.0)` diam-diam oleh `compute_expected_energy_kwh` dan menyusutkan `L_total` tanpa jejak.
   - `g = float(gains.get(wb_id, 1.0))`.
   - `e_exp = compute_expected_energy_kwh(poa, tcell, spec, wb_id, bifacial_gain=g)` -- `pd.Series` ber-index `idx`.
   - `e_act = compute_actual_energy_kwh(group[f"{pv_label} Power(kW)"])` -- `pd.Series` ber-index `idx`.
   - `site_e_expected_kwh += float(e_exp.sum())` (kendala #5 -- HANYA untuk string-hari yang benar-benar diproses, bukan yang di-`continue` di atas).
   - `ledger = LossLedger(string_id, day, e_exp.to_numpy(), e_act.to_numpy(), index=idx)` -- **`index=idx` WAJIB** (kendala #1); tanpanya `LossLedger.claim()` tidak punya acuan untuk memvalidasi `pd.Series` yang di-passing di langkah 5.
5. Klaim berurutan `cfg["attribution_order"]`, lewati entri `"unexplained"`:
   - `availability_outage`: `down_mask` = baris yang kolom `Inverter status`-nya tidak mengandung kata kunci on-grid (gunakan `config["m2e"]["inverter_status_map"]["on_grid_keywords"]`, bandingkan lowercase substring). Selalu dipanggil -- kolom ini selalu ada di `combined_df`.
   - `dc_cable_fault`: **Kendala #2** -- bila `deficit_frames` (dari `cfg.get("deficit_frames")`, list gabungan `self.deficit_frames` ketiga detektor m2b yang diteruskan pemanggil) kosong/`None`, **JANGAN panggil `claim_dc_cable_fault` sama sekali** untuk string manapun; kategori tetap `None`. Bila ada, panggil `reduce_deficit_frames(deficit_frames, poa_source=poa_source, index=idx, freq_hours=DEFAULT_FREQ_HOURS)` -- Series ini SUDAH di-reindex ke `idx` dan sudah mengambil MAKSIMUM lintas ketiga detektor (double-count-safe), tidak perlu filter `inverter_id`/`pv_string` manual lagi karena itu bagian dari `deficit_to_kwh` yang dipanggil di dalamnya per frame. Lalu `claim_dc_cable_fault(ledger, deficit_kwh=reduced)`.
   - `soiling`: **Kendala #3** -- `month_key = day.strftime("%Y-%m")`; bila `month_key not in p_loss_by_month`, **JANGAN panggil `claim_soiling`**; kategori tetap `None`. Bila ada, `claim_soiling(ledger, p_loss=p_loss_by_month[month_key], e_expected_kwh_per_ts=e_exp.to_numpy())`.
6. `ledger.assert_closure()`. Kumpulkan `ledger.totals()` ke akumulator site-level (merge per kategori: `None` hanya bertahan bila SEMUA string-hari yang diproses melaporkan `None` untuk kategori itu; kalau ada satu saja yang mengklaim, jumlahkan yang bukan `None` dan perlakukan `None` lain sebagai 0 kontribusi -- bukan gugurkan seluruh site jadi `None`) dan ke `per_string_rows`.
7. Rakit artifact:
   - `self.artifacts["M2f_PerString"]` — kolom `string_id, day, category, loss_kwh`.
   - `self.artifacts["M2f_Waterfall"]` — `build_waterfall_table(site_totals, cfg["attribution_order"], e_expected_kwh=site_e_expected_kwh)` (kendala #5 -- parameter `e_expected_kwh` WAJIB sejak Task 8).
   - `self.artifacts["M2f_Pareto"]` — `build_pareto_table(site_totals)` (`cum_pct`-nya sekarang atas porsi actionable saja -- kendala #6).
   - `self.artifacts["M2f_Closure"]` — kolom `string_id, day, l_total_kwh, claimed_kwh, residual_kwh, residual_pct, poa_coverage_pct, tcell_coverage_pct, skipped_reason`.
   - `self.artifacts["M2f_BifacialCalib"]` — kolom `wb_id, g_bifacial, n_strings, n_days`.
   Kelima artifact SELALU di-assign saat `enabled=True`, memakai `pd.DataFrame(columns=[...])` bila tidak ada baris.
8. Bila `residual_pct > cfg["residual_warn_pct"]`, emit satu `M2Finding` dengan `fault_type="weak_attribution"`, `severity=Severity.INFO`, `sub_module="M2f_loss_attribution"`, `value=residual_pct`, `threshold=cfg["residual_warn_pct"]`. Konstruktor persis mengikuti pola di `pv_pipeline/availability.py:180-195`. **Ingat kendala #6**: residual besar adalah hasil YANG DIHARAPKAN di v1 (shading/low-irradiance/microcrack/bifacial/ground-fault semuanya jatuh ke sini) -- finding ini murni sinyal kualitas, bukan indikasi bug.

- [ ] **Step 4: Tambahkan config dan registrasi**

Tambahkan di akhir `config/m2_config.yaml`:

```yaml
# M2f Loss Attribution & Pareto (spec 2026-08-11).
# Default OFF, opt-in mengikuti pola detektor lain.
m2f:
  enabled: false
  # Urutan prioritas atribusi. Eksplisit di config supaya dapat diaudit dan
  # diuji, bukan tersembunyi di kode. Shading WAJIB mendahului soiling (v2):
  # SRR menyerap apa saja yang turun perlahan, jadi kalau dibalik, rugi
  # shading diklaim sebagai rugi soiling dan ROI cleaning jadi overstated.
  # ground_fault SENGAJA tidak ada di daftar ini -- lihat spec bagian
  # "Ledger klaim dan urutan prioritas"; rugi ground_fault jatuh ke
  # unexplained di v1.
  attribution_order:
    - availability_outage
    - dc_cable_fault
    - soiling
    - unexplained
  # Hasil kalibrasi dari string sehat pada hari clear-sky. Kosong = 1.0.
  # WAJIB diisi setelah run pertama dengan data POA nyata; tanpa ini
  # E_expected under-estimate karena hanya memakai POA depan.
  bifacial_gain_per_wb: {}
  clearsky_kt_min: 0.9
  # Ambang cakupan POA/Tcell (%) untuk memproses satu (string, hari). Di
  # bawah ini, string-hari itu di-skip dengan skipped_reason=
  # "poa_or_tcell_missing" alih-alih diam-diam diisi 0 di timestamp yang
  # bolong -- lihat "Kendala WAJIB" #4 di atas.
  poa_coverage_min_pct: 80.0
  # POA source yang dipakai untuk menyaring TimeseriesDeficit gabungan dari
  # 3 detektor m2b sebelum diklaim ke dc_cable_fault (lihat
  # reduce_deficit_frames, Task 3). Harus konsisten dengan source yang
  # dipakai poa_provider.get_poa di atas.
  poa_source: "pyranometer"
  # Residual di atas ambang ini memicu finding INFO "weak_attribution".
  # Residual besar TIDAK berarti bug di v1 -- lihat "Kendala WAJIB" #6.
  residual_warn_pct: 30.0
  # List pd.DataFrame -- gabungan self.deficit_frames dari instance
  # peer_zscore/open_circuit/mppt_ratio yang sudah dijalankan pemanggil
  # (notebook). None/kosong berarti dc_cable_fault TIDAK PERNAH diklaim
  # (tetap None, bukan 0.0) -- lihat "Kendala WAJIB" #2.
  deficit_frames: null
  # Fraksi rugi soiling per bulan (YYYY-MM -> 0..1), dari
  # M2aSoiling artifact MonthlySoilingLoss.p_loss_pct / 100. Bulan yang
  # absen dari dict ini berarti soiling TIDAK PERNAH diklaim untuk bulan itu
  # (tetap None, bukan p_loss=0.0) -- lihat "Kendala WAJIB" #3.
  p_loss_by_month: {}
```

Tambahkan ke `DEFAULT_SUBMODULE_TO_CFG_KEY` di `pv_pipeline/core.py`, setelah entri `"M2b_mppt_ratio"` (baris 137):

```python
    "M2f_loss_attribution": "m2f",
```

Tambahkan ke `pv_pipeline/m2f/__init__.py`:

```python
from pv_pipeline.m2f.report import M2fLossAttribution
```

dan masukkan `"M2fLossAttribution"` ke `__all__`.

- [ ] **Step 5: Jalankan tes M2f dan seluruh suite**

Run: `python -m pytest tests/unit/test_m2f_report.py -v`
Expected: PASS, 6 tes -- TAPI verifikasi dulu bahwa tes-tes ini benar-benar
menembus jalur provider (bukan lolos vakum lewat `provider_unavailable`
karena `raw data input/PV Module Temperature PLTS IKN.xlsx` hilang; lihat
catatan blocker di kepala Task 9). Angka "6 tes" ini aspirasional dari draft
di atas, bukan hasil run yang sudah diverifikasi -- `report.py` belum ada.

Run: `python -m pytest tests/ -q`
Expected: PASS — seluruh suite existing tetap hijau. Baseline HEAD saat
catatan ini ditulis (`b5bd9dc`, Task 1-8 shipped, Task 9 belum) adalah
**956 tes** di `tests/unit/`; jumlah setelah Task 9 akan lebih besar dari
itu (bukan "58 tes M2f dari baseline sebelum Task 1" seperti draft lama --
angka itu sudah tidak akurat karena Task 1-8 sendiri menambah lebih dari 58
tes lewat review berturut-turut; lihat jumlah tes per-task yang sudah
dikoreksi di atas: ledger 20, baseline 10, deficit 14 + 2, estimators 17,
pareto 8, plots 9 = 80 tes M2f dari Task 1-8 saja, di luar Task 9).

- [ ] **Step 6: Commit**

```bash
git add pv_pipeline/m2f/report.py pv_pipeline/m2f/__init__.py config/m2_config.yaml pv_pipeline/core.py tests/unit/test_m2f_report.py
git commit -m "feat(m2f): orchestrator M2fLossAttribution, config, dan workbook"
```

---

## Catatan verifikasi setelah rencana selesai

Rencana ini menghasilkan modul yang lulus tes sintetis, tetapi **belum** terverifikasi terhadap data nyata. Sebelum angkanya dipakai untuk keputusan biaya, jalankan sekali dengan `raw data input/` terisi dan periksa:

1. **Hipotesis gain bifacial.** Jalankan `calibrate_bifacial_gain` atas string sehat di hari clear-sky, lalu isi `m2f.bifacial_gain_per_wb`. Bila hasilnya jauh dari 1.0 (mis. di atas 1,10 atau di bawah 0,95), baseline perlu ditinjau ulang sebelum waterfall dipakai — lihat spec bagian "Asumsi dan risiko terbuka".
2. **Besar residual.** `unexplained` yang mendominasi Pareto berarti v1 belum cukup; itu argumen untuk menjalankan v2 (`shading`, `low_irradiance_eff`), bukan alasan menyembunyikan bucketnya.
3. **Tilt WB01-WB02.** `config/site_geometry.yaml:28` hanya mengonfirmasi WB03-WB10 pada 10 derajat. Bila WB01-02 berbeda, `E_expected` untuk 49 inverter bias, dan gain bifacial hasil kalibrasi akan menyerap bias itu secara keliru.
