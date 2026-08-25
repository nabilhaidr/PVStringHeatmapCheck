"""M2f: Loss Attribution & Pareto Analysis.

Spec: docs/superpowers/specs/2026-08-11-m2f-loss-attribution-design.md
"""
from pv_pipeline.m2f.ledger import (
    CLAIMABLE_CATEGORIES,
    CLOSURE_TOLERANCE_KWH,
    LOCKED_CATEGORIES,
    LossLedger,
)
from pv_pipeline.m2f.report import M2fLossAttribution

__all__ = [
    "CLAIMABLE_CATEGORIES",
    "CLOSURE_TOLERANCE_KWH",
    "LOCKED_CATEGORIES",
    "LossLedger",
    "M2fLossAttribution",
]
