# Penerapan Koreksi POA di Loader — Rencana Implementasi

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `PyranometerLoader` menerapkan blok `pyranometer.koreksi` (faktor per periode, jam penghalang, WS
dikecualikan) sehingga semua pemakai POA terkoreksi, sementara kalibrasi silang tetap membaca mentah.

**Architecture:** Koreksi diterapkan sekali di konstruktor loader sesudah offset waktu; `avg` dihitung ulang dari WS
terkoreksi. `from_geometry_yaml` dan `run_derate_calibration._muat_poa` membaca blok dari geometri;
`run_poa_cross_calibration.py` memanggil `_muat_poa(..., koreksi=False)`.

**Tech Stack:** Python, pandas, numpy, pytest. Spesifikasi:
`docs/superpowers/specs/2026-10-02-penerapan-koreksi-poa-loader-design.md`.

## Global Constraints

- Tanpa blok atau `aktif: false` → perilaku loader identik dengan sekarang.
- `config/site_geometry.yaml` TIDAK diubah dalam rencana ini (isi koreksi menunggu keputusan pengguna).
- Faktor dalam (0,8; 1,25]; tanggal inklusif hari penuh; `akhir: null` = terbuka.
- Uji hanya memakai data sintetis. Suite lengkap dijalankan sendirian (memori).

---

### Task 1: Koreksi di `PyranometerLoader`

**Files:** `pv_pipeline/poa/loader.py`, `tests/unit/test_poa_loader.py`.

**Interfaces:**
- Produces: `PyranometerLoader(..., koreksi: dict | None = None)`; atribut `df_mentah`, `koreksi_aktif: bool`,
  `ringkasan_koreksi: DataFrame[ws, jenis, mulai, akhir, nilai, n_sampel]`; `from_geometry_yaml(path, *, koreksi=True)`;
  `get_per_ws(...).attrs["koreksi_aktif"]`.

- [ ] **Step 1: Uji gagal** (tambahkan di akhir `tests/unit/test_poa_loader.py`):

```python
# ---------- Koreksi sensor (spec 2026-10-02-penerapan-koreksi-poa-loader) ----------

def _k(**isi):
    return {"aktif": True, **isi}


def _poa(loader, ws, t):
    return float(loader.df.loc[pd.Timestamp(t), ws])


def test_koreksi_tidak_aktif_identik_mentah(synthetic_pyranometer_xlsx):
    """Blok koreksi yang belum disetujui (aktif: false) tidak boleh mengubah satu angka pun."""
    base = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    off = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, koreksi={
        "aktif": False, "ws_faktor_periode": {"WS-3": [{"mulai": "2026-05-14", "akhir": None, "faktor": 1.1}]}})
    pd.testing.assert_frame_equal(base.df, off.df)
    assert not off.koreksi_aktif and off.ringkasan_koreksi.empty


def test_faktor_hanya_di_rentang(synthetic_pyranometer_xlsx):
    """WS-3 1 Jan-10 Agu 2026 membaca ~6 % rendah: faktor berlaku tepat di rentangnya saja."""
    base = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    k = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, koreksi=_k(ws_faktor_periode={
        "WS-3": [{"mulai": "2026-05-14", "akhir": "2026-05-14", "faktor": 1.1}],
        "WS-4": [{"mulai": "2026-05-15", "akhir": None, "faktor": 1.1}]}))
    assert _poa(k, "WS-3", "2026-05-14 12:00") == pytest.approx(1.1 * _poa(base, "WS-3", "2026-05-14 12:00"))
    assert _poa(k, "WS-3", "2026-05-14 23:55") == pytest.approx(1.1 * _poa(base, "WS-3", "2026-05-14 23:55"))
    assert _poa(k, "WS-4", "2026-05-14 12:00") == pytest.approx(_poa(base, "WS-4", "2026-05-14 12:00"))


def test_penghalang_hanya_jam_dan_rentangnya(synthetic_pyranometer_xlsx):
    """WS-1 terbayangi pukul 11-12 sejak Jun 2026: hanya jam itu yang dibuang."""
    k = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, koreksi=_k(ws_jam_penghalang={
        "WS-1": [{"mulai": "2026-05-14", "akhir": None, "jam": [11]}]}))
    assert k.df.loc["2026-05-14 11:00":"2026-05-14 11:55", "WS-1"].isna().all()
    assert k.df.loc["2026-05-14 10:55", "WS-1"] > 0 and k.df.loc["2026-05-14 12:00", "WS-1"] > 0


def test_dikecualikan_diisi_avg_terkoreksi(synthetic_pyranometer_xlsx):
    """WS-1 dikeluarkan: WB08 diisi rata-rata WS lain yang SUDAH dikoreksi, bukan avg xlsx yang tercemar."""
    k = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, koreksi=_k(
        ws_dikecualikan={"WS-1": [{"mulai": "2026-05-14", "akhir": None}]},
        ws_faktor_periode={"WS-3": [{"mulai": "2026-05-14", "akhir": None, "faktor": 1.2}]}))
    t = pd.DatetimeIndex(["2026-05-14 12:00"])
    s = k.get_per_ws(t, "WB08")
    p = float(k.df_mentah.loc[t[0], "WS-4"])                 # semua WS mentah sama; WS-2 NaN pukul 8-14
    assert s.iloc[0] == pytest.approx((1.2 * p + p + p) / 3)  # WS-3 x1,2; WS-4; WS-5
    assert s.attrs["koreksi_aktif"] and s.attrs["fallback_filled"] == 1


def test_ringkasan_koreksi_menghitung_sampel(synthetic_pyranometer_xlsx):
    k = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, koreksi=_k(ws_jam_penghalang={
        "WS-1": [{"mulai": "2026-05-14", "akhir": "2026-05-14", "jam": [11]}]}))
    r = k.ringkasan_koreksi.iloc[0]
    assert (r["ws"], r["jenis"], r["n_sampel"]) == ("WS-1", "ws_jam_penghalang", 12)


@pytest.mark.parametrize("koreksi", [
    _k(ws_faktor_periode={"WS-3": [{"mulai": "2026-05-14", "akhir": None, "faktor": 1.5}]}),
    _k(ws_faktor_periode={"WS-3": [{"mulai": "2026-05-01", "akhir": "2026-05-20", "faktor": 1.1},
                                   {"mulai": "2026-05-14", "akhir": None, "faktor": 1.05}]}),
    _k(ws_dikecualikan={"WS-9": [{"mulai": "2026-05-14", "akhir": None}]}),
    _k(ws_jam_penghalang={"WS-1": [{"mulai": "2026-05-14", "akhir": None, "jam": [24]}]}),
    {"aktif": "ya"},
])
def test_koreksi_tidak_sah_ditolak(synthetic_pyranometer_xlsx, koreksi):
    """Salah ketik di config tidak boleh diam-diam menghasilkan POA yang salah."""
    with pytest.raises(ValueError):
        PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP, koreksi=koreksi)


def test_from_geometry_yaml_membaca_dan_bisa_mematikan_koreksi(synthetic_pyranometer_xlsx, tmp_path):
    geo = tmp_path / "geo.yaml"
    geo.write_text(
        "pyranometer:\n"
        f"  xlsx_path: {synthetic_pyranometer_xlsx!r}\n"
        "  koreksi:\n"
        "    aktif: true\n"
        "    ws_faktor_periode:\n"
        "      WS-3:\n"
        "        - {mulai: 2026-05-14, akhir: null, faktor: 1.1}\n",
        encoding="utf-8",
    )
    base = PyranometerLoader(synthetic_pyranometer_xlsx, ws_to_wb=WS_TO_WB_MAP)
    t = "2026-05-14 12:00"
    assert _poa(PyranometerLoader.from_geometry_yaml(str(geo)), "WS-3", t) == pytest.approx(1.1 * _poa(base, "WS-3", t))
    assert _poa(PyranometerLoader.from_geometry_yaml(str(geo), koreksi=False), "WS-3", t) == pytest.approx(
        _poa(base, "WS-3", t))
```

- [ ] **Step 2:** `python -m pytest tests/unit/test_poa_loader.py -q` → FAIL (`unexpected keyword argument 'koreksi'`).

- [ ] **Step 3: Implementasi** di `pv_pipeline/poa/loader.py`:

```python
KOREKSI_KUNCI = ("ws_faktor_periode", "ws_jam_penghalang", "ws_dikecualikan")
KOREKSI_WS = {f"WS-{i}" for i in range(1, 6)}
KOREKSI_FAKTOR = (0.8, 1.25)


def _rentang_koreksi(entri: dict) -> Tuple[pd.Timestamp, pd.Timestamp]:
    """(mulai 00:00, akhir 23:59:59) inklusif; ``akhir`` None = terbuka."""
    mulai = pd.Timestamp(entri["mulai"]).normalize()
    akhir = (pd.Timestamp.max if entri.get("akhir") is None
             else pd.Timestamp(entri["akhir"]).normalize() + pd.Timedelta(days=1) - pd.Timedelta(seconds=1))
    return mulai, akhir


def _validasi_koreksi(koreksi: dict) -> None:
    """Salah ketik di config harus gagal keras, bukan diam-diam memberi POA yang salah."""
    if not isinstance(koreksi.get("aktif"), bool):
        raise ValueError(f"[pyranometer] koreksi.aktif harus true/false, bukan {koreksi.get('aktif')!r}")
    for kunci in KOREKSI_KUNCI:
        for ws, daftar in (koreksi.get(kunci) or {}).items():
            if ws not in KOREKSI_WS:
                raise ValueError(f"[pyranometer] koreksi.{kunci}: WS tak dikenal {ws!r}")
            rentang = []
            for e in daftar:
                a, b = _rentang_koreksi(e)
                if a > b:
                    raise ValueError(f"[pyranometer] koreksi.{kunci}.{ws}: mulai > akhir pada {e!r}")
                if kunci == "ws_faktor_periode" and not KOREKSI_FAKTOR[0] < float(e["faktor"]) <= KOREKSI_FAKTOR[1]:
                    raise ValueError(f"[pyranometer] koreksi.{kunci}.{ws}: faktor {e['faktor']} di luar (0,8; 1,25]")
                if kunci == "ws_jam_penghalang" and not all(isinstance(j, int) and 0 <= j <= 23 for j in e["jam"]):
                    raise ValueError(f"[pyranometer] koreksi.{kunci}.{ws}: jam harus bilangan bulat 0-23")
                rentang.append((a, b))
            rentang.sort()
            for (_, b1), (a2, _) in zip(rentang, rentang[1:]):
                if a2 <= b1:
                    raise ValueError(f"[pyranometer] koreksi.{kunci}.{ws}: periode tumpang tindih")
```

  Konstruktor: parameter `koreksi: Optional[dict] = None`; sesudah `self.df` dibuat dan sebelum peta WB:

```python
        self.df_mentah: pd.DataFrame = self.df
        self.koreksi: Optional[dict] = None
        self.koreksi_aktif: bool = False
        self.ringkasan_koreksi = pd.DataFrame(columns=["ws", "jenis", "mulai", "akhir", "nilai", "n_sampel"])
        if koreksi is not None:
            _validasi_koreksi(koreksi)
            if koreksi["aktif"]:
                self._terapkan_koreksi(koreksi)
```

  Metode baru:

```python
    def _terapkan_koreksi(self, koreksi: dict) -> None:
        """Faktor -> penghalang -> dikecualikan -> avg dihitung ulang dari WS terkoreksi (spec 2026-10-02)."""
        self.df_mentah = self.df.copy()
        df, idx, baris = self.df, self.df.index, []
        for kunci in KOREKSI_KUNCI:
            for ws, daftar in (koreksi.get(kunci) or {}).items():
                if ws not in df.columns:
                    raise ValueError(f"[pyranometer] koreksi.{kunci}: kolom {ws} tidak ada di xlsx")
                for e in daftar:
                    a, b = _rentang_koreksi(e)
                    m = (idx >= a) & (idx <= b)
                    if kunci == "ws_jam_penghalang":
                        m &= idx.hour.isin(e["jam"])
                    n = int(df.loc[m, ws].notna().sum())
                    if kunci == "ws_faktor_periode":
                        df.loc[m, ws] = df.loc[m, ws] * float(e["faktor"])
                        nilai = float(e["faktor"])
                    else:
                        df.loc[m, ws] = np.nan
                        nilai = e.get("jam", e.get("alasan"))
                    baris.append({"ws": ws, "jenis": kunci, "mulai": a, "akhir": e.get("akhir"), "nilai": nilai,
                                  "n_sampel": n})
        kol_ws = [c for c in df.columns if str(c).startswith("WS-")]
        df["avg"] = df[kol_ws].mean(axis=1, skipna=True)
        self.koreksi, self.koreksi_aktif = koreksi, True
        self.ringkasan_koreksi = pd.DataFrame(baris, columns=self.ringkasan_koreksi.columns)
```

  `from_geometry_yaml(cls, geometry_path, *, koreksi: bool = True)`: teruskan
  `koreksi=(pyr.get("koreksi") if koreksi else None)` ke konstruktor. `get_per_ws`: tambahkan
  `series.attrs["koreksi_aktif"] = self.koreksi_aktif` (juga pada Series kosong). Impor `numpy as np` dan `Tuple` bila
  belum ada. Docstring kelas: sebutkan parameter `koreksi` dan atributnya.

- [ ] **Step 4:** `python -m pytest tests/unit/test_poa_loader.py tests/unit/test_poa_provider.py -q` → lolos.
- [ ] **Step 5: Commit** — `feat(poa): koreksi sensor di PyranometerLoader (faktor, penghalang, dikecualikan)`.

### Task 2: `_muat_poa`, kalibrasi derate, dan CLI kalibrasi silang

**Files:** `run_derate_calibration.py`, `run_poa_cross_calibration.py`, `tests/unit/test_poa_kalibrasi_silang.py`,
`tests/unit/test_derate_calibration.py`.

- [ ] **Step 1: Uji gagal:**
  - `tests/unit/test_poa_kalibrasi_silang.py`: ubah lambda monkeypatch `_muat_poa` (uji CLI utama dan `_pasang`) menjadi
    `lambda geometry, raw_root, offset, **kw: (...)`, lalu tambahkan:

```python
def test_cli_kalibrasi_silang_membaca_poa_mentah(tmp_path, monkeypatch):
    """Kalibrasi silang atas POA terkoreksi akan mengukur koreksinya sendiri."""
    dipanggil = {}

    def muat(geometry, raw_root, offset, **kw):
        dipanggil.update(kw)
        return _Loader(), 5.0
    monkeypatch.setattr(cli, "_muat_poa", muat)
    monkeypatch.setattr(cli.PvlibClearSkyEstimator, "from_geometry_yaml",
                        classmethod(lambda cls, *a, **k: _Langit()))
    cli.main(["--mulai", "2026-01-01", "--akhir", "2026-03-31", "--output-dir", str(tmp_path)])
    assert dipanggil == {"koreksi": False}
```

  - Di `test_cli_delapan_sheet_usulan_ws2_tanpa_mengubah_config`, periksa cetakan: gunakan `capsys`, lalu
    `assert "ws_faktor_periode" in out and "faktor: 1.250" in out and "gain:" not in out`.
  - `tests/unit/test_derate_calibration.py`: di uji CLI yang ada, tambahkan pemeriksaan `Catatan` memuat butir
    `"koreksi POA"` bernilai `"tidak aktif"` (stub loader tanpa atribut koreksi).

- [ ] **Step 2:** jalankan → FAIL.
- [ ] **Step 3: Implementasi:**
  - `run_derate_calibration._muat_poa(geometry, raw_root, offset, *, koreksi: bool = True)`: teruskan
    `koreksi=(pyr.get("koreksi") if koreksi else None)` ke `PyranometerLoader`. `Catatan` derate: butir
    `"koreksi POA"` = `f"aktif ({loader.koreksi.get('sumber', '-')})"` bila `getattr(loader, "koreksi_aktif", False)`,
    selain itu `"tidak aktif"`.
  - `run_poa_cross_calibration.py`: `_muat_poa(a.geometry, a.raw_root, None, koreksi=False)`; `Catatan` butir
    `"POA"` = `"mentah (koreksi tidak diterapkan)"`; cetakan usulan:

```python
            print("  ws_faktor_periode:")
            for ws, g in usul.groupby("ws"):
                print(f"    {ws}:")
                for r in g.itertuples():
                    print(f"      - {{mulai: {r.mulai:%Y-%m-%d}, akhir: {r.akhir:%Y-%m-%d}, faktor: {r.usulan:.3f}}}")
        ...
            print("  ws_jam_penghalang:")
            for ws, g in hal.groupby("ws"):
                print(f"    {ws}:\n      - {{mulai: <isi>, akhir: <isi>, jam: {sorted(int(j) for j in g['jam'])}}}")
```

    Docstring modul: `ws_gain_periode` → `ws_faktor_periode`.
- [ ] **Step 4:** uji modul lolos; suite lengkap lolos (sendirian).
- [ ] **Step 5: Commit** — `feat(poa): derate memakai POA terkoreksi, kalibrasi silang tetap mentah; cetakan faktor`.
- [ ] **Step 6:** spesifikasi: status "diimplementasikan (config belum diubah)"; commit.
