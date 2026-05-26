"""String configuration loader.

Membaca daftar PV string yang dianggap *empty* (tidak terpasang modul) dari
file YAML eksternal, lalu melakukan sanitasi (cap pada ``pv_max_allowed``,
pastikan integer, dedup, sort).

Format YAML yang diharapkan
---------------------------
.. code-block:: yaml

    empty_pv_map:
      WB01-INV01: [19, 20, 21, 22, 23, 24, 25, 26, 27, 28]
      WB03-INV05: [1, 5, 6, 14, 17, 19, 24]
      ...
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Dict, List


def _ensure_yaml() -> None:
    """Pastikan modul ``yaml`` (PyYAML) tersedia."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("Installing missing package: PyYAML")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "PyYAML"])


def load_empty_pv_map(yaml_path: str) -> Dict[str, List[int]]:
    """Baca file YAML dan kembalikan dict ``empty_pv_map`` mentah.

    Raises
    ------
    KeyError
        Bila YAML tidak mempunyai top-level key ``empty_pv_map``.
    """
    _ensure_yaml()
    import yaml  # noqa: WPS433

    with open(yaml_path, "r", encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}

    if "empty_pv_map" not in data:
        raise KeyError(
            f"YAML file {yaml_path} must contain top-level key 'empty_pv_map'."
        )
    return data["empty_pv_map"] or {}


def sanitize_empty_pv_map(
    empty_pv_map: Dict[str, List[int]],
    pv_max_allowed: int = 28,
) -> Dict[str, List[int]]:
    """Bersihkan dict EMPTY_PV_MAP supaya aman dipakai untuk plotting.

    Operasi:
    - Uppercase semua key (``wb01-inv01`` -> ``WB01-INV01``).
    - Convert nilai ke int (parse digit dari string bila perlu).
    - Buang nilai > ``pv_max_allowed``.
    - Dedup + sort ascending.
    - Buang entry kosong.
    """
    cleaned_map: Dict[str, List[int]] = {}
    for key, values in empty_pv_map.items():
        key_up = str(key).upper()
        cleaned: List[int] = []
        for item in values or []:
            try:
                num = int(item)
            except Exception:
                m = re.search(r"(\d+)", str(item))
                if not m:
                    continue
                num = int(m.group(1))
            if num <= pv_max_allowed:
                cleaned.append(num)
        cleaned = sorted(set(cleaned))
        if cleaned:
            cleaned_map[key_up] = cleaned
    return cleaned_map


def get_empty_pv_map(yaml_path: str, pv_max_allowed: int = 28) -> Dict[str, List[int]]:
    """Convenience helper: load + sanitize dalam satu panggilan."""
    raw = load_empty_pv_map(yaml_path)
    return sanitize_empty_pv_map(raw, pv_max_allowed=pv_max_allowed)
