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
from pv_pipeline.m2f.setpoint import SetpointCaps
from pv_pipeline.panel_spec import PanelSpec


REPO_ROOT = Path(__file__).resolve().parents[2]
PANEL_SPEC_PATH = str(REPO_ROOT / "config" / "panel_spec.yaml")
STRINGS_YAML_PATH = str(REPO_ROOT / "config" / "strings.yaml")

POA_WM2 = 1000.0
TCELL_C = 45.0
POA_SOURCE = "pyranometer_per_ws"
ACTUAL_KW = 4.0
FREQ_HOURS = 5.0 / 60.0
INDEX = pd.date_range("2026-05-13 08:00", periods=4, freq="5min")
DAY_TWO = pd.date_range("2026-05-14 08:00", periods=4, freq="5min")

# Salinan keymap nyata dari config/m2_config.yaml -> m2e.inverter_status_map.
# Dipakai utuh (bukan hanya on_grid_keywords) supaya klasifikasi empat arah
# _classify_status benar-benar terlatih: DOWN / ON / TRANSITIONAL / UNKNOWN.
STATUS_MAP = {
    "on_grid_keywords": ["grid connected", "on-grid", "on grid", "ongrid"],
    "down_keywords": ["shutdown", "fault", "stopped", "stop", "error"],
    "transitional_keywords": [
        "standby", "starting", "stopping", "initializing",
        "initialization", "detecting", "detection", "no sunlight",
    ],
}


class _ConstantPOA:
    """POA konstan, opsional dengan ``n_nan`` timestamp pertama kosong.

    Merekam tiap ``source`` yang diminta di ``requested_sources`` supaya tes
    dapat membuktikan orchestrator tidak jatuh ke ``source="auto"``.
    """

    def __init__(
        self, value: float = POA_WM2, n_nan: int = 0, all_nan_for=None,
        elevation: float = 60.0, fallback_filled: int = 0,
    ):
        self.value = value
        self.elevation = elevation
        self.fallback_filled = fallback_filled
        self.n_nan = n_nan
        # Source yang "tidak punya data" -- meniru berkas pyranometer hilang.
        self.all_nan_for = set(all_nan_for or [])
        self.requested_sources = []

    def get_poa(self, timestamps, wb_id, source="auto"):
        self.requested_sources.append(source)
        idx = pd.DatetimeIndex(timestamps)
        if source in self.all_nan_for:
            return pd.Series(np.nan, index=idx, dtype=float)
        series = pd.Series(self.value, index=idx, dtype=float)
        if self.n_nan:
            series.iloc[: self.n_nan] = np.nan
        # Meniru PyranometerLoader.get_per_ws: posisi yang diisi dari avg.
        series.attrs["fallback_filled"] = self.fallback_filled
        return series

    def get_solar_elevation(self, timestamps):
        return pd.Series(self.elevation, index=pd.DatetimeIndex(timestamps), dtype=float)


class _ConstantTcell:
    """Tcell konstan penuh-cakupan.

    Merekam tiap ``source`` yang diminta di ``requested_sources``, sama
    seperti ``_ConstantPOA``, supaya tes dapat membuktikan orchestrator
    tidak diam-diam jatuh ke ``source="auto"``.
    """

    def __init__(self, value: float = TCELL_C):
        self.value = value
        self.requested_sources = []

    def get_tcell(self, timestamps, wb_id, source="auto"):
        self.requested_sources.append(source)
        return pd.Series(self.value, index=pd.DatetimeIndex(timestamps), dtype=float)


def _install_providers(monkeypatch, poa=None, tcell=None, setpoint=None):
    providers = {
        "poa": poa if poa is not None else _ConstantPOA(),
        "tcell": tcell if tcell is not None else _ConstantTcell(),
        "spec": PanelSpec.from_yaml(PANEL_SPEC_PATH),
    }
    if setpoint is not None:
        providers["setpoint"] = setpoint
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
        # empty_pv_map_path menunjuk strings.yaml NYATA: slot kosong yang
        # di-skip harus yang benar-benar terdaftar di site, bukan karangan.
        "m2e": {
            "inverter_status_map": STATUS_MAP,
            "empty_pv_map_path": STRINGS_YAML_PATH,
        },
        "m2f": {
            "enabled": enabled,
            "attribution_order": [
                "availability_outage", "dc_cable_fault", "soiling", "unexplained",
            ],
            "bifacial_gain_per_wb": {"WB03": 1.05},
            # Salinan config/m2_config.yaml -> m2f.curtailment_keywords.
            "curtailment_keywords": ["instructed shutdown", "power limited"],
            "poa_coverage_min_pct": 80.0,
            "poa_source": POA_SOURCE,
            "residual_warn_pct": 30.0,
            "deficit_frames": None,
            "p_loss_by_month": {},
        },
    }
    cfg["m2f"].update(overrides)
    return cfg


def _rows(inverter_id, index, pv_powers, status="On-grid"):
    """Baris telemetri untuk satu inverter, satu hari, beberapa kolom PV."""
    out = []
    for ts in index:
        row = {
            "Start Time": ts,
            "Inverter_ID": inverter_id,
            "Inverter status": status,
        }
        for pv_label, kw in pv_powers.items():
            row[f"{pv_label} Power(kW)"] = kw
        out.append(row)
    return out


def _combined_df(status="On-grid"):
    return pd.DataFrame(_rows(
        "WB03-INV01", INDEX, {"PV3": ACTUAL_KW}, status=status,
    ))


def _multi_combined_df():
    """Dua inverter x dua WB x dua hari x beberapa string.

    Memuat DUA slot kosong nyata, keduanya berdaya 0.0 kW (bukan NaN) persis
    seperti pelaporan Huawei untuk input MPPT yang tidak terpasang:
    WB01-INV01 PV19 (dari pola PV19..PV28 di tiap inverter WB01) dan
    WB03-INV01 PV5 (dari [5, 19, 24]). Keduanya diambil dari
    config/strings.yaml yang NYATA, bukan peta karangan -- slot yang di-skip
    harus benar-benar tidak ada di site.

    String riil yang tersisa: WB03-INV01 PV3 + PV6, WB01-INV01 PV1.
    """
    rows = []
    for index in (INDEX, DAY_TWO):
        rows += _rows(
            "WB03-INV01", index,
            {"PV3": ACTUAL_KW, "PV5": 0.0, "PV6": 3.0},
        )
        rows += _rows("WB01-INV01", index, {"PV1": 3.5, "PV19": 0.0})
    return pd.DataFrame(rows)


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
    pv_string="PV3",
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
        "M2f_Closure", "M2f_BaselineCalib",
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
    assert list(sm.artifacts["M2f_BaselineCalib"].columns) == [
        "wb_id", "g_bifacial", "dc_derate", "measured_ratio",
        "n_calib_string_days", "n_strings", "n_days",
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
# Konsistensi aritmetika closure
# --------------------------------------------------------------------------

def test_closure_columns_are_nan_free_and_arithmetically_consistent(stubbed):
    # WHAT INI BUKTIKAN: `claimed + residual - l_total` secara aljabar selalu
    # nol untuk atribusi APA PUN, benar atau salah, karena ledger.residual()
    # DIDEFINISIKAN sebagai l_total() - sum(claims) (ledger.py:171-173). Jadi
    # tes ini TIDAK memvalidasi atribusi. Yang benar-benar ia tangkap: (a)
    # NaN yang menyelinap ke salah satu dari ketiga kolom -- perbandingan
    # dengan NaN selalu False sehingga assertion di bawah merah; (b) baris
    # yang benar-benar dinilai memang ada, lewat penjaga len(scored) > 0,
    # tanpanya drift atas nol baris selalu "lolos" secara vakum.
    #
    # Atribusi yang SESUNGGUHNYA diverifikasi di tempat lain: nilai kWh per
    # kategori di test_dc_cable_fault_claims_deficit_when_detector_flagged,
    # test_soiling_claimed_for_month_with_srr_data, dan
    # test_availability_claims_whole_loss_when_inverter_down; batas
    # "tidak pernah diukur" di keluarga tes _never_claimed_*.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    scored = _scored(sm)
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
# Slot PV kosong (empty_pv_map)
# --------------------------------------------------------------------------

def test_empty_pv_slot_is_skipped_even_though_it_reports_zero_not_nan(stubbed):
    # WHY: Huawei melaporkan 0 V / 0 A -- BUKAN NaN -- untuk input MPPT yang
    # tidak terpasang, jadi penjaga all-NaN tidak pernah menangkapnya. Tanpa
    # empty_pv_map, PV19..PV28 di tiap inverter WB01 mendapat E_expected satu
    # string penuh melawan aktual ~0: rugi 100% palsu yang menggelembungkan
    # E_expected site, waterfall, dan residual Pareto.
    sm = M2fLossAttribution()
    sm.run(_multi_combined_df(), _config())
    scored_ids = set(_scored(sm)["string_id"])
    closure_ids = set(sm.artifacts["M2f_Closure"]["string_id"])
    for phantom in ("WB01-INV01-PV19", "WB03-INV01-PV5"):
        assert phantom not in scored_ids
        # Tidak juga muncul sebagai baris skipped: slot itu bukan "gagal
        # dinilai", ia memang tidak ada secara fisik.
        assert phantom not in closure_ids
    assert "WB01-INV01-PV1" in scored_ids
    assert "WB03-INV01-PV3" in scored_ids


def test_empty_slot_does_not_inflate_site_expected_energy(stubbed):
    # WHY: tiap slot hantu menambah E_expected 26 modul penuh ke total site.
    sm = M2fLossAttribution()
    sm.run(_multi_combined_df(), _config())
    waterfall = sm.artifacts["M2f_Waterfall"]
    e_expected = waterfall.loc[
        waterfall["label"] == "E_expected", "delta_kwh"
    ].iloc[0]
    # 3 string riil x 2 hari x 4 timestamp; PV19 dan PV5 tidak ikut.
    per_ts_wb03 = _expected_kwh_per_ts(bifacial_gain=1.05, wb_id="WB03")
    per_ts_wb01 = _expected_kwh_per_ts(bifacial_gain=1.0, wb_id="WB01")
    expected = 2 * 4 * (2 * per_ts_wb03 + per_ts_wb01)
    assert e_expected == pytest.approx(expected)


# --------------------------------------------------------------------------
# Multi-string / multi-inverter / multi-hari
# --------------------------------------------------------------------------

def test_site_aggregation_spans_every_string_inverter_and_day(stubbed):
    # WHY: agregasi site, akumulasi l_total, dan _iter_string_days sendiri
    # tidak pernah teruji oleh fixture satu-string-satu-hari.
    sm = M2fLossAttribution()
    sm.run(_multi_combined_df(), _config())
    scored = _scored(sm)
    # 3 string riil x 2 hari.
    assert len(scored) == 6
    assert set(scored["string_id"]) == {
        "WB03-INV01-PV3", "WB03-INV01-PV6", "WB01-INV01-PV1",
    }
    assert scored["day"].nunique() == 2
    # Total rugi site = jumlah per baris, bukan hanya baris terakhir.
    per_string = sm.artifacts["M2f_PerString"]
    unexplained = per_string[per_string["category"] == "unexplained"]
    assert len(unexplained) == 6
    assert unexplained["loss_kwh"].sum() == pytest.approx(
        scored["residual_kwh"].sum()
    )


def test_bifacial_table_counts_strings_and_days_per_wb(stubbed):
    # WHY: n_strings > 1 dan n_days > 1 tidak pernah tersentuh sebelumnya.
    sm = M2fLossAttribution()
    sm.run(_multi_combined_df(), _config())
    calib = sm.artifacts["M2f_BaselineCalib"].set_index("wb_id")
    assert calib.loc["WB03", "n_strings"] == 2
    assert calib.loc["WB03", "n_days"] == 2
    assert calib.loc["WB03", "g_bifacial"] == pytest.approx(1.05)
    # WB01 tidak ada di bifacial_gain_per_wb -> default 1.0, dan hanya PV1
    # yang terhitung karena PV19 slot kosong.
    assert calib.loc["WB01", "n_strings"] == 1
    assert calib.loc["WB01", "g_bifacial"] == pytest.approx(1.0)


def test_inverter_id_and_power_columns_are_derived_when_absent(stubbed):
    # WHY: kedua cabang fallback (add_inverter_id, add_pv_power_columns) tidak
    # pernah dieksekusi oleh fixture yang sudah menyediakan keduanya.
    raw = pd.DataFrame([
        {
            "Start Time": ts,
            "ManageObject": "Inv_A_101_IKN",
            "PV1 input voltage(V)": 1200.0,
            "PV1 input current(A)": 3.0,
            "Inverter status": "On-grid",
        }
        for ts in INDEX
    ])
    sm = M2fLossAttribution()
    sm.run(raw, _config())
    scored = _scored(sm)
    assert len(scored) == 1
    assert scored["string_id"].iloc[0] == "WB01-INV01-PV1"


def test_ac_power_column_is_not_mistaken_for_a_string(stubbed):
    # WHY: endswith(" Power(kW)") yang peka huruf akan menyerap
    # "Active Power(kW)" -- daya AC seluruh inverter -- dan membandingkannya
    # dengan E_expected SATU string. PV_POWER_RE hanya cocok pada PV<n>.
    df = _combined_df()
    df["Active Power(kW)"] = 95.0
    sm = M2fLossAttribution()
    sm.run(df, _config())
    assert set(_scored(sm)["string_id"]) == {"WB03-INV01-PV3"}


# --------------------------------------------------------------------------
# Sumber POA (bukan "auto")
# --------------------------------------------------------------------------

def test_poa_is_requested_with_the_configured_source_not_auto(monkeypatch):
    # WHY: default get_poa adalah "auto", yang mengisi tiap NaN dari rantai
    # fallback sampai pvlib clear-sky. Cakupan lalu terbaca ~100% walau tidak
    # ada satu pun pembacaan pyranometer, dan gate cakupan tidak pernah nyala.
    poa = _ConstantPOA()
    _install_providers(monkeypatch, poa=poa)
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    assert poa.requested_sources, "get_poa tidak pernah dipanggil"
    assert set(poa.requested_sources) == {POA_SOURCE}
    assert "auto" not in poa.requested_sources


def test_missing_measured_poa_skips_every_string_instead_of_substituting(
    monkeypatch,
):
    # WHY: inilah keadaan working tree hari ini -- tidak ada berkas POA sama
    # sekali. Hasil yang BENAR adalah setiap string di-skip dan blokirnya
    # terlihat, bukan diam-diam dijalankan di atas irradiance model.
    poa = _ConstantPOA(all_nan_for=[POA_SOURCE])
    _install_providers(monkeypatch, poa=poa)
    sm = M2fLossAttribution()
    findings = sm.run(_multi_combined_df(), _config())
    closure = sm.artifacts["M2f_Closure"]
    assert len(closure) == 6
    assert (closure["skipped_reason"] == "poa_or_tcell_missing").all()
    assert (closure["poa_coverage_pct"] == 0.0).all()
    assert _scored(sm).empty
    assert findings == []


def test_closure_records_the_poa_source_actually_used(stubbed):
    # WHY: bila seseorang sengaja mengonfigurasi source clear-sky, workbook
    # harus mengatakannya -- bukan menyajikan irradiance model seolah terukur.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(poa_source="pvlib_clearsky_ineichen"))
    closure = sm.artifacts["M2f_Closure"]
    assert "poa_source" in closure.columns
    assert set(closure["poa_source"]) == {"pvlib_clearsky_ineichen"}


# --------------------------------------------------------------------------
# Sumber Tcell (bukan "auto")
# --------------------------------------------------------------------------

def test_tcell_is_requested_with_the_configured_source_not_auto(monkeypatch):
    # WHY: default get_tcell adalah "auto", yang rantai fallbacknya berakhir
    # di SAPM (Tcell MODEL, bukan terukur). tcell_coverage_pct lalu terbaca
    # penuh walau tidak ada satu pun pembacaan sensor Tcell, dan baseline
    # absolut M2f diam-diam berdiri di atas suhu model.
    tcell = _ConstantTcell()
    _install_providers(monkeypatch, tcell=tcell)
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(tcell_source="measured_overall_avg"))
    assert tcell.requested_sources, "get_tcell tidak pernah dipanggil"
    assert set(tcell.requested_sources) == {"measured_overall_avg"}
    assert "auto" not in tcell.requested_sources


def test_missing_tcell_source_key_defaults_to_measured_not_auto(monkeypatch):
    # WHY: config yang lupa mengisi tcell_source tidak boleh diam-diam jatuh
    # ke "auto" (-> SAPM, model). _config() SENGAJA tidak mengisi
    # tcell_source, jadi tes ini menembus default produksi di report.py,
    # bukan default milik helper tes.
    tcell = _ConstantTcell()
    _install_providers(monkeypatch, tcell=tcell)
    sm = M2fLossAttribution()
    cfg = _config()
    assert "tcell_source" not in cfg["m2f"]
    sm.run(_combined_df(), cfg)
    assert set(tcell.requested_sources) == {"measured_per_ws"}
    assert "auto" not in tcell.requested_sources


def test_closure_records_the_tcell_source_actually_used(stubbed):
    # WHY: bila seseorang sengaja mengonfigurasi source SAPM/auto, workbook
    # harus mengatakannya -- bukan menyajikan Tcell model seolah terukur.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(tcell_source="measured_overall_avg"))
    closure = sm.artifacts["M2f_Closure"]
    assert "tcell_source" in closure.columns
    assert set(closure["tcell_source"]) == {"measured_overall_avg"}


def test_skipped_closure_row_also_records_tcell_source(monkeypatch):
    # WHY: string-hari yang di-skip (cakupan di bawah ambang) tetap harus
    # menyatakan source Tcell yang DIKONFIGURASI di M2f_Closure -- audit
    # tidak boleh menyisakan kolom kosong hanya karena string itu tidak
    # pernah dinilai.
    _install_providers(monkeypatch, poa=_ConstantPOA(n_nan=4))  # cakupan 0%
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(tcell_source="measured_overall_avg"))
    closure = sm.artifacts["M2f_Closure"]
    row = closure.iloc[0]
    assert row["skipped_reason"] == "poa_or_tcell_missing"
    assert row["tcell_source"] == "measured_overall_avg"


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


def test_poa_filled_from_site_average_is_marked_in_closure(monkeypatch):
    # WHY: pyranometer_per_ws diam-diam mengisi WS yang kosong dari rata-rata
    # 5 WS, padahal antar-WS berbeda hingga +-10% -- E_expected string-hari
    # itu berdiri di atas POA situs, bukan POA WS-nya. Cakupan tetap penuh
    # (nilainya sah), tapi audit harus bisa melihat porsinya.
    _install_providers(monkeypatch, poa=_ConstantPOA(fallback_filled=1))  # 1 dari 4
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    scored = _scored(sm)
    assert scored["poa_coverage_pct"].iloc[0] == pytest.approx(100.0)
    assert scored["poa_fallback_pct"].iloc[0] == pytest.approx(25.0)


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
    # sebagai rugi kabel PV3 -- angka per string jadi salah tapi bukunya tetap
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


# --------------------------------------------------------------------------
# Klasifikasi status inverter
# --------------------------------------------------------------------------

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


@pytest.mark.parametrize("status", ["No Sunlight", "Standby", "Starting"])
def test_transitional_status_is_not_an_outage(stubbed, status):
    # WHY: `~on_grid` menyapu tiap status peralihan menjadi DOWN. "No Sunlight"
    # adalah status fajar/senja yang muncul pada timestamp siang hari dengan
    # E_expected > 0, jadi ini salah tembak pada data nyata -- bukan hanya
    # malam hari. Karena availability berprioritas pertama dan mengklaim
    # seluruh sisa, ia akan melaparkan dc_cable_fault dan soiling.
    sm = M2fLossAttribution()
    sm.run(_combined_df(status=status), _config())
    per_string = sm.artifacts["M2f_PerString"]
    availability = per_string[per_string["category"] == "availability_outage"]
    assert availability["loss_kwh"].iloc[0] == pytest.approx(0.0)


@pytest.mark.parametrize("status", ["", None])
def test_unknown_status_is_not_an_outage(stubbed, status):
    # WHY: status kosong berarti "tidak terukur", dan melaporkannya sebagai
    # "terukur, string mati" adalah kekeliruan yang sama persis yang dicegah
    # oleh aturan None-bukan-0.0 pada dc_cable_fault dan soiling.
    sm = M2fLossAttribution()
    sm.run(_combined_df(status=status), _config())
    per_string = sm.artifacts["M2f_PerString"]
    availability = per_string[per_string["category"] == "availability_outage"]
    assert availability["loss_kwh"].iloc[0] == pytest.approx(0.0)


def test_transitional_status_leaves_energy_for_lower_priority_categories(stubbed):
    # WHY: ini akibat konkret dari salah klasifikasi -- bila "No Sunlight"
    # diklaim sebagai outage, tidak ada sisa energi tersisa untuk soiling.
    sm = M2fLossAttribution()
    sm.run(
        _combined_df(status="No Sunlight"),
        _config(p_loss_by_month={"2026-05": 0.10}),
    )
    per_string = sm.artifacts["M2f_PerString"]
    soiling = per_string[per_string["category"] == "soiling"]
    assert soiling["loss_kwh"].iloc[0] == pytest.approx(
        0.10 * 4 * _expected_kwh_per_ts()
    )


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
    assert finding.extra["poa_source"] == POA_SOURCE


def test_no_finding_when_residual_does_not_exceed_threshold(stubbed):
    # WHY: ambang adalah batas "lebih besar dari", bukan "sama dengan" --
    # residual tepat di ambang belum melanggar apa pun.
    sm = M2fLossAttribution()
    assert sm.run(_combined_df(), _config(residual_warn_pct=100.0)) == []


def test_bifacial_calib_records_gain_actually_used(stubbed):
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    calib = sm.artifacts["M2f_BaselineCalib"]
    assert calib["wb_id"].tolist() == ["WB03"]
    assert calib["g_bifacial"].iloc[0] == pytest.approx(1.05)
    assert calib["n_strings"].iloc[0] == 1
    assert calib["n_days"].iloc[0] == 1


# --------------------------------------------------------------------------
# dc_derate dan kalibrasi baseline
# --------------------------------------------------------------------------

def _raw_expected_kwh_per_ts(wb_id="WB03"):
    """E_expected MENTAH satu timestamp: tanpa gain bifacial, tanpa derate."""
    return _expected_kwh_per_ts(bifacial_gain=1.0, wb_id=wb_id)


def test_dc_derate_scales_expected_energy(stubbed):
    # WHY: baseline pelat-nama tanpa derate menaruh seluruh rugi struktural
    # (IAM, mismatch, kabel DC, LID) ke unexplained -- 92% di run
    # 2026-08-31. Derate harus benar-benar mengecilkan E_expected yang
    # dipakai ledger DAN terminal waterfall, bukan hanya dicatat di sheet.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(dc_derate_per_wb={"WB03": 0.8}))
    expected_ts = _raw_expected_kwh_per_ts() * 1.05 * 0.8
    assert _scored(sm)["l_total_kwh"].iloc[0] == pytest.approx(
        len(INDEX) * (expected_ts - ACTUAL_KW * FREQ_HOURS)
    )
    waterfall = sm.artifacts["M2f_Waterfall"].set_index("label")
    assert waterfall.loc["E_expected", "delta_kwh"] == pytest.approx(
        len(INDEX) * expected_ts
    )


def test_dc_derate_defaults_to_one_for_unlisted_wb(stubbed):
    # WHY: WB yang belum dikalibrasi tidak boleh diam-diam memakai derate WB
    # lain -- ia tetap di baseline pelat-nama sampai dikalibrasi sendiri.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(dc_derate_per_wb={"WB01": 0.8}))
    expected_ts = _raw_expected_kwh_per_ts() * 1.05
    assert _scored(sm)["l_total_kwh"].iloc[0] == pytest.approx(
        len(INDEX) * (expected_ts - ACTUAL_KW * FREQ_HOURS)
    )


@pytest.mark.parametrize("bad", [85.0, 0.0, -0.8])
def test_dc_derate_out_of_range_fails_loud(stubbed, bad):
    # WHY: kebingungan persen vs fraksi berulang di codebase ini
    # (p_loss_pct / 100). Derate 85 alih-alih 0,85 menggelembungkan
    # E_expected 100x dan seluruh waterfall -- harus crash, bukan angka.
    sm = M2fLossAttribution()
    with pytest.raises(ValueError, match="dc_derate"):
        sm.run(_combined_df(), _config(dc_derate_per_wb={"WB03": bad}))


def test_baseline_calib_measured_ratio_is_median_of_raw_ratios(stubbed):
    # WHY: measured_ratio adalah angka yang disalin operator ke
    # dc_derate_per_wb. Ia median rasio aktual / harapan-MENTAH per
    # string-hari: PV3 (4,0 kW) dan PV6 (3,0 kW), masing-masing dua hari.
    sm = M2fLossAttribution()
    sm.run(_multi_combined_df(), _config())
    calib = sm.artifacts["M2f_BaselineCalib"].set_index("wb_id")
    raw = _raw_expected_kwh_per_ts()
    r_pv3 = ACTUAL_KW * FREQ_HOURS / raw
    r_pv6 = 3.0 * FREQ_HOURS / raw
    assert calib.loc["WB03", "measured_ratio"] == pytest.approx((r_pv3 + r_pv6) / 2)
    assert calib.loc["WB03", "n_calib_string_days"] == 4


def test_measured_ratio_ignores_configured_gain_and_derate(stubbed):
    # WHY: bila rasio diukur terhadap E_expected yang SUDAH di-derate,
    # menyalinnya ke config menggandakan derate di tiap putaran kalibrasi
    # (0,85 -> 0,72 -> 0,61 ...) dan baseline merosot tanpa batas.
    plain = M2fLossAttribution()
    plain.run(_multi_combined_df(), _config())
    derated = M2fLossAttribution()
    derated.run(_multi_combined_df(), _config(dc_derate_per_wb={"WB03": 0.8}))
    a = plain.artifacts["M2f_BaselineCalib"].set_index("wb_id")
    b = derated.artifacts["M2f_BaselineCalib"].set_index("wb_id")
    assert b.loc["WB03", "measured_ratio"] == pytest.approx(
        a.loc["WB03", "measured_ratio"]
    )
    assert b.loc["WB03", "dc_derate"] == pytest.approx(0.8)
    assert a.loc["WB03", "dc_derate"] == pytest.approx(1.0)


def test_measured_ratio_excludes_curtailed_and_non_on_timestamps(stubbed):
    # WHY: power limited dan standby menekan aktual karena sebab di luar
    # modul. Ikut dihitung, derate menyerap curtailment dan rugi
    # curtailment ter-underestimate di setiap run sesudahnya.
    statuses = [
        "On-grid", "On-grid", "Grid connected : power limited",
        "Standby : no sunlight",
    ]
    powers = [ACTUAL_KW, ACTUAL_KW, 0.5, 0.0]
    rows = []
    for index in (INDEX, DAY_TWO):
        for ts, status, kw in zip(index, statuses, powers):
            rows += _rows(
                "WB03-INV01", [ts], {"PV3": kw, "PV6": kw}, status=status,
            )
    sm = M2fLossAttribution()
    sm.run(pd.DataFrame(rows), _config())
    calib = sm.artifacts["M2f_BaselineCalib"].set_index("wb_id")
    assert calib.loc["WB03", "measured_ratio"] == pytest.approx(
        ACTUAL_KW * FREQ_HOURS / _raw_expected_kwh_per_ts()
    )


def test_measured_ratio_is_nan_when_too_few_string_days(stubbed):
    # WHY: median satu-dua string adalah kebetulan, bukan kalibrasi. NaN
    # plus n_calib_string_days memberi tahu pembaca KENAPA kosong.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config())
    calib = sm.artifacts["M2f_BaselineCalib"].set_index("wb_id")
    assert np.isnan(calib.loc["WB03", "measured_ratio"])
    assert calib.loc["WB03", "n_calib_string_days"] == 1


# --------------------------------------------------------------------------
# dc_cable_fault: timestamp ter-flag yang tak terukur (NaN)
# --------------------------------------------------------------------------

def _deficit_frame_with_unsized(n_unsized, gap_kw=1.0):
    """PV3 ter-flag di seluruh INDEX; ``n_unsized`` timestamp pertama tak terukur."""
    n = len(INDEX)
    actual = np.full(n, ACTUAL_KW)
    actual[:n_unsized] = np.nan
    return build_deficit_frame(
        timestamps=INDEX, poa_source=POA_SOURCE,
        inverter_id="WB03-INV01", pv_string="PV3",
        actual_kw=actual, counterfactual_kw=np.full(n, ACTUAL_KW + gap_kw),
        flagged=np.full(n, True),
    )


def test_dc_cable_fault_claims_sized_steps_when_coverage_is_enough(stubbed):
    # WHY (2026-07-29, WB03-INV08-PV6): filter Hampel mengubah 1 dari 38
    # sampel ter-flag menjadi NaN, dan M2f crash untuk SELURUH run. Bagian
    # yang terukur tetap rugi nyata -- diklaim sebagai batas bawah.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(
        deficit_frames=[_deficit_frame_with_unsized(1)], poa_coverage_min_pct=70.0,
    ))
    assert _loss_by_category(sm)["dc_cable_fault"] == pytest.approx(3 * 1.0 * FREQ_HOURS)


def test_dc_cable_fault_is_unmeasured_when_flagged_steps_mostly_unsized(stubbed):
    # WHY (2026-08-31): 42 string PV15-28 ter-flag tanpa satu pun defisit
    # terukur (tegangan hilang) tercatat 0.0 -- "dicek, aman". Di bawah ambang
    # cakupan, kategori harus None ("tak terukur"), terdengar lewat
    # peringatan, dan run tidak crash.
    sm = M2fLossAttribution()
    with pytest.warns(UserWarning, match="dc_cable_fault tak terukur"):
        sm.run(_combined_df(), _config(deficit_frames=[_deficit_frame_with_unsized(4)]))
    assert "dc_cable_fault" not in _categories(sm)


# --------------------------------------------------------------------------
# curtailment dipisah dari availability_outage
# --------------------------------------------------------------------------

CURTAIL_FIRST = [
    "curtailment", "availability_outage", "dc_cable_fault", "soiling",
    "unexplained",
]


def _status_rows(statuses, pv3_kw):
    """Satu inverter, satu string (PV3), status dan daya per timestamp INDEX."""
    rows = []
    for ts, status, kw in zip(INDEX, statuses, pv3_kw):
        rows += _rows("WB03-INV01", [ts], {"PV3": kw}, status=status)
    return pd.DataFrame(rows)


def _loss_by_category(sm):
    return sm.artifacts["M2f_PerString"].set_index("category")["loss_kwh"]


def test_instructed_shutdown_is_curtailment_not_outage(stubbed):
    # WHY: "OFF : instructed shutdown" memuat "shutdown", jadi
    # _classify_status menyebutnya DOWN. Tanpa pemisahan, perintah grid
    # terhitung outage -- biaya yang tak bisa dipulihkan maintenance tampil
    # sebagai target maintenance. "unexpected shutdown" tetap outage.
    df = _status_rows(
        ["On-grid", "OFF : instructed shutdown", "OFF : unexpected shutdown", "On-grid"],
        [ACTUAL_KW, 0.0, 0.0, ACTUAL_KW],
    )
    sm = M2fLossAttribution()
    sm.run(df, _config(attribution_order=CURTAIL_FIRST))
    loss = _loss_by_category(sm)
    assert loss["curtailment"] == pytest.approx(_expected_kwh_per_ts())
    assert loss["availability_outage"] == pytest.approx(_expected_kwh_per_ts())


def test_power_limited_deficit_is_curtailment(stubbed):
    # WHY: inverter tetap "Grid connected" tetapi dibatasi -- _classify_status
    # menyebutnya ON, jadi tanpa kategori ini defisitnya jatuh ke unexplained.
    df = _status_rows(
        ["On-grid", "Grid connected : power limited", "On-grid", "On-grid"],
        [ACTUAL_KW, 1.0, ACTUAL_KW, ACTUAL_KW],
    )
    sm = M2fLossAttribution()
    sm.run(df, _config(attribution_order=CURTAIL_FIRST))
    loss = _loss_by_category(sm)
    assert loss["curtailment"] == pytest.approx(
        _expected_kwh_per_ts() - 1.0 * FREQ_HOURS
    )
    assert loss["availability_outage"] == pytest.approx(0.0)


def test_curtailed_timestamps_never_become_outage_whatever_the_order(stubbed):
    # WHY: attribution_order dapat ditukar di config. Perintah grid tetap
    # bukan gangguan, jadi availability tidak boleh mengklaimnya walau
    # berprioritas lebih tinggi.
    df = _status_rows(
        ["On-grid", "OFF : instructed shutdown", "OFF : unexpected shutdown", "On-grid"],
        [ACTUAL_KW, 0.0, 0.0, ACTUAL_KW],
    )
    order = [
        "availability_outage", "curtailment", "dc_cable_fault", "soiling",
        "unexplained",
    ]
    sm = M2fLossAttribution()
    sm.run(df, _config(attribution_order=order))
    loss = _loss_by_category(sm)
    assert loss["availability_outage"] == pytest.approx(_expected_kwh_per_ts())
    assert loss["curtailment"] == pytest.approx(_expected_kwh_per_ts())


def test_without_curtailment_keywords_category_stays_unmeasured(stubbed):
    # WHY: tanpa kata kunci, "tidak di-curtail" tak bisa dibedakan dari
    # "tidak dicek" -- kategori None (absen), bukan 0.0; dan instructed
    # shutdown kembali ke perilaku lama (DOWN = outage).
    df = _status_rows(
        ["On-grid", "OFF : instructed shutdown", "OFF : unexpected shutdown", "On-grid"],
        [ACTUAL_KW, 0.0, 0.0, ACTUAL_KW],
    )
    sm = M2fLossAttribution()
    sm.run(df, _config(attribution_order=CURTAIL_FIRST, curtailment_keywords=[]))
    loss = _loss_by_category(sm)
    assert "curtailment" not in loss.index
    assert loss["availability_outage"] == pytest.approx(2 * _expected_kwh_per_ts())


# --------------------------------------------------------------------------
# curtailment dari plafon set point busbar (jaringan 20 kV)
# --------------------------------------------------------------------------

BUS_WB03 = [{"column": "Setpoint Busbar 1", "wbs": ["WB03"], "max_ac_kw": 28240.0}]
CAP_WB03 = 25000.0 * 330.0 / 28240.0


def _setpoint_caps(value=25000.0):
    """Riwayat 10 menit yang menutupi INDEX dan DAY_TWO."""
    stamps = [ts for day in (INDEX, DAY_TWO)
              for ts in pd.date_range(day[0], periods=2, freq="10min")]
    history = pd.DataFrame({"Setpoint Busbar 1": value}, index=pd.DatetimeIndex(stamps))
    return SetpointCaps(history, BUS_WB03, {"WB03": 330.0})


def _with_ac(df, ac_kw):
    """Tambah daya AC inverter per baris (urutan sama dengan baris df)."""
    df = df.copy()
    df["Active power(kW)"] = ac_kw
    return df


def test_grid_connected_output_held_at_setpoint_cap_is_curtailment(monkeypatch):
    # WHY: 2026-07-29 bus 1 tertahan di plafon set point dengan status "Grid
    # connected", bukan "power limited". Dari status saja, energi yang
    # terpotong jaringan jatuh ke unexplained dan menekan measured_ratio di
    # hari cerah.
    _install_providers(monkeypatch, setpoint=_setpoint_caps())
    df = _with_ac(
        _status_rows(["On-grid"] * 4, [1.0, 1.0, ACTUAL_KW, ACTUAL_KW]),
        [CAP_WB03, CAP_WB03, 200.0, 200.0],
    )
    sm = M2fLossAttribution()
    sm.run(df, _config(attribution_order=CURTAIL_FIRST))
    loss = _loss_by_category(sm)
    assert loss["curtailment"] == pytest.approx(
        2 * (_expected_kwh_per_ts() - 1.0 * FREQ_HOURS)
    )
    assert loss["availability_outage"] == pytest.approx(0.0)


def test_output_at_full_setpoint_is_not_curtailment(monkeypatch):
    # WHY: set point = kapasitas penuh bus (bus 2 sepanjang 2025) tidak
    # membatasi; inverter di Pmax-nya bukan korban jaringan.
    _install_providers(monkeypatch, setpoint=_setpoint_caps(value=28240.0))
    df = _with_ac(
        _status_rows(["On-grid"] * 4, [1.0, 1.0, ACTUAL_KW, ACTUAL_KW]),
        [330.0, 330.0, 200.0, 200.0],
    )
    sm = M2fLossAttribution()
    sm.run(df, _config(attribution_order=CURTAIL_FIRST))
    assert _loss_by_category(sm)["curtailment"] == pytest.approx(0.0)


def test_measured_ratio_excludes_setpoint_capped_timestamps(monkeypatch):
    # WHY: plafon hanya mengikat di hari cerah. Bila sampelnya ikut
    # kalibrasi, derate menyerap curtailment jaringan dan measured_ratio ikut
    # langit (~0,85 cerah vs ~1,0 mendung).
    _install_providers(monkeypatch, setpoint=_setpoint_caps())
    rows = []
    for index in (INDEX, DAY_TWO):
        for ts, kw, ac in zip(index, [1.0, 1.0, ACTUAL_KW, ACTUAL_KW],
                              [CAP_WB03, CAP_WB03, 200.0, 200.0]):
            for row in _rows("WB03-INV01", [ts], {"PV3": kw, "PV6": kw}):
                row["Active power(kW)"] = ac
                rows.append(row)
    sm = M2fLossAttribution()
    sm.run(pd.DataFrame(rows), _config())
    calib = sm.artifacts["M2f_BaselineCalib"].set_index("wb_id")
    assert calib.loc["WB03", "measured_ratio"] == pytest.approx(
        ACTUAL_KW * FREQ_HOURS / _raw_expected_kwh_per_ts()
    )


class _RampPOA(_ConstantPOA):
    """POA naik 700 -> 1000 W/m2 sepanjang indeks yang diminta."""

    def get_poa(self, timestamps, wb_id, source="auto"):
        idx = pd.DatetimeIndex(timestamps)
        return pd.Series(np.linspace(700.0, 1000.0, len(idx)), index=idx)


def test_plateau_marks_curtailment_when_setpoint_history_is_missing(monkeypatch):
    # WHY: riwayat set point berhenti 2026-08-31 dan bisa keliru; sesudahnya
    # plafon hanya dikenali dari bentuknya -- daya AC datar di bawah Pmax
    # sementara POA terus naik.
    rows = []
    for ts in pd.date_range("2026-05-13 10:00", periods=8, freq="5min"):
        row = _rows("WB03-INV01", [ts], {"PV3": 3.0})[0]
        row["Active power(kW)"] = 275.0
        rows.append(row)
    no_history = SetpointCaps(pd.DataFrame(), BUS_WB03, {"WB03": 330.0})
    _install_providers(monkeypatch, poa=_RampPOA(), setpoint=no_history)
    sm = M2fLossAttribution()
    sm.run(pd.DataFrame(rows), _config(attribution_order=CURTAIL_FIRST))
    assert _loss_by_category(sm)["curtailment"] > 0.0


# --------------------------------------------------------------------------
# v2: shading dan low_irradiance_eff
# --------------------------------------------------------------------------

V2_ORDER = [
    "curtailment", "availability_outage", "dc_cable_fault", "shading",
    "soiling", "low_irradiance_eff", "unexplained",
]


def _shading_rows(fault_type="shading_morning", poa_source=POA_SOURCE, day="2026-05-13"):
    """HourlyMetrics M2aShading: jam 08 (= seluruh INDEX) ter-flag."""
    return pd.DataFrame([{
        "inverter_id": "WB03-INV01", "day": pd.Timestamp(day),
        "poa_source": poa_source, "hour": 8, "pr_proxy": 0.5,
        "pr_reference": 1.0, "suspicious": True, "fault_type": fault_type,
    }])


SHADING_KWH = len(INDEX) * ACTUAL_KW * FREQ_HOURS * (1.0 / 0.5 - 1.0)


def test_shading_claims_flagged_hour_against_reference_pr(stubbed):
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(attribution_order=V2_ORDER, shading_hourly=_shading_rows()))
    assert _loss_by_category(sm)["shading"] == pytest.approx(SHADING_KWH)


def test_shading_is_claimed_before_soiling(stubbed):
    # WHY: SRR menyerap apa saja yang turun perlahan. Bila soiling mengklaim
    # lebih dulu, rugi shading terbaca rugi soiling dan ROI cleaning
    # overstated -- padahal angka itu dasar keputusan biaya.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(
        attribution_order=V2_ORDER, shading_hourly=_shading_rows(),
        p_loss_by_month={"2026-05": 1.0},
    ))
    loss = _loss_by_category(sm)
    l_total = _scored(sm)["l_total_kwh"].iloc[0]
    assert loss["shading"] == pytest.approx(SHADING_KWH)
    assert loss["soiling"] == pytest.approx(l_total - SHADING_KWH)


def test_symmetric_shading_pattern_is_not_claimed_as_shading(stubbed):
    # WHY: menurut M2aShading sendiri, pola simetris pagi = sore lebih
    # mirip soiling atau awan. Mengklaimnya sebagai shading mencuri energi
    # soiling. Inverter-hari itu tetap terevaluasi: 0.0, bukan None.
    sm = M2fLossAttribution()
    sm.run(_combined_df(), _config(
        attribution_order=V2_ORDER,
        shading_hourly=_shading_rows(fault_type="shading_uniform"),
    ))
    assert _loss_by_category(sm)["shading"] == pytest.approx(0.0)


def test_shading_from_other_poa_source_or_day_stays_unmeasured(stubbed):
    # WHY: baris dari sumber POA lain (mis. "auto" = clear-sky) tidak
    # sebanding; inverter-hari yang tidak dievaluasi detektor tidak boleh
    # terbaca "dicek, tidak terbayang".
    for rows in (_shading_rows(poa_source="auto"), _shading_rows(day="2026-05-14")):
        sm = M2fLossAttribution()
        sm.run(_combined_df(), _config(attribution_order=V2_ORDER, shading_hourly=rows))
        assert "shading" not in _categories(sm)


LOW_FIT = {
    "inverter_id": "WB03-INV01", "day": pd.Timestamp("2026-05-13"),
    "poa_source": POA_SOURCE, "low_ratio": 0.8,
    "classification": "low_irradiance_underperform",
}


def _low_light_run(monkeypatch, elevation=60.0, **fit_overrides):
    """POA 150 W/m2 (pita rendah), matahari tinggi, PV3 = 1.0 kW."""
    _install_providers(monkeypatch, poa=_ConstantPOA(value=150.0, elevation=elevation))
    df = pd.DataFrame(_rows("WB03-INV01", INDEX, {"PV3": 1.0}))
    sm = M2fLossAttribution()
    sm.run(df, _config(
        attribution_order=V2_ORDER,
        low_irradiance_fit=pd.DataFrame([dict(LOW_FIT, **fit_overrides)]),
    ))
    return sm


def test_low_irradiance_claims_deficit_against_peer_median(monkeypatch):
    # WHY: counterfactual = setara median tetangga se-WB; low_ratio 0.8 ->
    # aktual x (1/0.8 - 1) = aktual x 0.25 di pita rendah bermatahari tinggi.
    sm = _low_light_run(monkeypatch)
    assert _loss_by_category(sm)["low_irradiance_eff"] == pytest.approx(
        len(INDEX) * 1.0 * FREQ_HOURS * 0.25
    )


def test_low_irradiance_ignores_low_sun_samples(monkeypatch):
    # WHY: low_ratio diukur hanya saat matahari >= 30 derajat; mengklaimnya
    # di pagi/sore akan menyamakan bayangan geometri dengan cacat low-light.
    sm = _low_light_run(monkeypatch, elevation=20.0)
    assert _loss_by_category(sm)["low_irradiance_eff"] == pytest.approx(0.0)


def test_low_irradiance_normal_inverter_is_measured_zero(monkeypatch):
    sm = _low_light_run(monkeypatch, classification="normal")
    assert _loss_by_category(sm)["low_irradiance_eff"] == pytest.approx(0.0)


def test_low_irradiance_unevaluated_or_other_source_stays_unmeasured(monkeypatch):
    # WHY: "insufficient_data" berarti fit tidak pernah dihitung; fit dari
    # sumber POA lain tidak sebanding dengan POA yang dipakai M2f.
    for overrides in (
        {"classification": "insufficient_data"}, {"poa_source": "auto"},
        {"day": pd.Timestamp("2026-05-14")},
    ):
        sm = _low_light_run(monkeypatch, **overrides)
        assert "low_irradiance_eff" not in _categories(sm)


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
