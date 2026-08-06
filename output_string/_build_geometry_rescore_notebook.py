"""Builder String_Geometry_Rescore.ipynb.

Notebook menilai ULANG workbook diagnostik yang sudah ada dengan bukti
kemiringan tanah -- tanpa membaca ulang CSV baseline. Yang berubah pada
penilaian ulang hanyalah bukti geometrisnya; ``pagi`` dan ``sore`` terukur
tidak bergerak sedikit pun.

Edit builder ini lalu jalankan:
    python output_string/_build_geometry_rescore_notebook.py
"""
from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).parent / "String_Geometry_Rescore.ipynb"

MD_INTRO = '''# Penilaian Ulang Geometris -- SHADING_PAGI/SORE

Label `SHADING_PAGI` dan `SHADING_SORE` lahir dari satu uji: selisih rasio
pagi vs sore melewati `DEFAULT_AMPM_GAP` (0,12). Uji itu mengandaikan semua
string satu inverter menghadap arah yang sama.

**Andaian itu tidak berlaku di WB03-WB10.** Sebaran `cross_slope_deg` DALAM
SATU inverter bermedian 21,7 derajat; 100% inverter terdampak. Karena
`ratio` dihitung terhadap median tetangga se-inverter, setiap perbandingan
membandingkan modul yang menghadap arah berbeda -- dan sebagian selisih
pagi-sore itu murni geometri tanah, bukan halangan yang bisa dipangkas.

## Yang dihitung di sini

```
expected_ampm_asym[i] = asym(cross_slope[i]) - median  asym(cross_slope[j])
                                                    j se-inverter
ampm_residual[i]      = (pagi[i] - sore[i]) - expected_ampm_asym[i]
```

Acuannya **median se-inverter**, bukan meja datar -- sama seperti `ratio`.
Acuan meja datar akan meninggalkan offset sistematis per inverter.

## Cara membacanya

| Residual | Artinya | Tindakan |
|---|---|---|
| di bawah 0,12 | asimetrinya dijelaskan kemiringan tanah | **tidak perlu dikunjungi** |
| 0,12 ke atas | masih ada asimetri tak terjelaskan | obstruksi nyata, datangi |
| `NA` | string tanpa koordinat tepercaya | perlu cek lapangan biasa |

Ambang yang dipakai adalah `DEFAULT_AMPM_GAP` yang sama -- ambang yang
memunculkan labelnya. Kalau sisa setelah geometri dikeluarkan tidak lagi
melewati ambang itu, labelnya memang produk geometri.

**Kolom ini bukti, bukan koreksi.** `ratio`, `deficit_pct`, dan `kategori`
tidak disentuh. Mengoreksinya butuh model POA per string per timestamp.

## Validasi silang (opsional, Cell 5)

Isi `WORKBOOK_MUSIM_LAIN` dengan workbook diagnostik dari rentang tanggal
musim lain, lalu Cell 5 mengadu **dua penilai yang saling bebas**:

- `seasonal_discriminator` hanya membaca `pagi`/`sore` terukur, buta terhadap
  cross-slope;
- `ampm_residual` datang dari model pvlib clear-sky yang tidak pernah
  dipaskan ke telemetri.

Kesepakatan keduanya karena itu adalah validasi, bukan tautologi. Yang paling
berharga justru saat mereka berbeda: asimetri bertanda tetap dengan drift di
bawah 30% dulu otomatis disebut GEOMETRI -- padahal bangunan permanen di sisi
timur menghasilkan pola yang sama persis.

Jalankan Cell 1 sampai Cell 6 berurutan. Edit hanya nilai di **Cell 2**.
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

# Workbook diagnostik yang mau dinilai ulang -- keluaran
# String_Intraday_Diagnostic.ipynb. Sheet "Klasifikasi" dan "Metadata" dipakai.
WORKBOOK = "/content/drive/MyDrive/Cek PV String/outputs/" \\
           "string_intraday_diagnostic_20260601_20260630.xlsx"

OUTPUT_DIR = "/content/drive/MyDrive/Cek PV String/outputs"

# Workbook dari rentang tanggal musim LAIN, untuk validasi silang di Cell 5.
# Idealnya dekat solstis seberang: makin jauh jaraknya, makin besar beda
# musiman yang harus diprediksi model, jadi makin tajam ujinya. Kosongkan
# ("") untuk melewati validasi.
WORKBOOK_MUSIM_LAIN = ""

# Daftar string yang jadi subjek laporan yang sudah beredar
# (Analisis_20String_Underperform_20260729). Kosongkan ([]) untuk menilai
# ulang SELURUH string di workbook.
FOKUS = [
    "WB03-INV09-PV7", "WB04-INV02-PV26", "WB07-INV08-PV13", "WB07-INV04-PV20",
    "WB10-INV03-PV24", "WB09-INV08-PV2", "WB08-INV15-PV20", "WB03-INV06-PV13",
    "WB05-INV06-PV22", "WB05-INV03-PV1", "WB07-INV07-PV10", "WB05-INV04-PV20",
    "WB05-INV03-PV18", "WB05-INV05-PV5", "WB05-INV06-PV10", "WB05-INV10-PV28",
    "WB09-INV20-PV1", "WB05-INV11-PV24", "WB04-INV17-PV5", "WB04-INV17-PV1",
]

print("Workbook:", WORKBOOK)
print("Fokus   :", len(FOKUS) or "SEMUA", "string")
'''

CODE_ATTACH = '''# Cell 3 - Pasang bukti geometris pada klasifikasi lama
import pandas as pd
from pv_pipeline.string_intraday_diagnostic import attach_geometry_evidence

GEOM = pd.read_csv(REPO_DIR / "config" / "string_geometry.csv")


def muat(path):
    """Workbook diagnostik -> (klasifikasi + bukti geometris, awal, akhir, doy)."""
    xl = pd.ExcelFile(path)
    meta = xl.parse("Metadata").set_index("key")["value"]
    awal = pd.Timestamp(meta["tanggal_awal"])
    akhir = pd.Timestamp(meta["tanggal_akhir"])
    # Asimetri geometris bergeser ~22,5% antar solstis, jadi harapannya dihitung
    # pada pertengahan rentang tanggal yang benar-benar dianalisis workbook itu.
    doy = int((awal + (akhir - awal) / 2).dayofyear)
    return attach_geometry_evidence(xl.parse("Klasifikasi"), GEOM, doy), awal, akhir, doy


KLAS, AWAL, AKHIR, DOY = muat(WORKBOOK)
print(f"Rentang workbook: {AWAL.date()} .. {AKHIR.date()} -> day-of-year {DOY}")

ada = KLAS["cross_slope_deg"].notna().sum()
print(f"{len(KLAS)} string di workbook; cross-slope tepercaya {ada} "
      f"({100 * ada / len(KLAS):.1f}%)")
print("NA berarti: WB01/WB02 (tidak dipetakan), fit bidang buruk, atau label "
      "DXF muncul di dua tempat -- BUKAN 'tanahnya datar'.")
'''

CODE_VERDICT = '''# Cell 4 - Putusan ulang: geometri atau obstruksi
import numpy as np
from pv_pipeline.string_intraday_diagnostic import (
    DEFAULT_AMPM_GAP, DEFAULT_DROPOUT_SHARE,
)

try:
    from IPython.display import display
except ImportError:
    display = print

ARAH_KATEGORI = ("SHADING_PAGI", "SHADING_SORE")

def putusan(row):
    """Uji geometris hanya berwenang atas label yang LAHIR dari asimetri.

    Dua penjaga di depan, keduanya menutup cara yang sama untuk salah:
    menyimpulkan "geometri, tidak perlu dikunjungi" dari residual kecil pada
    string yang labelnya sama sekali bukan produk asimetri pagi-sore.

    1. Kategori non-arah. SHADING_PULIH lahir dari rasio >1,02 dan UNIFORM
       dari profil datar. Cross-slope tidak bisa membuat sebuah string
       MELAMPAUI tetangganya, jadi residual tidak menjelaskan label itu.
    2. Cabang mati dini. SHADING_SORE lahir dari DUA cabang dan cabang mati
       dini (dropout >= 40%) menyala lebih dulu. Cross-slope menggeser bagi
       hasil pagi vs sore; ia tidak bisa membuat sebuah string berhenti
       produksi sementara tetangganya masih jalan.

    Sisanya -- label arah yang memang lahir dari |pagi - sore| >= ambang --
    barulah dinilai residualnya, dengan ambang yang sama yang memunculkannya.
    """
    if row["kategori"] not in ARAH_KATEGORI:
        return "TIDAK_BERLAKU"
    if row["dropout_share_pct"] >= DEFAULT_DROPOUT_SHARE * 100:
        return "MATI_DINI"
    if not np.isfinite(row["cross_slope_deg"]):
        return "DATA_GEOMETRI_TIDAK_ADA"
    return ("GEOMETRI" if abs(row["ampm_residual"]) < DEFAULT_AMPM_GAP
            else "OBSTRUKSI")

KLAS["asym_terukur"] = (KLAS["pagi"] - KLAS["sore"]).round(4)
KLAS["putusan_geometris"] = KLAS.apply(putusan, axis=1)

TARGET = KLAS[KLAS["pv_string"].isin(FOKUS)] if FOKUS else KLAS
TARGET = TARGET.sort_values("ampm_residual", key=abs, ascending=False)

display(TARGET[[
    "pv_string", "deficit_pct", "kategori", "cross_slope_deg",
    "asym_terukur", "expected_ampm_asym", "ampm_residual", "putusan_geometris",
]])

print()
for nama, n in TARGET["putusan_geometris"].value_counts().items():
    print(f"  {nama:<24} {n}")

turun = int((TARGET["putusan_geometris"] == "GEOMETRI").sum())
arah = int(TARGET["kategori"].isin(ARAH_KATEGORI).sum())
print(f"\\n{turun} dari {arah} label arah dijelaskan kemiringan tanah -> turun "
      f"dari daftar kunjungan lapangan.")
print("MATI_DINI      labelnya dari cabang mati dini, bukan asimetri. "
      "Cross-slope tidak")
print("               bisa membuat produksi NOL sementara tetangga jalan. "
      "Tetap didatangi.")
print("TIDAK_BERLAKU  label non-arah (SHADING_PULIH/UNIFORM/CAMPURAN). "
      "Rasio >1,0 dan")
print("               defisit datar juga tidak bisa dihasilkan cross-slope. "
      "Tetap didatangi.")
'''

CODE_VALIDATE = '''# Cell 5 - Validasi silang: prediksi geometris vs perilaku musiman
from pv_pipeline.string_intraday_diagnostic import validate_geometry_seasonally

VALIDASI = None
if not WORKBOOK_MUSIM_LAIN:
    print("WORKBOOK_MUSIM_LAIN kosong -- validasi musiman dilewati.")
    print("Isi di Cell 2 dengan workbook diagnostik dari rentang tanggal musim")
    print("LAIN (idealnya dekat solstis seberang) untuk mengaktifkan sel ini.")
else:
    KLAS2, AWAL2, AKHIR2, DOY2 = muat(WORKBOOK_MUSIM_LAIN)
    print(f"Musim pembanding: {AWAL2.date()} .. {AKHIR2.date()} -> doy {DOY2}")

    VALIDASI = validate_geometry_seasonally({
        str(AWAL.date()): KLAS,
        str(AWAL2.date()): KLAS2,
    })
    display(VALIDASI.head(40))

    print()
    for nama, n in VALIDASI["hasil"].value_counts().items():
        print(f"  {nama:<24} {n}")

    print()
    print("SEPAKAT                 dua metode yang saling BEBAS memberi putusan")
    print("                        sama. Uji musiman hanya membaca pagi/sore")
    print("                        terukur; residual datang dari model pvlib yang")
    print("                        tidak pernah dipaskan ke telemetri -- jadi ini")
    print("                        validasi, bukan tautologi.")
    print("MUSIMAN_TERLALU_LONGGAR uji musiman menyebut GEOMETRI padahal residual")
    print("                        masih besar. Obstruksi permanen memberi asimetri")
    print("                        bertanda tetap yang bergeser pelan -- persis")
    print("                        tanda tangan yang dulu tak bisa dibedakan.")
    print("                        String di sini TETAP didatangi.")
    print("MUSIMAN_TERLALU_KETAT   uji musiman menyebut OBSTRUKSI padahal residual")
    print("                        kecil di kedua musim. Periksa ambang 0,30.")
    print("TIDAK_BERLAKU           tanpa cross-slope tepercaya, atau asimetrinya")
    print("                        tidak melewati ambang sama sekali.")

    drift = VALIDASI["residual_drift"].dropna()
    if len(drift):
        print(f"\\nresidual_drift  median {drift.median():.3f}  "
              f"p90 {drift.quantile(0.9):.3f}")
        print("Ini menguji penskalaan musiman model. Untuk string yang memang")
        print("geometris, residual harus jauh lebih stabil antar musim daripada")
        print("asimetri mentahnya; drift besar menandakan k(hari) meleset.")
'''

CODE_SAVE = '''# Cell 6 - Simpan hasil penilaian ulang
from pathlib import Path

tag = f"{AWAL.strftime('%Y%m%d')}_{AKHIR.strftime('%Y%m%d')}"
out_path = Path(OUTPUT_DIR) / f"geometry_rescore_{tag}.xlsx"
out_path.parent.mkdir(parents=True, exist_ok=True)

with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
    TARGET.to_excel(writer, sheet_name="Putusan_Fokus", index=False)
    KLAS.to_excel(writer, sheet_name="Klasifikasi_Dinilai_Ulang", index=False)
    if VALIDASI is not None:
        VALIDASI.to_excel(writer, sheet_name="Validasi_Musiman", index=False)
    pd.DataFrame([
        ("workbook_sumber", str(WORKBOOK)),
        ("rentang", f"{AWAL.date()} .. {AKHIR.date()}"),
        ("day_of_year", DOY),
        ("acuan_harapan",
         "median se-inverter, sama seperti ratio -- BUKAN meja datar"),
        ("ambang_putusan", f"DEFAULT_AMPM_GAP = {DEFAULT_AMPM_GAP}"),
        ("sifat_kolom",
         "bukti, BUKAN koreksi: ratio/deficit_pct/kategori tidak disentuh"),
    ], columns=["key", "value"]).to_excel(writer, sheet_name="Metadata", index=False)

print("Workbook:", out_path)
'''

CELLS = [
    ("markdown", MD_INTRO),
    ("code", CODE_SETUP),
    ("code", CODE_CONFIG),
    ("code", CODE_ATTACH),
    ("code", CODE_VERDICT),
    ("code", CODE_VALIDATE),
    ("code", CODE_SAVE),
]


def _cell(kind: str, source: str, index: int) -> dict:
    cell = {
        "cell_type": kind,
        "id": f"geometry-rescore-{index:02d}",
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
