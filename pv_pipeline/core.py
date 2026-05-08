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
