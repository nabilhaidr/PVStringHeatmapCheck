"""Sprint 3.3 - Baseline Accumulator untuk PLTS-IKN.

Akumulasi data NORMAL daily snapshot supaya nanti dipakai untuk training
LSTM-AE (Sprint 4) detector intermittent fault.

User decisions (recorded):
    - Periodicity        : Manual via notebook setiap hari (no cron).
    - Storage format     : Parquet primary + CSV backup (both saved).
    - NORMAL labeling    : Hybrid - user mark major maintenance + auto-skip
                           detector findings dengan severity CRITICAL/HIGH.
    - Storage location   : ``baseline/{YYYY-MM}/{YYYY-MM-DD}.{parquet,csv}``
                           (local di REPO_DIR; user upload manual ke Drive folder
                           yang sama dengan Huawei xlsx).

Hybrid filter strategy:
    1. Drop rows di periode user-marked maintenance (from baseline.yaml).
       - ``affected: all`` -> drop semua row dalam window
       - ``affected: ["WB02-INV05", ...]`` -> drop hanya Inverter_IDs match
       - ``affected: ["WB02"]`` (WB prefix only) -> drop semua inverter di WB02

    2. Auto-skip from findings CRITICAL/HIGH. Scope determined by
       ``skip_scope`` (default ``"pv_string"`` since 2026-05-23):

       - ``"pv_string"`` (DEFAULT, granular):
         * Per-PV findings (``finding.pv_string`` is set, e.g. PV3): NaN
           out only that PV's input voltage(V), input current(A), and
           Power(kW) columns. Inverter's other PV strings tetap valid
           untuk baseline -- don't waste healthy data karena 1 string fault.
         * Inverter-level findings (``finding.pv_string is None``, e.g.
           M2eAvailability, M2aShading site-level): drop seluruh inverter-
           day -- can't isolate to specific PV.
       - ``"inverter_day"`` (LEGACY, conservative): drop seluruh inverter-
         day jika ADA finding (regardless of pv_string). Sebelumnya default;
         masih supported untuk backward compat.

Output:
    {base_dir}/
        {YYYY-MM}/{YYYY-MM-DD}.parquet   (primary, columnar, efficient)
        {YYYY-MM}/{YYYY-MM-DD}.csv       (backup, universal)
    {base_dir}/manifest.csv              (audit trail per accumulated date)
"""
from __future__ import annotations

import csv
import os
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


DEFAULT_BASE_DIR: str = "baseline"
DEFAULT_OUTPUT_FORMATS: Tuple[str, ...] = ("parquet", "csv")
DEFAULT_AUTO_SKIP_SEVERITY: Tuple[str, ...] = ("CRITICAL", "HIGH")
# Skip scope: "pv_string" (default, 2026-05-23+) = NaN per-PV cols for findings
# dengan finding.pv_string set; only drop whole inverter untuk inverter-level
# findings. "inverter_day" (legacy) = drop whole inverter for any finding.
DEFAULT_SKIP_SCOPE: str = "pv_string"
VALID_SKIP_SCOPES: Tuple[str, ...] = ("pv_string", "inverter_day")
DEFAULT_MIN_ROWS_PER_INVERTER: int = 30   # minimum surviving rows per inverter
DEFAULT_OVERWRITE: bool = False

# PV column patterns to NaN out under "pv_string" scope. Match all known
# Huawei xlsx variants (lowercase PV1-PV14 + Title Case PV15-PV28).
_PV_COL_PATTERNS_TO_NAN: Tuple[str, ...] = (
    "PV{n} input voltage(V)",
    "PV{n} input current(A)",
    "PV{n} Input Voltage(V)",
    "PV{n} Input Current(A)",
    "PV{n} Power(kW)",
)


def _extract_pv_index(pv_string: str) -> Optional[int]:
    """Extract integer N from "PV3" / "pv12" / "PV{n}". Return None if invalid."""
    if not pv_string:
        return None
    s = str(pv_string).strip().upper().replace("PV", "").replace(" ", "")
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _ensure_yaml() -> None:
    try:
        import yaml  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: PyYAML")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "PyYAML"])


def _ensure_pyarrow() -> None:
    """Pastikan pyarrow tersedia (auto-install kalau belum) untuk parquet."""
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: pyarrow (for parquet I/O)")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyarrow"])


@dataclass
class FilterSummary:
    """Statistik filter run (untuk manifest + reporting)."""

    rows_total: int = 0
    rows_kept: int = 0
    rows_skipped_maintenance: int = 0
    rows_skipped_findings: int = 0          # inverter-level drops only
    rows_skipped_min_rows: int = 0
    rows_pv_nanned: int = 0                 # rows touched by per-PV NaN-out
    inverters_total: int = 0
    inverters_kept: int = 0
    inverters_skipped_findings: List[str] = field(default_factory=list)
    inverters_skipped_min_rows: List[str] = field(default_factory=list)
    pv_strings_skipped_findings: List[str] = field(default_factory=list)
    """Per-PV NaN list, format ``"WB05-INV01:PV3"`` (scope=pv_string only)."""
    maintenance_matches: int = 0


@dataclass
class MaintenancePeriod:
    """User-marked maintenance window dari config/baseline.yaml."""

    start: pd.Timestamp
    end: pd.Timestamp
    affected: List[str] = field(default_factory=list)
    affect_all: bool = False
    affect_wb_prefix: List[str] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MaintenancePeriod":
        start = pd.to_datetime(d["start"])
        end = pd.to_datetime(d["end"])
        # End mungkin date saja (00:00); kalau jam tidak ada, set ke 23:59:59 untuk
        # cover full day (asumsi: user tulis tanggal saja = seluruh hari).
        if end == end.normalize():
            end = end + pd.Timedelta(hours=23, minutes=59, seconds=59)
        affected_raw = d.get("affected", "all")
        affect_all = False
        affected: List[str] = []
        affect_wb_prefix: List[str] = []
        if isinstance(affected_raw, str) and affected_raw.lower() == "all":
            affect_all = True
        elif isinstance(affected_raw, list):
            for item in affected_raw:
                s = str(item).upper().strip()
                if not s:
                    continue
                if "-INV" in s:
                    # Specific Inverter_ID, mis "WB02-INV05"
                    affected.append(s)
                else:
                    # WB prefix wildcard, mis "WB02"
                    affect_wb_prefix.append(s)
        return cls(
            start=start,
            end=end,
            affected=affected,
            affect_all=affect_all,
            affect_wb_prefix=affect_wb_prefix,
            reason=str(d.get("reason", "")),
        )

    def matches(self, ts: pd.Timestamp, inverter_id: str) -> bool:
        if not (self.start <= ts <= self.end):
            return False
        if self.affect_all:
            return True
        inv_up = str(inverter_id).upper()
        if inv_up in self.affected:
            return True
        for prefix in self.affect_wb_prefix:
            if inv_up.startswith(prefix):
                return True
        return False


class BaselineAccumulator:
    """Filter NORMAL periods + save daily snapshot ke parquet/csv + manifest.

    Parameters
    ----------
    base_dir : str
        Root directory untuk output (default ``"baseline"``).
    output_formats : tuple of {"parquet","csv"}
        Format file yang di-save (keduanya default).
    auto_skip_severity : tuple of str
        Severity levels yang trigger auto-skip (default CRITICAL+HIGH).
    skip_scope : {"inverter_day"}
        Scope auto-skip (saat ini hanya "inverter_day": kalau inverter punya
        finding di hari itu, seluruh datanya di-skip).
    min_rows_per_inverter : int
        Minimum row count per inverter setelah filter; kurang dari ini -> drop.
    overwrite : bool
        True = overwrite existing daily file; False = skip + warn.
    maintenance_periods : List[MaintenancePeriod]
        User-marked windows untuk di-skip.
    """

    def __init__(
        self,
        base_dir: str = DEFAULT_BASE_DIR,
        output_formats: Iterable[str] = DEFAULT_OUTPUT_FORMATS,
        auto_skip_severity: Iterable[str] = DEFAULT_AUTO_SKIP_SEVERITY,
        skip_scope: str = DEFAULT_SKIP_SCOPE,
        min_rows_per_inverter: int = DEFAULT_MIN_ROWS_PER_INVERTER,
        overwrite: bool = DEFAULT_OVERWRITE,
        maintenance_periods: Optional[List[MaintenancePeriod]] = None,
    ):
        self.base_dir = str(base_dir)
        self.output_formats = tuple(str(f).lower() for f in output_formats)
        self.auto_skip_severity = tuple(str(s).upper() for s in auto_skip_severity)
        self.skip_scope = str(skip_scope).lower()
        if self.skip_scope not in VALID_SKIP_SCOPES:
            warnings.warn(
                f"[baseline] skip_scope={self.skip_scope!r} unknown; "
                f"valid={VALID_SKIP_SCOPES}. Defaulting to {DEFAULT_SKIP_SCOPE!r}.",
                stacklevel=2,
            )
            self.skip_scope = DEFAULT_SKIP_SCOPE
        self.min_rows_per_inverter = int(min_rows_per_inverter)
        self.overwrite = bool(overwrite)
        self.maintenance_periods: List[MaintenancePeriod] = list(maintenance_periods or [])

    # ---------- IO helpers ----------

    @classmethod
    def from_yaml(cls, baseline_yaml_path: str) -> "BaselineAccumulator":
        """Load dari ``config/baseline.yaml``.

        Yaml structure expected:
            accumulator:
              base_dir: "baseline"
              output_formats: [parquet, csv]
              auto_skip_severity: [CRITICAL, HIGH]
              skip_scope: "inverter_day"
              min_rows_per_inverter: 30
              overwrite: false
            maintenance_periods:
              - start: "2026-04-15 08:00"
                end: "2026-04-15 16:00"
                affected: ["WB02-INV05", "WB02-INV06"]
                reason: "Inverter maintenance"
        """
        if not os.path.exists(baseline_yaml_path):
            warnings.warn(
                f"[baseline] {baseline_yaml_path!r} not found, using defaults (no maintenance).",
                stacklevel=2,
            )
            return cls()
        _ensure_yaml()
        import yaml  # noqa: WPS433

        with open(baseline_yaml_path, "r", encoding="utf-8") as fp:
            cfg = yaml.safe_load(fp) or {}

        acc_cfg = cfg.get("accumulator", {}) or {}
        periods_raw = cfg.get("maintenance_periods", []) or []
        periods = []
        for p in periods_raw:
            try:
                periods.append(MaintenancePeriod.from_dict(p))
            except Exception as exc:
                warnings.warn(
                    f"[baseline] invalid maintenance period {p!r}: {exc}",
                    stacklevel=2,
                )

        return cls(
            base_dir=str(acc_cfg.get("base_dir", DEFAULT_BASE_DIR)),
            output_formats=tuple(acc_cfg.get("output_formats", DEFAULT_OUTPUT_FORMATS)),
            auto_skip_severity=tuple(acc_cfg.get("auto_skip_severity", DEFAULT_AUTO_SKIP_SEVERITY)),
            skip_scope=str(acc_cfg.get("skip_scope", DEFAULT_SKIP_SCOPE)),
            min_rows_per_inverter=int(acc_cfg.get("min_rows_per_inverter", DEFAULT_MIN_ROWS_PER_INVERTER)),
            overwrite=bool(acc_cfg.get("overwrite", DEFAULT_OVERWRITE)),
            maintenance_periods=periods,
        )

    # ---------- Filtering ----------

    def filter_combined_df(
        self,
        df: pd.DataFrame,
        findings: Optional[List[Any]] = None,
    ) -> Tuple[pd.DataFrame, FilterSummary]:
        """Apply hybrid filter (maintenance + findings auto-skip).

        Returns
        -------
        (filtered_df, summary)
        """
        if "Start Time" not in df.columns or "Inverter_ID" not in df.columns:
            raise ValueError(
                "[baseline] combined_df missing 'Start Time' or 'Inverter_ID' column."
            )

        df = df.copy()
        df["__ts"] = pd.to_datetime(df["Start Time"], errors="coerce")
        df["__inv"] = df["Inverter_ID"].astype(str).str.upper()

        summary = FilterSummary()
        summary.rows_total = len(df)
        summary.inverters_total = df["__inv"].nunique()

        keep_mask = pd.Series(True, index=df.index)

        # --- Step 1: User-marked maintenance ---
        if self.maintenance_periods:
            maint_mask = pd.Series(False, index=df.index)
            for period in self.maintenance_periods:
                # Build boolean mask per period.
                ts_in = (df["__ts"] >= period.start) & (df["__ts"] <= period.end)
                if period.affect_all:
                    inv_in = pd.Series(True, index=df.index)
                else:
                    inv_match_exact = df["__inv"].isin(period.affected)
                    inv_match_prefix = pd.Series(False, index=df.index)
                    for prefix in period.affect_wb_prefix:
                        inv_match_prefix |= df["__inv"].str.startswith(prefix)
                    inv_in = inv_match_exact | inv_match_prefix
                period_match = ts_in & inv_in
                summary.maintenance_matches += int(period_match.sum())
                maint_mask |= period_match
            summary.rows_skipped_maintenance = int(maint_mask.sum())
            keep_mask &= ~maint_mask

        # --- Step 2: Auto-skip from findings ---
        # Split by scope: per-PV findings -> NaN PV cols only; inverter-level
        # findings (pv_string=None) -> drop whole inverter regardless of scope.
        skip_inverters: set = set()           # whole-inverter drop set
        skip_pv_pairs: set = set()            # {(inverter_id, pv_string), ...}
        if findings:
            for f in findings:
                severity_val = getattr(f, "severity", None)
                # Severity bisa Enum atau string.
                sev_str = severity_val.value if hasattr(severity_val, "value") else str(severity_val)
                if sev_str.upper() not in self.auto_skip_severity:
                    continue
                inv_id = str(getattr(f, "inverter_id", "")).upper()
                if not inv_id:
                    continue
                pv_str = getattr(f, "pv_string", None)
                if self.skip_scope == "inverter_day":
                    # Legacy: drop whole inverter for any finding.
                    skip_inverters.add(inv_id)
                elif pv_str is not None and str(pv_str).strip():
                    # pv_string scope: NaN out only this PV's cols.
                    skip_pv_pairs.add((inv_id, str(pv_str).strip().upper()))
                else:
                    # pv_string scope + finding has no pv_string ->
                    # inverter-level (e.g. M2eAvailability, M2a Soiling SITE).
                    # Can't isolate, so still drop whole inverter.
                    skip_inverters.add(inv_id)

        # Apply per-PV NaN (in place on df copy; happens BEFORE row drop so
        # filtered_df below sees the NaN'd columns).
        if skip_pv_pairs:
            for inv_id, pv_str in skip_pv_pairs:
                pv_n = _extract_pv_index(pv_str)
                if pv_n is None:
                    continue
                row_mask = df["__inv"] == inv_id
                if row_mask.sum() == 0:
                    continue
                touched = False
                for pat in _PV_COL_PATTERNS_TO_NAN:
                    col = pat.format(n=pv_n)
                    if col in df.columns:
                        df.loc[row_mask, col] = float("nan")
                        touched = True
                if touched:
                    summary.rows_pv_nanned += int(row_mask.sum())
            summary.pv_strings_skipped_findings = sorted(
                f"{inv}:{pv}" for inv, pv in skip_pv_pairs
            )

        # Apply inverter-level skip (drop rows).
        if skip_inverters:
            findings_mask = df["__inv"].isin(skip_inverters)
            summary.rows_skipped_findings = int(findings_mask.sum())
            summary.inverters_skipped_findings = sorted(skip_inverters)
            keep_mask &= ~findings_mask

        filtered = df.loc[keep_mask].copy()

        # --- Step 3: Min rows per inverter ---
        if not filtered.empty and self.min_rows_per_inverter > 0:
            counts = filtered.groupby("__inv").size()
            sparse_invs = set(counts[counts < self.min_rows_per_inverter].index)
            if sparse_invs:
                sparse_mask = filtered["__inv"].isin(sparse_invs)
                summary.rows_skipped_min_rows = int(sparse_mask.sum())
                summary.inverters_skipped_min_rows = sorted(sparse_invs)
                filtered = filtered.loc[~sparse_mask].copy()

        # Drop helper cols.
        filtered = filtered.drop(columns=["__ts", "__inv"], errors="ignore")

        summary.rows_kept = len(filtered)
        summary.inverters_kept = filtered["Inverter_ID"].nunique() if not filtered.empty else 0

        return filtered, summary

    # ---------- Save ----------

    def _resolve_paths(self, date_str: str) -> Dict[str, str]:
        """date_str = 'YYYYMMDD' or 'YYYY-MM-DD'. Build daily file paths."""
        s = str(date_str).replace("-", "")
        if len(s) != 8 or not s.isdigit():
            raise ValueError(f"[baseline] date_str must be YYYYMMDD, got {date_str!r}")
        ym = f"{s[:4]}-{s[4:6]}"      # "YYYY-MM" subfolder
        ymd = f"{s[:4]}-{s[4:6]}-{s[6:]}"  # "YYYY-MM-DD" file basename
        sub_dir = os.path.join(self.base_dir, ym)
        return {
            "dir": sub_dir,
            "parquet": os.path.join(sub_dir, f"{ymd}.parquet"),
            "csv": os.path.join(sub_dir, f"{ymd}.csv"),
            "ymd": ymd,
        }

    def save_daily(
        self,
        df: pd.DataFrame,
        date_str: str,
    ) -> Dict[str, Optional[str]]:
        """Save filtered df ke parquet + csv.

        Returns
        -------
        Dict[str, str | None]
            {"parquet": path or None, "csv": path or None}.
        """
        paths = self._resolve_paths(date_str)
        os.makedirs(paths["dir"], exist_ok=True)
        out: Dict[str, Optional[str]] = {"parquet": None, "csv": None}

        if df.empty:
            warnings.warn(
                f"[baseline] filtered df kosong untuk {date_str}; skip save.",
                stacklevel=2,
            )
            return out

        if "parquet" in self.output_formats:
            target = paths["parquet"]
            if os.path.exists(target) and not self.overwrite:
                warnings.warn(
                    f"[baseline] {target!r} exists, skipping (set overwrite=True to replace).",
                    stacklevel=2,
                )
            else:
                _ensure_pyarrow()
                try:
                    df.to_parquet(target, engine="pyarrow", index=False)
                    out["parquet"] = target
                except Exception as exc:
                    warnings.warn(
                        f"[baseline] parquet save failed ({exc.__class__.__name__}): {exc}",
                        stacklevel=2,
                    )

        if "csv" in self.output_formats:
            target = paths["csv"]
            if os.path.exists(target) and not self.overwrite:
                warnings.warn(
                    f"[baseline] {target!r} exists, skipping (set overwrite=True to replace).",
                    stacklevel=2,
                )
            else:
                try:
                    df.to_csv(target, index=False, encoding="utf-8")
                    out["csv"] = target
                except Exception as exc:
                    warnings.warn(
                        f"[baseline] csv save failed ({exc.__class__.__name__}): {exc}",
                        stacklevel=2,
                    )

        return out

    # ---------- Manifest ----------

    def manifest_path(self) -> str:
        return os.path.join(self.base_dir, "manifest.csv")

    def update_manifest(
        self,
        date_str: str,
        summary: FilterSummary,
        paths: Dict[str, Optional[str]],
    ) -> None:
        """Append row ke manifest CSV."""
        paths_resolved = self._resolve_paths(date_str)
        ymd = paths_resolved["ymd"]
        compact_ymd = ymd.replace("-", "")
        csv_path = paths.get("csv") or ""
        csv_name = os.path.basename(csv_path or paths_resolved["csv"])
        manifest_file = self.manifest_path()
        os.makedirs(self.base_dir, exist_ok=True)

        row = {
            "date": ymd,
            "rows_total": summary.rows_total,
            "rows_kept": summary.rows_kept,
            "rows_skipped_maintenance": summary.rows_skipped_maintenance,
            "rows_skipped_findings": summary.rows_skipped_findings,
            "rows_skipped_min_rows": summary.rows_skipped_min_rows,
            "rows_pv_nanned": summary.rows_pv_nanned,
            "inverters_total": summary.inverters_total,
            "inverters_kept": summary.inverters_kept,
            "inverters_skipped_findings": ";".join(summary.inverters_skipped_findings),
            "inverters_skipped_min_rows": ";".join(summary.inverters_skipped_min_rows),
            "pv_strings_skipped_findings": ";".join(summary.pv_strings_skipped_findings),
            "maintenance_matches": summary.maintenance_matches,
            "file_parquet": paths.get("parquet") or "",
            "file_csv": csv_path,
            "baseline_csv_name": csv_name,
            "baseline_csv_file_id": "",
            "baseline_csv_url": "",
            "findings_xlsx_name": f"m2_findings_{compact_ymd}.xlsx",
            "findings_xlsx_file_id": "",
            "findings_xlsx_url": "",
            "findings_jsonl_name": f"m2_findings_{compact_ymd}.jsonl",
            "findings_jsonl_file_id": "",
            "findings_jsonl_url": "",
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        fieldnames = list(row.keys())
        rows: List[Dict[str, Any]] = []
        if os.path.exists(manifest_file):
            with open(manifest_file, "r", encoding="utf-8", newline="") as fp:
                reader = csv.reader(fp)
                header = next(reader, [])
                for existing_row in reader:
                    if not any(existing_row):
                        continue
                    if len(existing_row) == len(header):
                        rows.append(dict(zip(header, existing_row)))
                    elif len(existing_row) == len(fieldnames):
                        rows.append(dict(zip(fieldnames, existing_row)))
                    else:
                        parsed = dict(zip(header, existing_row[:len(header)]))
                        extra_columns = [col for col in fieldnames if col not in header]
                        for col, value in zip(extra_columns, existing_row[len(header):]):
                            parsed[col] = value
                        rows.append(parsed)
                for col in header:
                    if col and col not in fieldnames:
                        fieldnames.append(col)

        rows.append(row)
        with open(manifest_file, "w", encoding="utf-8", newline="") as fp:
            w = csv.DictWriter(fp, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    # ---------- All-in-one ----------

    def run(
        self,
        combined_df: pd.DataFrame,
        date_str: str,
        findings: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """Filter + save + update manifest. Returns ringkasan dict."""
        filtered, summary = self.filter_combined_df(combined_df, findings=findings)
        paths = self.save_daily(filtered, date_str)
        self.update_manifest(date_str, summary, paths)
        return {
            "date_str": date_str,
            "summary": summary,
            "paths": paths,
            "rows_kept": summary.rows_kept,
            "inverters_kept": summary.inverters_kept,
        }


if __name__ == "__main__":
    # Smoke test: synthetic combined_df 3 inverter, dengan 1 maintenance period
    # dan 1 finding HIGH untuk inverter X (auto-skip).
    import sys
    import tempfile

    sys.path.insert(0, ".claude/worktrees/modest-shockley-9c31f4")

    # Buat synthetic data: 2026-05-14 06:00 - 18:00, 3 inverters @ 5-min.
    n = 145
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 18:00", freq="5min")[:n]
    rows = []
    for inv in ["WB05-INV01", "WB05-INV02", "WB02-INV05"]:
        for ts in t:
            rows.append({
                "Inverter_ID": inv,
                "Start Time": ts,
                "PV1 input voltage(V)": 1200.0,
                "PV1 input current(A)": 10.0,
            })
    df = pd.DataFrame(rows)

    # Maintenance: WB02 di 2026-05-14 10:00-12:00.
    periods = [MaintenancePeriod.from_dict({
        "start": "2026-05-14 10:00",
        "end": "2026-05-14 12:00",
        "affected": ["WB02"],
        "reason": "Test maintenance",
    })]

    # Synthetic finding: WB05-INV02 HIGH severity -> auto-skip seluruh inverter_day.
    class _MockFinding:
        def __init__(self, inv, sev):
            self.inverter_id = inv
            class _S:
                pass
            self.severity = _S()
            self.severity.value = sev
    findings = [_MockFinding("WB05-INV02", "HIGH")]

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        acc = BaselineAccumulator(
            base_dir=os.path.join(td, "baseline"),
            output_formats=("parquet", "csv"),
            maintenance_periods=periods,
            min_rows_per_inverter=30,
            overwrite=True,
        )
        result = acc.run(df, "2026-05-14", findings=findings)
        s = result["summary"]
        print(f"[baseline] rows_total={s.rows_total} kept={s.rows_kept}")
        print(f"[baseline] skipped_maintenance={s.rows_skipped_maintenance} "
              f"(matches={s.maintenance_matches})")
        print(f"[baseline] skipped_findings={s.rows_skipped_findings} "
              f"inverters={s.inverters_skipped_findings}")
        print(f"[baseline] paths={result['paths']}")

        # Verify file dibuat
        assert result["paths"]["parquet"] and os.path.exists(result["paths"]["parquet"])
        assert result["paths"]["csv"] and os.path.exists(result["paths"]["csv"])
        # Verify manifest dibuat
        assert os.path.exists(acc.manifest_path())
        # WB05-INV02 harus tidak ada di filtered (auto-skip from findings)
        df_out = pd.read_parquet(result["paths"]["parquet"])
        invs_out = set(df_out["Inverter_ID"].unique())
        assert "WB05-INV02" not in invs_out, "WB05-INV02 should be auto-skipped"
        # WB02-INV05 partially di-filter (10-12 maintenance window).
        wb02_count = (df_out["Inverter_ID"] == "WB02-INV05").sum()
        # Expected: WB02 has 145 rows total, 10:00-12:00 = 25 rows (inclusive), so ~120 kept.
        assert 115 < wb02_count < 130, f"WB02-INV05 unexpected count: {wb02_count}"
        # WB05-INV01 fully kept
        assert (df_out["Inverter_ID"] == "WB05-INV01").sum() == 145
        print("[baseline] all assertions passed")

    print("[baseline] smoke OK")
