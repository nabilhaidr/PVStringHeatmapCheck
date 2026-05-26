"""Apply per-detector exclude_from_findings_sheet filter ke Cell 4.

Filter dijalankan SETELAH ``findings = engine.run_all(combined_df, cfg)`` dan
SEBELUM ``M2Engine.write_xlsx_multi(findings, ...)`` + sebelum Cell 7 baca
``findings`` global. Hasil:
    - Findings sheet utama xlsx: hanya berisi findings dari detector tanpa
      exclude flag (M2eAvailability, M2b detectors, dst).
    - Cell 7 auto-skip: hanya pakai findings non-excluded -> M2_iforest
      noisy findings tidak ngebuang baseline rows.
    - Artifact sheets per detector (mis. M2_iforest_AnomalyScores,
      M2_iforest_AnomalySummary): TETAP di-emit (sm.artifacts tidak
      dipengaruhi filter ini).

Default detectors yang exclude_from_findings_sheet=True per DEFAULT_M2_CONFIG:
    - m2_iforest

User dapat override via config yaml untuk detector lain.

Idempotent: re-running on already-patched notebook is a no-op.
"""
import json
import sys
from pathlib import Path

ANCHOR = 'findings = engine.run_all(combined_df, cfg)\n'

INSERT_AFTER = '''\
findings = engine.run_all(combined_df, cfg)

# --- Per-detector exclude_from_findings_sheet filter (2026-05-23) -----------
# Findings dari detector dengan exclude_from_findings_sheet=True dibuang dari
# Findings sheet utama + Cell 7 auto-skip. Artifact sheets per-detector TETAP
# di-emit di xlsx (sm.artifacts tidak dipengaruhi).
EXCLUDE_CFG_MAP = {
    "M2_iforest":         "m2_iforest",
    "M2a_shading":        "m2a_shading",
    "M2a_low_irradiance": "m2a_low_irradiance",
    "M2a_soiling":        "m2a_soiling",
}
findings_all = list(findings)  # preserve full list untuk diagnostic
excluded_counts = {}
for _sm_name, _cfg_key in EXCLUDE_CFG_MAP.items():
    _det_cfg = cfg.get(_cfg_key, {}) or {}
    if not _det_cfg.get("exclude_from_findings_sheet", False):
        continue
    _n_before = sum(1 for _f in findings if _f.sub_module == _sm_name)
    if _n_before == 0:
        continue
    findings = [_f for _f in findings if _f.sub_module != _sm_name]
    excluded_counts[_sm_name] = _n_before
if excluded_counts:
    print(f"\\n[m2-pipeline] excluded from Findings sheet + Cell 7 auto-skip:")
    for _sm, _n in sorted(excluded_counts.items()):
        print(f"  {_sm:25s} : {_n:5d} findings hidden (artifacts tetap di per-detector sheets)")
print(f"[m2-pipeline] findings: total={len(findings_all)} -> shown_in_Findings_sheet={len(findings)}")
# --- end exclude filter ----------------------------------------------------
'''


def patch_notebook(path: str) -> bool:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        nb = json.load(f)

    cell4 = nb["cells"][4]
    src = "".join(cell4.get("source", []))

    if "excluded_counts" in src and "EXCLUDE_CFG_MAP" in src:
        print(f"  [SKIP] {p.name}: already patched (excluded_counts + EXCLUDE_CFG_MAP present)")
        return False
    if ANCHOR not in src:
        print(f"  [WARN] {p.name}: anchor not found: {ANCHOR!r}")
        return False
    if src.count(ANCHOR) != 1:
        print(f"  [WARN] {p.name}: anchor appears {src.count(ANCHOR)} times; aborting")
        return False

    new_src = src.replace(ANCHOR, INSERT_AFTER, 1)

    if new_src.endswith("\n"):
        new_lines = [line + "\n" for line in new_src.split("\n")[:-1]]
    else:
        parts = new_src.split("\n")
        new_lines = [line + "\n" for line in parts[:-1]] + [parts[-1]]
    cell4["source"] = new_lines

    with p.open("w", encoding="utf-8") as f:
        json.dump(nb, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"  [DONE] {p.name}: patched")
    return True


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else [
        r"C:\Users\nabil\Downloads\SolarYieldPro-main\kodingan pv string\.claude\worktrees\modest-shockley-9c31f4\20260517stringmap_v1.5.ipynb",
        r"C:\Users\nabil\Downloads\SolarYieldPro-main\kodingan pv string\.claude\worktrees\modest-shockley-9c31f4\20260514stringmap_v1.5.ipynb",
    ]
    print(f"Patching Cell 4 with exclude_from_findings_sheet filter di {len(targets)} notebook(s):")
    for t in targets:
        patch_notebook(t)
