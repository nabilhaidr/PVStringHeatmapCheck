# Daily Runfast Notebook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Notebook Colab harian cepat (`notebook/daily_runfast_v1.ipynb`) berisi heatmap semua inverter + M2eAvailability saja, output ke `outputs_daily_runfast/`, tanpa baseline CSV/parquet.

**Architecture:** Builder script `notebook/_build_daily_runfast_nb.py` menulis JSON nbformat-4.5 mentah (pola `notebook/_build_physics_nb.py` — nbformat TIDAK terinstal lokal). Notebook = 1 markdown + 4 code cell: Cell 1 (config+download Drive+`DAILY_OUT_DIR`), Cell 2 (load+transform, verbatim notebook penuh), Cell 3 (loop heatmap per inverter dengan save PNG — `viz.py` tidak disentuh), Cell 4 (M2Engine hanya `M2eAvailability`). Verifikasi = round-trip JSON + smoke headless Cell 3+4 dengan `combined_df` sintetis.

**Tech Stack:** Python 3, pandas, matplotlib (Agg untuk smoke), openpyxl (via `write_xlsx_multi`). Tidak ada dependensi baru.

## Global Constraints

- Notebook di-generate builder script JSON mentah nbformat 4.5 — JANGAN pakai `nbformat` (tidak terinstal lokal). Edit builder → rerun → verifikasi, jangan edit .ipynb langsung.
- Output notebook ke `DAILY_OUT_DIR = os.path.join(REPO_DIR, "outputs_daily_runfast")` — BUKAN `outputs/` dan BUKAN `cfg["m2e"]["output_dir"]`.
- Nama file output identik dengan notebook penuh (`m2_findings_{DATA_DATESTR}.jsonl/.xlsx`, `inverter_operation_{DATA_DATESTR}.csv`) — kompatibel dashboard loader; hanya folder yang beda.
- TIDAK mengubah `pv_pipeline/` sama sekali. PNG disimpan lewat loop cell notebook (`plot_single_inv_heatmap(show=False)` → `plt.gcf().savefig(...)`).
- TIDAK ada detector POA-aware / soiling / LSTM / baseline Cell 7 / export Cell 8.
- Smoke lokal: `matplotlib.use("Agg")` sebelum import pyplot, stub `plt.show`, jalankan dengan `python -X utf8` (console Windows cp1252).
- Commit LOCAL saja (`git commit`, tanpa push). User push ke 2 remote (nabilhaidr + ompltsikn) saat diminta. Pesan commit conventional (`feat:`, `test:`).

---

### Task 1: Builder script + notebook + .gitignore

**Files:**
- Create: `notebook/_build_daily_runfast_nb.py`
- Create (generated): `notebook/daily_runfast_v1.ipynb`
- Modify: `.gitignore:42` (tambah 1 baris setelah `baseline/`)

**Interfaces:**
- Consumes: pola builder `notebook/_build_physics_nb.py` (fungsi `_cell`, `build`, dump `indent=1, ensure_ascii=False`).
- Produces: `notebook/daily_runfast_v1.ipynb` dengan `cells[3]` = heatmap loop dan `cells[4]` = M2e pipeline — indeks ini dipakai smoke script Task 2. Nama global yang di-share antar cell: `REPO_DIR`, `DAILY_OUT_DIR`, `STRINGS_YAML`, `PV_MAX_ALLOWED`, `CELL_SIZE`, `FOLDER`, `EXPECTED_FILES`, `EXCEL_HEADER_ROW`, `USECOLS`, `NROWS`, `combined_df`.

- [ ] **Step 1: Tulis builder script lengkap**

Create `notebook/_build_daily_runfast_nb.py`:

```python
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
```

PERHATIAN escaping: di dalam string CODE_CELL3/CODE_CELL4, newline literal pada `print` ditulis `\\n` (dua karakter di builder → `\n` di notebook). Jangan diganti newline asli.

- [ ] **Step 2: Jalankan builder**

Run: `python -X utf8 notebook/_build_daily_runfast_nb.py`
Expected: `Wrote ...\notebook\daily_runfast_v1.ipynb (5 cells)`

- [ ] **Step 3: Verifikasi round-trip JSON + struktur cell**

Run:
```bash
python -X utf8 -c "import json; nb=json.load(open('notebook/daily_runfast_v1.ipynb',encoding='utf-8')); kinds=[c['cell_type'] for c in nb['cells']]; assert nb['nbformat']==4 and nb['nbformat_minor']==5, 'nbformat'; assert kinds==['markdown','code','code','code','code'], kinds; assert 'M2eAvailability' in ''.join(nb['cells'][4]['source']); assert 'plot_single_inv_heatmap' in ''.join(nb['cells'][3]['source']); print('notebook OK:', kinds)"
```
Expected: `notebook OK: ['markdown', 'code', 'code', 'code', 'code']`

- [ ] **Step 4: Tambah baris .gitignore**

Di `.gitignore`, setelah baris `baseline/` (baris 42), tambahkan sehingga blok menjadi:

```
# Local outputs (regenerated per run)
outputs/
baseline/
outputs_daily_runfast/
```

- [ ] **Step 5: Commit**

```bash
git add notebook/_build_daily_runfast_nb.py notebook/daily_runfast_v1.ipynb .gitignore
git commit -m "feat(notebook): daily_runfast v1 — heatmap + M2e only, output terpisah"
```

---

### Task 2: Smoke headless Cell 3+4 (tanpa Drive)

**Files:**
- Create: `notebook/_smoke_daily_runfast.py`

**Interfaces:**
- Consumes: `notebook/daily_runfast_v1.ipynb` hasil Task 1 — `cells[3]` (heatmap loop, butuh globals `STRINGS_YAML`, `PV_MAX_ALLOWED`, `CELL_SIZE`, `DAILY_OUT_DIR`, `combined_df`) dan `cells[4]` (M2e pipeline, butuh `REPO_DIR`, `DAILY_OUT_DIR`, `combined_df`).
- Produces: skrip verifikasi rerunnable; exit 0 + print `[smoke] OK` bila PNG + jsonl + xlsx tercipta di folder temp.

- [ ] **Step 1: Tulis smoke script**

Create `notebook/_smoke_daily_runfast.py`:

```python
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

# combined_df sintetis: 2 inverter x 6 timestamp, 1 tanggal
times = pd.date_range("2026-07-01 06:00", periods=6, freq="h")
rows = []
for inv in ["WB01-INV01", "WB01-INV02"]:
    for t in times:
        rows.append({
            "Inverter_ID": inv,
            "Start Time": t,
            "Inverter status": "Grid connected",
            "PV1 Power(kW)": 1.0,
            "PV2 Power(kW)": 2.0,
        })
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
```

- [ ] **Step 2: Jalankan smoke**

Run: `python -X utf8 notebook/_smoke_daily_runfast.py`
Expected: baris terakhir `[smoke] OK`; daftar outputs memuat 2 PNG `heatmap_20260701_WB01-INV01.png`/`...INV02.png`, `m2_findings_20260701.jsonl`, `m2_findings_20260701.xlsx`.

Bila gagal: perbaiki `CODE_CELL3`/`CODE_CELL4` di BUILDER (bukan .ipynb), rerun `python -X utf8 notebook/_build_daily_runfast_nb.py`, lalu rerun smoke sampai `[smoke] OK`.

- [ ] **Step 3: Verifikasi isi xlsx (sheet M2e)**

Run:
```bash
python -X utf8 -c "import glob,tempfile,os; import openpyxl; d=sorted(glob.glob(os.path.join(tempfile.gettempdir(),'runfast_smoke_*')))[-1]; x=glob.glob(os.path.join(d,'m2_findings_*.xlsx'))[0]; wb=openpyxl.load_workbook(x,read_only=True); print(wb.sheetnames); assert 'Findings' in wb.sheetnames"
```
Expected: list sheet memuat `Findings` (+ `M2e_hybrid_AllStrings`/`M2e_hybrid_InverterLog` bila artifacts non-empty pada data sintetis).

- [ ] **Step 4: Commit**

```bash
git add notebook/_smoke_daily_runfast.py
git commit -m "test(notebook): smoke headless daily_runfast Cell 3+4 (data sintetis)"
```

---

## Self-Review Notes

- **Spec coverage:** 3 deliverable spec → Task 1 (builder + ipynb + .gitignore); struktur 1 md + 4 code → CELLS builder; Cell 1 config+Drive+DAILY_OUT_DIR ✓; Cell 2 verbatim ✓; Cell 3 loop PNG `heatmap_{datestr}_{inv}.png` dengan try/except per inverter ✓; Cell 4 M2e-only dengan bridge artifacts + 3 output + summary ✓; verifikasi builder round-trip (Task 1 Step 3) + smoke headless sintetis (Task 2) ✓; tidak menyentuh `pv_pipeline/` ✓.
- **Deviasi kecil dari spec (disengaja, sesuai teks spec):** konstanta legacy Cell 1 notebook penuh yang tidak terpakai di runfast (`N_WB01_WB02_TOTAL`, `N_WB_REST`, `SNAPSHOT_TIME`, `TOLERANCE_MINUTES`, import `dtime`) TIDAK disalin — spec hanya mensyaratkan find_repo_root + USER CONFIG Drive + download. Smoke script di-commit sebagai `notebook/_smoke_daily_runfast.py` (rerunnable; pola helper `_wire_*.py` yang sudah ada di repo).
- **Placeholder scan:** tidak ada TBD/TODO; semua step berkod penuh.
- **Type consistency:** indeks `cells[3]`/`cells[4]` konsisten Task 1 ↔ Task 2; nama globals yang di-inject smoke (`REPO_DIR`, `DAILY_OUT_DIR`, `STRINGS_YAML`, `PV_MAX_ALLOWED`, `CELL_SIZE`, `combined_df`) match yang dipakai CODE_CELL3/CODE_CELL4; `HEATMAP_DATESTR` lokal Cell 3, `DATA_DATESTR` lokal Cell 4 (deteksi independen — Cell 4 tetap jalan bila Cell 3 di-skip).
