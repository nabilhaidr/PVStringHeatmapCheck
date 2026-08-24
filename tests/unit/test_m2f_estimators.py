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
