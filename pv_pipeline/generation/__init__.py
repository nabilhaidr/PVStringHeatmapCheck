"""Generation dataset loader untuk PLTS-IKN (Wave 7).

Load ``IKN Generation.xlsx`` sheet ``Summary (PV)`` -> daily energy per WB
(WB01..WB10) + total + PAE (Projected Available Energy) + Generation STS
(max gated by busbar setpoint) untuk PR (Performance Ratio) analysis.
"""
from __future__ import annotations

from .loader import GenerationLoader

__all__ = ["GenerationLoader"]
