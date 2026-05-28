from __future__ import annotations

from pv_pipeline.dashboard.auth import _check_password


def test_check_password_accepts_exact_match():
    assert _check_password("correct horse", "correct horse") is True


def test_check_password_rejects_wrong_or_empty_password():
    assert _check_password("wrong", "correct horse") is False
    assert _check_password("", "correct horse") is False
