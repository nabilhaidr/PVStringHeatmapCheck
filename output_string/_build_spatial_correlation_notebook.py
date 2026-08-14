"""Builder Spatial_Correlation.ipynb.

Menutup separuh Open Question 8 yang tersisa: penempatan MANA yang benar untuk
empat inverter tepi utara Phase One.

Edit builder ini lalu jalankan:
    python output_string/_build_spatial_correlation_notebook.py
"""
from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).parent / "Spatial_Correlation.ipynb"

MD_INTRO = '''# Korelasi spasial -- penempatan mana yang benar?

Open Question 8 baru **setengah** terjawab. Yang sudah terbukti: penempatan DXF
untuk WB02-INV01/02/04/06 **salah** -- di posisi itu keempatnya duduk di lereng
sampai -15,4 derajat, sementara **tidak ada satu pun string Phase One di mana
pun yang melewati 2,67 derajat**. Yang belum diketahui: posisi yang BENAR.
Memindahkannya ke posisi EL sudah diperiksa dan ditolak, karena posisi itu
sudah ditempati baris DXF milik inverter lain.

Notebook ini membawa bukti dari arah yang sama sekali lain: **fisika awan.**

## Gagasannya

Pada hari berawan sebagian, bayangan awan bergerak melintasi larik. String yang
berdekatan secara fisik masuk dan keluar bayangan pada saat yang hampir sama;
yang berjauhan tidak. Jadi setelah sinyal iradians se-situs dibuang, korelasi
sisa antar dua string harus **meluruh terhadap jarak**.

```
penempatan BENAR -> string dekat berkorelasi, jauh tidak -> rho sangat negatif
penempatan SALAH -> hubungannya teracak                  -> rho mendekati nol
```

Yang diukur peringkat (Spearman), bukan bentuk peluruhannya, jadi tidak ada
model yang harus ditebak.

## Tiga cara uji ini menipu, dan bagaimana ketiganya dijaga

**1. Semua string mengikuti matahari.** Tanpa membuang sinyal bersama, setiap
pasangan berkorelasi hampir sempurna dan peluruhan terhadap jarak muncul pada
penempatan APA PUN -- termasuk yang koordinatnya diacak. Karena itu tiap string
dibagi median seluruh string pada timestamp yang sama lebih dulu.

**2. Hari yang salah tidak punya sinyal.** Karena itu ada **kontrol**: skor yang
sama dihitung pada string yang penempatannya TIDAK dibantah. Kalau di sana pun
peluruhannya tidak muncul, instrumennya buta hari itu dan notebook menolak
memilih. Selisih besar di atas kontrol yang lemah adalah tanda derau, bukan
tanda temuan.

**3. Dua skor berdekatan bukan pemenang dan pecundang.** Selisih di bawah ambang
dilaporkan `SERI`.

## Hari yang dipakai

Dipilih oleh `Drive_Probe.ipynb` dari variabilitas iradians dalam-hari:
**2026-06-08, 2026-06-22, 2026-06-20** -- tiga hari paling variabel di Juni yang
tetap melewati lantai kecerahan. Hari cerah stabil tidak berguna di sini karena
tidak memberi kontras antar string sama sekali.

Jalankan Sel 1 sampai Sel 6 berurutan. Edit hanya nilai di **Sel 2**.
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

# .git di Drive bisa tertinggal berbulan-bulan; penanda isi tidak bisa bohong.
try:
    import pv_pipeline.spatial_correlation  # noqa: F401
    print("versi repo (isi): pv_pipeline.spatial_correlation ADA")
except ImportError:
    raise RuntimeError(
        "pv_pipeline/spatial_correlation.py tidak ada di salinan Drive ini. "
        "Sinkronkan ulang repo ke Drive."
    )
'''

CODE_CONFIG = '''# Cell 2 - Konfigurasi (edit nilai di sini)
from pv_pipeline.spatial_correlation import (
    DEFAULT_MIDDAY,
    DEFAULT_MIN_CONTROL_RHO,
    DEFAULT_MIN_MARGIN,
    DEFAULT_MIN_OVERLAP,
)

BASELINE_DIR = "/content/drive/MyDrive/Cek PV String/baseline"

# Hari berawan sebagian, hasil peringkat Drive_Probe.ipynb Sel 5.
# Hari cerah stabil TIDAK berguna di sini: tanpa awan lewat tidak ada kontras
# antar string, dan seluruh skor akan runtuh ke nol untuk kedua kandidat.
HARI = ["2026-06-08", "2026-06-22", "2026-06-20"]

# Empat inverter yang penempatan DXF-nya dibantah.
INVERTER_DIBANTAH = ["WB02-INV01", "WB02-INV02", "WB02-INV04", "WB02-INV06"]

# KONTROL -- penempatannya tidak dibantah, dipakai membuktikan metodenya punya
# daya pisah pada data hari itu. Tetangga se-blok supaya cuacanya sama.
INVERTER_KONTROL = ["WB02-INV03", "WB02-INV05", "WB02-INV07", "WB02-INV08"]

# --- Kandidat penempatan -----------------------------------------------------
# DXF: dari config/string_geometry.csv, kolom north/east. Kolom BIDANG keempat
# inverter ini sudah di-NULL-kan lewat PLACEMENT_DISPUTED, tapi KOORDINATnya
# masih ada -- itulah yang diuji di sini.
#
# EL: dari survei drone. Nama kolomnya TIDAK terekam di kode mana pun, jadi ia
# jadi nilai konfigurasi alih-alih ditebak. Sel 4 akan berhenti dan mencetak
# daftar kolom sebenarnya kalau ketiganya tidak cocok -- betulkan di sini lalu
# jalankan ulang selnya.
EL_CSV = "/content/drive/MyDrive/raw data input/el drone 2025/all.csv"
EL_HEADER_ROW = 32          # header di baris ke-33 (0-indexed 32)
EL_KOL_STRING = "string_id"
EL_KOL_NORTH = "north"
EL_KOL_EAST = "east"

JENDELA = DEFAULT_MIDDAY
MIN_OVERLAP = DEFAULT_MIN_OVERLAP
MIN_KONTROL_RHO = DEFAULT_MIN_CONTROL_RHO
MIN_MARGIN = DEFAULT_MIN_MARGIN

print("Hari      :", HARI)
print("Dibantah  :", INVERTER_DIBANTAH)
print("Kontrol   :", INVERTER_KONTROL)
print(f"Ambang    : kontrol <= {MIN_KONTROL_RHO}, margin >= {MIN_MARGIN}")
'''

CODE_LOAD = '''# Cell 3 - Muat telemetri hari-hari terpilih
import pandas as pd
from pv_pipeline.core import load_empty_pv_map
from pv_pipeline.string_intraday_diagnostic import load_baseline_power_long

CSV_PATHS = []
for hari in HARI:
    bulan = hari[:7]
    p = Path(BASELINE_DIR) / bulan / f"{hari}.csv"
    if p.exists():
        CSV_PATHS.append(p)
    else:
        print(f"  LEWATI {p.name} -- tidak ada")
assert CSV_PATHS, "Tidak ada CSV untuk HARI; cek BASELINE_DIR dan tanggalnya."
print(f"{len(CSV_PATHS)} hari terbaca")

EMPTY_PV_MAP = load_empty_pv_map("config/strings.yaml")
SEMUA_INV = INVERTER_DIBANTAH + INVERTER_KONTROL

LONG = load_baseline_power_long(
    CSV_PATHS, inverter_ids=SEMUA_INV, empty_pv_map=EMPTY_PV_MAP,
)
LONG["pv_string"] = LONG["inverter_id"] + "-" + LONG["pv"]
print(f"{len(LONG):,} baris, {LONG['pv_string'].nunique()} string, "
      f"{LONG['inverter_id'].nunique()} inverter")

_hadir = set(LONG["inverter_id"].unique())
_hilang = [i for i in SEMUA_INV if i not in _hadir]
if _hilang:
    print(f"\\nPERHATIAN -- inverter tidak melapor sama sekali: {_hilang}")
    print("Phase One lewat fiber IconPlus; absen serentak berarti putus tautan,")
    print("bukan inverter mati. Uji ini butuh mereka HADIR -- pilih hari lain.")
'''

CODE_CORR = '''# Cell 4 - Sisa setelah sinyal se-situs, lalu korelasi tiap pasangan
from pv_pipeline.spatial_correlation import (
    coords_from_geometry, pairwise_correlation, residual_after_site_median,
)

WIDE = residual_after_site_median(LONG, midday=JENDELA)
print(f"residual: {WIDE.shape[0]} timestamp x {WIDE.shape[1]} string "
      f"(jendela jam {JENDELA[0]}-{JENDELA[1]})")

PASANGAN = pairwise_correlation(WIDE, min_overlap=MIN_OVERLAP)
print(f"{len(PASANGAN)} pasangan lolos tumpang tindih >= {MIN_OVERLAP} cuplikan")
print(f"korelasi sisa: median {PASANGAN['r'].median():+.3f}, "
      f"p10 {PASANGAN['r'].quantile(0.1):+.3f}, "
      f"p90 {PASANGAN['r'].quantile(0.9):+.3f}")
print()
print("Kalau median korelasi sisa mendekati 1,0 berarti sinyal se-situs BELUM")
print("terbuang dan seluruh hasil di bawah tidak sah. Yang diharapkan sebaran")
print("lebar di sekitar nol -- itulah struktur lokal yang mau diukur.")

# --- Koordinat kandidat -----------------------------------------------------
GEOM = pd.read_csv("config/string_geometry.csv")
KOOR_DXF = coords_from_geometry(GEOM)
print(f"\\nDXF : {len(KOOR_DXF)} string berkoordinat")

EL = pd.read_csv(EL_CSV, header=EL_HEADER_ROW)
_perlu = [EL_KOL_STRING, EL_KOL_NORTH, EL_KOL_EAST]
_tidak_ada = [c for c in _perlu if c not in EL.columns]
if _tidak_ada:
    raise KeyError(
        f"Kolom {_tidak_ada} tidak ada di {EL_CSV}.\\n"
        f"Kolom yang TERSEDIA: {list(EL.columns)}\\n"
        "Betulkan EL_KOL_* di Cell 2 lalu jalankan ulang sel ini."
    )
KOOR_EL = {
    str(r[EL_KOL_STRING]).strip().upper(): (float(r[EL_KOL_NORTH]),
                                            float(r[EL_KOL_EAST]))
    for _, r in EL.iterrows()
    if pd.notna(r[EL_KOL_NORTH]) and pd.notna(r[EL_KOL_EAST])
}
print(f"EL  : {len(KOOR_EL)} string berkoordinat")

_uji = set(PASANGAN["a"]) | set(PASANGAN["b"])
print(f"\\ncakupan pada string yang diuji: DXF {len(_uji & set(KOOR_DXF))}, "
      f"EL {len(_uji & set(KOOR_EL))} dari {len(_uji)}")
print("Cakupan yang timpang membuat kedua skor dihitung atas pasangan berbeda")
print("dan tidak sebanding. Periksa angka ini sebelum membaca vonis.")
'''

CODE_SCORE = '''# Cell 5 - Skor peluruhan: kontrol lebih dulu, baru kandidat
from pv_pipeline.spatial_correlation import decay_score


def _milik(pasangan, himpunan):
    """Pasangan yang KEDUA stringnya milik himpunan inverter tertentu."""
    ia = pasangan["a"].str.rsplit("-", n=1).str[0]
    ib = pasangan["b"].str.rsplit("-", n=1).str[0]
    return pasangan[ia.isin(himpunan) & ib.isin(himpunan)]


P_KONTROL = _milik(PASANGAN, set(INVERTER_KONTROL))
P_DIBANTAH = _milik(PASANGAN, set(INVERTER_DIBANTAH))
print(f"pasangan kontrol : {len(P_KONTROL)}")
print(f"pasangan dibantah: {len(P_DIBANTAH)}")
print()

# Kontrol memakai koordinat DXF karena untuk inverter ini DXF TIDAK dibantah --
# itulah yang membuatnya kontrol: geometri yang sudah diketahui benar.
KONTROL = decay_score(P_KONTROL, KOOR_DXF)
print(f"KONTROL (penempatan diketahui benar): rho {KONTROL['rho']:+.3f}  "
      f"n={KONTROL['n_pasangan']}  jarak median {KONTROL['jarak_median_m']:.0f} m")

SKOR = {
    "DXF": decay_score(P_DIBANTAH, KOOR_DXF),
    "EL": decay_score(P_DIBANTAH, KOOR_EL),
}
for nama, s in SKOR.items():
    print(f"  {nama:4s}: rho {s['rho']:+.3f}  n={s['n_pasangan']}  "
          f"jarak median {s['jarak_median_m']:.0f} m")
'''

CODE_VERDICT = '''# Cell 6 - Vonis
from pv_pipeline.spatial_correlation import verdict_placement

V = verdict_placement(
    KONTROL, SKOR,
    min_control_rho=MIN_KONTROL_RHO, min_margin=MIN_MARGIN,
)
print(f"PUTUSAN: {V['putusan']}")
print()

if V["putusan"] == "TIDAK_SENSITIF":
    print(f"Kontrol hanya mencapai rho {V['kontrol_rho']:+.3f}, tidak melewati")
    print(f"{MIN_KONTROL_RHO}. String yang geometrinya SUDAH diketahui benar pun")
    print("tidak menunjukkan peluruhan terhadap jarak, jadi hari-hari ini tidak")
    print("punya bayangan bergerak yang cukup dan instrumennya buta.")
    print()
    print("Skor kandidat di Cell 5 TIDAK bermakna, sebesar apa pun selisihnya.")
    print("Selisih besar di atas kontrol yang lemah adalah tanda derau.")
    print()
    print("Yang bisa dilakukan: pilih hari lain dari peringkat Drive_Probe Sel 5,")
    print("atau perlebar HARI supaya pasangannya lebih banyak.")
elif V["putusan"] == "SERI":
    print(f"Kontrol sehat (rho {V['kontrol_rho']:+.3f}) -- metodenya bekerja.")
    print(f"Tapi selisih dua kandidat cuma {V['margin']:.3f}, di bawah "
          f"{MIN_MARGIN}.")
    print("Keduanya menjelaskan data sama baiknya; ini bukan pemenang dan")
    print("pecundang. Open Question 8 tetap terbuka.")
else:
    print(f"Kontrol sehat (rho {V['kontrol_rho']:+.3f}) -- metodenya bekerja.")
    print(f"Penempatan yang didukung data: {V['pilihan']}  "
          f"(margin {V['margin']:.3f})")
    print()
    print("Ini bukti dari arah yang BEBAS dari argumen medan yang dipakai")
    print("sebelumnya: yang satu membaca kemiringan tanah, yang ini membaca")
    print("waktu kedatangan bayangan awan. Kesepakatan keduanya kuat.")
    print()
    print("SEBELUM mengubah string_geometry.csv, periksa dulu: posisi tujuan")
    print("mungkin sudah ditempati baris milik inverter lain. Pemindahan yang")
    print("merambat ke luar keempat inverter ini menuntut penurunan ulang")
    print("pemetaan label->posisi untuk seluruh 900 string Phase One.")

print()
print("skor lengkap:", V["skor"])
'''

CELLS = [
    ("markdown", MD_INTRO),
    ("code", CODE_SETUP),
    ("code", CODE_CONFIG),
    ("code", CODE_LOAD),
    ("code", CODE_CORR),
    ("code", CODE_SCORE),
    ("code", CODE_VERDICT),
]


def _cell(kind: str, source: str, index: int) -> dict:
    cell = {
        "cell_type": kind,
        "id": f"spatial-corr-{index:02d}",
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
