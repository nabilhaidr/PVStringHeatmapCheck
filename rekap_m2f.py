"""Rekap bulanan M2f dari kumpulan workbook harian M2f.

Input : folder berisi m2f_loss_attribution_{YYYYMMDD}.xlsx -- satu per hari,
        hasil notebook/m2f_loss_attribution.ipynb yang dijalankan
        RUN_BATCH_orchestrator.ipynb (TEMPLATE_NB = notebook M2f), mis. Drive
        "Cek PV String/outputs_m2f".
Output: rekap_m2f_bulanan.xlsx + PNG waterfall/Pareto per bulan:
    - Bulanan           : per bulan -- jumlah hari, string-hari dinilai /
                          di-skip, E_expected, E_actual, L_total, kWh per
                          kategori (kosong = tidak pernah diukur bulan itu),
                          dan porsi unexplained.
    - Waterfall_YYYY-MM : tabel waterfall, builder yang sama dengan run harian.
    - Pareto_YYYY-MM    : tabel Pareto, builder yang sama dengan run harian.
    - Harian            : ringkasan per hari, untuk menelusuri hari janggal.
    - KalibrasiHarian   : measured_ratio per WB per hari (M2f_BaselineCalib),
                          bahan memilih m2f.dc_derate_per_wb.

Hanya berkas harian (8 digit) yang dibaca. Berkas rentang dari run multi-hari
(m2f_loss_attribution_YYYYMMDD-YYYYMMDD.xlsx) dilewati: bila berdampingan
dengan berkas harian untuk hari yang sama, menjumlah keduanya menggandakan
rugi bulan itu.

Usage (Colab):
    !python rekap_m2f.py \
        --input-dir "/content/drive/MyDrive/Cek PV String/outputs_m2f"

    Default output: <input-dir>/rekap_m2f_bulanan.xlsx
"""
from __future__ import annotations

import argparse
import os
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

from pv_pipeline.m2f.ledger import CLAIMABLE_CATEGORIES, LOCKED_CATEGORIES
from pv_pipeline.m2f.pareto import build_pareto_table
from pv_pipeline.m2f.plots import build_waterfall_table

_M2F_XLSX_RE = re.compile(r"^m2f_loss_attribution_(.+)\.xlsx$", re.I)
_DAILY_XLSX_RE = re.compile(r"^m2f_loss_attribution_(\d{8})\.xlsx$", re.I)
_NAN = float("nan")


def discover_m2f_xlsx(dir_path: str) -> List[Tuple[pd.Timestamp, str]]:
    """(tanggal, path) workbook M2f HARIAN di folder, terurut tanggal."""
    out: List[Tuple[pd.Timestamp, str]] = []
    for name in sorted(os.listdir(dir_path)):
        match = _DAILY_XLSX_RE.match(name)
        if match:
            out.append((pd.Timestamp(match.group(1)), os.path.join(dir_path, name)))
    return out


def _sheet(workbook: pd.ExcelFile, name: str) -> Optional[str]:
    """Nama sheet persis, atau berprefiks nama submodule.

    ``M2Engine.write_xlsx_multi`` menulis ``{submodule}_{artifact}`` bila
    muat 31 karakter, dan nama artifact polos bila tidak.
    """
    for sheet in workbook.sheet_names:
        if sheet == name or sheet.endswith(f"_{name}"):
            return sheet
    return None


def load_day(path: str) -> dict:
    """Muat satu workbook harian. Sheet inti yang hilang me-raise."""
    workbook = pd.ExcelFile(path)
    name = os.path.basename(path)

    def parse(artifact: str, required: bool = True) -> Optional[pd.DataFrame]:
        sheet = _sheet(workbook, artifact)
        if sheet is None:
            if required:
                raise ValueError(f"[rekap_m2f] {name}: sheet {artifact!r} tidak ada.")
            return None
        return workbook.parse(sheet)

    waterfall = parse("M2f_Waterfall")
    e_expected = waterfall.loc[waterfall["label"] == "E_expected", "delta_kwh"]
    return {
        "date": pd.Timestamp(_DAILY_XLSX_RE.match(name).group(1)),
        "e_expected_kwh": float(e_expected.iloc[0]),
        "per_string": parse("M2f_PerString"),
        "closure": parse("M2f_Closure"),
        # Workbook sebelum 2026-09-27 punya M2f_BifacialCalib tanpa
        # measured_ratio -- absen di sini, bukan error.
        "calib": parse("M2f_BaselineCalib", required=False),
    }


def _totals(per_string: pd.DataFrame) -> Dict[str, Optional[float]]:
    """kWh per kategori. Kategori yang tidak pernah muncul = None, bukan 0.0.

    Sama dengan site_totals di M2fLossAttribution: satu string-hari terukur
    sudah membuat kategori terukur; absen di semua string-hari berarti
    "tidak pernah diukur", dan 0.0 akan terbaca "dicek, bersih".
    """
    sums = per_string.groupby("category")["loss_kwh"].sum()
    out: Dict[str, Optional[float]] = {
        cat: (float(sums[cat]) if cat in sums.index else None)
        for cat in CLAIMABLE_CATEGORIES
    }
    out.update({cat: None for cat in LOCKED_CATEGORIES})
    out["unexplained"] = float(sums["unexplained"]) if "unexplained" in sums.index else None
    return out


def _energy(items: List[dict]) -> dict:
    closure = pd.concat([d["closure"] for d in items], ignore_index=True)
    scored = closure[closure["skipped_reason"].isna()]
    e_expected = float(sum(d["e_expected_kwh"] for d in items))
    # E_actual dari Closure (L_total yang benar-benar dinilai), bukan terminal
    # waterfall: terminal meleset bila sebuah kategori terklaim tetapi tidak
    # ada di attribution_order.
    l_total = float(scored["l_total_kwh"].sum())
    return {
        "e_expected_kwh": e_expected,
        "l_total_kwh": l_total,
        "e_actual_kwh": e_expected - l_total,
        "n_scored": int(len(scored)),
        "n_skipped": int(len(closure) - len(scored)),
    }


def build_monthly(days: List[dict], attribution_order: List[str]) -> Dict[str, dict]:
    """Rekap per bulan kalender: energi, total kategori, waterfall, Pareto."""
    by_month: Dict[str, List[dict]] = {}
    for day in days:
        by_month.setdefault(day["date"].strftime("%Y-%m"), []).append(day)
    out: Dict[str, dict] = {}
    for month, items in sorted(by_month.items()):
        energy = _energy(items)
        totals = _totals(pd.concat([d["per_string"] for d in items], ignore_index=True))
        out[month] = {
            "n_days": len(items),
            **energy,
            "totals": totals,
            "waterfall": build_waterfall_table(
                totals, attribution_order, e_expected_kwh=energy["e_expected_kwh"],
            ),
            "pareto": build_pareto_table(totals),
        }
    return out


def _summary_row(label_key: str, label, energy: dict, totals: dict) -> dict:
    row = {label_key: label, **energy}
    for cat in CLAIMABLE_CATEGORIES + ["unexplained"]:
        value = totals.get(cat)
        row[f"{cat}_kwh"] = _NAN if value is None else value
    unexplained = totals.get("unexplained")
    row["unexplained_pct_of_loss"] = (
        unexplained / energy["l_total_kwh"] * 100.0
        if unexplained is not None and energy["l_total_kwh"] > 0 else _NAN
    )
    return row


def build_monthly_summary(months: Dict[str, dict]) -> pd.DataFrame:
    rows = []
    for month, m in months.items():
        energy = {k: m[k] for k in (
            "e_expected_kwh", "l_total_kwh", "e_actual_kwh", "n_scored", "n_skipped",
        )}
        row = _summary_row("month", month, energy, m["totals"])
        row["n_days"] = m["n_days"]
        rows.append(row)
    return pd.DataFrame(rows)


def build_daily_summary(days: List[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        _summary_row("date", d["date"], _energy([d]), _totals(d["per_string"]))
        for d in days
    ])


def build_daily_calib(days: List[dict]) -> pd.DataFrame:
    """measured_ratio per WB per hari; workbook tanpa kolom itu dilewati."""
    frames = [
        d["calib"].assign(date=d["date"])
        for d in days
        if d["calib"] is not None and "measured_ratio" in d["calib"].columns
    ]
    if not frames:
        return pd.DataFrame(columns=["date", "wb_id", "measured_ratio"])
    out = pd.concat(frames, ignore_index=True)
    return out[["date"] + [c for c in out.columns if c != "date"]]


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Rekap bulanan M2f dari workbook harian m2f_loss_attribution_YYYYMMDD.xlsx.",
    )
    parser.add_argument("--input-dir", required=True)
    parser.add_argument(
        "--output", default=None,
        help="Default: <input-dir>/rekap_m2f_bulanan.xlsx",
    )
    parser.add_argument(
        "--config", default="config/m2_config.yaml",
        help="Sumber m2f.attribution_order untuk urutan waterfall.",
    )
    args = parser.parse_args(argv)

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from pv_pipeline.m2_config import load_m2_config
    from pv_pipeline.m2f.plots import build_loss_waterfall_figure, build_pareto_figure

    order = list((load_m2_config(args.config).get("m2f") or {}).get("attribution_order") or [])
    found = discover_m2f_xlsx(args.input_dir)
    ignored = [
        name for name in sorted(os.listdir(args.input_dir))
        if _M2F_XLSX_RE.match(name) and not _DAILY_XLSX_RE.match(name)
    ]
    if ignored:
        print(f"[rekap_m2f] dilewati (bukan berkas harian): {ignored}")
    if not found:
        raise SystemExit(
            f"[rekap_m2f] tidak ada m2f_loss_attribution_YYYYMMDD.xlsx di {args.input_dir}"
        )

    days = [load_day(path) for _, path in found]
    months = build_monthly(days, order)
    out_path = args.output or os.path.join(args.input_dir, "rekap_m2f_bulanan.xlsx")
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)

    with pd.ExcelWriter(out_path) as writer:
        build_monthly_summary(months).to_excel(writer, sheet_name="Bulanan", index=False)
        for month, m in months.items():
            m["waterfall"].to_excel(writer, sheet_name=f"Waterfall_{month}", index=False)
            m["pareto"].to_excel(writer, sheet_name=f"Pareto_{month}", index=False)
        build_daily_summary(days).to_excel(writer, sheet_name="Harian", index=False)
        build_daily_calib(days).to_excel(writer, sheet_name="KalibrasiHarian", index=False)

    for month, m in months.items():
        for kind, fig in (
            ("waterfall", build_loss_waterfall_figure(
                m["waterfall"], scope="site", period_label=month)),
            ("pareto", build_pareto_figure(
                m["pareto"], scope="site", period_label=month)),
        ):
            fig.savefig(os.path.join(out_dir, f"m2f_{kind}_{month}.png"), dpi=150)
            plt.close(fig)

    print(f"[rekap_m2f] {len(days)} hari, {len(months)} bulan -> {out_path}")
    for month, m in months.items():
        print(
            f"  {month}: {m['n_days']} hari, E_expected {m['e_expected_kwh']:,.0f} kWh, "
            f"L_total {m['l_total_kwh']:,.0f} kWh, string-hari di-skip {m['n_skipped']}"
        )


if __name__ == "__main__":
    main()
