"""Uji korelasi spasial -- memilih penempatan mana yang benar dari bayangan awan.

Open Question 8 baru setengah terjawab. Yang terbukti: penempatan DXF untuk
empat inverter tepi utara SALAH. Yang belum: penempatan mana yang BENAR.

Gagasan ujinya fisika sederhana. Pada hari berawan sebagian, bayangan awan
bergerak melintasi larik dan menaungi string yang berdekatan pada saat yang
hampir sama. Jadi setelah sinyal iradians se-situs dibuang, sisa fluktuasi dua
string yang berdekatan harus lebih berkorelasi daripada dua string yang
berjauhan. Penempatan yang BENAR menghasilkan peluruhan korelasi terhadap jarak
yang tajam; penempatan yang salah mengacaknya mendekati nol.

Tiga cara uji semacam ini menipu, dan ketiganya dijaga di sini:

1. Tanpa membuang sinyal se-situs, SEMUA string berkorelasi tinggi -- mereka
   sama-sama mengikuti matahari. Peluruhan terhadap jarak akan tampak di
   penempatan apa pun, termasuk yang koordinatnya diacak.
2. Tanpa KONTROL pada string yang penempatannya tidak dibantah, tidak ada yang
   membuktikan metodenya punya daya pisah pada data hari itu. Memilih pemenang
   dari dua skor yang sama-sama lemah adalah lempar koin berbaju bukti -- dan
   Open Question 8 sudah sekali terbakar oleh klaim penempatan.
3. Dua kandidat yang skornya berdekatan bukan pemenang dan pecundang,
   melainkan seri. Selisih di bawah ambang harus dilaporkan sebagai seri.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pv_pipeline.spatial_correlation import (
    DEFAULT_MIN_CONTROL_RHO,
    decay_score,
    pairwise_correlation,
    residual_after_site_median,
    verdict_placement,
)


# --------------------------------------------------------------------------
# Pembangkit data sintetis
# --------------------------------------------------------------------------

N_STRING = 12
JARAK_M = 20.0          # jarak antar string pada satu garis timur-barat


def _koordinat_benar():
    """String berderet lurus: string i pada east = i * 20 m."""
    return {f"S{i}": (0.0, i * JARAK_M) for i in range(N_STRING)}


def _koordinat_acak(seed=0):
    """Posisi yang sama, tapi ditempelkan ke string yang salah."""
    rng = np.random.default_rng(seed)
    urut = list(range(N_STRING))
    rng.shuffle(urut)
    return {f"S{i}": (0.0, j * JARAK_M) for i, j in enumerate(urut)}


def _hari_berawan(seed=1, n_langkah=84):
    """Awan bergerak dari barat ke timur; string dekat ternaungi bersamaan.

    Ditambah derau per string supaya korelasinya tidak sempurna, dan sinyal
    matahari se-situs berbentuk kubah supaya normalisasi punya sesuatu untuk
    dibuang.
    """
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2026-06-08 09:00", periods=n_langkah, freq="5min")
    surya = 700 + 200 * np.sin(np.linspace(0, np.pi, n_langkah))

    lebar = 45.0                                   # radius bayangan, meter
    pusat = np.linspace(-60.0, N_STRING * JARAK_M + 60.0, n_langkah)

    baris = []
    for i in range(N_STRING):
        x = i * JARAK_M
        teduh = np.exp(-((pusat - x) ** 2) / (2 * lebar ** 2))
        daya = surya * (1.0 - 0.55 * teduh) / 100.0
        daya = daya * (1.0 + rng.normal(0, 0.01, n_langkah))
        for t, p in zip(ts, daya):
            baris.append({"ts": t, "pv_string": f"S{i}", "power_kw": float(p)})
    return pd.DataFrame(baris)


def _hari_mendung_merata(seed=2, n_langkah=84):
    """Seluruh situs turun-naik BERSAMAAN, tanpa struktur ruang sama sekali."""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2026-06-09 09:00", periods=n_langkah, freq="5min")
    global_ = 700 + 300 * rng.normal(0, 1, n_langkah).cumsum() / 10.0

    baris = []
    for i in range(N_STRING):
        daya = (global_ / 100.0) * (1.0 + rng.normal(0, 0.01, n_langkah))
        for t, p in zip(ts, daya):
            baris.append({"ts": t, "pv_string": f"S{i}", "power_kw": float(p)})
    return pd.DataFrame(baris)


# --------------------------------------------------------------------------


class TestResidualAfterSiteMedian:

    def test_sinyal_se_situs_dibuang(self):
        """Naik-turun serentak tidak boleh menyisakan korelasi.

        Ini jebakan nomor satu. Tanpa pembagian terhadap median se-situs,
        seluruh string berkorelasi ~1 karena sama-sama mengikuti matahari dan
        awan besar -- dan peluruhan terhadap jarak akan muncul pada penempatan
        APA PUN, termasuk yang koordinatnya diacak.
        """
        wide = residual_after_site_median(_hari_mendung_merata())
        pasangan = pairwise_correlation(wide)

        assert len(pasangan) > 0
        assert pasangan["r"].abs().median() < 0.30, (
            "sinyal bersama masih tersisa di residual"
        )

    def test_hanya_jendela_tengah_hari_yang_dipakai(self):
        """Fajar dan senja rasionya liar karena penyebutnya mendekati nol."""
        df = _hari_berawan()
        pagi = df.copy()
        pagi["ts"] = pagi["ts"] - pd.Timedelta(hours=4)   # geser ke 05:00
        wide = residual_after_site_median(pd.concat([df, pagi]), midday=(9, 15))

        assert wide.index.hour.min() >= 9
        assert wide.index.hour.max() <= 15


class TestDecayScore:

    def test_penempatan_benar_memberi_peluruhan_negatif_tajam(self):
        """Awan bergerak membuat string berdekatan turun bersamaan.

        Kalau ini tidak muncul pada data sintetis yang jelas-jelas punya
        struktur, uji ini tidak akan pernah bisa memutuskan apa pun di lapangan.
        """
        wide = residual_after_site_median(_hari_berawan())
        pasangan = pairwise_correlation(wide)

        skor = decay_score(pasangan, _koordinat_benar())

        assert skor["n_pasangan"] == N_STRING * (N_STRING - 1) // 2
        assert skor["rho"] < -0.5, skor

    def test_koordinat_yang_diacak_kehilangan_peluruhan(self):
        """Penempatan salah harus menghasilkan skor yang jelas lebih lemah.

        Inilah seluruh dasar ujinya. Kalau koordinat acak memberi skor sekuat
        koordinat benar, metodenya tidak mengukur ruang -- ia mengukur sesuatu
        yang lain.
        """
        wide = residual_after_site_median(_hari_berawan())
        pasangan = pairwise_correlation(wide)

        benar = decay_score(pasangan, _koordinat_benar())
        acak = decay_score(pasangan, _koordinat_acak())

        assert benar["rho"] < acak["rho"] - 0.2, (benar, acak)

    def test_pasangan_tanpa_koordinat_dilewati_bukan_dianggap_nol(self):
        """Koordinat yang hilang bukan jarak nol.

        Empat inverter tepi utara persis punya kolom yang dikosongkan; kalau
        yang hilang diperlakukan sebagai 0 m, mereka akan tampak sebagai
        pasangan paling dekat di seluruh situs.
        """
        wide = residual_after_site_median(_hari_berawan())
        pasangan = pairwise_correlation(wide)
        sebagian = {k: v for k, v in _koordinat_benar().items()
                    if k not in ("S0", "S1")}

        skor = decay_score(pasangan, sebagian)

        tersisa = N_STRING - 2
        assert skor["n_pasangan"] == tersisa * (tersisa - 1) // 2


class TestVerdictPlacement:

    def _skor(self, rho):
        return {"rho": rho, "n_pasangan": 66, "jarak_median_m": 100.0}

    def test_kontrol_lemah_menolak_memilih(self):
        """Tanpa daya pisah yang terbukti, memilih adalah lempar koin.

        Kontrol memakai string yang penempatannya TIDAK dibantah. Kalau di sana
        pun peluruhannya tidak muncul, hari itu tidak punya bayangan bergerak
        yang cukup -- dan skor kandidat apa pun tidak bermakna, sebesar apa pun
        selisihnya.
        """
        hasil = verdict_placement(
            self._skor(-0.05),
            {"DXF": self._skor(-0.02), "EL": self._skor(-0.60)},
        )

        assert hasil["putusan"] == "TIDAK_SENSITIF"
        assert hasil["pilihan"] is None

    def test_selisih_tipis_dilaporkan_seri(self):
        """Dua skor berdekatan bukan pemenang dan pecundang."""
        hasil = verdict_placement(
            self._skor(-0.70),
            {"DXF": self._skor(-0.44), "EL": self._skor(-0.48)},
        )

        assert hasil["putusan"] == "SERI"
        assert hasil["pilihan"] is None

    def test_pemenang_jelas_dipilih(self):
        """Kontrol kuat + selisih lebar -> penempatan bisa diputuskan."""
        hasil = verdict_placement(
            self._skor(-0.70),
            {"DXF": self._skor(-0.10), "EL": self._skor(-0.65)},
        )

        assert hasil["putusan"] == "TERPILIH"
        assert hasil["pilihan"] == "EL"
        assert hasil["margin"] > 0.10

    def test_ambang_kontrol_terpasang_sebagai_konstanta(self):
        """Ambangnya konvensi, bukan turunan fisika -- maka harus terlihat.

        Menguburnya sebagai angka telanjang di dalam fungsi membuat orang
        mengira ia diturunkan dari sesuatu.
        """
        assert DEFAULT_MIN_CONTROL_RHO < 0
        hasil = verdict_placement(
            self._skor(DEFAULT_MIN_CONTROL_RHO + 0.01),
            {"DXF": self._skor(-0.9), "EL": self._skor(-0.1)},
        )
        assert hasil["putusan"] == "TIDAK_SENSITIF"
