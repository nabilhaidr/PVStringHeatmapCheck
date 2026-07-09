"""Smoke headless daily_runfast_v1.ipynb: exec Cell 3+4 dengan data sintetis.

Pakai: python -X utf8 notebook/_smoke_daily_runfast.py
Tanpa Drive/Colab: combined_df sintetis kecil, output ke folder temp.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.show = lambda *a, **k: None  # stub: headless

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

nb = json.loads(
    (ROOT / "notebook" / "daily_runfast_v1.ipynb").read_text(encoding="utf-8"))
src_cell3 = "".join(nb["cells"][3]["source"])
src_cell4 = "".join(nb["cells"][4]["source"])

# combined_df sintetis: 2 inverter x 6 timestamp, 1 tanggal.
# Semua 28 kolom PV power WAJIB ada: plot_single_inv_heatmap mengindeks
# PV1..PV{pv_max_allowed} (viz.py) — data asli selalu punya 28 kolom.
times = pd.date_range("2026-07-01 06:00", periods=6, freq="h")
rows = []
for inv in ["WB01-INV01", "WB01-INV02"]:
    for t in times:
        row = {
            "Inverter_ID": inv,
            "Start Time": t,
            "Inverter status": "Grid connected",
        }
        for n in range(1, 29):
            row[f"PV{n} Power(kW)"] = float(n)
        rows.append(row)
combined_df = pd.DataFrame(rows)

out_dir = tempfile.mkdtemp(prefix="runfast_smoke_")
g = {
    "REPO_DIR": str(ROOT),
    "DAILY_OUT_DIR": out_dir,
    "STRINGS_YAML": str(ROOT / "config" / "strings.yaml"),
    "PV_MAX_ALLOWED": 28,
    "CELL_SIZE": 0.22,
    "combined_df": combined_df,
}

print("[smoke] exec Cell 3 (heatmap loop)...")
exec(src_cell3, g)
print("[smoke] exec Cell 4 (M2e only)...")
exec(src_cell4, g)

produced = sorted(os.listdir(out_dir))
print("[smoke] outputs:", produced)
assert any(f.startswith("heatmap_") and f.endswith(".png") for f in produced), \
    "no heatmap PNG saved"
assert any(f.startswith("m2_findings_") and f.endswith(".jsonl") for f in produced), \
    "no findings jsonl"
assert any(f.startswith("m2_findings_") and f.endswith(".xlsx") for f in produced), \
    "no findings xlsx"
has_csv = any(f.startswith("inverter_operation_") for f in produced)
print(f"[smoke] inverter_operation csv present: {has_csv}")
print("[smoke] OK")
