"""Generate notebook/physics_calculations.ipynb (standalone physics walkthrough).

Pakai: python notebook/_build_physics_nb.py
nbformat tidak terinstall di environment ini, jadi notebook ditulis sebagai JSON
mentah (nbformat 4.5) — sama seperti _build_orch.py.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "physics_calculations.ipynb"

MD_INTRO = """\
# Perhitungan Physics PV Pipeline (PLTS-IKN)

Notebook terpisah untuk seluruh perhitungan berbasis fisika di `pv_pipeline`:
**P_expected**, **Kt** (clearness index), **ΔP**, **integrasi energi**, **PR**
(IEC 61724-1), dan **Voc**. Referensi lengkap formula + audit `file:baris`:
`docs/reverse_engineering_physics.md`.

Notebook ini hanya *memakai* fungsi `pv_pipeline.physics` / `PanelSpec`
(tidak menduplikasi formula), dengan:
1. Contoh worked-example (scalar) yang bisa dicek tangan.
2. Profil hari sintetis 5-menit (raw data pengukuran tidak ada di repo).
3. Verifikasi konsistensi terhadap `outputs/pr_daily_YYYYMMDD.csv`.
"""

CODE_SETUP = """\
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Repo root = folder yang berisi pv_pipeline/ (notebook ini ada di notebook/)
ROOT = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p / "pv_pipeline").exists())
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pv_pipeline import physics
from pv_pipeline.panel_spec import PanelSpec

spec = PanelSpec.from_yaml(str(ROOT / "config" / "panel_spec.yaml"))
print("Konstanta STC :", physics.G_STC_WM2, "W/m^2 ,", physics.T_STC_C, "C")
print("Panel         :", spec.stc.pmax_w, "W ; gamma_Pmax =", spec.temp_coef.pmax_pct_per_c, "%/C")
print("Modul/string  : WB01 =", spec.modules_per_string("WB01"), "; WB05 =", spec.modules_per_string("WB05"))
"""

MD_PEXP = """\
## 1. P_expected — Daya Ekspektasi

Per modul (`physics.compute_pmax_per_module`):

$$P = P_{max,STC} \\times \\frac{POA}{1000} \\times \\left(1 + \\frac{\\gamma}{100}(T_{cell} - 25)\\right)$$

Per string = per modul × jumlah modul (24 utk WB01–02, 26 utk WB03–10).
"""

CODE_PEXP = """\
# Worked examples (cek tangan)
p_stc = physics.compute_pmax_per_module(1000.0, 25.0, spec)
p_hot = physics.compute_pmax_per_module(1000.0, 55.0, spec)
print(f"STC (1000 W/m2, 25 C)      : {p_stc:.1f} W/modul (datasheet: 625)")
print(f"Hot noon (1000 W/m2, 55 C) : {p_hot:.1f} W/modul (expected: 570.6)")
print(f"P_string WB01 @STC         : {physics.compute_p_expected_per_string(1000.0, 25.0, spec, 'WB01'):.0f} W (= 625 x 24)")
print(f"P_string WB05 @STC         : {physics.compute_p_expected_per_string(1000.0, 25.0, spec, 'WB05'):.0f} W (= 625 x 26)")

# Profil hari sintetis: grid 5-menit, POA bell-curve 06:00-18:00, Tcell ikut POA
ts = pd.date_range("2026-05-14 00:00", "2026-05-14 23:55", freq="5min")
hour = ts.hour + ts.minute / 60.0
sun = np.clip(np.sin(np.pi * (hour - 6.0) / 12.0), 0.0, None)  # 0 di luar 06-18
poa = pd.Series(1000.0 * sun, index=ts, name="poa_wm2")
tcell = pd.Series(25.0 + 30.0 * sun, index=ts, name="tcell_c")  # puncak 55 C

p_exp_wb01 = physics.compute_p_expected_per_string(poa, tcell, spec, "WB01") / 1000.0  # kW
p_exp_wb05 = physics.compute_p_expected_per_string(poa, tcell, spec, "WB05") / 1000.0

fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(ts, p_exp_wb01, label="WB01 (24 modul)")
ax.plot(ts, p_exp_wb05, label="WB05 (26 modul)")
ax.set_ylabel("P_expected per string (kW)")
ax.set_title("P_expected per string — profil hari sintetis")
ax.legend()
plt.tight_layout()
plt.show()
"""

MD_KT = """\
## 2. Kt — Clearness Index

`Kt = POA_measured / POA_clearsky` (NaN bila clearsky < 1 W/m²).
Kt ≈ 1 cerah, < 1 berawan, > 1 cloud-edge enhancement (biasanya artefak).
"""

CODE_KT = """\
# Simulasi hari berawan: lewatkan awan jam 10-12 (POA terukur drop 60%)
poa_measured = poa.copy()
cloud = (hour >= 10) & (hour < 12)
poa_measured[cloud] *= 0.4

kt = physics.compute_kt(poa_measured, poa)
fig, ax = plt.subplots(figsize=(10, 3))
ax.plot(ts, kt, color="tab:purple")
ax.axhline(1.0, color="gray", ls="--", lw=0.8)
ax.set_ylabel("Kt")
ax.set_title("Clearness index — jam 10-12 berawan (Kt = 0.4)")
plt.tight_layout()
plt.show()
print("Kt siang (clear) :", float(kt[hour == 14].iloc[0]))
print("Kt saat berawan  :", float(kt[hour == 11].iloc[0]))
print("Kt malam         :", kt[hour == 0].iloc[0], "(NaN sesuai guard clearsky < 1 W/m2)")
"""

MD_DP = """\
## 3. ΔP — Delta Power Ratio

`ΔP_ratio = (P_actual / P_expected) − 1` (NaN bila P_expected < 1 W).
≈ 0 normal; < 0 underperform (soiling/shading/fault); > 0 overperform
(drift sensor / cloud-edge / underestimate model).
"""

CODE_DP = """\
# String sehat = 98% dari ekspektasi; injeksi fault jam 13-15 (drop ke 55%)
p_expected_w = physics.compute_p_expected_per_string(poa, tcell, spec, "WB05")
health = pd.Series(0.98, index=ts)
fault = (hour >= 13) & (hour < 15)
health[fault] = 0.55
p_actual_w = p_expected_w * health

delta = physics.compute_delta_power(p_actual_w, p_expected_w)
fig, ax = plt.subplots(figsize=(10, 3))
ax.plot(ts, delta, color="tab:red")
ax.axhline(0.0, color="gray", ls="--", lw=0.8)
ax.set_ylabel("ΔP ratio")
ax.set_title("ΔP ratio — fault disuntik jam 13-15")
plt.tight_layout()
plt.show()
print(f"ΔP normal : {float(delta[hour == 10].iloc[0]):+.3f} (expected -0.02)")
print(f"ΔP fault  : {float(delta[hour == 14].iloc[0]):+.3f} (expected -0.45)")
"""

MD_ENERGY = """\
## 4. Integrasi Energi

Riemann sum: `E_kWh = Σ P_kW × dt_jam`, dt auto-detect dari median Δt index
(5 menit → 1/12 jam). Cross-check yang dimaksud kode: hasil integrasi ≈ kWh
harian STS di `IKN Generation.xlsx`.
"""

CODE_ENERGY = """\
p_string_kw = pd.Series(p_actual_w / 1000.0, index=ts)
e_string = physics.compute_active_power_integration_kwh(p_string_kw)

# Sanity analitik: integral bell sin 06-18 = (2/pi)*12 h * P_peak
p_peak_kw = float(p_string_kw.max())
print(f"E string (integrasi)  : {e_string:.2f} kWh")
print(f"Sanity (2/pi)*12*Ppeak: {(2 / np.pi) * 12 * p_peak_kw:.2f} kWh (kasar, sebelum koreksi suhu & fault)")

# Insolasi harian POA (kWh/m2): input PR
h_poa = physics.compute_active_power_integration_kwh(poa / 1000.0)
print(f"Insolasi H_POA        : {h_poa:.2f} kWh/m2 (bell 1000 W/m2 peak -> (2/pi)*12 = {2 / np.pi * 12:.2f})")
"""

MD_PR = """\
## 5. PR — Performance Ratio (IEC 61724-1)

$$PR = \\frac{E_{actual}}{H_{POA} \\times C_{kWp}}$$

Kapasitas site 71 500 kWp; per-WB memakai pembagi **terpisah** dari
`generation.capacity_kwp_per_wb` (user-provided 2026-06-12, total 71 513 kWp).
Interpretasi: 0.75–0.85 normal (PLTS tropis), < 0.70 underperform,
> 0.90 mencurigakan (drift kalibrasi).
"""

CODE_PR = """\
CAPACITY_SITE_KWP = 71_500.0

# Pembagi DC per WB (2026-06-12): terpisah per WB, bukan site/10.
from pv_pipeline.m2_config import load_m2_config
_gen_cfg = load_m2_config(str(ROOT / "config" / "m2_config.yaml")).get("generation", {})
CAP_PER_WB = _gen_cfg.get("capacity_kwp_per_wb", {}) or {}
print("Pembagi DC per WB (kWp):", CAP_PER_WB)
print(f"Total per-WB: {sum(CAP_PER_WB.values()):.0f} kWp vs site: {CAPACITY_SITE_KWP:.0f} kWp")

# Worked example sintetis: site dgn PR "sebenarnya" 0.80
e_site_kwh = 0.80 * h_poa * CAPACITY_SITE_KWP
pr_site = physics.compute_pr(e_site_kwh, h_poa, CAPACITY_SITE_KWP)
print(f"PR site sintetis: {pr_site:.4f} (harus 0.8000)")

# Verifikasi terhadap output pipeline nyata
pr_files = sorted((ROOT / "outputs").glob("pr_daily_*.csv"))
if not pr_files:
    print("Tidak ada outputs/pr_daily_*.csv — bagian verifikasi dilewati.")
else:
    pr_df = pd.read_csv(pr_files[-1], index_col=0)
    wb_cols = [c for c in pr_df.columns if c.startswith("pr_WB")]
    row = pr_df.iloc[-1]
    pr_site_csv = float(row["pr_site"])
    pr_wb_mean = float(row[wb_cols].astype(float).mean())
    print(f"\\nFile           : {pr_files[-1].name}")
    print(f"pr_site        : {pr_site_csv:.5f}")
    print(f"mean(pr_WBxx)  : {pr_wb_mean:.5f}")
    print(f"selisih        : {abs(pr_site_csv - pr_wb_mean):.5f}")
    print("Konsisten: PR_site = ΣE_wb/(mean(H_wb)×C) ≈ rata-rata PR per-WB"
          " bila insolasi antar WB hampir sama (docs §13.1).")
    assert abs(pr_site_csv - pr_wb_mean) < 0.01, "pr_site menyimpang dari mean per-WB"
"""

MD_VOC = """\
## 6. Voc — Open-Circuit Voltage

Nominal dari datasheet (`PanelSpec`): `Voc(T) = Voc_STC × (1 + β/100 (T−25))`,
β = −0.25 %/°C, dikali jumlah modul per string. Voc aktual diestimasi pipeline
dari V saat |I| < 0.5 A (sunrise/sunset). Threshold detector:
`voc_ratio > 0.95` syarat `high_R` (M2b); `< 0.85` indikasi ground fault.
"""

CODE_VOC = """\
for wb in ["WB01", "WB05"]:
    v_stc = spec.voc_string_stc(wb)
    v_cold = spec.voc_string_at_design_min_temp(wb)  # 10 C cold morning
    v_hot = spec.voc_string_nominal(65.0, wb)
    print(f"{wb}: Voc_string STC={v_stc:7.1f} V | cold 10C={v_cold:7.1f} V | hot 65C={v_hot:7.1f} V")
print("\\nBatas sistem 1500 V — cold morning tidak boleh overshoot.")

# Ilustrasi voc_ratio: string sehat vs degradasi
voc_nominal = spec.voc_string_stc("WB05")
for label, voc_actual in [("sehat", 0.98 * voc_nominal), ("ground-fault suspect", 0.80 * voc_nominal)]:
    ratio = voc_actual / voc_nominal
    flag = "high_R eligible (>0.95)" if ratio > 0.95 else ("ground fault indicator (<0.85)" if ratio < 0.85 else "abu-abu")
    print(f"voc_ratio {label}: {ratio:.2f} -> {flag}")
"""

MD_CAVEAT = """\
## 7. Catatan Model (dari reverse engineering, docs §14)

- γ_Pmax operatif = **−0.29 %/°C** (`panel_spec.yaml`); docstring `physics.py`
  menulis −0.30 — yang dipakai runtime −0.29.
- **Bifacial gain tidak dimodelkan** (panel bifacial 80%) → P_expected/PR
  cenderung underestimate ekspektasi; ΔP bisa bias positif.
- **PR tidak weather-corrected** → hari panas PR tampak lebih rendah tanpa
  berarti fault.
- Pembagi DC per-WB kini **terpisah** (`generation.capacity_kwp_per_wb`,
  2026-06-12) menggantikan asumsi seragam 7 150 kWp. Total per-WB 71 513 kWp
  vs site 71 500 kWp (selisih 13 kWp; pr_site tetap pakai 71 500).
- P_expected linear single-factor: tanpa low-light efficiency / spectral / IAM.
"""

CELLS = [
    ("markdown", MD_INTRO),
    ("code", CODE_SETUP),
    ("markdown", MD_PEXP),
    ("code", CODE_PEXP),
    ("markdown", MD_KT),
    ("code", CODE_KT),
    ("markdown", MD_DP),
    ("code", CODE_DP),
    ("markdown", MD_ENERGY),
    ("code", CODE_ENERGY),
    ("markdown", MD_PR),
    ("code", CODE_PR),
    ("markdown", MD_VOC),
    ("code", CODE_VOC),
    ("markdown", MD_CAVEAT),
]


def _cell(kind: str, source: str, idx: int) -> dict:
    lines = source.splitlines(keepends=True)
    cell = {
        "cell_type": kind,
        "id": f"physics-{idx:02d}",
        "metadata": {},
        "source": lines,
    }
    if kind == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def build() -> None:
    nb = {
        "cells": [_cell(kind, src, idx) for idx, (kind, src) in enumerate(CELLS)],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    OUT.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT} ({len(CELLS)} cells)")


if __name__ == "__main__":
    build()
