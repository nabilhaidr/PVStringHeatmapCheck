"""Data acquisition layer: Google Drive download + Excel ingestion.

Sumber data: file Excel logging inverter (per-WB) yang disimpan di folder Google Drive.
Mode default: gdown -> temporary directory -> load via pandas.read_excel.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
from typing import List, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Package management helpers
# ---------------------------------------------------------------------------
def ensure_package(pkg_name: str) -> None:
    """Install a package via pip if it is not currently importable.

    Mirroring perilaku Cell 1 notebook v1.2: digunakan untuk menjamin gdown
    tersedia di environment Colab atau VM tanpa konfigurasi tambahan.
    """
    try:
        __import__(pkg_name)
    except ImportError:
        print(f"Installing missing package: {pkg_name}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg_name])


# ---------------------------------------------------------------------------
# Google Drive download
# ---------------------------------------------------------------------------
def download_from_gdrive(
    folder_url: str,
    expected_files: Optional[List[str]] = None,
) -> str:
    """Download a Google Drive folder using gdown into a temporary directory.

    Parameters
    ----------
    folder_url : str
        URL folder Google Drive (publik / shareable).
    expected_files : list[str], optional
        Tidak digunakan untuk filter saat download (gdown akan unduh seluruh folder),
        diterima untuk kompatibilitas dengan API lama.

    Returns
    -------
    str
        Path absolut ke direktori temporer berisi file hasil download.
    """
    ensure_package("gdown")
    import gdown  # noqa: WPS433 (lazy import after ensure_package)

    tmpdir = tempfile.mkdtemp(prefix="gdrive_")
    print(f"Downloading folder to temporary dir: {tmpdir}")
    try:
        gdown.download_folder(folder_url, output=tmpdir, quiet=False, use_cookies=False)
    except Exception as err:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError(f"gdown failed to download the folder. Error: {err}") from err

    return tmpdir


def find_expected_files(root_dir: str, expected_files: List[str]) -> List[str]:
    """Cari path lengkap untuk setiap file di ``expected_files`` di bawah ``root_dir``.

    Pencarian dilakukan rekursif. Bila exact match tidak ditemukan, dilakukan
    fallback case-insensitive. File yang tidak ditemukan menghasilkan warning
    pada stdout (tidak raise) sehingga pemanggil dapat memutuskan apakah
    melanjutkan atau berhenti.
    """
    found: List[str] = []
    for fname in expected_files:
        pattern = os.path.join(root_dir, "**", fname)
        matches = glob.glob(pattern, recursive=True)
        if not matches:
            all_matches = glob.glob(os.path.join(root_dir, "**", "*"), recursive=True)
            matches = [p for p in all_matches if os.path.basename(p).lower() == fname.lower()]
        if not matches:
            print(f"WARNING: expected file '{fname}' not found under {root_dir}")
        else:
            chosen = matches[0]
            print(f"Found '{fname}' as: {chosen}")
            found.append(os.path.abspath(chosen))
    return found


# ---------------------------------------------------------------------------
# Excel reading
# ---------------------------------------------------------------------------
def safe_read_excel(
    fpath: str,
    header_row: int = 3,
    usecols: Optional[List[str]] = None,
    nrows: Optional[int] = None,
) -> pd.DataFrame:
    """Wrapper around ``pandas.read_excel`` that wraps IOError dengan pesan jelas."""
    try:
        return pd.read_excel(fpath, header=header_row, usecols=usecols, nrows=nrows)
    except Exception as err:
        raise IOError(f"Could not read file {fpath}: {err}") from err


def load_and_prepare_data(
    folder_path: str,
    expected_files: Optional[List[str]] = None,
    excel_header_row: int = 3,
    usecols: Optional[List[str]] = None,
    nrows: Optional[int] = None,
) -> pd.DataFrame:
    """Load every Excel in ``expected_files`` dan gabungkan menjadi satu DataFrame.

    Setiap baris diberi kolom ``_source_file`` untuk traceability.

    Raises
    ------
    ValueError
        Bila ``expected_files`` kosong / None.
    FileNotFoundError
        Bila tidak ada file yang berhasil di-load.
    """
    # Suppress openpyxl "no default style" warning (selalu muncul untuk file
    # ekspor SCADA yang tidak menyimpan style default).
    warnings.filterwarnings(
        "ignore",
        message=r"Workbook contains no default style, apply openpyxl's default",
        category=UserWarning,
    )

    if not expected_files:
        raise ValueError("expected_files cannot be empty or None.")

    dfs: List[pd.DataFrame] = []
    for fname in expected_files:
        full_path = os.path.join(folder_path, fname)
        if not os.path.exists(full_path):
            print(f"WARNING: File {full_path} not found. Skipping.")
            continue
        try:
            df = pd.read_excel(full_path, header=excel_header_row, usecols=usecols, nrows=nrows)
            df["_source_file"] = fname
            dfs.append(df)
        except Exception as err:
            print(f"ERROR: Could not load file {fname}. Error: {err}")

    if not dfs:
        raise FileNotFoundError("No data loaded. Ensure the files exist or check file paths.")

    return pd.concat(dfs, ignore_index=True, sort=False)
