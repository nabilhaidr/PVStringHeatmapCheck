"""M2IForest: Isolation Forest per-inverter anomaly detection (Fase 3 Part 2 Task #2).

Sklearn-based UNSUPERVISED anomaly detection. Per inverter, train one
``IsolationForest`` model on per-(PV, timestamp) feature vectors and flag
the bottom ``contamination`` fraction as anomalies.

Features per (inverter, PV, timestamp) triplet
----------------------------------------------
    V       : input voltage(V)
    I       : input current(A)
    V_dev   : V - median(V across sibling PVs same inverter same timestamp)
    I_dev   : I - median(I across siblings)
    R       : V / max(I, 0.1)   (apparent string resistance proxy)

Per inverter, samples = N_pv_active * N_daylight_timestamps. Train
``IsolationForest(contamination=cfg["contamination"], random_state=...)``
on this matrix; ``predict`` returns -1 for anomalies. Severity is mapped
from the quartile of the (negative) anomaly score within the flagged set
of that inverter.

Gating reuses the pattern from ``peer_zscore`` (POA threshold +
``poa_floor`` sanity + ``solar_elevation`` filter w/ defensive
``hour_cutoff`` AND + ``Inverter shutdown time`` sentinel guard) so the
detector only fits / predicts on legitimate daylight samples.

Config (default in ``DEFAULT_M2_CONFIG["m2_iforest"]``)
--------------------------------------------------------
    enabled              : False    (opt-in like Wave 9 Hampel)
    contamination        : 0.01     (1% bottom flagged)
    n_estimators         : 100
    random_state         : 42
    min_daylight_samples : 30       (need decent training set per inverter)
    poa_threshold_wm2    : 50.0     (low gate -- iforest learns from broader daylight)
    pv_max               : 28
    include_r_string     : True
    include_sibling_dev  : True

Default OFF so backwards-compat is preserved; user toggles ON in
``config/m2_config.yaml`` or notebook to engage. Mirrors Wave 9 pattern.

Outputs
-------
    Findings : one M2Finding per anomalous (inverter, PV, timestamp).
               fault_type = "iforest_anomaly".
               severity   = CRITICAL / HIGH / MEDIUM / INFO from score quartile.
               value      = anomaly score (more negative = more anomalous).
               threshold  = max score within the flagged set (decision boundary).
               evidence   = {V, I, V_dev, I_dev, R, score, percentile}.

    artifacts["AnomalyScores"]  : full DataFrame of all (inverter, PV, ts, score, flag).
    artifacts["AnomalySummary"] : top anomalies per inverter (sorted by score).
"""
from __future__ import annotations

import warnings
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from pv_pipeline.core import M2Finding, Severity, SubModule, load_empty_pv_map


# --- Defaults (mirror DEFAULT_M2_CONFIG["m2_iforest"]) ----------------------
DEFAULT_ENABLED: bool = False
DEFAULT_CONTAMINATION: float = 0.01
DEFAULT_N_ESTIMATORS: int = 100
DEFAULT_RANDOM_STATE: int = 42
DEFAULT_MIN_DAYLIGHT_SAMPLES: int = 30
DEFAULT_POA_THRESHOLD_WM2: float = 50.0
DEFAULT_POA_FLOOR_WM2: float = 50.0
DEFAULT_HOUR_CUTOFF_END: float = 18.0
DEFAULT_SOLAR_ELEV_MIN_DEG: float = 5.0
DEFAULT_RESPECT_INVERTER_SHUTDOWN: bool = True
DEFAULT_PV_MAX: int = 28
DEFAULT_INCLUDE_R_STRING: bool = True
DEFAULT_INCLUDE_SIBLING_DEV: bool = True

# Voltage/current column candidates (Huawei xlsx schema). Match peer_zscore
# pattern: lowercase canonical after Wave 11 hotfix #11 normalization.
PV_V_COL_TEMPLATE: str = "PV{pv} input voltage(V)"
PV_I_COL_TEMPLATE: str = "PV{pv} input current(A)"

# Inverter shutdown sentinel guard (same as peer_zscore).
INVERTER_SHUTDOWN_COL_CANDIDATES: List[str] = [
    "Inverter shutdown time",
    "Shutdown time",
]


# --- Utilities ---------------------------------------------------------------
def _ensure_sklearn() -> None:
    """Auto-install scikit-learn on first use (mirror ``_ensure_pvlib`` pattern)."""
    try:
        import sklearn  # noqa: F401
    except ImportError:  # pragma: no cover
        import subprocess
        import sys
        print("Installing scikit-learn for M2IForest detector...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "scikit-learn"]
        )


def _find_shutdown_col(df: pd.DataFrame) -> Optional[str]:
    for cand in INVERTER_SHUTDOWN_COL_CANDIDATES:
        if cand in df.columns:
            return cand
    return None


def _wb_from_inverter_id(inverter_id: str) -> str:
    """``WB05-INV12`` -> ``"WB05"``."""
    if not inverter_id:
        return ""
    parts = str(inverter_id).split("-")
    return parts[0].upper() if parts else str(inverter_id).upper()


def _normalize_pv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Wave 11 hotfix #11 mirror: normalize Title Case V/I cols to lowercase."""
    rename_map = {}
    for col in df.columns:
        if "Input Voltage" in col:
            rename_map[col] = col.replace("Input Voltage", "input voltage")
        elif "Input Current" in col:
            rename_map[col] = col.replace("Input Current", "input current")
    if rename_map:
        return df.rename(columns=rename_map)
    return df


def build_feature_matrix(
    group_clean: pd.DataFrame,
    mask: pd.Series,
    pv_indices: List[int],
    *,
    include_r_string: bool = True,
    include_sibling_dev: bool = True,
) -> Tuple[np.ndarray, List[Tuple[int, pd.Timestamp]]]:
    """Build (X, sample_keys) for one inverter group.

    Returns
    -------
    X : np.ndarray, shape (N_samples, n_features)
        n_features = 2 (V, I) + 2*include_sibling_dev + 1*include_r_string.
    sample_keys : list of (pv_n, timestamp)
        One entry per row of X. Used to map IsolationForest predictions back
        to (PV, ts) for finding emission.
    """
    rows: List[np.ndarray] = []
    keys: List[Tuple[int, pd.Timestamp]] = []

    if mask.sum() == 0 or not pv_indices:
        n_feat = 2 + (2 if include_sibling_dev else 0) + (1 if include_r_string else 0)
        return np.empty((0, n_feat), dtype=float), []

    masked = group_clean.loc[mask]
    timestamps = masked.index

    # Pre-extract V and I matrices per PV: shape (N_ts, N_pv).
    v_mat = np.full((len(masked), len(pv_indices)), np.nan, dtype=float)
    i_mat = np.full((len(masked), len(pv_indices)), np.nan, dtype=float)
    for j, pv in enumerate(pv_indices):
        v_col = PV_V_COL_TEMPLATE.format(pv=pv)
        i_col = PV_I_COL_TEMPLATE.format(pv=pv)
        if v_col in masked.columns:
            v_mat[:, j] = pd.to_numeric(masked[v_col], errors="coerce").to_numpy()
        if i_col in masked.columns:
            i_mat[:, j] = pd.to_numeric(masked[i_col], errors="coerce").to_numpy()

    # Per-timestamp medians across siblings (used for deviation features).
    v_med_per_ts = np.nanmedian(v_mat, axis=1)  # shape (N_ts,)
    i_med_per_ts = np.nanmedian(i_mat, axis=1)

    for t_idx in range(len(masked)):
        ts = timestamps[t_idx]
        for j, pv in enumerate(pv_indices):
            v = v_mat[t_idx, j]
            i = i_mat[t_idx, j]
            if not (np.isfinite(v) and np.isfinite(i)):
                continue
            feat = [v, i]
            if include_sibling_dev:
                v_med = v_med_per_ts[t_idx]
                i_med = i_med_per_ts[t_idx]
                v_dev = v - v_med if np.isfinite(v_med) else 0.0
                i_dev = i - i_med if np.isfinite(i_med) else 0.0
                feat.extend([v_dev, i_dev])
            if include_r_string:
                r = v / max(i, 0.1)
                feat.append(r)
            rows.append(np.asarray(feat, dtype=float))
            keys.append((pv, ts))

    if not rows:
        n_feat = 2 + (2 if include_sibling_dev else 0) + (1 if include_r_string else 0)
        return np.empty((0, n_feat), dtype=float), []

    return np.vstack(rows), keys


def _severity_from_quartile(rank_pct: float) -> Severity:
    """Map within-flagged-set percentile (0..100) to Severity.

    rank_pct = 0   -> most anomalous (lowest score)   -> CRITICAL
    rank_pct = 100 -> least anomalous in flagged set  -> INFO
    """
    if rank_pct <= 25.0:
        return Severity.CRITICAL
    if rank_pct <= 50.0:
        return Severity.HIGH
    if rank_pct <= 75.0:
        return Severity.MEDIUM
    return Severity.INFO


class M2IForest(SubModule):
    """Isolation Forest per-inverter anomaly detector.

    Dependency injection (optional):
        prov = POAProvider.from_yaml(...)
        sm = M2IForest(poa=prov)
    """

    name: str = "M2_iforest"

    def __init__(self, poa=None):
        super().__init__()
        self.poa = poa

    def _ensure_providers(self, config: dict) -> None:
        if self.poa is None:
            from pv_pipeline.poa.provider import POAProvider
            geom_path = (
                config.get("poa", {})
                .get("site_geometry_path", "config/site_geometry.yaml")
            )
            self.poa = POAProvider.from_yaml(geom_path)

    def _build_gate_mask(
        self,
        group_clean: pd.DataFrame,
        ts_clean: pd.DatetimeIndex,
        wb_id: str,
        cfg: dict,
        shutdown_col: Optional[str],
    ) -> pd.Series:
        """Compute daylight gate mask (POA + solar_elev + shutdown)."""
        poa_threshold = float(cfg.get("poa_threshold_wm2", DEFAULT_POA_THRESHOLD_WM2))
        poa_floor = float(cfg.get("poa_floor_wm2", DEFAULT_POA_FLOOR_WM2))
        hour_cutoff_end = float(cfg.get("hour_cutoff_end", DEFAULT_HOUR_CUTOFF_END))
        solar_elev_min = float(cfg.get("solar_elevation_min_deg", DEFAULT_SOLAR_ELEV_MIN_DEG))
        respect_shutdown = bool(cfg.get(
            "respect_inverter_shutdown", DEFAULT_RESPECT_INVERTER_SHUTDOWN
        ))

        # POA gate
        try:
            poa = self.poa.get_poa(ts_clean, wb_id, source="auto")
        except Exception as exc:
            warnings.warn(
                f"[M2IForest] POA query failed (wb={wb_id}): "
                f"{exc.__class__.__name__}: {exc}. Skipping inverter.",
                stacklevel=2,
            )
            return pd.Series(False, index=ts_clean)

        poa_aligned = poa.reindex(ts_clean).fillna(0.0)
        mask_poa = (poa_aligned > poa_threshold) & (poa_aligned > poa_floor)

        # Solar elevation (defensive AND with hour_cutoff per Wave 11 hotfix #7).
        try:
            elev = self.poa.get_solar_elevation(ts_clean)
            elev_aligned = elev.reindex(ts_clean)
            if elev_aligned.notna().any():
                elev_mask = elev_aligned.fillna(-90.0).values > solar_elev_min
                hour_arr = ts_clean.hour + ts_clean.minute / 60.0
                hour_mask = hour_arr < hour_cutoff_end
                mask_time = pd.Series(elev_mask & hour_mask, index=ts_clean)
            else:
                hour_arr = ts_clean.hour + ts_clean.minute / 60.0
                mask_time = pd.Series(hour_arr < hour_cutoff_end, index=ts_clean)
        except Exception:
            hour_arr = ts_clean.hour + ts_clean.minute / 60.0
            mask_time = pd.Series(hour_arr < hour_cutoff_end, index=ts_clean)

        # Shutdown gate (Wave 11 hotfix #5/#6 sentinel guard).
        mask_shutdown = pd.Series(True, index=ts_clean)
        if respect_shutdown and shutdown_col is not None and shutdown_col in group_clean.columns:
            raw_shut = pd.to_datetime(group_clean[shutdown_col], errors="coerce")
            valid_shut = raw_shut.dropna()
            if not valid_shut.empty:
                valid_shut = valid_shut[valid_shut.dt.year >= 2000]
            if not valid_shut.empty:
                shutdown_ts = valid_shut.min()
                proposed = pd.Series(ts_clean < shutdown_ts, index=ts_clean)
                if proposed.sum() > 0:
                    mask_shutdown = proposed

        return mask_poa & mask_time & mask_shutdown

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        cfg = config.get("m2_iforest", {}) or {}
        enabled = bool(cfg.get("enabled", DEFAULT_ENABLED))
        if not enabled:
            return []  # opt-in, mirror Wave 9 default OFF

        contamination = float(cfg.get("contamination", DEFAULT_CONTAMINATION))
        n_estimators = int(cfg.get("n_estimators", DEFAULT_N_ESTIMATORS))
        random_state = int(cfg.get("random_state", DEFAULT_RANDOM_STATE))
        min_daylight_samples = int(cfg.get(
            "min_daylight_samples", DEFAULT_MIN_DAYLIGHT_SAMPLES
        ))
        pv_max = int(cfg.get("pv_max", DEFAULT_PV_MAX))
        include_r = bool(cfg.get("include_r_string", DEFAULT_INCLUDE_R_STRING))
        include_dev = bool(cfg.get("include_sibling_dev", DEFAULT_INCLUDE_SIBLING_DEV))

        if "Inverter_ID" not in combined_df.columns or "Start Time" not in combined_df.columns:
            warnings.warn(
                "[M2IForest] missing 'Inverter_ID' or 'Start Time'; skipping.",
                stacklevel=2,
            )
            return []

        _ensure_sklearn()
        from sklearn.ensemble import IsolationForest  # noqa: WPS433

        combined_df = _normalize_pv_columns(combined_df)
        shutdown_col = _find_shutdown_col(combined_df)
        empty_map = load_empty_pv_map(config)

        # Lazy POA provider (may fail in unit tests w/o yaml; gate will skip).
        try:
            self._ensure_providers(config)
        except Exception as exc:
            warnings.warn(
                f"[M2IForest] POA provider init failed: {exc.__class__.__name__}: {exc}. "
                "Detector requires POA gate; skipping.",
                stacklevel=2,
            )
            return []

        findings: List[M2Finding] = []
        all_score_rows: List[dict] = []
        summary_rows: List[dict] = []

        for inverter_id, group in combined_df.groupby("Inverter_ID"):
            wb_id = _wb_from_inverter_id(inverter_id)
            inv_empties = set(int(n) for n in empty_map.get(str(inverter_id).upper(), []))
            pv_indices = [n for n in range(1, pv_max + 1) if n not in inv_empties]

            timestamps = pd.to_datetime(group["Start Time"], errors="coerce")
            valid_idx = timestamps.notna()
            if valid_idx.sum() < min_daylight_samples:
                continue
            ts_clean = pd.DatetimeIndex(timestamps[valid_idx].values)
            group_clean = group.loc[valid_idx].copy()
            group_clean.index = ts_clean

            mask = self._build_gate_mask(group_clean, ts_clean, wb_id, cfg, shutdown_col)
            if mask.sum() < min_daylight_samples:
                continue

            X, keys = build_feature_matrix(
                group_clean,
                mask,
                pv_indices,
                include_r_string=include_r,
                include_sibling_dev=include_dev,
            )
            if X.shape[0] < min_daylight_samples:
                continue

            # Train + score
            iforest = IsolationForest(
                contamination=contamination,
                n_estimators=n_estimators,
                random_state=random_state,
                n_jobs=1,
            )
            try:
                iforest.fit(X)
            except Exception as exc:
                warnings.warn(
                    f"[M2IForest] IsolationForest fit failed (inv={inverter_id}): "
                    f"{exc.__class__.__name__}: {exc}",
                    stacklevel=2,
                )
                continue

            scores = iforest.score_samples(X)        # higher = more normal
            preds = iforest.predict(X)               # -1 = anomaly, +1 = inlier
            flagged_mask = preds == -1
            n_flagged = int(flagged_mask.sum())

            # Decision boundary score = max(score) among flagged (or NaN if empty).
            if n_flagged > 0:
                flagged_scores = scores[flagged_mask]
                threshold_score = float(flagged_scores.max())
                # Rank within flagged set (0 = most anomalous, 100 = least).
                # Lower score = more anomalous; argsort ascending.
                sort_order = np.argsort(flagged_scores)
                rank_within = np.empty_like(sort_order)
                rank_within[sort_order] = np.arange(n_flagged)
                if n_flagged > 1:
                    rank_pct = (rank_within / (n_flagged - 1)) * 100.0
                else:
                    rank_pct = np.zeros(n_flagged)
            else:
                threshold_score = float("nan")
                rank_pct = np.array([])

            # Score artifact rows (ALL samples, not just flagged).
            for k_idx, (pv, ts) in enumerate(keys):
                v = float(X[k_idx, 0])
                i = float(X[k_idx, 1])
                feat_offset = 2
                v_dev = float(X[k_idx, feat_offset]) if include_dev else float("nan")
                i_dev = float(X[k_idx, feat_offset + 1]) if include_dev else float("nan")
                feat_offset += 2 if include_dev else 0
                r = float(X[k_idx, feat_offset]) if include_r else float("nan")
                all_score_rows.append({
                    "inverter_id": inverter_id,
                    "pv_string": f"PV{pv}",
                    "timestamp": ts,
                    "score": float(scores[k_idx]),
                    "flag": "anomaly" if flagged_mask[k_idx] else "normal",
                    "V": v,
                    "I": i,
                    "V_dev": v_dev,
                    "I_dev": i_dev,
                    "R": r,
                })

            # Emit findings for flagged samples only.
            flagged_indices = np.where(flagged_mask)[0]
            for rel_idx, abs_idx in enumerate(flagged_indices):
                pv, ts = keys[abs_idx]
                score = float(scores[abs_idx])
                pct = float(rank_pct[rel_idx]) if n_flagged > 0 else 0.0
                sev = _severity_from_quartile(pct)
                v = float(X[abs_idx, 0])
                i = float(X[abs_idx, 1])
                feat_offset = 2
                v_dev = float(X[abs_idx, feat_offset]) if include_dev else float("nan")
                i_dev = float(X[abs_idx, feat_offset + 1]) if include_dev else float("nan")
                feat_offset += 2 if include_dev else 0
                r = float(X[abs_idx, feat_offset]) if include_r else float("nan")
                # Confidence: 0..100 monotonic with anomaly intensity.
                #   pct=0 (most anomalous) -> 100. pct=100 (least) -> 50.
                confidence = float(100.0 - pct * 0.5)
                findings.append(M2Finding(
                    timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                    inverter_id=str(inverter_id),
                    pv_string=f"PV{pv}",
                    sub_module=self.name,
                    severity=sev,
                    value=score,
                    threshold=threshold_score,
                    message=(
                        f"IsolationForest anomaly (PV{pv} @ {ts.isoformat()}); "
                        f"score={score:.4f}, pct_within_flagged={pct:.1f}%"
                    ),
                    fault_type="iforest_anomaly",
                    confidence=confidence,
                    evidence={
                        "V": v, "I": i, "V_dev": v_dev, "I_dev": i_dev, "R": r,
                        "score": score, "percentile_within_flagged": pct,
                        "contamination": contamination,
                    },
                ))

            # Summary row per inverter.
            summary_rows.append({
                "inverter_id": inverter_id,
                "n_samples": int(X.shape[0]),
                "n_flagged": n_flagged,
                "flagged_pct": (n_flagged / X.shape[0]) * 100.0 if X.shape[0] > 0 else 0.0,
                "min_score": float(scores.min()) if scores.size else float("nan"),
                "threshold_score": threshold_score,
                "contamination": contamination,
            })

        # Emit artifacts.
        if all_score_rows:
            self.artifacts["AnomalyScores"] = pd.DataFrame(all_score_rows)
        if summary_rows:
            self.artifacts["AnomalySummary"] = pd.DataFrame(summary_rows)

        return findings


if __name__ == "__main__":  # pragma: no cover
    # Tiny smoke test (no POA provider; manually-built feature matrix).
    rng = np.random.default_rng(0)
    X = rng.normal(0, 1, size=(200, 5))
    X[0] = [100, 100, 100, 100, 100]  # injected outlier
    _ensure_sklearn()
    from sklearn.ensemble import IsolationForest
    iforest = IsolationForest(contamination=0.01, random_state=42, n_jobs=1)
    iforest.fit(X)
    preds = iforest.predict(X)
    print(f"[iforest] smoke: n_anomalies={int((preds == -1).sum())} / 200 (expect ~2)")
    assert preds[0] == -1, "injected outlier should be flagged"
    print("[iforest] smoke OK")
