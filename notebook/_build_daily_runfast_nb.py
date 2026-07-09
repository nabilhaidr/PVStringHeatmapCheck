"""Generate notebook/daily_runfast_v1.ipynb (heatmap + M2e Availability only).

Pakai: python notebook/_build_daily_runfast_nb.py
nbformat tidak terinstall di environment ini, jadi notebook ditulis sebagai JSON
mentah (nbformat 4.5) — sama seperti _build_physics_nb.py.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "daily_runfast_v1.ipynb"

MD_INTRO = """\
# Daily Runfast — Heatmap + M2e Availability (v1)

Notebook harian CEPAT: hanya butuh snapshot Excel harian (`1-2.xlsx` + `3-10.xlsx`)
dari folder Drive raw data. Tanpa cuaca/pyranometer/baseline history/model.

Isi:
1. **Cell 1** — Config + download folder Drive + siapkan `outputs_daily_runfast/`.
2. **Cell 2** — Load Excel + transform → `combined_df`.
3. **Cell 3** — Heatmap semua inverter; PNG disimpan ke `outputs_daily_runfast/`.
4. **Cell 4** — M2eAvailability only → `m2_findings_{tanggal}.jsonl/.xlsx` +
   `inverter_operation_{tanggal}.csv` di `outputs_daily_runfast/`.

Yang sengaja TIDAK ada: detector POA-aware (PeerZ / OpenCircuit / GroundFault /
IForest / Shading / LowIrradiance), soiling, LSTM-AE, baseline CSV/parquet
(Cell 7 notebook penuh), dan export df_plot (Cell 8). Output tidak menimpa
`outputs/` milik run penuh.

Notebook ini di-generate `notebook/_build_daily_runfast_nb.py` — edit builder,
bukan .ipynb langsung.
"""

CODE_CELL1 = """\
# Cell 1 — Config + download Google Drive folder + folder output runfast
from pathlib import Path
import os, sys

def find_repo_root(start=None):
    p = Path(start or os.getcwd()).resolve()
    for candidate in [p, *p.parents]:
        if (candidate / "pv_pipeline").is_dir() and (candidate / "config").is_dir():
            return candidate
    raise RuntimeError("Repo root tidak ditemukan dari cwd/notebook location.")

REPO_DIR = str(find_repo_root())
os.chdir(REPO_DIR)

if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

from pv_pipeline.data_loader import download_from_gdrive, find_expected_files

# ===== USER CONFIG (edit per-run) =====
DRIVE_FOLDER_URL = "https://drive.google.com/drive/folders/1rQVbeekVrUn0flzl-wyUjAJzu4MfTAIP"
EXPECTED_FILES   = ['1-2.xlsx', '3-10.xlsx']

EXCEL_HEADER_ROW = 3
USECOLS          = None
NROWS            = None

# Visual & string config
CELL_SIZE       = 0.22
PV_MAX_ALLOWED  = 28
STRINGS_YAML    = os.path.join(REPO_DIR, 'config', 'strings.yaml')
# =====================================

DAILY_OUT_DIR = os.path.join(REPO_DIR, "outputs_daily_runfast")
os.makedirs(DAILY_OUT_DIR, exist_ok=True)
print("DAILY_OUT_DIR:", DAILY_OUT_DIR)

FOLDER     = download_from_gdrive(DRIVE_FOLDER_URL, EXPECTED_FILES)
FILE_PATHS = find_expected_files(FOLDER, EXPECTED_FILES)

_missing = [f for f in EXPECTED_FILES
            if not any(os.path.basename(p).lower() == f.lower() for p in FILE_PATHS)]
if _missing:
    print("Warning: expected files not found:", _missing)
else:
    print("All expected files found.")

print("FOLDER     :", FOLDER)
print("FILE_PATHS :")
for p in FILE_PATHS:
    print(" -", p)
"""

CODE_CELL2 = """\
# Cell 2 — Load Excel + transform (data-processing only, no plot)
from pv_pipeline.data_loader import load_and_prepare_data
from pv_pipeline.transformations import (
    add_inverter_id, add_pv_power_columns, add_total_pv_power, make_pivot,
)

combined_df = load_and_prepare_data(
    folder_path     = FOLDER,
    expected_files  = EXPECTED_FILES,
    excel_header_row= EXCEL_HEADER_ROW,
    usecols         = USECOLS,
    nrows           = NROWS,
)
print("Loaded combined_df rows:", combined_df.shape[0],
      "columns:", combined_df.shape[1])

combined_df = add_inverter_id(combined_df)
print(f"Found {combined_df['Inverter_ID'].nunique()} unique Inverter_IDs.")

combined_df, pv_cols = add_pv_power_columns(combined_df)
combined_df          = add_total_pv_power(combined_df, pv_cols)
print(f"Created {len(pv_cols)} PV power columns.")
print(f"Total_PV_power_kW non-null count: "
      f"{combined_df['Total_PV_power_kW'].notna().sum()}")

pivot = make_pivot(combined_df)
print(f"Pivot table shape: {pivot.shape}")
"""

CODE_CELL3 = """\
# Cell 3 — Heatmap per inverter + simpan PNG ke outputs_daily_runfast/
import os
import pandas as pd
import matplotlib.pyplot as plt

from pv_pipeline.transformations import prepare_df_work
from pv_pipeline.string_config  import get_empty_pv_map
from pv_pipeline.viz            import plot_single_inv_heatmap

if 'combined_df' not in globals():
    raise RuntimeError("[runfast] combined_df missing. Run Cell 1 + Cell 2 dulu.")

EMPTY_PV_MAP_CLEAN = get_empty_pv_map(STRINGS_YAML, pv_max_allowed=PV_MAX_ALLOWED)
print(f"Loaded EMPTY_PV_MAP: {len(EMPTY_PV_MAP_CLEAN)} inverter entries")

df_work, df_plot, pv_keep = prepare_df_work(combined_df, pv_max_allowed=PV_MAX_ALLOWED)
print(f"df_work rows={len(df_work)}, df_plot rows={len(df_plot)}, "
      f"pv_keep cols={len(pv_keep)}")

# Auto-detect tanggal untuk nama file PNG (pola Cell 8 notebook penuh)
_start_times = pd.to_datetime(df_plot['Start Time'], errors='coerce').dropna()
_dates = sorted(_start_times.dt.date.unique())
if len(_dates) == 1:
    HEATMAP_DATESTR = _dates[0].strftime("%Y%m%d")
else:
    HEATMAP_DATESTR = f"{_dates[0].strftime('%Y%m%d')}-{_dates[-1].strftime('%Y%m%d')}"
    print(f"  Warning: data mencakup {len(_dates)} tanggal "
          f"({_dates[0]} s.d. {_dates[-1]})")

MAX_TO_PLOT = None  # None => plot semua inverter
all_invs = sorted(set(df_plot['Inverter_ID'].dropna().unique()))
if MAX_TO_PLOT is not None:
    all_invs = all_invs[:MAX_TO_PLOT]

count = 0
errors = []
for idx, inv in enumerate(all_invs, start=1):
    try:
        print(f"[{idx}/{len(all_invs)}] Plotting: {inv}")
        plot_single_inv_heatmap(
            inv_id         = inv,
            df             = df_plot,
            pv_max_allowed = PV_MAX_ALLOWED,
            cell_size      = CELL_SIZE,
            show           = False,
            empty_pv_map   = EMPTY_PV_MAP_CLEAN,
        )
        fig = plt.gcf()
        png_path = os.path.join(DAILY_OUT_DIR, f"heatmap_{HEATMAP_DATESTR}_{inv}.png")
        fig.savefig(png_path, bbox_inches="tight")
        plt.show()
        plt.close(fig)
        count += 1
    except Exception as err:
        print(f"Error for {inv}: {err}")
        errors.append((inv, str(err)))
        continue

print(f"\\nPlotted+saved: {count}  |  Errors: {len(errors)}")
print(f"PNG folder: {DAILY_OUT_DIR}")
if errors:
    print("First errors:")
    for e in errors[:10]:
        print(" -", e[0], ":", e[1])
"""

CODE_CELL4 = """\
# Cell 4 — M2eAvailability ONLY (runfast: tanpa POA/panel/Tcell/detector lain)
import os
import warnings
from collections import Counter
import pandas as pd

from pv_pipeline.availability import M2eAvailability
from pv_pipeline.core import M2Engine
from pv_pipeline.m2_config import load_m2_config

warnings.simplefilter("ignore", UserWarning)

REPO_DIR = globals().get("REPO_DIR", os.getcwd())

def _resolve(cfg_filename: str) -> str:
    for base in [os.path.join(REPO_DIR, "config"), "config"]:
        p = os.path.join(base, cfg_filename)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"[m2e-runfast] {cfg_filename!r} not found.")

M2_CFG_PATH = _resolve("m2_config.yaml")
print(f"[m2e-runfast] m2_config: {M2_CFG_PATH}")
cfg = load_m2_config(M2_CFG_PATH)

# --- Pre-check combined_df ---
if "combined_df" not in globals():
    raise RuntimeError("[m2e-runfast] combined_df missing. Run Cell 1 + Cell 2 dulu.")
required = ["Inverter_ID", "Start Time", "Inverter status"]
missing = [c for c in required if c not in combined_df.columns]
if missing:
    raise RuntimeError(f"[m2e-runfast] missing columns: {missing}")

# --- Auto-detect tanggal ---
st = pd.to_datetime(combined_df["Start Time"], errors="coerce").dropna()
st_dates = sorted(st.dt.date.unique())
if len(st_dates) == 1:
    DATA_DATESTR = st_dates[0].strftime("%Y%m%d")
else:
    DATA_DATESTR = f"{st_dates[0].strftime('%Y%m%d')}-{st_dates[-1].strftime('%Y%m%d')}"
print(f"[m2e-runfast] data dates = {DATA_DATESTR}  ({len(st_dates)} day(s))")
print(f"[m2e-runfast] combined_df shape = {combined_df.shape}")

# --- Engine: M2e saja ---
sm_e = M2eAvailability()
engine = M2Engine([sm_e])
findings = engine.run_all(combined_df, cfg)

# --- Bridge M2eAvailability legacy artifacts ke self.artifacts ---
if getattr(sm_e, "last_all_strings_df", None) is not None and not sm_e.last_all_strings_df.empty:
    sm_e.artifacts["AllStrings"] = sm_e.last_all_strings_df
if getattr(sm_e, "last_inverter_log_df", None) is not None and not sm_e.last_inverter_log_df.empty:
    sm_e.artifacts["InverterLog"] = sm_e.last_inverter_log_df

# --- Emit outputs ke folder runfast (BUKAN outputs/) ---
DAILY_OUT_DIR = globals().get(
    "DAILY_OUT_DIR", os.path.join(REPO_DIR, "outputs_daily_runfast"))
os.makedirs(DAILY_OUT_DIR, exist_ok=True)

jsonl_path = os.path.join(DAILY_OUT_DIR, f"m2_findings_{DATA_DATESTR}.jsonl")
xlsx_path  = os.path.join(DAILY_OUT_DIR, f"m2_findings_{DATA_DATESTR}.xlsx")
M2Engine.write_jsonl(findings, jsonl_path)
M2Engine.write_xlsx_multi(findings, [sm_e], xlsx_path)

inv_log_csv = os.path.join(DAILY_OUT_DIR, f"inverter_operation_{DATA_DATESTR}.csv")
if getattr(sm_e, "last_inverter_log_df", None) is not None and not sm_e.last_inverter_log_df.empty:
    sm_e.last_inverter_log_df.to_csv(inv_log_csv, index=False, encoding="utf-8")

print(f"\\n[m2e-runfast] findings JSONL : {jsonl_path} ({len(findings)} records)")
print(f"[m2e-runfast] xlsx multi-sheet: {xlsx_path}")
print(f"[m2e-runfast] inverter log    : {inv_log_csv}")

# --- Summary ---
sev_counts = Counter(f.severity.value for f in findings)
print(f"\\n[m2e-runfast] by severity: {dict(sev_counts)}")
if getattr(sm_e, "last_inverter_log_df", None) is not None and not sm_e.last_inverter_log_df.empty:
    print(f"\\n[M2e] Inverter status distribution:")
    print(sm_e.last_inverter_log_df["status"].value_counts().to_string())

print(f"\\n[m2e-runfast] DONE. data dates: {DATA_DATESTR}")
print(f"[m2e-runfast] output folder : {DAILY_OUT_DIR}")
"""

CELLS = [
    ("markdown", MD_INTRO),
    ("code", CODE_CELL1),
    ("code", CODE_CELL2),
    ("code", CODE_CELL3),
    ("code", CODE_CELL4),
]


def _cell(kind: str, source: str, idx: int) -> dict:
    lines = source.splitlines(keepends=True)
    cell = {
        "cell_type": kind,
        "id": f"runfast-{idx:02d}",
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
