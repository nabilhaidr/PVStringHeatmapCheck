"""Tests rekap_m2e_allstrings.py (gabung + rekap M2e_hybrid_AllStrings)."""
from __future__ import annotations

import pandas as pd
import pytest

from rekap_m2e_allstrings import (
    ALLSTRINGS_SHEET,
    REKAP_PER_STRING_COLUMNS,
    build_rekap_per_string,
    build_uptime_pivot,
    discover_findings_xlsx,
    load_allstrings,
)


def _write_findings_xlsx(path, rows, sheet=ALLSTRINGS_SHEET):
    with pd.ExcelWriter(path) as xw:
        pd.DataFrame(rows).to_excel(xw, sheet_name=sheet, index=False)


def _allstrings_row(inv="WB01-INV01", pv="PV1", status="NORMAL",
                    uptime=100.0, downtime=0.0):
    return {
        "inverter_id": inv, "pv_string": pv, "status": status,
        "uptime_pct": uptime, "downtime_minutes": downtime,
        "event_minutes": downtime, "n_events": 0 if downtime == 0 else 1,
        "daylight_minutes": 480.0,
    }


@pytest.fixture
def outputs_dir(tmp_path):
    """2 tanggal valid + 1 file tanpa sheet AllStrings + 1 nama tak dikenal."""
    _write_findings_xlsx(tmp_path / "m2_findings_20260502.xlsx", [
        _allstrings_row(pv="PV1", uptime=100.0),
        _allstrings_row(pv="PV2", uptime=80.0, downtime=96.0),
        _allstrings_row(pv="PV3", status="EMPTY", uptime=float("nan")),
    ])
    _write_findings_xlsx(tmp_path / "m2_findings_20260501.xlsx", [
        _allstrings_row(pv="PV1", uptime=98.0),
        _allstrings_row(pv="PV2", uptime=90.0, downtime=48.0),
        _allstrings_row(pv="PV3", status="EMPTY", uptime=float("nan")),
    ])
    _write_findings_xlsx(
        tmp_path / "m2_findings_20260503.xlsx",
        [{"a": 1}], sheet="Findings",   # tanpa sheet AllStrings -> skip
    )
    (tmp_path / "bukan_m2_findings.xlsx").write_bytes(b"")
    return tmp_path


def test_discover_findings_xlsx_sorted_by_date(outputs_dir):
    files = discover_findings_xlsx(str(outputs_dir))
    assert [d.strftime("%Y%m%d") for d, _ in files] == [
        "20260501", "20260502", "20260503",
    ]


def test_load_allstrings_inserts_date_and_skips_missing_sheet(outputs_dir):
    files = dict(
        (d.strftime("%Y%m%d"), p) for d, p in discover_findings_xlsx(str(outputs_dir))
    )
    df = load_allstrings(files["20260501"], pd.Timestamp("2026-05-01"))
    assert df is not None
    assert df.columns[0] == "date"
    assert (df["date"] == pd.Timestamp("2026-05-01")).all()
    assert len(df) == 3
    # File tanpa sheet AllStrings -> None (skip, bukan error).
    assert load_allstrings(files["20260503"], pd.Timestamp("2026-05-03")) is None


def _combined(outputs_dir) -> pd.DataFrame:
    frames = []
    for day, path in discover_findings_xlsx(str(outputs_dir)):
        df = load_allstrings(path, day)
        if df is not None:
            frames.append(df)
    return pd.concat(frames, ignore_index=True)


def test_build_uptime_pivot_dates_as_columns(outputs_dir):
    pivot = build_uptime_pivot(_combined(outputs_dir))
    assert list(pivot.columns) == [
        "inverter_id", "pv_string", "2026-05-01", "2026-05-02",
    ]
    pv2 = pivot[pivot["pv_string"] == "PV2"].iloc[0]
    assert pv2["2026-05-01"] == pytest.approx(90.0)
    assert pv2["2026-05-02"] == pytest.approx(80.0)


def test_build_uptime_pivot_natural_pv_order(tmp_path):
    """pv_string harus terurut natural (PV1, PV2, PV10), bukan leksikal
    (PV1, PV10, PV2) -- supaya sheet mudah dibaca berurutan."""
    _write_findings_xlsx(tmp_path / "m2_findings_20260501.xlsx", [
        _allstrings_row(inv="WB01-INV02", pv="PV1", uptime=99.0),
        _allstrings_row(inv="WB01-INV01", pv="PV10", uptime=97.0),
        _allstrings_row(inv="WB01-INV01", pv="PV2", uptime=98.0),
        _allstrings_row(inv="WB01-INV01", pv="PV1", uptime=99.0),
        _allstrings_row(inv="WB01-INV01", pv="PV11", uptime=96.0),
    ])
    pivot = build_uptime_pivot(_combined(tmp_path))
    assert list(zip(pivot["inverter_id"], pivot["pv_string"])) == [
        ("WB01-INV01", "PV1"), ("WB01-INV01", "PV2"),
        ("WB01-INV01", "PV10"), ("WB01-INV01", "PV11"),
        ("WB01-INV02", "PV1"),
    ]


def test_build_rekap_per_string_stats_and_order(outputs_dir):
    rekap = build_rekap_per_string(_combined(outputs_dir), 95.0)
    assert list(rekap.columns) == REKAP_PER_STRING_COLUMNS

    # PV2 paling bermasalah (2 hari < 95%) -> baris pertama.
    top = rekap.iloc[0]
    assert top["pv_string"] == "PV2"
    assert top["n_days"] == 2
    assert top["n_days_below_threshold"] == 2
    assert top["uptime_mean"] == pytest.approx(85.0)
    assert top["uptime_min"] == pytest.approx(80.0)
    assert top["worst_date"] == "2026-05-02"
    assert top["downtime_minutes_total"] == pytest.approx(96.0 + 48.0)

    pv1 = rekap[rekap["pv_string"] == "PV1"].iloc[0]
    assert pv1["n_days_below_threshold"] == 0

    pv3 = rekap[rekap["pv_string"] == "PV3"].iloc[0]
    assert pv3["n_days_empty"] == 2
    assert pd.isna(pv3["uptime_mean"])


# ---------------------------------------------------------------------------
# Kesadaran putus tautan
# ---------------------------------------------------------------------------

class TestRekapSadarTautan:
    """``n_days`` yang berbeda antar plant tidak boleh tanpa penjelasan.

    Phase One (WB01/WB02) menumpang fiber IconPlus, WB03-WB10 ethernet lokal.
    Fiber putus menghapus seluruh Phase One dari ekspor hari itu. Rekap ini
    mengelompokkan baris yang ADA, jadi tanggal yang hilang cuma mengecilkan
    ``n_days`` -- tanpa satu pun kolom yang memberi tahu pembacanya kenapa.

    Akibatnya ``uptime_mean`` Phase One dan WB03-WB10 dihitung atas jumlah hari
    berbeda dan diperbandingkan seolah setara.
    """

    def _long(self, baris):
        return pd.DataFrame(baris)

    def _b(self, tanggal, inv, pv="PV1", uptime=100.0):
        return {"date": pd.Timestamp(tanggal), "inverter_id": inv,
                "pv_string": pv, "status": "NORMAL", "uptime_pct": uptime,
                "downtime_minutes": 0.0}

    def test_hari_tanpa_phase_one_sama_sekali_tercatat_putus_tautan(self):
        """Seluruh kelompok absen sementara kelompok lain hadir = tautan."""
        from rekap_m2e_allstrings import build_link_audit

        long_df = self._long([
            self._b("2025-11-13", "WB01-INV01"), self._b("2025-11-13", "WB02-INV01"),
            self._b("2025-11-13", "WB05-INV01"),
            # 03 Nov: Phase One hilang seluruhnya, WB05 tetap melapor.
            self._b("2025-11-03", "WB05-INV01"),
        ])

        audit = build_link_audit(long_df)
        baris = audit[(audit["date"] == pd.Timestamp("2025-11-03"))
                      & (audit["group"] == "phase_one_iconplus_fibre")]

        assert len(baris) == 1
        assert baris.iloc[0]["verdict"] == "LINK_OUTAGE"
        assert baris.iloc[0]["present"] == 0
        assert baris.iloc[0]["expected"] == 2

    def test_hari_lengkap_tidak_masuk_audit(self):
        """Audit yang memuat setiap hari akan diabaikan orang."""
        from rekap_m2e_allstrings import build_link_audit

        long_df = self._long([
            self._b("2025-11-13", "WB01-INV01"), self._b("2025-11-13", "WB05-INV01"),
            self._b("2025-11-14", "WB01-INV01"), self._b("2025-11-14", "WB05-INV01"),
        ])

        assert build_link_audit(long_df).empty

    def test_sebagian_inverter_absen_bukan_putus_tautan(self):
        """Pemadaman inverter sungguhan tidak boleh dimaafkan sebagai tautan.

        Ini arah kegagalan yang paling mahal: label "faktor eksternal" pada
        inverter yang benar-benar mati membuatnya hilang dari perhatian.
        """
        from rekap_m2e_allstrings import build_link_audit

        long_df = self._long([
            self._b("2025-11-13", "WB01-INV01"), self._b("2025-11-13", "WB01-INV02"),
            self._b("2025-11-13", "WB05-INV01"),
            self._b("2025-11-14", "WB01-INV01"),   # INV02 absen, INV01 hadir
            self._b("2025-11-14", "WB05-INV01"),
        ])

        audit = build_link_audit(long_df)
        baris = audit[audit["date"] == pd.Timestamp("2025-11-14")]

        assert list(baris["verdict"]) == ["INVERTER_ABSENCE"]

    def test_rekap_memberi_kolom_hari_tautan_putus(self):
        """String Phase One harus membawa hitungan itu; WB03-10 tidak.

        Tanpa kolom ini ``n_days`` Phase One yang lebih kecil terbaca sebagai
        data yang lebih sedikit tanpa sebab, atau lebih buruk lagi sebagai
        pembangkit yang lebih sering mati.
        """
        from rekap_m2e_allstrings import (
            attach_link_days, build_link_audit, build_rekap_per_string,
        )

        long_df = self._long([
            self._b("2025-11-13", "WB01-INV01"), self._b("2025-11-13", "WB05-INV01"),
            self._b("2025-11-03", "WB05-INV01"),
        ])

        rekap = attach_link_days(
            build_rekap_per_string(long_df), build_link_audit(long_df),
        ).set_index("inverter_id")

        assert rekap.loc["WB01-INV01", "n_days_link_outage"] == 1
        assert rekap.loc["WB05-INV01", "n_days_link_outage"] == 0

    def test_kolom_baru_terdaftar_di_skema(self):
        """Kolom yang tidak terdaftar akan hilang saat DataFrame direindeks."""
        from rekap_m2e_allstrings import REKAP_PER_STRING_COLUMNS

        assert "n_days_link_outage" in REKAP_PER_STRING_COLUMNS
