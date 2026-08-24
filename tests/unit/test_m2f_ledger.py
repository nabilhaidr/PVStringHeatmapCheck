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
