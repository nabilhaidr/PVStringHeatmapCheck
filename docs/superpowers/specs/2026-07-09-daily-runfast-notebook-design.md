# Notebook Harian Cepat — Heatmap + M2eAvailability (daily_runfast)

**Tanggal:** 2026-07-09
**Status:** Disetujui user (folder `outputs_daily_runfast/`, PNG heatmap ikut disimpan)

## Tujuan

Notebook Colab baru yang bisa dijalankan **harian secara cepat** dengan input minimal:
hanya snapshot Excel harian plant (`1-2.xlsx` + `3-10.xlsx` dari folder Drive). Tidak
menunggu/mengunggah raw data tambahan (cuaca, pyranometer, baseline history, model).
Isinya hanya **heatmap semua inverter + M2eAvailability** — detector yang tidak butuh
input di luar snapshot harian.

## Non-tujuan (yang sengaja TIDAK ada)

- TIDAK menjalankan detector POA-aware (PeerZScore, OpenCircuit, GroundFault, IForest,
  Shading, LowIrradiance) — semuanya butuh POAProvider (geometry/pyranometer/pvlib).
- TIDAK menjalankan M2aSoiling (butuh ≥90 hari baseline) dan LSTM-AE (butuh model+history).
- TIDAK membuat file baseline CSV/parquet/manifest (Cell 7 notebook penuh di-drop).
- TIDAK export df_plot CSV / download Colab (Cell 8 di-drop).
- TIDAK ada PR/curtailment (Cell 5) dan sanity POA/albedo/Tcell (Cell 6).
- TIDAK mengubah `pv_pipeline/` sama sekali (viz.py tidak disentuh — PNG disimpan
  lewat loop di cell notebook).

## Kenyataan teknis yang mendasari

- Semua detector M2 mengonsumsi `combined_df` (snapshot harian) — yang membedakan
  ringan/berat adalah input TAMBAHAN. M2eAvailability hanya butuh kolom
  `Inverter_ID`, `Start Time`, `Inverter status` (+ kolom PV power untuk string-proxy).
- `plot_all_inverters` / `plot_single_inv_heatmap` (viz.py:80,290) TIDAK punya
  parameter save. Cell 3 baru melakukan loop sendiri per inverter:
  `plot_single_inv_heatmap(..., show=False)` → `fig = plt.gcf()` →
  `fig.savefig(<out>/heatmap_{datestr}_{inv_id}.png, bbox_inches="tight")` →
  `plt.show()` → `plt.close(fig)`.
- Konvensi repo: notebook di-generate builder script yang menulis JSON nbformat-4.5
  langsung (nbformat tidak terinstal lokal). Contoh pola: `notebook/_build_physics_nb.py`.
- Sheet xlsx M2e ter-emit sebagai `M2e_hybrid_AllStrings` + `M2e_hybrid_InverterLog`
  setelah bridge `sm_e.last_*_df` → `sm_e.artifacts` (pola Cell 4 notebook penuh).

## Deliverables

| File | Aksi |
|---|---|
| `notebook/_build_daily_runfast_nb.py` | baru — builder script JSON nbformat-4.5 |
| `notebook/daily_runfast_v1.ipynb` | baru — hasil generate builder |
| `.gitignore` | +1 baris `outputs_daily_runfast/` (sejajar `outputs/`, `baseline/`) |

## Struktur notebook (1 markdown + 4 code cell)

**Cell 0 (markdown):** judul, tujuan (run harian cepat), prasyarat (hanya folder Drive
berisi `1-2.xlsx`/`3-10.xlsx`), daftar output.

**Cell 1 — Config + download Drive.** Salinan Cell 1 notebook
`20260514stringmap_v1.5.ipynb` (find_repo_root, USER CONFIG DRIVE_FOLDER_URL/
EXPECTED_FILES, `download_from_gdrive`) + tambahan:
```python
DAILY_OUT_DIR = os.path.join(REPO_DIR, "outputs_daily_runfast")
os.makedirs(DAILY_OUT_DIR, exist_ok=True)
```

**Cell 2 — Load + transform.** Verbatim Cell 2 notebook penuh
(`load_and_prepare_data` → `add_inverter_id` → `add_pv_power_columns` →
`add_total_pv_power` → `make_pivot`) → `combined_df`.

**Cell 3 — Heatmap per inverter + save PNG.** `prepare_df_work` + `get_empty_pv_map`
(sama seperti existing), lalu loop manual menggantikan `plot_all_inverters`:
auto-detect `DATA_DATESTR` dari `df_plot["Start Time"]` (pola Cell 8 existing),
lalu per inverter: plot (show=False) → savefig ke
`DAILY_OUT_DIR/heatmap_{DATA_DATESTR}_{inv_id}.png` → show inline → close.
Error per inverter di-catch dan dilaporkan (pola errors list `plot_all_inverters`).

**Cell 4 — M2eAvailability only.** Versi pangkas Cell 4 notebook penuh:
- Import hanya `M2eAvailability`, `M2Engine`, `load_m2_config`. TANPA POAProvider/
  PanelSpec/CellTempProvider/detector lain.
- Pre-check `combined_df` + kolom wajib; auto-detect `DATA_DATESTR` (fallback bila
  Cell 3 belum jalan).
- `engine = M2Engine([sm_e]); findings = engine.run_all(combined_df, cfg)`.
- Bridge `sm_e.last_all_strings_df`/`last_inverter_log_df` → `sm_e.artifacts`.
- Output ke `DAILY_OUT_DIR` (BUKAN `cfg["m2e"]["output_dir"]`):
  - `m2_findings_{DATA_DATESTR}.jsonl`
  - `m2_findings_{DATA_DATESTR}.xlsx` (Findings + M2e_hybrid_AllStrings + M2e_hybrid_InverterLog)
  - `inverter_operation_{DATA_DATESTR}.csv`
- Summary print: severity counts + distribusi status inverter (pola existing).

Nama file output sama dengan notebook penuh (kompatibel dashboard loader
`m2_findings_YYYYMMDD.xlsx`) — hanya foldernya yang berbeda, sehingga tidak
tercampur dengan hasil run penuh di `outputs/`.

## Error handling

Ikut pola notebook penuh: `RuntimeError` bila `combined_df` belum ada (Cell 1+2 belum
jalan); `RuntimeError` bila kolom wajib hilang; per-inverter try/except di loop heatmap
(satu inverter gagal tidak menghentikan sisanya); `os.makedirs(exist_ok=True)`.

## Verifikasi

1. **Builder valid:** jalankan `python notebook/_build_daily_runfast_nb.py`, lalu
   round-trip `json.load` — nbformat 4.5, 5 cell (1 markdown + 4 code).
2. **Smoke headless lokal (tanpa Drive):** exec source Cell 3+4 dengan `combined_df`
   sintetis kecil (2 inverter × beberapa timestamp, kolom `Inverter_ID`, `Start Time`,
   `Inverter status`, `PV1..PV2 Power(kW)`), `matplotlib.use("Agg")`, stub `plt.show`,
   `PYTHONIOENCODING=utf-8`, `DAILY_OUT_DIR` diarahkan ke folder temp → assert PNG +
   jsonl + xlsx + csv tercipta.
3. Notebook cells tidak diunit-test (konvensi repo); tidak ada test suite baru.
