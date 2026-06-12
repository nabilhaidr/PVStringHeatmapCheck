"""Apply 7 wire-up edits to Cell 4 (M2 Pipeline UNIFIED) to add M2bMpptRatio.

Target: notebook/20260209stringmap_v1.5.ipynb (direct-import style, BUKAN
pola _load importlib worktree). Cell dicari via marker "# Cell 4 - M2
Pipeline UNIFIED" karena notebook ini punya cell mount Colab ekstra
(cell M2 ada di JSON index 5, bukan 4).

Idempotent: re-running on an already-wired notebook is a no-op.
"""
import ast
import json
import sys
from pathlib import Path

CELL_MARKER = "# Cell 4 - M2 Pipeline UNIFIED"

EDITS = [
    # (anchor_text, replacement_text, name_for_log)
    (
        "from pv_pipeline.open_circuit import M2bOpenCircuit\n",
        "from pv_pipeline.mppt_ratio import M2bMpptRatio\n"
        "from pv_pipeline.open_circuit import M2bOpenCircuit\n",
        "M2bMpptRatio import",
    ),
    (
        'print("  M2bGroundFault   : V-to-ground triple-signal")\n',
        'print("  M2bGroundFault   : V-to-ground triple-signal")\n'
        'print("  M2bMpptRatio     : I_string vs median partner se-MPPT (mppt_map, ratio-gated, 2026-06-12)")\n',
        "M2bMpptRatio print banner",
    ),
    (
        "sm_gf = M2bGroundFault(poa=poa_provider, panel=panel_spec, cell_temp=cell_temp_provider)\n",
        "sm_gf = M2bGroundFault(poa=poa_provider, panel=panel_spec, cell_temp=cell_temp_provider)\n"
        "sm_mppt = M2bMpptRatio(poa=poa_provider)\n",
        "sm_mppt instantiation",
    ),
    (
        "submodules = [sm_e, sm_peer, sm_oc, sm_gf, sm_iforest, sm_shading, sm_low_irr, sm_soiling]\n",
        "submodules = [sm_e, sm_peer, sm_oc, sm_gf, sm_mppt, sm_iforest, sm_shading, sm_low_irr, sm_soiling]\n",
        "submodules list",
    ),
    (
        '    "M2a_soiling":        "m2a_soiling",\n',
        '    "M2a_soiling":        "m2a_soiling",\n'
        '    "M2b_mppt_ratio":     "m2b_mppt_ratio",\n',
        "EXCLUDE_CFG_MAP entry",
    ),
    (
        'for sub_name in ["M2b_peer_zscore", "M2b_open_circuit", "M2b_ground_fault"]:\n',
        'for sub_name in ["M2b_peer_zscore", "M2b_open_circuit", "M2b_ground_fault", "M2b_mppt_ratio"]:\n',
        "M2b summaries loop",
    ),
    (
        "    for sm_inst in [sm_peer, sm_oc, sm_gf]:\n",
        "    for sm_inst in [sm_peer, sm_oc, sm_gf, sm_mppt]:\n",
        "preprocessing audit loop",
    ),
]


def _find_m2_cell(nb: dict) -> int:
    for idx, cell in enumerate(nb.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        if CELL_MARKER in "".join(cell.get("source", [])):
            return idx
    raise RuntimeError(f"Cell marker {CELL_MARKER!r} tidak ditemukan")


def wire_notebook(path: str) -> bool:
    """Return True if notebook was modified."""
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    nb = json.loads(raw)
    # Pertahankan format serialisasi asli supaya git diff tetap surgical:
    # notebook ini disimpan compact ({"cells":[...] satu baris), bukan indent=1.
    compact = raw.lstrip().startswith('{"')
    trailing_newline = raw.endswith("\n")

    cell_idx = _find_m2_cell(nb)
    cell = nb["cells"][cell_idx]
    src = "".join(cell.get("source", []))

    if "M2bMpptRatio" in src:
        print(f"  [SKIP] {p.name}: already wired (M2bMpptRatio present)")
        return False

    new_src = src
    applied = 0
    for anchor, replacement, name in EDITS:
        if anchor not in new_src:
            print(f"  [WARN] {p.name}: anchor missing for '{name}'")
            print(f"         anchor = {anchor!r}")
            continue
        new_src = new_src.replace(anchor, replacement, 1)
        applied += 1
        print(f"  [OK]   {p.name}: applied '{name}'")

    if applied != len(EDITS):
        print(f"  [ABORT] {p.name}: hanya {applied}/{len(EDITS)} anchor match; notebook TIDAK ditulis")
        return False

    # Cell harus tetap valid Python sebelum ditulis balik.
    ast.parse(new_src)

    if new_src.endswith("\n"):
        new_lines = [line + "\n" for line in new_src.split("\n")[:-1]]
    else:
        parts = new_src.split("\n")
        new_lines = [line + "\n" for line in parts[:-1]] + [parts[-1]]
    cell["source"] = new_lines

    with p.open("w", encoding="utf-8") as f:
        if compact:
            json.dump(nb, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(nb, f, ensure_ascii=False, indent=1)
        if trailing_newline:
            f.write("\n")

    print(f"  [DONE] {p.name}: {applied}/{len(EDITS)} edits applied (cell index {cell_idx})")
    return True


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else [
        str(Path(__file__).parent / "notebook" / "20260209stringmap_v1.5.ipynb"),
    ]
    print(f"Wiring M2bMpptRatio to M2 pipeline cell of {len(targets)} notebook(s):")
    for t in targets:
        wire_notebook(t)
