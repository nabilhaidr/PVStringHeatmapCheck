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


def load_mppt_map(yaml_path: str) -> Dict[str, dict]:
    """Baca file YAML dan kembalikan dict ``mppt_map`` mentah (per model inverter).

    Raises
    ------
    KeyError
        Bila YAML tidak mempunyai top-level key ``mppt_map``.
    """
    _ensure_yaml()
    import yaml  # noqa: WPS433

    with open(yaml_path, "r", encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}

    if "mppt_map" not in data:
        raise KeyError(
            f"YAML file {yaml_path} must contain top-level key 'mppt_map'."
        )
    return data["mppt_map"] or {}


def get_mppt_groups(
    yaml_path: str,
    pv_max_allowed: int = 28,
) -> Dict[str, Dict[int, List[int]]]:
    """Mapping per-WB: ``{WB_id: {mppt_n: [pv_index, ...]}}`` (sanitized).

    Ekspansi dari format per-model di YAML (``mppt_map.<model>.wbs`` +
    ``mppt_map.<model>.mppt``). Sanitasi mengikuti pola empty_pv_map:
    uppercase WB id, int-ify mppt/pv index, cap pada ``pv_max_allowed``,
    dedup + sort, buang group kosong.
    """
    raw = load_mppt_map(yaml_path)
    groups: Dict[str, Dict[int, List[int]]] = {}
    for entry in raw.values():
        mppt_raw = (entry or {}).get("mppt", {}) or {}
        cleaned_mppt: Dict[int, List[int]] = {}
        for mppt_n, pv_list in mppt_raw.items():
            cleaned: List[int] = []
            for item in pv_list or []:
                try:
                    num = int(item)
                except Exception:
                    continue
                if num <= pv_max_allowed:
                    cleaned.append(num)
            cleaned = sorted(set(cleaned))
            if cleaned:
                cleaned_mppt[int(mppt_n)] = cleaned
        for wb in (entry or {}).get("wbs", []) or []:
            if cleaned_mppt:
                groups[str(wb).upper()] = cleaned_mppt
    return groups


def mppt_of_pv(wb_groups: Dict[int, List[int]], pv_n: int) -> int | None:
    """MPPT id yang memuat ``pv_n`` dalam satu WB, atau None bila tidak terdaftar."""
    for mppt_n, pv_list in (wb_groups or {}).items():
        if pv_n in pv_list:
            return mppt_n
    return None


def mppt_siblings_of_pv(wb_groups: Dict[int, List[int]], pv_n: int) -> List[int]:
    """Sibling PV indices se-MPPT dengan ``pv_n`` (exclude dirinya sendiri).

    Kembalikan [] bila ``pv_n`` tidak terdaftar di mppt_map -- caller harus
    fallback ke peer scope inverter-wide.
    """
    mppt_n = mppt_of_pv(wb_groups, pv_n)
    if mppt_n is None:
        return []
    return [n for n in wb_groups[mppt_n] if n != pv_n]
