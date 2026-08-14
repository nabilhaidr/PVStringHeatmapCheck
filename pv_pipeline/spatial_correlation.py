"""Korelasi spasial dari bayangan awan -- menguji penempatan string.

Open Question 8 baru setengah terjawab. Penempatan DXF untuk empat inverter
tepi utara Phase One sudah terbukti SALAH; yang belum diketahui penempatan mana
yang BENAR. Memindahkannya ke posisi EL pernah diperiksa dan ditolak, karena
posisi itu sudah ditempati baris DXF milik inverter lain.

Modul ini menawarkan bukti dari arah yang sama sekali lain: fisika awan.

Pada hari berawan sebagian, bayangan awan bergerak melintasi larik. String yang
berdekatan secara fisik masuk dan keluar bayangan pada saat yang hampir sama;
yang berjauhan tidak. Jadi setelah sinyal iradians se-situs dibuang, korelasi
sisa antar dua string harus MELURUH terhadap jarak. Penempatan yang benar
menghasilkan peluruhan tajam, yang salah mengacaknya mendekati nol.

Alur pemakaian::

    wide = residual_after_site_median(long_df)
    pasangan = pairwise_correlation(wide)
    kontrol = decay_score(pasangan_kontrol, koordinat_tak_dibantah)
    skor = {"DXF": decay_score(pasangan, koor_dxf),
            "EL":  decay_score(pasangan, koor_el)}
    verdict_placement(kontrol, skor)

Kontrol itu wajib, bukan hiasan. Ia dihitung atas string yang penempatannya
TIDAK dibantah, dan menjawab pertanyaan yang mendahului segalanya: apakah data
hari ini punya daya pisah sama sekali? Tanpa itu, memilih pemenang dari dua
skor yang sama-sama lemah adalah lempar koin berbaju bukti.
"""
from __future__ import annotations

from itertools import combinations
from math import hypot
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

# Jendela tengah hari. Fajar dan senja dibuang: di sana median se-situs
# mendekati nol sehingga rasionya meledak, dan ramp-nya geometri matahari yang
# sama untuk semua string -- tidak membawa informasi ruang.
DEFAULT_MIDDAY: Tuple[int, int] = (9, 15)

# Cuplikan tumpang tindih minimum per pasangan. Di bawah ini korelasinya
# didominasi derau pencuplikan, bukan awan.
DEFAULT_MIN_OVERLAP: int = 30

# Ambang KONVENSI, bukan turunan fisika -- dinyatakan sebagai konstanta supaya
# tidak dikira begitu.
#
# Kontrol harus setidaknya mencapai peluruhan lemah-tapi-nyata. Kalau string
# yang geometrinya sudah diketahui pun tidak menunjukkan peluruhan, hari itu
# tidak punya bayangan bergerak yang cukup dan instrumennya buta.
DEFAULT_MIN_CONTROL_RHO: float = -0.20

# Dua kandidat yang selisih rho-nya di bawah ini tidak terbedakan mengingat
# derau dari jumlah pasangan yang terbatas. Laporkan seri, jangan memilih.
DEFAULT_MIN_MARGIN: float = 0.10


def residual_after_site_median(
    long_df: pd.DataFrame,
    *,
    midday: Tuple[int, int] = DEFAULT_MIDDAY,
) -> pd.DataFrame:
    """Long ``[ts, pv_string, power_kw]`` -> wide sisa setelah sinyal se-situs.

    Tiap string dibagi MEDIAN SELURUH STRING pada timestamp yang sama. Median
    itu memuat matahari, suhu, dan awan besar yang menaungi situs sekaligus --
    semua yang dialami bersama. Yang tersisa adalah simpangan lokal, dan hanya
    itu yang membawa informasi ruang.

    Tanpa langkah ini setiap pasangan string berkorelasi hampir sempurna karena
    sama-sama mengikuti matahari, dan peluruhan terhadap jarak akan tampak pada
    penempatan apa pun -- termasuk yang koordinatnya diacak.
    """
    df = long_df.copy()
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    df = df.loc[df["ts"].notna()]
    lo, hi = midday
    df = df[(df["ts"].dt.hour >= lo) & (df["ts"].dt.hour <= hi)]
    if df.empty:
        return pd.DataFrame()

    wide = df.pivot_table(
        index="ts", columns="pv_string", values="power_kw", aggfunc="mean",
    )
    acuan = wide.median(axis=1, skipna=True)
    acuan = acuan.where(acuan > 0)
    return wide.div(acuan, axis=0)


def pairwise_correlation(
    wide: pd.DataFrame,
    *,
    min_overlap: int = DEFAULT_MIN_OVERLAP,
) -> pd.DataFrame:
    """Korelasi Pearson tiap pasangan kolom -> ``[a, b, r, n]``.

    Pasangan dengan tumpang tindih di bawah ``min_overlap`` dibuang, bukan
    diberi r=0: sedikit cuplikan menghasilkan korelasi liar yang akan mencemari
    peringkat jarak.
    """
    if wide.empty:
        return pd.DataFrame(columns=["a", "b", "r", "n"])

    kolom = [c for c in wide.columns if wide[c].notna().sum() >= min_overlap]
    baris = []
    for a, b in combinations(sorted(kolom), 2):
        pasangan = wide[[a, b]].dropna()
        if len(pasangan) < min_overlap:
            continue
        sa, sb = pasangan[a], pasangan[b]
        if sa.std() == 0 or sb.std() == 0:
            continue
        baris.append({"a": a, "b": b, "r": float(sa.corr(sb)),
                      "n": int(len(pasangan))})
    return pd.DataFrame(baris, columns=["a", "b", "r", "n"])


def decay_score(
    pairs: pd.DataFrame,
    coords: Dict[str, Tuple[float, float]],
) -> dict:
    """Seberapa kuat korelasi meluruh terhadap jarak pada penempatan ini.

    Memakai korelasi peringkat Spearman antara jarak dan r, jadi yang diuji
    hanya URUTANnya -- tidak ada asumsi bentuk peluruhan yang harus ditebak.

    Pasangan yang salah satu stringnya tidak punya koordinat DILEWATI. Koordinat
    yang hilang bukan jarak nol; empat inverter tepi utara persis punya kolom
    yang dikosongkan, dan memperlakukannya sebagai 0 m akan membuat mereka
    tampak sebagai pasangan terdekat di seluruh situs.

    Returns
    -------
    dict
        ``rho`` (negatif = meluruh, makin negatif makin tajam), ``n_pasangan``,
        ``jarak_median_m``. ``rho`` NaN bila pasangannya terlalu sedikit.
    """
    jarak, korelasi = [], []
    for row in pairs.itertuples(index=False):
        pa, pb = coords.get(row.a), coords.get(row.b)
        if pa is None or pb is None:
            continue
        jarak.append(hypot(pa[0] - pb[0], pa[1] - pb[1]))
        korelasi.append(row.r)

    if len(jarak) < 3:
        return {"rho": float("nan"), "n_pasangan": len(jarak),
                "jarak_median_m": float("nan")}

    d = pd.Series(jarak)
    r = pd.Series(korelasi)
    return {
        "rho": float(d.corr(r, method="spearman")),
        "n_pasangan": len(jarak),
        "jarak_median_m": float(np.median(jarak)),
    }


def verdict_placement(
    control: dict,
    candidates: Dict[str, dict],
    *,
    min_control_rho: float = DEFAULT_MIN_CONTROL_RHO,
    min_margin: float = DEFAULT_MIN_MARGIN,
) -> dict:
    """Putuskan penempatan mana yang didukung data -- atau tolak memutuskan.

    Urutan pemeriksaannya disengaja: sensitivitas lebih dulu, baru pemenang.

    ``TIDAK_SENSITIF``
        Kontrol tidak mencapai ``min_control_rho``. String yang geometrinya
        sudah diketahui pun tidak menunjukkan peluruhan, jadi hari itu tidak
        punya bayangan bergerak yang cukup. Skor kandidat tidak bermakna,
        sebesar apa pun selisihnya -- dan justru selisih besar di atas kontrol
        yang lemah adalah tanda derau, bukan tanda temuan.
    ``SERI``
        Sensitif, tapi selisih dua kandidat teratas di bawah ``min_margin``.
    ``TERPILIH``
        Sensitif dan ada pemenang yang jelas.
    """
    dasar = {
        "kontrol_rho": control.get("rho", float("nan")),
        "kontrol_n": control.get("n_pasangan", 0),
        "skor": {k: v.get("rho", float("nan")) for k, v in candidates.items()},
        "pilihan": None,
        "margin": float("nan"),
    }

    rho_kontrol = dasar["kontrol_rho"]
    if not np.isfinite(rho_kontrol) or rho_kontrol > min_control_rho:
        return {**dasar, "putusan": "TIDAK_SENSITIF"}

    sah = {k: v["rho"] for k, v in candidates.items()
           if np.isfinite(v.get("rho", float("nan")))}
    if len(sah) < 2:
        return {**dasar, "putusan": "TIDAK_SENSITIF"}

    urut = sorted(sah.items(), key=lambda kv: kv[1])      # paling negatif dulu
    terbaik, kedua = urut[0], urut[1]
    margin = float(kedua[1] - terbaik[1])

    if margin < min_margin:
        return {**dasar, "putusan": "SERI", "margin": margin}
    return {**dasar, "putusan": "TERPILIH",
            "pilihan": terbaik[0], "margin": margin}


def coords_from_geometry(
    geom: pd.DataFrame,
    *,
    north_col: str = "north",
    east_col: str = "east",
) -> Dict[str, Tuple[float, float]]:
    """``string_geometry.csv`` -> ``{pv_string: (north, east)}``.

    Baris tanpa koordinat dibuang, bukan diisi nol -- lihat :func:`decay_score`.
    """
    keluar: Dict[str, Tuple[float, float]] = {}
    for row in geom.itertuples(index=False):
        n, e = getattr(row, north_col, None), getattr(row, east_col, None)
        pv: Optional[float] = getattr(row, "pv", None)
        if pv is None or pd.isna(pv) or pd.isna(n) or pd.isna(e):
            continue
        nama = f"{row.inverter_id}-PV{int(pv)}"
        keluar[nama] = (float(n), float(e))
    return keluar
