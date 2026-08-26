"""Generate notebook/m2f_loss_attribution.ipynb (full chain: detektor m2b +
soiling + availability -> collect_m2f_inputs -> M2fLossAttribution -> workbook
+ figures).

Pakai: python notebook/_build_m2f_nb.py
nbformat tidak terinstall di environment ini, jadi notebook ditulis sebagai
JSON mentah (nbformat 4.5) -- sama seperti _build_daily_runfast_nb.py, yang
strukturnya diikuti persis oleh builder ini (Cell 1+2 identik).
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).parent / "m2f_loss_attribution.ipynb"

MD_INTRO = """\
# M2f Loss Attribution -- Full Chain (v1)

Notebook penuh: 3 detektor m2b (PeerZScore / OpenCircuit / MpptRatio) +
M2aSoiling + M2eAvailability -> `collect_m2f_inputs` -> `M2fLossAttribution`
-> workbook multi-sheet + 2 figure (waterfall, Pareto) sebagai PNG.

Isi:
1. **Cell 1** -- Config + download folder Drive + siapkan `outputs_m2f/`.
2. **Cell 2** -- Load Excel + transform -> `combined_df` (identik Cell 2
   `daily_runfast_v1.ipynb`).
3. **Cell 3** -- Load `config/m2_config.yaml`, aktifkan `m2f` + `m2a_soiling`
   (keduanya default OFF/opt-in di yaml), deteksi rentang tanggal data.
4. **Cell 4** -- Jalankan 3 detektor m2b + M2aSoiling + M2eAvailability,
   masing-masing dibungkus `try/except` supaya satu detektor gagal tidak
   menjatuhkan yang lain.
5. **Cell 5** -- `collect_m2f_inputs(submodules, cfg)` menjembatani
   `deficit_frames`/`p_loss_by_month` ke `M2fLossAttribution`, lalu
   `M2fLossAttribution().run(...)`.
6. **Cell 6** -- Tulis workbook (`M2Engine.write_xlsx_multi`) + simpan figure
   waterfall & Pareto (`fig.savefig(..., dpi=150)` -- menyimpan adalah
   tanggung jawab notebook, `pv_pipeline.m2f.plots` hanya mengembalikan
   `Figure`).

Beda dari `daily_runfast_v1.ipynb` (M2e saja, sengaja tanpa POA/Tcell):
notebook ini BUTUH POA, panel spec, dan konfigurasi Tcell, karena baseline
`E_expected` M2f bergantung pada ketiganya.

## PENTING -- kondisi data hari ini

`raw data input/` di working tree TIDAK punya berkas pyranometer POA maupun
berkas suhu modul (`PV Module Temperature PLTS IKN.xlsx`). Akibatnya:

- `M2fLossAttribution._load_providers` gagal saat `CellTempProvider` meng-
  konstruksi dirinya (raise `FileNotFoundError` sebelum sempat mengecek
  cakupan POA/Tcell manapun), jadi SETIAP string-hari tercatat
  `skipped_reason="provider_unavailable"` di sheet `M2f_Closure` -- BUKAN
  `poa_or_tcell_missing` (yang baru muncul kalau berkasnya ADA tapi
  cakupannya di bawah ambang `poa_coverage_min_pct`). Workbook tetap
  ter-generate lengkap dengan skema yang benar; isinya nol. Itu memang hasil
  yang jujur untuk ditampilkan mengingat data yang tersedia -- bukan bug di
  wiring ini.
- `M2bPeerZScore` -- beda dari `M2bOpenCircuit`/`M2bMpptRatio` (yang hanya
  butuh POA, dan gagal-lunak) serta `M2aSoiling` (yang membungkus
  `CellTempProvider`-nya sendiri dengan `try/except`) -- memuat
  `CellTempProvider` TANPA `try/except` di `_ensure_providers()`. `run()`-nya
  akan RAISE `FileNotFoundError` yang sama persis. Cell 4 di bawah
  membungkus SETIAP detektor dalam `try/except` supaya kegagalan itu
  terlihat (dicetak apa adanya) tapi tidak menjatuhkan detektor lain di
  daftar. `pv_pipeline/peer_zscore.py` sengaja TIDAK diubah -- itu di luar
  cakupan wiring ini.
- Begitu berkas POA dan `PV Module Temperature PLTS IKN.xlsx` tersedia,
  jalankan ulang notebook ini tanpa perubahan apa pun -- workbook akan mulai
  terisi angka nyata.

Notebook ini di-generate `notebook/_build_m2f_nb.py` -- edit builder, bukan
.ipynb langsung.
"""

CODE_CELL1 = """\
# Cell 1 -- Config + download Google Drive folder + folder output m2f
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
# =====================================

M2F_OUT_DIR = os.path.join(REPO_DIR, "outputs_m2f")
os.makedirs(M2F_OUT_DIR, exist_ok=True)
print("M2F_OUT_DIR:", M2F_OUT_DIR)

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
# Cell 2 -- Load Excel + transform (identik Cell 2 daily_runfast_v1.ipynb)
from pv_pipeline.data_loader import load_and_prepare_data
from pv_pipeline.transformations import add_inverter_id, add_pv_power_columns, add_total_pv_power

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
"""

CODE_CELL3 = """\
# Cell 3 -- Load m2_config.yaml, aktifkan m2f + m2a_soiling, deteksi tanggal
import os
import pandas as pd

from pv_pipeline.m2_config import load_m2_config

REPO_DIR = globals().get("REPO_DIR", os.getcwd())

def _resolve(cfg_filename: str) -> str:
    for base in [os.path.join(REPO_DIR, "config"), "config"]:
        p = os.path.join(base, cfg_filename)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"[m2f-nb] {cfg_filename!r} not found.")

M2_CFG_PATH = _resolve("m2_config.yaml")
print(f"[m2f-nb] m2_config: {M2_CFG_PATH}")
cfg = load_m2_config(M2_CFG_PATH)

# m2f dan m2a_soiling default OFF (opt-in) di config/m2_config.yaml --
# notebook ini yang mewakili "user opt-in" untuk keduanya.
cfg["m2f"]["enabled"] = True
cfg.setdefault("m2a_soiling", {})["enabled"] = True

# --- Pre-check combined_df ---
if "combined_df" not in globals():
    raise RuntimeError("[m2f-nb] combined_df missing. Run Cell 1 + Cell 2 dulu.")
required = ["Inverter_ID", "Start Time", "Inverter status"]
missing = [c for c in required if c not in combined_df.columns]
if missing:
    raise RuntimeError(f"[m2f-nb] missing columns: {missing}")

# --- Auto-detect tanggal ---
st = pd.to_datetime(combined_df["Start Time"], errors="coerce").dropna()
st_dates = sorted(st.dt.date.unique())
if len(st_dates) == 1:
    DATA_DATESTR = st_dates[0].strftime("%Y%m%d")
else:
    DATA_DATESTR = f"{st_dates[0].strftime('%Y%m%d')}-{st_dates[-1].strftime('%Y%m%d')}"
print(f"[m2f-nb] data dates = {DATA_DATESTR}  ({len(st_dates)} day(s))")
print(f"[m2f-nb] combined_df shape = {combined_df.shape}")
"""

CODE_CELL4 = """\
# Cell 4 -- Jalankan 3 detektor m2b + M2aSoiling + M2eAvailability (resilient)
#
# M2bPeerZScore memuat CellTempProvider TANPA try/except di dalam dirinya
# sendiri (lihat markdown Cell 0) -- tanpa berkas
# "PV Module Temperature PLTS IKN.xlsx", run()-nya RAISE FileNotFoundError.
# Loop di bawah membungkus SETIAP detektor dalam try/except supaya satu
# detektor gagal tidak menjatuhkan yang lain -- kegagalannya tetap dicetak
# apa adanya, bukan ditelan diam-diam.
from pv_pipeline.availability import M2eAvailability
from pv_pipeline.m2a.soiling import M2aSoiling
from pv_pipeline.mppt_ratio import M2bMpptRatio
from pv_pipeline.open_circuit import M2bOpenCircuit
from pv_pipeline.peer_zscore import M2bPeerZScore

submodules = [
    M2bPeerZScore(), M2bOpenCircuit(), M2bMpptRatio(),
    M2aSoiling(), M2eAvailability(),
]
all_findings = []
for sm in submodules:
    try:
        findings = sm.run(combined_df, cfg)
        all_findings.extend(findings)
        print(f"[m2f-nb] {sm.name}: {len(findings)} finding(s)")
    except Exception as err:
        print(f"[m2f-nb] {sm.name}: RAISED {type(err).__name__}: {err}")

# Bridge artefak legacy M2eAvailability (bukan channel self.artifacts) --
# sama seperti Cell 4 daily_runfast_v1.ipynb.
sm_avail = submodules[-1]
if (getattr(sm_avail, "last_all_strings_df", None) is not None
        and not sm_avail.last_all_strings_df.empty):
    sm_avail.artifacts["AllStrings"] = sm_avail.last_all_strings_df
if (getattr(sm_avail, "last_inverter_log_df", None) is not None
        and not sm_avail.last_inverter_log_df.empty):
    sm_avail.artifacts["InverterLog"] = sm_avail.last_inverter_log_df
"""

CODE_CELL5 = """\
# Cell 5 -- Jembatani hasil detektor ke M2f lalu jalankan M2fLossAttribution
from pv_pipeline.m2f.collect import collect_m2f_inputs
from pv_pipeline.m2f.report import M2fLossAttribution

collect_m2f_inputs(submodules, cfg)
_deficit_frames = cfg["m2f"]["deficit_frames"]
print(f"[m2f-nb] deficit_frames terkumpul: {len(_deficit_frames) if _deficit_frames else 0}")
print(f"[m2f-nb] p_loss_by_month terkumpul: {cfg['m2f']['p_loss_by_month']}")

sm_m2f = M2fLossAttribution()
m2f_findings = sm_m2f.run(combined_df, cfg)
all_findings.extend(m2f_findings)
submodules.append(sm_m2f)
print(f"[m2f-nb] M2fLossAttribution: {len(m2f_findings)} finding(s)")

closure = sm_m2f.artifacts.get("M2f_Closure")
if closure is not None and not closure.empty:
    print("[m2f-nb] M2f_Closure skipped_reason:")
    print(closure["skipped_reason"].value_counts(dropna=False).to_string())
else:
    print("[m2f-nb] M2f_Closure kosong (tidak ada string-hari yang diproses).")
"""

CODE_CELL6 = """\
# Cell 6 -- Tulis workbook multi-sheet + simpan figure waterfall & Pareto
# (savefig adalah tanggung jawab notebook -- pv_pipeline.m2f.plots hanya
# mengembalikan Figure, lihat docstring plots.py.)
import os
import matplotlib.pyplot as plt

from pv_pipeline.core import M2Engine
from pv_pipeline.m2f.plots import build_loss_waterfall_figure, build_pareto_figure

M2F_OUT_DIR = globals().get("M2F_OUT_DIR", os.path.join(REPO_DIR, "outputs_m2f"))
os.makedirs(M2F_OUT_DIR, exist_ok=True)

xlsx_path = os.path.join(M2F_OUT_DIR, f"m2f_loss_attribution_{DATA_DATESTR}.xlsx")
M2Engine.write_xlsx_multi(all_findings, submodules, xlsx_path)
print(f"[m2f-nb] workbook: {xlsx_path}")

waterfall_df = sm_m2f.artifacts.get("M2f_Waterfall")
pareto_df    = sm_m2f.artifacts.get("M2f_Pareto")

fig_waterfall = build_loss_waterfall_figure(
    waterfall_df, scope="site", period_label=DATA_DATESTR,
)
waterfall_png = os.path.join(M2F_OUT_DIR, f"m2f_waterfall_{DATA_DATESTR}.png")
fig_waterfall.savefig(waterfall_png, dpi=150)
plt.close(fig_waterfall)

fig_pareto = build_pareto_figure(
    pareto_df, scope="site", period_label=DATA_DATESTR,
)
pareto_png = os.path.join(M2F_OUT_DIR, f"m2f_pareto_{DATA_DATESTR}.png")
fig_pareto.savefig(pareto_png, dpi=150)
plt.close(fig_pareto)

print(f"[m2f-nb] waterfall PNG: {waterfall_png}")
print(f"[m2f-nb] pareto   PNG: {pareto_png}")
print(f"\\n[m2f-nb] DONE. data dates: {DATA_DATESTR}")
print(f"[m2f-nb] output folder : {M2F_OUT_DIR}")
"""

CELLS = [
    ("markdown", MD_INTRO),
    ("code", CODE_CELL1),
    ("code", CODE_CELL2),
    ("code", CODE_CELL3),
    ("code", CODE_CELL4),
    ("code", CODE_CELL5),
    ("code", CODE_CELL6),
]


def _cell(kind: str, source: str, idx: int) -> dict:
    lines = source.splitlines(keepends=True)
    cell = {
        "cell_type": kind,
        "id": f"m2f-{idx:02d}",
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
