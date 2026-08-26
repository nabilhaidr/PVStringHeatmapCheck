"""Smoke headless m2f_loss_attribution.ipynb: exec Cell 3-6 dengan data sintetis.

Pakai: python -X utf8 notebook/_smoke_m2f.py
Tanpa Drive/Colab: combined_df sintetis kecil, output ke folder temp. Berkas
POA/pyranometer dan "PV Module Temperature PLTS IKN.xlsx" TIDAK ada di
working tree ini (lihat markdown Cell 0 notebook) -- smoke ini justru
membuktikan chain-nya tetap selesai sampai workbook + 2 PNG walau tiap
provider gagal load, persis kondisi nyata hari ini.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

warnings.simplefilter("ignore", UserWarning)

nb = json.loads(
    (ROOT / "notebook" / "m2f_loss_attribution.ipynb").read_text(encoding="utf-8"))
src_cell3 = "".join(nb["cells"][3]["source"])
src_cell4 = "".join(nb["cells"][4]["source"])
src_cell5 = "".join(nb["cells"][5]["source"])
src_cell6 = "".join(nb["cells"][6]["source"])

# combined_df sintetis: 1 inverter x 4 timestamp x 1 hari, PV1-PV5 lengkap
# (power/voltage/current) supaya open_circuit/mppt_ratio punya sesuatu untuk
# dianalisis.
times = pd.date_range("2026-05-13 08:00", periods=4, freq="5min")
rows = []
for t in times:
    row = {
        "Inverter_ID": "WB03-INV01",
        "Start Time": t,
        "Inverter status": "On-grid",
    }
    for n in range(1, 6):
        row[f"PV{n} Power(kW)"] = 4.0
        row[f"PV{n} input voltage(V)"] = 1200.0
        row[f"PV{n} input current(A)"] = 3.33
    rows.append(row)
combined_df = pd.DataFrame(rows)

out_dir = tempfile.mkdtemp(prefix="m2f_smoke_")
g = {
    "REPO_DIR": str(ROOT),
    "M2F_OUT_DIR": out_dir,
    "combined_df": combined_df,
}

print("[smoke] exec Cell 3 (config)...")
exec(src_cell3, g)
print("[smoke] exec Cell 4 (detektor)...")
exec(src_cell4, g)
print("[smoke] exec Cell 5 (collect + M2f)...")
exec(src_cell5, g)
print("[smoke] exec Cell 6 (workbook + figures)...")
exec(src_cell6, g)

produced = sorted(os.listdir(out_dir))
print("[smoke] outputs:", produced)
assert any(
    f.startswith("m2f_loss_attribution_") and f.endswith(".xlsx") for f in produced
), "no M2f workbook xlsx"
assert any(
    f.startswith("m2f_waterfall_") and f.endswith(".png") for f in produced
), "no waterfall PNG"
assert any(
    f.startswith("m2f_pareto_") and f.endswith(".png") for f in produced
), "no pareto PNG"
print("[smoke] OK")
