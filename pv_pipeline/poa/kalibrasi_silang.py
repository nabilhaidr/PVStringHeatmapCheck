"""Kalibrasi silang POA antar-stasiun cuaca -- bias amplitudo sensor dan jam penghalang.

Rancangan: ``docs/superpowers/specs/2026-10-01-kalibrasi-silang-poa-design.md``.

Tidak ada sensor POA "benar" di lokasi, jadi bias tiap WS diukur terhadap tiga
acuan: median stasiun lain (sampel yang stabil di semua stasiun yang
dibandingkan, supaya bayangan awan lokal tak terbaca sebagai bias), POA langit
cerah pvlib, dan larik itu sendiri lewat ``measured_ratio`` M2f. Faktor koreksi
hanya diusulkan bila >= 2 acuan sepakat.
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

POA_MAKS = 1400.0


def sampel_stabil(poa: pd.DataFrame, *, toleransi: float = 0.02, poa_min: float = 300.0,
                  jam: tuple = ("09:00", "15:00")) -> pd.DataFrame:
    """Benar bila POA mulus di sampel itu dan kedua tetangganya, > ``poa_min``, dalam jendela ``jam``.

    "Mulus" = |POA - rata-rata kedua tetangga| / POA < ``toleransi`` -- definisi
    yang sama dengan ``pv_pipeline.m2f.saturation``. POA < 0 atau > 1400 = galat.
    """
    p = poa.where((poa >= 0.0) & (poa <= POA_MAKS))
    dev = (p - (p.shift(1) + p.shift(-1)) / 2.0).abs() / p
    mulus = dev < toleransi
    stabil = mulus & mulus.shift(1, fill_value=False) & mulus.shift(-1, fill_value=False)
    dalam = np.zeros(len(p), dtype=bool)
    dalam[p.index.indexer_between_time(*jam)] = True
    jendela = pd.DataFrame(np.repeat(dalam[:, None], p.shape[1], axis=1), index=p.index, columns=p.columns)
    return stabil & (p > poa_min) & jendela


def _acuan_ws(kolom, ws, pembanding=None) -> list:
    """Stasiun acuan untuk ``ws``: ``pembanding`` tanpa ws itu sendiri, atau semua WS lain.

    Pembanding tetap dari WS yang sehat menahan acuan agar tidak bergeser ketika susunan
    WS berubah (WS-2 hilang Mar-Jun 2026 lalu kembali +12 %).
    """
    return [c for c in (pembanding or kolom) if c != ws and c in kolom]


def rasio_ke_median(poa: pd.DataFrame, stabil: pd.DataFrame, *, min_pembanding: int = 2,
                    pembanding=None) -> pd.DataFrame:
    """POA_WS / median POA stasiun LAIN yang stabil di sampel yang sama.

    NaN bila WS itu tak stabil atau pembandingnya < ``min_pembanding``. WS
    yang kosong tidak diisi rata-rata situs: isi rata-rata menarik rasio ke 1.
    ``pembanding`` membatasi stasiun acuan (lihat ``_acuan_ws``).
    """
    p = poa.where(stabil)
    hasil = {}
    for ws in p.columns:
        lain = p[_acuan_ws(p.columns, ws, pembanding)]
        hasil[ws] = (p[ws] / lain.median(axis=1, skipna=True)).where(lain.notna().sum(axis=1) >= min_pembanding)
    return pd.DataFrame(hasil, index=p.index)


def rasio_cerah(poa: pd.DataFrame, poa_cerah: pd.Series, *, kt_min: float = 0.75,
                jam: tuple = ("09:00", "15:00"), pembanding=None) -> pd.DataFrame:
    """POA_WS / median POA stasiun LAIN, pada sampel yang cerah menurut stasiun LAIN.

    Bahan ``profil_jam``/``penghalang``. Sampel stabil tidak dipakai: bayangan menjatuhkan
    bacaan WS itu sendiri sampai gagal syarat mulus/POA minimum, sehingga penghalang
    tersaring keluar dari pengukurannya sendiri (WS-1 pukul 11-12, Jun-Agu 2026).
    Bacaan <= 0 dibuang: bayangan menyisakan cahaya baur, jadi nol di langit cerah adalah
    sensor/logger mati (WS-1 Okt 2025-Mei 2026), bukan penghalang.
    """
    p = poa.where((poa > 0.0) & (poa <= POA_MAKS))
    c = poa_cerah.reindex(p.index)
    dalam = np.zeros(len(p), dtype=bool)
    dalam[p.index.indexer_between_time(*jam)] = True
    hasil = {}
    for ws in p.columns:
        lain = p[_acuan_ws(p.columns, ws, pembanding)]
        acuan = lain.median(axis=1, skipna=True)
        cerah = (acuan / c >= kt_min) & (lain.notna().sum(axis=1) >= 2) & dalam
        hasil[ws] = (p[ws] / acuan).where(cerah)
    return pd.DataFrame(hasil, index=p.index)


KOLOM_BULANAN = ["ws", "bulan", "n", "median", "iqr", "alasan"]


def gain_bulanan(rasio: pd.DataFrame, *, min_sampel: int = 200) -> pd.DataFrame:
    """Median rasio per WS per bulan; < ``min_sampel`` sampel -> NaN, alasan "data tipis"."""
    baris = []
    for ws in rasio.columns:
        s = rasio[ws].dropna()
        for bulan, g in s.groupby(s.index.to_period("M")):
            cukup = len(g) >= min_sampel
            baris.append({"ws": ws, "bulan": str(bulan), "n": int(len(g)),
                          "median": float(g.median()) if cukup else np.nan,
                          "iqr": float(g.quantile(0.75) - g.quantile(0.25)) if cukup else np.nan,
                          "alasan": "" if cukup else "data tipis"})
    return pd.DataFrame(baris, columns=KOLOM_BULANAN)


def profil_jam(rasio: pd.DataFrame) -> pd.DataFrame:
    """Median rasio per jam / median rasio seluruh sampel WS itu di bulan itu."""
    baris = []
    for ws in rasio.columns:
        s = rasio[ws].dropna()
        for bulan, g in s.groupby(s.index.to_period("M")):
            acuan = g.median()
            for jam, h in g.groupby(g.index.hour):
                baris.append({"ws": ws, "bulan": str(bulan), "jam": int(jam), "n": int(len(h)),
                              "profil": float(h.median() / acuan)})
    return pd.DataFrame(baris, columns=["ws", "bulan", "jam", "n", "profil"])


def penghalang(profil: pd.DataFrame, *, ambang: float = 0.10, min_bulan: int = 3) -> pd.DataFrame:
    """(WS, jam) yang profilnya < 1 - ``ambang`` pada >= ``min_bulan`` bulan."""
    turun = profil[profil["profil"] < 1.0 - ambang]
    hit = turun.groupby(["ws", "jam"])["bulan"].nunique().reset_index(name="n_bulan")
    return hit[hit["n_bulan"] >= min_bulan].reset_index(drop=True)


def gain_relatif(rasio: pd.DataFrame, jam_penghalang: pd.DataFrame, *, min_sampel: int = 200,
                 ambang_geser: float = 0.05) -> pd.DataFrame:
    """Median rasio selama rentang tanpa jam penghalang; ``bergeser`` bila median bulanan berayun > ambang.

    Ayunan butuh >= 2 bulan sah; dengan kurang dari itu ``ayunan_bulanan`` NaN
    (bukan 0) dan ``sepakati`` menolak mengusulkan.
    """
    baris = []
    for ws in rasio.columns:
        s = rasio[ws].dropna()
        buang = set(jam_penghalang.loc[jam_penghalang["ws"] == ws, "jam"]) if len(jam_penghalang) else set()
        s = s[~s.index.hour.isin(sorted(buang))]
        bulanan = gain_bulanan(s.to_frame(ws), min_sampel=min_sampel)["median"].dropna()
        ayunan = float(bulanan.max() - bulanan.min()) if len(bulanan) >= 2 else np.nan
        baris.append({"ws": ws, "gain": float(s.median()) if len(s) >= min_sampel else np.nan,
                      "n": int(len(s)), "n_bulan_sah": int(len(bulanan)), "ayunan_bulanan": ayunan,
                      "bergeser": bool(np.isfinite(ayunan) and ayunan > ambang_geser)})
    return pd.DataFrame(baris, columns=["ws", "gain", "n", "n_bulan_sah", "ayunan_bulanan", "bergeser"])


def gain_absolut(poa: pd.DataFrame, poa_cerah: pd.Series, stabil: pd.DataFrame, *,
                 kt_min: float = 0.75) -> pd.DataFrame:
    """Median POA_WS / POA langit cerah pada sampel stabil di saat langit sangat cerah.

    "Sangat cerah" dinilai dari MEDIAN Kt seluruh WS di sampel itu (>= ``kt_min``),
    bukan dari rasio WS itu sendiri: sensor yang membaca jauh terlalu rendah
    tidak boleh tersaring keluar dari pengukuran biasnya.
    """
    c = poa_cerah.reindex(poa.index).to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        kt_situs = poa.median(axis=1).to_numpy(dtype=float) / c
        cerah = kt_situs >= kt_min
        baris = []
        for ws in poa.columns:
            r = poa[ws].to_numpy(dtype=float) / c
            m = cerah & stabil[ws].to_numpy(dtype=bool) & np.isfinite(r)
            baris.append({"ws": ws, "gain": float(np.median(r[m])) if m.any() else np.nan, "n": int(m.sum())})
    return pd.DataFrame(baris, columns=["ws", "gain", "n"])


def gain_larik(kalibrasi_harian: pd.DataFrame, wb_to_ws: dict) -> pd.DataFrame:
    """Per WS: median atas WB-hari dari (median rasio situs hari itu / rasio WB).

    Bila larik setara, rasio aktual/harapan WB berbanding terbalik dengan gain
    sensor POA-nya: nilai ini sebanding dengan gain sensor, relatif terhadap armada.
    """
    k = kalibrasi_harian.dropna(subset=["measured_ratio"]).copy()
    k["ws"] = k["wb_id"].astype(str).str.upper().map({str(w).upper(): s for w, s in wb_to_ws.items()})
    k = k.dropna(subset=["ws"])
    k["g"] = k.groupby("date")["measured_ratio"].transform("median") / k["measured_ratio"]
    out = k.groupby("ws")["g"].agg(gain="median", n="size").reset_index()
    return out[["ws", "gain", "n"]]


def periode_ws(poa: pd.DataFrame, *, min_celah_hari: int = 30, batas: dict | None = None) -> pd.DataFrame:
    """Potong rentang tiap WS di celah >= ``min_celah_hari`` hari tanpa data.

    Sensor yang dilepas lalu dipasang ulang (WS-2, Mar-Jun 2026) bisa kembali dengan
    orientasi atau kalibrasi lain; gain atas rentang gabungan mencampur keduanya.
    Hari kosong di awal/akhir rentang bukan periode. ``batas`` = {ws: [tanggal]}: hari
    pertama periode baru untuk perubahan tanpa celah data (WS-3 ~10 Agu 2026, +6 %).
    """
    hari = poa.notna().groupby(poa.index.normalize()).any()
    baris = []
    for ws in poa.columns:
        tgl = hari.index[hari[ws].to_numpy()]
        if not len(tgl):
            continue
        # N hari kosong di antara dua hari berdata = selisih tanggal N + 1 hari.
        putus = set(np.flatnonzero(np.diff(tgl.to_numpy()) > np.timedelta64(min_celah_hari, "D")).tolist())
        for d in (batas or {}).get(ws, []):
            # Batas = hari pertama periode baru; di luar data atau di dalam celah tidak menambah potongan.
            k = int(tgl.searchsorted(pd.Timestamp(d).normalize()))
            if 0 < k < len(tgl):
                putus.add(k - 1)
        putus = np.array(sorted(putus), dtype=int)
        awal, akhir = np.r_[0, putus + 1], np.r_[putus, len(tgl) - 1]
        for i, (a, b) in enumerate(zip(awal, akhir), start=1):
            baris.append({"ws": ws, "periode": i, "mulai": tgl[a], "akhir": tgl[b]})
    return pd.DataFrame(baris, columns=["ws", "periode", "mulai", "akhir"])


def mutu_data(poa: pd.DataFrame, *, cerah_min: float = 500.0, jam: tuple = ("10:00", "14:00")) -> pd.DataFrame:
    """Per WS: hari tanpa data, sampel 0 saat median WS lain > ``cerah_min`` (jam ``jam``), sampel galat.

    Nol di siang cerah = logger/sensor mati (WS-1 Okt 2025-Mei 2026); ditampilkan di setiap
    run supaya tidak tersembunyi di balik penyaringan.
    """
    hari = poa.notna().groupby(poa.index.normalize()).any()
    siang = poa.iloc[poa.index.indexer_between_time(*jam)]
    baris = []
    for ws in poa.columns:
        lain = siang.drop(columns=ws).median(axis=1)
        baris.append({"ws": ws, "hari_kosong": int((~hari[ws]).sum()),
                      "nol_saat_cerah": int(((siang[ws] == 0.0) & (lain > cerah_min)).sum()),
                      "galat": int(((poa[ws] < 0.0) | (poa[ws] > POA_MAKS)).sum())})
    return pd.DataFrame(baris, columns=["ws", "hari_kosong", "nol_saat_cerah", "galat"])


def rasio_harian(poa: pd.DataFrame, *, pembanding=None, jam: tuple = ("09:00", "15:00"),
                 min_sampel: int = 40, jam_penghalang: pd.DataFrame | None = None) -> pd.DataFrame:
    """Energi harian WS / energi harian acuan (median stasiun acuan) pada sampel yang sama.

    Bahan ``titik_ubah``. Bacaan <= 0 atau > 1400 dibuang; hari dengan < ``min_sampel``
    sampel bersama = NaN. Jam penghalang WS itu dibuang (seperti ``gain_relatif``): bayangan
    yang berubah mengikuti matahari bukan lompatan gain (WS-1 Jun-Agu 2026).
    """
    p = poa.where((poa > 0.0) & (poa <= POA_MAKS))
    p = p.iloc[p.index.indexer_between_time(*jam)]
    hasil = {}
    for ws in p.columns:
        lain = p[_acuan_ws(p.columns, ws, pembanding)]
        acuan = lain.median(axis=1).where(lain.notna().sum(axis=1) >= 2)
        buang = (set(jam_penghalang.loc[jam_penghalang["ws"] == ws, "jam"])
                 if jam_penghalang is not None and len(jam_penghalang) else set())
        x = p[ws].where(~p.index.hour.isin(sorted(buang)))
        ada = x.notna() & acuan.notna()
        hasil[ws] = (x.where(ada).resample("D").sum(min_count=min_sampel)
                     / acuan.where(ada).resample("D").sum(min_count=min_sampel))
    return pd.DataFrame(hasil)


def titik_ubah(rasio_hari: pd.DataFrame, periode: pd.DataFrame, *, jendela_hari: int = 14,
               ambang: float = 0.05, min_hari: int = 10) -> pd.DataFrame:
    """Kandidat lompatan tanpa celah data, per (WS, periode): saran ``--batas``, bukan batas otomatis.

    Untuk tiap hari d: median rasio ``jendela_hari`` sebelum d vs sesudahnya (>= ``min_hari``
    hari per sisi). Hari dengan |lompatan| >= ``ambang`` dikelompokkan; per kelompok diambil
    tengah dataran |lompatan| maksimum. Jendela tak melintasi batas periode, jadi celah yang
    sudah memotong periode tidak muncul lagi (WS-3 ~10 Agu 2026: +5,7 % tanpa celah).
    """
    w = pd.Timedelta(days=jendela_hari)
    baris = []
    for p in periode.itertuples(index=False):
        r = rasio_hari.loc[p.mulai:p.akhir, p.ws].dropna()
        nilai = {}
        for d in r.index:
            a, b = r[(r.index >= d - w) & (r.index < d)], r[(r.index >= d) & (r.index < d + w)]
            if len(a) >= min_hari and len(b) >= min_hari:
                nilai[d] = (a.median(), b.median())
        lompat = pd.Series({d: b / a - 1.0 for d, (a, b) in nilai.items()}, dtype=float)
        calon = lompat[lompat.abs() >= ambang]
        if calon.empty:
            continue
        # Kelompok baru bila jarak > jendela ATAU arah berbalik: turun lalu naik = dua peristiwa.
        pisah = ((calon.index.to_series().diff() > w).to_numpy()
                 | (np.sign(calon).diff().fillna(0.0).to_numpy() != 0.0))
        for _, g in calon.groupby(pisah.cumsum()):
            puncak = g.index[np.isclose(g.abs(), g.abs().max(), rtol=0.0, atol=1e-9)]
            d = puncak[len(puncak) // 2]
            baris.append({"ws": p.ws, "periode": p.periode, "tanggal": d, "sebelum": nilai[d][0],
                          "sesudah": nilai[d][1], "lompatan": float(lompat[d])})
    return pd.DataFrame(baris, columns=["ws", "periode", "tanggal", "sebelum", "sesudah", "lompatan"])


def _saling_sepakat(ada: dict, tol: float) -> set:
    """Himpunan acuan terbesar yang SEMUA pasangannya dalam ``tol``; seri -> sebaran terkecil.

    Bukan gabungan pasangan: rel-abs dan abs-larik yang masing-masing lolos tidak
    membuat rel-larik sepakat.
    """
    for n in range(len(ada), 1, -1):
        cocok = [c for c in itertools.combinations(ada, n)
                 if max(ada[k] for k in c) - min(ada[k] for k in c) <= tol]
        if cocok:
            return set(min(cocok, key=lambda c: max(ada[k] for k in c) - min(ada[k] for k in c)))
    return set()


def sepakati(rel: pd.DataFrame, absolut: pd.DataFrame, larik, *, tol: float = 0.03,
             min_bulan: int = 2, pembanding=None) -> pd.DataFrame:
    """Usulan faktor (1/gain) bila >= 2 acuan saling sepakat dalam ``tol`` dan gain terbukti tak bergeser.

    "Terbukti tak bergeser" butuh >= ``min_bulan`` bulan sah. ``gain_abs``
    dinormalkan ke median semua WS: langit cerah pvlib punya bias bersama
    (kekeruhan, albedo) yang bukan milik satu sensor.
    """
    a = absolut.set_index("ws")["gain"]
    # Normalkan ke WS pembanding bila ada: satu sensor bias tidak boleh menggeser acuan.
    a = a / (a[a.index.isin(pembanding)].median() if pembanding else a.median())
    lr = larik.set_index("ws")["gain"] if larik is not None and len(larik) else pd.Series(dtype=float)
    baris = []
    for r in rel.itertuples(index=False):
        nilai = {"rel": r.gain, "abs": a.get(r.ws, np.nan), "larik": lr.get(r.ws, np.nan)}
        ada = {k: float(v) for k, v in nilai.items() if np.isfinite(v)}
        sepakat = _saling_sepakat(ada, tol)
        dasar = {"ws": r.ws, "gain_rel": nilai["rel"], "gain_abs": nilai["abs"], "gain_larik": nilai["larik"],
                 "n_bulan_sah": int(r.n_bulan_sah), "bergeser": bool(r.bergeser)}
        if r.n_bulan_sah < min_bulan:
            alasan = f"data bulanan kurang: {int(r.n_bulan_sah)} bulan sah < {min_bulan}"
        elif len(sepakat) < 2:
            alasan = f"acuan berselisih > {tol}: " + ", ".join(f"{k} {v:.3f}" for k, v in ada.items())
        elif r.bergeser:
            alasan = "gain bergeser antar-bulan"
        else:
            g = float(np.median([ada[k] for k in sepakat]))
            baris.append({**dasar, "status": "usulan_koreksi", "usulan": 1.0 / g,
                          "alasan": "sepakat: " + ", ".join(sorted(sepakat))})
            continue
        baris.append({**dasar, "status": "perlu_lapangan", "usulan": np.nan, "alasan": alasan})
    return pd.DataFrame(baris, columns=["ws", "gain_rel", "gain_abs", "gain_larik", "n_bulan_sah", "bergeser",
                                        "status", "usulan", "alasan"])


KOLOM_PERIODE = ["ws", "periode", "mulai", "akhir", "gain_rel", "gain_abs", "gain_larik", "n_bulan_sah",
                 "bergeser", "status", "usulan", "alasan"]


def kalibrasi_per_periode(poa: pd.DataFrame, stabil: pd.DataFrame, rasio: pd.DataFrame,
                          jam_penghalang: pd.DataFrame, poa_cerah: pd.Series, periode: pd.DataFrame,
                          kalibrasi_harian=None, wb_to_ws=None, *, min_sampel: int = 200,
                          tol: float = 0.03, pembanding=None) -> pd.DataFrame:
    """``sepakati`` per (WS, periode); ketiga acuan dihitung pada jendela periode itu.

    ``gain_absolut`` dihitung untuk SEMUA WS di jendela yang sama, supaya normalisasi
    median di ``sepakati`` tidak mencampur waktu.
    """
    baris = []
    for p in periode.itertuples(index=False):
        j = slice(p.mulai, p.akhir + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
        rel = gain_relatif(rasio.loc[j, [p.ws]], jam_penghalang, min_sampel=min_sampel)
        absolut = gain_absolut(poa.loc[j], poa_cerah, stabil.loc[j])
        larik = None
        if kalibrasi_harian is not None:
            tgl = pd.to_datetime(kalibrasi_harian["date"])
            k = kalibrasi_harian[(tgl >= p.mulai) & (tgl <= p.akhir)]
            larik = gain_larik(k, wb_to_ws) if len(k) else None
        s = sepakati(rel, absolut, larik, tol=tol, pembanding=pembanding).iloc[0]
        baris.append({"ws": p.ws, "periode": p.periode, "mulai": p.mulai, "akhir": p.akhir,
                      **{k: s[k] for k in KOLOM_PERIODE[4:]}})
    return pd.DataFrame(baris, columns=KOLOM_PERIODE)
