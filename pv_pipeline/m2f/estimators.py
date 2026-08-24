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
