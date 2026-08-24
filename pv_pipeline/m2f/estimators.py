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
    atas gabungan artefak ketiga detektor m2b.
    """
    deficit = np.asarray(deficit_kwh, dtype=float)
    remaining = ledger.remaining()
    if deficit.shape != remaining.shape:
        raise ValueError(
            f"[m2f] panjang deficit_kwh {deficit.shape} != ledger {remaining.shape}"
        )
    return ledger.claim("dc_cable_fault", np.maximum(deficit, 0.0))


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
