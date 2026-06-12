"""Test pv_pipeline.mppt_ratio: M2bMpptRatio (sibling se-MPPT, current ratio).

Desain user-approved 2026-06-12: peer scope grup MPPT (mppt_map), sinyal
ratio = I_string / median(I_partner se-MPPT), debounce 20 langkah, severity
tier 0.20/0.50/0.85. Detector ini komplemen detector inverter-wide.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.core import Severity
from pv_pipeline.mppt_ratio import M2bMpptRatio


def _write_strings_yaml(tmp_path, empty_map_yaml: str = "empty_pv_map: {}\n", with_mppt: bool = True) -> str:
    text = empty_map_yaml
    if with_mppt:
        text += (
            "mppt_map:\n"
            "  TEST-MODEL:\n"
            "    wbs: [WB05]\n"
            "    mppt:\n"
            "      1: [1, 2]\n"
            "      2: [3, 4, 5]\n"
        )
    path = tmp_path / "strings.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _df_day(scale_by_pv: dict, inverter: str = "WB05-INV01") -> pd.DataFrame:
    """Synthetic 1 hari 06:00-20:00 @5min; arus = 13 A * sun^2 * scale per PV."""
    rng = np.random.default_rng(11)
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 20:00", freq="5min")
    rows = []
    for ts in t:
        hour = ts.hour + ts.minute / 60.0
        sun = np.sin(np.pi * (hour - 6.0) / 12.0) ** 2 if 6.0 <= hour <= 18.0 else 0.0
        i_base = 13.0 * sun
        row = {"Inverter_ID": inverter, "Start Time": ts}
        for pv_n, scale in scale_by_pv.items():
            row[f"PV{pv_n} input current(A)"] = i_base * scale + rng.normal(0, 0.02)
        rows.append(row)
    return pd.DataFrame(rows)


class _MockPOA:
    def get_poa(self, timestamps, wb_id, source="auto"):
        idx = pd.DatetimeIndex(timestamps)
        out = []
        for ts in idx:
            hour = ts.hour + ts.minute / 60.0
            sun = np.sin(np.pi * (hour - 6.0) / 12.0) ** 2 if 6.0 <= hour <= 18.0 else 0.0
            out.append(1000.0 * sun)
        return pd.Series(out, index=idx)


def _cfg(strings_yaml: str, **overrides) -> dict:
    det = {
        "poa_threshold_wm2": 300.0,
        "poa_floor_wm2": 50.0,
        "hour_cutoff_end": 18.0,
        "respect_inverter_shutdown": False,
        "filter_mode": "hour_cutoff",
        "ratio_threshold": 0.85,
        "ratio_high": 0.50,
        "ratio_critical": 0.20,
        "debounce_consecutive_steps": 20,
        "pv_max": 5,
        "min_partner_strings": 1,
        "min_daylight_samples": 5,
        "mppt_map_path": strings_yaml,
    }
    det.update(overrides)
    return {
        "m2b_mppt_ratio": det,
        "m2e": {"empty_pv_map_path": strings_yaml},
        "poa": {"emit_all_sources": False, "default_source": "auto"},
        "preprocessing": {"enabled": False},
    }


def test_degraded_strings_flagged_with_severity_tiers(tmp_path):
    # PV2 45% dari partner se-MPPT (grup 2-string) -> HIGH (<0.50).
    # PV4 10% dari median partner {PV3, PV5} -> CRITICAL (<0.20).
    yaml_path = _write_strings_yaml(tmp_path)
    df = _df_day({1: 1.0, 2: 0.45, 3: 1.0, 4: 0.10, 5: 1.0})
    sm = M2bMpptRatio(poa=_MockPOA())

    findings = sm.run(df, _cfg(yaml_path))

    by_pv = {f.pv_string: f for f in findings}
    assert set(by_pv) == {"PV2", "PV4"}
    assert by_pv["PV2"].severity == Severity.HIGH
    assert by_pv["PV4"].severity == Severity.CRITICAL
    assert by_pv["PV2"].fault_type == "mppt_partner_underperform"
    assert by_pv["PV2"].evidence["mppt"] == 1
    assert by_pv["PV2"].evidence["partner_strings"] == ["PV1"]
    assert by_pv["PV4"].evidence["mppt"] == 2
    assert by_pv["PV4"].evidence["partner_strings"] == ["PV3", "PV5"]
    # Confidence monotonic terhadap depth: PV4 (ratio ~0.10) > PV2 (~0.45).
    assert by_pv["PV4"].confidence > by_pv["PV2"].confidence
    assert by_pv["PV4"].confidence == pytest.approx(90.0, abs=0.1)


def test_healthy_partner_of_degraded_string_not_flagged(tmp_path):
    # Partner sehat punya ratio > 1 terhadap string degraded -> tidak boleh
    # ikut ter-flag (tidak ada mutual false positive dalam grup 2-string).
    yaml_path = _write_strings_yaml(tmp_path)
    df = _df_day({1: 1.0, 2: 0.45, 3: 1.0, 4: 1.0, 5: 1.0})
    sm = M2bMpptRatio(poa=_MockPOA())

    findings = sm.run(df, _cfg(yaml_path))

    assert [f.pv_string for f in findings] == ["PV2"]


def test_short_dip_filtered_by_debounce(tmp_path):
    # Dip 10 langkah (50 menit) < debounce 20 -> bukan genuine event.
    yaml_path = _write_strings_yaml(tmp_path)
    df = _df_day({1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0})
    dip = (df["Start Time"] >= "2026-05-14 12:00") & (df["Start Time"] < "2026-05-14 12:50")
    df.loc[dip, "PV2 input current(A)"] *= 0.4
    sm = M2bMpptRatio(poa=_MockPOA())

    findings = sm.run(df, _cfg(yaml_path))

    assert findings == []


def test_empty_slot_excluded_from_analysis_and_partner_pool(tmp_path):
    # PV4 empty slot (Huawei report 0): tidak dianalisis (bukan false CRITICAL)
    # dan tidak menyeret median partner PV3/PV5 ke bawah.
    yaml_path = _write_strings_yaml(
        tmp_path,
        empty_map_yaml="empty_pv_map:\n  WB05-INV01: [4]\n",
    )
    df = _df_day({1: 1.0, 2: 1.0, 3: 1.0, 5: 1.0})
    df["PV4 input current(A)"] = 0.0
    sm = M2bMpptRatio(poa=_MockPOA())

    findings = sm.run(df, _cfg(yaml_path))

    assert findings == []
    status = sm.artifacts["StringStatus"]
    pv4 = status[status["pv_string"] == "PV4"]
    assert (pv4["status"] == "EMPTY").all()


def test_inverter_without_mppt_map_skipped(tmp_path):
    # WB99 tidak punya mapping -> skip (tanpa fallback inverter-wide).
    yaml_path = _write_strings_yaml(tmp_path)
    df = _df_day({1: 1.0, 2: 0.45}, inverter="WB99-INV01")
    sm = M2bMpptRatio(poa=_MockPOA())

    findings = sm.run(df, _cfg(yaml_path))

    assert findings == []
    assert "StringStatus" not in sm.artifacts


def test_missing_mppt_map_key_warns_and_skips(tmp_path):
    yaml_path = _write_strings_yaml(tmp_path, with_mppt=False)
    df = _df_day({1: 1.0, 2: 0.45})
    sm = M2bMpptRatio(poa=_MockPOA())

    with pytest.warns(UserWarning, match="mppt_map load failed"):
        findings = sm.run(df, _cfg(yaml_path))

    assert findings == []


def test_string_status_artifact_structure(tmp_path):
    yaml_path = _write_strings_yaml(tmp_path)
    df = _df_day({1: 1.0, 2: 0.45, 3: 1.0, 4: 1.0, 5: 1.0})
    sm = M2bMpptRatio(poa=_MockPOA())
    sm.run(df, _cfg(yaml_path))

    status = sm.artifacts["StringStatus"]
    assert {"inverter_id", "pv_string", "mppt", "status", "partner_strings",
            "ratio_median_daylight", "n_debounced_events"}.issubset(status.columns)
    assert set(status["status"].unique()).issubset({"NORMAL", "mppt_partner_underperform", "EMPTY"})
    assert sorted(status["mppt"].unique().tolist()) == [1, 2]
    pv2 = status[status["pv_string"] == "PV2"].iloc[0]
    assert pv2["status"] == "mppt_partner_underperform"
    assert pv2["partner_strings"] == "PV1"


def test_disabled_flag_short_circuits(tmp_path):
    yaml_path = _write_strings_yaml(tmp_path)
    df = _df_day({1: 1.0, 2: 0.45})
    sm = M2bMpptRatio(poa=_MockPOA())

    findings = sm.run(df, _cfg(yaml_path, enabled=False))

    assert findings == []
    assert sm.artifacts == {}
