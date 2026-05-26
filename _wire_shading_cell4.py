"""Apply 6 small wire-up edits to Cell 4 to add M2aShading detector.

Mirrors the defensive-importlib pattern used for M2b detectors (peer_zscore,
open_circuit, ground_fault) and M2IForest (Task #2b).

M2aShading lives at ``pv_pipeline/m2a/shading.py`` (sub-package), so the
_load call uses path ``os.path.join(WORKTREE_PV_PIPELINE, "m2a", "shading.py")``
and namespace ``f"{alias}.m2a.shading"``, mirroring the existing POA pattern.

Idempotent: re-running on an already-wired notebook is a no-op.
"""
import json
import sys
from pathlib import Path

EDITS = [
    # (anchor_text, insertion_text, name_for_log)
    (
        '        iforest_mod = _load(os.path.join(WORKTREE_PV_PIPELINE, "iforest.py"), f"{alias}.iforest")\n',
        '        iforest_mod = _load(os.path.join(WORKTREE_PV_PIPELINE, "iforest.py"), f"{alias}.iforest")\n'
        '        shading_mod = _load(os.path.join(WORKTREE_PV_PIPELINE, "m2a", "shading.py"), f"{alias}.m2a.shading")\n',
        "shading_mod _load",
    ),
    (
        '        "M2IForest": iforest_mod.M2IForest,\n',
        '        "M2IForest": iforest_mod.M2IForest,\n'
        '        "M2aShading": shading_mod.M2aShading,\n',
        "M2aShading dict entry",
    ),
    (
        'M2IForest = sprint4["M2IForest"]\n',
        'M2IForest = sprint4["M2IForest"]\n'
        'M2aShading = sprint4["M2aShading"]\n',
        "M2aShading unpack",
    ),
    (
        'print("  M2IForest       (worktree)   : IsolationForest unsupervised per-inverter (Fase 3 Task #2, opt-in)")\n',
        'print("  M2IForest       (worktree)   : IsolationForest unsupervised per-inverter (Fase 3 Task #2, opt-in)")\n'
        'print("  M2aShading      (worktree)   : Diurnal CV + PR-proxy whole-inverter shading (Fase 3 Task #4, opt-in)")\n',
        "M2aShading print",
    ),
    (
        'sm_iforest = M2IForest(poa=poa_provider)\n',
        'sm_iforest = M2IForest(poa=poa_provider)\n'
        'sm_shading = M2aShading(poa=poa_provider)\n',
        "sm_shading instantiation",
    ),
    (
        'submodules = [sm_e, sm_peer, sm_oc, sm_gf, sm_iforest]\n',
        'submodules = [sm_e, sm_peer, sm_oc, sm_gf, sm_iforest, sm_shading]\n',
        "submodules list",
    ),
]


def wire_notebook(path: str) -> bool:
    """Return True if notebook was modified."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        nb = json.load(f)

    cell4 = nb["cells"][4]
    src_list = cell4.get("source", [])
    src = "".join(src_list)

    if "M2aShading" in src:
        print(f"  [SKIP] {p.name}: already wired (M2aShading present)")
        return False

    new_src = src
    applied = 0
    for anchor, replacement, name in EDITS:
        if anchor not in new_src:
            print(f"  [WARN] {p.name}: anchor missing for '{name}'")
            print(f"         anchor = {anchor!r}")
            continue
        if replacement in new_src:
            continue
        new_src = new_src.replace(anchor, replacement, 1)
        applied += 1
        print(f"  [OK]   {p.name}: applied '{name}'")

    if applied == 0:
        print(f"  [SKIP] {p.name}: no edits applied")
        return False

    # Re-split into nbformat \n-terminated source list.
    if new_src.endswith("\n"):
        new_lines = [line + "\n" for line in new_src.split("\n")[:-1]]
    else:
        parts = new_src.split("\n")
        new_lines = [line + "\n" for line in parts[:-1]] + [parts[-1]]
    cell4["source"] = new_lines

    with p.open("w", encoding="utf-8") as f:
        json.dump(nb, f, ensure_ascii=False, indent=1)
        f.write("\n")

    print(f"  [DONE] {p.name}: {applied}/{len(EDITS)} edits applied")
    return True


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else [
        r"C:\Users\nabil\Downloads\SolarYieldPro-main\kodingan pv string\.claude\worktrees\modest-shockley-9c31f4\20260517stringmap_v1.5.ipynb",
        r"C:\Users\nabil\Downloads\SolarYieldPro-main\kodingan pv string\.claude\worktrees\modest-shockley-9c31f4\20260514stringmap_v1.5.ipynb",
    ]
    print(f"Wiring M2aShading to Cell 4 of {len(targets)} notebook(s):")
    for t in targets:
        wire_notebook(t)
