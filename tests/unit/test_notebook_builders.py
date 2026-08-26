"""Menjaga sel notebook yang digenerate tetap punya semua nama yang dipakainya.

Sel notebook tidak pernah dieksekusi oleh tes -- ia butuh Google Drive. Yang
bisa dijaga tanpa Drive adalah kelengkapan namanya, dan justru di celah itulah
sebuah regresi lolos: commit 036358c mempersempit blok impor Cell 5 ketika
logika verdikt inline diganti ``provisional_direction_verdict``, sementara
Cell 6 masih memakai ``DEFAULT_AMPM_GAP`` untuk menulis metadata. Suite hijau,
tes modul hijau, dan cacatnya baru muncul enam hari kemudian sebagai NameError
di Colab -- setelah lima sel pertama selesai menghitung.
"""

import ast
import importlib
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
# Pembangun -> modul yang konstantanya dijaga untuk pembangun itu. Dipetakan
# per pembangun, bukan satu modul untuk semua: notebook baru memakai modul
# lain, dan menguncinya ke modul yang tidak dipakainya membuat penjaga ini
# hijau tanpa menjaga apa pun.
BUILDERS = {
    "_build_geometry_rescore_notebook": "pv_pipeline.string_intraday_diagnostic",
    "_build_string_intraday_notebook": "pv_pipeline.string_intraday_diagnostic",
    "_build_drive_probe_notebook": "pv_pipeline.drive_probe",
    "_build_spatial_correlation_notebook": "pv_pipeline.spatial_correlation",
    "_build_m2f_nb": "pv_pipeline.m2f.report",
}

# Pembangun lama tinggal di output_string/; pembangun notebook lebih baru
# (mis. _build_daily_runfast_nb.py, _build_m2f_nb.py) tinggal di notebook/.
# Dicoba dua-duanya supaya BUILDERS bisa memetakan pembangun dari kedua
# folder tanpa memindahkan berkas yang sudah ada.
_BUILDER_DIRS = ("output_string", "notebook")


def _muat(nama: str):
    """Impor pembangun notebook lewat path; ``output_string``/``notebook`` bukan paket."""
    for folder in _BUILDER_DIRS:
        path = REPO / folder / f"{nama}.py"
        if path.exists():
            break
    spec = importlib.util.spec_from_file_location(nama, path)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _nama_dalam_sel(sumber_sel):
    """-> (diimpor, dibuat, dipakai) atas seluruh sel kode digabung.

    Digabung karena sel berbagi satu kernel: nama yang diimpor Cell 3 sah
    dipakai Cell 6. Yang dicari bukan impor per sel, melainkan nama yang
    TIDAK ADA satu pun selnya menyediakan.
    """
    diimpor, dibuat, dipakai = set(), set(), set()
    for sumber in sumber_sel:
        for node in ast.walk(ast.parse(sumber)):
            if isinstance(node, ast.ImportFrom):
                diimpor |= {a.asname or a.name for a in node.names}
            elif isinstance(node, ast.Import):
                diimpor |= {(a.asname or a.name).split(".")[0] for a in node.names}
            elif isinstance(node, ast.Name):
                if isinstance(node.ctx, ast.Store):
                    dibuat.add(node.id)
                else:
                    dipakai.add(node.id)
    return diimpor, dibuat, dipakai


@pytest.mark.parametrize("nama,modul", sorted(BUILDERS.items()))
def test_konstanta_modul_yang_dipakai_sel_selalu_ada_yang_mengimpor(nama, modul):
    """Konstanta modul sumber yang dipakai sel wajib ada yang mengimpornya.

    Dibatasi pada konstanta ALL_CAPS milik modul itu, bukan seluruh nama
    bebas: nama seperti ``OUTPUT_DIR`` atau ``WORKBOOK`` memang lahir di sel
    dan bukan urusan tes ini. Yang dijaga satu kelas kesalahan yang sudah
    terbukti terjadi -- konstanta modul dipakai tanpa ada yang mengimpornya --
    dan itu tidak mungkin ketahuan dari tes modul mana pun, karena modulnya
    sendiri baik-baik saja.
    """
    pembangun = _muat(nama)
    sumber_modul = importlib.import_module(modul)
    konstanta = {
        n for n in dir(sumber_modul) if n.isupper() and not n.startswith("_")
    }

    sel_kode = [sumber for jenis, sumber in pembangun.CELLS if jenis == "code"]
    assert sel_kode, f"{nama}: tidak ada sel kode"

    diimpor, dibuat, dipakai = _nama_dalam_sel(sel_kode)
    hilang = sorted((dipakai & konstanta) - diimpor - dibuat)

    assert not hilang, (
        f"{nama}: dipakai sel notebook tapi tidak ada sel yang mengimpornya: "
        f"{hilang}"
    )


@pytest.mark.parametrize("nama,modul", sorted(BUILDERS.items()))
def test_penanda_versi_menyebut_setiap_nama_yang_dipakai_sel(nama, modul):
    """Penanda versi harus memeriksa ISI modul, bukan sekadar keberadaannya.

    Repo dibaca dari Drive, dan salinan Drive bisa tertinggal berbulan-bulan.
    Penanda yang hanya menjalankan ``import pv_pipeline.x`` lulus pada salinan
    lama mana pun yang kebetulan sudah punya berkas itu -- lalu sel yang jauh di
    bawah tumbang dengan ImportError. Persis itu yang terjadi pada
    ``el_coords_to_pv``: Sel 1 mencetak "ADA", Sel 4 gagal.

    Maka penandanya menyebut nama satu per satu lewat tuple ``_WAJIB``, dan tes
    ini menjaga daftar itu tetap lengkap saat sel berubah -- kalau tidak, ia
    akan membusuk dan kembali memberi lampu hijau palsu.

    Pembangun tanpa ``_WAJIB`` dilewati: sebagian notebook lama memakai penanda
    isi bergaya lain (mis. mencetak 72/72 string yang dikosongkan).
    """
    pembangun = _muat(nama)
    sel_kode = [sumber for jenis, sumber in pembangun.CELLS if jenis == "code"]

    wajib = None
    dipakai = set()
    for sumber in sel_kode:
        for node in ast.walk(ast.parse(sumber)):
            if (isinstance(node, ast.Assign)
                    and any(getattr(t, "id", None) == "_WAJIB"
                            for t in node.targets)):
                wajib = {el.value for el in node.value.elts}
            elif isinstance(node, ast.ImportFrom) and node.module == modul:
                dipakai |= {a.name for a in node.names}

    if wajib is None:
        pytest.skip(f"{nama}: tidak memakai penanda _WAJIB")

    # Konstanta ALL_CAPS sudah dijaga tes di atas; di sini yang penting nama
    # yang dipanggil, karena itulah yang hilang saat salinan Drive tertinggal.
    hilang = sorted(n for n in dipakai - wajib if not n.isupper())
    assert not hilang, (
        f"{nama}: diimpor sel tapi tidak diperiksa penanda versi Sel 1: "
        f"{hilang}. Salinan Drive yang tertinggal akan lolos Sel 1 lalu "
        f"gagal di sel yang memakainya."
    )
