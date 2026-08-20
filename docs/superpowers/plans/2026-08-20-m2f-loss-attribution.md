# M2f Loss Attribution & Pareto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Membangun M2f v1 — atribusi rugi energi DC ke `availability_outage`, `dc_cable_fault`, `soiling`, dan `unexplained` per (string, hari), plus tabel Pareto, grafik waterfall, dan diagram Pareto.

**Architecture:** `LossLedger` menyimpan energi belum-terklaim per timestamp untuk satu (string, hari). Estimator dipanggil berurutan prioritas dan mengklaim dari ledger; energi yang sudah diklaim tidak bisa diklaim ulang, sehingga double-count mustahil secara struktural, bukan secara konvensi. `E_expected` dihitung dari POA terukur + Tcell terukur lewat `physics.compute_p_expected_per_string`, dikali koefisien gain bifacial per WB. Residual jatuh otomatis ke `unexplained`.

**Tech Stack:** Python 3, pandas, numpy, matplotlib, seaborn, openpyxl, pytest. Tidak ada dependensi baru — semuanya sudah di `requirements.txt`.

**Spec:** `docs/superpowers/specs/2026-08-11-m2f-loss-attribution-design.md`

## Global Constraints

- **Lingkup rencana ini = v1 saja.** Kategori `shading` dan `low_irradiance_eff` (v2), serta `microcrack` dan `bifacial_underperf` (v3) TIDAK diimplementasikan di sini. Keduanya jatuh ke `unexplained` — itu perilaku yang benar, bukan bug.
- **Identitas closure wajib:** `sum(klaim) + residual == L_total` per (string, hari), toleransi `1e-6` kWh absolut. Ini invarian, bukan target.
- **Urutan prioritas tetap:** `availability_outage` → `dc_cable_fault` → `soiling` → `unexplained`. Urutan dibaca dari config `m2f.attribution_order`, tidak di-hardcode di logika klaim.
- `microcrack` dan `bifacial_underperf` bernilai `None`, BUKAN `0.0`.
- **Modul library tidak menulis file gambar.** `plots.py` mengembalikan `matplotlib.figure.Figure`; `savefig` adalah tanggung jawab pemanggil (notebook). Pola `pv_pipeline/viz.py`.
- **Kriteria penerimaan #6 di spec menyebut enam detektor; v1 hanya menyentuh empat.** `m2a/shading.py` dan `m2a/low_irradiance.py` baru diperlukan di v2 dan TIDAK diubah di sini.
- **Perubahan pada 4 detektor m2b harus aditif.** Tidak boleh mengubah findings, severity, atau artifact `StringStatus` yang sudah ada. Suite tes existing (`tests/unit/test_peer_zscore.py`, `test_open_circuit.py`, `test_ground_fault.py`, `test_mppt_ratio.py`) harus tetap hijau tanpa diubah.
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
  - `LossLedger(string_id: str, day: pd.Timestamp, e_expected: np.ndarray, e_actual: np.ndarray)`
  - `LossLedger.l_total() -> float`
  - `LossLedger.remaining() -> np.ndarray`
  - `LossLedger.claim(category: str, amount_kwh_per_ts: np.ndarray) -> float`
  - `LossLedger.residual() -> float`
  - `LossLedger.totals() -> Dict[str, Optional[float]]`
  - `LossLedger.assert_closure() -> None`

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
        Tanggal (dinormalisasi ke tengah malam oleh caller).
    e_expected, e_actual : np.ndarray
        Energi per timestamp (kWh), panjang sama.
    """

    def __init__(
        self,
        string_id: str,
        day: pd.Timestamp,
        e_expected: np.ndarray,
        e_actual: np.ndarray,
    ):
        expected = np.asarray(e_expected, dtype=float)
        actual = np.asarray(e_actual, dtype=float)
        if expected.shape != actual.shape:
            raise ValueError(
                f"[m2f] e_expected {expected.shape} != e_actual {actual.shape}"
            )
        self.string_id = string_id
        self.day = day
        self.e_expected = expected
        self.e_actual = actual
        # Hanya rugi positif yang dapat diklaim. Timestamp dengan
        # over-performance tidak menyediakan energi untuk diklaim siapa pun.
        self._remaining = np.maximum(expected - actual, 0.0)
        self._claims: Dict[str, float] = {}

    def l_total(self) -> float:
        """Total rugi (kWh). Boleh negatif bila string melebihi ekspektasi."""
        return float(np.nansum(self.e_expected) - np.nansum(self.e_actual))

    def remaining(self) -> np.ndarray:
        """Energi belum terklaim per timestamp (kWh), selalu >= 0."""
        return self._remaining.copy()

    def claim(self, category: str, amount_kwh_per_ts: np.ndarray) -> float:
        """Klaim energi untuk ``category``, dipotong ke sisa per timestamp.

        Returns
        -------
        float
            kWh yang benar-benar terklaim (bisa lebih kecil dari yang diminta).
        """
        if category in LOCKED_CATEGORIES:
            raise ValueError(
                f"[m2f] {category!r} terkunci (tidak ada instrumen), tidak boleh klaim."
            )
        amount = np.asarray(amount_kwh_per_ts, dtype=float)
        amount = np.nan_to_num(amount, nan=0.0, posinf=0.0, neginf=0.0)
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
        """Raise bila identitas closure dilanggar."""
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
Expected: PASS, 10 tes.

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

### Task 3: Artefak deret waktu di 4 detektor m2b

Keempat detektor sudah menghitung arus aktual dan arus counterfactual per timestamp di dalam `run()`, tetapi hanya menyimpan skor akhirnya. M2f butuh deret waktunya. Perubahan bersifat aditif: satu artifact baru per detektor, dengan skema identik.

**Files:**
- Create: `pv_pipeline/m2f/deficit.py`
- Modify: `pv_pipeline/peer_zscore.py:523` (tambah blok setelah baris ini)
- Modify: `pv_pipeline/open_circuit.py:432` (tambah blok setelah baris ini)
- Modify: `pv_pipeline/ground_fault.py:574` (tambah blok setelah baris ini)
- Modify: `pv_pipeline/mppt_ratio.py:358` (tambah blok setelah baris ini)
- Test: `tests/unit/test_m2f_deficit.py`

**Interfaces:**
- Consumes: `DEFAULT_FREQ_HOURS` (Task 2).
- Produces:
  - `TIMESERIES_DEFICIT_SHEET: str = "TimeseriesDeficit"`
  - `DEFICIT_COLUMNS: List[str]` = `["timestamp", "inverter_id", "pv_string", "actual_kw", "counterfactual_kw", "flagged"]`
  - `build_deficit_frame(timestamps, inverter_id, pv_string, actual_kw, counterfactual_kw, flagged) -> pd.DataFrame`
  - `deficit_to_kwh(frame, *, freq_hours) -> pd.Series` — indexed by timestamp, kWh >= 0.

- [ ] **Step 1: Tulis tes yang gagal**

Buat `tests/unit/test_m2f_deficit.py`:

```python
"""Tes skema artefak deret waktu defisit dan konversinya ke kWh."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.baseline import DEFAULT_FREQ_HOURS
from pv_pipeline.m2f.deficit import (
    DEFICIT_COLUMNS,
    build_deficit_frame,
    deficit_to_kwh,
)


def _frame(actual, counterfactual, flagged):
    idx = pd.date_range("2026-05-13 12:00", periods=len(actual), freq="5min")
    return build_deficit_frame(
        timestamps=idx,
        inverter_id="WB03-INV01",
        pv_string="PV5",
        actual_kw=np.array(actual, dtype=float),
        counterfactual_kw=np.array(counterfactual, dtype=float),
        flagged=np.array(flagged, dtype=bool),
    )


def test_frame_has_exact_schema():
    frame = _frame([1.0], [2.0], [True])
    assert list(frame.columns) == DEFICIT_COLUMNS


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


def test_nan_deficit_is_zero():
    frame = _frame([np.nan], [3.0], [True])
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
```

- [ ] **Step 2: Jalankan tes, pastikan gagal**

Run: `python -m pytest tests/unit/test_m2f_deficit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pv_pipeline.m2f.deficit'`

- [ ] **Step 3: Tulis `deficit.py`**

Buat `pv_pipeline/m2f/deficit.py`:

```python
"""Skema artefak deret waktu defisit, dipakai bersama oleh 4 detektor m2b.

Detektor m2b sudah menghitung arus aktual dan arus counterfactual (median
sibling / median partner MPPT) per timestamp, tetapi hanya menyimpan skor
akhirnya. M2f butuh deret waktunya untuk mengklaim energi ke ledger.
"""
from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd


TIMESERIES_DEFICIT_SHEET: str = "TimeseriesDeficit"

DEFICIT_COLUMNS: List[str] = [
    "timestamp",
    "inverter_id",
    "pv_string",
    "actual_kw",
    "counterfactual_kw",
    "flagged",
]


def build_deficit_frame(
    timestamps,
    inverter_id: str,
    pv_string: str,
    actual_kw: np.ndarray,
    counterfactual_kw: np.ndarray,
    flagged: np.ndarray,
) -> pd.DataFrame:
    """Rakit satu frame defisit dengan skema tetap ``DEFICIT_COLUMNS``."""
    idx = pd.DatetimeIndex(timestamps)
    frame = pd.DataFrame(
        {
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
    """
    missing = [c for c in DEFICIT_COLUMNS if c not in frame.columns]
    if missing:
        raise KeyError(f"[m2f] frame defisit kehilangan kolom {missing}.")
    actual = pd.to_numeric(frame["actual_kw"], errors="coerce")
    counterfactual = pd.to_numeric(frame["counterfactual_kw"], errors="coerce")
    gap = (counterfactual - actual).fillna(0.0).clip(lower=0.0)
    gap = gap.where(frame["flagged"].astype(bool), 0.0)
    out = pd.Series(
        (gap * float(freq_hours)).to_numpy(dtype=float),
        index=pd.DatetimeIndex(frame["timestamp"]),
        name="deficit_kwh",
    )
    return out
```

- [ ] **Step 4: Jalankan tes, pastikan lulus**

Run: `python -m pytest tests/unit/test_m2f_deficit.py -v`
Expected: PASS, 6 tes.

- [ ] **Step 5: Sambungkan ke 4 detektor**

Tambahkan import di bagian atas keempat file, setelah import `pv_pipeline.core` yang sudah ada:

```python
from pv_pipeline.m2f.deficit import TIMESERIES_DEFICIT_SHEET, build_deficit_frame
```

Di setiap `run()`, inisialisasi list penampung bersebelahan dengan inisialisasi `artifact_rows` yang sudah ada:

```python
        deficit_rows: list = []
```

Di dalam loop per-string, tepat sebelum `artifact_rows.append({...})` yang sudah ada, tambahkan (ganti nama variabel arus/tegangan/mask dengan yang sudah ada di scope masing-masing detektor — lihat pemetaan di bawah):

```python
        deficit_rows.append(build_deficit_frame(
            timestamps=group.index,
            inverter_id=str(inverter_id),
            pv_string=f"PV{pv_n}",
            actual_kw=i_string * v_string / 1000.0,
            counterfactual_kw=i_counterfactual * v_string / 1000.0,
            flagged=flag_mask,
        ))
```

Lalu emit, tepat setelah baris `self.artifacts["StringStatus"] = pd.DataFrame(...)` yang sudah ada:

```python
        if deficit_rows:
            self.artifacts[TIMESERIES_DEFICIT_SHEET] = pd.concat(
                deficit_rows, ignore_index=True
            )
```

Pemetaan `i_counterfactual` dan `flag_mask` per detektor:

| File | Baris emit | `i_counterfactual` | `flag_mask` |
|---|---|---|---|
| `peer_zscore.py` | 523 | median arus sibling se-inverter per timestamp | mask `abs(z) > z_threshold` |
| `open_circuit.py` | 432 | `I_q95` sibling per timestamp | mask event ter-debounce |
| `ground_fault.py` | 574 | median arus fleet per timestamp | mask trigger absolute atau adaptive |
| `mppt_ratio.py` | 358 | median arus partner se-MPPT per timestamp | mask `qualifying` |

- [ ] **Step 6: Verifikasi detektor lama tidak berubah**

Run: `python -m pytest tests/unit/test_peer_zscore.py tests/unit/test_open_circuit.py tests/unit/test_ground_fault.py tests/unit/test_mppt_ratio.py -v`
Expected: PASS — semua tes existing tetap hijau tanpa satupun diubah. Kalau ada yang merah, perubahannya tidak aditif dan implementasinya harus diperbaiki — bukan tesnya yang disesuaikan.

- [ ] **Step 7: Commit**

```bash
git add pv_pipeline/m2f/deficit.py tests/unit/test_m2f_deficit.py pv_pipeline/peer_zscore.py pv_pipeline/open_circuit.py pv_pipeline/ground_fault.py pv_pipeline/mppt_ratio.py
git commit -m "feat(m2f): artefak deret waktu defisit di 4 detektor m2b"
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
    atas gabungan artefak keempat detektor m2b.
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
- Produces: `claim_soiling(ledger: LossLedger, *, p_loss: float) -> float`

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
def test_soiling_claims_fraction_of_remaining():
    led = _ledger([10.0], [6.0])
    claimed = claim_soiling(led, p_loss=0.25)
    assert claimed == pytest.approx(1.0)


def test_soiling_claims_after_higher_priority_categories():
    # WHY: SRR menyerap apa saja yang turun perlahan. Kalau soiling mengklaim
    # sebelum fault, rugi fault dihitung sebagai rugi soiling dan ROI cleaning
    # jadi overstated -- padahal angka itu dasar keputusan biaya.
    led = _ledger([10.0, 10.0], [0.0, 8.0])
    claim_dc_cable_fault(led, deficit_kwh=np.array([10.0, 0.0]))
    claimed = claim_soiling(led, p_loss=0.5)
    assert claimed == pytest.approx(1.0)
    led.assert_closure()


def test_soiling_with_zero_p_loss_claims_nothing():
    led = _ledger([10.0], [5.0])
    assert claim_soiling(led, p_loss=0.0) == pytest.approx(0.0)


def test_soiling_p_loss_out_of_range_raises():
    led = _ledger([10.0], [5.0])
    with pytest.raises(ValueError, match="p_loss"):
        claim_soiling(led, p_loss=1.5)
    with pytest.raises(ValueError, match="p_loss"):
        claim_soiling(led, p_loss=-0.1)


def test_fully_explained_string_leaves_soiling_at_zero():
    # WHY: proksi untuk tes anti-double-count di spec. Ketika kategori
    # berprioritas lebih tinggi sudah menjelaskan seluruh rugi, soiling tidak
    # boleh mengklaim apa pun -- berapa pun p_loss dari SRR.
    led = _ledger([4.0, 4.0], [0.0, 4.0])
    claim_dc_cable_fault(led, deficit_kwh=np.array([4.0, 0.0]))
    assert claim_soiling(led, p_loss=0.9) == pytest.approx(0.0)
    assert led.residual() == pytest.approx(0.0)
```

- [ ] **Step 2: Jalankan tes, pastikan gagal**

Run: `python -m pytest tests/unit/test_m2f_estimators.py -v -k soiling`
Expected: FAIL — `ImportError: cannot import name 'claim_soiling'`

- [ ] **Step 3: Tulis implementasi minimal**

Tambahkan di akhir `pv_pipeline/m2f/estimators.py`:

```python
def claim_soiling(ledger: LossLedger, *, p_loss: float) -> float:
    """Klaim fraksi ``p_loss`` dari sisa rugi yang belum dijelaskan.

    ``p_loss`` adalah fraksi rugi soiling insolation-weighted dari rdtools SRR
    (``M2aSoiling`` artifact ``MonthlySoilingLoss.p_loss_pct / 100``).

    Prioritas keempat, setelah availability dan fault: SRR menyerap apa saja
    yang menurun perlahan, jadi ia hanya boleh melihat energi yang belum
    diklaim kategori berprioritas lebih tinggi.
    """
    p = float(p_loss)
    if not (0.0 <= p <= 1.0):
        raise ValueError(f"[m2f] p_loss harus di [0, 1], dapat {p}.")
    return ledger.claim("soiling", ledger.remaining() * p)
```

- [ ] **Step 4: Jalankan tes, pastikan lulus**

Run: `python -m pytest tests/unit/test_m2f_estimators.py -v`
Expected: PASS, 14 tes.

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
    table = table.sort_values("loss_kwh", ascending=False).reset_index(drop=True)

    denom = float(table.loc[table["loss_kwh"] > 0.0, "loss_kwh"].sum())
    if denom <= 0.0:
        table["pct"] = 0.0
    else:
        table["pct"] = table["loss_kwh"] / denom * 100.0
    table["cum_pct"] = table["pct"].cumsum()

    table["actionable"] = ~table["category"].isin(NON_ACTIONABLE)
    # Vital few = kategori yang dapat ditindak sampai kumulatif menembus 80%.
    # Baris pertama yang menembus ambang ikut masuk (konvensi Pareto).
    crossed = table["cum_pct"] >= VITAL_FEW_THRESHOLD_PCT
    first_crossing = crossed.idxmax() if crossed.any() else len(table) - 1
    within = table.index <= first_crossing
    table["vital_few"] = within & table["actionable"] & (table["loss_kwh"] > 0.0)

    return table[PARETO_COLUMNS]
```

- [ ] **Step 4: Jalankan tes, pastikan lulus**

Run: `python -m pytest tests/unit/test_m2f_pareto.py -v`
Expected: PASS, 7 tes.

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
  - `build_waterfall_table(totals: Dict[str, Optional[float]], attribution_order: List[str]) -> pd.DataFrame` — `kind` bernilai `"terminal"`, `"loss"`, atau `"gain"`
  - `build_loss_waterfall_figure(waterfall_df, *, scope: str, period_label: str) -> Figure`
  - `build_pareto_figure(pareto_df, *, scope: str, period_label: str) -> Figure`

- [ ] **Step 1: Tulis tes yang gagal**

Buat `tests/unit/test_m2f_plots.py`:

```python
"""Tes grafik M2f: mengembalikan Figure, tidak menulis file, tahan input kosong."""
import matplotlib
matplotlib.use("Agg")

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
    )
    labels = table["label"].tolist()
    assert labels[0] == "E_expected"
    assert labels[-1] == "E_actual"
    assert labels[1:-1] == ORDER


def test_waterfall_marks_terminals_and_losses():
    table = build_waterfall_table(_totals(soiling=5.0), attribution_order=ORDER)
    kinds = table.set_index("label")["kind"]
    assert kinds["E_expected"] == "terminal"
    assert kinds["E_actual"] == "terminal"
    assert kinds["soiling"] == "loss"


def test_waterfall_marks_negative_residual_as_gain():
    # String melebihi ekspektasi -> batang naik, bukan dipaksa nol.
    table = build_waterfall_table(_totals(unexplained=-3.0), attribution_order=ORDER)
    assert table.set_index("label").loc["unexplained", "kind"] == "gain"


def test_waterfall_figure_returns_figure_without_writing_files(tmp_path):
    table = build_waterfall_table(
        _totals(dc_cable_fault=5.0, soiling=3.0), attribution_order=ORDER
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
```

- [ ] **Step 2: Jalankan tes, pastikan gagal**

Run: `python -m pytest tests/unit/test_m2f_plots.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pv_pipeline.m2f.plots'`

- [ ] **Step 3: Tulis implementasi minimal**

Buat `pv_pipeline/m2f/plots.py`:

```python
"""Grafik M2f: waterfall rugi dan diagram Pareto.

Fungsi mengembalikan ``matplotlib.figure.Figure`` dan TIDAK menulis file --
``savefig`` adalah tanggung jawab pemanggil (notebook). Pola sama dengan
``pv_pipeline/viz.py``.
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
) -> pd.DataFrame:
    """Susun tabel waterfall: terminal, kategori berurutan prioritas, terminal.

    Urutan mengikuti ``attribution_order``, BUKAN besaran. Urutan prioritas
    adalah inti metodenya dan harus terbaca dari grafik.

    ``delta_kwh`` pada baris ``E_expected`` berisi tinggi batang awal (total
    seluruh klaim + residual); pada baris ``E_actual`` berisi 0.0 karena
    tingginya dihitung sebagai sisa berjalan saat menggambar.
    """
    claimed = [
        float(totals[cat])
        for cat in attribution_order
        if totals.get(cat) is not None
    ]
    rows = [{
        "label": "E_expected",
        "delta_kwh": float(sum(claimed)),
        "kind": "terminal",
    }]
    for cat in attribution_order:
        val = totals.get(cat)
        if val is None:
            continue
        val = float(val)
        rows.append({
            "label": cat,
            "delta_kwh": val,
            "kind": "gain" if val < 0.0 else "loss",
        })
    rows.append({"label": "E_actual", "delta_kwh": 0.0, "kind": "terminal"})
    return pd.DataFrame(rows, columns=WATERFALL_COLUMNS)


def _empty_figure(title: str) -> Figure:
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.text(0.5, 0.5, _EMPTY_MESSAGE, ha="center", va="center", fontsize=14)
    ax.set_title(title)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    return fig


def build_loss_waterfall_figure(
    waterfall_df: pd.DataFrame,
    *,
    scope: str,
    period_label: str,
) -> Figure:
    """Waterfall dari E_expected ke E_actual, berurutan prioritas atribusi."""
    title = f"Waterfall rugi energi DC - {scope} - {period_label}"
    if waterfall_df is None or waterfall_df.empty:
        return _empty_figure(title)

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
    return fig


def build_pareto_figure(
    pareto_df: pd.DataFrame,
    *,
    scope: str,
    period_label: str,
) -> Figure:
    """Batang kWh menurun + garis kumulatif % + garis ambang 80%."""
    base_title = f"Pareto rugi energi DC - {scope} - {period_label}"
    if pareto_df is None or pareto_df.empty:
        return _empty_figure(base_title)

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
    return fig
```

- [ ] **Step 4: Jalankan tes, pastikan lulus**

Run: `python -m pytest tests/unit/test_m2f_plots.py -v`
Expected: PASS, 7 tes.

- [ ] **Step 5: Commit**

```bash
git add pv_pipeline/m2f/plots.py tests/unit/test_m2f_plots.py
git commit -m "feat(m2f): grafik waterfall dan diagram Pareto"
```

---

### Task 9: Orchestrator M2fLossAttribution, config, dan workbook

Menyatukan semuanya menjadi `SubModule` yang bisa dijalankan `M2Engine`.

**Files:**
- Create: `pv_pipeline/m2f/report.py`
- Modify: `pv_pipeline/m2f/__init__.py` (ekspor `M2fLossAttribution`)
- Modify: `config/m2_config.yaml` (tambah section `m2f` di akhir file)
- Modify: `pv_pipeline/core.py:137` (tambah entri ke `DEFAULT_SUBMODULE_TO_CFG_KEY`)
- Test: `tests/unit/test_m2f_report.py`

**Interfaces:**
- Consumes: `LossLedger` (Task 1); `compute_expected_energy_kwh`, `compute_actual_energy_kwh`, `DEFAULT_FREQ_HOURS` (Task 2); `deficit_to_kwh`, `TIMESERIES_DEFICIT_SHEET` (Task 3); `claim_availability_outage` (Task 4); `claim_dc_cable_fault` (Task 5); `claim_soiling` (Task 6); `build_pareto_table` (Task 7); `build_waterfall_table` (Task 8); `pv_pipeline.core.SubModule`, `M2Finding`, `Severity`; `pv_pipeline.poa.provider.POAProvider`; `pv_pipeline.cell_temp.CellTempProvider`; `pv_pipeline.panel_spec.PanelSpec`; `pv_pipeline.transformations.add_inverter_id`, `add_pv_power_columns`.
- Produces:
  - `M2fLossAttribution(SubModule)` dengan `name = "M2f_loss_attribution"`
  - `M2fLossAttribution.run(combined_df: pd.DataFrame, config: dict) -> List[M2Finding]`
  - `M2fLossAttribution._load_providers(config) -> Tuple[Optional[dict], Optional[str]]`
  - `M2fLossAttribution._iter_string_days(df) -> Iterator[Tuple[str, str, pd.Timestamp, pd.DataFrame, str]]`
  - `PER_STRING_COLUMNS`, `CLOSURE_COLUMNS`, `BIFACIAL_COLUMNS`
  - `artifacts`: `M2f_Waterfall`, `M2f_Pareto`, `M2f_PerString`, `M2f_Closure`, `M2f_BifacialCalib`

- [ ] **Step 1: Tulis tes yang gagal**

Buat `tests/unit/test_m2f_report.py`:

```python
"""Tes orchestrator M2f: closure end-to-end, artefak, dan gating config."""
import pandas as pd
import pytest

from pv_pipeline.m2f.ledger import CLOSURE_TOLERANCE_KWH
from pv_pipeline.m2f.report import M2fLossAttribution


def _config(enabled=True, **overrides):
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
            "residual_warn_pct": 30.0,
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
from pv_pipeline.m2f.deficit import deficit_to_kwh
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
    "residual_kwh", "residual_pct", "skipped_reason",
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
        p_loss_by_month: Dict[str, float] = dict(cfg.get("p_loss_by_month") or {})
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

        for string_id, wb_id, day, group, pv_label in self._iter_string_days(df):
            # --- langkah 4: baseline + ledger --------------------------------
            # --- langkah 5: klaim berurutan `order` --------------------------
            # --- langkah 6: assert_closure + akumulasi -----------------------
            pass

        # --- langkah 7: rakit kelima artifact --------------------------------
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

Isi bagian bertanda dengan urutan berikut:

1. `cfg = config.get("m2f") or {}`. Bila `not cfg.get("enabled", False)`: `return []` tanpa menyentuh `self.artifacts`.
2. Bila kolom `Inverter_ID` belum ada, panggil `add_inverter_id(combined_df)`. Bila kolom `PV{n} Power(kW)` belum ada, panggil `add_pv_power_columns(...)`.
3. Muat provider sekali di awal:
   `poa_provider = POAProvider.from_yaml(config["poa"]["site_geometry_path"])`,
   `tcell_provider = CellTempProvider.from_geometry_yaml(config["poa"]["site_geometry_path"])`,
   `spec = PanelSpec.from_yaml(config["panel"]["spec_path"])`.
   Bungkus dengan `try/except Exception` — bila gagal, catat seluruh string sebagai `skipped_reason="provider_unavailable"` dan tetap emit kelima artifact (kosong tapi berskema).
4. Untuk tiap (inverter, PV string, hari):
   - `idx = group["Start Time"]` sebagai `pd.DatetimeIndex`; `wb_id` dari 4 karakter pertama `Inverter_ID`.
   - `poa = poa_provider.get_poa(idx, wb_id)`, `tcell = tcell_provider.get_tcell(idx, wb_id)`.
   - Bila `poa.isna().all()` atau `tcell.isna().all()`: tambahkan baris ke `closure_rows` dengan `skipped_reason="poa_or_tcell_missing"` dan `l_total_kwh/claimed_kwh/residual_kwh = float("nan")`; `continue`.
   - `g = float(cfg.get("bifacial_gain_per_wb", {}).get(wb_id, 1.0))`.
   - `e_exp = compute_expected_energy_kwh(poa, tcell, spec, wb_id, bifacial_gain=g)`.
   - `e_act = compute_actual_energy_kwh(group[f"{pv_label} Power(kW)"])`.
   - `ledger = LossLedger(string_id, day, e_exp.to_numpy(), e_act.to_numpy())`.
5. Klaim berurutan `cfg["attribution_order"]`, lewati entri `"unexplained"`:
   - `availability_outage`: `down_mask` = baris yang kolom `Inverter status`-nya tidak mengandung kata kunci on-grid (gunakan `config["m2e"]["inverter_status_map"]["on_grid_keywords"]`, bandingkan lowercase substring).
   - `dc_cable_fault`: gabungkan artifact `TIMESERIES_DEFICIT_SHEET` dari detektor m2b (diteruskan lewat `config["m2f"].get("deficit_frames")` sebagai `pd.DataFrame` opsional), saring ke `inverter_id`+`pv_string` ini, panggil `deficit_to_kwh(frame, freq_hours=DEFAULT_FREQ_HOURS)`, reindex ke `idx` dengan `fill_value=0.0`, lalu `claim_dc_cable_fault`. Bila tidak ada frame, klaim `0.0`.
   - `soiling`: `p_loss` dari `cfg.get("p_loss_by_month", {}).get(day.strftime("%Y-%m"), 0.0)`; panggil `claim_soiling`.
6. `ledger.assert_closure()`. Kumpulkan `ledger.totals()` ke akumulator site-level dan ke `per_string_rows`.
7. Rakit artifact:
   - `self.artifacts["M2f_PerString"]` — kolom `string_id, day, category, loss_kwh`.
   - `self.artifacts["M2f_Waterfall"]` — `build_waterfall_table(site_totals, cfg["attribution_order"])`.
   - `self.artifacts["M2f_Pareto"]` — `build_pareto_table(site_totals)`.
   - `self.artifacts["M2f_Closure"]` — kolom `string_id, day, l_total_kwh, claimed_kwh, residual_kwh, residual_pct, skipped_reason`.
   - `self.artifacts["M2f_BifacialCalib"]` — kolom `wb_id, g_bifacial, n_strings, n_days`.
   Kelima artifact SELALU di-assign saat `enabled=True`, memakai `pd.DataFrame(columns=[...])` bila tidak ada baris.
8. Bila `residual_pct > cfg["residual_warn_pct"]`, emit satu `M2Finding` dengan `fault_type="weak_attribution"`, `severity=Severity.INFO`, `sub_module="M2f_loss_attribution"`, `value=residual_pct`, `threshold=cfg["residual_warn_pct"]`. Konstruktor persis mengikuti pola di `pv_pipeline/availability.py:180-195`.

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
  # Residual di atas ambang ini memicu finding INFO "weak_attribution".
  residual_warn_pct: 30.0
  # Fraksi rugi soiling per bulan (YYYY-MM -> 0..1), dari
  # M2aSoiling artifact MonthlySoilingLoss.p_loss_pct / 100.
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
Expected: PASS, 6 tes.

Run: `python -m pytest tests/ -q`
Expected: PASS — seluruh suite existing tetap hijau, bertambah 58 tes M2f dari baseline sebelum Task 1.

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
