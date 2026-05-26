# pv_pipeline Test Suite (pytest)

## Quick start

```bash
# Dari main repo root (cwd untuk resolve "raw data input/" yaml paths)
cd "C:/Users/nabil/Downloads/SolarYieldPro-main/kodingan pv string"
pip install pytest

# Run semua test (dari worktree)
cd .claude/worktrees/modest-shockley-9c31f4
pytest

# Verbose + per-test result
pytest -v

# Run satu file
pytest tests/unit/test_baseline.py

# Run satu function
pytest tests/unit/test_voc_estimator.py::test_voc_estimate_normal

# Run integration tests only
pytest tests/integration

# Skip tests yang marked slow
pytest -m "not slow"
```

## Folder layout

```
tests/
├── __init__.py
├── conftest.py            # Shared fixtures: synthetic_combined_df + mock providers + synthetic xlsx
├── README.md              # This file
├── unit/                  # Per-module unit tests
│   ├── __init__.py
│   ├── test_core.py             # M2Finding, SubModule, M2Engine.write_xlsx_multi
│   ├── test_m2_config.py        # DEFAULT_M2_CONFIG + load_m2_config deep-merge
│   ├── test_voc_estimator.py    # estimate_voc_at_low_current
│   ├── test_panel_spec.py       # PanelSpec parse + Voc helpers
│   ├── test_baseline.py         # BaselineAccumulator hybrid filter + parquet/csv I/O
│   ├── test_peer_zscore.py      # M2bPeerZScore POA-gated + sunset fix + StringStatus + preprocessing flag
│   ├── test_open_circuit.py     # M2bOpenCircuit POA-gated + sunset fix + StringStatus
│   ├── test_ground_fault.py     # M2bGroundFault triple-signal + per-PV StringStatus fan-out
│   ├── test_poa_loader.py       # PyranometerLoader multi-year + WB->WS mapping
│   ├── test_albedo_loader.py    # AlbedoLoader NSRDB TMY xlsx + reindex tolerance
│   ├── test_pvlib_estimator.py  # PvlibClearSkyEstimator 3 models + Perez default + solar_elevation
│   ├── test_cell_temp.py        # CellTempProvider + WB->WS Tcell piggyback + SAPM fallback
│   ├── test_poa_provider.py     # POAProvider orchestrator + auto fallback + solar_elevation passthrough
│   ├── test_physics.py          # compute_pmax/p_expected/Kt/delta_power/PR/active_power_integration
│   ├── test_preprocessing.py    # Hampel outlier filter + apply_hampel_to_pv_dataframe (A/B helper)
│   ├── test_weather_loader.py   # 3 weather loaders (Ambient/WindSpeed/WindDirection, 4-WS multi-year)
│   └── test_generation_loader.py # GenerationLoader Summary (PV) sheet (PR data source)
└── integration/           # End-to-end pipeline tests
    ├── __init__.py
    └── test_cell4_e2e.py        # Notebook Cell 4 M2 pipeline (M2Engine + 3 detectors + xlsx output)
```

## Fixtures (conftest.py)

### Synthetic DataFrames + Mock Providers
- **`synthetic_combined_df`** : 3 inverters (WB05-INV01/02, WB02-INV05) × 10 PV strings × 145 timestamps. Realistic V (1200V Vmp) / I (13A Imp) Jinko 26-module string profile.
- **`synthetic_combined_df_with_outlier`** : Same + PV3@WB05-INV01 R abnormal (I×0.40, V×1.03 → ~2.6x R) — high_R signature.
- **`mock_poa`** : MockPOA sin-curve POA peak 1000 W/m² @ noon. Wave 2: includes `get_solar_elevation` method.
- **`mock_panel`** : MockPanel Jinko JKM625N (Voc=55.72, WB01/02=24 modules, lainnya=26).
- **`mock_cell_temp`** : MockTcell constant 30°C.
- **`m2_config_minimal`** : Minimal config dict untuk panggil detector.run().

### Synthetic xlsx files (untuk loader tests, written ke `tmp_path`)
- **`synthetic_pyranometer_xlsx`** : Mirror `POA PLTS IKN <year>.xlsx` (sheet "POA PLTS IKN", 288 rows 5-min, 5 WS + avg).
- **`synthetic_tcell_xlsx`** : Mirror `PV Module Temperature PLTS IKN.xlsx` (sheet "PV Module Temp", 18 cols, sub-headers row 0).
- **`synthetic_albedo_xlsx`** : Mirror `Surface Albedo Forecast TMY NSRDB PLTS IKN.xlsx`.

## Inline smoke tests (`if __name__ == "__main__":`)

Tiap module punya inline smoke block untuk standalone debug. Run dari main repo cwd:
```bash
cd "C:/Users/nabil/Downloads/SolarYieldPro-main/kodingan pv string"
python ".claude/worktrees/modest-shockley-9c31f4/pv_pipeline/peer_zscore.py"
python ".claude/worktrees/modest-shockley-9c31f4/pv_pipeline/cell_temp.py"
# dst.
```

Pytest suite covers same assertions + edge cases + integration.

## Coverage status

**Total: 219 tests pass in ~22s** (214 unit + 5 integration).

| Module | Unit tests | Notes |
|---|---|---|
| `core.py` | 5 | M2Finding, M2Engine, write_xlsx_multi |
| `m2_config.py` | 16 | + Perez default + filter_mode + SAPM chain |
| `voc_estimator.py` | 5 | |
| `panel_spec.py` | 7 | Jinko JKM625N + Voc temp coef |
| `peer_zscore.py` | 8 | + StringStatus + preprocessing flag |
| `open_circuit.py` | 7 | + StringStatus + sunset fix |
| `ground_fault.py` | 8 | + StringStatus per-PV fan-out |
| `baseline.py` | 9 | parquet + csv + hybrid filter |
| `poa/loader.py` | 10 | multi-year support |
| `poa/pvlib_estimator.py` | 16 | Perez default + solar_elevation |
| `poa/provider.py` | 15 | + solar_elevation passthrough |
| `poa/albedo_loader.py` | 8 | NSRDB TMY |
| `cell_temp.py` | 18 | + SAPM model fallback |
| `physics.py` | 33 | Pmax/P_expected/Kt/DeltaP/PR/active_power_int |
| `preprocessing.py` | 16 | Hampel + apply_hampel_to_pv_dataframe |
| `weather/loader.py` | 15 | Ambient + WindSpeed + WindDirection |
| `generation/loader.py` | 14 | IKN Generation Summary (PV) |
| `training_data.py` | – | TODO (blocked: needs >=3 mo baseline data) |
| `lstm_ae.py` | – | TODO (blocked: needs trained model) |
| **integration/test_cell4_e2e.py** | **5** | **End-to-end M2 pipeline + xlsx output** |

## Fase 2 wave history

| Wave | Commit | Item | Tests delta |
|---|---|---|---|
| 1 | 220bcfc | Perez transposition default + physics base | 106 → 130 |
| 2 | 6cb38a9 | solar_elevation filter + DeltaP helper | 130 → 144 |
| 3 | cc126a8 | pvanalytics Hampel preprocessing module | 144 → 157 |
| 4 | 135a645 | path migration to `raw data input/` + multi-year POA | 157 → 157 |
| 5 | fcd0336 | weather loaders (Ambient/WindSpeed/WindDirection) | 157 → 172 |
| 6 | c9376c7 | SAPM Tcell fallback (Sandia thermal model) | 172 → 178 |
| 7 | c686760 | Generation loader + PR/active_power helpers | 178 → 207 |
| 8 | 01ce52f | per-PV StringStatus sheets per detector | 207 → 209 |
| 9 | 05406d6 | Hampel preprocessing A/B wire (feature flag) | 209 → 214 |
| 10 | – | integration tests Cell 4 e2e | 214 → 219 |

## Adding new tests

1. File baru di `tests/unit/test_<module>.py` (unit) atau `tests/integration/test_<feature>_e2e.py` (e2e).
2. Pakai fixtures dari `conftest.py` (jangan re-build synthetic data).
3. Mock providers digunakan agar tidak butuh real xlsx files (kecuali integration test yang sengaja test loader I/O).
4. Untuk test yang butuh real xlsx production data, mark `@pytest.mark.slow` (TBD: marker belum diaktifkan).

## Dependencies

```bash
pip install pytest             # core test runner
pip install pyarrow            # untuk test_baseline (parquet I/O)
pip install openpyxl           # untuk test_core + loader tests (xlsx I/O)
pip install pvlib              # clearsky models + sapm_cell + solar position
pip install pvanalytics        # Hampel outlier filter (Wave 3)
```

PyYAML, pandas, numpy sudah ada karena dipakai pv_pipeline.
