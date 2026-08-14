"""Tes untuk ``pv_pipeline.availability`` -- deteksi putus tautan telemetri.

Situs mengekspor dari DUA Fusion Solar di transport berbeda: Phase One
(WB01/WB02) lewat fiber IconPlus, WB03-WB10 lewat ethernet lokal. Fiber putus
menghapus 900 string Phase One dari ekspor hari itu.

Yang dijaga di sini BUKAN "absen jangan dihitung downtime" -- itu sudah benar
dengan sendirinya, karena inverter tanpa baris tidak menghasilkan grup sehingga
tidak ada temuan apa pun, dan status kosong dipetakan UNKNOWN yang tidak masuk
penyebut. Yang dijaga adalah bahwa absennya tidak SENYAP. Ketiadaan yang tak
terlaporkan membuat rerata uptime lintas tanggal dihitung atas jumlah hari yang
berbeda antar plant tanpa satu pun penanda, dan pembaca tidak punya cara
mengetahuinya.

Pembedanya jumlah, bukan kehadiran: SELURUH kelompok hilang serentak berarti
tautan, sebagian berarti inverter. Kelompok di transport lain adalah kontrolnya.
"""

from __future__ import annotations

import pandas as pd

from pv_pipeline.availability import (
    TELEMETRY_LINK_GROUPS,
    detect_link_outage,
)


def _roster():
    """Roster kecil: 4 inverter Phase One, 4 inverter WB03-10."""
    return (
        [f"WB01-INV{n:02d}" for n in (1, 2)]
        + [f"WB02-INV{n:02d}" for n in (1, 2)]
        + [f"WB05-INV{n:02d}" for n in (1, 2)]
        + [f"WB07-INV{n:02d}" for n in (1, 2)]
    )


def _cari(baris, grup):
    cocok = [b for b in baris if b["group"] == grup]
    return cocok[0] if cocok else None


class TestDetectLinkOutage:

    def test_seluruh_phase_one_hilang_adalah_putus_tautan(self):
        """Semua hilang serentak + kelompok lain hadir = tautan, bukan pembangkit.

        Inilah kejadian 2025-11-03: nol inverter Phase One sementara WB03-10
        melapor normal. Tanpa baris ini, satu-satunya jejaknya adalah rerata
        uptime Phase One yang diam-diam dihitung atas hari lebih sedikit.
        """
        hadir = [i for i in _roster() if not i.startswith(("WB01", "WB02"))]

        baris = detect_link_outage(hadir, _roster())
        p1 = _cari(baris, "phase_one_iconplus_fibre")

        assert p1 is not None
        assert p1["verdict"] == "LINK_OUTAGE"
        assert p1["present"] == 0
        assert p1["expected"] == 4
        assert p1["control_present"] is True

    def test_sebagian_hilang_adalah_inverter_bukan_tautan(self):
        """Subhimpunan hilang = inverternya, dan itu temuan yang sah.

        Menyebutnya "tautan" akan menyembunyikan pemadaman inverter sungguhan di
        balik alasan eksternal -- persis kebalikan dari yang diinginkan aturan
        ini. Nama yang hilang harus ikut, karena itulah yang ditindaklanjuti.
        """
        hadir = [i for i in _roster() if i != "WB01-INV02"]

        p1 = _cari(detect_link_outage(hadir, _roster()), "phase_one_iconplus_fibre")

        assert p1["verdict"] == "INVERTER_ABSENCE"
        assert p1["missing_inverters"] == ["WB01-INV02"]

    def test_kedua_kelompok_hilang_bukan_putus_tautan(self):
        """Tanpa kontrol yang hadir, penyebabnya tak bisa ditimpakan ke satu jalur.

        Kalau semuanya hilang, mengklaim "eksternal, pembangkit baik-baik saja"
        tidak berdasar -- yang benar mengaku tidak ada datanya.
        """
        baris = detect_link_outage([], _roster())

        for grup in ("phase_one_iconplus_fibre", "wb03_10_local_ethernet"):
            b = _cari(baris, grup)
            assert b["verdict"] == "NO_DATA", grup
            assert b["control_present"] is False

    def test_lengkap_tidak_melaporkan_apa_apa(self):
        """Hari normal tidak boleh menghasilkan baris -- penanda harus langka."""
        assert detect_link_outage(_roster(), _roster()) == []

    def test_inverter_di_luar_kelompok_mana_pun_tidak_hilang_diam_diam(self):
        """Roster bisa memuat blok yang belum terpetakan ke transport.

        Membuangnya tanpa suara berarti pemadaman di blok itu tidak akan pernah
        terlihat oleh pemeriksaan ini.
        """
        roster = _roster() + ["WB99-INV01"]

        baris = detect_link_outage([], roster)
        lain = _cari(baris, "tak_terpetakan")

        assert lain is not None
        assert lain["missing_inverters"] == ["WB99-INV01"]

    def test_kelompok_mencakup_seluruh_blok_situs(self):
        """WB01..WB10 harus terpetakan; blok yang lupa didaftarkan jadi buta."""
        terpetakan = {b for blok in TELEMETRY_LINK_GROUPS.values() for b in blok}
        assert terpetakan == {f"WB{n:02d}" for n in range(1, 11)}


class TestLinkOutageWiredIntoRun:
    """Deteksinya harus otomatis, bukan fungsi yang menunggu dipanggil orang."""

    def _yaml_roster(self, tmp_path, inverters):
        p = tmp_path / "strings.yaml"
        isi = ["empty_pv_map:"] + [f"  {i}: [19, 20]" for i in inverters]
        p.write_text("\n".join(isi) + "\n", encoding="utf-8")
        return str(p)

    def _df(self, inverters):
        ts = pd.date_range("2025-11-03 08:00", periods=4, freq="30min")
        return pd.DataFrame([
            {"Inverter_ID": inv, "Start Time": t,
             "Inverter status": "Grid connected"}
            for inv in inverters for t in ts
        ])

    def test_run_memancarkan_temuan_putus_tautan(self, tmp_path):
        """Tanpa ini, aturannya cuma prosa di PRD yang harus diingat orang."""
        from pv_pipeline.availability import M2eAvailability

        roster = _roster()
        hadir = [i for i in roster if not i.startswith(("WB01", "WB02"))]
        cfg = {"m2e": {
            "empty_pv_map_path": self._yaml_roster(tmp_path, roster),
            "inverter_status_map": {"on_grid_keywords": ["grid connected"]},
        }}

        temuan = M2eAvailability().run(self._df(hadir), cfg)
        tautan = [f for f in temuan if f.sub_module == "M2e_link"]

        assert len(tautan) == 1
        f = tautan[0]
        assert f.severity.value == "INFO", (
            "putus tautan bukan cacat pembangkit; severity yang lebih tinggi "
            "akan mengirim orang ke peralatan yang sehat"
        )
        assert "downtime" in f.message.lower()

    def test_hari_lengkap_tidak_memancarkan_temuan_tautan(self, tmp_path):
        """Penanda yang muncul tiap hari akan diabaikan orang."""
        from pv_pipeline.availability import M2eAvailability

        roster = _roster()
        cfg = {"m2e": {
            "empty_pv_map_path": self._yaml_roster(tmp_path, roster),
            "inverter_status_map": {"on_grid_keywords": ["grid connected"]},
        }}

        temuan = M2eAvailability().run(self._df(roster), cfg)

        assert [f for f in temuan if f.sub_module == "M2e_link"] == []
