"""Update Cell 7 print summary untuk surface new per-PV NaN fields.

After 2026-05-23 change (baseline.py skip_scope="pv_string" default), the
FilterSummary gains `rows_pv_nanned` + `pv_strings_skipped_findings`.
This script adds 2 print lines to Cell 7's summary block so user sees
the new per-PV skip counts.

Idempotent: re-running on already-updated notebook is a no-op.
"""
import json
import sys
from pathlib import Path

EDITS = [
    # After the existing skipped_minrows print, insert pv_nanned print.
    (
        '    print(f"    skipped_minrows : {s.rows_skipped_min_rows} "\n'
        '          f"({len(s.inverters_skipped_min_rows)} sparse inverters)")\n',
        '    print(f"    skipped_minrows : {s.rows_skipped_min_rows} "\n'
        '          f"({len(s.inverters_skipped_min_rows)} sparse inverters)")\n'
        '    print(f"    pv_nanned       : {s.rows_pv_nanned} rows touched "\n'
        '          f"({len(s.pv_strings_skipped_findings)} PV strings NaN\'d)")\n',
        "pv_nanned summary line",
    ),
    # Add a list dump for the per-PV skip pairs, right after the existing
    # auto-skip list dump.
    (
        '    if s.inverters_skipped_findings:\n'
        '        print(f"    auto-skip list  : {\', \'.join(s.inverters_skipped_findings)}")\n',
        '    if s.inverters_skipped_findings:\n'
        '        print(f"    auto-skip list  : {\', \'.join(s.inverters_skipped_findings)}")\n'
        '    if s.pv_strings_skipped_findings:\n'
        '        print(f"    pv-skip list    : {\', \'.join(s.pv_strings_skipped_findings)}")\n',
        "pv-skip list dump",
    ),
]


def update_notebook(path: str) -> bool:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        nb = json.load(f)

    cell7 = nb["cells"][7]
    src_list = cell7.get("source", [])
    src = "".join(src_list)

    if "pv_nanned" in src and "pv-skip list" in src:
        print(f"  [SKIP] {p.name}: already updated (pv_nanned + pv-skip list present)")
        return False

    new_src = src
    applied = 0
    for anchor, replacement, name in EDITS:
        if anchor not in new_src:
            print(f"  [WARN] {p.name}: anchor missing for '{name}'")
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
    cell7["source"] = new_lines

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
    print(f"Updating Cell 7 PV-skip summary di {len(targets)} notebook(s):")
    for t in targets:
        update_notebook(t)
