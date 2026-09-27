"""Estimator kWh per kategori. Tiap fungsi mengklaim dari LossLedger.

Dipanggil berurutan prioritas oleh orchestrator. Karena ledger memotong klaim
ke sisa yang tersedia, kategori berprioritas lebih rendah otomatis hanya
melihat energi yang belum dijelaskan.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

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


def claim_curtailment(
    ledger: LossLedger,
    *,
    curtailed_mask: np.ndarray,
) -> float:
    """Klaim seluruh sisa rugi pada timestamp saat output dibatasi dari luar.

    Counterfactual: tanpa perintah shutdown / pembatasan daya dari grid atau
    plant controller, string akan menghasilkan ``E_expected``. Rugi lain di
    jendela itu (soiling, fault) tidak teridentifikasi karena tertutup batas,
    jadi seluruh sisanya milik curtailment -- sama seperti availability.
    Bukan target maintenance; ``pareto.NON_ACTIONABLE`` memuatnya.
    """
    mask = np.asarray(curtailed_mask, dtype=bool)
    remaining = ledger.remaining()
    if mask.shape != remaining.shape:
        raise ValueError(
            f"[m2f] panjang curtailed_mask {mask.shape} != ledger {remaining.shape}"
        )
    return ledger.claim("curtailment", np.where(mask, remaining, 0.0))


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


def shading_deficit_kwh(actual_kwh: pd.Series, hourly: pd.DataFrame) -> np.ndarray:
    """Defisit jam ter-flag M2aShading: ``aktual x (pr_reference / pr_proxy - 1)``.

    Counterfactual referensi-diri: pada jam terbayang, inverter berkinerja
    setara median PR-proxy HARINYA SENDIRI -- bukan inverter tetangga, yang
    biasanya ikut tertutup bayangan terrain yang sama, dan lintas plant
    mencampur 24 vs 26 modul per string. ``hourly`` adalah baris
    HourlyMetrics satu inverter-hari. Jam dengan ``pr_proxy <= 0`` dilewati:
    inverter yang tidak berproduksi di siang hari adalah outage, bukan
    bayangan, dan rasionya tak terhingga.
    """
    actual = actual_kwh.to_numpy(dtype=float)
    deficit = np.zeros_like(actual)
    hours = np.asarray(actual_kwh.index.hour)
    flagged = hourly[hourly["suspicious"].astype(bool) & (hourly["pr_proxy"] > 0)]
    for hour, pr_proxy, pr_reference in zip(
        flagged["hour"], flagged["pr_proxy"], flagged["pr_reference"],
    ):
        factor = float(pr_reference) / float(pr_proxy) - 1.0
        if factor > 0.0:
            in_hour = hours == int(hour)
            deficit[in_hour] = actual[in_hour] * factor
    return deficit


def low_irradiance_deficit_kwh(
    actual_kwh: np.ndarray,
    poa_wm2: np.ndarray,
    inverter_kw: np.ndarray,
    *,
    intercept_mid: float,
    slope_mid: float,
    poa_low_min: float,
    poa_low_max: float,
) -> np.ndarray:
    """Defisit pita cahaya rendah pada inverter yang di-flag M2aLowIrradiance.

    Counterfactual: PR-proxy (kW per W/m2) fit pita menengah, diekstrapolasi
    ke POA pita rendah (``intercept_mid + slope_mid x POA``). Defisit per
    timestamp = ``aktual x (pr_fit / pr_aktual - 1)`` di dalam pita, nol di
    luar pita. Timestamp tanpa daya inverter dilewati (outage, bukan
    low-light). ``inverter_kw`` harus jumlah daya PV yang sama dengan yang
    dipakai detektor, dan POA dari sumber yang sama dengan fit-nya.
    """
    actual = np.asarray(actual_kwh, dtype=float)
    poa = np.asarray(poa_wm2, dtype=float)
    inverter = np.asarray(inverter_kw, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        factor = (intercept_mid + slope_mid * poa) / (inverter / poa) - 1.0
    usable = (
        (poa >= poa_low_min) & (poa <= poa_low_max)
        & (inverter > 0.0) & np.isfinite(factor)
    )
    return np.where(usable, actual * np.clip(factor, 0.0, None), 0.0)


def _claim_deficit(ledger: LossLedger, category: str, deficit_kwh: np.ndarray) -> float:
    deficit = np.asarray(deficit_kwh, dtype=float)
    remaining = ledger.remaining()
    if deficit.shape != remaining.shape:
        raise ValueError(
            f"[m2f] panjang deficit_kwh {deficit.shape} != ledger {remaining.shape}"
        )
    return ledger.claim(category, np.maximum(deficit, 0.0))


def claim_shading(ledger: LossLedger, *, deficit_kwh: np.ndarray) -> float:
    """Klaim defisit :func:`shading_deficit_kwh`. Prioritas SEBELUM soiling:
    SRR menyerap apa saja yang turun perlahan, termasuk bayangan."""
    return _claim_deficit(ledger, "shading", deficit_kwh)


def claim_low_irradiance_eff(ledger: LossLedger, *, deficit_kwh: np.ndarray) -> float:
    """Klaim defisit :func:`low_irradiance_deficit_kwh`, sesudah soiling."""
    return _claim_deficit(ledger, "low_irradiance_eff", deficit_kwh)
