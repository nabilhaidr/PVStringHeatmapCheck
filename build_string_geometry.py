"""Koordinat + kemiringan tanah PER STRING -> config/string_geometry.csv.

Sumber:
1. ``raw data input/1129.dxf`` -- export DXF dari 1129.dwg. Layer
   "String number" memuat 3.570 label ``WB##INV##ST##`` beserta titik
   sisipnya dalam WGS 84 / UTM zone 50S. Ini satu-satunya sumber koordinat
   per string yang tersedia; gambar PDF hanya memberi patok setting-out
   per petak.
2. ``dsm.tif`` survei topografi (0,1187 m/piksel) untuk elevasi di titik
   label dan bidang tanah lokal di bawah meja tiap string.
3. ``List of DC Cables 0411.xls`` untuk memetakan nomor ST (sisi lapangan)
   ke nomor PV Huawei (sisi telemetri), supaya artefak ini bisa di-join ke
   data monitoring.

Kenapa penting: foto lapangan 2026-08-06 dan foto drone memastikan meja PV
di WB03-WB10 MENGIKUTI kontur berbukit -- tidak diratakan di atas bench.
Karena itu kemiringan tanah di posisi sebuah string adalah orientasi bidang
modulnya, dan ``cross_slope_deg`` (komponen timur-barat) menentukan berapa
besar bias pagi-sore yang murni geometris pada perbandingan antar-sibling.

Jalankan: python build_string_geometry.py
"""
from __future__ import annotations

import csv
import math
import os
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

from build_site_layout import (
    BLOCK_GAP_M,
    dsm_path,
    find_raw,
    fit_plane,
    latlon_to_utm50s,
    open_dsm,
    sample_dsm,
    utm50s_to_latlon,
)

RAW_DIR = "raw data input"
DXF_NAME = "1129.dxf"
CABLE_NAME = "List of DC Cables 0411.xls"
EL_SURVEY_NAME = "all.csv"
OUT_PATH = os.path.join("config", "string_geometry.csv")

LABEL_RE = re.compile(r"^WB(\d{2})INV(\d{2})ST(\d+)$", re.IGNORECASE)
EL_PHASE_ONE_RE = re.compile(r"^S([12])(\d{2})_(\d+)$")
TEXT_ENTITIES = {"TEXT", "MTEXT", "ATTRIB"}
# Layer tapak meja: satu LWPOLYLINE per meja.
TABLE_LAYER_PREFIX = "array"             # 1129.dxf (WB03-WB10)
PHASE_ONE_TABLE_LAYER_PREFIX = "_INV_"   # DXF Cable Routing (WB01/WB02)
# Label yang tepat di garis tepi masih dihitung sebagai "di dalam" meja.
TABLE_TOL_M = 0.3

# --- Phase One (WB01/WB02) ----------------------------------------------------
# Sumbernya gambar tray AC, bukan gambar string: 7.840 entitas teks, hanya 900
# di layer di bawah ini. Tanpa penyaringan layer, dimensi BOQ dan teks kop
# gambar ikut terbaca.
PHASE_ONE_DXF_PREFIX = "Cable Routing"
PHASE_ONE_LAYER = "_TEXT_STRING"
PHASE_ONE_LABEL_RE = re.compile(r"^S([12])(\d{2})-(\d{2})$")
# Tiap label membawa kode format MTEXT di depan nilainya (mis.
# "\W1.23077x;S101-18"). Tanpa dibersihkan tidak satu pun dari 900 label
# cocok dengan polanya dan parser mengembalikan daftar KOSONG -- gagal diam.
MTEXT_FORMAT_RE = re.compile(r"\\[A-Za-z][^;\\]*;|[{}]")
# S226 sisa revisi gambar: sudah diubah menjadi S125 = WB01-INV25, tapi sheet
# tray AC masih membawa label lamanya. Dibaca apa adanya ia menjadi
# WB02-INV26 yang TIDAK ADA di telemetri, sekaligus meninggalkan WB01-INV25
# tanpa koordinat. Bukti spasial sejalan: S226 duduk di blok barat, terpisah
# ~99 m dari seluruh gugus S2xx.
PHASE_ONE_REVISED = {(2, 26): (1, 25)}
# WB01/WB02 memakai Huawei SUN2000-215KTL: 9 MPPT x 2 string berurutan.
# Tabel pasangannya TIDAK ditulis ulang di sini -- lihat phase_one_mppt_map().
PHASE_ONE_MODEL = "SUN2000-215KTL-H0"
STRINGS_YAML = os.path.join("config", "strings.yaml")

# --- koreksi penomoran inverter di 1129.dxf -----------------------------------
# Dua blok salah dinomori. Ditemukan lewat JUMLAH STRING per inverter yang
# dicocokkan ke as-built DC cable list -- bukti yang tidak bergantung koordinat
# sama sekali -- lalu dikuatkan susunan kolom pada General Layout DW-001.
#
# WB04 melewatkan INV17 lalu menggeser sisanya naik satu: label INV18 membawa
# 27 string sementara as-built INV18 punya 24 dan INV17 punya 27; ketiga
# pergeserannya cocok berurutan (27, 24, 23).
DXF_RELABEL = {
    (4, 18): (4, 17), (4, 19): (4, 18), (4, 20): (4, 19),
    # WB05 berhenti di INV19 baik menurut as-built maupun General Layout, jadi
    # label INV20 sepenuhnya milik WB06.
    (5, 20): (6, 20),
}
# Label INV15-INV19 di WB05 dipakai DUA KALI: sekali untuk array WB05, sekali
# untuk array WB06 ratusan meter di timurnya. Gugus TIMUR adalah WB06 dengan
# nomor inverter yang sama. Jumlah kedua sisi cocok persis dengan as-built pada
# keenam label, jadi pemisahannya memverifikasi dirinya sendiri.
DXF_SPLIT_EAST = {(5, i): (6, i) for i in range(15, 20)}
# Beberapa label muncul dua kali DI DALAM satu inverter. Salinan yang nyasar
# ternyata melanjutkan grid inverter LAIN -- dan inverter itu kekurangan ST yang
# sama persis menurut as-built, jadi kedua sisinya saling menutup. Diurutkan
# menaik karena ST14 baru bisa dijangkar setelah ST13 pindah.
DXF_STRAY = {
    (3, 11, 13): (3, 8, 13),
    (3, 11, 14): (3, 8, 14),
    (3, 11, 15): (3, 8, 15),
    (5, 14, 25): (6, 14, 25),
}
# --- koreksi penomoran ST di dalam satu inverter -------------------------------
# Dua inverter salah menomori STRING-nya, bukan inverternya. Kebenaran diambil
# dari dua sumber bebas yang sepakat -- List of DC Cables dan survei EL drone
# 2025 -- dengan kontrol positif: di WB06-INV05/07 dan WB10-INV02/04 ketiga
# sumber (DXF, as-built, EL) sepakat persis, jadi kedua inverter di bawah ini
# benar-benar menyimpang dan bukan derau metode.
#
# WB06-INV06 melompati ST02 lalu berjalan satu di depan sampai ST24, sementara
# as-built dan EL sama-sama berhenti di ST23. Nilai: (ST awal, geseran, jumlah
# string menurut as-built). Jumlahnya dipakai sebagai pagar: kalau gambar
# berubah dan cacahnya tak lagi cocok, koreksi tidak dijalankan.
DXF_ST_SHIFT = {(6, 6): (3, -1, 23)}
# WB10-INV03: string fisik ke-5 dilabeli "25", sehingga penomorannya berjalan
# satu di depan dan ST27 hilang -- meninggalkan ST25 GANDA. DXF_STRAY tidak
# menjangkaunya karena ia memindahkan salinan ke slot kosong, sedangkan slot
# tujuan di sini (ST05) sudah terisi. Penomoran ulang menurut urutan spasial
# memulihkan bijeksi tanpa bergantung pada nomor lama; cocok 27/27 dengan
# posisi EL. Urutan spasial sengaja TIDAK dipakai untuk WB06-INV06 -- di sana
# ia hanya cocok 4/23, jadi tata letaknya memang bukan raster sederhana.
DXF_RENUMBER_SPATIAL = {(10, 3): 27}

# --- penempatan string yang dibantah bukti lain -------------------------------
# Label DXF menaruh keempat inverter ini di lereng -- cross-slope sampai
# -15,4 deg -- dan tiga sumber bebas menyanggahnya:
#
#   survei EL : posisinya di tanah datar, |cs| <= 1,9 deg dan sd <= 0,69 (di
#               posisi DXF sd 1,24-5,53). Jarak ke titik geometri terdekat cuma
#               1,9-2,0 m, sementara baris DXF milik string itu sendiri 14-36 m
#               jauhnya -- seluruh gugus tergeser, bukan sebagian.
#   telemetri : Juni 2026, 72 string. r = -0,020 padahal model yang sama
#               mencapai +0,699 di WB03-10, dan sebaran asimetri terukur hanya
#               9-30% dari yang diprediksi. Sinyalnya TIDAK ADA, bukan teracak;
#               label yang tertukar di dalam satu inverter tidak bisa
#               menghasilkan itu.
#   kontrol   : WB05-INV03, WB07-INV04, WB03-INV06 memberi sd yang sama di
#               kedua posisi (8,26/8,17, 4,58/4,58, 9,07/9,07), jadi selisih di
#               atas bukan artefak metode.
#
# Sampai 15 Agu 2026 kolom bidangnya dikosongkan, karena yang terbukti hanya
# "DXF salah" dan bukan "EL benar". Open Question 8 mencabut dasar itu: di
# posisi EL medannya dibaca ULANG dari dsm.tif dan ternyata rata seperti sisa
# Phase One (|cs| median 1,34 maks 3,29 deg), sementara posisi DXF menaruh
# keempatnya di lereng -15,4 deg. Karena itu ke-72 string PINDAH ke koordinat
# survei EL, dan kolom bidangnya diisi di posisi itu.
#
# Penerapan pertamanya (commit 22b059e) lewat skrip sekali pakai LANGSUNG ke
# CSV, sehingga builder ini memulangkannya ke posisi DXF setiap kali dijalankan
# -- tanpa satu pun galat, ke-72 string hanya jatuh diam-diam ke TIDAK_BERLAKU.
# Sejak 18 Sep 2026 posisinya dibaca dari survei EL di sini, dan hilangnya
# berkas survei itu menghentikan builder alih-alih menerbitkan posisi DXF.
#
# Gugus WB01 (13 inverter, 18 Sep 2026). Uji awan 16 Agu memisahkan hanya
# gelombang 2 (INV21/18/25/01, memihak EL dua kali); gelombang 3 dan 4 seri,
# dan PRD menahan seluruh gugus karena memindah sebagian menaikkan tabrakan
# < 3 m dari 14 ke 68. Empat jalur bebas kini memihak EL, satu di tiap
# gelombang:
#   label fisik : stempel S107-04/05 di rel difoto ber-GPS di posisi EL,
#                 24 m dari DXF (INV07, gelombang 4).
#   log cleaning: kru mencatat string menurut label fisik; 12 Sep 19 string
#                 INV20/21 dicuci = 19 meja biru di foto drone 0216. Posisi EL
#                 cocok 14/14, posisi DXF 13/29 -- setara menebak (INV20
#                 gelombang 3, INV21 gelombang 2).
#   persegi kosong: 11 persegi DXF gugus tidak kebagian satu pun titik EL; 7
#                 yang tertangkap kamera (0216/0217) semuanya berisi rumput.
#   meja tak tergambar: 6 titik EL di luar persegi DXF mana pun mendarat di
#                 meja fisik tanpa poligon di foto 0217.
# Titik EL gugus hanya jatuh di persegi milik gugus itu sendiri, jadi inverter
# WB01 lain tidak tersentuh.
#
# Gugus WB02 utuh (19 Sep 2026): INV03/07/08 menyusul keempat OQ8. Titik EL
# ketujuhnya jatuh di persegi milik ketujuhnya sendiri (114 dari 126; 11 di
# luar semua persegi, 1 di persegi INV05), jadi OQ8 memindah empat dari satu
# gugus -- 12 string OQ8 tidak bisa mendapat meja karena meja itu masih
# diduduki INV03/07/08 di posisi DXF. Foto 9-15 Sep (arsip F:): 12 persegi DXF
# tanpa titik EL bermedian pecahan modul 0,19 lawan 0,59 pada persegi berisi
# titik EL di bingkai yang sama; 6 titik EL di luar persegi mendarat di meja
# fisik yang tidak tergambar (bingkai hampir nadir 0100). INV05 sepakat dengan
# EL (median <= 2 m) dan tidak dipindah.
PLACEMENT_FROM_EL = {f"WB02-INV{i:02d}" for i in (1, 2, 3, 4, 6, 7, 8)} | {
    f"WB01-INV{i:02d}" for i in (1, 2, 3, 6, 7, 8, 12, 13, 18, 19, 20, 21, 25)
}

# --- dua ST satu kanal PV di as-built -----------------------------------------
# Delapan inverter mencatat dua ST berbeda pada SATU kanal PV (Koreksi As-Built
# butir 2.1). Di tiap inverter itu telemetri memperlihatkan tepat satu kanal
# yang berarus tetapi tidak diklaim ST mana pun, dan cacah kanal berarus sama
# dengan cacah string -- jadi salah satu dari kedua ST pasti duduk di kanal itu.
# Yang tersisa hanya: ST yang mana.
#
# Diputuskan hanya bila ada bukti kedua yang bebas dari pola urutan:
#   WB04-INV01/06  baris ST23/ST18 membantah dirinya sendiri (M1PV6/M1PV5,
#                  padahal PV5-PV6 milik MPPT2); baris pasangannya konsisten.
#   WB10-INV02     ST17 tercatat MPPT2 -- itu MPPT kanal bebas PV5, bukan PV10.
#   WB04-INV01/08/10  asimetri pagi-sore terukur cocok dengan cross-slope ST
#                  yang diusulkan di setiap hari yang tersedia (3-4 hari,
#                  Des 2025-Jul 2026); tukarannya selalu kalah.
# WB04-INV08 diganti unit baru ("WB04-INV08-NEW") setelah Des 2025 dan kanal
# bebasnya pindah PV5 -> PV9. Nilai di sini mengikuti unit yang terpasang
# sekarang, sama seperti strings.yaml.
#
# Tiga sisanya hanya didukung pola urutan, dan itu bukan bukti: KEDUA ST
# dikosongkan sampai EPC menjawab. Kuncinya nilai as-built yang dipersengketakan,
# jadi cable list revisi yang menulis nilai lain tidak ditimpa diam-diam.
ASBUILT_PV_FIX = {
    # (wb, inv, st): ((pv, mppt) tercatat, (pv, mppt) dipakai)
    (4, 1, 23): ((6, 1), (26, 6)),
    (4, 6, 18): ((5, 1), (25, 6)),
    (4, 8, 12): ((2, 1), (9, 2)),
    (4, 10, 25): ((14, 3), (27, 6)),
    (10, 2, 17): ((10, 2), (5, 2)),
    (4, 19, 19): ((16, 4), (None, None)),
    (4, 19, 23): ((16, 4), (None, None)),
    (5, 19, 6): ((6, 2), (None, None)),
    (5, 19, 14): ((6, 2), (None, None)),
    (7, 17, 3): ((25, 6), (None, None)),
    (7, 17, 10): ((25, 6), (None, None)),
    # Digit MPPT yang membantah kanal PV-nya sendiri. Satu kanal hanya duduk di
    # satu MPPT, jadi salah satu kolom pasti keliru; telemetri 1 Des 2025 dan
    # 13 Mei 2026 memilih MPPT-nya. Di tiap inverter ini cacah kanal berarus =
    # cacah ST, kanal PV tercatat berarus dan hanya diklaim ST itu, dan semua
    # kanal berarus di MPPT tercatat sudah dipakai ST lain -- memindahkan
    # string ke sana butuh kesalahan kedua. Tegangan kanalnya selalu sama
    # (<= 0,1 V) dengan saudara se-MPPT menurut strings.yaml, bukan menurut
    # as-built. PV dipertahankan; MPPT mengikuti peta perangkat keras.
    (4, 3, 4): ((6, 1), (6, 2)),
    (4, 3, 15): ((7, 1), (7, 2)),
    (4, 4, 17): ((17, 2), (17, 4)),
    (4, 6, 2): ((2, 2), (2, 1)),
    (4, 7, 5): ((5, 1), (5, 2)),
    (4, 10, 19): ((22, 4), (22, 5)),
    (4, 10, 20): ((21, 4), (21, 5)),
    (4, 10, 21): ((16, 3), (16, 4)),
    (4, 10, 22): ((18, 3), (18, 4)),
    (4, 16, 13): ((17, 3), (17, 4)),
    (4, 16, 14): ((18, 3), (18, 4)),
    (5, 4, 19): ((15, 3), (15, 4)),
    (5, 4, 20): ((17, 3), (17, 4)),
    (5, 6, 23): ((23, 4), (23, 5)),
    (5, 7, 4): ((5, 1), (5, 2)),
    # Konduktor ST13+ menulis M3PV11, ST13- menulis M1PV11; build_st_to_pv
    # meneruskan baris terakhir. Bukan baris tersalin ganda (butir 2.3).
    (5, 7, 13): ((11, 1), (11, 3)),
    (7, 14, 8): ((16, 6), (16, 4)),
}

# Jendela fit bidang di bawah MEJA: 15 m timur-barat (panjang satu meja)
# x 4 m utara-selatan, langkah 0,5 m. Cukup lebar untuk meredam kekasaran
# tanah, cukup sempit untuk tetap mewakili meja itu sendiri.
WIN_EW_M = 7.5
WIN_NS_M = 2.0
WIN_STEP_M = 0.5
# rms di atas ini = permukaan tidak cukup planar (vegetasi/tanah kasar).
MAX_PLANE_RMS_M = 0.5
# Titik label di sepanjang meja, pecahan panjang dari ujung barat (0 = ujung
# barat, 0,5 = pusat). Foto drone 12 September: label 1129.dxf di WB03-WB10
# ada di ujung barat meja, label DXF Cable Routing WB01-WB02 di pusatnya
# (cv-drone-plts docs/uji_segmentasi_nyata_12sep.md). Jendela yang berpusat di
# label WB03-WB10 separuhnya jatuh di barat meja.
LABEL_POSITION = {1: 0.5, 2: 0.5}
LABEL_POSITION_DEFAULT = 0.0
# Panjang meja timur-barat: 12 kolom modul portrait di WB01-WB02 (24 modul per
# string), 13 di WB03-WB10 (26); modul 1,134 m, celah 0,02 m.
TABLE_LENGTH_M = {1: 12 * 1.134 + 11 * 0.02, 2: 12 * 1.134 + 11 * 0.02}
TABLE_LENGTH_DEFAULT_M = 13 * 1.134 + 12 * 0.02

COLUMNS = [
    "inverter_id", "st", "pv", "mppt", "north", "east", "lat", "lon",
    "elev_m", "slope_deg", "aspect_deg", "cross_slope_deg", "plane_rms_m",
    "table_east", "table_north", "table_lat", "table_lon",
]


def _iter_dxf_entities(path: str, kinds):
    """Streaming entitas DXF -> {kind, label, layer, pts: [[east, north], ...]}.

    Per pasangan (kode, nilai) karena DXF hasil export bisa ratusan MB --
    gambar tray AC yang satu itu 337 MB. Kode 8 = layer, 10 = easting,
    20 = northing; polyline mengulang 10/20 per simpul.
    """
    cur: Optional[Dict] = None
    code: Optional[str] = None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if code is None:
                token = line.strip()
                code = token if token.lstrip("-").isdigit() else None
                continue
            value, current_code, code = line.rstrip("\r\n"), code, None
            if current_code == "0":
                if cur:
                    yield cur
                kind = value.strip().upper()
                cur = {"kind": kind, "pts": []} if kind in kinds else None
            elif cur is not None:
                if current_code == "1":
                    cur["label"] = value.strip()
                elif current_code == "8":
                    cur["layer"] = value.strip()
                elif current_code == "10":
                    cur["pts"].append([float(value), None])
                elif current_code == "20" and cur["pts"] and cur["pts"][-1][1] is None:
                    cur["pts"][-1][1] = float(value)
    if cur:
        yield cur


def _iter_dxf_text(path: str):
    """Streaming entitas teks DXF -> {label, layer, east, north}."""
    for ent in _iter_dxf_entities(path, TEXT_ENTITIES):
        out = {k: ent[k] for k in ("label", "layer") if k in ent}
        if ent["pts"] and ent["pts"][0][1] is not None:
            out["east"], out["north"] = ent["pts"][0]
        yield out


def parse_dxf_tables(path: str, layer_prefix: str) -> List[Tuple[float, ...]]:
    """Tapak meja -> [(east_min, east_max, north_min, north_max), ...].

    Satu LWPOLYLINE per meja: 14,95 x 4,87 m di semua 3.570 meja 1129.dxf
    (4,87 = 4,95 m tampak-atas pada tilt 10 derajat), 14,40 x 4,77 m di 900
    meja DXF Cable Routing. Dipakai karena TITIK LABEL tidak konsisten
    letaknya di dalam meja, sedangkan persegi ini konsisten.
    """
    rects: List[Tuple[float, ...]] = []
    for ent in _iter_dxf_entities(path, {"LWPOLYLINE"}):
        if not ent.get("layer", "").startswith(layer_prefix):
            continue
        pts = [p for p in ent["pts"] if p[1] is not None]
        if len(pts) < 4:
            continue
        easts = [p[0] for p in pts]
        norths = [p[1] for p in pts]
        rects.append((min(easts), max(easts), min(norths), max(norths)))
    return rects


def attach_table_centers(labels: List[Dict],
                         tables: List[Tuple[float, ...]]) -> List[Dict]:
    """Isi ``table_east``/``table_north`` dari persegi meja yang MEMUAT label.

    Label tanpa persegi yang memuatnya dibiarkan TANPA kedua kolom itu, dan
    ``_geom_row`` jatuh ke rumus pecahan. 13 label memang di luar setiap
    persegi; enam di antaranya 5,6-9,1 m jauhnya (WB08-INV06/INV07) dan
    persegi terdekatnya sudah dipakai label lain -- men-snap ke yang terdekat
    berarti menebak meja milik string lain lalu menerbitkannya sebagai
    koordinat.
    """
    bucket: Dict[int, List[Tuple[float, ...]]] = {}
    for t in tables:
        for k in range(int((t[2] - TABLE_TOL_M) // 10),
                       int((t[3] + TABLE_TOL_M) // 10) + 1):
            bucket.setdefault(k, []).append(t)
    for item in labels:
        east, north = item["east"], item["north"]
        for t in bucket.get(int(north // 10), ()):
            if (t[0] - TABLE_TOL_M <= east <= t[1] + TABLE_TOL_M
                    and t[2] - TABLE_TOL_M <= north <= t[3] + TABLE_TOL_M):
                item["table_east"] = (t[0] + t[1]) / 2.0
                item["table_north"] = (t[2] + t[3]) / 2.0
                break
    return labels


def el_survey_positions(path: str,
                        inverter_ids) -> Dict[Tuple[str, int], Tuple[float, float]]:
    """(inverter_id, st) -> (north, east) rata-rata modul di survei EL.

    Hanya untuk ``inverter_ids`` yang diminta. Dua jebakan berkasnya ditangani
    di sini karena keduanya gagal DIAM-DIAM: header data duduk di bawah
    preambel ambang rating yang bergerigi, dan nama kolomnya berawalan spasi.
    """
    total: Dict[Tuple[str, int], List[float]] = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        for line in handle:
            if line.startswith("#String"):
                header = [c.strip() for c in next(csv.reader([line]))]
                break
        else:
            return {}
        for row in csv.DictReader(handle, fieldnames=header):
            kunci = _el_key(row.get("#String", ""))
            if kunci is None or kunci[0] not in inverter_ids:
                continue
            acc = total.setdefault(kunci, [0.0, 0.0, 0])
            acc[0] += float(row["Latitude"])
            acc[1] += float(row["Longitude"])
            acc[2] += 1
    return {k: latlon_to_utm50s(lat / n, lon / n)
            for k, (lat, lon, n) in total.items()}


def _el_key(label: str) -> Optional[Tuple[str, int]]:
    """Label survei EL -> (inverter_id, st). Dua konvensi dalam satu berkas."""
    label = label.strip()
    phase_one = EL_PHASE_ONE_RE.match(label)
    match = phase_one or LABEL_RE.match(label)
    if not match:
        return None
    wb, inv, st = match.groups()
    return f"WB{int(wb):02d}-INV{int(inv):02d}", int(st)


def relocate_to_el_survey(
        labels: List[Dict],
        positions: Dict[Tuple[str, int], Tuple[float, float]]) -> List[Dict]:
    """Pindahkan string ``PLACEMENT_FROM_EL`` ke koordinat survei EL.

    Yang dipindah hanya string yang penempatan DXF-nya dibantah; ``positions``
    boleh memuat lebih banyak tanpa menyentuh string lain.
    """
    for item in labels:
        kunci = (f"WB{item['wb']:02d}-INV{item['inv']:02d}", item["st"])
        if kunci[0] not in PLACEMENT_FROM_EL or kunci not in positions:
            continue
        item["north"], item["east"] = positions[kunci]
        item["dari_el"] = True
        item.pop("table_east", None)
        item.pop("table_north", None)
    return labels


def attach_relocated_table_centers(items: List[Dict],
                                   tables: List[Tuple[float, ...]]) -> List[Dict]:
    """Pusat meja untuk seluruh string satu gambar, sesudah relokasi EL.

    String yang tidak dipindah mengambil persegi yang memuat labelnya lebih
    dulu. String yang dipindah (``dari_el``) hanya boleh mengambil persegi yang
    tersisa, dan hanya bila titik EL-nya jatuh di dalamnya. Dua string di satu
    persegi adalah tabrakan; memilih salah satunya berarti menebak, jadi yang
    dipindah dibiarkan di titik EL tanpa pusat meja -- begitu pula titik EL di
    luar semua persegi, yang di gugus WB01 terbukti menandai meja fisik yang
    tidak tergambar di DXF.
    """
    def pusat(t):
        return (t[0] + t[1]) / 2.0, (t[2] + t[3]) / 2.0

    tetap = [i for i in items if not i.get("dari_el")]
    pindah = [i for i in items if i.get("dari_el")]
    attach_table_centers(tetap, tables)
    dipakai = {(i["table_east"], i["table_north"]) for i in tetap if "table_east" in i}
    attach_table_centers(pindah, [t for t in tables if pusat(t) not in dipakai])
    ganda = Counter((i["table_east"], i["table_north"]) for i in pindah if "table_east" in i)
    for item in pindah:
        if ganda.get((item.get("table_east"), item.get("table_north")), 0) > 1:
            del item["table_east"], item["table_north"]
    return items


def parse_dxf_string_labels(path: str) -> List[Dict]:
    """Label string WB03-WB10 -> [{label, wb, inv, st, north, east}, ...].

    Label tanpa titik sisip dilewati: memberinya koordinat 0 akan menaruh
    string di lepas pantai Afrika tanpa ada yang menyadari.
    """
    rows: List[Dict] = []
    for entry in _iter_dxf_text(path):
        match = LABEL_RE.match(entry.get("label", ""))
        if not match or "east" not in entry or "north" not in entry:
            continue
        rows.append({
            "label": entry["label"],
            "wb": int(match.group(1)),
            "inv": int(match.group(2)),
            "st": int(match.group(3)),
            "east": entry["east"],
            "north": entry["north"],
        })
    return rows


def parse_phase_one_labels(path: str) -> List[Dict]:
    """Label WB01/WB02 -> [{label, wb, inv, st, pv, mppt, north, east}, ...].

    Konvensi Phase One (ISPP/Tractebel) berbeda sama sekali dari
    ``WB##INV##ST##`` milik SEPEC: ``S<blok><inv>-<st>``, blok 1 = WB01,
    blok 2 = WB02.

    ``pv`` diisi dari ``st`` karena di sini nomor ST lapangan ADALAH kanal
    PV Huawei. Itu sebabnya Phase One tidak butuh as-built DC cable list
    sama sekali, berbeda dengan WB03-WB10 yang pemetaan ST->PV-nya hanya
    ada di sana. MPPT-nya menyusul lewat ``phase_one_mppt_map()``.
    """
    rows: List[Dict] = []
    for entry in _iter_dxf_text(path):
        if entry.get("layer") != PHASE_ONE_LAYER:
            continue
        label = MTEXT_FORMAT_RE.sub("", entry.get("label", "")).strip()
        match = PHASE_ONE_LABEL_RE.match(label)
        if not match or "east" not in entry or "north" not in entry:
            continue
        wb, inv = int(match.group(1)), int(match.group(2))
        wb, inv = PHASE_ONE_REVISED.get((wb, inv), (wb, inv))
        st = int(match.group(3))
        rows.append({
            "label": label, "wb": wb, "inv": inv, "st": st, "pv": st,
            "east": entry["east"], "north": entry["north"],
        })
    return rows


def resolve_dxf_relabels(labels: List[Dict]) -> List[Dict]:
    """Perbaiki penomoran inverter yang salah di 1129.dxf.

    Dua cacat berbeda, dua penanganan berbeda:

    * ``DXF_RELABEL`` -- seluruh label memang milik inverter lain (WB04 yang
      penomorannya bergeser, dan WB05-INV20 yang tidak pernah ada).
    * ``DXF_SPLIT_EAST`` -- satu label dipakai dua array berbeda. Gugus TIMUR
      pindah blok; gugus barat tetap. Pemisahnya celah easting terbesar,
      dan hanya diterima bila celah itu melebihi ``BLOCK_GAP_M`` -- ambang
      pemisah petak yang sudah diturunkan dari histogram jarak patok. Celah
      nyatanya 71-334 m, jauh di atas lebar satu inverter (~50 m).
    * ``DXF_ST_SHIFT`` dan ``DXF_RENUMBER_SPATIAL`` -- inverternya benar,
      penomoran STRING-nya yang salah. Keduanya dipagari jumlah string
      menurut as-built: cacah tidak cocok berarti gambarnya berubah, dan
      koreksi diam-diam pada input yang sudah lain jauh lebih berbahaya
      daripada membiarkan cacat lama terlihat.
    """
    keluar = [dict(row) for row in labels]
    for row in keluar:
        baru = DXF_RELABEL.get((row["wb"], row["inv"]))
        if baru:
            row["wb"], row["inv"] = baru

    for kunci, baru in DXF_SPLIT_EAST.items():
        grup = [r for r in keluar if (r["wb"], r["inv"]) == kunci]
        if len(grup) < 2:
            continue
        grup.sort(key=lambda r: r["east"])
        jarak = [grup[i + 1]["east"] - grup[i]["east"] for i in range(len(grup) - 1)]
        lebar = max(jarak)
        if lebar <= BLOCK_GAP_M:
            continue
        for row in grup[jarak.index(lebar) + 1:]:
            row["wb"], row["inv"] = baru

    for (wb, inv, st), (wb2, inv2, st2) in DXF_STRAY.items():
        salinan = [r for r in keluar if (r["wb"], r["inv"], r["st"]) == (wb, inv, st)]
        acuan = _tetangga_tunggal(keluar, wb2, inv2, st2)
        if len(salinan) != 2 or acuan is None:
            continue
        nyasar = min(salinan, key=lambda r: math.hypot(r["east"] - acuan["east"],
                                                       r["north"] - acuan["north"]))
        nyasar["wb"], nyasar["inv"], nyasar["st"] = wb2, inv2, st2

    for (wb, inv), (mulai, geser, jumlah) in DXF_ST_SHIFT.items():
        grup = [r for r in keluar if (r["wb"], r["inv"]) == (wb, inv)]
        if len(grup) != jumlah:
            continue
        for row in grup:
            if row["st"] >= mulai:
                row["st"] += geser

    for (wb, inv), jumlah in DXF_RENUMBER_SPATIAL.items():
        grup = [r for r in keluar if (r["wb"], r["inv"]) == (wb, inv)]
        if len(grup) != jumlah:
            continue
        grup.sort(key=lambda r: (-r["north"], r["east"]))
        for nomor, row in enumerate(grup, start=1):
            row["st"] = nomor
    return keluar


def _tetangga_tunggal(rows: List[Dict], wb: int, inv: int, st: int) -> Optional[Dict]:
    """String bernomor bersebelahan pada inverter tujuan, bila tidak ambigu.

    Dipakai sebagai jangkar grid: slot yang ditinggalkan as-built pasti
    bersebelahan dengan ST sebelum atau sesudahnya.
    """
    for tetangga in (st - 1, st + 1):
        cocok = [r for r in rows if (r["wb"], r["inv"], r["st"]) == (wb, inv, tetangga)]
        if len(cocok) == 1:
            return cocok[0]
    return None


def empty_pv_channels(path: str = STRINGS_YAML) -> Dict[str, set]:
    """``inverter_id`` -> kanal PV yang KOSONG by design, dari strings.yaml."""
    import yaml

    with open(path, encoding="utf-8") as handle:
        peta = yaml.safe_load(handle)["empty_pv_map"]
    return {inv: set(kanal) for inv, kanal in peta.items()}


def disprove_empty_channel(inverter_id: str, pv, mppt, kosong: Dict[str, set]):
    """Gugurkan pemetaan as-built yang mendarat di kanal kosong by design.

    Telemetri 13 Mei 2026 memutuskan perselisihan ini: delapan kanal semacam
    itu membaca 0,00 kW di tengah hari sementara kanal terpakai pada inverter
    yang sama berjalan 3,1-3,8 kW, dan seluruh kanal lain yang ditandai kosong
    di sana juga nol. strings.yaml benar; as-built keliru di titik ini.

    Menyimpan ``pv`` yang sudah terbantah lebih buruk daripada mengosongkannya:
    ia membuat artefak geometri menunjuk kanal yang tidak pernah menghasilkan
    apa pun, dan pembacanya tidak punya cara tahu itu sudah diuji dan gugur.
    """
    if pv is not None and pv in kosong.get(inverter_id, ()):
        return None, None
    return pv, mppt


def fix_asbuilt_pv(key, pv, mppt):
    """Terapkan ``ASBUILT_PV_FIX`` pada (pv, mppt) as-built string ``key``.

    Hanya berlaku selama as-built masih mencatat nilai yang dipersengketakan.
    """
    fix = ASBUILT_PV_FIX.get(key)
    if fix is None or (pv, mppt) != fix[0]:
        return pv, mppt
    return fix[1]


def phase_one_mppt_map(path: str = STRINGS_YAML) -> Dict[int, int]:
    """PV -> MPPT untuk WB01/WB02, dibaca dari ``config/strings.yaml``.

    Fakta ini sudah dimiliki repo, berkunci model inverter, dan strings.yaml
    adalah acuan yang tervalidasi telemetri. Menyalin pasangannya ke builder
    ini akan membuat dua rumah untuk satu fakta yang sama.
    """
    import yaml

    with open(path, encoding="utf-8") as handle:
        entry = yaml.safe_load(handle)["mppt_map"][PHASE_ONE_MODEL]
    return {pv: mppt for mppt, pvs in entry["mppt"].items() for pv in pvs}


def cross_slope_deg(slope_deg: float, aspect_deg: float) -> float:
    """Komponen timur-barat dari kemiringan tanah, BERTANDA.

    Positif = tanah turun ke TIMUR = bidang modul condong ke timur = pagi
    lebih kuat. Komponen utara-selatan sengaja diabaikan: struktur DW-003
    menyediakan varian lereng utara/selatan yang menjaga tilt 10 derajat,
    jadi hanya komponen menyamping yang memutar bidang modul.
    """
    return math.degrees(math.atan(
        math.tan(math.radians(slope_deg)) * math.sin(math.radians(aspect_deg))
    ))


def table_center_east(wb: int, east: float) -> float:
    """Easting pusat meja dari easting titik label (sumbu meja timur-barat)."""
    posisi = LABEL_POSITION.get(wb, LABEL_POSITION_DEFAULT)
    return east + (0.5 - posisi) * TABLE_LENGTH_M.get(wb, TABLE_LENGTH_DEFAULT_M)


def local_plane(image, header, north: float, east: float) -> Optional[Dict]:
    """Fit bidang tanah pada jendela seukuran meja yang berpusat di (north, east)."""
    samples = []
    n_steps = int(round(WIN_NS_M / 1.0))
    e_steps = int(round(WIN_EW_M / WIN_STEP_M))
    for i in range(-n_steps, n_steps + 1):
        for j in range(-e_steps, e_steps + 1):
            north_i, east_j = north + i * 1.0, east + j * WIN_STEP_M
            z = sample_dsm(image, header, north_i, east_j)
            if z is not None:
                samples.append((north_i, east_j, z))
    return fit_plane(samples)


def _st_to_pv() -> Dict:
    """(wb, inv, st) -> (pv, mppt) dari as-built DC cable list. {} bila absen."""
    cable_path = find_raw(CABLE_NAME, required=False)
    if cable_path is None:
        print(f"[string-geometry] cable list tidak ada ({CABLE_NAME}); "
              f"kolom pv/mppt dikosongkan.")
        return {}
    from pv_pipeline.m2a.cleaning_report import build_st_to_pv, load_dc_cable_map

    return build_st_to_pv(load_dc_cable_map(cable_path))


def _geom_row(item: Dict, image, header, pv, mppt) -> Dict:
    """Satu baris string_geometry.csv dari label + DSM.

    ``elev_m`` adalah elevasi DI titik label; bidang tanah difit di bawah meja
    -- pusat meja dari persegi DXF bila diketahui (``attach_table_centers``),
    kalau tidak dari rumus pecahan (``table_center_east``).
    """
    inverter_id = f"WB{item['wb']:02d}-INV{item['inv']:02d}"
    lat, lon = utm50s_to_latlon(item["north"], item["east"])
    center_east = item.get("table_east", table_center_east(item["wb"], item["east"]))
    center_north = item.get("table_north", item["north"])
    plane = local_plane(image, header, center_north, center_east)
    clean = plane is not None and plane["rms_m"] <= MAX_PLANE_RMS_M
    # Pusat meja ditulis dalam KEDUA satuan, seperti titik label: UTM untuk
    # kerja spasial, lat/lon untuk ``tapak`` di cv-drone-plts.
    punya_meja = "table_east" in item
    table_lat, table_lon = (utm50s_to_latlon(center_north, center_east)
                            if punya_meja else (None, None))
    return {
        "inverter_id": inverter_id,
        "st": item["st"],
        "pv": pv,
        "mppt": mppt,
        "north": round(item["north"], 3),
        "east": round(item["east"], 3),
        "lat": round(lat, 7),
        "lon": round(lon, 7),
        "elev_m": sample_dsm(image, header, item["north"], item["east"]),
        "slope_deg": plane["slope_deg"] if clean else None,
        "aspect_deg": plane["aspect_deg"] if clean else None,
        "cross_slope_deg": (
            round(cross_slope_deg(plane["slope_deg"], plane["aspect_deg"]), 2)
            if clean else None
        ),
        "plane_rms_m": plane["rms_m"] if plane else None,
        "table_east": round(center_east, 3) if punya_meja else None,
        "table_north": round(center_north, 3) if punya_meja else None,
        "table_lat": round(table_lat, 7) if punya_meja else None,
        "table_lon": round(table_lon, 7) if punya_meja else None,
    }


def main() -> None:
    dxf_path = find_raw(DXF_NAME)
    labels = resolve_dxf_relabels(parse_dxf_string_labels(dxf_path))
    if not labels:
        raise SystemExit(f"{dxf_path}: tidak ada label string ditemukan.")
    print(f"[string-geometry] {dxf_path}: {len(labels)} label string")

    tables = parse_dxf_tables(dxf_path, TABLE_LAYER_PREFIX)
    attach_table_centers(labels, tables)
    n_meja = sum(1 for item in labels if "table_east" in item)
    print(f"[string-geometry] {dxf_path}: {len(tables)} persegi meja; "
          f"label bermeja {n_meja}/{len(labels)}")

    dsm_file = dsm_path()
    image, header = open_dsm(dsm_file)
    if image is None:
        raise SystemExit(f"DSM tidak ada: {dsm_file}")
    st_map = _st_to_pv()

    kosong = empty_pv_channels()

    def _kanal(item: Dict):
        kunci = (item["wb"], item["inv"], item["st"])
        pv, mppt = fix_asbuilt_pv(kunci, *st_map.get(kunci, (None, None)))
        return disprove_empty_channel(
            f"WB{item['wb']:02d}-INV{item['inv']:02d}", pv, mppt, kosong,
        )

    rows: List[Dict] = [_geom_row(item, image, header, *_kanal(item))
                        for item in labels]

    # Phase One (WB01/WB02) datang dari gambar tray AC: pv = st, dan MPPT dari
    # strings.yaml. Blok ini karena itu tidak menyentuh as-built cable list
    # sama sekali -- satu-satunya bagian site yang pemetaan kanalnya diketahui
    # penuh tanpanya.
    phase_one_path = find_raw(PHASE_ONE_DXF_PREFIX, required=False)
    if phase_one_path:
        phase_one = parse_phase_one_labels(phase_one_path)
        mppt_by_pv = phase_one_mppt_map()
        print(f"[string-geometry] {phase_one_path}: "
              f"{len(phase_one)} label Phase One")
        # Berkas survei EL hilang -> BERHENTI. Meneruskannya akan menerbitkan
        # gugus WB01 dan tepi utara WB02 di posisi DXF yang sudah dibantah, dan
        # tidak ada kolom yang memperlihatkan bedanya.
        el_path = find_raw(EL_SURVEY_NAME, required=False)
        if el_path is None:
            raise SystemExit(
                f"survei EL ({EL_SURVEY_NAME}) tidak ada di bawah {RAW_DIR!r}; "
                f"{len(PLACEMENT_FROM_EL)} inverter Phase One butuh posisinya."
            )
        posisi_el = el_survey_positions(el_path, PLACEMENT_FROM_EL)
        pindah = relocate_to_el_survey(phase_one, posisi_el)
        attach_relocated_table_centers(
            pindah, parse_dxf_tables(phase_one_path, PHASE_ONE_TABLE_LAYER_PREFIX),
        )
        n_el = sum(1 for item in pindah if item.get("dari_el"))
        n_el_meja = sum(1 for item in pindah if item.get("dari_el") and "table_east" in item)
        print(f"[string-geometry] {el_path}: {n_el} string pindah ke posisi EL "
              f"({len(PLACEMENT_FROM_EL)} inverter yang penempatannya dibantah); "
              f"{n_el_meja} mendapat persegi meja, {n_el - n_el_meja} tetap di titik EL")
        rows += [_geom_row(item, image, header, item["pv"],
                           mppt_by_pv.get(item["pv"]))
                 for item in phase_one]
    else:
        print(f"[string-geometry] {PHASE_ONE_DXF_PREFIX}*.dxf tidak ada -> "
              f"WB01/WB02 tanpa koordinat")

    rows.sort(key=lambda r: (r["inverter_id"], r["st"]))
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    n_pv = sum(1 for r in rows if r["pv"] is not None)
    n_plane = sum(1 for r in rows if r["cross_slope_deg"] is not None)
    print(f"[string-geometry] pv termapping : {n_pv}/{len(rows)}")
    print(f"[string-geometry] bidang bersih : {n_plane}/{len(rows)} "
          f"(rms <= {MAX_PLANE_RMS_M} m)")
    print(f"[string-geometry] ditulis: {OUT_PATH} "
          f"({os.path.getsize(OUT_PATH) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
