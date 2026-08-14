"""Gabung + rekap sheet M2e_hybrid_AllStrings dari kumpulan m2_findings xlsx.

Input : folder berisi m2_findings_{YYYYMMDD}.xlsx (output daily notebook,
        mis. Drive "Cek PV String/outputs").
Output: 1 file excel berisi:
    - RekapUptimePct  : pivot uptime_pct -- baris (inverter_id, pv_string),
                        kolom tanggal.
    - RekapPerString  : ringkasan per string lintas tanggal (n_days, mean/min
                        uptime, jumlah hari di bawah ambang, total downtime,
                        dan n_days_link_outage -- hari saat SELURUH kelompok
                        transport string itu hilang dari ekspor).
    - Tautan          : tanggal x kelompok transport yang tidak lengkap.
                        LINK_OUTAGE = faktor eksternal (fiber IconPlus),
                        BUKAN downtime pembangkit; INVERTER_ABSENCE = memang
                        inverternya.
    - AllStrings      : gabungan long semua tanggal (kolom date di depan).
                        Bila melebihi batas baris Excel (1.048.576), sheet
                        dilewati dan data ditulis ke CSV pendamping.

Usage (Colab):
    !python rekap_m2e_allstrings.py \
        --outputs-dir "/content/drive/MyDrive/Cek PV String/outputs"

    Default output: <outputs-dir>/rekap_m2e_allstrings.xlsx
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import datetime
from typing import List, Optional, Tuple

import pandas as pd

from pv_pipeline.availability import (
    TELEMETRY_LINK_GROUPS,
    detect_link_outage,
)

ALLSTRINGS_SHEET = "M2e_hybrid_AllStrings"
EXCEL_MAX_ROWS = 1_048_576
_FINDINGS_XLSX_RE = re.compile(r"^m2_findings_(\d{8})\.xlsx$", re.I)

REKAP_PER_STRING_COLUMNS: List[str] = [
    "inverter_id", "pv_string", "n_days", "n_days_empty",
    "uptime_mean", "uptime_min", "worst_date",
    "n_days_below_threshold", "downtime_minutes_total",
    "n_days_link_outage",
]


def discover_findings_xlsx(dir_path: str) -> List[Tuple[pd.Timestamp, str]]:
    """List (tanggal, path) m2_findings_{YYYYMMDD}.xlsx terurut tanggal."""
    out: List[Tuple[pd.Timestamp, str]] = []
    for fn in os.listdir(dir_path):
        m = _FINDINGS_XLSX_RE.match(fn)
        if not m:
            continue
        day = pd.Timestamp(datetime.strptime(m.group(1), "%Y%m%d"))
        out.append((day, os.path.join(dir_path, fn)))
    return sorted(out)


def load_allstrings(path: str, day: pd.Timestamp) -> Optional[pd.DataFrame]:
    """Baca sheet AllStrings satu file; None bila sheet tidak ada/kosong.

    Kolom ``date`` (tanggal dari nama file) disisipkan paling depan.
    """
    try:
        xl = pd.ExcelFile(path)
        if ALLSTRINGS_SHEET not in xl.sheet_names:
            print(f"[rekap-m2e] SKIP {os.path.basename(path)}: tidak ada sheet "
                  f"{ALLSTRINGS_SHEET} (sheets: {xl.sheet_names})")
            return None
        df = pd.read_excel(path, sheet_name=ALLSTRINGS_SHEET)
    except Exception as exc:
        print(f"[rekap-m2e] SKIP {os.path.basename(path)}: gagal dibaca ({exc})")
        return None
    if df.empty:
        return None
    df.insert(0, "date", day)
    return df


_PV_NUM_RE = re.compile(r"(\d+)")


def _pv_num(pv_string) -> int:
    """'PV10' -> 10 (kunci sort natural); tanpa angka -> paling akhir."""
    m = _PV_NUM_RE.search(str(pv_string))
    return int(m.group(1)) if m else 10 ** 9


def build_uptime_pivot(long_df: pd.DataFrame) -> pd.DataFrame:
    """Pivot uptime_pct: baris (inverter_id, pv_string), kolom tanggal.

    Baris diurutkan NATURAL per nomor PV (PV1, PV2, ..., PV10) -- sort
    default pivot_table leksikal ('PV10' < 'PV2') menyulitkan pembacaan.
    """
    pv = long_df.pivot_table(
        index=["inverter_id", "pv_string"],
        columns="date",
        values="uptime_pct",
        aggfunc="mean",
    )
    pv.columns = [pd.Timestamp(c).strftime("%Y-%m-%d") for c in pv.columns]
    out = pv.reset_index()
    out["_pv_num"] = out["pv_string"].map(_pv_num)
    return out.sort_values(
        ["inverter_id", "_pv_num", "pv_string"], ignore_index=True,
    ).drop(columns="_pv_num")


def build_rekap_per_string(
    long_df: pd.DataFrame,
    uptime_threshold_pct: float = 95.0,
) -> pd.DataFrame:
    """Ringkasan per string lintas tanggal, terurut paling bermasalah dulu."""
    rows = []
    for (inv, pvs), g in long_df.groupby(["inverter_id", "pv_string"]):
        uptime = pd.to_numeric(g["uptime_pct"], errors="coerce")
        valid = g.loc[uptime.notna()]
        u = uptime.dropna()
        worst_date = None
        if not u.empty:
            worst_date = pd.Timestamp(
                valid.loc[u.idxmin(), "date"]
            ).strftime("%Y-%m-%d")
        rows.append({
            "inverter_id": inv,
            "pv_string": pvs,
            "n_days": int(g["date"].nunique()),
            "n_days_empty": int((g["status"] == "EMPTY").sum()),
            "uptime_mean": float(u.mean()) if not u.empty else float("nan"),
            "uptime_min": float(u.min()) if not u.empty else float("nan"),
            "worst_date": worst_date,
            "n_days_below_threshold": int((u < uptime_threshold_pct).sum()),
            "downtime_minutes_total": float(
                pd.to_numeric(g["downtime_minutes"], errors="coerce").sum()
            ),
        })
    out = pd.DataFrame(rows, columns=REKAP_PER_STRING_COLUMNS)
    return out.sort_values(
        ["n_days_below_threshold", "uptime_mean"],
        ascending=[False, True],
        ignore_index=True,
    )


def build_link_audit(long_df: pd.DataFrame) -> pd.DataFrame:
    """Tanggal mana yang kehilangan SELURUH satu kelompok transport.

    Situs mengekspor dari dua Fusion Solar di jaringan berbeda: Phase One
    (WB01/WB02) lewat fiber IconPlus, WB03-WB10 lewat ethernet lokal. Fiber
    putus menghapus seluruh Phase One dari ekspor hari itu.

    Rekap ini mengelompokkan baris yang ADA, jadi tanggal yang hilang hanya
    mengecilkan ``n_days`` -- tanpa satu pun kolom yang memberi tahu kenapa.
    Akibatnya ``uptime_mean`` dua plant dihitung atas jumlah hari berbeda lalu
    diperbandingkan seolah setara.

    Roster "yang seharusnya ada" disimpulkan dari data itu sendiri: gabungan
    seluruh inverter yang pernah terlihat pada rentang tanggal ini.
    Konsekuensinya perlu disadari -- inverter yang TIDAK PERNAH melapor
    sepanjang rentang tidak ada di roster dan karenanya tidak terdeteksi. Untuk
    kasus itu pakai temuan ``M2e_link`` dari run harian, yang rosternya diambil
    dari ``empty_pv_map``.

    Returns
    -------
    DataFrame
        ``date``, ``group``, ``expected``, ``present``, ``missing``,
        ``verdict``. Hanya kelompok yang TIDAK lengkap; tanggal normal tidak
        menghasilkan baris supaya penandanya tetap langka dan tetap dibaca.
    """
    if long_df.empty or "inverter_id" not in long_df.columns:
        return pd.DataFrame(
            columns=["date", "group", "expected", "present", "missing",
                     "verdict"]
        )

    roster = sorted(long_df["inverter_id"].dropna().astype(str).unique())
    baris = []
    for hari, sub in long_df.groupby("date", sort=True):
        hadir = sub["inverter_id"].dropna().astype(str).unique()
        for b in detect_link_outage(hadir, roster):
            baris.append({
                "date": hari,
                "group": b["group"],
                "expected": b["expected"],
                "present": b["present"],
                "missing": b["missing"],
                "verdict": b["verdict"],
            })
    return pd.DataFrame(
        baris,
        columns=["date", "group", "expected", "present", "missing", "verdict"],
    )


def attach_link_days(
    rekap: pd.DataFrame,
    link_audit: pd.DataFrame,
) -> pd.DataFrame:
    """Tambahkan ``n_days_link_outage`` per string ke rekap.

    Dihitung hanya dari vonis ``LINK_OUTAGE``. ``INVERTER_ABSENCE`` sengaja
    TIDAK dihitung: inverter yang benar-benar mati bukan faktor eksternal, dan
    memberinya label tautan akan membuatnya hilang dari perhatian -- arah
    kegagalan yang paling mahal di sini.
    """
    out = rekap.copy()
    if link_audit.empty:
        out["n_days_link_outage"] = 0
        return out[REKAP_PER_STRING_COLUMNS]

    putus = link_audit[link_audit["verdict"] == "LINK_OUTAGE"]
    per_grup = putus.groupby("group")["date"].nunique().to_dict()

    blok_ke_grup = {
        b: nama for nama, blok in TELEMETRY_LINK_GROUPS.items() for b in blok
    }
    out["n_days_link_outage"] = [
        per_grup.get(
            blok_ke_grup.get(str(inv).strip().upper().split("-")[0]), 0,
        )
        for inv in out["inverter_id"]
    ]
    return out[REKAP_PER_STRING_COLUMNS]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gabung + rekap M2e_hybrid_AllStrings ke 1 file excel.",
    )
    parser.add_argument(
        "--outputs-dir", required=True,
        help="Folder berisi m2_findings_{YYYYMMDD}.xlsx.",
    )
    parser.add_argument(
        "--output-xlsx", default=None,
        help="Path file excel hasil. Default: "
             "<outputs-dir>/rekap_m2e_allstrings.xlsx",
    )
    parser.add_argument(
        "--uptime-threshold", type=float, default=95.0,
        help="Ambang %% untuk kolom n_days_below_threshold (default 95).",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.outputs_dir):
        raise SystemExit(f"[rekap-m2e] folder tidak ditemukan: {args.outputs_dir!r}")
    out_xlsx = args.output_xlsx or os.path.join(
        args.outputs_dir, "rekap_m2e_allstrings.xlsx",
    )

    files = discover_findings_xlsx(args.outputs_dir)
    if not files:
        raise SystemExit(
            f"[rekap-m2e] tidak ada m2_findings_YYYYMMDD.xlsx di {args.outputs_dir!r}"
        )
    print(f"[rekap-m2e] {len(files)} file ditemukan "
          f"({files[0][0].date()} .. {files[-1][0].date()})")

    frames: List[pd.DataFrame] = []
    for i, (day, path) in enumerate(files, 1):
        df = load_allstrings(path, day)
        if df is not None:
            frames.append(df)
        if i % 25 == 0 or i == len(files):
            print(f"[rekap-m2e] {i}/{len(files)} file dibaca...")
    if not frames:
        raise SystemExit("[rekap-m2e] tidak ada sheet AllStrings yang terbaca.")

    long_df = pd.concat(frames, ignore_index=True)
    print(f"[rekap-m2e] gabungan: {len(long_df)} baris, "
          f"{long_df['date'].nunique()} tanggal, "
          f"{long_df.groupby(['inverter_id', 'pv_string']).ngroups} string")

    pivot = build_uptime_pivot(long_df)
    link_audit = build_link_audit(long_df)
    rekap = attach_link_days(
        build_rekap_per_string(long_df, args.uptime_threshold), link_audit,
    )

    _putus = link_audit[link_audit["verdict"] == "LINK_OUTAGE"]
    if len(_putus):
        print(f"[rekap-m2e] {_putus['date'].nunique()} tanggal kehilangan "
              f"SELURUH satu kelompok transport -- putus tautan, faktor "
              f"eksternal, BUKAN downtime. Lihat sheet Tautan; kolom "
              f"n_days_link_outage menjelaskan kenapa n_days berbeda "
              f"antar plant.")
    else:
        print("[rekap-m2e] tidak ada tanggal dengan kelompok transport hilang "
              "seluruhnya.")

    with pd.ExcelWriter(out_xlsx) as writer:
        pivot.to_excel(writer, sheet_name="RekapUptimePct", index=False)
        rekap.to_excel(writer, sheet_name="RekapPerString", index=False)
        link_audit.to_excel(writer, sheet_name="Tautan", index=False)
        if len(long_df) + 1 <= EXCEL_MAX_ROWS:
            long_df.to_excel(writer, sheet_name="AllStrings", index=False)
        else:
            csv_path = os.path.splitext(out_xlsx)[0] + "_allstrings_long.csv"
            long_df.to_csv(csv_path, index=False)
            print(f"[rekap-m2e] WARNING: {len(long_df)} baris melebihi batas "
                  f"Excel ({EXCEL_MAX_ROWS}); sheet AllStrings dilewati, "
                  f"gabungan long ditulis ke {csv_path}")
    print(f"[rekap-m2e] hasil: {out_xlsx}")


if __name__ == "__main__":
    main()
