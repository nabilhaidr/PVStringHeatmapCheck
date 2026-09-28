"""Tests for ``pv_pipeline.m2a.low_irradiance`` (Fase 3 Task #6)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2a.low_irradiance import (
    DEFAULT_ENABLED,
    DEFAULT_LOW_BAND_MIN_ELEVATION_DEG,
    DEFAULT_LOW_RATIO_THRESHOLD,
    DEFAULT_MIN_PEERS,
    DEFAULT_POA_LOW_RANGE,
    DEFAULT_POA_MID_RANGE,
    DEFAULT_ROBUST_Z_MIN,
    M2aLowIrradiance,
    _find_shutdown_col,
    _normalize_pv_columns,
    _wb_from_inverter_id,
    build_inverter_power_series,
)


# ============================================================================
# Config fixtures
# ============================================================================


@pytest.fixture
def low_irr_cfg(m2_config_minimal):
    """Extend m2_config_minimal with enabled m2a_low_irradiance section."""
    cfg = dict(m2_config_minimal)
    cfg["m2a_low_irradiance"] = {
        "enabled": True,
        "poa_low_range": [50.0, 250.0],
        "poa_mid_range": [300.0, 800.0],
        "min_low_samples": 10,        # lower for synthetic data
        "min_mid_samples": 10,
        "hour_range": [6.0, 18.0],
        "hour_cutoff_end": 18.0,
        "solar_elevation_min_deg": 5.0,
        "respect_inverter_shutdown": False,
        "pv_max": 10,
    }
    return cfg


@pytest.fixture
def low_irr_cfg_disabled(m2_config_minimal):
    """cfg with m2a_low_irradiance.enabled=False (opt-in default)."""
    cfg = dict(m2_config_minimal)
    cfg["m2a_low_irradiance"] = {"enabled": False}
    return cfg


# ============================================================================
# Pure-utility tests
# ============================================================================


class TestWbFromInverterId:
    def test_standard(self):
        assert _wb_from_inverter_id("WB05-INV12") == "WB05"

    def test_lowercase(self):
        assert _wb_from_inverter_id("wb02-inv05") == "WB02"

    def test_empty(self):
        assert _wb_from_inverter_id("") == ""


class TestFindShutdownCol:
    def test_canonical(self):
        df = pd.DataFrame({"Inverter shutdown time": []})
        assert _find_shutdown_col(df) == "Inverter shutdown time"

    def test_missing(self):
        df = pd.DataFrame({"X": []})
        assert _find_shutdown_col(df) is None


class TestNormalizePvColumns:
    def test_title_case_to_lowercase(self):
        df = pd.DataFrame({"PV15 Input Voltage(V)": [1.0]})
        out = _normalize_pv_columns(df)
        assert "PV15 input voltage(V)" in out.columns


# ============================================================================
# build_inverter_power_series
# ============================================================================


class TestBuildInverterPowerSeries:
    def test_v_i_fallback(self):
        df = pd.DataFrame({
            "PV1 input voltage(V)": [1000.0, 1200.0],
            "PV1 input current(A)": [10.0, 12.0],
            "PV2 input voltage(V)": [1100.0, 1100.0],
            "PV2 input current(A)": [11.0, 11.0],
        })
        p_inv = build_inverter_power_series(df, [1, 2])
        # PV1+PV2 t0: 10 + 12.1 = 22.1 kW
        # PV1+PV2 t1: 14.4 + 12.1 = 26.5 kW
        assert p_inv[0] == pytest.approx(22.1)
        assert p_inv[1] == pytest.approx(26.5)

    def test_prefer_power_col(self):
        df = pd.DataFrame({
            "PV1 Power(kW)": [5.0, 6.0],
            "PV1 input voltage(V)": [1000.0, 1200.0],
            "PV1 input current(A)": [10.0, 12.0],
        })
        p_inv = build_inverter_power_series(df, [1])
        assert p_inv[0] == 5.0
        assert p_inv[1] == 6.0

    def test_missing_returns_nan_sum_zero(self):
        df = pd.DataFrame({"X": [1.0, 2.0]})
        p_inv = build_inverter_power_series(df, [1])
        # All NaN -> nansum -> 0.0
        assert p_inv[0] == 0.0


# ============================================================================
# Module-level defaults
# ============================================================================


class TestDefaults:
    def test_default_enabled_false(self):
        assert DEFAULT_ENABLED is False

    def test_default_poa_low_range(self):
        assert DEFAULT_POA_LOW_RANGE == (50.0, 250.0)

    def test_default_poa_mid_range(self):
        assert DEFAULT_POA_MID_RANGE == (300.0, 800.0)

    def test_default_peer_relative_thresholds(self):
        assert DEFAULT_LOW_RATIO_THRESHOLD == 0.90
        assert DEFAULT_ROBUST_Z_MIN == 3.0
        assert DEFAULT_LOW_BAND_MIN_ELEVATION_DEG == 30.0
        assert DEFAULT_MIN_PEERS == 5


# ============================================================================
# Synthetic-data helpers
# ============================================================================


def _make_inverter_df(inverter_id: str, rs_high: bool = False, seed: int = 42):
    """Build combined_df for a single inverter spanning full daylight.

    rs_high=True simulates HIGH series resistance: at low POA, current
    output is depressed disproportionately. Resulting PR_proxy slope in
    low band becomes NEGATIVE (PR decreases as POA rises in low range).

    rs_high=False is HEALTHY: linear I vs POA -> PR_proxy roughly flat
    in low band, so slope >= 0.
    """
    rng = np.random.default_rng(seed)
    n = 145
    t = pd.date_range("2026-05-14 06:00", "2026-05-14 18:00", freq="5min")[:n]
    hours = np.linspace(0, 12, n)
    sun = np.sin(np.pi * hours / 12) ** 2
    poa_proxy = 1000.0 * sun

    rows = []
    for ts_i, ts in enumerate(t):
        row = {"Inverter_ID": inverter_id, "Start Time": ts}
        for pv_n in range(1, 6):
            if rs_high:
                if poa_proxy[ts_i] < 250:
                    # Sub-linear penalty at low POA (mimics high Rs)
                    I_factor = (poa_proxy[ts_i] / 1000.0) ** 0.7
                    I_pv = I_factor * 13.0 + rng.normal(0, 0.05)
                else:
                    I_pv = (poa_proxy[ts_i] / 1000.0) * 13.0 + rng.normal(0, 0.05)
            else:
                I_pv = (poa_proxy[ts_i] / 1000.0) * 13.0 + rng.normal(0, 0.05)
            V_pv = (1200.0 + 200.0 * np.exp(-3 * sun[ts_i])) * (
                1.0 + rng.normal(0, 0.001)
            )
            row[f"PV{pv_n} input voltage(V)"] = V_pv
            row[f"PV{pv_n} input current(A)"] = I_pv
        rows.append(row)
    return pd.DataFrame(rows)


# ============================================================================
# M2aLowIrradiance.run() integration tests
# ============================================================================


class TestM2aLowIrradianceRunDefaults:
    def test_default_disabled_returns_empty(
        self, synthetic_combined_df, low_irr_cfg_disabled, mock_poa
    ):
        sm = M2aLowIrradiance(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, low_irr_cfg_disabled)
        assert findings == []
        assert sm.artifacts == {}

    def test_no_m2a_low_irradiance_section_returns_empty(
        self, synthetic_combined_df, m2_config_minimal, mock_poa
    ):
        sm = M2aLowIrradiance(poa=mock_poa)
        findings = sm.run(synthetic_combined_df, m2_config_minimal)
        assert findings == []

    def test_missing_inverter_id_returns_empty(self, low_irr_cfg, mock_poa):
        df = pd.DataFrame({"Start Time": [pd.Timestamp("2026-05-14 12:00")]})
        sm = M2aLowIrradiance(poa=mock_poa)
        assert sm.run(df, low_irr_cfg) == []

    def test_missing_start_time_returns_empty(self, low_irr_cfg, mock_poa):
        df = pd.DataFrame({"Inverter_ID": ["WB01-INV01"]})
        sm = M2aLowIrradiance(poa=mock_poa)
        assert sm.run(df, low_irr_cfg) == []

    def test_empty_df_returns_empty(self, low_irr_cfg, mock_poa):
        df = pd.DataFrame(columns=["Inverter_ID", "Start Time"])
        sm = M2aLowIrradiance(poa=mock_poa)
        assert sm.run(df, low_irr_cfg) == []


class TestM2aLowIrradianceBasic:
    def test_artifact_fit_always_emitted(self, low_irr_cfg, mock_poa):
        """LowIrradianceFit artifact should always emit even when no findings."""
        df = _make_inverter_df("WB05-INV01", rs_high=False)
        sm = M2aLowIrradiance(poa=mock_poa)
        sm.run(df, low_irr_cfg)
        assert "LowIrradianceFit" in sm.artifacts
        fit = sm.artifacts["LowIrradianceFit"]
        for col in ("inverter_id", "day", "poa_source", "n_low_samples",
                    "n_mid_samples", "low_ratio", "wb_median_low_ratio",
                    "robust_z", "classification", "severity"):
            assert col in fit.columns

    def test_artifact_summary_emitted(self, low_irr_cfg, mock_poa):
        df = _make_inverter_df("WB05-INV01", rs_high=False)
        sm = M2aLowIrradiance(poa=mock_poa)
        sm.run(df, low_irr_cfg)
        assert "LowIrradianceSummary" in sm.artifacts
        summary = sm.artifacts["LowIrradianceSummary"]
        assert len(summary) == 1
        for col in ("normal", "low_irradiance_underperform",
                    "general_underperform", "skipped"):
            assert col in summary.columns

    def test_reproducible(self, low_irr_cfg, mock_poa):
        """Same input -> same output."""
        df = _make_inverter_df("WB05-INV01", rs_high=False)
        sm1 = M2aLowIrradiance(poa=mock_poa)
        sm2 = M2aLowIrradiance(poa=mock_poa)
        f1 = sm1.run(df, low_irr_cfg)
        f2 = sm2.run(df, low_irr_cfg)
        assert len(f1) == len(f2)


class _SourceRecordingPOA:
    """Bungkus mock POA; catat tiap ``source`` yang diminta detektor."""

    def __init__(self, inner):
        self.inner = inner
        self.sources = []

    def get_poa(self, timestamps, wb_id, source="auto"):
        self.sources.append(source)
        return self.inner.get_poa(timestamps, wb_id, source=source)


class TestM2aLowIrradiancePoaSource:
    def test_poa_source_comes_from_config_and_is_recorded(self, low_irr_cfg, mock_poa):
        # WHY: M2f memakai fit PR-proxy pita menengah sebagai counterfactual
        # low_irradiance_eff dan mengevaluasinya dengan POA terukur miliknya.
        # Fit yang dibangun di atas clear-sky ("auto") tidak sebanding, jadi
        # sumbernya harus bisa dipaksa dan tercatat supaya M2f menolaknya.
        cfg = dict(low_irr_cfg)
        cfg["m2a_low_irradiance"] = dict(
            low_irr_cfg["m2a_low_irradiance"], poa_source="pyranometer_per_ws",
        )
        poa = _SourceRecordingPOA(mock_poa)
        sm = M2aLowIrradiance(poa=poa)
        sm.run(_make_inverter_df("WB05-INV01"), cfg)
        assert set(poa.sources) == {"pyranometer_per_ws"}
        assert set(sm.artifacts["LowIrradianceFit"]["poa_source"]) == {"pyranometer_per_ws"}

    def test_poa_source_defaults_to_auto(self, low_irr_cfg, mock_poa):
        # WHY: daily_runfast dan config lama tidak memuat kunci ini; perilaku
        # mereka tidak boleh berubah diam-diam.
        poa = _SourceRecordingPOA(mock_poa)
        M2aLowIrradiance(poa=poa).run(_make_inverter_df("WB05-INV01"), low_irr_cfg)
        assert set(poa.sources) == {"auto"}


class TestM2aLowIrradianceConfigOverrides:
    def test_insufficient_samples_skipped(self, low_irr_cfg, mock_poa):
        """Inverter dengan < min_low_samples skipped."""
        ts = pd.date_range("2026-05-14 12:00", periods=3, freq="5min")
        rows = []
        for t in ts:
            row = {"Inverter_ID": "WB01-INV01", "Start Time": t}
            for pv in range(1, 6):
                row[f"PV{pv} input voltage(V)"] = 1200.0
                row[f"PV{pv} input current(A)"] = 10.0
            rows.append(row)
        df = pd.DataFrame(rows)
        cfg = dict(low_irr_cfg)
        cfg["m2a_low_irradiance"] = dict(low_irr_cfg["m2a_low_irradiance"])
        cfg["m2a_low_irradiance"]["min_low_samples"] = 100
        sm = M2aLowIrradiance(poa=mock_poa)
        findings = sm.run(df, cfg)
        assert findings == []
        if "LowIrradianceSummary" in sm.artifacts:
            assert sm.artifacts["LowIrradianceSummary"]["skipped"].iloc[0] >= 1


class TestM2aLowIrradianceMultipleInverters:
    def test_multi_inverter_processed(
        self, synthetic_combined_df, low_irr_cfg, mock_poa,
    ):
        """Fixture has 3 inverters -- all should get artifact rows."""
        sm = M2aLowIrradiance(poa=mock_poa)
        sm.run(synthetic_combined_df, low_irr_cfg)
        if "LowIrradianceFit" in sm.artifacts:
            fit = sm.artifacts["LowIrradianceFit"]
            # All 3 inverters should appear (even if skipped due to insufficient
            # low-band samples).
            assert fit["inverter_id"].nunique() == 3


class _DupSafeMockPOA:
    """Mock POA UNIQUE-indexed (mimics real POAProvider). Lihat
    test_shading.py untuk rationale -- replikasi real provider supaya
    reindex PASS dan kita exercise path .loc/.iloc yang di-fix 2026-06-01.
    """

    def get_poa(self, timestamps, wb_id, source="auto"):
        ts = pd.DatetimeIndex(timestamps)
        ts_u = ts[~ts.duplicated(keep="first")]
        hrs = (ts_u.hour - 6) + (ts_u.minute / 60.0)
        poa = np.where((hrs >= 0) & (hrs <= 12),
                       1000.0 * np.sin(np.pi * hrs / 12) ** 2, 0.0)
        return pd.Series(poa, index=ts_u)

    def get_solar_elevation(self, timestamps):
        ts = pd.DatetimeIndex(timestamps)
        ts_u = ts[~ts.duplicated(keep="first")]
        hrs = (ts_u.hour - 6) + (ts_u.minute / 60.0)
        elev = np.where((hrs >= 0) & (hrs <= 12),
                        85.0 * np.sin(np.pi * hrs / 12), -45.0)
        return pd.Series(elev, index=ts_u, name="solar_elevation_deg")


class TestM2aLowIrradianceDuplicateTimestamps:
    """Regression (2026-06-01): duplicate 'Start Time' tidak boleh bikin
    'IndexError: Boolean index has wrong length' (sama seperti bug shading).
    """

    def test_duplicate_start_time_no_indexerror(self, low_irr_cfg):
        df = _make_inverter_df("WB05-INV01", rs_high=True)
        dup = df.iloc[[30, 60]].copy()
        df_dup = (
            pd.concat([df, dup], ignore_index=True)
            .sort_values("Start Time")
            .reset_index(drop=True)
        )
        assert df_dup["Start Time"].duplicated().any()  # confirm dupes injected
        sm = M2aLowIrradiance(poa=_DupSafeMockPOA())
        findings = sm.run(df_dup, low_irr_cfg)  # OLD code -> IndexError
        assert isinstance(findings, list)
        # Detector run sampai selesai -> fit artifact ke-emit.
        assert "LowIrradianceFit" in sm.artifacts


# ============================================================================
# Rancangan ulang 2026-09-28: deviasi relatif-tetangga
# ============================================================================

PEER_WB = "WB05"


def _hours(timestamps):
    ts = pd.DatetimeIndex(timestamps)
    return np.asarray(ts.hour + ts.minute / 60.0)


class _CloudyNoonPOA:
    """Kurva cerah, kecuali awan 11:00-13:00 (POA 150 W/m2, matahari tinggi).

    POA rendah pagi/sore = matahari RENDAH; POA rendah karena awan siang =
    matahari TINGGI. Detektor harus membedakan keduanya.
    """

    def get_poa(self, timestamps, wb_id, source="auto"):
        hrs = _hours(timestamps)
        clear = np.where(
            (hrs >= 6) & (hrs <= 18), 1000.0 * np.sin(np.pi * (hrs - 6) / 12) ** 2, 0.0,
        )
        cloud = (hrs >= 11) & (hrs < 13)
        return pd.Series(np.where(cloud, 150.0, clear), index=pd.DatetimeIndex(timestamps))

    def get_solar_elevation(self, timestamps):
        hrs = _hours(timestamps)
        elev = np.where((hrs >= 6) & (hrs <= 18), 85.0 * np.sin(np.pi * (hrs - 6) / 12), -45.0)
        return pd.Series(elev, index=pd.DatetimeIndex(timestamps))


def _peer_df(low_light_factors, *, day="2026-05-14", low_sun_only=False):
    """Satu WB, satu inverter per faktor; faktor dikalikan ke daya saat POA < 250.

    ``low_sun_only=True``: faktor hanya berlaku saat elevasi < 30 derajat
    (pagi/sore) -- meniru bayangan geometri, bukan respons cahaya rendah.
    Kapasitas tiap inverter berbeda: detektor harus menormalkannya.
    """
    poa = _CloudyNoonPOA()
    ts = pd.date_range(f"{day} 06:00", f"{day} 18:00", freq="5min")
    irr = poa.get_poa(ts, PEER_WB).to_numpy()
    elev = poa.get_solar_elevation(ts).to_numpy()
    affected = irr < 250.0
    if low_sun_only:
        affected &= elev < 30.0
    rows = []
    for k, factor in enumerate(low_light_factors, start=1):
        p_kw = (100.0 + 10.0 * k) * irr / 1000.0 * np.where(affected, factor, 1.0)
        for t, pk in zip(ts, p_kw):
            rows.append({
                "Inverter_ID": f"{PEER_WB}-INV{k:02d}", "Start Time": t,
                "PV1 Power(kW)": pk / 2, "PV2 Power(kW)": pk / 2,
            })
    return pd.DataFrame(rows)


@pytest.fixture
def peer_cfg(low_irr_cfg):
    cfg = dict(low_irr_cfg)
    cfg["m2a_low_irradiance"] = dict(
        low_irr_cfg["m2a_low_irradiance"],
        pv_max=2, min_low_samples=12, min_mid_samples=12,
    )
    return cfg


def _classes(sm):
    fit = sm.artifacts["LowIrradianceFit"]
    return dict(zip(fit["inverter_id"], fit["classification"]))


class TestPeerRelativeLowIrradiance:
    def test_flags_inverter_with_poor_low_light_relative_to_peers(self, peer_cfg):
        # WHY: cacat low-light (Rsh rendah) = efisiensi turun HANYA saat
        # cahaya rendah, relatif terhadap inverter se-WB pada saat yang sama.
        sm = M2aLowIrradiance(poa=_CloudyNoonPOA())
        findings = sm.run(_peer_df([1.0] * 5 + [0.8]), peer_cfg)
        classes = _classes(sm)
        assert classes.pop("WB05-INV06") == "low_irradiance_underperform"
        assert set(classes.values()) == {"normal"}
        assert [f.inverter_id for f in findings] == ["WB05-INV06"]
        assert findings[0].value == pytest.approx(0.8, abs=0.01)

    def test_fleet_wide_low_light_shift_is_not_flagged(self, peer_cfg):
        # WHY: detektor lama (tanda kemiringan absolut) mem-flag 31 + 128
        # inverter pada 2026-07-01 (hujan) -- efek cuaca/pengukuran yang sama
        # untuk SEMUA inverter. Relatif terhadap tetangga, efek itu lenyap.
        sm = M2aLowIrradiance(poa=_CloudyNoonPOA())
        assert sm.run(_peer_df([0.8] * 6), peer_cfg) == []
        assert set(_classes(sm).values()) == {"normal"}

    def test_deficit_only_at_low_sun_is_not_low_light(self, peer_cfg):
        # WHY: di hari cerah POA rendah = matahari rendah, jadi pita rendah
        # tercampur bayangan geometri (WB07-INV15: turun hanya di hari cerah,
        # normal di hari hujan). Cacat low-light harus tampak juga saat
        # matahari TINGGI tertutup awan.
        sm = M2aLowIrradiance(poa=_CloudyNoonPOA())
        assert sm.run(_peer_df([1.0] * 5 + [0.8], low_sun_only=True), peer_cfg) == []
        assert _classes(sm)["WB05-INV06"] == "normal"

    def test_symmetric_spread_is_noise_not_fault(self, peer_cfg):
        # WHY: 2026-05-13 (awan konvektif): sebaran low_ratio simetris lebar,
        # 18 inverter < 0,90. Cacat hanya muncul di ekor bawah -- tanpa
        # robust z terhadap WB, hari berisik membanjiri flag.
        sm = M2aLowIrradiance(poa=_CloudyNoonPOA())
        assert sm.run(_peer_df([0.85, 0.9, 1.0, 1.0, 1.1, 1.15]), peer_cfg) == []

    def test_too_few_peers_is_insufficient_data(self, peer_cfg):
        # WHY: median tetangga dari 2-3 inverter adalah kebetulan.
        sm = M2aLowIrradiance(poa=_CloudyNoonPOA())
        assert sm.run(_peer_df([1.0, 1.0, 0.6]), peer_cfg) == []
        assert set(_classes(sm).values()) == {"insufficient_data"}

    def test_each_day_scored_separately(self, peer_cfg):
        # WHY: M2f mengklaim per (string, hari); flag satu hari tidak boleh
        # menempel ke hari lain.
        df = pd.concat([
            _peer_df([1.0] * 6, day="2026-05-14"),
            _peer_df([1.0] * 5 + [0.8], day="2026-05-15"),
        ], ignore_index=True)
        sm = M2aLowIrradiance(poa=_CloudyNoonPOA())
        sm.run(df, peer_cfg)
        fit = sm.artifacts["LowIrradianceFit"]
        flagged = fit[fit["classification"] == "low_irradiance_underperform"]
        assert flagged["day"].dt.strftime("%Y-%m-%d").tolist() == ["2026-05-15"]

    def test_ratio_threshold_is_configurable(self, peer_cfg):
        # WHY: 0,90 dipilih dari sebaran data 2026 (hari hujan p5 ~0,98);
        # operator harus bisa mengetatkan/melonggarkan tanpa ubah kode.
        df = _peer_df([1.0] * 5 + [0.93])
        assert M2aLowIrradiance(poa=_CloudyNoonPOA()).run(df, peer_cfg) == []
        cfg = dict(peer_cfg)
        cfg["m2a_low_irradiance"] = dict(peer_cfg["m2a_low_irradiance"], low_ratio_threshold=0.95)
        assert len(M2aLowIrradiance(poa=_CloudyNoonPOA()).run(df, cfg)) == 1

    def test_general_underperform_is_no_longer_produced(self, peer_cfg):
        # WHY: rugi seragam lenyap by design (normalisasi per inverter).
        # Kuncinya tetap di Summary (0) supaya dasbor trends tidak rusak.
        sm = M2aLowIrradiance(poa=_CloudyNoonPOA())
        sm.run(_peer_df([1.0] * 5 + [0.8]), peer_cfg)
        summary = sm.artifacts["LowIrradianceSummary"].iloc[0]
        assert summary["general_underperform"] == 0
        assert summary["low_irradiance_underperform"] == 1
