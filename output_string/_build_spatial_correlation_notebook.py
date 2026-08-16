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
import os, subprocess, sys

REPO_URL = "https://github.com/nabilhaidr/PVStringHeatmapCheck.git"
DRIVE_ROOT = "/content/drive/MyDrive/Cek PV String"     # DATA, bukan kode

try:
    from google.colab import drive
    drive.mount("/content/drive")
    IN_COLAB = True
except ImportError:
    IN_COLAB = False
    print("Bukan Colab -- pakai repo lokal.")

def find_repo_root(start=None):
    path = Path(start or os.getcwd()).resolve()
    for candidate in [path, *path.parents]:
        if (candidate / "pv_pipeline").is_dir() and (candidate / "config").is_dir():
            return candidate
    raise RuntimeError("Repo root tidak ditemukan.")

if IN_COLAB:
    # KODE dari GitHub, BUKAN dari salinan Drive. Salinan Drive disinkron
    # sebagai berkas biasa, jadi .git ikut tersalin dan melaporkan commit lama
    # sementara kodenya entah versi mana. Clone dangkal menghapus seluruh kelas
    # kegagalan itu. KONSEKUENSINYA: suntingan yang belum dipush TIDAK ikut.
    REPO_DIR = Path("/content/PVStringHeatmapCheck")
    if (REPO_DIR / ".git").is_dir():
        subprocess.check_call(["git", "-C", str(REPO_DIR),
                               "fetch", "--depth", "1", "origin", "master"])
        subprocess.check_call(["git", "-C", str(REPO_DIR),
                               "reset", "--hard", "origin/master"])
    else:
        subprocess.check_call(["git", "clone", "--depth", "1",
                               REPO_URL, str(REPO_DIR)])
else:
    REPO_DIR = find_repo_root()
    DRIVE_ROOT = str(REPO_DIR)      # lokal: data bersebelahan dgn kode

os.chdir(REPO_DIR)
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))
print("KODE :", REPO_DIR)
print("DATA :", DRIVE_ROOT)

# Penanda ISI. Clone sudah menutup kelas kegagalan salinan basi, tapi ini
# tetap ada untuk kasus lain: fetch yang gagal separuh, atau nama yang dipakai
# sel ternyata belum dipush. Memeriksa keberadaan MODUL saja tidak cukup --
# salinan yang punya berkasnya tapi belum punya fungsi terbarunya lolos Sel 1
# lalu menjatuhkan Sel 4. Sebuah tes menjaga daftar ini tetap lengkap.
_WAJIB = (
    "coords_from_geometry", "decay_score", "el_coords_to_pv",
    "load_el_coords", "pairwise_correlation", "residual_after_site_median",
    "verdict_placement",
)
try:
    import pv_pipeline.spatial_correlation as _sc
except ImportError:
    raise RuntimeError(
        "pv_pipeline/spatial_correlation.py tidak ada di clone. Cek apakah "
        "berkas itu memang sudah dipush ke master."
    )
_hilang = [n for n in _WAJIB if not hasattr(_sc, n)]
if _hilang:
    raise RuntimeError(
        f"Clone TERTINGGAL dari yang dibutuhkan notebook ini: "
        f"spatial_correlation.py ada tapi tidak punya {_hilang}. Push dulu "
        f"perubahannya, lalu jalankan ulang dari Sel 1."
    )
print(f"versi repo (isi): spatial_correlation lengkap ({len(_WAJIB)} nama)")
'''

CODE_CONFIG = '''# Cell 2 - Konfigurasi (edit nilai di sini)
from pv_pipeline.spatial_correlation import (
    DEFAULT_MIDDAY,
    DEFAULT_MIN_CONTROL_RHO,
    DEFAULT_MIN_MARGIN,
    DEFAULT_MIN_OVERLAP,
)

BASELINE_DIR = f"{DRIVE_ROOT}/baseline"

# Hari berawan sebagian, hasil peringkat Drive_Probe.ipynb Sel 5.
# Hari cerah stabil TIDAK berguna di sini: tanpa awan lewat tidak ada kontras
# antar string, dan seluruh skor akan runtuh ke nol untuk kedua kandidat.
# --- Gelombang mana yang dijalankan -----------------------------------------
# Gugus WB01 yang penempatannya berselisih dengan survei EL berisi 13
# inverter (>5 m). Diuji BERGELOMBANG, bukan sekaligus: satu run hanya
# sanggup ~4 kandidat + 4 kontrol sebelum pasangannya terlalu berjauhan
# untuk dibandingkan -- pasangan kandidat gelombang 2 sudah bermedian
# 249-285 m lawan 64 m pada kontrol.
#
# KENAPA HARUS SELESAI SEMUA SEBELUM DITERAPKAN. Memindahkan sebagian gugus
# meninggalkan tetangganya di posisi DXF, dan pasangan yang terbelah dua
# sumber saling bertabrakan. Terukur pada gelombang 2: memindahkan 4 dari 13
# menaikkan tabrakan <3 m dari 14 menjadi 68, memindahkan 7 menjadi 28, dan
# hanya memindahkan ke-13 sekaligus yang mengembalikannya ke 14. Jadi
# ketiga gelombang harus tuntas lebih dulu, baru string_geometry.csv diubah
# satu kali untuk seluruh 13.
GELOMBANG = 3          # <-- ganti ke 4 untuk putaran terakhir

_KANDIDAT = {
    # SELESAI 16 Agu 2026, dua set hari bebas, keduanya memilih EL:
    # margin 0,107 (08/22/20 Jun) dan 0,131 (01/10/06 Jun).
    2: ["WB01-INV21", "WB01-INV18", "WB01-INV25", "WB01-INV01"],
    # Empat terparah berikutnya, 30-33 m. Ketiganya yang pertama justru
    # tetangga yang bertabrakan dengan gelombang 2 -- itulah sebabnya
    # mereka didahulukan.
    3: ["WB01-INV20", "WB01-INV19", "WB01-INV08", "WB01-INV02"],
    # Lima terakhir, 9-27 m.
    4: ["WB01-INV07", "WB01-INV03", "WB01-INV13", "WB01-INV12",
        "WB01-INV06"],
}

# Hari berawan sebagian, hasil peringkat Drive_Probe.ipynb Sel 5. Hari cerah
# stabil TIDAK berguna: tanpa awan lewat tidak ada kontras antar string dan
# skornya runtuh ke nol untuk kedua kandidat.
#
# Tiap gelombang memakai hari yang BELUM dipakai gelombang lain. Bukan
# karena harinya habis -- 23 dari 30 hari Juni memenuhi lantai -- melainkan
# supaya tiap vonis berdiri di atas awan yang berbeda.
_HARI = {
    2: ["2026-06-01", "2026-06-10", "2026-06-06"],
    3: ["2026-06-24", "2026-06-21", "2026-06-28"],
    # ISI DARI Drive_Probe Sel 5 sebelum menjalankan gelombang 4. Sengaja
    # dikosongkan: menebak tanggal yang belum diperingkat bisa memilih hari
    # cerah stabil, yang memulangkan skor nol dan terbaca seolah buktinya
    # hilang.
    4: [],
}

INVERTER_DIBANTAH = _KANDIDAT[GELOMBANG]
HARI = _HARI[GELOMBANG]
if not HARI:
    raise ValueError(
        f"HARI untuk gelombang {GELOMBANG} masih kosong. Jalankan "
        f"Drive_Probe.ipynb Sel 5 -- ia mengecualikan hari yang sudah "
        f"dipakai -- lalu salin baris HARI-nya ke _HARI[{GELOMBANG}]."
    )

# KONTROL -- dipakai membuktikan metodenya punya daya pisah pada data hari
# itu. Tetangga se-blok supaya cuacanya sama, dan SAMA untuk tiap gelombang
# supaya angka kontrolnya bisa dibandingkan antar putaran.
#
# PILIH YANG BERSIH. Kontrol gelombang 1 keliru: WB02-INV03 (16,25 m),
# INV07 (16,17) dan INV08 (14,60) ternyata IKUT bergeser, cuma INV05 yang
# bersih. Keempat ini terukur berselisih 0,11 m saja terhadap posisi EL
# setelah direcenter -- benar-benar tidak disengketakan.
INVERTER_KONTROL = ["WB01-INV24", "WB01-INV22", "WB01-INV23", "WB01-INV17"]

# --- Kandidat penempatan -----------------------------------------------------
# DXF: dari config/string_geometry.csv, kolom north/east. Untuk gelombang 2
# koordinat itu masih koordinat DXF asli, belum tersentuh -- itulah yang diuji
# di sini melawan posisi EL. (Untuk gelombang 1 keduanya kini identik karena
# hasilnya sudah diterapkan; lihat catatan di INVERTER_DIBANTAH.)
#
# EL: survei drone. Skemanya kini ditangani load_el_coords, jadi tidak ada lagi
# nama kolom yang perlu ditebak di sini. Berkasnya per MODUL (114.420 baris
# untuk 4.470 string), berkoordinat Longitude/Latitude, dan memakai DUA ragam
# label: S{plant}{inv}_{st} untuk Phase One, WB10INV17ST23 untuk WB03-10.
# Headernya dicari lewat penanda "#String", bukan nomor baris -- menghitung
# baris dari luar sudah dua kali meleset karena pd.read_csv melewati baris
# kosong di blok pengantarnya.
EL_CSV = f"{DRIVE_ROOT}/raw data input/Drawing/el drone 2025/all.csv"

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

# core.load_empty_pv_map menerima dict KONFIGURASI, bukan path -- beda dari
# string_config.load_empty_pv_map dan string_config.get_empty_pv_map yang
# keduanya menerima path. Dict kosong memakai default config/strings.yaml.
# Pemanggilan ini sama persis dengan String_Intraday_Diagnostic Cell 4.
EMPTY_PV_MAP = load_empty_pv_map({}, base_dir=str(REPO_DIR))
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
    coords_from_geometry, el_coords_to_pv, load_el_coords,
    pairwise_correlation, residual_after_site_median,
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

EL_PER_ST = load_el_coords(EL_CSV)
KOOR_EL = el_coords_to_pv(EL_PER_ST, GEOM)
print(f"EL  : {len(EL_PER_ST)} string di survei -> {len(KOOR_EL)} berkanal PV")
print("      Selisihnya string yang tidak punya pemetaan ST->PV di as-built;")
print("      dibuang, bukan ditebak. pv = st BENAR di Phase One, SALAH di WB03-10.")

_uji = set(PASANGAN["a"]) | set(PASANGAN["b"])
print(f"\\ncakupan pada string yang diuji: DXF {len(_uji & set(KOOR_DXF))}, "
      f"EL {len(_uji & set(KOOR_EL))} dari {len(_uji)}")

# Penjaga gelombang. Begitu sebuah gelombang diterapkan ke
# string_geometry.csv, koordinat DXF kandidatnya MENJADI koordinat EL, dan
# uji ini berubah jadi EL lawan EL: margin nol, tanpa satu pun galat, dan
# hasilnya terbaca seolah buktinya menghilang. Itu kegagalan senyap.
#
# KEDUA HIMPUNAN BEDA KERANGKA: coords_from_geometry memulangkan UTM
# absolut (north ~9.890.000) sedangkan load_el_coords memulangkan meter
# relatif terhadap centroid survei (~0). Mengurangkannya mentah-mentah
# memberi ~9.901.361 m -- jarak antar kerangka, bukan pergeseran string --
# sehingga penjaganya SELALU lolos dan tidak menjaga apa pun. Versi pertama
# penjaga ini melakukan persis itu. Recenter dulu, baru bandingkan.
#
# decay_score sendiri tidak terpengaruh: ia menghitung jarak DI DALAM satu
# himpunan, jadi offset kerangka meniadakan diri dan skornya tetap sah.
_kand = sorted(s for s in _uji
               if s.rsplit('-PV', 1)[0] in set(INVERTER_DIBANTAH)
               and s in KOOR_DXF and s in KOOR_EL)
if _kand:
    import math as _math
    _a = [KOOR_DXF[s] for s in _kand]
    _b = [KOOR_EL[s] for s in _kand]
    _ca = (sum(p[0] for p in _a) / len(_a), sum(p[1] for p in _a) / len(_a))
    _cb = (sum(p[0] for p in _b) / len(_b), sum(p[1] for p in _b) / len(_b))
    _d = sorted(_math.dist((x[0] - _ca[0], x[1] - _ca[1]),
                          (y[0] - _cb[0], y[1] - _cb[1]))
                for x, y in zip(_a, _b))
    _med = _d[len(_d) // 2]
    print(f"pergeseran DXF->EL pada KANDIDAT (setelah recenter): "
          f"median {_med:.2f} m, maks {_d[-1]:.2f} m (n={len(_d)})")
    if _med < 1.0:
        raise RuntimeError(
            f"Kedua penempatan praktis SAMA pada kandidat (median "
            f"{_med:.2f} m setelah recenter). Gelombang ini agaknya SUDAH "
            f"diterapkan ke config/string_geometry.csv, jadi uji ini "
            f"mengadu EL lawan EL dan memberi margin nol. Setel "
            f"INVERTER_DIBANTAH di Sel 2 ke gelombang yang BELUM diterapkan."
        )
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
