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
        # np.nansum sebelumnya di sini murni defensif -- constructor sudah
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
        # nan_to_num(nan=0.0) di sini dulu akan melaporkan "dicek, aman"
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
