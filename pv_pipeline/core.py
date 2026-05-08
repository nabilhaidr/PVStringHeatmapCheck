"""M2 plugin skeleton: Severity, M2Finding, SubModule, M2Engine."""
from __future__ import annotations

from enum import Enum


class Severity(str, Enum):
    NORMAL = "NORMAL"
    INFO = "INFO"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


import json
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class M2Finding:
    timestamp: datetime
    inverter_id: str
    pv_string: Optional[str]
    sub_module: str
    severity: Severity
    value: float
    threshold: float
    message: str
    extra: dict = field(default_factory=dict)

    def to_jsonl(self) -> str:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat() if self.timestamp else None
        d["severity"] = self.severity.value if isinstance(self.severity, Severity) else self.severity
        return json.dumps(d, ensure_ascii=False)


from typing import Iterable, List
import pandas as pd


class SubModule:
    """Base class for M2 submodules. Override `run()`."""
    name: str = "base"

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        raise NotImplementedError


class M2Engine:
    """Minimal orchestrator: jalankan list of SubModule, kumpulkan findings."""

    def __init__(self, submodules: Iterable[SubModule]):
        self.submodules = list(submodules)

    def run_all(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        findings: List[M2Finding] = []
        for sm in self.submodules:
            findings.extend(sm.run(combined_df, config))
        return findings

    @staticmethod
    def write_jsonl(findings: List[M2Finding], path: str) -> None:
        with open(path, "w", encoding="utf-8") as fp:
            for fin in findings:
                fp.write(fin.to_jsonl() + "\n")

    @staticmethod
    def to_summary_df(findings: List[M2Finding]) -> pd.DataFrame:
        rows = []
        for f in findings:
            d = asdict(f)
            d["severity"] = f.severity.value if isinstance(f.severity, Severity) else f.severity
            d["timestamp"] = f.timestamp.isoformat() if f.timestamp else None
            rows.append(d)
        return pd.DataFrame(rows)

    @staticmethod
    def write_xlsx(findings: List[M2Finding], all_strings_df: "pd.DataFrame", path: str) -> None:
        """Write 2-sheet xlsx: Findings + AllStrings."""
        findings_df = M2Engine.to_summary_df(findings)
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            import subprocess
            import sys
            print("Installing openpyxl for xlsx output")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl"])
        empty_findings = pd.DataFrame(columns=[
            "timestamp", "inverter_id", "pv_string", "sub_module",
            "severity", "value", "threshold", "message", "extra",
        ])
        empty_all = pd.DataFrame(columns=[
            "inverter_id", "pv_string", "status", "uptime_pct",
            "downtime_minutes", "event_minutes", "n_events", "daylight_minutes",
        ])
        with pd.ExcelWriter(path, engine="openpyxl") as xw:
            (findings_df if not findings_df.empty else empty_findings).to_excel(
                xw, sheet_name="Findings", index=False,
            )
            (all_strings_df if all_strings_df is not None and not all_strings_df.empty
             else empty_all).to_excel(xw, sheet_name="AllStrings", index=False)


if __name__ == "__main__":
    from pv_pipeline.core import Severity
    assert Severity.CRITICAL.value == "CRITICAL"
    assert Severity.NORMAL.value == "NORMAL"
    assert Severity("HIGH") == Severity.HIGH
    print("[core] Severity smoke OK")

    # M2Finding test
    from datetime import datetime
    from pv_pipeline.core import M2Finding
    f = M2Finding(
        timestamp=datetime(2026, 5, 7, 12, 0, 0),
        inverter_id="WB02-INV14",
        pv_string=None,
        sub_module="M2e_inverter",
        severity=Severity.CRITICAL,
        value=85.0,
        threshold=90.0,
        message="uptime 85% < 90%",
    )
    line = f.to_jsonl()
    assert '"severity": "CRITICAL"' in line
    assert '"timestamp": "2026-05-07T12:00:00"' in line
    assert '"pv_string": null' in line
    print("[core] M2Finding smoke OK")

    # M2Engine test
    import os
    import tempfile
    from pv_pipeline.core import SubModule, M2Engine
    import pandas as pd

    class _DummySM(SubModule):
        name = "dummy"
        def run(self, combined_df, config):
            return [
                M2Finding(
                    timestamp=datetime(2026, 5, 7, 12, 0, 0),
                    inverter_id="WB02-INV01",
                    pv_string="PV1",
                    sub_module="dummy",
                    severity=Severity.HIGH,
                    value=92.0,
                    threshold=95.0,
                    message="dummy",
                ),
            ]

    eng = M2Engine([_DummySM()])
    findings = eng.run_all(pd.DataFrame(), {})
    assert len(findings) == 1
    df = M2Engine.to_summary_df(findings)
    assert df.iloc[0]["severity"] == "HIGH"

    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "f.jsonl")
        M2Engine.write_jsonl(findings, out)
        with open(out, "r", encoding="utf-8") as fp:
            lines = fp.readlines()
        assert len(lines) == 1
        assert '"severity": "HIGH"' in lines[0]
    print("[core] M2Engine smoke OK")
