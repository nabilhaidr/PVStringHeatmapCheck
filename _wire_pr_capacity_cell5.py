"""Apply 3 edits to Cell 5 (Performance Ratio): pembagi DC PR per WB.

2026-06-12 (user-provided): capacity per-WB dipisah (WB01=6750 ... WB10=7069
kWp) menggantikan asumsi seragam site/10 = 7150 kWp. Nilai dibaca dari
cfg["generation"]["capacity_kwp_per_wb"] (config/m2_config.yaml) dengan
fallback site/10 per WB bila key tidak ada (config lama tetap jalan).

Target: notebook/20260209stringmap_v1.5.ipynb. Cell dicari via marker.
Idempotent: re-running on an already-wired notebook is a no-op.
"""
import ast
import json
import sys
from pathlib import Path

CELL_MARKER = "# Cell 5 - Performance Ratio"

EDITS = [
    # (anchor_text, replacement_text, name_for_log)
    (
        "capacity_wb_kwp = capacity_site_kwp / 10.0\n",
        '_cap_per_wb_cfg = cfg.get("generation", {}).get("capacity_kwp_per_wb", {}) or {}\n'
        "capacity_wb_kwp = {wb: float(_cap_per_wb_cfg.get(wb, capacity_site_kwp / 10.0))\n"
        "                   for wb in _wb_all}\n",
        "per-WB capacity dict dari cfg",
    ),
    (
        "pr_wb = {wb: compute_pr(gen_loader.get_daily(date_index, wb),\n"
        "                        poa_wb_daily[wb], capacity_wb_kwp)\n"
        "         for wb in wb_keys}\n",
        "pr_wb = {wb: compute_pr(gen_loader.get_daily(date_index, wb),\n"
        "                        poa_wb_daily[wb], capacity_wb_kwp[wb])\n"
        "         for wb in wb_keys}\n",
        "compute_pr pakai capacity per WB",
    ),
    (
        'print(f"\\n[PR] per-WB capacity   : {capacity_wb_kwp:.0f} kWp each")\n',
        'print("\\n[PR] per-WB capacity (kWp): "\n'
        '      + ", ".join(f"{wb}={capacity_wb_kwp[wb]:.0f}" for wb in wb_keys))\n',
        "print per-WB capacity map",
    ),
]


def _find_pr_cell(nb: dict) -> int:
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

    cell_idx = _find_pr_cell(nb)
    cell = nb["cells"][cell_idx]
    src = "".join(cell.get("source", []))

    if "capacity_kwp_per_wb" in src:
        print(f"  [SKIP] {p.name}: already wired (capacity_kwp_per_wb present)")
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
    print(f"Wiring per-WB PR capacity to PR cell of {len(targets)} notebook(s):")
    for t in targets:
        wire_notebook(t)
