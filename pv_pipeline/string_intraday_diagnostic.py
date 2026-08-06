"""Diagnostik intraday PER STRING: pisahkan soiling dari shading.

``pv_pipeline.m2a.shading`` bekerja di level inverter agregat (lihat catatan
di modul itu: "M2a operates at inverter aggregate level"), sehingga buta
terhadap satu-dua string yang ternaungi di antara 24-28 string sehat. Modul
ini menutup celah tersebut.

Metode: daya tiap string dibagi median tetangga se-inverter PADA TIMESTAMP
YANG SAMA, lalu diagregasi menjadi profil per jam. Irradiance dan suhu
tercoret karena semua string satu inverter mengalaminya bersamaan.

Pemisah soiling vs shading::

    soiling  -> rugi proporsional; rasio DATAR sepanjang hari
    shading  -> rugi terkonsentrasi di jam tertentu; rasio bisa >1,0 di jam
                lain -- string kotor/rusak TIDAK MUNGKIN melampaui tetangga
                yang bersih, jadi rasio >1,0 membuktikan panelnya sehat dan
                yang bermasalah adalah halangan pada jam tertentu

Dipakai oleh ``output_string/String_Intraday_Diagnostic.ipynb``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from pv_pipeline.m2a.shading import (
    PV_I_COL_TEMPLATE,
    PV_POWER_COL_TEMPLATE,
    PV_V_COL_TEMPLATE,
    _normalize_pv_columns,
)

DEFAULT_PV_MAX: int = 28
DEFAULT_MIN_PEAK_KW: float = 0.5      # slot dgn puncak < ini dianggap kosong
DEFAULT_MIN_MEDIAN_KW: float = 0.3    # timestamp gelap dibuang
DEFAULT_HOUR_RANGE = (7, 17)
DEFAULT_DEAD_RATIO: float = 0.10
DEFAULT_RECOVERY_RATIO: float = 1.02  # rasio >= ini di suatu jam -> panel sehat
DEFAULT_FLAT_RANGE: float = 0.10      # rentang <= ini -> profil datar
DEFAULT_DROPOUT_SHARE: float = 0.40   # >= ini bagian hari mati dini -> shading sore
DEFAULT_AMPM_GAP: float = 0.12        # selisih pagi vs sore -> shading berarah

LONG_COLUMNS: List[str] = ["ts", "inverter_id", "pv", "power_kw"]
GEOMETRY_COLUMNS: List[str] = [
    "cross_slope_deg", "expected_ampm_asym", "ampm_residual",
]
CLASSIFICATION_COLUMNS: List[str] = [
    "pv_string", "inverter_id", "pv", "n_days", "ratio_median",
    "deficit_pct", "ratio_range", "ratio_max_hourly", "jam_terburuk",
    "jam_terbaik", "pagi", "sore", "dropout_share_pct",
    "vdrop_pct", "vdrop_minus_inv_median", *GEOMETRY_COLUMNS, "kategori",
]
RAIN_COLUMNS: List[str] = [
    "pv_string", "event", "ratio_before", "ratio_after", "delta_pp",
]

# Asimetri pagi-sore akibat kemiringan tanah bervariasi ~22,5% dari nilainya
# sendiri antar solstis, dan angka itu KONSTAN terhadap besar cross-slope
# (2,5 / 5,0 / 9,2 / 13,2 / 18,3 derajat -> 22,6 / 22,7 / 22,6 / 22,5 / 22,4%).
# Tandanya juga tidak pernah berbalik. Ambang 0,30 memberi margin derau di atas
# 22,5% tanpa melewatkan obstruksi ringan. Diturunkan dari geometri surya
# (pvlib clear-sky pada lintang site), bukan disetel ke data.
SEASONAL_REL_RANGE_MAX: float = 0.30

# Asimetri itu berbentuk ``k(hari) * sin(cross_slope)``. Bentuk sinus bukan
# pilihan gaya: ia mereproduksi kedua puluh nilai pvlib clear-sky yang sudah
# diturunkan (cross-slope 2,5-18,3 derajat x empat tanggal) dengan galat
# maksimum 0,0005, dan ia yang menjelaskan kenapa drift musiman 22,5% KONSTAN
# terhadap besar cross-slope -- sin memfaktorkan besarannya keluar. Bentuk itu
# juga yang benar untuk mengekstrapolasi ke rentang nyata site (-29,8 sampai
# +31,5 derajat), di luar jangkar tabel; pendekatan linear akan melebihkan.
# ``k`` di 21 Mar / 21 Jun / 21 Sep / 22 Des, interpolasi siklis antar tanggal.
AMPM_ASYM_DOY: tuple = (80, 172, 264, 356)
AMPM_ASYM_K: tuple = (2.9775, 2.6104, 2.9039, 3.2693)

SEASONAL_COLUMNS: List[str] = [
    "pv_string", "n_musim", "asym_mean", "asym_rel_range", "verdikt",
]

# Kolom yang harus diisi surveyor. Dua yang pertama adalah satu-satunya cara
# menutup pertanyaan yang tidak terjawab dari gambar mana pun: apakah meja
# diratakan (tanah digrading) atau mengikuti kemiringan menyamping. Satu foto
# yang membidik memanjang sepanjang baris meja sudah cukup menjawabnya.
FIELD_OBSERVATION_COLUMNS: List[str] = [
    "meja_rata_atau_ikut_kontur",   # rata (digrading) | ikut kontur | tidak jelas
    "foto_memanjang_baris",         # bidik sepanjang baris, bukan tegak lurus
    "arah_kemiringan_tanah",        # timur | barat | utara | selatan | datar
    "objek_penghalang",             # vegetasi | tiang | bangunan | tidak ada
    "jam_pengamatan",
]


def _pv_indices(columns: Iterable[str], pv_max: int) -> List[int]:
    """Indeks PV yang punya kolom daya ATAU pasangan V/I di ``columns``."""
    cols = set(columns)
    found = []
    for n in range(1, pv_max + 1):
        if PV_POWER_COL_TEMPLATE.format(pv=n) in cols:
            found.append(n)
        elif (PV_V_COL_TEMPLATE.format(pv=n) in cols
              and PV_I_COL_TEMPLATE.format(pv=n) in cols):
            found.append(n)
    return found


def load_baseline_power_long(
    csv_paths: Sequence[Path | str],
    *,
    inverter_ids: Optional[Iterable[str]] = None,
    empty_pv_map: Optional[Dict[str, List[int]]] = None,
    pv_max: int = DEFAULT_PV_MAX,
    hour_range: tuple = DEFAULT_HOUR_RANGE,
) -> pd.DataFrame:
    """Baca CSV baseline harian -> long df ``[ts, inverter_id, pv, power_kw]``.

    Hanya kolom yang diperlukan yang dibaca supaya muat di RAM Colab: satu
    hari penuh ~30 MB, sebulan ~700 MB kalau dibaca utuh.
    """
    want = {"Inverter_ID", "ManageObject", "Start Time"}
    keep_inv = {str(i).upper() for i in inverter_ids} if inverter_ids else None
    empties = {k.upper(): set(v) for k, v in (empty_pv_map or {}).items()}
    lo, hi = hour_range
    frames: List[pd.DataFrame] = []

    for path in csv_paths:
        head = _normalize_pv_columns(pd.read_csv(path, nrows=0))
        idx = _pv_indices(head.columns, pv_max)
        if not idx:
            continue
        usecols = {c for c in head.columns if c in want}
        for n in idx:
            for tpl in (PV_POWER_COL_TEMPLATE, PV_V_COL_TEMPLATE, PV_I_COL_TEMPLATE):
                col = tpl.format(pv=n)
                if col in head.columns:
                    usecols.add(col)
        # CSV asli memakai Title Case utk PV15+; baca dgn nama asli lalu normalisasi.
        orig = set()
        for col in pd.read_csv(path, nrows=0).columns:
            norm = col.replace("Input Voltage", "input voltage")
            norm = norm.replace("Input Current", "input current")
            if norm in usecols:
                orig.add(col)
        raw = _normalize_pv_columns(
            pd.read_csv(path, usecols=lambda c: c in orig, low_memory=False)
        )
        if "Inverter_ID" not in raw.columns:
            from pv_pipeline.transformations import transform_manage_object_to_id
            raw["Inverter_ID"] = raw["ManageObject"].map(transform_manage_object_to_id)
        raw["Inverter_ID"] = raw["Inverter_ID"].astype(str).str.upper()
        if keep_inv is not None:
            raw = raw[raw["Inverter_ID"].isin(keep_inv)]
        if raw.empty:
            continue
        ts = pd.to_datetime(raw["Start Time"], errors="coerce")
        raw = raw.loc[ts.notna()].copy()
        raw["ts"] = ts.loc[ts.notna()].to_numpy()
        raw = raw[(raw["ts"].dt.hour >= lo) & (raw["ts"].dt.hour <= hi)]
        if raw.empty:
            continue

        for n in idx:
            p_col = PV_POWER_COL_TEMPLATE.format(pv=n)
            if p_col in raw.columns:
                power = pd.to_numeric(raw[p_col], errors="coerce")
            else:
                v = pd.to_numeric(raw[PV_V_COL_TEMPLATE.format(pv=n)], errors="coerce")
                i = pd.to_numeric(raw[PV_I_COL_TEMPLATE.format(pv=n)], errors="coerce")
                power = v * i / 1000.0
            part = pd.DataFrame({
                "ts": raw["ts"].to_numpy(),
                "inverter_id": raw["Inverter_ID"].to_numpy(),
                "pv": f"PV{n}",
                "power_kw": power.to_numpy(),
            })
            part = part[part["power_kw"].notna()]
            if empties:
                keep = ~part["inverter_id"].map(lambda inv: n in empties.get(inv, ()))
                part = part[keep]
            if not part.empty:
                frames.append(part)

    if not frames:
        return pd.DataFrame(columns=LONG_COLUMNS)
    return pd.concat(frames, ignore_index=True)[LONG_COLUMNS]


def add_sibling_ratio(
    long_df: pd.DataFrame,
    *,
    min_peak_kw: float = DEFAULT_MIN_PEAK_KW,
    min_median_kw: float = DEFAULT_MIN_MEDIAN_KW,
) -> pd.DataFrame:
    """Tambah kolom ``ratio`` = daya string / median tetangga se-inverter."""
    if long_df.empty:
        return long_df.assign(ratio=pd.Series(dtype=float))
    df = long_df.copy()
    peak = df.groupby(["inverter_id", "pv"])["power_kw"].transform("max")
    df = df[peak > min_peak_kw]
    if df.empty:
        return df.assign(ratio=pd.Series(dtype=float))
    med = df.groupby(["inverter_id", "ts"])["power_kw"].transform("median")
    df = df[med > min_median_kw]
    med = med.loc[df.index]
    return df.assign(ratio=df["power_kw"] / med)


def build_hourly_profile(ratio_df: pd.DataFrame) -> pd.DataFrame:
    """Matriks profil: baris = string, kolom = jam, nilai = median rasio."""
    if ratio_df.empty:
        return pd.DataFrame()
    work = ratio_df.assign(hour=ratio_df["ts"].dt.hour)
    prof = work.groupby(["inverter_id", "pv", "hour"])["ratio"].median().unstack("hour")
    prof.index = [f"{inv}-{pv}" for inv, pv in prof.index]
    prof.index.name = "pv_string"
    return prof.sort_index(axis=1)


def _dropout_share(ratio_df: pd.DataFrame, late_hour: int) -> pd.Series:
    """Bagian hari (%) saat string ~mati di ``late_hour`` padahal tetangga tidak."""
    late = ratio_df[ratio_df["ts"].dt.hour == late_hour]
    if late.empty:
        return pd.Series(dtype=float)
    share = late.assign(dead=late["ratio"] < 0.05).groupby(
        ["inverter_id", "pv"]
    )["dead"].mean() * 100.0
    share.index = [f"{inv}-{pv}" for inv, pv in share.index]
    return share


def _attach_cable_metrics(
    out: pd.DataFrame, cable_metrics: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """Isi kolom bukti rugi resistif kabel DC pada tabel klasifikasi.

    ``vdrop_minus_inv_median`` memakai pembanding yang sama dengan
    ``ratio`` (median se-inverter) supaya bisa dibaca berdampingan: kalau
    sebuah string UNIFORM juga jauh di atas median vdrop inverternya,
    sebagian defisitnya permanen dan tidak akan hilang setelah dicuci.
    NA berarti string itu tidak punya baris di cable list -- bukan 0.
    """
    if (out.empty or cable_metrics is None or cable_metrics.empty
            or not {"inverter_id", "pv", "vdrop_pct"} <= set(cable_metrics.columns)):
        return out
    cm = cable_metrics.drop_duplicates(["inverter_id", "pv"])[
        ["inverter_id", "pv", "vdrop_pct"]
    ].rename(columns={"pv": "_pv_idx", "vdrop_pct": "_vd"})
    cm["inverter_id"] = cm["inverter_id"].astype(str)
    cm["_pv_idx"] = pd.to_numeric(cm["_pv_idx"], errors="coerce")

    work = out.drop(columns=["vdrop_pct", "vdrop_minus_inv_median"])
    # Kolom ``pv`` di tabel ini berbentuk label ("PV4"), cable list memakai int.
    work["_pv_idx"] = pd.to_numeric(
        out["pv"].astype(str).str.extract(r"(\d+)")[0], errors="coerce",
    )
    work = work.merge(cm, on=["inverter_id", "_pv_idx"], how="left")
    work["vdrop_pct"] = work["_vd"]
    work["vdrop_minus_inv_median"] = (
        work["vdrop_pct"]
        - work.groupby("inverter_id")["vdrop_pct"].transform("median")
    )
    return work[CLASSIFICATION_COLUMNS]


def expected_ampm_asymmetry(cross_slope_deg, day_of_year):
    """Asimetri pagi-sore yang HARUS muncul dari cross-slope, relatif meja datar.

    Positif = tanah turun ke timur = pagi lebih kuat. Nilainya dipakai sebagai
    beda terhadap median se-inverter (lihat ``_attach_geometry_metrics``),
    bukan langsung, karena ``ratio`` juga relatif median se-inverter.
    """
    k = np.interp(day_of_year, AMPM_ASYM_DOY, AMPM_ASYM_K, period=365.25)
    return k * np.sin(np.radians(cross_slope_deg))


def attach_geometry_evidence(
    classification: pd.DataFrame,
    string_geometry: Optional[pd.DataFrame],
    day_of_year: int,
) -> pd.DataFrame:
    """Isi kolom bukti kemiringan tanah pada tabel klasifikasi.

    Setiap inverter membandingkan string yang menghadap arah berbeda: sebaran
    cross-slope DALAM SATU inverter bermedian 21,7 derajat. Sebagian
    SHADING_PAGI/SHADING_SORE karena itu geometri murni, bukan halangan yang
    perlu didatangi. ``ampm_residual`` memisahkannya: asimetri besar dengan
    residual ~0 adalah geometri; residual besar barulah obstruksi.

    Acuannya median se-inverter, sama seperti ``ratio`` dan
    ``vdrop_minus_inv_median``. Memakai meja datar sebagai acuan akan
    meninggalkan offset sistematis per inverter -- cross-slope median site
    -0,7 derajat, tapi per inverter jauh berbeda-beda.

    Kolom ini BUKTI, bukan koreksi: ``ratio`` tidak disentuh. Mengoreksinya
    butuh model POA per string per timestamp, bukan aritmetika.

    Menerima tabel klasifikasi apa pun yang punya ``inverter_id``, ``pv``,
    ``pagi``, ``sore`` -- termasuk workbook yang dibuat sebelum kolom ini ada,
    supaya laporan lama bisa dinilai ulang tanpa membaca ulang CSV baseline.
    """
    work = classification.copy()
    for col in GEOMETRY_COLUMNS:
        if col not in work.columns:
            work[col] = np.nan
    if (work.empty or string_geometry is None or string_geometry.empty
            or not {"inverter_id", "pv", "cross_slope_deg"}
            <= set(string_geometry.columns)):
        return work
    geo = string_geometry[["inverter_id", "pv", "cross_slope_deg"]].copy()
    geo["inverter_id"] = geo["inverter_id"].astype(str)
    geo["_pv_idx"] = pd.to_numeric(geo["pv"], errors="coerce")
    # Delapan inverter punya label string yang muncul di DUA posisi DXF, median
    # 308 m terpisah. Label seperti itu tidak menunjuk satu petak, jadi NA --
    # memilih yang pertama berarti menebak lokasi lalu menyajikannya sebagai bukti.
    grp = geo.groupby(["inverter_id", "_pv_idx"])["cross_slope_deg"]
    tegas = grp.first()[grp.nunique(dropna=False) == 1].rename("_cs").reset_index()

    # Kolom ``pv`` di tabel ini berbentuk label ("PV4"), geometri memakai int.
    work["_pv_idx"] = pd.to_numeric(
        work["pv"].astype(str).str.extract(r"(\d+)")[0], errors="coerce",
    )
    work["inverter_id"] = work["inverter_id"].astype(str)
    work = work.merge(tegas, on=["inverter_id", "_pv_idx"], how="left")
    work["cross_slope_deg"] = work.pop("_cs")   # kolom sudah ada -> urutan tetap

    datar = expected_ampm_asymmetry(work["cross_slope_deg"], day_of_year)
    work["expected_ampm_asym"] = (
        datar - datar.groupby(work["inverter_id"]).transform("median")
    ).round(4)
    work["ampm_residual"] = (
        work["pagi"] - work["sore"] - work["expected_ampm_asym"]
    ).round(4)
    return work.drop(columns=["_pv_idx"])


def classify_strings(
    ratio_df: pd.DataFrame,
    profile: pd.DataFrame,
    *,
    cable_metrics: Optional[pd.DataFrame] = None,
    string_geometry: Optional[pd.DataFrame] = None,
    dead_ratio: float = DEFAULT_DEAD_RATIO,
    recovery_ratio: float = DEFAULT_RECOVERY_RATIO,
    flat_range: float = DEFAULT_FLAT_RANGE,
    dropout_share: float = DEFAULT_DROPOUT_SHARE,
    ampm_gap: float = DEFAULT_AMPM_GAP,
) -> pd.DataFrame:
    """Klasifikasi bentuk kurva harian tiap string.

    Kategori (urut prioritas): ``DEAD_OR_OFFLINE``, ``SHADING_PULIH``,
    ``SHADING_SORE``, ``SHADING_PAGI``, ``UNIFORM``, ``CAMPURAN``.
    """
    if profile.empty:
        return pd.DataFrame(columns=CLASSIFICATION_COLUMNS)
    hours = list(profile.columns)
    core = [h for h in hours if 9 <= h <= 15] or hours
    am = [h for h in hours if 7 <= h <= 9]
    pm = [h for h in hours if 15 <= h <= 17]
    late_hour = max(hours)
    share = _dropout_share(ratio_df, late_hour)
    ndays = ratio_df.assign(d=ratio_df["ts"].dt.date).groupby(
        ["inverter_id", "pv"]
    )["d"].nunique()
    ndays.index = [f"{inv}-{pv}" for inv, pv in ndays.index]

    rows = []
    for key, prof in profile.iterrows():
        c = prof[core].dropna()
        if c.empty:
            continue
        med = float(c.median())
        rng = float(c.max() - c.min())
        mx = float(prof.dropna().max())
        drop = float(share.get(key, 0.0))
        a = float(prof[am].dropna().median()) if am and prof[am].notna().any() else np.nan
        p = float(prof[pm].dropna().median()) if pm and prof[pm].notna().any() else np.nan

        if med < dead_ratio:
            kat = "DEAD_OR_OFFLINE"
        elif mx >= recovery_ratio and rng >= 0.08:
            kat = "SHADING_PULIH"
        elif drop >= dropout_share * 100:
            kat = "SHADING_SORE"
        elif np.isfinite(a) and np.isfinite(p) and abs(a - p) >= ampm_gap:
            kat = "SHADING_PAGI" if a < p else "SHADING_SORE"
        elif rng <= flat_range:
            kat = "UNIFORM"
        else:
            kat = "CAMPURAN"

        inv, pv = key.rsplit("-", 1)
        rows.append({
            "pv_string": key, "inverter_id": inv, "pv": pv,
            "n_days": int(ndays.get(key, 0)),
            "ratio_median": round(med, 4),
            "deficit_pct": round((1 - med) * 100, 2),
            "ratio_range": round(rng, 4),
            "ratio_max_hourly": round(mx, 4),
            "jam_terburuk": int(c.idxmin()), "jam_terbaik": int(c.idxmax()),
            "pagi": round(a, 4) if np.isfinite(a) else np.nan,
            "sore": round(p, 4) if np.isfinite(p) else np.nan,
            "dropout_share_pct": round(drop, 1),
            "kategori": kat,
        })
    out = _attach_cable_metrics(
        pd.DataFrame(rows, columns=CLASSIFICATION_COLUMNS), cable_metrics,
    )
    # Asimetri geometris bergeser ~22,5% antar solstis, jadi harapannya dihitung
    # pada pertengahan rentang tanggal yang benar-benar dianalisis.
    out = attach_geometry_evidence(
        out, string_geometry,
        int(pd.Timestamp(ratio_df["ts"].median()).dayofyear),
    )
    return out.sort_values("ratio_median", ignore_index=True)


def rain_recovery(
    ratio_df: pd.DataFrame,
    events: Sequence[dict],
    *,
    midday: tuple = (10, 14),
) -> pd.DataFrame:
    """Uji apakah rasio pulih setelah hujan -- ujian pemisah soiling.

    Hujan mencuci string target DAN tetangganya. Kalau defisitnya debu
    non-seragam, string yang lebih kotor punya lebih banyak yang bisa
    dipulihkan sehingga RASIO naik. Rasio yang tidak bergerak = bukan debu.

    ``events``: ``[{"nama": ..., "before": (d0, d1), "after": (d0, d1)}, ...]``
    dengan tanggal berformat ``YYYY-MM-DD``.
    """
    if ratio_df.empty or not events:
        return pd.DataFrame(columns=RAIN_COLUMNS)
    lo, hi = midday
    mid = ratio_df[(ratio_df["ts"].dt.hour >= lo)
                   & (ratio_df["ts"].dt.hour <= hi)].copy()
    if mid.empty:
        return pd.DataFrame(columns=RAIN_COLUMNS)
    mid["date"] = mid["ts"].dt.normalize()
    mid["pv_string"] = mid["inverter_id"] + "-" + mid["pv"]

    def _median(d0: str, d1: str) -> pd.Series:
        w = mid[(mid["date"] >= pd.Timestamp(d0)) & (mid["date"] <= pd.Timestamp(d1))]
        return w.groupby("pv_string")["ratio"].median()

    rows = []
    for ev in events:
        before = _median(*ev["before"])
        after = _median(*ev["after"])
        for key in before.index.union(after.index):
            b, a = before.get(key, np.nan), after.get(key, np.nan)
            ok = np.isfinite(b) and np.isfinite(a)
            rows.append({
                "pv_string": key, "event": ev["nama"],
                "ratio_before": round(b, 4) if np.isfinite(b) else np.nan,
                "ratio_after": round(a, 4) if np.isfinite(a) else np.nan,
                "delta_pp": round((a - b) * 100, 2) if ok else np.nan,
            })
    return pd.DataFrame(rows, columns=RAIN_COLUMNS)


def seasonal_discriminator(
    by_season: Dict[str, pd.DataFrame],
    *,
    ampm_gap: float = DEFAULT_AMPM_GAP,
    rel_range_max: float = SEASONAL_REL_RANGE_MAX,
) -> pd.DataFrame:
    """Pisahkan asimetri pagi-sore GEOMETRI dari OBSTRUKSI lewat perilaku musiman.

    ``by_season``: {label musim -> DataFrame klasifikasi} dari rentang tanggal
    berbeda (idealnya dekat dua solstis). Butuh kolom ``pv_string``, ``pagi``,
    ``sore``.

    Dasarnya: kemiringan tanah menghasilkan asimetri yang bertanda TETAP
    sepanjang tahun dan besarnya hanya bergeser ~22,5% dari nilainya sendiri.
    Bayangan objek bergeser jamnya mengikuti deklinasi matahari, sehingga
    tandanya bisa berbalik atau besarnya melonjak.

    Verdikt: DATA_KURANG (< 2 musim), TANPA_ASIMETRI, OBSTRUKSI, GEOMETRI.
    """
    per: Dict[str, Dict[str, float]] = {}
    for label, frame in by_season.items():
        if frame is None or frame.empty:
            continue
        for row in frame.itertuples(index=False):
            am, pm = getattr(row, "pagi", np.nan), getattr(row, "sore", np.nan)
            if pd.notna(am) and pd.notna(pm):
                per.setdefault(row.pv_string, {})[label] = float(am) - float(pm)

    labels = list(by_season)
    rows: List[dict] = []
    for key, seasons in per.items():
        amps = list(seasons.values())
        mean_abs = float(np.mean([abs(a) for a in amps]))
        spread = (max(amps) - min(amps)) if len(amps) > 1 else np.nan
        rel = spread / mean_abs if len(amps) > 1 and mean_abs > 0 else np.nan

        if len(amps) < 2:
            verdikt = "DATA_KURANG"
        elif max(abs(a) for a in amps) < ampm_gap:
            verdikt = "TANPA_ASIMETRI"
        elif min(amps) < 0 < max(amps):
            verdikt = "OBSTRUKSI"          # tanda berbalik -- tak mungkin geometri
        elif rel > rel_range_max:
            verdikt = "OBSTRUKSI"          # bergeser jauh di atas drift geometris
        else:
            verdikt = "GEOMETRI"

        entry = {
            "pv_string": key,
            "n_musim": len(amps),
            "asym_mean": round(float(np.mean(amps)), 4),
            "asym_rel_range": None if pd.isna(rel) else round(float(rel), 3),
            "verdikt": verdikt,
        }
        for label in labels:
            entry[f"asym_{label}"] = (round(seasons[label], 4)
                                      if label in seasons else None)
        rows.append(entry)

    columns = SEASONAL_COLUMNS + [f"asym_{lb}" for lb in labels]
    out = pd.DataFrame(rows, columns=columns)
    return out.sort_values(["verdikt", "pv_string"], ignore_index=True)


@dataclass
class IntradayDiagnosticReport:
    """Hasil diagnostik siap tulis ke Excel."""

    classification: pd.DataFrame
    profile: pd.DataFrame
    rain: pd.DataFrame
    metadata: pd.DataFrame

    def to_excel(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with pd.ExcelWriter(target, engine="openpyxl") as writer:
            self.classification.to_excel(writer, sheet_name="Klasifikasi", index=False)
            self.profile.to_excel(writer, sheet_name="Profil_Jam")
            self.rain.to_excel(writer, sheet_name="Uji_Hujan", index=False)
            self.metadata.to_excel(writer, sheet_name="Metadata", index=False)
        return target

    def summary(self) -> str:
        if self.classification.empty:
            return "Tidak ada string yang bisa diklasifikasi."
        counts = self.classification["kategori"].value_counts()
        lines = [f"{len(self.classification)} string terklasifikasi:"]
        lines += [f"  {k:<16} {v}" for k, v in counts.items()]
        return "\n".join(lines)


def build_intraday_diagnostic(
    csv_paths: Sequence[Path | str],
    *,
    inverter_ids: Optional[Iterable[str]] = None,
    empty_pv_map: Optional[Dict[str, List[int]]] = None,
    rain_events: Sequence[dict] = (),
    cable_metrics: Optional[pd.DataFrame] = None,
    string_geometry: Optional[pd.DataFrame] = None,
    pv_max: int = DEFAULT_PV_MAX,
) -> IntradayDiagnosticReport:
    """Orkestrasi penuh: CSV baseline -> klasifikasi + profil + uji hujan."""
    paths = list(csv_paths)
    long_df = load_baseline_power_long(
        paths, inverter_ids=inverter_ids,
        empty_pv_map=empty_pv_map, pv_max=pv_max,
    )
    ratio_df = add_sibling_ratio(long_df)
    profile = build_hourly_profile(ratio_df)
    classification = classify_strings(
        ratio_df, profile, cable_metrics=cable_metrics,
        string_geometry=string_geometry,
    )
    rain = rain_recovery(ratio_df, rain_events)
    dates = sorted(ratio_df["ts"].dt.date.unique()) if not ratio_df.empty else []
    meta = pd.DataFrame([
        ("metode", "rasio daya string / median tetangga se-inverter per timestamp"),
        ("pemisah", "soiling = profil datar; shading = jam-spesifik, rasio bisa >1,0"),
        ("n_file_csv", len(paths)),
        ("n_hari", len(dates)),
        ("tanggal_awal", str(dates[0]) if dates else ""),
        ("tanggal_akhir", str(dates[-1]) if dates else ""),
        ("n_string", int(classification.shape[0])),
        ("ambang_dead_ratio", DEFAULT_DEAD_RATIO),
        ("ambang_recovery_ratio", DEFAULT_RECOVERY_RATIO),
        ("ambang_flat_range", DEFAULT_FLAT_RANGE),
        ("ambang_dropout_share", DEFAULT_DROPOUT_SHARE),
        ("catatan",
         "M2aShading level inverter buta thd shading 1-2 string; modul ini menutupnya"),
        ("uji_musiman",
         "SHADING_PAGI/SORE punya dua sebab. Jalankan lagi pada rentang "
         "tanggal dekat solstis yang lain, lalu seasonal_discriminator() atas "
         "kedua klasifikasi: geometri bertanda tetap dan bergeser <=30%, "
         "obstruksi berbalik tanda atau melonjak."),
        ("bukti_geometris",
         "cross_slope_deg + expected_ampm_asym + ampm_residual. Acuan harapan "
         "adalah MEDIAN SE-INVERTER, sama seperti ratio -- bukan meja datar. "
         "Asimetri besar dengan residual ~0 adalah geometri dan tidak perlu "
         "dikunjungi; residual besar barulah obstruksi. Ini bukti, BUKAN "
         "koreksi: ratio tidak disentuh."),
        ("form_lapangan", " | ".join(FIELD_OBSERVATION_COLUMNS)),
        ("form_lapangan_kenapa",
         "Apakah meja diratakan (tanah digrading) atau mengikuti kemiringan "
         "menyamping tidak terjawab oleh gambar mana pun; satu foto membidik "
         "memanjang sepanjang baris meja menutupnya."),
    ], columns=["key", "value"])
    return IntradayDiagnosticReport(classification, profile, rain, meta)
