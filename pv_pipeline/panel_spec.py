"""Panel datasheet loader untuk PLTS-IKN (Jinko JKM625N 78HL4-BDV).

Tanggung jawab:
- Parse ``config/panel_spec.yaml`` ke ``PanelSpec`` (frozen dataclass).
- Helper: ``modules_per_string(wb_id)``, ``voc_at_cell_temp(T)``,
  ``voc_string_nominal(T, wb_id)``, ``voc_string_at_design_min_temp(wb_id)``.

Tidak menyentuh notebook/M2 detector — pure data holder + math helper.
Downstream consumer (rencana): ``peer_zscore.py`` High-R rule untuk hitung Voc_ratio.

Convention temperature coefficient:
    Voc(T_cell) = Voc_STC * (1 + tc_voc_pct/100 * (T_cell - 25))
    Pmax(T)    = Pmax_STC * (1 + tc_pmax_pct/100 * (T_cell - 25))
"""
from __future__ import annotations

import copy
import os
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# Temperatur referensi STC (datasheet IEC 61215).
_T_STC_C: float = 25.0
# Temperatur referensi NOCT (datasheet Jinko: 45 +/- 2 C, dipakai sebagai default).
_T_NOCT_C: float = 45.0


def _ensure_yaml() -> None:
    """Pastikan PyYAML terpasang (mirror pattern di pv_pipeline.m2_config)."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: PyYAML")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "PyYAML"])


@dataclass(frozen=True)
class ElectricalParams:
    """Datasheet electrical numbers di kondisi tertentu (STC atau NOCT)."""

    pmax_w: float
    vmp_v: float
    imp_a: float
    voc_v: float
    isc_a: float
    module_efficiency_pct: Optional[float] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ElectricalParams":
        return cls(
            pmax_w=float(d["pmax_w"]),
            vmp_v=float(d["vmp_v"]),
            imp_a=float(d["imp_a"]),
            voc_v=float(d["voc_v"]),
            isc_a=float(d["isc_a"]),
            module_efficiency_pct=(
                float(d["module_efficiency_pct"])
                if d.get("module_efficiency_pct") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class TempCoefficients:
    """Temperature coefficients (% per Celsius) + operating envelope."""

    pmax_pct_per_c: float
    voc_pct_per_c: float
    isc_pct_per_c: float
    operating_min_c: float
    operating_max_c: float
    noct_c: float
    noct_tolerance_c: float = 0.0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TempCoefficients":
        return cls(
            pmax_pct_per_c=float(d["temp_coef_pmax_pct_per_c"]),
            voc_pct_per_c=float(d["temp_coef_voc_pct_per_c"]),
            isc_pct_per_c=float(d["temp_coef_isc_pct_per_c"]),
            operating_min_c=float(d.get("operating_min_c", -40.0)),
            operating_max_c=float(d.get("operating_max_c", 85.0)),
            noct_c=float(d.get("noct_c", _T_NOCT_C)),
            noct_tolerance_c=float(d.get("noct_tolerance_c", 0.0)),
        )


@dataclass(frozen=True)
class PanelSpec:
    """Hasil parse ``config/panel_spec.yaml``.

    Attributes
    ----------
    panel_model : str
        Identifier human-readable (mis. "Jinko Solar JKM625N 78HL4-BDV").
    technology : str
        Deskripsi teknologi (mis. "N-type Mono-crystalline ...").
    stc, noct : ElectricalParams
        Electrical performance pada dua kondisi referensi.
    temp_coef : TempCoefficients
        Koefisien temperatur + operating envelope.
    max_system_voltage_v : int
        Batas tegangan sistem (Vdc, default 1500).
    cells_per_module : int
        Jumlah sel (default 156 = 2x78).
    bifacial_factor_pct : float | None
        Bifacial factor dari datasheet (None bila non-bifacial).
    default_modules_per_string : int
        Fallback ketika WB tidak terdaftar di ``strings_per_wb``.
    strings_per_wb : Dict[str, int]
        Map ``"WB01" -> modules_per_string``.
    """

    panel_model: str
    technology: str
    stc: ElectricalParams
    noct: ElectricalParams
    temp_coef: TempCoefficients
    max_system_voltage_v: int
    cells_per_module: int
    bifacial_factor_pct: Optional[float]
    default_modules_per_string: int
    strings_per_wb: Dict[str, int] = field(default_factory=dict)

    # ---------- IO ----------

    @classmethod
    def from_yaml(cls, path: str) -> "PanelSpec":
        if not path or not os.path.exists(path):
            raise FileNotFoundError(
                f"[panel_spec] {path!r} not found. Provide path ke config/panel_spec.yaml."
            )

        _ensure_yaml()
        import yaml  # noqa: WPS433

        with open(path, "r", encoding="utf-8") as fp:
            d = yaml.safe_load(fp) or {}

        # strings_per_wb mungkin pakai bentuk {WB01: {modules_per_string: 24}}
        # atau bentuk pendek {WB01: 24}. Normalize ke int per WB.
        raw_strings = d.get("strings_per_wb", {}) or {}
        strings_per_wb: Dict[str, int] = {}
        for wb_id, val in raw_strings.items():
            key = str(wb_id).upper()
            if isinstance(val, dict):
                n = val.get("modules_per_string")
            else:
                n = val
            try:
                strings_per_wb[key] = int(n)
            except (TypeError, ValueError):
                warnings.warn(
                    f"[panel_spec] strings_per_wb[{key!r}] invalid (got {val!r}), skipping.",
                    stacklevel=2,
                )

        bifacial_d = d.get("bifacial") or {}
        bif_pct = bifacial_d.get("bifacial_factor_pct")
        system_d = d.get("system") or {}
        mech_d = d.get("mechanical") or {}

        return cls(
            panel_model=str(d.get("panel_model", "Unknown panel")),
            technology=str(d.get("technology", "")),
            stc=ElectricalParams.from_dict(d["electrical_stc"]),
            noct=ElectricalParams.from_dict(d["electrical_noct"]),
            temp_coef=TempCoefficients.from_dict(d["temperature"]),
            max_system_voltage_v=int(system_d.get("max_system_voltage_v", 1500)),
            cells_per_module=int(mech_d.get("cells_per_module", 156)),
            bifacial_factor_pct=(float(bif_pct) if bif_pct is not None else None),
            default_modules_per_string=int(d.get("default_modules_per_string", 26)),
            strings_per_wb=strings_per_wb,
        )

    # ---------- Helpers ----------

    def modules_per_string(self, wb_id: str) -> int:
        """Ambil modules_per_string per WB (case-insensitive), fallback ke default."""
        if wb_id is None:
            return self.default_modules_per_string
        key = str(wb_id).upper()
        return int(self.strings_per_wb.get(key, self.default_modules_per_string))

    def voc_at_cell_temp(
        self,
        cell_temp_c: float,
        *,
        base: str = "stc",
    ) -> float:
        """Voc(T_cell) per panel, applying linear temp coefficient.

        Parameters
        ----------
        cell_temp_c : float
            Suhu sel (C). Untuk cold-morning Voc analysis kira-kira 10-15 C.
        base : {"stc", "noct"}
            Kondisi referensi. Default STC (25 C, datasheet umum).
        """
        if base == "stc":
            voc_base = self.stc.voc_v
            t_base = _T_STC_C
        elif base == "noct":
            voc_base = self.noct.voc_v
            t_base = self.temp_coef.noct_c
        else:
            raise ValueError(f"base must be 'stc' or 'noct', got {base!r}")

        delta_t = float(cell_temp_c) - t_base
        return voc_base * (1.0 + self.temp_coef.voc_pct_per_c / 100.0 * delta_t)

    def voc_string_nominal(
        self,
        cell_temp_c: float,
        wb_id: str,
        *,
        base: str = "stc",
    ) -> float:
        """Voc_string nominal pada (T_cell, WB).

        = voc_at_cell_temp(T) * modules_per_string(wb_id).
        """
        return self.voc_at_cell_temp(cell_temp_c, base=base) * self.modules_per_string(wb_id)

    def voc_string_at_design_min_temp(
        self,
        wb_id: str,
        *,
        min_cell_temp_c: float = 10.0,
    ) -> float:
        """Voc_string saat cold-morning (default 10 C) -> max Voc skenario.

        Dipakai untuk validasi system voltage tidak overshoot 1500 V dan untuk
        baseline High-R rule (Voc_ratio = Voc_aktual / Voc_string_nominal_cold).
        """
        return self.voc_string_nominal(min_cell_temp_c, wb_id, base="stc")

    def voc_string_stc(self, wb_id: str) -> float:
        """Convenience: Voc_string @ STC (25 C cell)."""
        return self.voc_string_nominal(_T_STC_C, wb_id, base="stc")


# Optional: smoke test saat dijalankan langsung (`python -m pv_pipeline.panel_spec`).
if __name__ == "__main__":
    import sys

    default_path = "config/panel_spec.yaml"
    yaml_path = sys.argv[1] if len(sys.argv) > 1 else default_path

    spec = PanelSpec.from_yaml(yaml_path)
    print(f"[panel_spec] loaded: {spec.panel_model}")
    print(f"  STC Voc={spec.stc.voc_v} V  Isc={spec.stc.isc_a} A  Pmax={spec.stc.pmax_w} W")
    print(f"  NOCT Voc={spec.noct.voc_v} V  Isc={spec.noct.isc_a} A  Pmax={spec.noct.pmax_w} W")
    print(f"  temp_coef Voc={spec.temp_coef.voc_pct_per_c} %/C")

    for wb in ["WB01", "WB02", "WB05", "WB10", "WB99"]:
        n = spec.modules_per_string(wb)
        voc_stc = spec.voc_string_stc(wb)
        voc_cold = spec.voc_string_at_design_min_temp(wb)
        margin = (1.0 - voc_cold / spec.max_system_voltage_v) * 100.0
        print(
            f"  {wb}: n={n}  Voc_string_STC={voc_stc:.1f} V  "
            f"Voc_string_cold(10C)={voc_cold:.1f} V  margin_to_1500V={margin:+.1f}%"
        )
    print("[panel_spec] smoke OK")
