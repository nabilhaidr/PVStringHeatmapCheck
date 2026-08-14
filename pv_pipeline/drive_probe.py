"""Probe murah atas data Drive -- menjawab pertanyaan cakupan tanpa run penuh.

Empat pertanyaan yang selama ini berstatus "menunggu" sebenarnya tidak menunggu
data, melainkan menunggu dibaca. Masing-masing dijawab dengan membaca satu
berkas atau satu deret, bukan dengan menjalankan diagnostik 1,4 GB:

- :func:`probe_inverter_coverage` -- apakah ekspor periode ini memuat Phase One,
  dan berapa kanal PV yang benar-benar ada. Satu CSV, header + satu kolom.
- :func:`rank_variable_days` -- hari mana yang berawan sebagian. Deret POA.
- :func:`probe_channel_silence` -- kanal yang dituduh kosong itu memang diam,
  atau inverternya yang tidak melapor. Satu CSV.
- :func:`inventory_baseline` -- bulan apa saja yang tersedia di Drive, untuk
  memilih jendela musim ketiga.

Semuanya menolak menjawab ketika datanya tidak mendukung. Itu disengaja: probe
yang mengembalikan nol saat berkasnya cacat, atau 0,00 kW saat inverternya diam,
menghasilkan vonis yang terlihat wajar dan salah.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from pv_pipeline.m2a.shading import (
    PV_I_COL_TEMPLATE,
    PV_POWER_COL_TEMPLATE,
    PV_V_COL_TEMPLATE,
    _normalize_pv_columns,
)
from pv_pipeline.transformations import transform_manage_object_to_id

# Ekspor Huawei memuat kanal sampai PV36 walau tidak semuanya terpasang.
# Diagnostik memotong di 28 karena itu string terbanyak per inverter; probe
# cakupan tidak boleh ikut memotong -- tugasnya melaporkan apa yang ADA.
DEFAULT_PV_MAX: int = 36

# Jendela untuk peringkat variabilitas. Fajar dan senja dibuang: ramp-nya
# geometri matahari, bukan awan, dan kalau ikut dihitung setiap hari cerah
# tampak sangat variabel.
DEFAULT_VARIABILITY_MIDDAY: Tuple[int, int] = (9, 15)

# Di bawah ini harinya mendung total, bukan berawan sebagian. Mendung total
# tidak punya bayangan bergerak -- cahayanya difus dan merata, jadi tidak ada
# kontras spasial antar string untuk dikorelasikan.
DEFAULT_MIN_MEAN_POA: float = 400.0

# Jendela dan ambang untuk uji kanal diam.
DEFAULT_MIDDAY: Tuple[int, int] = (10, 14)
# Kanal kosong membaca 0,00; margin ini untuk derau sensor, bukan untuk
# menampung string yang produksinya rendah.
DEFAULT_SILENT_KW: float = 0.1
# String 24-26 modul yang hidup pada tengah hari menarik jauh di atas 1 kW.
# Kalau saudaranya sendiri di bawah ini, inverternya sedang ditekan atau
# harinya mendung, dan perbandingannya tidak membuktikan apa pun.
DEFAULT_MIN_SIBLING_KW: float = 1.0

_BULAN_RE = re.compile(r"^\d{4}-\d{2}$")


@dataclass(frozen=True)
class CoverageProbe:
    """Cakupan satu CSV baseline harian."""

    inverters: List[str]
    phase_one: List[str]
    phase_two: List[str]
    pv_channels: List[int]
    channels_lowercase: List[int]
    channels_titlecase: List[int]

    def ringkas(self) -> str:
        return (
            f"{len(self.inverters)} inverter "
            f"({len(self.phase_one)} Phase One, {len(self.phase_two)} WB03-10) - "
            f"{len(self.pv_channels)} kanal PV "
            f"({len(self.channels_lowercase)} huruf kecil, "
            f"{len(self.channels_titlecase)} huruf besar)"
        )


def probe_inverter_coverage(
    csv_path: Path | str,
    *,
    pv_max: int = DEFAULT_PV_MAX,
) -> CoverageProbe:
    """Baca satu CSV baseline -> inverter dan kanal PV yang ada di dalamnya.

    Hanya header dan kolom ``ManageObject`` yang dibaca, jadi ongkosnya tetap
    kecil untuk berkas 28 MB.

    Kedua konvensi huruf dilaporkan terpisah. Satu berkas memakai keduanya --
    ``PV1 input current(A)`` sampai PV14 lalu ``PV15 Input Current(A)``
    seterusnya -- dan pembacaan yang peka huruf menjatuhkan separuhnya tanpa
    galat apa pun.

    Raises
    ------
    ValueError
        Bila kolom ``ManageObject`` tidak ada. Melaporkan "nol inverter" untuk
        berkas cacat akan terbaca sebagai "ekspor format lama", yaitu vonis
        yang salah atas sebab yang salah.
    """
    path = Path(csv_path)
    header = pd.read_csv(path, nrows=0)
    if "ManageObject" not in header.columns:
        raise ValueError(
            f"{path.name}: kolom ManageObject tidak ada -- berkas ini bukan "
            f"ekspor baseline yang dikenali, dan cakupannya tidak bisa dinilai."
        )

    kolom = set(header.columns)
    kecil, besar = [], []
    for n in range(1, pv_max + 1):
        if PV_I_COL_TEMPLATE.format(pv=n) in kolom:
            kecil.append(n)
        elif f"PV{n} Input Current(A)" in kolom:
            besar.append(n)
        elif PV_POWER_COL_TEMPLATE.format(pv=n) in kolom:
            besar.append(n)

    mo = pd.read_csv(path, usecols=["ManageObject"])["ManageObject"]
    ids = sorted({
        inv for inv in mo.map(transform_manage_object_to_id).dropna().unique()
    })
    phase_one = [i for i in ids if i[:4] in ("WB01", "WB02")]

    return CoverageProbe(
        inverters=ids,
        phase_one=phase_one,
        phase_two=[i for i in ids if i[:4] not in ("WB01", "WB02")],
        pv_channels=sorted(kecil + besar),
        channels_lowercase=kecil,
        channels_titlecase=besar,
    )


def rank_variable_days(
    poa: pd.Series,
    *,
    midday: Tuple[int, int] = DEFAULT_VARIABILITY_MIDDAY,
    min_mean_poa: float = DEFAULT_MIN_MEAN_POA,
) -> pd.DataFrame:
    """Peringkatkan hari menurut variabilitas iradians di dalam hari.

    Yang dicari bukan hari mendung melainkan hari cerah yang DIINTERUPSI: awan
    lewat, bayangannya bergerak melintasi larik, dan itulah kontras spasial yang
    dibaca uji korelasi. Ukurannya rata-rata lompatan antar cuplikan dibagi
    tingkat iradians -- tak berdimensi, jadi hari terang dan hari redup
    sebanding.

    Hari yang gagal ``min_mean_poa`` tetap dikembalikan, ditandai
    ``cukup_terang=False`` dan diurutkan di bawah. Membuangnya diam-diam
    menyembunyikan bahwa periode itu memang tidak punya hari yang layak.

    Returns
    -------
    DataFrame
        Kolom ``tanggal``, ``n``, ``poa_rata``, ``variabilitas``,
        ``cukup_terang``; hari yang memenuhi lantai lebih dulu, lalu
        variabilitas menurun.
    """
    lo, hi = midday
    jam = poa.index.hour
    jendela = poa[(jam >= lo) & (jam <= hi)].dropna()
    if jendela.empty:
        return pd.DataFrame(
            columns=["tanggal", "n", "poa_rata", "variabilitas", "cukup_terang"]
        )

    baris = []
    for tanggal, deret in jendela.groupby(jendela.index.date):
        rata = float(deret.mean())
        lompatan = deret.diff().abs().dropna()
        baris.append({
            "tanggal": tanggal,
            "n": int(len(deret)),
            "poa_rata": round(rata, 1),
            "variabilitas": round(float(lompatan.mean()) / rata, 4) if rata else 0.0,
            "cukup_terang": rata >= min_mean_poa,
        })

    out = pd.DataFrame(baris)
    return (out.sort_values(["cukup_terang", "variabilitas"], ascending=False)
               .reset_index(drop=True))


def _daya_per_kanal(df: pd.DataFrame, pv_max: int) -> Dict[int, pd.Series]:
    """Daya kW per kanal PV, dari kolom Power atau dari V x I."""
    keluar: Dict[int, pd.Series] = {}
    for n in range(1, pv_max + 1):
        p_col = PV_POWER_COL_TEMPLATE.format(pv=n)
        if p_col in df.columns:
            keluar[n] = pd.to_numeric(df[p_col], errors="coerce")
            continue
        v_col = PV_V_COL_TEMPLATE.format(pv=n)
        i_col = PV_I_COL_TEMPLATE.format(pv=n)
        if v_col in df.columns and i_col in df.columns:
            volt = pd.to_numeric(df[v_col], errors="coerce")
            arus = pd.to_numeric(df[i_col], errors="coerce")
            keluar[n] = volt * arus / 1000.0
    return keluar


def probe_channel_silence(
    csv_path: Path | str,
    inverter_id: str,
    pv: int,
    *,
    midday: Tuple[int, int] = DEFAULT_MIDDAY,
    pv_max: int = DEFAULT_PV_MAX,
    silent_kw: float = DEFAULT_SILENT_KW,
    min_sibling_kw: float = DEFAULT_MIN_SIBLING_KW,
) -> dict:
    """Uji satu kanal PV terhadap saudara se-inverternya pada tengah hari.

    Ini aturan ``disprove_empty_channel`` dijalankan langsung atas telemetri
    mentah, tanpa lewat ``empty_pv_map``. Diagnostik tidak bisa menjawabnya:
    ia menyaring kanal kosong lebih dulu, jadi menanyakan "apakah kanal ini
    kosong" kepadanya melingkar.

    Empat putusan, dan dua di antaranya menolak menyimpulkan:

    ``TIDAK_MELAPOR``
        Inverternya tidak ada di berkas ini. Bukan nol -- tidak tahu. Persis
        keadaan WB05-INV05 pada 2026-05-13.
    ``TIDAK_MENENTUKAN``
        Saudaranya sendiri di bawah ``min_sibling_kw``; tidak ada pembanding.
    ``KOSONG_TERBUKTI``
        Target diam sementara saudaranya berproduksi -> pemetaan as-built ke
        kanal ini terbantah.
    ``TERPAKAI``
        Target berproduksi -> pemetaan as-built-nya benar.
    """
    path = Path(csv_path)
    df = _normalize_pv_columns(pd.read_csv(path, low_memory=False))
    if "ManageObject" not in df.columns:
        raise ValueError(f"{path.name}: kolom ManageObject tidak ada.")

    df = df.copy()
    df["_inv"] = df["ManageObject"].map(transform_manage_object_to_id)
    df = df[df["_inv"].astype(str).str.upper() == str(inverter_id).upper()]

    kosong = {
        "inverter_hadir": False,
        "putusan": "TIDAK_MELAPOR",
        "target_kw": None,
        "saudara_kw": None,
        "saudara_n": 0,
    }
    if df.empty:
        return kosong

    if "Start Time" in df.columns:
        ts = pd.to_datetime(df["Start Time"], errors="coerce")
        lo, hi = midday
        df = df[(ts.dt.hour >= lo) & (ts.dt.hour <= hi)]
    if df.empty:
        return {**kosong, "inverter_hadir": True, "putusan": "TIDAK_MENENTUKAN"}

    daya = _daya_per_kanal(df, pv_max)
    if pv not in daya:
        raise ValueError(
            f"{path.name}: kanal PV{pv} tidak punya kolom daya maupun V/I."
        )

    target = float(pd.Series(daya[pv]).median())
    saudara = [float(pd.Series(s).median()) for n, s in daya.items() if n != pv]
    hidup = [m for m in saudara if pd.notna(m) and m > silent_kw]
    saudara_kw = float(pd.Series(hidup).median()) if hidup else None

    if saudara_kw is None or saudara_kw < min_sibling_kw:
        putusan = "TIDAK_MENENTUKAN"
    elif target <= silent_kw:
        putusan = "KOSONG_TERBUKTI"
    else:
        putusan = "TERPAKAI"

    return {
        "inverter_hadir": True,
        "putusan": putusan,
        "target_kw": target,
        "saudara_kw": saudara_kw,
        "saudara_n": len(hidup),
    }


def inventory_baseline(baseline_dir: Path | str) -> pd.DataFrame:
    """Daftar bulan di folder baseline Drive, untuk memilih jendela musim.

    Bulan yang foldernya ada tapi kosong dilaporkan ``n_hari=0``, tidak
    dihilangkan: "ekspornya gagal" dan "periodenya tidak pernah ada" menuntut
    tindakan berbeda, dan tabel inilah yang dipakai memilih jendela ketiga.

    ``doy_tengah`` memakai day-of-year karena pemisahan musim diukur di situ --
    dua jendela yang sudah ada jatuh di doy 166 dan 335.
    """
    root = Path(baseline_dir)
    baris = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if not _BULAN_RE.match(folder.name):
            continue
        berkas = sorted(folder.glob("*.csv"))
        tanggal = pd.to_datetime(
            [p.stem for p in berkas], format="%Y-%m-%d", errors="coerce"
        ).dropna()
        if len(tanggal):
            awal, akhir = tanggal.min(), tanggal.max()
            tengah = awal + (akhir - awal) / 2
            baris.append({
                "bulan": folder.name,
                "n_hari": len(berkas),
                "tanggal_awal": awal.date(),
                "tanggal_akhir": akhir.date(),
                "doy_tengah": int(tengah.dayofyear),
            })
        else:
            baris.append({
                "bulan": folder.name,
                "n_hari": len(berkas),
                "tanggal_awal": None,
                "tanggal_akhir": None,
                "doy_tengah": None,
            })

    return pd.DataFrame(
        baris,
        columns=["bulan", "n_hari", "tanggal_awal", "tanggal_akhir", "doy_tengah"],
    )
