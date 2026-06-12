from __future__ import annotations

from pathlib import Path

import pytest

from pv_pipeline.string_config import (
    get_mppt_groups,
    mppt_of_pv,
    mppt_siblings_of_pv,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_yaml(tmp_path, text: str) -> str:
    path = tmp_path / "strings.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_get_mppt_groups_expands_models_to_wbs_and_sanitizes(tmp_path):
    yaml_path = _write_yaml(tmp_path, """
mppt_map:
  MODEL-A:
    wbs: [wb01, WB02]
    mppt:
      1: [2, 1, 1]
      2: ["3", 4, 99]
""")

    groups = get_mppt_groups(yaml_path, pv_max_allowed=28)

    assert set(groups) == {"WB01", "WB02"}
    assert groups["WB01"][1] == [1, 2]
    assert groups["WB01"][2] == [3, 4]
    assert groups["WB01"] == groups["WB02"]


def test_get_mppt_groups_requires_top_level_key(tmp_path):
    yaml_path = _write_yaml(tmp_path, "empty_pv_map: {}\n")

    with pytest.raises(KeyError):
        get_mppt_groups(yaml_path)


def test_real_config_matches_confirmed_mppt_hardware():
    # Lock-in mapping user-confirmed 2026-06-11: grouping sibling harus
    # mengikuti MPPT fisik inverter, bukan asumsi.
    groups = get_mppt_groups(str(_REPO_ROOT / "config" / "strings.yaml"), pv_max_allowed=28)

    # WB01-02 (SUN2000-215KTL-H0): 9 MPPT x 2 string, PV1..PV18.
    for wb in ["WB01", "WB02"]:
        assert len(groups[wb]) == 9
        assert all(len(pvs) == 2 for pvs in groups[wb].values())
        assert sorted(n for pvs in groups[wb].values() for n in pvs) == list(range(1, 19))

    # WB03-10 (SUN2000-330KTL-H1): 6 MPPT campuran 4-5 string, PV1..PV28.
    for wb in [f"WB{i:02d}" for i in range(3, 11)]:
        assert len(groups[wb]) == 6
        assert groups[wb][1] == [1, 2, 3, 4]
        assert groups[wb][2] == [5, 6, 7, 8, 9]
        assert groups[wb][6] == [24, 25, 26, 27, 28]
        assert sorted(n for pvs in groups[wb].values() for n in pvs) == list(range(1, 29))


def test_mppt_lookup_excludes_self_and_handles_unknown_pv():
    wb_groups = {1: [1, 2], 2: [3, 4, 5]}

    assert mppt_of_pv(wb_groups, 4) == 2
    assert mppt_siblings_of_pv(wb_groups, 4) == [3, 5]
    # PV di luar mapping -> None/[] supaya caller fallback ke inverter-wide.
    assert mppt_of_pv(wb_groups, 28) is None
    assert mppt_siblings_of_pv(wb_groups, 28) == []
    assert mppt_siblings_of_pv({}, 1) == []
