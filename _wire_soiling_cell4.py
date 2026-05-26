"""Apply 6 small wire-up edits to Cell 4 to add M2aSoiling detector.

Mirrors the defensive-importlib pattern used for M2aShading (Task #4b)
and M2aLowIrradiance (Task #6b).

M2aSoiling lives at ``pv_pipeline/m2a/soiling.py`` (sub-package, Task #5
SKELETON). When enabled=True dengan data < 90 days, detector emits
informative "insufficient_data" INFO finding -- safe untuk wire-up
sekarang sebelum baseline accumulates.

Idempotent: re-running on an already-wired notebook is a no-op.
"""
import json
import sys
from pathlib import Path

EDITS = [
    # (anchor_text, insertion_text, name_for_log)
    (
        '        low_irradiance_mod = _load(os.path.join(WORKTREE_PV_PIPELINE, "m2a", "low_irradiance.py"), f"{alias}.m2a.low_irradiance")\n',
        '        low_irradiance_mod = _load(os.path.join(WORKTREE_PV_PIPELINE, "m2a", "low_irradiance.py"), f"{alias}.m2a.low_irradiance")\n'
        '        soiling_mod = _load(os.path.join(WORKTREE_PV_PIPELINE, "m2a", "soiling.py"), f"{alias}.m2a.soiling")\n',
        "soiling_mod _load",
    ),
    (
        '        "M2aLowIrradiance": low_irradiance_mod.M2aLowIrradiance,\n',
        '        "M2aLowIrradiance": low_irradiance_mod.M2aLowIrradiance,\n'
        '        "M2aSoiling": soiling_mod.M2aSoiling,\n',
        "M2aSoiling dict entry",
    ),
    (
        'M2aLowIrradiance = sprint4["M2aLowIrradiance"]\n',
        'M2aLowIrradiance = sprint4["M2aLowIrradiance"]\n'
        'M2aSoiling = sprint4["M2aSoiling"]\n',
        "M2aSoiling unpack",
    ),
    (
        'print("  M2aLowIrradiance(worktree)   : PR-proxy slope di low-band (50-250 W/m^2) underperform (Fase 3 Task #6, opt-in)")\n',
        'print("  M2aLowIrradiance(worktree)   : PR-proxy slope di low-band (50-250 W/m^2) underperform (Fase 3 Task #6, opt-in)")\n'
        'print("  M2aSoiling      (worktree)   : rdtools SRR site-level soiling detector (Fase 3 Task #5 SKELETON, opt-in, gracefully insufficient_data sampai >=90 days baseline)")\n',
        "M2aSoiling print",
    ),
    (
        'sm_low_irr = M2aLowIrradiance(poa=poa_provider)\n',
        'sm_low_irr = M2aLowIrradiance(poa=poa_provider)\n'
        'sm_soiling = M2aSoiling(poa=poa_provider)\n',
        "sm_soiling instantiation",
    ),
    (
        'submodules = [sm_e, sm_peer, sm_oc, sm_gf, sm_iforest, sm_shading, sm_low_irr]\n',
        'submodules = [sm_e, sm_peer, sm_oc, sm_gf, sm_iforest, sm_shading, sm_low_irr, sm_soiling]\n',
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

    if "M2aSoiling" in src:
        print(f"  [SKIP] {p.name}: already wired (M2aSoiling present)")
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
    print(f"Wiring M2aSoiling to Cell 4 of {len(targets)} notebook(s):")
    for t in targets:
        wire_notebook(t)
