"""Smoke Spatial_Correlation.ipynb -- eksekusi selnya sungguhan, offline.

Sel notebook tidak pernah dieksekusi tes unit, dan celah itu sudah dua kali
meloloskan cacat ke Colab: DEFAULT_AMPM_GAP yang tidak diimpor, lalu
``load_empty_pv_map("config/strings.yaml")`` -- path string diberikan ke
parameter yang mengharap dict konfigurasi. Yang kedua lolos dari SEMUA
pemeriksaan statis: aritasnya sah, dan anotasinya berupa string karena
``from __future__ import annotations``. Hanya menjalankan selnya yang
menangkapnya.

Uji ini bukan sekadar "jalan tanpa galat". Ia membangun kasus yang jawabannya
SUDAH DIKETAHUI, lalu menuntut notebook menemukannya:

* Telemetri dibangkitkan dari satu awan yang menyapu diagonal melintasi larik.
* String KONTROL (WB02-INV03/05/07/08) dibayangi menurut koordinat DXF
  ASLINYA dari ``config/string_geometry.csv`` -- untuk mereka DXF memang benar,
  dan itulah yang membuatnya kontrol.
* String DIBANTAH (WB02-INV01/02/04/06) dibayangi menurut posisi lain: posisi
  DXF yang sama tapi ditukar-tukar antar string. Penukaran dipilih, bukan
  pencerminan atau pergeseran, karena keduanya isometri -- matriks jaraknya
  tidak berubah sehingga DXF akan mencetak skor identik dan ujinya tidak
  membuktikan apa pun.
* Penugasan yang tertukar itulah yang ditulis ke CSV EL palsu.

Maka jawaban yang benar adalah EL, dan notebook harus memilihnya.

``config/strings.yaml`` dan ``config/string_geometry.csv`` yang ASLI dipakai,
bukan tiruan -- justru pemanggilan ke keduanya yang pernah rusak.

Jalankan:
    python output_string/_smoke_spatial_correlation.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NOTEBOOK = ROOT / "output_string/Spatial_Correlation.ipynb"

DIBANTAH = ["WB02-INV01", "WB02-INV02", "WB02-INV04", "WB02-INV06"]
KONTROL = ["WB02-INV03", "WB02-INV05", "WB02-INV07", "WB02-INV08"]
HARI = ["2026-06-08", "2026-06-22", "2026-06-20"]

LEBAR_AWAN_M = 35.0      # radius bayangan; sempit supaya median situs tak ikut
KEDALAMAN = 0.55         # 55% penurunan di pusat bayangan
DERAU = 0.01


def _source(notebook: dict, index: int) -> str:
    return "".join(notebook["cells"][index]["source"])


def _exec(notebook: dict, indexes, scope: dict) -> None:
    """Eksekusi sel apa adanya.

    Sengaja TANPA ``patch.dict(sys.modules, ...)`` yang dipakai smoke script
    lain untuk memaksa fallback ``display = print``. Notebook ini tidak
    menyentuh IPython, dan patch itu mengembalikan seluruh ``sys.modules`` saat
    keluar -- membuang modul yang baru diimpor di dalamnya, sehingga ekstensi C
    seperti ``numpy.fft`` (lewat rantai scipy) gagal dimuat ulang dengan
    ``ImportError: cannot load module more than once per process``.
    """
    for index in indexes:
        exec(_source(notebook, index), scope)


def _posisi_benar() -> dict:
    """Kebenaran yang dibangun uji ini, dalam DUA sistem koordinat sekaligus.

    ``{pv_string: {"utm": (north, east), "ll": (lat, lon), "st": int}}``

    Keduanya perlu karena dipakai di tempat berbeda: telemetri sintetis
    dibayangi menurut UTM, sementara berkas EL palsu ditulis dalam
    lintang/bujur seperti berkas aslinya. Keduanya harus menggambarkan posisi
    yang SAMA, jadi permutasinya menukar seluruh catatan sekaligus.

    Kontrol memakai koordinat DXF apa adanya. Yang dibantah memakai koordinat
    DXF milik SAUDARANYA -- ditukar dengan permutasi tetap, sehingga struktur
    jaraknya benar-benar berbeda dari DXF dan bukan sekadar tergeser.
    """
    geom = pd.read_csv(ROOT / "config" / "string_geometry.csv")
    geom = geom[geom["inverter_id"].isin(DIBANTAH + KONTROL)]

    benar = {}
    for row in geom.itertuples(index=False):
        if any(pd.isna(v) for v in
               (row.pv, row.st, row.north, row.east, row.lat, row.lon)):
            continue
        nama = f"{row.inverter_id}-PV{int(row.pv)}"
        benar[nama] = {
            "utm": (float(row.north), float(row.east)),
            "ll": (float(row.lat), float(row.lon)),
            "st": int(row.st),
        }

    nama_dibantah = sorted(k for k in benar
                           if k.rsplit("-", 1)[0] in set(DIBANTAH))
    catatan = [dict(benar[k]) for k in nama_dibantah]
    rng = np.random.default_rng(7)
    urut = rng.permutation(len(catatan))
    for k, j in zip(nama_dibantah, urut):
        # ST tetap milik string itu sendiri; yang ditukar hanya POSISInya.
        benar[k] = {**catatan[j], "st": benar[k]["st"]}
    return benar


def _kolom_v(n: int) -> str:
    return (f"PV{n} input voltage(V)" if n <= 14
            else f"PV{n} Input Voltage(V)")


def _kolom_i(n: int) -> str:
    return (f"PV{n} input current(A)" if n <= 14
            else f"PV{n} Input Current(A)")


def _tulis_baseline(baseline_dir: Path, benar: dict) -> None:
    """CSV harian dengan satu awan menyapu diagonal melintasi larik.

    Penamaan ``ManageObject`` memakai bentuk Logger asli (``Inv_A_2nn_IKN``)
    dan kolom PV memakai DUA konvensi huruf, supaya pemetaan inverter dan
    jebakan huruf besar-kecil ikut teruji.
    """
    utara = np.array([p["utm"][0] for p in benar.values()])
    timur = np.array([p["utm"][1] for p in benar.values()])
    u0, u1 = utara.min(), utara.max()
    t0, t1 = timur.min(), timur.max()

    rng = np.random.default_rng(11)
    for hari in HARI:
        ts = pd.date_range(f"{hari} 09:00", f"{hari} 15:55", freq="5min")
        n = len(ts)
        surya = 700 + 200 * np.sin(np.linspace(0, np.pi, n))
        # Pusat bayangan menyapu diagonal: memutus degenerasi baris/kolom yang
        # muncul kalau awan hanya bergerak searah sumbu.
        pusat_t = np.linspace(t0 - 80, t1 + 80, n)
        pusat_u = np.linspace(u0 - 50, u1 + 50, n)

        per_inv: dict = {}
        for nama, rec in benar.items():
            nu, te = rec["utm"]
            inv, pv = nama.rsplit("-PV", 1)
            jarak2 = (pusat_t - te) ** 2 + (pusat_u - nu) ** 2
            teduh = np.exp(-jarak2 / (2 * LEBAR_AWAN_M ** 2))
            daya_kw = surya / 100.0 * (1.0 - KEDALAMAN * teduh)
            daya_kw = daya_kw * (1.0 + rng.normal(0, DERAU, n))
            per_inv.setdefault(inv, {})[int(pv)] = daya_kw

        baris = []
        for inv, kanal in per_inv.items():
            nomor = int(inv.split("INV")[1])
            mo = f"Logger-1/Inv_A_2{nomor:02d}_IKN"
            for i, t in enumerate(ts):
                rec = {"ManageObject": mo,
                       "Start Time": t.strftime("%Y-%m-%d %H:%M:%S")}
                for pv, daya in kanal.items():
                    # V dibuat tetap; seluruh variasi lewat arus, seperti data
                    # nyata pada MPPT yang terkunci.
                    rec[_kolom_v(pv)] = 600.0
                    rec[_kolom_i(pv)] = daya[i] * 1000.0 / 600.0
                baris.append(rec)

        folder = baseline_dir / hari[:7]
        folder.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(baris).to_csv(folder / f"{hari}.csv", index=False)


EL_HEADER = ("#String, Table x, Table y, Module x, Module y, Module type,"
             " Longitude, Latitude, EL Uniformity\n")


def _tulis_el(path: Path, benar: dict, *, penanda: str = "#String") -> None:
    """CSV EL palsu dalam format ASLINYA.

    Tiga sifat berkas asli ditiru karena ketiganya pernah menjatuhkan
    pembacaan: blok pengantar yang memuat baris KOSONG (yang membuat hitungan
    indeks baris meleset), label bentuk pendek ``S{plant}{inv}_{st}``, dan satu
    baris PER MODUL dengan koordinat lintang/bujur -- bukan per string, bukan
    meter.
    """
    with open(path, "w", encoding="utf-8", newline="") as fp:
        for i in range(33):
            fp.write("\n" if i % 7 == 0 else f"#pengantar survei {i}\n")
        fp.write(EL_HEADER.replace("#String", penanda, 1))
        for nama, rec in benar.items():
            inv = nama.rsplit("-PV", 1)[0]
            plant = int(inv[3:4])          # WB01 -> 1, WB02 -> 2
            nomor = int(inv.split("INV")[1])
            label = f"S{plant}{nomor:02d}_{rec['st']:02d}"
            lat, lon = rec["ll"]
            for m in range(4):             # beberapa modul per string
                fp.write(f"{label}, , 2, {m + 1}, 2, 1, "
                         f"{lon + m * 1e-7:.8f}, {lat - m * 1e-7:.8f}, 10.0\n")


def _scope(temp: Path, el_csv: Path, notebook: dict) -> dict:
    """Jalankan Cell 2 lalu arahkan nilainya ke data sintetis."""
    scope = {"REPO_DIR": ROOT, "Path": Path}
    _exec(notebook, (2,), scope)
    scope.update(
        BASELINE_DIR=str(temp / "baseline"),
        HARI=HARI,
        INVERTER_DIBANTAH=DIBANTAH,
        INVERTER_KONTROL=KONTROL,
        EL_CSV=str(el_csv),
    )
    return scope


def _jalankan(temp: Path, el_csv: Path) -> dict:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    scope = _scope(temp, el_csv, notebook)
    _exec(notebook, (3, 4, 5, 6), scope)
    return scope


def _periksa(scope: dict) -> None:
    long_df = scope["LONG"]
    assert long_df["inverter_id"].nunique() == 8, long_df["inverter_id"].nunique()
    assert long_df["pv_string"].nunique() >= 100, long_df["pv_string"].nunique()

    pasangan = scope["PASANGAN"]
    assert len(pasangan) > 1000, len(pasangan)
    # Sinyal se-situs harus sudah terbuang. Kalau median |r| mendekati 1, tiap
    # string masih mengikuti matahari dan seluruh skor di bawah tak bermakna.
    med_abs = pasangan["r"].abs().median()
    assert med_abs < 0.6, f"sinyal se-situs belum terbuang: median |r|={med_abs:.3f}"

    kontrol = scope["KONTROL"]
    assert kontrol["rho"] < -0.2, f"kontrol tidak sensitif: {kontrol}"

    verdict = scope["V"]
    assert verdict["putusan"] == "TERPILIH", verdict
    assert verdict["pilihan"] == "EL", verdict
    print(f"[smoke] kontrol rho {kontrol['rho']:+.3f} | "
          f"DXF {verdict['skor']['DXF']:+.3f} | EL {verdict['skor']['EL']:+.3f} "
          f"| margin {verdict['margin']:.3f}")


def _periksa_berkas_el_asing(temp: Path, benar: dict) -> None:
    """Berkas tanpa penanda header harus BERGALAT, bukan mengarang kolom.

    Inilah kegagalan yang sebenarnya terjadi: dua tebakan indeks baris
    berturut-turut mendarat di baris DATA, dan pandas dengan patuh memakai
    ``S101_01`` serta ``116.63659701`` sebagai NAMA KOLOM. Tidak ada galat --
    hanya tabel yang salah. Pencarian lewat penanda harus berhenti sama sekali
    kalau penandanya tidak ada.
    """
    el_asing = temp / "el_tanpa_penanda.csv"
    _tulis_el(el_asing, benar, penanda="Kolom Pertama")
    try:
        _jalankan(temp, el_asing)
    except ValueError as exc:
        assert "#String" in str(exc), str(exc)
        print("[smoke] berkas EL tanpa penanda header bergalat OK")
        return
    raise AssertionError("berkas EL asing seharusnya menyalak, tapi lolos")


def main() -> None:
    cwd = os.getcwd()
    os.chdir(ROOT)          # Cell 4 membaca config/ dengan path relatif
    try:
        with tempfile.TemporaryDirectory(prefix="spatial_smoke_") as name:
            temp = Path(name)
            benar = _posisi_benar()
            print(f"[smoke] {len(benar)} string, kebenaran dibangun")

            _tulis_baseline(temp / "baseline", benar)
            el_csv = temp / "el_all.csv"
            _tulis_el(el_csv, benar)

            _periksa(_jalankan(temp, el_csv))
            _periksa_berkas_el_asing(temp, benar)
            print("[smoke] OK")
    finally:
        os.chdir(cwd)


if __name__ == "__main__":
    main()
