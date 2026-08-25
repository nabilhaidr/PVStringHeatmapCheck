"""Tes orchestrator M2f: closure end-to-end, artefak, dan gating config.

Provider POA/Tcell di-stub di modul ini (lihat :func:`_install_providers`).
Berkas ``raw data input/PV Module Temperature PLTS IKN.xlsx`` tidak ada di
working tree, sehingga ``_load_providers`` yang sebenarnya mengembalikan
``provider_unavailable`` dan SELURUH string tercatat skipped -- closure lalu
"berlaku" semata-mata karena tidak ada satu baris pun yang pernah dicek. Stub
ini memaksa tes menembus jalur ledger yang sesungguhnya; jalur
``provider_unavailable`` diuji terpisah dan eksplisit di bawah supaya cabang
itu tetap tercakup. Stub hidup HANYA di modul tes ini -- kode produksi tidak
punya cabang khusus tes.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.m2f.baseline import compute_expected_energy_kwh
from pv_pipeline.m2f.deficit import build_deficit_frame
from pv_pipeline.m2f.ledger import CLOSURE_TOLERANCE_KWH
from pv_pipeline.m2f.report import M2fLossAttribution
from pv_pipeline.panel_spec import PanelSpec


REPO_ROOT = Path(__file__).resolve().parents[2]
PANEL_SPEC_PATH = str(REPO_ROOT / "config" / "panel_spec.yaml")

POA_WM2 = 1000.0
TCELL_C = 45.0
POA_SOURCE = "pyranometer_per_ws"
ACTUAL_KW = 4.0
FREQ_HOURS = 5.0 / 60.0
INDEX = pd.date_range("2026-05-13 08:00", periods=4, freq="5min")


class _ConstantPOA:
    """POA konstan, opsional dengan ``n_nan`` timestamp pertama kosong."""

    def __init__(self, value: float = POA_WM2, n_nan: int = 0):
        self.value = value
        self.n_nan = n_nan

    def get_poa(self, timestamps, wb_id, source="auto"):
        idx = pd.DatetimeIndex(timestamps)
        series = pd.Series(self.value, index=idx, dtype=float)
        if self.n_nan:
            series.iloc[: self.n_nan] = np.nan
        return series


class _ConstantTcell:
    def get_tcell(self, timestamps, wb_id, source="auto"):
        return pd.Series(TCELL_C, index=pd.DatetimeIndex(timestamps), dtype=float)


def _install_providers(monkeypatch, poa=None):
    providers = {
        "poa": poa if poa is not None else _ConstantPOA(),
        "tcell": _ConstantTcell(),
        "spec": PanelSpec.from_yaml(PANEL_SPEC_PATH),
    }
    monkeypatch.setattr(
        M2fLossAttribution,
        "_load_providers",
        staticmethod(lambda config: (providers, None)),
    )
    return providers


@pytest.fixture
def stubbed(monkeypatch):
    """Provider konstan penuh-cakupan untuk mayoritas tes."""
    return _install_providers(monkeypatch)


def _config(enabled=True, **overrides):
    # deficit_frames dan p_loss_by_month SENGAJA tidak diisi di sini: itu
    # menegakkan "tanpa artefak -> estimator TIDAK dipanggil" secara default,
    # bukan lewat kebetulan fixture. Tes yang butuh dc_cable_fault/soiling
    # terisi harus mengisinya eksplisit.
    cfg = {
        "poa": {"site_geometry_path": "config/site_geometry.yaml"},
        "panel": {"spec_path": PANEL_SPEC_PATH},
        # Peta status dipakai untuk down_mask availability. Tanpa section ini
        # orchestrator sengaja TIDAK mengklaim availability sama sekali.
        "m2e": {"inverter_status_map": {"on_grid_keywords": ["on-grid"]}},
        "m2f": {
            "enabled": enabled,
            "attribution_order": [
                "availability_outage", "dc_cable_fault", "soiling", "unexplained",
            ],
            "bifacial_gain_per_wb": {"WB03": 1.05},
            "clearsky_kt_min": 0.9,
            "poa_coverage_min_pct": 80.0,
            "poa_source": POA_SOURCE,
            "residual_warn_pct": 30.0,
            "deficit_frames": None,
            "p_loss_by_month": {},
        },
    }
    cfg["m2f"].update(overrides)
    return cfg


def _combined_df(status="On-grid"):
    return pd.DataFrame([
        {
            "Start Time": ts,
            "Inverter_ID": "WB03-INV01",
            "PV5 Power(kW)": ACTUAL_KW,
            "PV5 input voltage(V)": 1200.0,
            "PV5 input current(A)": 3.33,
            "Inverter status": status,
        }
        for ts in INDEX
    ])


def _expected_kwh_per_ts(bifacial_gain=1.05, wb_id="WB03"):
    """E_expected satu timestamp pada kondisi stub, lewat jalur produksi."""
    one = pd.DatetimeIndex([INDEX[0]])
    return float(compute_expected_energy_kwh(
        pd.Series(POA_WM2, index=one),
        pd.Series(TCELL_C, index=one),
        PanelSpec.from_yaml(PANEL_SPEC_PATH),
        wb_id,
        bifacial_gain=bifacial_gain,
    ).iloc[0])


def _deficit_frame(
    inverter_id="WB03-INV01",
    pv_string="PV5",
    poa_source=POA_SOURCE,
    flagged=True,
    gap_kw=1.0,
):
    n = len(INDEX)
    return build_deficit_frame(
        timestamps=INDEX,
        poa_source=poa_source,
        inverter_id=inverter_id,
        pv_string=pv_string,
        actual_kw=np.full(n, ACTUAL_KW),
        counterfactual_kw=np.full(n, ACTUAL_KW + gap_kw),
        flagged=np.full(n, flagged),
    )


def _scored(sm):
    """Baris closure yang benar-benar dinilai (bukan di-skip)."""
    closure = sm.artifacts["M2f_Closure"]
    return closure[closure["skipped_reason"].isna()]


def _categories(sm):
    return set(sm.artifacts["M2f_PerString"]["category"].tolist())


# --------------------------------------------------------------------------
# Gating config dan skema artefak
# --------------------------------------------------------------------------

def test_disabled_by_default_emits_nothing(stubbed):
    sm = M2fLossAttribution()
    assert sm.run(_combined_df(), _config(enabled=False)) == []
    assert sm.artifacts == {}


def test_emits_all_five_artifacts_when_enabled(stubbed):
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    for sheet in (
        "M2f_Waterfall", "M2f_Pareto", "M2f_PerString",
        "M2f_Closure", "M2f_BifacialCalib",
    ):
        assert sheet in sm.artifacts, f"artifact {sheet} hilang"


def test_artifacts_keep_their_schema_when_no_row_scored(monkeypatch):
    # WHY: workbook harus tetap punya kelima sheet berskema benar walau tidak
    # ada satu string pun yang bisa dinilai. Sheet yang hilang membuat
    # pembaca menyimpulkan modulnya tidak pernah dijalankan.
    monkeypatch.setattr(
        M2fLossAttribution,
        "_load_providers",
        staticmethod(lambda config: (None, "provider_unavailable: sengaja")),
    )
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    assert list(sm.artifacts["M2f_PerString"].columns) == [
        "string_id", "day", "category", "loss_kwh",
    ]
    assert list(sm.artifacts["M2f_BifacialCalib"].columns) == [
        "wb_id", "g_bifacial", "n_strings", "n_days",
    ]
    assert sm.artifacts["M2f_PerString"].empty
    assert sm.artifacts["M2f_Pareto"].empty


def test_closure_sheet_has_skipped_reason_column(stubbed):
    # WHY: hari tanpa POA bukan "hari tanpa rugi". Menganggapnya nol akan
    # menurunkan angka rugi secara palsu, jadi alasannya harus tercatat.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    assert "skipped_reason" in sm.artifacts["M2f_Closure"].columns


# --------------------------------------------------------------------------
# Closure
# --------------------------------------------------------------------------

def test_closure_holds_for_every_string_day_row(stubbed):
    # WHY: invarian yang membuat seluruh angka waterfall layak dipercaya.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    scored = _scored(sm)
    # Tanpa penjaga ini, tes lulus VAKUM saat seluruh string ter-skip:
    # drift atas nol baris selalu "lolos".
    assert len(scored) > 0, "tidak ada baris yang benar-benar dinilai"
    drift = (
        scored["claimed_kwh"] + scored["residual_kwh"] - scored["l_total_kwh"]
    ).abs()
    assert (drift <= CLOSURE_TOLERANCE_KWH).all()


def test_scored_row_reports_real_loss_not_zero(stubbed):
    # WHY: baris yang dinilai harus membawa energi sungguhan. l_total 0.0 di
    # sini berarti baseline runtuh diam-diam dan closure jadi hampa.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    scored = _scored(sm)
    assert len(scored) == 1
    expected_total = 4 * _expected_kwh_per_ts()
    actual_total = 4 * ACTUAL_KW * FREQ_HOURS
    assert scored["l_total_kwh"].iloc[0] == pytest.approx(
        expected_total - actual_total
    )
    assert scored["poa_coverage_pct"].iloc[0] == pytest.approx(100.0)


# --------------------------------------------------------------------------
# Gate cakupan POA/Tcell
# --------------------------------------------------------------------------

def test_partial_poa_coverage_below_threshold_is_skipped_with_coverage_recorded(
    monkeypatch,
):
    # WHY: gate isna().all() meloloskan cakupan sebagian, lalu
    # compute_expected_energy_kwh mem-fillna(0.0) tiap timestamp kosong --
    # E_expected menyusut diam-diam dan L_total ikut menyusut tanpa jejak.
    _install_providers(monkeypatch, poa=_ConstantPOA(n_nan=2))  # cakupan 50%
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    closure = sm.artifacts["M2f_Closure"]
    assert len(closure) == 1
    row = closure.iloc[0]
    assert row["skipped_reason"] == "poa_or_tcell_missing"
    assert row["poa_coverage_pct"] == pytest.approx(50.0)
    assert np.isnan(row["l_total_kwh"])


def test_partial_coverage_above_threshold_still_records_its_coverage(monkeypatch):
    # WHY: cakupan parsial yang LOLOS gate juga harus terlihat di audit --
    # bukan hanya yang di-skip total.
    _install_providers(monkeypatch, poa=_ConstantPOA(n_nan=1))  # cakupan 75%
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(poa_coverage_min_pct=70.0))
    scored = _scored(sm)
    assert len(scored) == 1
    assert scored["poa_coverage_pct"].iloc[0] == pytest.approx(75.0)
    assert scored["tcell_coverage_pct"].iloc[0] == pytest.approx(100.0)


def test_skipped_string_day_does_not_inflate_site_e_expected(monkeypatch):
    # WHY: E_expected site hanya boleh menghimpun string-hari yang benar-benar
    # diproses; string yang di-skip tidak punya baseline yang sah.
    _install_providers(monkeypatch, poa=_ConstantPOA(n_nan=4))  # cakupan 0%
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    waterfall = sm.artifacts["M2f_Waterfall"]
    e_expected = waterfall.loc[
        waterfall["label"] == "E_expected", "delta_kwh"
    ].iloc[0]
    assert e_expected == pytest.approx(0.0)


# --------------------------------------------------------------------------
# "Tidak pernah diukur" != "diukur, aman"
# --------------------------------------------------------------------------

def test_dc_cable_fault_never_claimed_without_deficit_frames(stubbed):
    # WHY: memanggil estimatornya dengan 0.0 mendaftarkan kategorinya dan
    # melaporkan "sudah dicek, aman" untuk sesuatu yang tidak pernah diukur.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(deficit_frames=None))
    assert "dc_cable_fault" not in _categories(sm)
    assert "dc_cable_fault" not in sm.artifacts["M2f_Pareto"]["category"].tolist()


def test_dc_cable_fault_claims_zero_when_detector_ran_and_found_nothing(stubbed):
    # WHY: beda kasus dari di atas -- detektornya JALAN dan legitimately tidak
    # menemukan apa-apa, jadi 0.0 ("dicek, aman") memang jawabannya.
    sm = M2fLossAttribution()
    sm.run(
        _combined_df(),
        _config(deficit_frames=[_deficit_frame(flagged=False)]),
    )
    per_string = sm.artifacts["M2f_PerString"]
    row = per_string[per_string["category"] == "dc_cable_fault"]
    assert len(row) == 1
    assert row["loss_kwh"].iloc[0] == pytest.approx(0.0)


def test_dc_cable_fault_claims_deficit_when_detector_flagged(stubbed):
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(deficit_frames=[_deficit_frame(gap_kw=1.0)]))
    per_string = sm.artifacts["M2f_PerString"]
    row = per_string[per_string["category"] == "dc_cable_fault"]
    assert row["loss_kwh"].iloc[0] == pytest.approx(4 * 1.0 * FREQ_HOURS)


def test_deficit_of_another_string_is_not_claimed_to_this_string(stubbed):
    # WHY: reduce_deficit_frames hanya menyaring poa_source lalu mengambil
    # maksimum lintas frame. Tiap frame milik satu (inverter, PV string), jadi
    # tanpa penyaringan per-string di orchestrator, defisit PV9 akan diklaim
    # sebagai rugi kabel PV5 -- angka per string jadi salah tapi bukunya tetap
    # tutup, sehingga closure TIDAK akan menangkapnya.
    sm = M2fLossAttribution()
    sm.run(
        _combined_df(),
        _config(deficit_frames=[_deficit_frame(pv_string="PV9", gap_kw=1.0)]),
    )
    assert "dc_cable_fault" not in _categories(sm)


def test_deficit_frame_of_other_poa_source_is_not_claimed(stubbed):
    # WHY: source yang tidak cocok berarti detektornya tidak pernah menilai
    # string ini pada source yang diminta -- None, bukan 0.0.
    sm = M2fLossAttribution()
    sm.run(
        _combined_df(),
        _config(deficit_frames=[_deficit_frame(poa_source="pyranometer_avg")]),
    )
    assert "dc_cable_fault" not in _categories(sm)


def test_soiling_never_claimed_for_month_without_srr_data(stubbed):
    # WHY: bulan tanpa data SRR harus tetap None. p_loss=0.0 akan melaporkan
    # "sudah dicek, tidak ada soiling" untuk bulan yang tidak pernah diukur.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(p_loss_by_month={"2026-04": 0.05}))
    assert "soiling" not in _categories(sm)


def test_soiling_claimed_for_month_with_srr_data(stubbed):
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(p_loss_by_month={"2026-05": 0.10}))
    per_string = sm.artifacts["M2f_PerString"]
    row = per_string[per_string["category"] == "soiling"]
    assert row["loss_kwh"].iloc[0] == pytest.approx(
        0.10 * 4 * _expected_kwh_per_ts()
    )


def test_availability_not_claimed_without_status_keyword_map(stubbed):
    # WHY: tanpa peta status, "mati" tak bisa dibedakan dari "hidup". Menebak
    # akan mengklaim seluruh hari sebagai outage.
    cfg = _config()
    cfg["m2e"] = {}
    sm = M2fLossAttribution()
    sm.run(_combined_df(), cfg)
    assert "availability_outage" not in _categories(sm)


def test_availability_claims_whole_loss_when_inverter_down(stubbed):
    sm = M2fLossAttribution()
    findings = sm.run(_combined_df(status="Shutdown"), _config())
    per_string = sm.artifacts["M2f_PerString"]
    availability = per_string[per_string["category"] == "availability_outage"]
    unexplained = per_string[per_string["category"] == "unexplained"]
    assert availability["loss_kwh"].iloc[0] > 0.0
    assert unexplained["loss_kwh"].iloc[0] == pytest.approx(0.0)
    # Residual nol berarti atribusinya kuat -- tidak ada finding kualitas.
    assert findings == []


# --------------------------------------------------------------------------
# Waterfall, Pareto, finding
# --------------------------------------------------------------------------

def test_waterfall_e_expected_is_real_energy_not_sum_of_claims(stubbed):
    # WHY: memakai sum(klaim) sebagai tinggi batang membuat E_actual selalu
    # jatuh ke 0.0 -- identitas aljabar yang tampak rapi tapi bukan energi.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    waterfall = sm.artifacts["M2f_Waterfall"]
    e_expected = waterfall.loc[
        waterfall["label"] == "E_expected", "delta_kwh"
    ].iloc[0]
    e_actual = waterfall.loc[
        waterfall["label"] == "E_actual", "delta_kwh"
    ].iloc[0]
    assert e_expected == pytest.approx(4 * _expected_kwh_per_ts())
    assert e_actual == pytest.approx(4 * ACTUAL_KW * FREQ_HOURS)


def test_locked_categories_absent_from_pareto(stubbed):
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    cats = sm.artifacts["M2f_Pareto"]["category"].tolist()
    assert "microcrack" not in cats
    assert "bifacial_underperf" not in cats


def test_pareto_cum_pct_need_not_reach_100_when_unexplained_dominates(stubbed):
    # WHY: sejak Task 7 cum_pct kumulatif atas porsi ACTIONABLE saja. Di v1
    # unexplained menyerap shading, low-irradiance, microcrack, bifacial dan
    # ground-fault sekaligus, jadi residual besar adalah yang DIHARAPKAN.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    pareto = sm.artifacts["M2f_Pareto"]
    unexplained = pareto[pareto["category"] == "unexplained"]
    assert unexplained["pct"].iloc[0] > 50.0
    assert pareto["cum_pct"].max() < 100.0


def test_high_residual_emits_weak_attribution_finding(stubbed):
    # WHY: residual besar berarti atribusinya lemah. Itu harus muncul sebagai
    # sinyal, bukan diam-diam lolos sebagai angka yang tampak rapi.
    sm = M2fLossAttribution()
    findings = sm.run(_combined_df(), _config(residual_warn_pct=0.0))
    assert any(f.fault_type == "weak_attribution" for f in findings)
    finding = next(f for f in findings if f.fault_type == "weak_attribution")
    assert finding.sub_module == "M2f_loss_attribution"
    assert finding.severity.value == "INFO"
    assert finding.value == pytest.approx(100.0)


def test_no_finding_when_residual_does_not_exceed_threshold(stubbed):
    # WHY: ambang adalah batas "lebih besar dari", bukan "sama dengan" --
    # residual tepat di ambang belum melanggar apa pun.
    sm = M2fLossAttribution()
    assert sm.run(_combined_df(), _config(residual_warn_pct=100.0)) == []


def test_bifacial_calib_records_gain_actually_used(stubbed):
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    calib = sm.artifacts["M2f_BifacialCalib"]
    assert calib["wb_id"].tolist() == ["WB03"]
    assert calib["g_bifacial"].iloc[0] == pytest.approx(1.05)
    assert calib["n_strings"].iloc[0] == 1
    assert calib["n_days"].iloc[0] == 1


# --------------------------------------------------------------------------
# Jalur provider_unavailable
# --------------------------------------------------------------------------

def test_provider_unavailable_marks_every_string_skipped(monkeypatch):
    # WHY: cabang ini yang akan aktif di working tree tanpa berkas Tcell.
    # Ia harus menandai tiap string secara eksplisit, bukan menghasilkan
    # closure kosong yang terbaca "semuanya beres".
    monkeypatch.setattr(
        M2fLossAttribution,
        "_load_providers",
        staticmethod(lambda config: (None, "provider_unavailable: sengaja")),
    )
    sm = M2fLossAttribution()
    findings = sm.run(_combined_df(), _config())
    closure = sm.artifacts["M2f_Closure"]
    assert len(closure) == 1
    assert closure["skipped_reason"].iloc[0].startswith("provider_unavailable")
    assert findings == []


def test_load_providers_reports_error_instead_of_raising():
    # WHY: kegagalan muat provider tidak boleh menjatuhkan seluruh run M2 --
    # tapi juga tidak boleh ditelan tanpa alasan yang bisa dibaca.
    providers, error = M2fLossAttribution._load_providers({
        "poa": {"site_geometry_path": "tidak/ada.yaml"},
        "panel": {"spec_path": PANEL_SPEC_PATH},
    })
    assert providers is None
    assert error.startswith("provider_unavailable: ")
