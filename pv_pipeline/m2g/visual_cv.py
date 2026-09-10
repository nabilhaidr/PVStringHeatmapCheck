"""M2g -- indeks soiling hasil CV udara masuk ke aliran temuan M2.

Tanggung jawab:
- ``load_cv_table(path)``  : CSV kontrak -> ``DataFrame`` tervalidasi keras.
- ``M2gVisualCV``          : ``SubModule`` yang memancarkan ``M2Finding``.

Kontrak tabel
-------------
Satu baris per (string, hari). Kolom, seluruhnya wajib:

===============  ======================================================
inverter_id      ``"WB03-INV01"`` -- harus ada di string_geometry.csv
pv               nomor string di inverter, bilangan bulat
date             tanggal ISO ``"2026-09-09"``
soiling_index    0..1, MAKIN BESAR MAKIN KOTOR
confidence       0..1, keyakinan model terhadap baris ini
source_image     nama berkas bingkai sumber, untuk telusur balik
===============  ======================================================

Kenapa berupa tabel, bukan modul citra
--------------------------------------
Pemrosesan citra tinggal di repositori CV yang terpisah; repo ini tidak memuat
OpenCV, torch, maupun bobot model. Satu-satunya yang menyeberang adalah tabel
di atas. Akibatnya model boleh berganti, bahkan seluruh tumpukan CV boleh
ditulis ulang, tanpa satu baris pun berubah di sini -- selama kolomnya tetap.
Itu pula sebabnya validasinya keras: kontrak yang dilanggar diam-diam adalah
satu-satunya cara sambungan ini bisa membusuk tanpa ketahuan.

Yang SENGAJA tidak dilakukan
----------------------------
Modul ini TIDAK mengklaim energi hilang ke ``m2f.LossLedger``. Kategori
``soiling`` di sana sudah punya estimator berbasis besaran listrik; menambah
klaim kedua dari citra akan menghitung ganda rugi yang sama. Lagi pula PRD
NSSE-CV-PRD-0001 Tabel 1-1 menegaskan peran citra adalah "lapisan spasial dan
prioritisasi; menjadi bukti hanya setelah dikalibrasi ke besaran listrik".
Sebelum kalibrasi itu ada, temuan di sini berperan sebagai penunjuk arah untuk
prioritas pembersihan, bukan sebagai bukti kuantitatif.
"""
from __future__ import annotations

import os
import warnings
from typing import List, Optional, Set, Tuple

import pandas as pd

from pv_pipeline.core import M2Finding, Severity, SubModule

CV_TABLE_COLUMNS: List[str] = [
    "inverter_id", "pv", "date", "soiling_index", "confidence", "source_image",
]

DEFAULT_ENABLED: bool = False
DEFAULT_TABLE_PATH: str = "outputs/cv_soiling_index.csv"
DEFAULT_GEOMETRY_PATH: str = "config/string_geometry.csv"
DEFAULT_MIN_CONFIDENCE: float = 0.60
# Ambang indeks -> severity. Angka ini BELUM terkalibrasi ke rugi listrik; ia
# hanya mengurutkan prioritas. Menyetelnya ulang setelah percobaan
# cuci-dan-ukur adalah pekerjaan yang memang direncanakan, bukan penyetelan
# sembarang.
DEFAULT_INDEX_MEDIUM: float = 0.35
DEFAULT_INDEX_HIGH: float = 0.55
DEFAULT_INDEX_CRITICAL: float = 0.75

REJECTED_COLUMNS: List[str] = ["inverter_id", "pv", "date", "alasan"]


def load_cv_table(path: str) -> pd.DataFrame:
    """CSV kontrak -> ``DataFrame``; menolak apa pun yang menyimpang.

    Lima pelanggaran dianggap fatal, dan semuanya dipilih karena sama-sama
    menghasilkan temuan yang TERLIHAT sah bila dibiarkan lewat:

    1. Berkas tidak ada -- pipeline CV belum jalan, bukan situs bersih.
    2. Kolom hilang -- baris tanpa ``confidence`` akan lolos setiap gerbang.
    3. ``soiling_index`` atau ``confidence`` di luar 0..1 -- skala yang berbeda
       (mis. 0..100) membuat seluruh ambang severity kehilangan makna tanpa
       satu pun galat.
    4. Duplikat (inverter_id, pv, date) -- dua nilai untuk satu string-hari;
       memilih salah satunya berarti menebak.
    5. Tabel kosong -- lebih mungkin berarti proses hulu gagal daripada berarti
       "tidak ada string kotor".

    Raises
    ------
    ValueError
        Untuk kelima kasus di atas, dengan pesan yang menyebut barisnya.
    """
    if not os.path.exists(path):
        raise ValueError(
            f"{path}: tabel kontrak CV tidak ada. Jalankan pipeline CV lebih "
            f"dulu, atau matikan m2g_visual_cv di config."
        )

    df = pd.read_csv(path)
    hilang = [c for c in CV_TABLE_COLUMNS if c not in df.columns]
    if hilang:
        raise ValueError(
            f"{path}: kolom kontrak hilang {hilang}. "
            f"Wajib: {CV_TABLE_COLUMNS}. Tersedia: {list(df.columns)}"
        )
    if df.empty:
        raise ValueError(
            f"{path}: tabel kosong. Tabel tanpa baris lebih mungkin berarti "
            f"proses CV di hulu gagal daripada berarti tidak ada string kotor."
        )

    df = df[CV_TABLE_COLUMNS].copy()
    df["inverter_id"] = df["inverter_id"].astype(str).str.strip()
    df["pv"] = pd.to_numeric(df["pv"], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    for kolom in ("soiling_index", "confidence"):
        df[kolom] = pd.to_numeric(df[kolom], errors="coerce")

    rusak = df[df[["pv", "date", "soiling_index", "confidence"]].isna().any(axis=1)]
    if not rusak.empty:
        raise ValueError(
            f"{path}: {len(rusak)} baris tak terbaca (pv/date/soiling_index/"
            f"confidence). Contoh indeks baris: {list(rusak.index[:5])}"
        )
    df["pv"] = df["pv"].astype(int)

    for kolom in ("soiling_index", "confidence"):
        di_luar = df[(df[kolom] < 0.0) | (df[kolom] > 1.0)]
        if not di_luar.empty:
            raise ValueError(
                f"{path}: {len(di_luar)} baris punya {kolom} di luar 0..1 "
                f"(min {df[kolom].min():.3g}, maks {df[kolom].max():.3g}). "
                f"Kontrak menuntut 0..1; skala lain membuat ambang severity "
                f"kehilangan makna tanpa memunculkan galat."
            )

    ganda = df.duplicated(subset=["inverter_id", "pv", "date"], keep=False)
    if ganda.any():
        contoh = df.loc[ganda, ["inverter_id", "pv", "date"]].head(3)
        raise ValueError(
            f"{path}: {int(ganda.sum())} baris duplikat untuk "
            f"(inverter_id, pv, date). Contoh:\n{contoh.to_string(index=False)}"
        )

    return df.reset_index(drop=True)


def _severity(
    index: float, medium: float, high: float, critical: float,
) -> Optional[Severity]:
    """Indeks -> severity; ``None`` bila di bawah ambang terendah.

    Di bawah ``medium`` sengaja TIDAK menghasilkan temuan. Memancarkan
    ``NORMAL`` untuk tiap string bersih akan membanjiri lembar temuan dengan
    ribuan baris per hari dan menenggelamkan yang benar-benar perlu dilihat.
    """
    if index >= critical:
        return Severity.CRITICAL
    if index >= high:
        return Severity.HIGH
    if index >= medium:
        return Severity.MEDIUM
    return None


class M2gVisualCV(SubModule):
    """Indeks soiling per string dari citra udara -> ``M2Finding``.

    Dependency injection (opsional), untuk pengujian dan notebook::

        sm = M2gVisualCV(cv_table=df, geom=geom_df)

    Peran ``combined_df``
    ---------------------
    Bukan sumber angka, melainkan penentu CAKUPAN WAKTU. Baris CV untuk tanggal
    atau inverter yang tidak ada di run ini ditolak, bukan dipancarkan. Tanpa
    itu, satu tabel CV lama akan menyuntikkan temuan bertanggal lama ke dalam
    run hari ini, dan tidak ada apa pun di hilir yang bisa mengetahuinya.
    """

    name: str = "M2g_visual_cv"

    def __init__(
        self,
        cv_table: Optional[pd.DataFrame] = None,
        geom: Optional[pd.DataFrame] = None,
    ):
        super().__init__()
        self.cv_table = cv_table
        self.geom = geom

    def _valid_strings(self, cfg: dict) -> Set[Tuple[str, int]]:
        """Pasangan (inverter_id, pv) yang sah, dari geometri as-built.

        Geometri yang jadi otoritas identitas, bukan telemetri: telemetri bisa
        kehilangan kanal untuk sementara (fiber putus, inverter mati) tanpa
        string itu berhenti ada.
        """
        if self.geom is None:
            path = cfg.get("string_geometry_path", DEFAULT_GEOMETRY_PATH)
            if not os.path.exists(path):
                raise ValueError(
                    f"{path}: geometri string tidak ada, jadi identitas baris "
                    f"CV tidak bisa diverifikasi."
                )
            self.geom = pd.read_csv(path, usecols=["inverter_id", "pv"])
        return {
            (str(r.inverter_id).strip(), int(r.pv))
            for r in self.geom.itertuples(index=False)
            if pd.notna(r.pv)
        }

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        cfg = config.get("m2g_visual_cv", {}) or {}
        if not bool(cfg.get("enabled", DEFAULT_ENABLED)):
            return []

        min_conf = float(cfg.get("min_confidence", DEFAULT_MIN_CONFIDENCE))
        medium = float(cfg.get("index_medium", DEFAULT_INDEX_MEDIUM))
        high = float(cfg.get("index_high", DEFAULT_INDEX_HIGH))
        critical = float(cfg.get("index_critical", DEFAULT_INDEX_CRITICAL))
        if not medium <= high <= critical:
            raise ValueError(
                f"m2g_visual_cv: ambang harus menaik, dapat medium={medium}, "
                f"high={high}, critical={critical}."
            )

        cv = self.cv_table
        if cv is None:
            cv = load_cv_table(cfg.get("table_path", DEFAULT_TABLE_PATH))

        if ("Inverter_ID" not in combined_df.columns
                or "Start Time" not in combined_df.columns):
            warnings.warn(
                "M2g_visual_cv: combined_df tanpa 'Inverter_ID'/'Start Time'; "
                "cakupan run tidak bisa ditentukan sehingga tidak ada temuan "
                "yang dipancarkan.",
                stacklevel=2,
            )
            return []

        cakupan = {
            (str(inv).strip(), ts)
            for inv, ts in zip(
                combined_df["Inverter_ID"],
                pd.to_datetime(
                    combined_df["Start Time"], errors="coerce",
                ).dt.normalize(),
            )
            if pd.notna(ts)
        }
        sah = self._valid_strings(cfg)

        findings: List[M2Finding] = []
        ditolak: List[dict] = []
        for row in cv.itertuples(index=False):
            inv, pv = str(row.inverter_id), int(row.pv)
            if (inv, pv) not in sah:
                ditolak.append({
                    "inverter_id": inv, "pv": pv, "date": row.date,
                    "alasan": "string tidak ada di geometri as-built",
                })
                continue
            if (inv, row.date) not in cakupan:
                ditolak.append({
                    "inverter_id": inv, "pv": pv, "date": row.date,
                    "alasan": "di luar cakupan tanggal/inverter run ini",
                })
                continue
            if float(row.confidence) < min_conf:
                ditolak.append({
                    "inverter_id": inv, "pv": pv, "date": row.date,
                    "alasan": (
                        f"confidence {float(row.confidence):.2f} "
                        f"< {min_conf:.2f}"
                    ),
                })
                continue

            sev = _severity(float(row.soiling_index), medium, high, critical)
            if sev is None:
                continue

            ambang = critical if sev is Severity.CRITICAL else (
                high if sev is Severity.HIGH else medium
            )
            findings.append(M2Finding(
                timestamp=row.date.to_pydatetime(),
                inverter_id=inv,
                pv_string=f"PV{pv}",
                sub_module=self.name,
                severity=sev,
                value=float(row.soiling_index),
                threshold=ambang,
                message=(
                    f"Indeks soiling citra {float(row.soiling_index):.2f} "
                    f"(ambang {ambang:.2f}); prioritas pembersihan, "
                    f"belum terkalibrasi ke rugi energi."
                ),
                confidence=float(row.confidence) * 100.0,
                evidence={
                    "source_image": str(row.source_image),
                    "cv_confidence": float(row.confidence),
                },
                extra={"pv": pv},
            ))

        if ditolak:
            self.artifacts["VisualCVRejected"] = pd.DataFrame(
                ditolak, columns=REJECTED_COLUMNS,
            )
        return findings
