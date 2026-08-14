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

# Repo DIBACA dari Drive, tidak di-clone dan tidak di-pull. Kalau salinan di
# Drive tertinggal, perubahan terbaru diam-diam tidak berlaku dan hasilnya
# tetap terlihat wajar -- kegagalan paling mahal di alur ini. Cetak versinya.
import subprocess
_v = subprocess.run(["git", "-C", str(REPO_DIR), "log", "--oneline", "-1"],
                    capture_output=True, text=True)
print("versi repo:", (_v.stdout.strip() or _v.stderr.strip()
                      or "(bukan git repo -- salinan manual?)"))
'''

CODE_CONFIG = '''# Cell 2 - Konfigurasi (edit nilai di sini)

# Folder baseline di Drive: berisi CSV harian {YYYY-MM-DD}.csv per bulan.
# Data dibaca LANGSUNG dari sini -- tidak diunduh, tidak disalin.
BASELINE_DIR = "/content/drive/MyDrive/Cek PV String/baseline"
BULAN = ["2026-03"]              # daftar subfolder bulan yang mau dianalisis
# Musim KETIGA. Dua yang sudah ada duduk di doy 166 (Jun 2026) dan 335
# (Nov-Des 2025) -- dua titik ekstrem. Ambang SEASONAL_REL_RANGE_MAX yang
# disetel pada dua titik tidak bisa dibedakan dari garis lurus; titik ketiga
# di TENGAH-lah yang mengujinya. Maret 2026 duduk di doy 76, berjarak 90 dari
# jendela terdekat, dan punya 25 hari -- terbaik dari seluruh inventaris Drive
# (Mar 2025 berjarak 91 tapi hanya 4 hari, tidak terpakai).

# Folder output di Drive.
OUTPUT_DIR = "/content/drive/MyDrive/Cek PV String/outputs"

# Inverter yang dianalisis. Kosongkan ([]) untuk SELURUH inverter --
# jauh lebih lambat dan boros RAM; isi daftar kalau sudah punya tersangka.
#
# Phase One (WB01/WB02) BELUM PERNAH sekali pun masuk run diagnostik. Sebabnya
# bukan data dan bukan pemuat -- keduanya bekerja: daftar tersangka di bawah
# terbawa terus dari investigasi lama tanpa ditinjau ulang, lalu diteruskan
# sebagai inverter_ids= sehingga 50 inverter Phase One tersaring diam-diam.
#
# Rasio saudara dihitung per-inverter, jadi penambahan ini ADITIF: ke-431
# string dari 17 tersangka wajib tereproduksi IDENTIK. Jadikan itu kontrol --
# kalau angkanya bergeser, yang salah perubahan ini, bukan hasilnya.
TERSANGKA_WB03_10 = [
    "WB03-INV06", "WB03-INV09", "WB04-INV02", "WB04-INV17",
    "WB05-INV03", "WB05-INV04", "WB05-INV05", "WB05-INV06",
    "WB05-INV10", "WB05-INV11", "WB07-INV04", "WB07-INV07",
    "WB07-INV08", "WB08-INV15", "WB09-INV08", "WB09-INV20",
    "WB10-INV03",
]
PHASE_ONE = [f"WB{wb:02d}-INV{nomor:02d}"
             for wb in (1, 2) for nomor in range(1, 26)]
# Bukan tersangka, melainkan UJI atas koreksi. Penomoran ST WB06-INV06 baru
# dibetulkan -- label gambar 1, 3-24 padahal as-built dan survei EL sama-sama
# bilang 1-23 -- dan koreksi itu belum pernah teruji pada data nyata karena
# WB06 tidak pernah masuk run mana pun. 23 string; tanpa ini perbaikannya
# hanya terbukti di tes, tidak di lapangan.
VERIFIKASI_KOREKSI = ["WB06-INV06"]
INVERTER_IDS = TERSANGKA_WB03_10 + VERIFIKASI_KOREKSI + PHASE_ONE

# Kejadian hujan untuk uji pemulihan. Ambil tanggalnya dari
# precipitation_daily_plts_ikn.csv (output run soiling) atau data hujan
# harian. Pilih hari hujan >= 5 mm, lalu 3-4 hari kering sebelum & sesudah.
#
# WAJIB bergerak bersama BULAN. Jendela yang tidak beririsan dengan data
# TIDAK menimbulkan galat -- rain_recovery() mengembalikan tabel kosong dan
# Cell 6 mencetak "Tidak ada RAIN_EVENTS", seolah-olah tidak ada yang diisi.
#
# Set di bawah untuk BULAN = 2026-03. Maret peralihan: 64% hari di bawah
# 5 mm, di antara Nov-Des (45-57%) dan Juni (87%).
#
# TUJUAN RUN INI BUKAN UJI HUJAN, melainkan musim KETIGA untuk validasi
# geometri. Jendela "sesudah" di Maret pendek-pendek (1 hari) karena hujan
# datang beruntun, jadi perlakukan Cell 6 sebagai pelengkap, bukan putusan.
# Vonis soiling tetap dari set Juni.
RAIN_EVENTS = [
    {"nama": "hujan 4 Mar (42,5 mm)",
     "before": ("2026-03-01", "2026-03-03"),
     "after":  ("2026-03-05", "2026-03-05")},
    {"nama": "hujan 14-16 Mar (67,6 mm)",
     "before": ("2026-03-10", "2026-03-13"),
     "after":  ("2026-03-17", "2026-03-17")},
    {"nama": "hujan 23 Mar (16,3 mm)",
     "before": ("2026-03-19", "2026-03-22"),
     "after":  ("2026-03-24", "2026-03-24")},
]
# Set untuk BULAN = 2026-06 (musim kering -- uji soiling yang sahih):
#     {"nama": "hujan 10-11 Jun", "before": ("2026-06-06", "2026-06-09"),
#      "after": ("2026-06-12", "2026-06-15")},
#     {"nama": "hujan 16 Jun",    "before": ("2026-06-13", "2026-06-15"),
#      "after": ("2026-06-17", "2026-06-20")},
# Set untuk BULAN = 2025-11/2025-12 (puncak musim hujan -- delta ~0 di sana
# BUKAN bukti "bukan debu", melainkan bukti tidak ada yang bisa dipulihkan):
#     {"nama": "hujan 29-30 Nov", "before": ("2025-11-24", "2025-11-28"),
#      "after": ("2025-12-01", "2025-12-02")},
#     {"nama": "hujan 10 Des",    "before": ("2025-12-08", "2025-12-09"),
#      "after": ("2025-12-11", "2025-12-12")},
#     {"nama": "hujan 27 Des",    "before": ("2025-12-25", "2025-12-26"),
#      "after": ("2025-12-28", "2025-12-29")},

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
    # Penanda versi kedua, kali ini pada DATA. Penempatan keempat inverter tepi
    # utara Phase One dibantah tiga sumber bebas, jadi kolom bidangnya sengaja
    # dikosongkan. Masih terisi = salinan repo di Drive tertinggal.
    _dibantah = STRING_GEOMETRY["inverter_id"].isin(
        ["WB02-INV01", "WB02-INV02", "WB02-INV04", "WB02-INV06"])
    _kosong = int(STRING_GEOMETRY.loc[_dibantah, "cross_slope_deg"].isna().sum())
    print(f"          {_kosong}/{int(_dibantah.sum())} string tepi utara "
          f"Phase One dikosongkan (harus 72/72)")
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
print()
print("string per blok -- Phase One (WB01/WB02) dan WB06 harus ada:")
print(REPORT.classification["inverter_id"].str[:4]
      .value_counts().sort_index().to_string())
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
from pv_pipeline.string_intraday_diagnostic import (
    DEFAULT_RAIN_RECOVER_PP,
    rain_recovery_verdict,
)

if REPORT.rain.empty:
    print("Tidak ada RAIN_EVENTS; lewati.")
    print("Kalau RAIN_EVENTS SUDAH diisi, penyebabnya jendela before/after")
    print("tidak beririsan dengan BULAN -- keduanya wajib bergerak bersama.")
else:
    V = rain_recovery_verdict(REPORT.rain, KANDIDAT["pv_string"])
    delta = REPORT.rain.groupby("pv_string")["delta_pp"].mean()
    print("Perubahan rasio siang setelah hujan (poin persen, + = pulih):\\n")
    print(f"  kandidat defisit tinggi  : median {V['median_pp']:+.2f} pp | "
          f"rata-rata {V['mean_pp']:+.2f} pp | n={V['n_kandidat']} | "
          f"pulih >={DEFAULT_RAIN_RECOVER_PP:.0f}pp: {V['n_pulih']}")
    print(f"  seluruh string dianalisis: median {delta.median():+.2f} pp | "
          f"rata-rata {delta.mean():+.2f} pp | n={len(delta)}")

    # Vonisnya memakai MEDIAN. Sebaran ini menjulur -- sebagian besar string
    # tidak bergerak, beberapa bergerak jauh -- jadi rata-rata bisa positif
    # sementara kandidat KHAS tidak pulih sama sekali. Itu bukan hipotesis:
    # run 14 Agustus mengumumkan "soiling nyata" atas rata-rata +1,21 pp
    # padahal mediannya -0,26 dan 5 dari 8 pemulih ada di satu inverter.
    if V["mean_pp"] - V["median_pp"] > 1.0:
        print()
        print(f"  CATATAN: rata-rata melampaui median "
              f"{V['mean_pp'] - V['median_pp']:.2f} pp.")
        print("  Sebarannya menjulur; rata-rata di sini menggambarkan ekornya,")
        print("  bukan kandidat yang khas. Vonis di bawah memakai median.")

    print()
    if V["putusan"] == "TIDAK_PULIH":
        print("  VONIS: hujan tidak memulihkan -> defisitnya BUKAN debu.")
        print("  Cleaning tidak akan menyelesaikan masalah ini.")
    elif V["putusan"] == "SOILING_MENYELURUH":
        print("  VONIS: kandidat KHAS pulih -> komponen soiling menyeluruh.")
        print("  Pembersihan se-situs terjustifikasi.")
    else:
        print("  VONIS: hanya ekornya yang pulih -> soiling LOKAL, bukan se-situs.")
        print(f"  {V['n_pulih']} dari {V['n_kandidat']} kandidat pulih; sisanya tidak.")
        if V["terkonsentrasi"]:
            print(f"  Terkumpul di {V['inverter_dominan']}: "
                  f"{V['bagian_dominan']:.0%} dari yang pulih.")
            print(f"  TINDAKAN: bersihkan {V['inverter_dominan']}, "
                  f"bukan seluruh situs.")
            print("  Verifikasi: ukur ulang 7 hari setelahnya.")
        else:
            print("  Pemulihnya tersebar, tidak menumpuk di satu inverter.")
            print("  Perlakukan sebagai daftar string, bukan program cleaning.")
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
