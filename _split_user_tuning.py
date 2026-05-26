"""Temporarily split user's m2b_open_circuit tuning out of Task #4 commit.

Usage:
    python _split_user_tuning.py revert    # Revert user-tuning to HEAD values
                                           # (so working tree has only Task #4 diffs)
    python _split_user_tuning.py reapply   # Re-apply user-tuning lines
                                           # (restore unstaged state for separate commit)

Idempotent: asserts the expected marker is present before replacing.
"""
import sys
from pathlib import Path

# (file, current_value, head_value) -- "current" = post-user-tuning, "head" = original
REVERTS = [
    (
        "config/m2_config.yaml",
        "poa_threshold_wm2:           500.0  # per spec",
        "poa_threshold_wm2:           200.0  # per spec",
    ),
    (
        "config/m2_config.yaml",
        "debounce_consecutive_steps:  20      # >=N langkah",
        "debounce_consecutive_steps:  2      # >=N langkah",
    ),
    (
        "pv_pipeline/m2_config.py",
        '"poa_threshold_wm2": 500.0,',
        '"poa_threshold_wm2": 1000.0,',
    ),
    (
        "tests/unit/test_m2_config.py",
        'assert cfg["poa_threshold_wm2"] == 500.0',
        'assert cfg["poa_threshold_wm2"] == 1000.0',
    ),
]


def apply(direction: str) -> None:
    """direction: 'revert' = current -> head ; 'reapply' = head -> current."""
    for fp, current, head in REVERTS:
        path = Path(fp)
        if not path.exists():
            print(f"  [SKIP] {fp}: not found")
            continue
        text = path.read_text(encoding="utf-8")
        if direction == "revert":
            src, dst, label = current, head, "user-tuning -> HEAD"
        elif direction == "reapply":
            src, dst, label = head, current, "HEAD -> user-tuning"
        else:
            raise ValueError(f"unknown direction: {direction!r}")
        if src not in text:
            print(f"  [SKIP] {fp}: marker '{src[:50]}...' not found (already {direction}d?)")
            continue
        if text.count(src) != 1:
            print(f"  [WARN] {fp}: marker appears {text.count(src)} times; replacing first only")
        new_text = text.replace(src, dst, 1)
        path.write_text(new_text, encoding="utf-8")
        print(f"  [OK] {fp}: {label}")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("revert", "reapply"):
        print(__doc__)
        sys.exit(1)
    apply(sys.argv[1])
