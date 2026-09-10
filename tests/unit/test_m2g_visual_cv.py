"""Uji adapter M2g: kontrak tabel CV -> M2Finding.

Adapter ini adalah satu-satunya pintu masuk hasil citra ke aliran temuan M2,
dan letaknya di batas antara dua repositori yang di-release terpisah. Batas
semacam itu membusuk lewat satu cara: kontraknya berubah di satu sisi dan sisi
lain tetap menerima. Karena itu yang dikunci di sini bukan "kodenya jalan",
melainkan lima keputusan yang menahan pembusukan itu:

1. Pelanggaran kontrak GAGAL KERAS, tidak dilewati. Baris ber-skala 0..100
   alih-alih 0..1 akan lolos setiap ambang severity dan memancarkan CRITICAL
   untuk seluruh situs tanpa satu pun galat.
2. Baris di luar cakupan run DITOLAK. Tanpa itu, satu tabel CV lama akan
   menyuntikkan temuan bertanggal lama ke run hari ini.
3. Penolakan DICATAT, bukan didiamkan. Baris yang hilang tanpa jejak tidak bisa
   dibedakan dari string yang memang bersih.
4. Di bawah ambang terendah TIDAK memancarkan apa pun. Memancarkan NORMAL untuk
   tiap string bersih akan menenggelamkan lembar temuan.
5. Default-nya MATI. Detektor baru yang menyala sendiri akan mengubah keluaran
   setiap run yang sudah berjalan hari ini.
"""
from __future__ import annotations

import pandas as pd
import pytest

from pv_pipeline.core import Severity
from pv_pipeline.m2g.visual_cv import (
    CV_TABLE_COLUMNS,
    REJECTED_COLUMNS,
    M2gVisualCV,
    load_cv_table,
)

_INV = "WB03-INV01"
_TGL = "2026-09-09"


def _baris(pv=5, tanggal=_TGL, index=0.62, conf=0.81, inv=_INV) -> dict:
    return {
        "inverter_id": inv, "pv": pv, "date": tanggal,
        "soiling_index": index, "confidence": conf,
        "source_image": "DJI_UJI_0107.JPG",
    }


def _tulis_csv(path, baris) -> str:
    pd.DataFrame(baris, columns=CV_TABLE_COLUMNS).to_csv(path, index=False)
    return str(path)


def _geom(pasangan=((_INV, 5),)) -> pd.DataFrame:
    return pd.DataFrame([{"inverter_id": i, "pv": p} for i, p in pasangan])


def _combined(inv=_INV, tanggal=_TGL) -> pd.DataFrame:
    return pd.DataFrame({
        "Inverter_ID": [inv],
        "Start Time": [pd.Timestamp(f"{tanggal} 10:00:00")],
    })


def _cfg(**ganti) -> dict:
    dasar = {"enabled": True}
    dasar.update(ganti)
    return {"m2g_visual_cv": dasar}


class TestLoadCvTable:
    def test_skala_di_luar_0_1_ditolak(self, tmp_path):
        """Skala 0..100 harus jadi galat, bukan banjir CRITICAL.

        Ini kegagalan paling berbahaya di seluruh sambungan: tabel yang
        skalanya berubah tetap punya kolom lengkap dan tipe benar, jadi
        satu-satunya yang bisa menangkapnya adalah pemeriksaan rentang.
        """
        p = _tulis_csv(tmp_path / "cv.csv", [_baris(index=62.0)])
        with pytest.raises(ValueError, match=r"soiling_index di luar 0\.\.1"):
            load_cv_table(p)

    def test_confidence_di_luar_0_1_ditolak(self, tmp_path):
        p = _tulis_csv(tmp_path / "cv.csv", [_baris(conf=81.0)])
        with pytest.raises(ValueError, match=r"confidence di luar 0\.\.1"):
            load_cv_table(p)

    def test_kolom_hilang_disebut_namanya(self, tmp_path):
        """Pesan galat harus menyebut kolom mana, bukan sekadar "tidak valid"."""
        df = pd.DataFrame([_baris()]).drop(columns=["confidence"])
        p = tmp_path / "cv.csv"
        df.to_csv(p, index=False)
        with pytest.raises(ValueError, match="confidence"):
            load_cv_table(str(p))

    def test_duplikat_string_hari_ditolak(self, tmp_path):
        """Dua nilai untuk satu string-hari; memilih salah satu berarti menebak."""
        p = _tulis_csv(tmp_path / "cv.csv", [_baris(index=0.4), _baris(index=0.9)])
        with pytest.raises(ValueError, match="duplikat"):
            load_cv_table(p)

    def test_tabel_kosong_ditolak(self, tmp_path):
        """Nol baris lebih mungkin berarti proses hulu gagal, bukan situs bersih."""
        p = _tulis_csv(tmp_path / "cv.csv", [])
        with pytest.raises(ValueError, match="kosong"):
            load_cv_table(p)

    def test_berkas_tidak_ada_ditolak(self, tmp_path):
        with pytest.raises(ValueError, match="tidak ada"):
            load_cv_table(str(tmp_path / "belum_ada.csv"))

    def test_tabel_sah_terbaca_dengan_tipe_benar(self, tmp_path):
        df = load_cv_table(_tulis_csv(tmp_path / "cv.csv", [_baris()]))
        assert list(df.columns) == CV_TABLE_COLUMNS
        assert df.loc[0, "date"] == pd.Timestamp(_TGL)
        assert int(df.loc[0, "pv"]) == 5


class TestGerbangConfig:
    def test_default_mati(self):
        """Detektor baru tidak boleh mengubah keluaran run yang sudah ada.

        Tanpa section config, ``run()`` harus mengembalikan daftar kosong tanpa
        menyentuh berkas apa pun -- termasuk tanpa mencoba membaca tabel CV yang
        memang belum ada.
        """
        sm = M2gVisualCV()
        assert sm.run(_combined(), {}) == []

    def test_ambang_tidak_menaik_gagal_keras(self, tmp_path):
        """medium > high akan membuat pemetaan severity tak berarti."""
        sm = M2gVisualCV(
            cv_table=load_cv_table(_tulis_csv(tmp_path / "cv.csv", [_baris()])),
            geom=_geom(),
        )
        with pytest.raises(ValueError, match="ambang harus menaik"):
            sm.run(_combined(), _cfg(index_medium=0.8, index_high=0.5))

    def test_combined_df_tanpa_kolom_kunci_memperingatkan_dan_kosong(self, tmp_path):
        """Cakupan tak terdefinisi -> tidak memancarkan, dan bilang kenapa.

        Diam-diam memancarkan semuanya akan melewati penjagaan cakupan; diam
        tanpa peringatan membuat run yang salah konfigurasi terlihat normal.
        """
        sm = M2gVisualCV(
            cv_table=load_cv_table(_tulis_csv(tmp_path / "cv.csv", [_baris()])),
            geom=_geom(),
        )
        with pytest.warns(UserWarning, match="cakupan run"):
            assert sm.run(pd.DataFrame({"lain": [1]}), _cfg()) == []


class TestPemetaanSeverity:
    @pytest.mark.parametrize("index,harap", [
        (0.34, None),
        (0.35, Severity.MEDIUM),
        (0.54, Severity.MEDIUM),
        (0.55, Severity.HIGH),
        (0.74, Severity.HIGH),
        (0.75, Severity.CRITICAL),
        (1.00, Severity.CRITICAL),
    ])
    def test_batas_ambang_inklusif_ke_atas(self, tmp_path, index, harap):
        """Nilai TEPAT di ambang naik kelas, bukan tetap di kelas bawah.

        Diuji di batasnya karena di situlah kekeliruan < vs <= bersembunyi, dan
        di situ pula selisihnya menentukan apakah satu string masuk daftar
        pembersihan atau tidak.
        """
        sm = M2gVisualCV(
            cv_table=load_cv_table(
                _tulis_csv(tmp_path / "cv.csv", [_baris(index=index)])),
            geom=_geom(),
        )
        hasil = sm.run(_combined(), _cfg())
        if harap is None:
            assert hasil == []
        else:
            assert len(hasil) == 1 and hasil[0].severity is harap

    def test_di_bawah_ambang_tidak_memancarkan_normal(self, tmp_path):
        """String bersih tidak menghasilkan baris apa pun.

        4.470 baris NORMAL per hari akan menenggelamkan lembar temuan sampai
        tidak ada yang membacanya lagi.
        """
        sm = M2gVisualCV(
            cv_table=load_cv_table(
                _tulis_csv(tmp_path / "cv.csv", [_baris(index=0.05)])),
            geom=_geom(),
        )
        assert sm.run(_combined(), _cfg()) == []
        assert "VisualCVRejected" not in sm.artifacts


class TestPenolakan:
    def test_tanggal_di_luar_cakupan_run_ditolak(self, tmp_path):
        """Tabel CV lama tidak boleh menyuntik temuan ke run hari ini.

        Skenario nyatanya sederhana: pipeline CV berjalan mingguan, pipeline M2
        harian. Tanpa penjagaan ini, temuan 9 September akan muncul lagi di
        setiap run harian sesudahnya.
        """
        sm = M2gVisualCV(
            cv_table=load_cv_table(
                _tulis_csv(tmp_path / "cv.csv", [_baris(tanggal="2026-09-01")])),
            geom=_geom(),
        )
        hasil = sm.run(_combined(tanggal=_TGL), _cfg())
        assert hasil == []
        tolak = sm.artifacts["VisualCVRejected"]
        assert list(tolak.columns) == REJECTED_COLUMNS
        assert "cakupan" in tolak.loc[0, "alasan"]

    def test_string_asing_ditolak_terhadap_geometri(self, tmp_path):
        """Identitas diverifikasi ke as-built, bukan diterima apa adanya.

        Kekeliruan penomoran di sisi CV akan menempelkan indeks ke string yang
        tidak ada; namanya tetap terlihat sah (``WB03-INV01-PV99``).
        """
        sm = M2gVisualCV(
            cv_table=load_cv_table(
                _tulis_csv(tmp_path / "cv.csv", [_baris(pv=99)])),
            geom=_geom(),
        )
        assert sm.run(_combined(), _cfg()) == []
        assert "geometri as-built" in (
            sm.artifacts["VisualCVRejected"].loc[0, "alasan"]
        )

    def test_confidence_rendah_ditolak_dan_dicatat(self, tmp_path):
        """Dibuang, tetapi meninggalkan jejak.

        Baris yang hilang tanpa catatan tidak bisa dibedakan dari string yang
        memang bersih -- padahal artinya berlawanan: yang satu belum terperiksa.
        """
        sm = M2gVisualCV(
            cv_table=load_cv_table(
                _tulis_csv(tmp_path / "cv.csv", [_baris(index=0.9, conf=0.30)])),
            geom=_geom(),
        )
        assert sm.run(_combined(), _cfg()) == []
        assert "confidence 0.30" in (
            sm.artifacts["VisualCVRejected"].loc[0, "alasan"]
        )


class TestBentukTemuan:
    def test_temuan_membawa_berkas_sumber_untuk_telusur_balik(self, tmp_path):
        """Tanpa nama bingkai, temuan tidak bisa diperiksa ulang manusia.

        Seluruh nilai lapisan CV bergantung pada seseorang bisa membuka citra
        yang memicunya sebelum menurunkan perintah kerja.
        """
        sm = M2gVisualCV(
            cv_table=load_cv_table(
                _tulis_csv(tmp_path / "cv.csv", [_baris(index=0.8, conf=0.9)])),
            geom=_geom(),
        )
        f = sm.run(_combined(), _cfg())[0]
        assert f.evidence["source_image"] == "DJI_UJI_0107.JPG"
        assert f.evidence["cv_confidence"] == pytest.approx(0.9)
        assert f.confidence == pytest.approx(90.0)
        assert f.inverter_id == _INV and f.pv_string == "PV5"
        assert f.sub_module == "M2g_visual_cv"
        assert f.value == pytest.approx(0.8)

    def test_pesan_menyatakan_belum_terkalibrasi(self, tmp_path):
        """Peringatan itu bagian dari temuan, bukan catatan kaki dokumen.

        PRD menempatkan citra sebagai lapisan prioritisasi sampai dikalibrasi ke
        besaran listrik. Temuan yang dibaca tanpa kalimat itu akan dikutip
        sebagai angka rugi -- persis yang PRD larang.
        """
        sm = M2gVisualCV(
            cv_table=load_cv_table(
                _tulis_csv(tmp_path / "cv.csv", [_baris(index=0.8)])),
            geom=_geom(),
        )
        assert "belum terkalibrasi" in sm.run(_combined(), _cfg())[0].message
