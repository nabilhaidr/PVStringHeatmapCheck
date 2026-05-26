"""Test pv_pipeline.core: M2Finding extension + write_xlsx_multi."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

import openpyxl
import pandas as pd
import pytest

from pv_pipeline.core import M2Engine, M2Finding, Severity, SubModule


def test_severity_enum_values():
    """Severity enum harus include CRITICAL/HIGH/MEDIUM/INFO/NORMAL."""
    for name in ["NORMAL", "INFO", "MEDIUM", "HIGH", "CRITICAL"]:
        assert hasattr(Severity, name)
    assert Severity.CRITICAL.value == "CRITICAL"
    assert Severity("HIGH") == Severity.HIGH


def test_m2finding_backward_compat():
    """M2Finding 8-field constructor harus tetap jalan (M2eAvailability legacy)."""
    f = M2Finding(
        timestamp=datetime(2026, 5, 14, 12, 0, 0),
        inverter_id="WB02-INV14",
        pv_string=None,
        sub_module="M2e_inverter",
        severity=Severity.CRITICAL,
        value=85.0,
        threshold=90.0,
        message="uptime 85% < 90%",
    )
    # New optional fields default None
    assert f.fault_type is None
    assert f.confidence is None
    assert f.evidence is None
    # JSONL serialization
    line = f.to_jsonl()
    assert '"severity": "CRITICAL"' in line
    assert '"fault_type": null' in line


def test_m2finding_with_extension_fields():
    """Sprint 1+2 extension fields serialize correctly."""
    f = M2Finding(
        timestamp=datetime(2026, 5, 14, 12, 0, 0),
        inverter_id="WB05-INV05",
        pv_string="PV3",
        sub_module="M2b_peer_zscore",
        severity=Severity.HIGH,
        value=3.2,
        threshold=2.5,
        message="High-R suspect",
        fault_type="high_R",
        confidence=80.0,
        evidence={"rstr": 412.5, "voc_ratio": 0.98},
    )
    line = f.to_jsonl()
    d = json.loads(line)
    assert d["fault_type"] == "high_R"
    assert d["confidence"] == 80.0
    assert d["evidence"]["rstr"] == 412.5
    assert d["evidence"]["voc_ratio"] == 0.98


def test_submodule_artifacts_initialized():
    """SubModule subclass tanpa explicit super().__init__() tetap dapat artifacts dict."""

    class DummyV1(SubModule):
        name = "dummy_v1"

        def run(self, df, cfg):
            return []

    class DummyV2(SubModule):
        name = "dummy_v2"

        def __init__(self):
            super().__init__()

        def run(self, df, cfg):
            return []

    for sm in [DummyV1(), DummyV2()]:
        assert hasattr(sm, "artifacts")
        assert sm.artifacts == {}


def test_write_xlsx_multi_creates_sheets():
    """write_xlsx_multi emit Findings sheet + 1 sheet per artifact per submodule."""

    class ArtSM(SubModule):
        name = "M2b_test"

        def __init__(self):
            super().__init__()

        def run(self, df, cfg):
            self.artifacts["Detail"] = pd.DataFrame(
                [{"inverter_id": "WB01-INV05", "metric": "rstr", "value": 412.5}]
            )
            return []

    finding = M2Finding(
        timestamp=datetime(2026, 5, 14, 12, 0, 0),
        inverter_id="WB01-INV05",
        pv_string="PV3",
        sub_module="M2b_test",
        severity=Severity.HIGH,
        value=1.0,
        threshold=0.5,
        message="test",
    )
    sm = ArtSM()
    eng = M2Engine([sm])
    eng.run_all(pd.DataFrame(), {})

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        out = os.path.join(td, "multi.xlsx")
        M2Engine.write_xlsx_multi([finding], [sm], out)
        wb = openpyxl.load_workbook(out, read_only=True)
        try:
            sheets = list(wb.sheetnames)
        finally:
            wb.close()
        assert "Findings" in sheets
        assert any(s.startswith("M2b_test_") for s in sheets), f"sheets={sheets}"


# ---------- filter_findings_by_exclude_flag (2026-05-23) ----------


def _mk_finding(sub_module, sev=Severity.HIGH, inv="WB05-INV01", pv="PV3"):
    return M2Finding(
        timestamp=datetime(2026, 5, 14, 12, 0, 0),
        inverter_id=inv,
        pv_string=pv,
        sub_module=sub_module,
        severity=sev,
        value=1.0,
        threshold=0.5,
        message="test",
    )


def test_default_submodule_to_cfg_key_covers_all_detectors():
    """Mapping harus include 7 detector aktif (M2b x3 + M2_iforest + M2a x3)."""
    from pv_pipeline.core import DEFAULT_SUBMODULE_TO_CFG_KEY
    expected = {
        "M2_iforest", "M2a_shading", "M2a_low_irradiance", "M2a_soiling",
        "M2b_peer_zscore", "M2b_open_circuit", "M2b_ground_fault",
    }
    assert set(DEFAULT_SUBMODULE_TO_CFG_KEY.keys()) == expected
    # iforest -> m2_iforest cfg key
    assert DEFAULT_SUBMODULE_TO_CFG_KEY["M2_iforest"] == "m2_iforest"


def test_filter_excludes_iforest_when_flag_true():
    """User scenario: M2_iforest findings dibuang, M2b peer_zscore tetap."""
    from pv_pipeline.core import filter_findings_by_exclude_flag
    findings = [
        _mk_finding("M2_iforest"),
        _mk_finding("M2_iforest"),
        _mk_finding("M2b_peer_zscore"),
        _mk_finding("M2a_shading"),
    ]
    cfg = {"m2_iforest": {"exclude_from_findings_sheet": True}}
    filtered, excluded = filter_findings_by_exclude_flag(findings, cfg)
    assert len(filtered) == 2
    assert all(f.sub_module != "M2_iforest" for f in filtered)
    assert excluded == {"M2_iforest": 2}


def test_filter_no_exclusion_when_flag_false():
    """flag=False -> pass-through, no findings excluded."""
    from pv_pipeline.core import filter_findings_by_exclude_flag
    findings = [_mk_finding("M2_iforest"), _mk_finding("M2b_peer_zscore")]
    cfg = {"m2_iforest": {"exclude_from_findings_sheet": False}}
    filtered, excluded = filter_findings_by_exclude_flag(findings, cfg)
    assert len(filtered) == 2
    assert excluded == {}


def test_filter_no_exclusion_when_key_missing():
    """No exclude_from_findings_sheet key di cfg -> pass-through."""
    from pv_pipeline.core import filter_findings_by_exclude_flag
    findings = [_mk_finding("M2_iforest"), _mk_finding("M2b_peer_zscore")]
    cfg = {"m2_iforest": {"enabled": True}}  # no exclude flag
    filtered, excluded = filter_findings_by_exclude_flag(findings, cfg)
    assert len(filtered) == 2
    assert excluded == {}


def test_filter_no_findings_for_detector_skips():
    """Detector dengan flag=True tapi 0 findings in list -> not in excluded dict."""
    from pv_pipeline.core import filter_findings_by_exclude_flag
    findings = [_mk_finding("M2b_peer_zscore")]   # no iforest
    cfg = {"m2_iforest": {"exclude_from_findings_sheet": True}}
    filtered, excluded = filter_findings_by_exclude_flag(findings, cfg)
    assert len(filtered) == 1
    assert excluded == {}


def test_filter_multiple_detectors_excluded():
    """Mau exclude 2 detectors at once."""
    from pv_pipeline.core import filter_findings_by_exclude_flag
    findings = [
        _mk_finding("M2_iforest"),
        _mk_finding("M2a_shading"),
        _mk_finding("M2a_shading"),
        _mk_finding("M2b_peer_zscore"),
    ]
    cfg = {
        "m2_iforest":  {"exclude_from_findings_sheet": True},
        "m2a_shading": {"exclude_from_findings_sheet": True},
    }
    filtered, excluded = filter_findings_by_exclude_flag(findings, cfg)
    assert len(filtered) == 1
    assert filtered[0].sub_module == "M2b_peer_zscore"
    assert excluded == {"M2_iforest": 1, "M2a_shading": 2}


def test_filter_returns_new_list_does_not_mutate_input():
    """Helper is pure -- input list tidak boleh di-mutate."""
    from pv_pipeline.core import filter_findings_by_exclude_flag
    findings = [_mk_finding("M2_iforest"), _mk_finding("M2b_peer_zscore")]
    cfg = {"m2_iforest": {"exclude_from_findings_sheet": True}}
    filter_findings_by_exclude_flag(findings, cfg)
    # Input unchanged
    assert len(findings) == 2
    assert findings[0].sub_module == "M2_iforest"


def test_filter_custom_mapping_overrides_default():
    """Caller can pass custom submodule_to_cfg_key mapping."""
    from pv_pipeline.core import filter_findings_by_exclude_flag
    findings = [_mk_finding("CustomDetector"), _mk_finding("M2b_peer_zscore")]
    cfg = {"custom_section": {"exclude_from_findings_sheet": True}}
    custom_map = {"CustomDetector": "custom_section"}
    filtered, excluded = filter_findings_by_exclude_flag(
        findings, cfg, submodule_to_cfg_key=custom_map,
    )
    assert len(filtered) == 1
    assert filtered[0].sub_module == "M2b_peer_zscore"
    assert excluded == {"CustomDetector": 1}


def test_filter_empty_findings_returns_empty():
    """Empty findings list -> empty result, empty excluded dict."""
    from pv_pipeline.core import filter_findings_by_exclude_flag
    cfg = {"m2_iforest": {"exclude_from_findings_sheet": True}}
    filtered, excluded = filter_findings_by_exclude_flag([], cfg)
    assert filtered == []
    assert excluded == {}


def test_default_m2_iforest_excludes_from_findings():
    """DEFAULT_M2_CONFIG['m2_iforest']['exclude_from_findings_sheet'] is True."""
    from pv_pipeline.m2_config import DEFAULT_M2_CONFIG
    assert DEFAULT_M2_CONFIG["m2_iforest"]["exclude_from_findings_sheet"] is True
