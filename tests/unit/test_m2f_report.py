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

    def __init__(self, value: float = POA_WM2, n_nan: int = 0, all_nan_for=None):
        self.value = value
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
        return series


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


def _install_providers(monkeypatch, poa=None, tcell=None):
    providers = {
        "poa": poa if poa is not None else _ConstantPOA(),
        "tcell": tcell if tcell is not None else _ConstantTcell(),
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
    calib = sm.artifacts["M2f_BifacialCalib"].set_index("wb_id")
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
