"""Probe murah atas data Drive -- empat pertanyaan yang tidak butuh run penuh.

Yang dijaga di sini bukan "fungsinya menghitung sesuatu", melainkan empat cara
spesifik probe semacam ini gagal DIAM-DIAM, masing-masing sudah pernah memakan
korban di proyek ini:

1. Regex peka huruf menjatuhkan PV15+ tanpa galat, karena satu berkas memakai
   dua konvensi huruf (``PV1 input current(A)`` lawan ``PV15 Input Current(A)``).
   Probe yang cuma menghitung 14 kanal akan terbaca sebagai "ekspor format lama".
2. Inverter yang tidak melapor dibaca sebagai kanal bernilai nol. Persis ini yang
   menggantung WB05-INV05 PV9: pada hari yang diuji inverternya diam, dan 0.00 kW
   dari inverter mati tidak membuktikan kanalnya kosong.
3. Hari mendung total ikut terpilih sebagai "berawan sebagian". Mendung total
   tidak punya bayangan bergerak -- iradiansnya difus dan merata, jadi tidak ada
   sinyal spasial untuk dikorelasikan. Yang dicari hari cerah yang DIINTERUPSI.
4. Bulan yang foldernya ada tapi kosong hilang dari inventaris, sehingga terbaca
   sama dengan bulan yang memang tidak pernah diekspor.
"""

from pathlib import Path

import pandas as pd
import pytest

from pv_pipeline.drive_probe import (
    inventory_baseline,
    probe_channel_silence,
    probe_inverter_coverage,
    rank_variable_days,
)


# --------------------------------------------------------------------------
# Pembantu: CSV baseline sintetis
# --------------------------------------------------------------------------

def _tulis_csv(path: Path, baris: list, kolom_pv: list) -> Path:
    kolom = ["ManageObject", "Start Time", *kolom_pv]
    pd.DataFrame(baris, columns=kolom).to_csv(path, index=False)
    return path


def _kolom_arus(n: int) -> str:
    """Konvensi huruf Huawei: kecil sampai PV14, besar mulai PV15."""
    return (f"PV{n} input current(A)" if n <= 14
            else f"PV{n} Input Current(A)")


def _kolom_tegangan(n: int) -> str:
    return (f"PV{n} input voltage(V)" if n <= 14
            else f"PV{n} Input Voltage(V)")


# --------------------------------------------------------------------------
# Probe A -- cakupan inverter dan kanal dalam satu CSV
# --------------------------------------------------------------------------

class TestProbeInverterCoverage:

    def test_phase_one_dikenali_dari_penamaan_logger(self, tmp_path):
        """``Inv_A_1xx``/``Inv_B_2xx`` adalah WB01/WB02, bukan inverter asing.

        Kalau probe hanya mengenali ``WBnn-INVmm``, Phase One akan dilaporkan
        absen padahal ada -- dan itu vonis yang salah untuk pertanyaan yang
        justru sedang ditanyakan ("apakah ekspor periode ini memuat Phase One").
        """
        csv = _tulis_csv(
            tmp_path / "hari.csv",
            [
                {"ManageObject": "Logger-1/Inv_A_201_IKN",
                 "Start Time": "2025-11-04 10:00:00"},
                {"ManageObject": "Logger-1/Inv_B_125_IKN",
                 "Start Time": "2025-11-04 10:00:00"},
                {"ManageObject": "WB05-INV05",
                 "Start Time": "2025-11-04 10:00:00"},
            ],
            [_kolom_arus(1)],
        )
        hasil = probe_inverter_coverage(csv)

        assert hasil.phase_one == ["WB01-INV25", "WB02-INV01"]
        assert hasil.phase_two == ["WB05-INV05"]

    def test_kanal_huruf_besar_ikut_terhitung(self, tmp_path):
        """PV15+ pakai Title Case; melewatkannya = salah baca format ekspor.

        Ini kegagalan yang benar-benar terjadi: hitungan 14 kanal dipakai untuk
        menyimpulkan "700 string", padahal kanalnya 18 dan stringnya 900.
        """
        kolom = [_kolom_arus(n) for n in (1, 14, 15, 28)]
        csv = _tulis_csv(
            tmp_path / "hari.csv",
            [{"ManageObject": "WB05-INV05", "Start Time": "2025-11-04 10:00:00"}],
            kolom,
        )
        hasil = probe_inverter_coverage(csv)

        assert hasil.pv_channels == [1, 14, 15, 28]
        assert hasil.channels_titlecase == [15, 28], (
            "pemisahan konvensi huruf harus terlihat, supaya pembaca tahu "
            "kedua ragam memang terbaca dan bukan kebetulan"
        )

    def test_berkas_tanpa_manage_object_menyalak(self, tmp_path):
        """Berkas cacat harus bergalat, bukan melapor "nol inverter".

        Nol inverter terbaca sebagai "ekspor format lama" -- kesimpulan yang
        salah dari sebab yang salah. Probe ini dipakai untuk memvonis format,
        jadi ia tidak boleh punya jalan keluar yang diam.
        """
        path = tmp_path / "cacat.csv"
        pd.DataFrame({"Start Time": ["2025-11-04 10:00:00"]}).to_csv(
            path, index=False)

        with pytest.raises(ValueError, match="ManageObject"):
            probe_inverter_coverage(path)


# --------------------------------------------------------------------------
# Probe B -- hari berawan sebagian
# --------------------------------------------------------------------------

def _poa_hari(tanggal: str, nilai: list) -> pd.Series:
    idx = pd.date_range(f"{tanggal} 09:00", periods=len(nilai), freq="5min")
    return pd.Series(nilai, index=idx, dtype=float)


class TestRankVariableDays:

    def test_hari_berawan_mengungguli_hari_cerah(self):
        """Hari cerah punya variabilitas ~0; ia bukan yang dicari.

        Uji korelasi spasial hidup dari bayangan awan yang BERGERAK. Hari cerah
        stabil tidak memberi kontras apa pun antar string, jadi peringkatnya
        harus di bawah hari yang iradiansnya naik-turun.
        """
        cerah = _poa_hari("2026-06-20", [800.0] * 12)
        berawan = _poa_hari("2026-06-21", [800, 300, 850, 250, 900, 200] * 2)
        poa = pd.concat([cerah, berawan])

        peringkat = rank_variable_days(poa, midday=(9, 15), min_mean_poa=400.0)

        assert list(peringkat["tanggal"].astype(str)) == ["2026-06-21", "2026-06-20"]
        assert peringkat.iloc[0]["variabilitas"] > peringkat.iloc[1]["variabilitas"]

    def test_mendung_total_ditandai_kurang_terang(self):
        """Mendung total punya variabilitas relatif tinggi tapi tanpa bayangan.

        Iradians difus dan rendah: rasio langkah bisa besar justru karena
        penyebutnya kecil. Hari begini harus ditandai TIDAK memenuhi lantai
        supaya tidak naik ke puncak daftar -- tapi tetap ditampilkan, bukan
        dibuang diam-diam.
        """
        mendung = _poa_hari("2026-06-22", [90, 40, 110, 35, 120, 45] * 2)
        poa = pd.concat([_poa_hari("2026-06-20", [800.0] * 12), mendung])

        peringkat = rank_variable_days(poa, midday=(9, 15), min_mean_poa=400.0)
        baris = peringkat[peringkat["tanggal"].astype(str) == "2026-06-22"]

        assert len(baris) == 1, "hari gagal-lantai tetap dilaporkan"
        assert not bool(baris.iloc[0]["cukup_terang"])

    def test_ramp_pagi_sore_tidak_dihitung_sebagai_awan(self):
        """Naik-turun matahari itu geometri, bukan cuaca.

        Kalau jendela tidak dibatasi, setiap hari cerah tampak sangat variabel
        karena fajar dan senja -- dan daftar hasilnya jadi tak berguna.
        """
        idx = pd.date_range("2026-06-20 06:00", periods=24, freq="30min")
        # Ramp naik lalu turun, mulus: 06:00 rendah, tengah hari tinggi.
        nilai = [max(0.0, 900 - abs(i - 12) * 80) for i in range(24)]
        poa = pd.Series(nilai, index=idx, dtype=float)

        sempit = rank_variable_days(poa, midday=(11, 13), min_mean_poa=400.0)
        lebar = rank_variable_days(poa, midday=(6, 18), min_mean_poa=400.0)

        assert sempit.iloc[0]["variabilitas"] < lebar.iloc[0]["variabilitas"]


# --------------------------------------------------------------------------
# Probe C -- kanal diam lawan inverter diam
# --------------------------------------------------------------------------

class TestProbeChannelSilence:

    def _csv_inverter(self, tmp_path, manage_object, arus_per_pv):
        kolom = []
        baris = {"ManageObject": manage_object,
                 "Start Time": "2025-11-04 12:00:00"}
        for n, arus in arus_per_pv.items():
            baris[_kolom_tegangan(n)] = 600.0
            baris[_kolom_arus(n)] = arus
            kolom += [_kolom_tegangan(n), _kolom_arus(n)]
        return _tulis_csv(tmp_path / "hari.csv", [baris], kolom)

    def test_kanal_diam_di_antara_saudara_sehat_terbukti_kosong(self, tmp_path):
        """Inilah aturan ``disprove_empty_channel``, diuji langsung.

        Kanal ~0 kW pada tengah hari sementara saudara se-inverter menarik
        beberapa kW: pemetaan as-built ke kanal itu terbantah.
        """
        csv = self._csv_inverter(
            tmp_path, "WB05-INV05", {9: 0.0, 10: 5.5, 11: 5.4, 12: 5.6},
        )
        hasil = probe_channel_silence(csv, "WB05-INV05", 9)

        assert hasil["inverter_hadir"] is True
        assert hasil["putusan"] == "KOSONG_TERBUKTI"
        assert hasil["target_kw"] == pytest.approx(0.0, abs=1e-6)
        assert hasil["saudara_kw"] > 3.0

    def test_inverter_tidak_melapor_bukan_nol(self, tmp_path):
        """Kasus WB05-INV05 pada 2026-05-13, dikunci sebagai tes.

        Inverter absen dari berkas. Mengembalikan 0.0 kW di sini akan MEMBUKTIKAN
        kanal kosong dari ketiadaan data -- kesimpulan yang benar secara kebetulan
        pun tetap tidak sah. Probe harus bilang tidak tahu.
        """
        csv = self._csv_inverter(
            tmp_path, "WB05-INV06", {9: 5.0, 10: 5.5},
        )
        hasil = probe_channel_silence(csv, "WB05-INV05", 9)

        assert hasil["inverter_hadir"] is False
        assert hasil["putusan"] == "TIDAK_MELAPOR"
        assert hasil["target_kw"] is None, (
            "None, bukan 0.0 -- ketiadaan data bukan nol")

    def test_kanal_hidup_membatalkan_klaim_kosong(self, tmp_path):
        """Kalau kanalnya justru berproduksi, pemetaan as-built-nya benar."""
        csv = self._csv_inverter(
            tmp_path, "WB05-INV05", {9: 5.2, 10: 5.5, 11: 5.4},
        )
        hasil = probe_channel_silence(csv, "WB05-INV05", 9)

        assert hasil["putusan"] == "TERPAKAI"


# --------------------------------------------------------------------------
# Probe D -- inventaris bulan di Drive
# --------------------------------------------------------------------------

class TestInventoryBaseline:

    def test_bulan_kosong_dilaporkan_nol_bukan_dihilangkan(self, tmp_path):
        """"Foldernya ada tapi kosong" beda arti dari "belum pernah diekspor".

        Yang pertama berarti ekspornya gagal dan bisa diulang; yang kedua berarti
        periodenya memang tidak tersedia. Menghilangkan barisnya menyamakan
        keduanya, dan jendela musim ketiga dipilih dari tabel ini.
        """
        (tmp_path / "2026-03").mkdir()
        (tmp_path / "2026-04").mkdir()
        (tmp_path / "2026-04" / "2026-04-01.csv").write_text("x", encoding="utf-8")

        inv = inventory_baseline(tmp_path)

        assert list(inv["bulan"]) == ["2026-03", "2026-04"]
        assert list(inv["n_hari"]) == [0, 1]

    def test_doy_tengah_untuk_memilih_jendela_musim_ketiga(self, tmp_path):
        """Pemisahan musim diukur pada day-of-year, bukan nama bulan.

        Dua jendela yang ada berjarak doy 166 lawan 335. Yang ketiga berguna
        kalau jatuh di antaranya, dan kolom ini yang dipakai menilai.
        """
        (tmp_path / "2026-03").mkdir()
        for hari in ("2026-03-10", "2026-03-20"):
            (tmp_path / "2026-03" / f"{hari}.csv").write_text("x", encoding="utf-8")

        inv = inventory_baseline(tmp_path)
        baris = inv.iloc[0]

        assert baris["n_hari"] == 2
        assert str(baris["tanggal_awal"]) == "2026-03-10"
        assert str(baris["tanggal_akhir"]) == "2026-03-20"
        assert baris["doy_tengah"] == 74  # 2026-03-15
