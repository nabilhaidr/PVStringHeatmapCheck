"""Tes collect_m2f_inputs: kontrak tiga-keadaan, bukan cuma "menggabungkan list".

Fokus tes ini: (1) None/absen vs [] vs isi untuk ``deficit_frames``; (2) konversi
persen->fraksi + format kunci bulan untuk ``p_loss_by_month``; (3) dua tes
end-to-end lewat ``M2fLossAttribution`` asli yang membuktikan kontrak
"tidak pernah diukur" != "diukur, aman" bertahan sampai ke ``report.py``,
bukan cuma di isi dict ``config["m2f"]``.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pv_pipeline.core import SubModule
from pv_pipeline.m2a.soiling import MONTHLY_SOILING_COLUMNS, build_monthly_soiling_loss
from pv_pipeline.m2f.collect import collect_m2f_inputs, load_p_loss_by_month_xlsx
from pv_pipeline.m2f.deficit import build_deficit_frame
from pv_pipeline.m2f.report import M2fLossAttribution
from pv_pipeline.panel_spec import PanelSpec

REPO_ROOT = Path(__file__).resolve().parents[2]
PANEL_SPEC_PATH = str(REPO_ROOT / "config" / "panel_spec.yaml")

POA_WM2 = 1000.0
TCELL_C = 45.0
POA_SOURCE = "pyranometer_per_ws"
ACTUAL_KW = 4.0
INDEX = pd.date_range("2026-05-13 08:00", periods=4, freq="5min")


class _FakeDetector(SubModule):
    """Submodule minimal untuk uji collector -- BUKAN detektor asli.

    Sengaja TIDAK set ``self.deficit_frames`` secara default, meniru
    ``ground_fault`` yang tidak punya atribut itu sama sekali. Panggil dengan
    ``deficit_frames=[]`` untuk meniru detektor yang JALAN tapi tidak
    menghasilkan frame apa pun (beda kasus dari tidak diberi argumen sama
    sekali).
    """

    name = "fake_detector"

    def __init__(self, deficit_frames=None, monthly_soiling_loss=None):
        super().__init__()
        if deficit_frames is not None:
            self.deficit_frames = deficit_frames
        if monthly_soiling_loss is not None:
            self.artifacts["MonthlySoilingLoss"] = monthly_soiling_loss

    def run(self, combined_df, config):  # pragma: no cover - tidak pernah dipanggil
        raise NotImplementedError


def _deficit_frame(flagged=True, gap_kw=1.0, pv_string="PV3", poa_source=POA_SOURCE):
    n = len(INDEX)
    return build_deficit_frame(
        timestamps=INDEX,
        poa_source=poa_source,
        inverter_id="WB03-INV01",
        pv_string=pv_string,
        actual_kw=np.full(n, ACTUAL_KW),
        counterfactual_kw=np.full(n, ACTUAL_KW + gap_kw),
        flagged=np.full(n, flagged),
    )


# --------------------------------------------------------------------------
# deficit_frames: None/absen vs [] vs isi
# --------------------------------------------------------------------------

def test_no_submodules_leaves_deficit_frames_key_none():
    config = {}
    collect_m2f_inputs([], config)
    assert config["m2f"]["deficit_frames"] is None


def test_submodule_without_deficit_frames_attribute_does_not_crash():
    # WHY: ground_fault sengaja TIDAK set self.deficit_frames. Collector harus
    # memperlakukan submodule seperti ini sebagai "tidak menyumbang apa pun",
    # bukan meledak AttributeError.
    config = {}
    collect_m2f_inputs([_FakeDetector()], config)
    assert config["m2f"]["deficit_frames"] is None


def test_submodule_with_empty_deficit_frames_list_leaves_key_none_not_empty_list():
    # WHY: detektor yang JALAN tapi tidak ada string lolos gate POA
    # meninggalkan self.deficit_frames = [] (atributnya ADA, isinya kosong).
    # Hasil di config harus tetap None -- PERSIS sama seperti kasus "tidak
    # pernah dijalankan" di atas -- bukan [] yang mengekspos detail
    # implementasi report.py._index_deficit_frames ke kontrak collector ini.
    config = {}
    collect_m2f_inputs([_FakeDetector(deficit_frames=[])], config)
    assert config["m2f"]["deficit_frames"] is None


def test_single_detector_deficit_frames_are_collected():
    frame = _deficit_frame()
    config = {}
    collect_m2f_inputs([_FakeDetector(deficit_frames=[frame])], config)
    frames = config["m2f"]["deficit_frames"]
    assert frames is not None
    assert len(frames) == 1
    pd.testing.assert_frame_equal(frames[0], frame)


def test_frames_from_multiple_detectors_are_concatenated_in_order():
    frame_a = _deficit_frame(pv_string="PV3")
    frame_b = _deficit_frame(pv_string="PV9")
    config = {}
    collect_m2f_inputs(
        [
            _FakeDetector(deficit_frames=[frame_a]),
            _FakeDetector(deficit_frames=[frame_b]),
        ],
        config,
    )
    frames = config["m2f"]["deficit_frames"]
    assert len(frames) == 2
    assert frames[0]["pv_string"].iloc[0] == "PV3"
    assert frames[1]["pv_string"].iloc[0] == "PV9"


def test_ground_fault_like_submodule_mixed_with_real_contributor_is_excluded():
    # WHY: ground_fault harus dilewati lewat KETIADAAN atributnya, bukan lewat
    # nama submodule -- kalau ada yang diam-diam menambahkan pengecualian
    # berbasis nama, tes ini tidak akan menangkapnya (memang tidak perlu:
    # _FakeDetector() di sini bukan bernama "ground_fault" apa pun).
    frame = _deficit_frame()
    config = {}
    collect_m2f_inputs(
        [_FakeDetector(), _FakeDetector(deficit_frames=[frame])], config,
    )
    frames = config["m2f"]["deficit_frames"]
    assert len(frames) == 1


# --------------------------------------------------------------------------
# p_loss_by_month: konversi persen->fraksi + format kunci bulan
# --------------------------------------------------------------------------

def test_no_submodules_leaves_p_loss_by_month_empty_dict():
    config = {}
    collect_m2f_inputs([], config)
    assert config["m2f"]["p_loss_by_month"] == {}


def test_submodule_without_soiling_artifact_leaves_p_loss_by_month_empty():
    config = {}
    collect_m2f_inputs([_FakeDetector(deficit_frames=[])], config)
    assert config["m2f"]["p_loss_by_month"] == {}


def test_monthly_soiling_loss_month_key_and_fraction_match_report_py_contract():
    # WHY: bukan tebakan skema -- profiles_df/insolation_daily digabung lewat
    # build_monthly_soiling_loss (pv_pipeline/m2a/soiling.py), fungsi yang
    # SESUNGGUHNYA mengisi artifact MonthlySoilingLoss di M2aSoiling.run().
    # Ini membuktikan kolom "month" yang dihasilkan produksi (str "YYYY-MM"
    # dari str(pd.Period(freq="M"))) memang cocok tanpa konversi tambahan
    # dengan day.strftime("%Y-%m") yang dipakai report.py.
    dates = pd.date_range("2026-03-01", periods=10, freq="D")
    profiles_df = pd.DataFrame(
        {"draw0": np.full(10, 0.95), "draw1": np.full(10, 0.95)}, index=dates,
    )
    insolation_daily = pd.Series(5000.0, index=dates)
    energy_daily = pd.Series(1000.0, index=dates)
    monthly = build_monthly_soiling_loss(
        profiles_df, insolation_daily, energy_daily, tariff_idr_per_kwh=1500.0,
    )
    assert list(monthly.columns) == MONTHLY_SOILING_COLUMNS
    assert isinstance(monthly["month"].iloc[0], str)
    assert monthly["p_loss_pct"].iloc[0] == pytest.approx(5.0)

    config = {}
    collect_m2f_inputs([_FakeDetector(monthly_soiling_loss=monthly)], config)
    p_loss_by_month = config["m2f"]["p_loss_by_month"]
    # Kunci yang PERSIS dicari M2fLossAttribution.run() lewat day.strftime("%Y-%m").
    expected_key = pd.Timestamp("2026-03-15").strftime("%Y-%m")
    assert p_loss_by_month == {expected_key: pytest.approx(0.05)}


def test_month_with_nan_p_loss_pct_is_excluded_not_zero():
    # WHY: p_loss=0.0 untuk bulan yang tidak pernah dihitung melaporkan
    # "sudah dicek, aman" -- persis kesalahan yang dicegah Task 9 Kendala #3.
    monthly = pd.DataFrame(
        [{
            "month": "2026-04", "n_days": 5, "sr_p50": np.nan,
            "sr_ci_lower": np.nan, "sr_ci_upper": np.nan, "p_loss_pct": np.nan,
            "energy_lost_kwh_est": np.nan, "loss_idr_est": np.nan,
        }],
        columns=MONTHLY_SOILING_COLUMNS,
    )
    config = {}
    collect_m2f_inputs([_FakeDetector(monthly_soiling_loss=monthly)], config)
    assert config["m2f"]["p_loss_by_month"] == {}


def test_multiple_soiling_months_all_collected():
    monthly = pd.DataFrame(
        [
            {"month": "2026-01", "n_days": 20, "sr_p50": 0.9, "sr_ci_lower": 0.85,
             "sr_ci_upper": 0.95, "p_loss_pct": 10.0, "energy_lost_kwh_est": 500.0,
             "loss_idr_est": 750000.0},
            {"month": "2026-02", "n_days": 18, "sr_p50": 0.97, "sr_ci_lower": 0.94,
             "sr_ci_upper": 0.99, "p_loss_pct": 3.0, "energy_lost_kwh_est": 120.0,
             "loss_idr_est": 180000.0},
        ],
        columns=MONTHLY_SOILING_COLUMNS,
    )
    config = {}
    collect_m2f_inputs([_FakeDetector(monthly_soiling_loss=monthly)], config)
    assert config["m2f"]["p_loss_by_month"] == {
        "2026-01": pytest.approx(0.10),
        "2026-02": pytest.approx(0.03),
    }


# --------------------------------------------------------------------------
# End-to-end lewat M2fLossAttribution asli: kontrak "tidak pernah diukur"
# != "diukur, aman" harus bertahan sampai ke report.py, bukan cuma di isi
# dict config["m2f"].
# --------------------------------------------------------------------------

class _ConstantPOA:
    def get_poa(self, timestamps, wb_id, source="auto"):
        return pd.Series(POA_WM2, index=pd.DatetimeIndex(timestamps), dtype=float)


class _ConstantTcell:
    def get_tcell(self, timestamps, wb_id, source="auto"):
        return pd.Series(TCELL_C, index=pd.DatetimeIndex(timestamps), dtype=float)


def _install_stub_providers(monkeypatch):
    """Stub provider POA/Tcell sama seperti tests/unit/test_m2f_report.py.

    Berkas ``raw data input/PV Module Temperature PLTS IKN.xlsx`` tidak ada
    di working tree -- tanpa stub ini, M2fLossAttribution._load_providers
    yang sesungguhnya akan gagal (provider_unavailable) dan seluruh string
    tercatat skipped, sehingga jalur dc_cable_fault yang mau diuji di sini
    tidak pernah tereksekusi.
    """
    providers = {
        "poa": _ConstantPOA(),
        "tcell": _ConstantTcell(),
        "spec": PanelSpec.from_yaml(PANEL_SPEC_PATH),
    }
    monkeypatch.setattr(
        M2fLossAttribution,
        "_load_providers",
        staticmethod(lambda config: (providers, None)),
    )


def _m2f_config():
    return {
        "poa": {"site_geometry_path": "config/site_geometry.yaml"},
        "panel": {"spec_path": PANEL_SPEC_PATH},
        "m2e": {
            "inverter_status_map": {
                "on_grid_keywords": ["on-grid"],
                "down_keywords": ["shutdown"],
                "transitional_keywords": [],
            },
        },
        "m2f": {
            "enabled": True,
            "attribution_order": [
                "availability_outage", "dc_cable_fault", "soiling", "unexplained",
            ],
            "bifacial_gain_per_wb": {},
            "poa_coverage_min_pct": 80.0,
            "poa_source": POA_SOURCE,
            "residual_warn_pct": 100.0,
        },
    }


def _combined_df():
    return pd.DataFrame([
        {
            "Start Time": ts,
            "Inverter_ID": "WB03-INV01",
            "PV3 Power(kW)": ACTUAL_KW,
            "Inverter status": "On-grid",
        }
        for ts in INDEX
    ])


def test_end_to_end_no_detectors_leaves_dc_cable_fault_never_measured(monkeypatch):
    _install_stub_providers(monkeypatch)
    config = _m2f_config()
    collect_m2f_inputs([], config)  # tidak ada detektor m2b yang dijalankan
    sm = M2fLossAttribution()
    sm.run(_combined_df(), config)
    categories = set(sm.artifacts["M2f_PerString"]["category"].tolist())
    assert "dc_cable_fault" not in categories
    assert "dc_cable_fault" not in sm.artifacts["M2f_Pareto"]["category"].tolist()


def test_end_to_end_detector_ran_found_nothing_is_distinguishable_from_never_ran(
    monkeypatch,
):
    # WHY: beda kasus dari tes di atas -- di sini detektornya BENAR-BENAR
    # jalan (menyumbang deficit_frames, walau flagged=False, "tidak
    # menemukan apa-apa"), jadi dc_cable_fault harus terklaim 0.0 ("dicek,
    # aman"), bukan tetap absen seperti kasus "tidak pernah dijalankan".
    _install_stub_providers(monkeypatch)
    config = _m2f_config()
    ran_detector = _FakeDetector(deficit_frames=[_deficit_frame(flagged=False)])
    collect_m2f_inputs([ran_detector], config)
    sm = M2fLossAttribution()
    sm.run(_combined_df(), config)
    per_string = sm.artifacts["M2f_PerString"]
    row = per_string[per_string["category"] == "dc_cable_fault"]
    assert len(row) == 1
    assert row["loss_kwh"].iloc[0] == pytest.approx(0.0)


# --------------------------------------------------------------------------
# v2: artefak M2aShading dan M2aLowIrradiance
# --------------------------------------------------------------------------

def test_collect_bridges_v2_detector_artifacts():
    # WHY: M2Engine tidak punya jalur antar-submodule. Tanpa jembatan ini
    # estimator shading/low_irradiance_eff tidak pernah melihat detektornya.
    shading = _FakeDetector()
    shading.artifacts["HourlyMetrics"] = pd.DataFrame(
        {"inverter_id": ["WB03-INV01"], "hour": [8]}
    )
    low = _FakeDetector()
    low.artifacts["LowIrradianceFit"] = pd.DataFrame({"inverter_id": ["WB03-INV01"]})
    config = {"m2f": {}}
    collect_m2f_inputs([shading, low], config)
    assert config["m2f"]["shading_hourly"]["hour"].tolist() == [8]
    assert config["m2f"]["low_irradiance_fit"]["inverter_id"].tolist() == ["WB03-INV01"]


def test_collect_without_v2_detectors_leaves_none():
    # WHY: None = "detektor tidak dijalankan"; kategori tetap tidak terukur,
    # bukan terbaca 0.0.
    config = {"m2f": {}}
    collect_m2f_inputs([_FakeDetector()], config)
    assert config["m2f"]["shading_hourly"] is None
    assert config["m2f"]["low_irradiance_fit"] is None


# --------------------------------------------------------------------------
# p_loss_by_month dari workbook soiling_srr_*.xlsx (run_soiling_analysis.py)
# --------------------------------------------------------------------------

def _monthly(rows):
    frame = pd.DataFrame(rows)
    for col in MONTHLY_SOILING_COLUMNS:
        if col not in frame.columns:
            frame[col] = np.nan
    return frame[MONTHLY_SOILING_COLUMNS]


def _write_srr_xlsx(path, monthly=None):
    """Meniru run_soiling_analysis.py: sheet kosong TIDAK ditulis sama sekali."""
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({"note": ["x"]}).to_excel(writer, sheet_name="Findings", index=False)
        if monthly is not None:
            monthly.to_excel(writer, sheet_name="MonthlySoilingLoss", index=False)
    return str(path)


def test_srr_xlsx_months_become_fractions(tmp_path):
    # WHY: run M2f harian hanya melihat hari-harinya sendiri, dan M2aSoiling
    # butuh >= 90 hari -- soiling TIDAK PERNAH diklaim (run 2026-08-31:
    # p_loss_by_month = {}). Workbook SRR multi-bulan adalah sumbernya, dan
    # p_loss_pct di sana PERSEN: tanpa /100, claim_soiling menolak nilai
    # > 1, atau untuk bulan < 1% mengklaim 100x terlalu besar.
    path = _write_srr_xlsx(tmp_path / "soiling_srr.xlsx", _monthly([
        {"month": "2026-05", "p_loss_pct": 3.2},
        {"month": "2026-06", "p_loss_pct": 0.5},
    ]))
    assert load_p_loss_by_month_xlsx(path) == pytest.approx(
        {"2026-05": 0.032, "2026-06": 0.005}
    )


def test_srr_xlsx_nan_month_stays_absent(tmp_path):
    # WHY: NaN = bulan tanpa cakupan insolasi yang cukup untuk SRR. Mengisinya
    # 0.0 akan terbaca "sudah dicek, bersih" untuk bulan yang tak pernah dihitung.
    path = _write_srr_xlsx(tmp_path / "soiling_srr.xlsx", _monthly([
        {"month": "2026-05", "p_loss_pct": 3.2},
        {"month": "2026-06", "p_loss_pct": np.nan},
    ]))
    out = load_p_loss_by_month_xlsx(path)
    assert "2026-06" not in out
    assert out["2026-05"] == pytest.approx(0.032)


def test_srr_xlsx_without_monthly_sheet_gives_empty(tmp_path):
    # WHY: run_soiling_analysis.py hanya menulis sheet yang tidak kosong --
    # sheet absen berarti SRR tidak menghasilkan profil bulanan, bukan nol.
    path = _write_srr_xlsx(tmp_path / "soiling_srr.xlsx", monthly=None)
    assert load_p_loss_by_month_xlsx(path) == {}


def test_srr_xlsx_missing_file_fails_loud(tmp_path):
    # WHY: path eksplisit yang salah ketik tidak boleh diam-diam menjadi
    # "tanpa soiling" -- itu tampak persis seperti bulan bersih.
    with pytest.raises(FileNotFoundError):
        load_p_loss_by_month_xlsx(str(tmp_path / "tidak_ada.xlsx"))


def test_collect_srr_xlsx_overrides_same_month_from_run(tmp_path):
    # WHY: dua sumber p_loss untuk bulan yang sama tidak boleh dirata-rata
    # (CLAUDE.md Rule 7). Workbook dibangun dari baseline multi-bulan; SRR di
    # run ini hanya melihat hari-hari run itu sendiri. Workbook menang untuk
    # bulan yang sama; bulan yang hanya ada di run tetap dipakai.
    run_sm = _FakeDetector(monthly_soiling_loss=_monthly([
        {"month": "2026-05", "p_loss_pct": 9.0},
        {"month": "2026-04", "p_loss_pct": 1.0},
    ]))
    path = _write_srr_xlsx(tmp_path / "soiling_srr.xlsx", _monthly([
        {"month": "2026-05", "p_loss_pct": 3.2},
    ]))
    config = {"m2f": {}}
    collect_m2f_inputs([run_sm], config, soiling_srr_xlsx=path)
    assert config["m2f"]["p_loss_by_month"] == pytest.approx(
        {"2026-05": 0.032, "2026-04": 0.01}
    )
