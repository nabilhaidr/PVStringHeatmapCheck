"""Builder Drive_Probe.ipynb.

Empat probe murah atas data Drive, masing-masing menjawab satu pertanyaan yang
selama ini berstatus "menunggu" -- tanpa menjalankan diagnostik penuh 1,4 GB.

Edit builder ini lalu jalankan:
    python output_string/_build_drive_probe_notebook.py
"""
from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).parent / "Drive_Probe.ipynb"

MD_INTRO = '''# Probe Drive -- empat pertanyaan yang tidak butuh run penuh

Empat hal berstatus "menunggu data". Tiga di antaranya sebenarnya tidak
menunggu apa pun: datanya sudah ada di Drive, tinggal dibaca. Notebook ini
membacanya.

| Sel | Pertanyaan | Ongkos |
| --- | --- | --- |
| 3 | **D** -- bulan apa saja yang ada di Drive? Jendela musim ketiga di mana? | daftar folder |
| 4 | **A** -- apakah ekspor Nov-Des 2025 memuat Phase One? | header + 1 kolom, beberapa CSV |
| 5 | **B** -- hari mana yang berawan sebagian? | satu deret POA |
| 6 | **C** -- WB05-INV05 PV9 itu kosong, atau inverternya yang diam? | 1 CSV, dicoba beberapa hari |

Urutannya disengaja: Sel 3 memberi tahu bulan mana yang ada sebelum Sel 4
mencoba membacanya.

## Kenapa probe, bukan run penuh

Pertanyaan A pernah dijawab dengan menjalankan diagnostik dan menghitung
stringnya. Itu mahal dan menyesatkan: angka 431 yang keluar berasal dari daftar
`INVERTER_IDS` yang saat itu 17, bukan dari isi CSV-nya. Membaca kolom
`ManageObject` menjawab pertanyaan sebenarnya dalam hitungan detik, dan
jawabannya tidak bisa dikacaukan oleh konfigurasi.

## Yang dijaga probe ini

Tiap probe menolak menjawab kalau datanya tidak mendukung, alih-alih
mengembalikan angka yang terlihat wajar:

- Berkas tanpa `ManageObject` **bergalat**, tidak melapor "nol inverter" --
  karena nol inverter terbaca sebagai "ekspor format lama", vonis salah atas
  sebab salah.
- Inverter yang tidak melapor mengembalikan `TIDAK_MELAPOR` dengan
  `target_kw=None`, **bukan 0,00 kW**. Nol dari inverter mati tidak
  membuktikan kanalnya kosong -- persis yang menggantung WB05-INV05 PV9.
- Kedua konvensi huruf kolom dilaporkan terpisah (`PV1 input current(A)`
  sampai PV14, `PV15 Input Current(A)` seterusnya), supaya terlihat kalau
  separuh kanal terjatuh.
- Bulan berfolder kosong dilaporkan `n_hari=0`, tidak dihilangkan.

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

# Repo DIBACA dari Drive, tidak di-clone dan tidak di-pull. Kalau salinan di
# Drive tertinggal, perubahan terbaru diam-diam tidak berlaku dan hasilnya
# tetap terlihat wajar. Cetak versinya -- tapi JANGAN percaya begitu saja:
# Drive disinkron sebagai berkas, .git bisa tertinggal berbulan-bulan.
import subprocess
_v = subprocess.run(["git", "-C", str(REPO_DIR), "log", "--oneline", "-1"],
                    capture_output=True, text=True)
print("versi repo (git):", (_v.stdout.strip() or _v.stderr.strip()
                            or "(bukan git repo -- salinan manual?)"))

# Penanda isi: ini TIDAK bisa bohong seperti .git bisa. Tapi memeriksa
# keberadaan MODUL saja tidak cukup -- salinan Drive yang punya berkasnya tapi
# belum punya fungsi terbarunya akan lolos di sini lalu menjatuhkan sel yang
# jauh di bawah dengan ImportError. Itu sudah terjadi pada notebook saudaranya.
# Nama-nama disebut satu per satu; sebuah tes menjaga daftarnya tetap lengkap.
_WAJIB = (
    "inventory_baseline", "probe_channel_silence", "probe_inverter_coverage",
    "rank_variable_days",
)
try:
    import pv_pipeline.drive_probe as _dp
except ImportError:
    raise RuntimeError(
        "pv_pipeline/drive_probe.py tidak ada di salinan Drive ini. "
        "Notebook ini butuh modul itu; sinkronkan ulang repo ke Drive."
    )
_hilang = [n for n in _WAJIB if not hasattr(_dp, n)]
if _hilang:
    raise RuntimeError(
        f"Salinan Drive TERTINGGAL: pv_pipeline/drive_probe.py ada, tapi "
        f"tidak punya {_hilang}. Sinkronkan ulang berkas itu lalu jalankan "
        f"ulang dari Sel 1."
    )
print(f"versi repo (isi) : drive_probe lengkap ({len(_WAJIB)} nama)")
'''

CODE_CONFIG = '''# Cell 2 - Konfigurasi (edit nilai di sini)
from pv_pipeline.drive_probe import (
    DEFAULT_MIDDAY,
    DEFAULT_MIN_MEAN_POA,
    DEFAULT_VARIABILITY_MIDDAY,
)

BASELINE_DIR = "/content/drive/MyDrive/Cek PV String/baseline"

# --- Probe A: periode yang mau diperiksa cakupannya -------------------------
BULAN_DIPERIKSA = ["2025-11", "2025-12"]
# Berapa CSV per bulan yang dicicipi. Tiga sudah cukup: yang dinilai format
# ekspor, dan format tidak berubah di tengah bulan. Naikkan kalau curiga.
CSV_PER_BULAN = 3
# Phase One = 50 inverter (WB01 25 + WB02 25). Di bawah ini dianggap tidak
# lengkap dan dilaporkan sebagai temuan, bukan diam-diam diterima.
PHASE_ONE_LENGKAP = 50

# --- Probe B: hari berawan sebagian ----------------------------------------
# Juni 2026 dipilih karena musim kering: 87% hari di bawah 5 mm, jadi hari
# yang variabel di sana benar-benar awan lewat, bukan hujan seharian.
POA_MULAI = "2026-06-01"
POA_SELESAI = "2026-06-30"
# Kosongkan untuk memakai config/site_geometry.yaml. Isi daftar path kalau
# folder "raw data input" sedang tidak di tempat yang diharapkan config.
POA_XLSX = []
JENDELA_VARIABILITAS = DEFAULT_VARIABILITY_MIDDAY
MIN_POA_RATA = DEFAULT_MIN_MEAN_POA
TOP_HARI = 10

# --- Probe C: kanal yang dituduh kosong ------------------------------------
KANAL_DIUJI = [("WB05-INV05", 9)]
# Bulan tempat mencari hari saat inverternya melapor.
BULAN_UNTUK_KANAL = ["2025-11", "2025-12"]
MAKS_HARI_DICOBA = 10
JENDELA_TENGAH_HARI = DEFAULT_MIDDAY

print("Baseline :", BASELINE_DIR)
print("Probe A  :", BULAN_DIPERIKSA, f"({CSV_PER_BULAN} CSV/bulan)")
print("Probe B  :", POA_MULAI, "s.d.", POA_SELESAI,
      f"jendela {JENDELA_VARIABILITAS}, lantai {MIN_POA_RATA:.0f} W/m2")
print("Probe C  :", KANAL_DIUJI, "dicari di", BULAN_UNTUK_KANAL)
'''

CODE_PROBE_D = '''# Cell 3 - Probe D: bulan apa saja yang ada di Drive
from pv_pipeline.drive_probe import inventory_baseline

INVENTARIS = inventory_baseline(BASELINE_DIR)
print(INVENTARIS.to_string(index=False))
print()

# Dua jendela yang sudah divalidasi. Jendela ketiga berguna kalau jatuh di
# ANTARA keduanya -- menyetel ambang pada dua titik ekstrem saja membuat
# SEASONAL_REL_RANGE_MAX tidak bisa dibedakan dari garis lurus.
DOY_TERPAKAI = {"Jun 2026": 166, "Nov-Des 2025": 335}
print("Jendela terpakai:", DOY_TERPAKAI)
print()
print("Kandidat jendela ketiga (jarak doy ke jendela terdekat, makin besar makin baik):")
_ada = INVENTARIS[INVENTARIS["n_hari"] > 0]
if _ada.empty:
    print("  TIDAK ADA bulan berisi di", BASELINE_DIR, "-- cek path/mount.")
else:
    _skor = []
    for _, b in _ada.iterrows():
        d = int(b["doy_tengah"])
        jarak = min(min(abs(d - v), 365 - abs(d - v)) for v in DOY_TERPAKAI.values())
        _skor.append((jarak, b["bulan"], b["n_hari"], d))
    for jarak, bulan, n, d in sorted(_skor, reverse=True)[:6]:
        print(f"  {bulan}  doy {d:3d}  {int(n):2d} hari  jarak {jarak:3d}")
    print()
    print("Baca: bulan berjarak besar dari KEDUA jendela adalah kandidat terbaik.")
    print("Maret/April (doy ~74-105) duduk di tengah Jun(166) dan Nov-Des(335).")

_kosong = INVENTARIS[INVENTARIS["n_hari"] == 0]
if len(_kosong):
    print()
    print("PERHATIAN -- folder ada tapi kosong (ekspor gagal, bukan 'tidak tersedia'):")
    print("  " + ", ".join(_kosong["bulan"]))
'''

CODE_PROBE_A = '''# Cell 4 - Probe A: apakah ekspor periode ini memuat Phase One
from pv_pipeline.drive_probe import probe_inverter_coverage

CAKUPAN = {}
for bulan in BULAN_DIPERIKSA:
    folder = Path(BASELINE_DIR) / bulan
    if not folder.is_dir():
        print(f"{bulan}: FOLDER TIDAK ADA -- lihat inventaris di Cell 3.")
        continue
    berkas = sorted(folder.glob("*.csv"))
    if not berkas:
        print(f"{bulan}: folder ada tapi KOSONG.")
        continue
    # Cicipi awal, tengah, akhir bulan -- bukan tiga berkas pertama, supaya
    # perubahan format di tengah periode tidak terlewat.
    langkah = max(1, len(berkas) // CSV_PER_BULAN)
    for path in berkas[::langkah][:CSV_PER_BULAN]:
        CAKUPAN[path.name] = probe_inverter_coverage(path)
        print(f"{path.name}: {CAKUPAN[path.name].ringkas()}")

print()
if not CAKUPAN:
    print("VONIS: tidak ada CSV terbaca. Bukan 'format lama' -- tidak ada datanya.")
else:
    _p1 = {n: len(c.phase_one) for n, c in CAKUPAN.items()}
    _min, _max = min(_p1.values()), max(_p1.values())
    _besar = {n: len(c.channels_titlecase) for n, c in CAKUPAN.items()}

    if _max == 0:
        print("VONIS: Phase One TIDAK ADA di ekspor periode ini.")
        print("  Ekspornya format lama. Musim kedua untuk WB01/WB02 belum ada;")
        print("  minta ulang ekspor periode ini dari portal, atau tunggu.")
        print("  CATATAN: 22 dari 25 CALON_GEOMETRI ada di WB03-10, dan itu")
        print("  TIDAK terpengaruh -- run musim kedua tetap layak dijalankan.")
    elif _min >= PHASE_ONE_LENGKAP:
        print(f"VONIS: Phase One ADA dan lengkap ({_min} inverter di tiap CSV).")
        print("  Musim kedua tersedia hari ini. Jalankan String_Intraday_Diagnostic")
        print("  dengan BULAN dan RAIN_EVENTS yang sudah diset ke periode ini.")
    else:
        print(f"VONIS: Phase One ADA tapi TIDAK LENGKAP ({_min}-{_max} dari "
              f"{PHASE_ONE_LENGKAP}).")
        print("  Jangan diperlakukan sebagai 'sebagian datanya hilang' sebelum")
        print("  dicek: inverter yang absen mungkin memang tidak melapor hari itu.")

    if any(v == 0 for v in _besar.values()):
        print()
        print("PERINGATAN -- ada CSV tanpa kanal huruf besar (PV15+).")
        print("  Berkas normal punya 14 huruf kecil + 22 huruf besar. Nol huruf")
        print("  besar berarti separuh kanal tidak ada, dan hitungan string")
        print("  apa pun dari berkas itu akan kurang tanpa memberi galat.")
'''

CODE_PROBE_B = '''# Cell 5 - Probe B: hari mana yang berawan sebagian
import pandas as pd
from pv_pipeline.drive_probe import rank_variable_days
from pv_pipeline.poa import PyranometerLoader

if POA_XLSX:
    LOADER = PyranometerLoader(POA_XLSX)
else:
    LOADER = PyranometerLoader.from_geometry_yaml("config/site_geometry.yaml")

# Grid 5 menit sepanjang periode; get_avg mereindeks ke sini dengan toleransi
# 2 menit, jadi lubang data jadi NaN dan dibuang -- bukan diinterpolasi.
IDX = pd.date_range(f"{POA_MULAI} 00:00", f"{POA_SELESAI} 23:55", freq="5min")
POA = LOADER.get_avg(IDX)
print(f"POA: {POA.notna().sum()} dari {len(POA)} cuplikan terisi "
      f"({POA_MULAI} s.d. {POA_SELESAI})")

HARI = rank_variable_days(
    POA, midday=JENDELA_VARIABILITAS, min_mean_poa=MIN_POA_RATA,
)
print()
print(HARI.head(TOP_HARI).to_string(index=False))
print()

_layak = HARI[HARI["cukup_terang"]]
print(f"{len(_layak)} dari {len(HARI)} hari memenuhi lantai "
      f"{MIN_POA_RATA:.0f} W/m2.")
if _layak.empty:
    print("Tidak ada hari yang layak. Periodenya mendung; pilih periode lain.")
else:
    _pakai = _layak.head(3)["tanggal"].tolist()
    print("Kandidat teratas untuk uji korelasi spasial:", _pakai)
    print()
    print("Cara membacanya:")
    print("  variabilitas tinggi + cukup_terang  -> hari cerah yang DIINTERUPSI.")
    print("     Bayangan awan bergerak melintasi larik; inilah sinyal spasial")
    print("     yang dibaca uji korelasi.")
    print("  variabilitas tinggi + TIDAK terang  -> mendung total. Cahayanya")
    print("     difus dan merata; tidak ada bayangan bergerak sama sekali.")
    print("     Rasionya besar cuma karena penyebutnya kecil.")
    print("  variabilitas ~0                     -> hari cerah stabil. Tidak")
    print("     memberi kontras antar string, jadi tidak berguna di sini.")
'''

CODE_PROBE_C = '''# Cell 6 - Probe C: kanal itu kosong, atau inverternya yang diam
from pv_pipeline.drive_probe import probe_channel_silence

_kandidat = []
for bulan in BULAN_UNTUK_KANAL:
    folder = Path(BASELINE_DIR) / bulan
    if folder.is_dir():
        _kandidat.extend(sorted(folder.glob("*.csv")))

if not _kandidat:
    print("Tidak ada CSV di", BULAN_UNTUK_KANAL, "-- lihat inventaris Cell 3.")
else:
    for inverter_id, pv in KANAL_DIUJI:
        print(f"=== {inverter_id} PV{pv} ===")
        dilewati = 0
        putusan = None
        for path in _kandidat[:MAKS_HARI_DICOBA]:
            hasil = probe_channel_silence(
                path, inverter_id, pv, midday=JENDELA_TENGAH_HARI,
            )
            if not hasil["inverter_hadir"]:
                dilewati += 1
                continue
            putusan = (path.name, hasil)
            break

        if putusan is None:
            print(f"  {dilewati} hari dicoba, inverternya TIDAK MELAPOR di "
                  f"satu pun.")
            print("  Ini bukan bukti kanalnya kosong -- ini ketiadaan data.")
            print("  Naikkan MAKS_HARI_DICOBA atau pilih bulan lain.")
        else:
            nama, h = putusan
            print(f"  hari terpakai   : {nama} ({dilewati} hari dilewati "
                  f"karena inverternya diam)")
            print(f"  target PV{pv}      : {h['target_kw']:.3f} kW")
            print(f"  saudara (n={h['saudara_n']:2d}) : {h['saudara_kw']:.3f} kW median")
            print(f"  PUTUSAN         : {h['putusan']}")
            print()
            if h["putusan"] == "KOSONG_TERBUKTI":
                print("  Kanal diam sementara saudaranya berproduksi -> pemetaan")
                print("  as-built ke kanal ini terbantah. Aturan yang sama sudah")
                print("  menjatuhkan delapan kanal lain; yang ini kini genap.")
            elif h["putusan"] == "TERPAKAI":
                print("  Kanal ini JUSTRU BERPRODUKSI. Pemetaan as-built-nya benar")
                print("  dan asumsi 'kosong' yang selama ini dipegang salah.")
                print("  Periksa ulang strings.yaml untuk inverter ini.")
            else:
                print("  Saudaranya sendiri di bawah ambang -- inverter sedang")
                print("  ditekan atau harinya mendung. Tidak membuktikan apa pun;")
                print("  coba hari lain.")
'''

CELLS = [
    ("markdown", MD_INTRO),
    ("code", CODE_SETUP),
    ("code", CODE_CONFIG),
    ("code", CODE_PROBE_D),
    ("code", CODE_PROBE_A),
    ("code", CODE_PROBE_B),
    ("code", CODE_PROBE_C),
]


def _cell(kind: str, source: str, index: int) -> dict:
    cell = {
        "cell_type": kind,
        "id": f"drive-probe-{index:02d}",
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
