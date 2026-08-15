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

import re
from itertools import combinations
from math import cos, hypot, radians
from pathlib import Path
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

# Header berkas survei EL dicari lewat PENANDA ini, bukan nomor baris.
# Menghitung barisnya dari luar sudah dua kali meleset: pengantarnya memuat
# baris kosong, ``pd.read_csv`` melewatinya secara default, dan indeks apa pun
# yang dihitung manual bergeser sebanyak jumlah baris kosong di atasnya.
# Gejalanya bukan galat melainkan nama kolom yang berisi angka.
EL_HEADER_MARKER: str = "#String"

# Konversi derajat -> meter di sekitar lintang situs (~ -1 derajat). Wajib,
# karena koordinat ini diadu dengan koordinat DXF yang satuannya meter:
# decay_score menghitung jarak Euclid apa adanya, dan dua kandidat dengan skala
# berbeda 10^5 kali sama-sama tetap menghasilkan rho tanpa ada yang menandai
# bahwa perbandingannya omong kosong.
_M_PER_DEG_LAT: float = 110574.0
_M_PER_DEG_LON_EKUATOR: float = 111320.0

# Dua ragam label dalam SATU berkas, seperti konvensi huruf kolom telemetri.
_EL_PHASE_ONE_RE = re.compile(r"^S(?P<plant>[12])(?P<inv>\d{2})_(?P<st>\d+)$")
_EL_PANJANG_RE = re.compile(
    r"^WB(?P<wb>\d{2})INV(?P<inv>\d+)ST(?P<st>\d+)$", re.I,
)


def _decode_el_label(label: str) -> Optional[str]:
    """Label survei EL -> ``"WBnn-INVmm-STss"``; None bila tak dikenali.

    Berkas memakai dua konvensi sekaligus. Phase One memakai bentuk pendek
    ``S{plant}{inverter}_{st}`` -- pengkodean yang sama dengan ``Inv_A_2nn_IKN``
    di telemetri -- sementara WB03-10 memakai bentuk panjang ``WB10INV17ST23``.
    Mengenali satu ragam saja membuang separuh survei tanpa galat apa pun.
    """
    teks = str(label).strip()
    m = _EL_PHASE_ONE_RE.match(teks)
    if m:
        return (f"WB0{m.group('plant')}-INV{int(m.group('inv')):02d}"
                f"-ST{int(m.group('st'))}")
    m = _EL_PANJANG_RE.match(teks)
    if m:
        return (f"WB{int(m.group('wb')):02d}-INV{int(m.group('inv')):02d}"
                f"-ST{int(m.group('st'))}")
    return None


def load_el_coords(
    csv_path: Path | str,
    *,
    header_marker: str = EL_HEADER_MARKER,
) -> Dict[str, Tuple[float, float]]:
    """Survei EL drone -> ``{"WBnn-INVmm-STss": (north_m, east_m)}``.

    Berkasnya per MODUL (114.420 baris untuk 4.470 string), berkoordinat
    Longitude/Latitude derajat, dan didahului blok pengantar yang panjang.
    Fungsi ini meringkas modul jadi satu titik per string dan mengubah derajat
    jadi meter relatif terhadap pusat data.

    Kuncinya memakai ST, bukan PV. Untuk Phase One keduanya kebetulan sama,
    tapi untuk WB03-10 TIDAK -- lihat :func:`el_coords_to_pv`.

    Raises
    ------
    ValueError
        Bila penanda header tidak ditemukan. Lebih baik berhenti daripada
        mengembalikan tabel yang nama kolomnya sebenarnya baris data.
    """
    path = Path(csv_path)
    lewati = None
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fp:
        for i, baris in enumerate(fp):
            if baris.lstrip().startswith(header_marker):
                lewati = i
                break
    if lewati is None:
        raise ValueError(
            f"{path.name}: penanda header {header_marker!r} tidak ditemukan; "
            f"berkas ini bukan ekspor survei EL yang dikenali."
        )

    df = pd.read_csv(path, skiprows=lewati, low_memory=False)
    df.columns = [str(c).strip().lstrip("#") for c in df.columns]
    for kolom in ("String", "Longitude", "Latitude"):
        if kolom not in df.columns:
            raise ValueError(
                f"{path.name}: kolom {kolom!r} tidak ada. "
                f"Tersedia: {list(df.columns)[:12]}"
            )

    df = df[["String", "Longitude", "Latitude"]].copy()
    df["kunci"] = df["String"].map(_decode_el_label)
    df["Longitude"] = pd.to_numeric(df["Longitude"], errors="coerce")
    df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
    df = df.dropna(subset=["kunci", "Longitude", "Latitude"])
    if df.empty:
        return {}

    per_string = df.groupby("kunci")[["Longitude", "Latitude"]].mean()
    lat0 = float(per_string["Latitude"].mean())
    lon0 = float(per_string["Longitude"].mean())
    m_per_lon = _M_PER_DEG_LON_EKUATOR * cos(radians(lat0))

    return {
        kunci: ((row.Latitude - lat0) * _M_PER_DEG_LAT,
                (row.Longitude - lon0) * m_per_lon)
        for kunci, row in per_string.iterrows()
    }


def el_coords_to_pv(
    el_by_st: Dict[str, Tuple[float, float]],
    geom: pd.DataFrame,
) -> Dict[str, Tuple[float, float]]:
    """Ganti kunci ST jadi kunci PV lewat pemetaan geometri.

    ``pv = st`` BENAR untuk Phase One dan SALAH untuk WB03-10. Menyamakannya
    akan menempelkan koordinat EL ke kanal yang keliru di seluruh WB03-10 --
    diam-diam, karena nama yang terbentuk tetap terlihat sah.

    Pemetaan ST->PV datang dari as-built DC cable list lewat
    ``string_geometry.csv`` dan tidak ada hubungannya dengan POSISI, jadi
    memakainya di sini tidak melingkar terhadap sengketa penempatan.

    ST yang tidak punya PV DIBUANG, tidak ditebak -- aturan yang sama dengan
    cross-slope yang di-NULL-kan: pemetaan yang belum terbukti lebih buruk
    daripada tidak ada, karena tidak ada yang di hilir bisa tahu ia karangan.
    """
    peta: Dict[str, str] = {}
    for row in geom.itertuples(index=False):
        st, pv = getattr(row, "st", None), getattr(row, "pv", None)
        if st is None or pv is None or pd.isna(st) or pd.isna(pv):
            continue
        peta[f"{row.inverter_id}-ST{int(st)}"] = (
            f"{row.inverter_id}-PV{int(pv)}"
        )

    return {peta[k]: v for k, v in el_by_st.items() if k in peta}


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
