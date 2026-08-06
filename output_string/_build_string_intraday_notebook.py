"""Builder String_Intraday_Diagnostic.ipynb.

Notebook memisahkan SOILING dari SHADING pada string bermasalah, dengan
membaca CSV baseline 5-menit LANGSUNG dari Drive ter-mount -- tanpa
mengunduh atau menyalin data mentah.

Edit builder ini lalu jalankan:
    python output_string/_build_string_intraday_notebook.py
"""
from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).parent / "String_Intraday_Diagnostic.ipynb"

MD_INTRO = '''# Diagnostik Intraday per String -- Soiling vs Shading

Notebook ini menjawab satu pertanyaan: **string yang jelek itu kotor, atau
tertutup bayangan?** Keduanya terlihat sama pada data harian, tapi sangat
berbeda pada data 5-menit.

## Prinsip

Daya tiap string dibagi **median tetangga se-inverter pada timestamp yang
sama**. Irradiance dan suhu tercoret karena semua string satu inverter
mengalaminya bersamaan. Yang tersisa adalah performa relatif murni.

Lalu lihat **bentuk kurvanya sepanjang hari**:

```
soiling  -> rugi proporsional; rasio DATAR dari pagi sampai sore
shading  -> rugi terkonsentrasi di jam tertentu
```

**Bukti pemutusnya:** kalau rasio sebuah string **melampaui 1,0** di jam
mana pun, panelnya SEHAT. Panel kotor atau rusak tidak mungkin mengungguli
tetangganya yang bersih. Berarti masalahnya halangan pada jam lain, dan
membersihkannya tidak akan menolong.

## Kenapa notebook ini perlu

`M2aShading` bekerja di level **inverter agregat** -- satu-dua string
ternaungi di antara 24-28 string sehat tidak menggerakkan CV agregat, jadi
lolos dari deteksi. Notebook ini menutup celah itu di level string.

## Uji hujan

Hujan mencuci string target DAN tetangganya. Kalau defisitnya debu
non-seragam, yang lebih kotor punya lebih banyak yang bisa dipulihkan
sehingga **rasio naik**. Rasio yang tidak bergerak setelah hujan lebat =
bukan debu.

## Output

Workbook empat sheet di folder outputs Drive:

- `Klasifikasi` -- satu baris per string + kategori (SHADING_PULIH,
  SHADING_PAGI, SHADING_SORE, UNIFORM, CAMPURAN, DEAD_OR_OFFLINE)
- `Profil_Jam` -- matriks rasio per jam, untuk plotting
- `Uji_Hujan` -- delta rasio sebelum vs sesudah tiap kejadian hujan
- `Metadata` -- ambang dan cakupan data

Jalankan Cell 1 sampai Cell 7 berurutan. Edit hanya nilai di **Cell 2**.
'''

CODE_SETUP = '''# Cell 1 - Mount Drive + siapkan repo
from pathlib import Path
import os, sys

try:
    from google.colab import drive
    drive.mount("/content/drive")
    IN_COLAB = True
except ImportError:
    IN_COLAB = False
    print("Bukan Colab -- pakai path lokal.")

def find_repo_root(start=None):
    path = Path(start or os.getcwd()).resolve()
    for candidate in [path, *path.parents]:
        if (candidate / "pv_pipeline").is_dir() and (candidate / "config").is_dir():
            return candidate
    raise RuntimeError("Repo root tidak ditemukan.")

REPO_DIR = find_repo_root(
    "/content/drive/MyDrive/Cek PV String" if IN_COLAB else None
)
os.chdir(REPO_DIR)
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))
print("REPO_DIR:", REPO_DIR)
'''

CODE_CONFIG = '''# Cell 2 - Konfigurasi (edit nilai di sini)

# Folder baseline di Drive: berisi CSV harian {YYYY-MM-DD}.csv per bulan.
# Data dibaca LANGSUNG dari sini -- tidak diunduh, tidak disalin.
BASELINE_DIR = "/content/drive/MyDrive/Cek PV String/baseline"
BULAN = ["2026-06"]          # daftar subfolder bulan yang mau dianalisis

# Folder output di Drive.
OUTPUT_DIR = "/content/drive/MyDrive/Cek PV String/outputs"

# Inverter yang dianalisis. Kosongkan ([]) untuk SELURUH inverter --
# jauh lebih lambat dan boros RAM; isi daftar kalau sudah punya tersangka.
INVERTER_IDS = [
    "WB03-INV06", "WB03-INV09", "WB04-INV02", "WB04-INV17",
    "WB05-INV03", "WB05-INV04", "WB05-INV05", "WB05-INV06",
    "WB05-INV10", "WB05-INV11", "WB07-INV04", "WB07-INV07",
    "WB07-INV08", "WB08-INV15", "WB09-INV08", "WB09-INV20",
    "WB10-INV03",
]

# Kejadian hujan untuk uji pemulihan. Ambil tanggalnya dari
# precipitation_daily_plts_ikn.csv (output run soiling) atau data hujan
# harian. Pilih hari hujan >= 5 mm, lalu 3-4 hari kering sebelum & sesudah.
RAIN_EVENTS = [
    {"nama": "hujan 10-11 Jun",
     "before": ("2026-06-06", "2026-06-09"),
     "after":  ("2026-06-12", "2026-06-15")},
    {"nama": "hujan 16 Jun",
     "before": ("2026-06-13", "2026-06-15"),
     "after":  ("2026-06-17", "2026-06-20")},
]

# Hanya string dengan defisit >= ini yang masuk daftar prioritas Cell 5.
# Klasifikasi tetap dihitung untuk semua string; ambang ini cuma memfilter
# tampilan supaya string sehat (yang wajar sesekali >1,0) tidak ikut.
MIN_DEFICIT_PCT = 10.0

print("Baseline :", BASELINE_DIR)
print("Bulan    :", BULAN)
print("Inverter :", len(INVERTER_IDS) or "SEMUA")
'''

CODE_COLLECT = '''# Cell 3 - Kumpulkan path CSV dari Drive (tanpa menyalin apa pun)
from pathlib import Path

CSV_PATHS = []
for bulan in BULAN:
    folder = Path(BASELINE_DIR) / bulan
    if not folder.is_dir():
        print(f"  LEWATI {folder} -- tidak ada")
        continue
    found = sorted(folder.glob("*.csv"))
    CSV_PATHS.extend(found)
    print(f"  {bulan}: {len(found)} file")

assert CSV_PATHS, "Tidak ada CSV baseline ditemukan; cek BASELINE_DIR/BULAN."
total_mb = sum(p.stat().st_size for p in CSV_PATHS) / 1e6
print(f"\\nTOTAL {len(CSV_PATHS)} file ({total_mb:,.0f} MB) -- dibaca langsung dari Drive")
'''

CODE_RUN = '''# Cell 4 - Jalankan diagnostik
from pv_pipeline.core import load_empty_pv_map
from pv_pipeline.string_intraday_diagnostic import build_intraday_diagnostic

# Slot PV kosong by design tidak boleh muncul sebagai string bermasalah.
EMPTY_PV_MAP = load_empty_pv_map({}, base_dir=str(REPO_DIR))
print(f"empty_pv_map: {len(EMPTY_PV_MAP)} inverter punya slot kosong terdaftar")

# Panjang kabel DC per string berbeda jauh (as-built 11-202 m, vdrop
# 0,15-2,79%). Rugi resistif memberi defisit RATA sepanjang hari -- bentuk
# yang sama persis dengan soiling -- jadi kolom vdrop dipakai membaca
# kategori UNIFORM sebelum regu cuci dikirim.
CABLE_XLS = REPO_DIR / "raw data input" / "List of DC Cables 0411.xls"
CABLE_METRICS = None
if CABLE_XLS.exists():
    from pv_pipeline.m2a.cleaning_report import build_cable_metrics, load_dc_cable_map
    CABLE_METRICS = build_cable_metrics(load_dc_cable_map(str(CABLE_XLS)))
    print(f"cable metrics: {len(CABLE_METRICS)} string punya panjang/vdrop kabel")
else:
    print(f"cable metrics: {CABLE_XLS.name} tidak ada -> kolom vdrop kosong")

# Setiap inverter membandingkan string yang menghadap arah berbeda: sebaran
# cross-slope DALAM SATU inverter bermedian 21,7 derajat, dan 100% inverter
# terdampak. Sebagian SHADING_PAGI/SORE karena itu geometri murni.
# ``ampm_residual`` memisahkannya -- lihat Cell 5.
GEOM_CSV = REPO_DIR / "config" / "string_geometry.csv"
STRING_GEOMETRY = None
if GEOM_CSV.exists():
    import pandas as pd
    STRING_GEOMETRY = pd.read_csv(GEOM_CSV)
    print(f"geometri: {STRING_GEOMETRY['cross_slope_deg'].notna().sum()} string "
          f"punya cross-slope terukur")
else:
    print(f"geometri: {GEOM_CSV.name} tidak ada -> kolom cross-slope kosong")

REPORT = build_intraday_diagnostic(
    CSV_PATHS,
    inverter_ids=INVERTER_IDS or None,
    empty_pv_map=EMPTY_PV_MAP,
    rain_events=RAIN_EVENTS,
    cable_metrics=CABLE_METRICS,
    string_geometry=STRING_GEOMETRY,
)
print()
print(REPORT.summary())
'''

CODE_PRIORITY = '''# Cell 5 - Daftar prioritas + cara membacanya
try:
    from IPython.display import display
except ImportError:
    display = print

KLAS = REPORT.classification
KANDIDAT = KLAS[KLAS["deficit_pct"] >= MIN_DEFICIT_PCT].copy()
print(f"{len(KANDIDAT)} string dgn defisit >= {MIN_DEFICIT_PCT}% "
      f"(dari {len(KLAS)} yang dianalisis)\\n")

display(KANDIDAT[[
    "pv_string", "deficit_pct", "ratio_range", "ratio_max_hourly",
    "jam_terburuk", "jam_terbaik", "dropout_share_pct",
    "cross_slope_deg", "expected_ampm_asym", "ampm_residual", "kategori",
]].head(40))

print("\\nSebelum mengurutkan kerja lapangan, baca ampm_residual:")
print("  asimetri besar + residual ~0 -> GEOMETRI. Kemiringan tanahnya sendiri")
print("                  yang membuat string ini beda dari tetangga se-inverter.")
print("                  Tidak perlu dikunjungi; tidak ada yang bisa dipangkas.")
print("  residual besar  -> OBSTRUKSI nyata. Ini yang didatangi lebih dulu.")
print("  NA              -> string tanpa koordinat (WB01/WB02, fit bidang buruk,")
print("                  atau label DXF muncul di dua tempat). Bukan 'datar'.")
print("  Kolom ini BUKTI, bukan koreksi: deficit_pct dan ratio TIDAK diubah.")

print("\\nCara membaca kategori:")
print("  SHADING_PULIH   rasio >1,0 di jam lain -> panel SEHAT, ada halangan.")
print("                  Tindakan: pangkas vegetasi / pindahkan objek.")
print("  SHADING_PAGI    defisit terkonsentrasi pagi -> halangan sisi timur.")
print("  SHADING_SORE    mati dini / defisit sore -> halangan sisi barat.")
print("  UNIFORM         datar sepanjang hari -> SATU-SATUNYA kandidat soiling")
print("                  atau mismatch modul. Uji: bersihkan, ukur 7 hari.")
print("  CAMPURAN        dua mekanisme -> perlu cek lapangan.")
print("  DEAD_OR_OFFLINE bukan urusan cleaning -> jalur perbaikan (M2e).")
'''

CODE_RAIN = '''# Cell 6 - Uji hujan: benarkah ini debu?
if REPORT.rain.empty:
    print("Tidak ada RAIN_EVENTS; lewati.")
else:
    delta = REPORT.rain.groupby("pv_string")["delta_pp"].mean()
    kand = delta.reindex(KANDIDAT["pv_string"]).dropna()
    print("Perubahan rasio siang setelah hujan (poin persen, + = pulih):\\n")
    print(f"  kandidat defisit tinggi  : {kand.mean():+.2f} pp "
          f"(n={len(kand)}, pulih >3pp: {(kand > 3).sum()})")
    print(f"  seluruh string dianalisis: {delta.mean():+.2f} pp (n={len(delta)})")
    print()
    if kand.mean() < 1.0 and (kand > 3).sum() <= max(1, len(kand) // 10):
        print("  KESIMPULAN: hujan tidak memulihkan -> defisitnya BUKAN debu.")
        print("  Cleaning tidak akan menyelesaikan masalah ini.")
    else:
        print("  KESIMPULAN: ada pemulihan setelah hujan -> komponen soiling nyata.")
'''

CODE_SAVE = '''# Cell 7 - Simpan workbook ke Drive + plot profil jam
from pathlib import Path

meta = REPORT.metadata.set_index("key")["value"]
tag = f"{str(meta['tanggal_awal']).replace('-', '')}_" \\
      f"{str(meta['tanggal_akhir']).replace('-', '')}"
out_path = Path(OUTPUT_DIR) / f"string_intraday_diagnostic_{tag}.xlsx"
REPORT.to_excel(out_path)
print("Workbook:", out_path)

try:
    import matplotlib.pyplot as plt
    top = KANDIDAT.head(8)["pv_string"].tolist()
    prof = REPORT.profile.loc[[k for k in top if k in REPORT.profile.index]]
    if not prof.empty:
        fig, ax = plt.subplots(figsize=(12, 5))
        for key, row in prof.iterrows():
            ax.plot(row.index, row.values, marker="o", label=key)
        ax.axhline(1.0, color="0.4", lw=1, ls="--")
        ax.set_xlabel("jam")
        ax.set_ylabel("rasio vs tetangga se-inverter")
        ax.set_title("Profil harian -- menyentuh garis putus = panel sehat, "
                     "halangan di jam lain")
        ax.legend(fontsize=8, ncol=2)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        png = Path(OUTPUT_DIR) / f"string_intraday_profil_{tag}.png"
        fig.savefig(png, dpi=150)
        plt.show()
        print("PNG:", png)
except ImportError:
    print("matplotlib tidak tersedia; lewati plot.")
'''

CELLS = [
    ("markdown", MD_INTRO),
    ("code", CODE_SETUP),
    ("code", CODE_CONFIG),
    ("code", CODE_COLLECT),
    ("code", CODE_RUN),
    ("code", CODE_PRIORITY),
    ("code", CODE_RAIN),
    ("code", CODE_SAVE),
]


def _cell(kind: str, source: str, index: int) -> dict:
    cell = {
        "cell_type": kind,
        "id": f"string-intraday-{index:02d}",
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }
    if kind == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def build(out: Path = OUT) -> Path:
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    notebook = {
        "cells": [
            _cell(kind, source, index)
            for index, (kind, source) in enumerate(CELLS)
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    target.write_text(
        json.dumps(notebook, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {target} ({len(CELLS)} cells)")
    return target


if __name__ == "__main__":
    build()
