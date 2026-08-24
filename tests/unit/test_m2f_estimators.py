"""Tes estimator M2f: tiap kategori mengklaim dari ledger sesuai counterfactual."""
import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.estimators import (
    claim_availability_outage,
    claim_dc_cable_fault,
    claim_soiling,
)
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


def test_dc_cable_fault_clips_negative_deficit_before_claiming():
    # WHY: ledger.claim() menolak klaim negatif (lihat
    # test_negative_claim_is_rejected di test_m2f_ledger.py) -- kalau
    # np.maximum(deficit, 0.0) di claim_dc_cable_fault dihapus, deficit_kwh
    # negatif akan diteruskan mentah dan meledak di ledger, bukan diklip
    # senyap ke 0 seperti kontrak eksplisit estimator ini.
    led = _ledger([2.0, 2.0], [1.0, 1.0])
    claimed = claim_dc_cable_fault(led, deficit_kwh=np.array([-5.0, 1.0]))
    assert claimed == pytest.approx(1.0)
