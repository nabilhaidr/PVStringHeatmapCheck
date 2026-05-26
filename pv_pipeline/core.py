"""M2 plugin skeleton: Severity, M2Finding, SubModule, M2Engine.

Sprint 1+2 extension (di branch ini di-replicate dari nol):
- M2Finding tambah optional fields: fault_type, confidence, evidence
  (semua None default supaya backward-compat dengan existing M2eAvailability).
- SubModule tambah `self.artifacts: Dict[str, pd.DataFrame]` channel untuk
  emit multi-sheet output ke xlsx (selain Findings utama).
- M2Engine.write_xlsx_multi: Findings + 1 sheet per artifact per submodule.
"""
from __future__ import annotations

from enum import Enum


class Severity(str, Enum):
    NORMAL = "NORMAL"
    INFO = "INFO"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Dict, List, Optional


def load_empty_pv_map(
    config: dict,
    *,
    base_dir: str = "",
) -> Dict[str, List[int]]:
    """Wave 11 hotfix #5: load ``empty_pv_map`` dari ``config/strings.yaml``.

    Path resolution order:
      1. ``config["m2e"]["empty_pv_map_path"]`` (default ``"config/strings.yaml"``).
      2. ``base_dir / path`` (kalau path relative + base_dir given).

    Konsumen utama: fan-out fallback di M2b detectors -- supaya
    StringStatus sheet skip PV slots yang memang kosong by design (sesuai
    EMPTY_PV_MAP) dan tidak menampilkan PV1..PV14 untuk SEMUA inverter
    secara uniform.

    Parameters
    ----------
    config : dict
        Full m2_config dict.
    base_dir : str, optional
        Optional prefix untuk relative path resolution. Berguna untuk
        notebook yang run dari cwd lain (e.g., test tmp_path).

    Returns
    -------
    dict
        ``{<Inverter_ID upper>: [<empty_pv_index>, ...]}``. Empty dict
        kalau file missing/parse error (silent fail; caller fallback ke
        emit-all-PV behavior).
    """
    path = (config.get("m2e") or {}).get("empty_pv_map_path", "config/strings.yaml")
    candidates = [path]
    if base_dir and not os.path.isabs(path):
        candidates.append(os.path.join(base_dir, path))
    for p in candidates:
        if not os.path.exists(p):
            continue
        try:
            import yaml  # noqa: WPS433
            with open(p, "r", encoding="utf-8") as fp:
                cfg = yaml.safe_load(fp) or {}
            empty_map = cfg.get("empty_pv_map") or {}
            # Normalize: uppercase keys, int list values.
            return {
                str(k).upper(): [int(v) for v in (vals or [])]
                for k, vals in empty_map.items()
            }
        except Exception:
            return {}
    return {}


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
    # Sprint 1+2 extension (semua optional supaya M2eAvailability lama tidak break).
    fault_type: Optional[str] = None   # "open_circuit" | "high_R" | "ground_fault" | "intermittent"
    confidence: Optional[float] = None # 0..100, biasanya per spec 4.2.3
    evidence: Optional[dict] = None    # extra signals untuk cross-check / artefact

    def to_jsonl(self) -> str:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat() if self.timestamp else None
        d["severity"] = self.severity.value if isinstance(self.severity, Severity) else self.severity
        # default=str untuk handle numpy/pandas types yang muncul di evidence dict.
        return json.dumps(d, ensure_ascii=False, default=str)


from typing import Iterable, List
import pandas as pd


class SubModule:
    """Base class for M2 submodules. Override `run()`.

    Multi-sheet output channel: assign DataFrame ke ``self.artifacts[sheet_name]``
    di dalam ``run()``. M2Engine.write_xlsx_multi akan emit setiap artifact
    sebagai sheet tersendiri (prefix dengan ``name_`` supaya tidak collision).
    """
    name: str = "base"

    def __init__(self):
        self.artifacts: Dict[str, pd.DataFrame] = {}

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        raise NotImplementedError


# Default mapping: submodule.name -> config key (used by
# filter_findings_by_exclude_flag). Update when adding new detectors.
DEFAULT_SUBMODULE_TO_CFG_KEY: Dict[str, str] = {
    "M2_iforest":         "m2_iforest",
    "M2a_shading":        "m2a_shading",
    "M2a_low_irradiance": "m2a_low_irradiance",
    "M2a_soiling":        "m2a_soiling",
    "M2b_peer_zscore":    "m2b",
    "M2b_open_circuit":   "m2b_open_circuit",
    "M2b_ground_fault":   "m2b_ground_fault",
}


def filter_findings_by_exclude_flag(
    findings: List["M2Finding"],
    config: dict,
    submodule_to_cfg_key: Optional[Dict[str, str]] = None,
):
    """Filter findings whose detector config has ``exclude_from_findings_sheet=True``.

    Use case (2026-05-23): user mau detector tertentu (mis. M2_iforest)
    tetap punya artifact sheets di xlsx tapi findings-nya TIDAK muncul
    di Findings sheet utama DAN tidak trigger auto-skip Cell 7 baseline.

    Behavior:
        - Findings dari detector dengan
          ``config[cfg_key]["exclude_from_findings_sheet"]=True`` dibuang
          dari output list.
        - Detector tanpa flag tsb (atau flag=False) tetap pass-through.
        - Submodule's own ``artifacts`` dict TIDAK dipengaruhi -- caller
          masih bisa pass ``submodules`` list ke ``write_xlsx_multi`` dan
          per-detector artifact sheets tetap di-emit.

    Parameters
    ----------
    findings : list of M2Finding
        Output dari ``M2Engine.run_all(...)``.
    config : dict
        Full m2_config dict (yang punya per-detector sections).
    submodule_to_cfg_key : dict, optional
        Override mapping from ``M2Finding.sub_module`` string ke config key.
        Default = ``DEFAULT_SUBMODULE_TO_CFG_KEY``.

    Returns
    -------
    (filtered_findings, excluded_counts) : tuple
        filtered_findings : list of M2Finding (subset of input).
        excluded_counts   : dict ``{sub_module_name: count_excluded}``.
                            Empty dict kalau tidak ada exclusion.

    Examples
    --------
    >>> # cfg has m2_iforest.exclude_from_findings_sheet=True
    >>> filtered, excluded = filter_findings_by_exclude_flag(findings, cfg)
    >>> # excluded = {"M2_iforest": 47} if 47 iforest findings were filtered.
    """
    mapping = submodule_to_cfg_key or DEFAULT_SUBMODULE_TO_CFG_KEY
    excluded_counts: Dict[str, int] = {}
    filtered = list(findings)
    for sm_name, cfg_key in mapping.items():
        det_cfg = config.get(cfg_key, {}) or {}
        if not det_cfg.get("exclude_from_findings_sheet", False):
            continue
        n_before = sum(1 for f in filtered if f.sub_module == sm_name)
        if n_before == 0:
            continue
        filtered = [f for f in filtered if f.sub_module != sm_name]
        excluded_counts[sm_name] = n_before
    return filtered, excluded_counts


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
    def write_xlsx_multi(
        findings: List[M2Finding],
        submodules: Iterable[SubModule],
        path: str,
    ) -> None:
        """Findings sheet + 1 sheet per artifact per submodule.

        Setiap submodule boleh assign ``self.artifacts[sheet_name] = DataFrame``
        di run(). M2Engine.write_xlsx_multi akan emit ke file xlsx dengan nama
        sheet ``{submodule.name}_{sheet_name}`` (max 31 char, dipotong kalau lebih).
        """
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            import subprocess
            import sys
            print("Installing openpyxl for xlsx output")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl"])

        findings_df = M2Engine.to_summary_df(findings)
        empty_findings = pd.DataFrame(columns=[
            "timestamp", "inverter_id", "pv_string", "sub_module",
            "severity", "value", "threshold", "message", "extra",
            "fault_type", "confidence", "evidence",
        ])
        used_sheet_names = set()
        with pd.ExcelWriter(path, engine="openpyxl") as xw:
            (findings_df if not findings_df.empty else empty_findings).to_excel(
                xw, sheet_name="Findings", index=False,
            )
            used_sheet_names.add("Findings")
            for sm in submodules:
                arts = getattr(sm, "artifacts", None) or {}
                for sheet_name, df in arts.items():
                    if df is None or df.empty:
                        continue
                    # Compose sheet name; Excel limit 31 char, unique.
                    base = f"{sm.name}_{sheet_name}" if len(f"{sm.name}_{sheet_name}") <= 31 \
                        else sheet_name[:31]
                    full = base[:31]
                    # Disambiguate kalau collision (rare).
                    counter = 1
                    while full in used_sheet_names:
                        suffix = f"_{counter}"
                        full = (base[: 31 - len(suffix)]) + suffix
                        counter += 1
                    used_sheet_names.add(full)
                    df.to_excel(xw, sheet_name=full, index=False)

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

    # Sprint 1+2 extension smoke: M2Finding fault_type/confidence/evidence
    f2 = M2Finding(
        timestamp=datetime(2026, 5, 14, 12, 0, 0),
        inverter_id="WB01-INV05",
        pv_string="PV12",
        sub_module="M2b_peer_zscore",
        severity=Severity.HIGH,
        value=3.2,
        threshold=2.5,
        message="High-R suspect",
        fault_type="high_R",
        confidence=80.0,
        evidence={"rstr": 412.5, "voc_ratio": 0.98, "poa_source": "auto"},
    )
    line2 = f2.to_jsonl()
    assert '"fault_type": "high_R"' in line2
    assert '"confidence": 80.0' in line2
    assert '"voc_ratio": 0.98' in line2
    print("[core] M2Finding Sprint 1+2 extension smoke OK")

    # SubModule.artifacts channel + M2Engine.write_xlsx_multi smoke
    class _ArtSM(SubModule):
        name = "M2b_test"

        def __init__(self):
            super().__init__()

        def run(self, combined_df, config):
            self.artifacts["Detail"] = pd.DataFrame(
                [{"inverter_id": "WB01-INV05", "metric": "rstr", "value": 412.5}]
            )
            return [f2]

    art_sm = _ArtSM()
    eng2 = M2Engine([art_sm])
    findings2 = eng2.run_all(pd.DataFrame(), {})
    assert "Detail" in art_sm.artifacts
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        out_xlsx = os.path.join(td, "multi.xlsx")
        M2Engine.write_xlsx_multi(findings2, [art_sm], out_xlsx)
        # Verify file dibuat dan punya 2 sheet. Close eksplisit (Windows file lock).
        import openpyxl
        wb = openpyxl.load_workbook(out_xlsx, read_only=True)
        try:
            sheets = list(wb.sheetnames)
        finally:
            wb.close()
        assert "Findings" in sheets
        assert any(s.startswith("M2b_test_") for s in sheets), f"sheets={sheets}"
    print("[core] write_xlsx_multi smoke OK")
