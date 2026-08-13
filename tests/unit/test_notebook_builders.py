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
BUILDERS = [
    "_build_geometry_rescore_notebook",
    "_build_string_intraday_notebook",
]


def _muat(nama: str):
    """Impor pembangun notebook lewat path; ``output_string`` bukan paket."""
    path = REPO / "output_string" / f"{nama}.py"
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


@pytest.mark.parametrize("nama", BUILDERS)
def test_konstanta_modul_yang_dipakai_sel_selalu_ada_yang_mengimpor(nama):
    """Konstanta ``string_intraday_diagnostic`` yang dipakai sel wajib diimpor.

    Dibatasi pada konstanta ALL_CAPS milik modul itu, bukan seluruh nama
    bebas: nama seperti ``OUTPUT_DIR`` atau ``WORKBOOK`` memang lahir di sel
    dan bukan urusan tes ini. Yang dijaga satu kelas kesalahan yang sudah
    terbukti terjadi -- konstanta modul dipakai tanpa ada yang mengimpornya --
    dan itu tidak mungkin ketahuan dari tes modul mana pun, karena modulnya
    sendiri baik-baik saja.
    """
    pembangun = _muat(nama)
    diagnostic = importlib.import_module("pv_pipeline.string_intraday_diagnostic")
    konstanta = {n for n in dir(diagnostic) if n.isupper() and not n.startswith("_")}

    sel_kode = [sumber for jenis, sumber in pembangun.CELLS if jenis == "code"]
    assert sel_kode, f"{nama}: tidak ada sel kode"

    diimpor, dibuat, dipakai = _nama_dalam_sel(sel_kode)
    hilang = sorted((dipakai & konstanta) - diimpor - dibuat)

    assert not hilang, (
        f"{nama}: dipakai sel notebook tapi tidak ada sel yang mengimpornya: "
        f"{hilang}"
    )
