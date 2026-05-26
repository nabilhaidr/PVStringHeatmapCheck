"""Tests for ``pv_pipeline.iforest`` (M2IForest detector, Fase 3 Part 2 Task #2)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.core import M2Finding, Severity
from pv_pipeline.iforest import (
    DEFAULT_CONTAMINATION,
    DEFAULT_ENABLED,
    M2IForest,
    _find_shutdown_col,
    _normalize_pv_columns,
    _severity_from_quartile,
    _wb_from_inverter_id,
    build_feature_matrix,
)


# ============================================================================
# Helpers / config fixtures
# ============================================================================


@pytest.fixture
def iforest_cfg(m2_config_minimal):
    """Extend ``m2_config_minimal`` with an enabled ``m2_iforest`` section."""
    cfg = dict(m2_config_minimal)
    cfg["m2_iforest"] = {
        "enabled": True,
        "contamination": 0.05,        # higher for synthetic data sensitivity
        "n_estimators": 50,           # smaller for test speed
        "random_state": 42,
        "min_daylight_samples": 10,
        "poa_threshold_wm2": 50.0,
        "poa_floor_wm2": 50.0,
        "hour_cutoff_end": 18.0,
        "solar_elevation_min_deg": 5.0,
        "respect_inverter_shutdown": False,
        "pv_max": 10,
        "include_r_string": True,
        "include_sibling_dev": True,
    }
    return cfg


@pytest.fixture
def iforest_cfg_disabled(m2_config_minimal):
    """Cfg with ``m2_iforest.enabled=False`` (opt-in default)."""
    cfg = dict(m2_config_minimal)
    cfg["m2_iforest"] = {"enabled": False}
    return cfg


# ============================================================================
# Pure-utility tests (no POA, no sklearn fit)
# ============================================================================


class TestSeverityFromQuartile:
    def test_critical_at_zero(self):
        assert _severity_from_quartile(0.0) == Severity.CRITICAL

    def test_critical_at_25(self):
        assert _severity_from_quartile(25.0) == Severity.CRITICAL

    def test_high_just_above_25(self):
        assert _severity_from_quartile(25.01) == Severity.HIGH

    def test_high_at_50(self):
        assert _severity_from_quartile(50.0) == Severity.HIGH

    def test_medium_at_75(self):
        assert _severity_from_quartile(75.0) == Severity.MEDIUM

    def test_info_above_75(self):
        assert _severity_from_quartile(80.0) == Severity.INFO

    def test_info_at_100(self):
        assert _severity_from_quartile(100.0) == Severity.INFO


class TestWbFromInverterId:
    def test_standard_format(self):
        assert _wb_from_inverter_id("WB05-INV12") == "WB05"

    def test_lowercase_input(self):
        assert _wb_from_inverter_id("wb02-inv05") == "WB02"

    def test_empty(self):
        assert _wb_from_inverter_id("") == ""

    def test_no_dash(self):
        assert _wb_from_inverter_id("WB10") == "WB10"


class TestFindShutdownCol:
    def test_canonical_name(self):
        df = pd.DataFrame({"Inverter shutdown time": [], "X": []})
        assert _find_shutdown_col(df) == "Inverter shutdown time"

    def test_short_alt_name(self):
        df = pd.DataFrame({"Shutdown time": [], "X": []})
        assert _find_shutdown_col(df) == "Shutdown time"

    def test_missing(self):
        df = pd.DataFrame({"X": [], "Y": []})
        assert _find_shutdown_col(df) is None


class TestNormalizePvColumns:
    def test_title_case_to_lowercase(self):
        df = pd.DataFrame({
            "PV15 Input Voltage(V)": [1.0],
            "PV15 Input Current(A)": [2.0],
            "PV1 input voltage(V)": [3.0],
        })
        out = _normalize_pv_columns(df)
        assert "PV15 input voltage(V)" in out.columns
        assert "PV15 input current(A)" in out.columns
        assert "PV15 Input Voltage(V)" not in out.columns
        # already-lowercase column preserved untouched
        assert "PV1 input voltage(V)" in out.columns

    def test_no_changes_returns_same_columns(self):
        df = pd.DataFrame({"PV1 input voltage(V)": [1.0], "PV1 input current(A)": [2.0]})
        out = _normalize_pv_columns(df)
        assert list(out.columns) == list(df.columns)


# ============================================================================
# Feature matrix construction
# ============================================================================


def _make_synthetic_group(n_ts: int = 20, n_pv: int = 5):
    """Return (group_df indexed by timestamp, all-True mask, pv_indices)."""
    ts = pd.date_range("2026-05-14 09:00", periods=n_ts, freq="5min")
    data = {}
    rng = np.random.default_rng(0)
    for pv in range(1, n_pv + 1):
        data[f"PV{pv} input voltage(V)"] = 1200.0 + rng.normal(0, 5, n_ts)
        data[f"PV{pv} input current(A)"] = 10.0 + rng.normal(0, 0.1, n_ts)
    df = pd.DataFrame(data, index=ts)
    mask = pd.Series(True, index=ts)
    return df, mask, list(range(1, n_pv + 1))


class TestBuildFeatureMatrix:
    def test_full_features_shape(self):
        df, mask, pv_idx = _make_synthetic_group(n_ts=20, n_pv=5)
        X, keys = build_feature_matrix(df, mask, pv_idx,
                                       include_r_string=True, include_sibling_dev=True)
        # 20 timestamps * 5 PVs = 100 samples; 5 features each
        assert X.shape == (100, 5), f"got {X.shape}"
        assert len(keys) == 100

    def test_no_r_string_shape(self):
        df, mask, pv_idx = _make_synthetic_group(n_ts=10, n_pv=3)
        X, keys = build_feature_matrix(df, mask, pv_idx,
                                       include_r_string=False, include_sibling_dev=True)
        # 10 ts * 3 PVs = 30 samples; 4 features (V, I, V_dev, I_dev)
        assert X.shape == (30, 4)

    def test_no_sibling_dev_shape(self):
        df, mask, pv_idx = _make_synthetic_group(n_ts=10, n_pv=3)
        X, keys = build_feature_matrix(df, mask, pv_idx,
                                       include_r_string=True, include_sibling_dev=False)
        # 10 ts * 3 PVs = 30 samples; 3 features (V, I, R)
        assert X.shape == (30, 3)

    def test_minimal_features_shape(self):
        df, mask, pv_idx = _make_synthetic_group(n_ts=5, n_pv=2)
        X, keys = build_feature_matrix(df, mask, pv_idx,
                                       include_r_string=False, include_sibling_dev=False)
        # 5 ts * 2 PVs = 10 samples; 2 features (V, I)
        assert X.shape == (10, 2)

    def test_skips_nan_rows(self):
        df, mask, pv_idx = _make_synthetic_group(n_ts=10, n_pv=3)
        # Inject NaN at (ts=0, PV1) -- should drop that single sample
        df.iloc[0, 0] = np.nan  # PV1 input voltage(V) at ts=0
        X, keys = build_feature_matrix(df, mask, pv_idx)
        # Normally 30 samples; one dropped -> 29
        assert X.shape[0] == 29
        # The dropped key (PV1, ts0) should not be in keys
        assert (1, df.index[0]) not in keys

    def test_empty_mask_returns_empty(self):
        df, _, pv_idx = _make_synthetic_group(n_ts=10, n_pv=3)
        empty_mask = pd.Series(False, index=df.index)
        X, keys = build_feature_matrix(df, empty_mask, pv_idx)
        assert X.shape[0] == 0
        assert X.shape[1] == 5   # full feature dim preserved
        assert keys == []

    def test_no_pv_indices_returns_empty(self):
        df, mask, _ = _make_synthetic_group(n_ts=10, n_pv=3)
        X, keys = build_feature_matrix(df, mask, [])
        assert X.shape[0] == 0
        assert keys == []

    def test_r_value_correct(self):
        """R = V / max(I, 0.1)."""
        ts = pd.date_range("2026-05-14 12:00", periods=2, freq="5min")
        df = pd.DataFrame({
            "PV1 input voltage(V)": [1200.0, 1200.0],
            "PV1 input current(A)": [10.0, 0.05],  # second below floor
        }, index=ts)
        mask = pd.Series(True, index=ts)
        X, _ = build_feature_matrix(df, mask, [1],
                                    include_r_string=True, include_sibling_dev=False)
        # R[0] = 1200/10 = 120
        # R[1] = 1200/max(0.05, 0.1) = 1200/0.1 = 12000
        assert X[0, 2] == pytest.approx(120.0)
        assert X[1, 2] == pytest.approx(12000.0)

    def test_sibling_dev_computation(self):
        """V_dev = V - median(V across siblings at same ts)."""
        ts = pd.date_range("2026-05-14 12:00", periods=1, freq="5min")
        df = pd.DataFrame({
            "PV1 input voltage(V)": [1200.0],
            "PV2 input voltage(V)": [1210.0],
            "PV3 input voltage(V)": [1220.0],
            "PV1 input current(A)": [10.0],
            "PV2 input current(A)": [10.5],
            "PV3 input current(A)": [11.0],
        }, index=ts)
        mask = pd.Series(True, index=ts)
        X, keys = build_feature_matrix(df, mask, [1, 2, 3],
                                       include_r_string=False, include_sibling_dev=True)
        # Median V = 1210, Median I = 10.5
        # PV1: V_dev = -10, I_dev = -0.5
        # PV2: V_dev = 0, I_dev = 0
        # PV3: V_dev = 10, I_dev = 0.5
        assert X.shape == (3, 4)
        # Order: (PV1, PV2, PV3) within timestamp 0
        assert X[0, 2] == pytest.approx(-10.0)   # PV1 V_dev
        assert X[1, 2] == pytest.approx(0.0)     # PV2 V_dev
        assert X[2, 2] == pytest.approx(10.0)    # PV3 V_dev


# ============================================================================
# M2IForest.run() integration tests
# ============================================================================


class TestM2IForestRunDefaults:
    def test_default_disabled_returns_empty(self, synthetic_combined_df, iforest_cfg_disabled, mock_poa):
        sm = M2IForest(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, iforest_cfg_disabled)
        assert findings == []
        assert sm.artifacts == {}

    def test_no_m2_iforest_section_returns_empty(self, synthetic_combined_df, m2_config_minimal, mock_poa):
        # cfg missing m2_iforest section entirely -> reads {} and enabled=False
        sm = M2IForest(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, m2_config_minimal)
        assert findings == []

    def test_default_enabled_flag_is_false(self):
        assert DEFAULT_ENABLED is False

    def test_default_contamination_is_one_percent(self):
        assert DEFAULT_CONTAMINATION == 0.01


class TestM2IForestRunBasic:
    def test_emits_findings_on_normal_data(self, synthetic_combined_df, iforest_cfg, mock_poa):
        """Synthetic uniform data -> ~5% (contamination) of daylight samples flagged."""
        sm = M2IForest(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, iforest_cfg)
        # 3 inverters x 10 PVs x ~145 daylight ts -> ~4000+ samples total
        # With contamination=0.05, ~200 findings expected. Allow wide range.
        assert len(findings) > 0
        assert len(findings) < 1500   # sanity upper bound

    def test_findings_have_iforest_fault_type(self, synthetic_combined_df, iforest_cfg, mock_poa):
        sm = M2IForest(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, iforest_cfg)
        assert len(findings) > 0
        for f in findings[:10]:
            assert f.fault_type == "iforest_anomaly"
            assert f.sub_module == "M2_iforest"
            assert f.pv_string is not None
            assert f.pv_string.startswith("PV")

    def test_findings_severity_is_valid_enum(self, synthetic_combined_df, iforest_cfg, mock_poa):
        sm = M2IForest(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, iforest_cfg)
        valid_sevs = {Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.INFO}
        for f in findings:
            assert f.severity in valid_sevs

    def test_findings_confidence_in_range(self, synthetic_combined_df, iforest_cfg, mock_poa):
        sm = M2IForest(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, iforest_cfg)
        for f in findings:
            assert f.confidence is not None
            assert 50.0 <= f.confidence <= 100.0

    def test_findings_evidence_populated(self, synthetic_combined_df, iforest_cfg, mock_poa):
        sm = M2IForest(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, iforest_cfg)
        assert len(findings) > 0
        ev = findings[0].evidence
        assert ev is not None
        for key in ("V", "I", "V_dev", "I_dev", "R", "score", "contamination"):
            assert key in ev, f"missing key {key} in evidence"

    def test_emits_anomaly_scores_artifact(self, synthetic_combined_df, iforest_cfg, mock_poa):
        sm = M2IForest(poa=mock_poa)
        sm.run(synthetic_combined_df, iforest_cfg)
        assert "AnomalyScores" in sm.artifacts
        df = sm.artifacts["AnomalyScores"]
        # 3 inverters x 10 PVs x ~145 daylight ts but POA-gated -> non-zero
        assert len(df) > 0
        for col in ("inverter_id", "pv_string", "timestamp", "score", "flag",
                    "V", "I", "V_dev", "I_dev", "R"):
            assert col in df.columns

    def test_emits_anomaly_summary_artifact(self, synthetic_combined_df, iforest_cfg, mock_poa):
        sm = M2IForest(poa=mock_poa)
        sm.run(synthetic_combined_df, iforest_cfg)
        assert "AnomalySummary" in sm.artifacts
        df = sm.artifacts["AnomalySummary"]
        # One row per inverter (3 inverters)
        assert len(df) == 3
        for col in ("inverter_id", "n_samples", "n_flagged", "flagged_pct",
                    "min_score", "threshold_score", "contamination"):
            assert col in df.columns


class TestM2IForestOutlierDetection:
    def test_flags_pv3_outlier_inverter(self, synthetic_combined_df_with_outlier, iforest_cfg, mock_poa):
        """PV3 di WB05-INV01 punya high_R signature -> iforest should flag it."""
        sm = M2IForest(poa=mock_poa)
        findings = sm.run(synthetic_combined_df_with_outlier, iforest_cfg)
        # PV3 in WB05-INV01 should be heavily represented among flagged samples
        pv3_inv01 = [
            f for f in findings
            if f.inverter_id == "WB05-INV01" and f.pv_string == "PV3"
        ]
        assert len(pv3_inv01) > 0, "PV3 outlier should be flagged at least once"


class TestM2IForestEdgeCases:
    def test_missing_inverter_id_returns_empty(self, iforest_cfg, mock_poa):
        df = pd.DataFrame({"Start Time": [pd.Timestamp("2026-05-14 12:00")]})
        sm = M2IForest(poa=mock_poa)
        findings = sm.run(df, iforest_cfg)
        assert findings == []

    def test_missing_start_time_returns_empty(self, iforest_cfg, mock_poa):
        df = pd.DataFrame({"Inverter_ID": ["WB01-INV01"]})
        sm = M2IForest(poa=mock_poa)
        findings = sm.run(df, iforest_cfg)
        assert findings == []

    def test_too_few_samples_skips_inverter(self, iforest_cfg, mock_poa):
        # Only 3 timestamps -- below min_daylight_samples=10
        ts = pd.date_range("2026-05-14 12:00", periods=3, freq="5min")
        rows = []
        for t in ts:
            row = {"Inverter_ID": "WB01-INV01", "Start Time": t}
            for pv in range(1, 11):
                row[f"PV{pv} input voltage(V)"] = 1200.0
                row[f"PV{pv} input current(A)"] = 10.0
            rows.append(row)
        df = pd.DataFrame(rows)
        sm = M2IForest(poa=mock_poa)
        findings = sm.run(df, iforest_cfg)
        assert findings == []

    def test_empty_dataframe_returns_empty(self, iforest_cfg, mock_poa):
        df = pd.DataFrame(columns=["Inverter_ID", "Start Time"])
        sm = M2IForest(poa=mock_poa)
        findings = sm.run(df, iforest_cfg)
        assert findings == []

    def test_empty_pv_map_excludes_pvs(self, synthetic_combined_df, iforest_cfg, mock_poa, tmp_path):
        """If config["m2e"]["empty_pv_map_path"] points to file with PV9 marked
        empty for an inverter, no PV9 findings should appear for that inverter."""
        strings_yaml = tmp_path / "strings.yaml"
        strings_yaml.write_text(
            "empty_pv_map:\n  WB05-INV01: [9, 10]\n",
            encoding="utf-8",
        )
        cfg = dict(iforest_cfg)
        cfg["m2e"] = {"empty_pv_map_path": str(strings_yaml)}
        sm = M2IForest(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, cfg)
        # No PV9 or PV10 findings in WB05-INV01
        pv9_inv01 = [
            f for f in findings
            if f.inverter_id == "WB05-INV01" and f.pv_string in ("PV9", "PV10")
        ]
        assert pv9_inv01 == [], \
            f"PV9/PV10 marked empty but {len(pv9_inv01)} findings emitted"


class TestM2IForestReproducibility:
    def test_same_random_state_same_findings(self, synthetic_combined_df, iforest_cfg, mock_poa):
        sm1 = M2IForest(poa=mock_poa)
        sm2 = M2IForest(poa=mock_poa)
        f1 = sm1.run(synthetic_combined_df, iforest_cfg)
        f2 = sm2.run(synthetic_combined_df, iforest_cfg)
        assert len(f1) == len(f2)
        # Compare scores element-wise (same random_state -> deterministic)
        scores1 = sorted(f.value for f in f1)
        scores2 = sorted(f.value for f in f2)
        assert scores1 == pytest.approx(scores2)
