# Streamlit Dashboard — Fase 4 Task 3

**Status:** Design approved, ready for implementation plan
**Date:** 2026-05-23
**Base branch:** `claude/modest-shockley-9c31f4` (HEAD `eb98f8c`, 8 detectors live, 418 tests green)
**Author:** brainstormed via superpowers:brainstorming
**Companion sessions:** Fase 4 Task 1 (AlertManager) — landing **after** this dashboard M0, then this dashboard M1 wires up actions.

## 1. Purpose & Scope

Browser dashboard untuk visualisasi output PV pipeline (M2 detectors). Menggantikan / melengkapi notebook Cell 3 heatmap loop dengan UI yang stateful, multi-page, dan accessible via URL.

**In scope (M0):**
- 4 pages: Overview, Heatmap, Findings Browser, Per-Detector Deep-Dive.
- Read-only — no ack/snooze/resolve actions (Task 1 jadi prerequisite untuk M1 actions).
- Cloud deployment via Streamlit Community Cloud.
- Single shared password auth.
- Data source: Google Drive folder berisi `m2_findings_YYYYMMDD.xlsx`.
- Multi-day date range aggregation.
- Manual refresh button untuk re-fetch GDrive.

**Out of scope (M0):**
- Acknowledge / snooze / resolve workflow (M1, gates on Task 1 AlertManager landing).
- Per-user authentication / role-based access (M1+).
- Push notifications (separate task — Task 2 NotificationDispatcher).
- SQLite intermediate layer (deferred; xlsx direct sufficient untuk M0 dataset size).
- Mobile responsive optimization (best-effort via Streamlit defaults).
- Custom domain (free Streamlit Cloud subdomain OK).
- POA overlay di heatmap (M1 nice-to-have).

**Sequencing:**
1. **Task 3 M0 (this spec)** — read-only dashboard, ship to Streamlit Cloud.
2. **Task 1** — `pv_pipeline/alerts/` AlertManager + persistence.
3. **Task 3 M1** — wire dashboard ke alerts module, add ack/snooze/resolve actions.
4. **Task 2** — NotificationDispatcher (Telegram/email), independent track.

## 2. Architecture & Module Structure

**Approach: Streamlit native multipage app di dalam pv_pipeline package.**

```
pv_pipeline/
└── dashboard/
    ├── __init__.py            # version, package marker
    ├── app.py                 # entry: auth gate + Overview page
    ├── pages/
    │   ├── 2_Heatmap.py       # per-inverter PV grid
    │   ├── 3_Findings.py      # dense findings browser
    │   └── 4_Detectors.py     # 8-detector tabs
    ├── data/
    │   ├── __init__.py
    │   ├── gdrive.py          # service account, list folder, download xlsx
    │   ├── loader.py          # single-day + multi-day xlsx → DataFrame dict
    │   └── cache.py           # @st.cache_data wrappers
    ├── auth.py                # password gate
    ├── styles.py              # CSS injection per-page (clean vs dense)
    └── widgets/
        ├── __init__.py
        ├── kpi.py             # KPI card
        ├── severity_badge.py  # color-coded chip
        ├── date_picker.py     # shared date-range picker
        └── filters.py         # detector/inverter/WB filter widgets

requirements-dashboard.txt     # streamlit, google-api-python-client, altair, openpyxl
.streamlit/
├── config.toml                # theme: light, sidebar default expanded
└── secrets.toml.example       # template for GDrive creds + password
streamlit_app.py               # 1-liner entry untuk Streamlit Cloud
docs/dashboard/
├── README.md                  # deploy guide
├── gdrive-setup.md            # service account walkthrough
└── smoke.md                   # post-deploy checklist
```

**Dependency boundaries:**
- `pv_pipeline.dashboard.*` imports `pv_pipeline.core` (M2Finding schema) dan `pv_pipeline.viz` (heatmap reuse). Backend tetap zero-streamlit-import.
- `requirements.txt` (existing pipeline deps) terpisah dari `requirements-dashboard.txt` (Streamlit + GDrive). Streamlit Cloud merges both at deploy.
- `loader.py` exposes both `load_single_day(date)` (fast path untuk Heatmap) dan `load_date_range(start, end)` (Overview, Findings, Detectors).

**Why this layout:**
- Native multipage = URL per page (bookmarkable, shareable).
- `dashboard/` jadi sub-package konsisten dengan `m2a/`, `alerts/` (future), `notifications/` (future).
- Widgets factored = reusable cross-page + lighter PR diffs ke depan.

## 3. Data Flow

```
Google Drive folder
  └── m2_findings_YYYYMMDD.xlsx (pipeline output, manual upload M0)
        │ (service account JSON from Streamlit secrets)
        ▼
dashboard/data/gdrive.py
  • _drive_client() -> Resource                                  @lru_cache, build once per process
  • list_findings_files() -> List[Tuple[date, file_id]]          parse YYYYMMDD from filename
  • download_xlsx(file_id: str) -> io.BytesIO                    stream bytes
        ▼
dashboard/data/loader.py
  • load_single_day(d: datetime.date) -> Dict[str, DataFrame]
        returns {"Findings": df, "M2e_hybrid_AllStrings": df, ...}
  • load_date_range(start: date, end: date) -> Dict[str, DataFrame]
        composes per-day cached calls (NOT bulk fetch — see decision 5 below),
        concat per-sheet, tambah kolom 'source_date'
  • _parse_xlsx(bytes_io: BytesIO) -> Dict[str, DataFrame]       openpyxl read_only=True
        ▼
dashboard/data/cache.py
  • @st.cache_data(ttl=∞) cached_load_single_day(d: date)         primary cache layer
  • cached_load_range(start, end) — uncached wrapper that calls
        cached_load_single_day for each day di range, then concat
  • clear_cache() — dipanggil dari "Refresh data" button (calls
        st.cache_data.clear() globally)
        ▼
Page render functions (app.py, pages/*.py)
  • st.session_state.date_range: Tuple[date, date]                shared selector
  • st.session_state.selected_inverter: Optional[str]             shared filter
  • Each page calls cached_load_*(...) dan render
```

**Key decisions:**
1. **Date types are `datetime.date`** (not str, not datetime). Loader functions accept `datetime.date`; cache hash via Streamlit's default repr (date objects hash fine). Filename parsing uses `datetime.strptime(token, "%Y%m%d").date()`.
2. **Cache TTL infinite.** Invalidation eksplisit via "Refresh data" button → `st.cache_data.clear()` + `st.rerun()`.
3. **GDrive filename convention:** `m2_findings_YYYYMMDD.xlsx`. Files mismatch pattern di-skip dengan warning.
4. **Empty / missing date:** loader returns empty Dict (or empty DataFrames per sheet), UI shows `st.caption("Hari tanpa data: X, Y")`.
5. **Multi-day load = compose per-day cached calls (NOT bulk fetch).** `load_date_range(start, end)` iterates days in range, calls `cached_load_single_day(d)` per day, concat results. Effect: 30-day range = 30 cache lookups; first call downloads 30 files, subsequent calls all-hit cache. Memory profile = additive (~5 MB × 30 = ~150 MB peak per range request), bounded by Streamlit Cloud 1 GB RAM.
6. **Multi-day concat schema:** untuk snapshot sheets (e.g. `M2e_hybrid_AllStrings`), tambah kolom `source_date: date`. Untuk timestamped sheets (`Findings`), derive `source_date = timestamp.dt.date`.
7. **Secrets layout** (`.streamlit/secrets.toml`):
   ```toml
   [dashboard]
   password = "<shared-pwd>"

   [gdrive]
   folder_id = "<gdrive-folder-id>"
   service_account_json = """<full JSON inline>"""
   ```
8. **Performance budget:** single-day ~2-5 MB parse ~1-2s; 30-day range = 30 per-day cache lookups × 5 MB peak each, additive ~150 MB peak per range request. First range load ~30-60s (downloads 30 files); subsequent calls all-hit cache (<1s). Fits Streamlit Cloud free tier 1 GB RAM.

## 4. Auth Flow

Single shared password, gated di `app.py` sebelum render apa pun. Setiap file di `pages/*.py` juga panggil `auth.require_auth()` di baris pertama setelah imports (Streamlit native multipage tidak punya middleware global).

```python
# dashboard/auth.py
import streamlit as st

def require_auth() -> None:
    """Gate. Render login form & st.stop() kalau belum authed."""
    if st.session_state.get("authed"):
        _render_logout_sidebar()
        return
    _render_login()
    st.stop()

def _render_login() -> None:
    st.title("PV Pipeline Dashboard")
    st.caption("Masukkan password untuk masuk.")
    pwd = st.text_input("Password", type="password")
    if st.button("Masuk", type="primary"):
        if pwd == st.secrets["dashboard"]["password"]:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("Password salah.")

def _render_logout_sidebar() -> None:
    with st.sidebar:
        if st.button("Logout"):
            st.session_state.clear()
            st.rerun()
```

**Critical behaviors:**
- `st.stop()` di `require_auth()` cegah leak: tanpa ini, sisa script tetap eksekusi (Streamlit tidak short-circuit otomatis).
- Per-page guard eksplisit di tiap `pages/*.py` (no global middleware).
- Session lifetime = browser tab. Close tab = logout. No "remember me" M0.
- No brute-force protection M0 (assumption: strong password di secrets, URL tidak public-indexed). M1 tambah counter + lockout kalau jadi concern.
- No password hash (plain compare). Secrets sudah encrypted-at-rest di Streamlit Cloud — hashing adalah over-engineering untuk M0.

## 5. Page-by-Page UI Structure

### 5.1 Overview (`app.py`) — visual style "clean / minimal"

Single column, no sidebar widgets. Date range picker di top.

Components (top → bottom):
1. Header bar: title + `[Refresh data]` button + `[Logout]` (via sidebar).
2. Date range picker — drives `st.session_state.date_range`, dipakai semua page.
3. 4× KPI cards via `widgets/kpi.py`: **FINDINGS**, **CRITICAL**, **FLEET UPTIME**, **INVERTERS**.
4. **Findings per WB block** — Altair bar chart, x=WB, y=count, color=severity.
5. **Findings per detector** — Altair horizontal bar chart, sorted descending.
6. **Trend over date range** — Altair line chart, daily count, stacked by severity.

### 5.2 Heatmap (`pages/2_Heatmap.py`) — neutral matplotlib

Sidebar widgets + main full-width.

Sidebar:
- Inverter selectbox (populated dari `findings_df["inverter_id"].unique()`)
- Date single-day picker (heatmap meaningful per-hari saja)
- POA overlay toggle (M1 nice-to-have; M0 stub disabled)
- `[Refresh]` + `[Logout]`

Main:
- Heatmap image via `st.pyplot(fig)` — reuse `pv_pipeline/viz.py` matplotlib code.
- Caption explaining color encoding (RdYlGn, empty PV white).

Single-day mode only — multi-day heatmap not meaningful (would average out signal).

### 5.3 Findings Browser (`pages/3_Findings.py`) — visual style "dense / terminal"

Sidebar widgets + main full-width (dark theme, mono font via CSS injection).

Sidebar:
- Severity checkbox group (CRITICAL, HIGH, MEDIUM, INFO, NORMAL — default first 3 checked)
- Detector selectbox (All + 8 detectors)
- Inverter selectbox (All + per-inverter)
- WB selectbox (All + per-WB)
- `[Export csv ⬇]` download_button
- `[Refresh]` + `[Logout]`

Main:
- Count line: "Showing X findings (filtered from Y)"
- `st.dataframe(df, height=600)` dengan custom CSS (dark theme, mono font, color-coded severity).
- Row click → `st.session_state.selected_row` → `st.expander("Detail")` di bawah tabel render:
  - All M2Finding fields (timestamp, inverter, severity, value, threshold, message, fault_type, confidence).
  - `evidence` dict via `st.json()`.

### 5.4 Per-Detector Deep-Dive (`pages/4_Detectors.py`) — neutral medium density

Tabs di main area, 8 detector total.

Per-tab structure (`widgets/detector_tab.py::render_detector(name, sheets_dict)`):
1. **Summary line** — finding count + unique inverter count.
2. **Chart** — kalau metric kuantitatif (ratio, score, z-score) → Altair histogram; kalau status enum → bar.
3. **Table** — sheet relevan dari xlsx (e.g. `M2b_open_circuit_StringStatus`), via `st.dataframe`.
4. **Export tab data** — `download_button` per-tab.

8 tabs: Availability, PeerZ, OpenCircuit, GroundFault, IForest, Shading, LowIrradiance, Soiling.

Tab lazy-render: Streamlit hanya render content tab aktif per rerun.

## 6. Error Handling

Cross-cutting principle: **never crash silently**. User selalu lihat data, warning, atau error — never empty screen tanpa indikasi.

| Layer | Failure | UX Handling |
|-------|---------|-------------|
| Secrets / Config | secrets.toml missing/malformed | `st.error` + `st.stop()` dengan pesan eksplisit |
| GDrive auth | Service account invalid / no folder permission | `st.error` + `st.stop()` |
| GDrive API | Timeout / rate limit | `st.warning` + retry 3× exponential backoff (60s max) |
| GDrive listing | Folder empty (zero xlsx) | `st.info("Belum ada file findings…")` + `st.stop()` |
| GDrive listing | Filename mismatch pattern | Skip file, log ke `st.warning` collector |
| Date range / Loader | Range tidak overlap files yang ada | `st.warning` + empty state (KPI "—") |
| Date range / Loader | Sebagian hari di range missing | `st.caption("Hari tanpa data: X, Y")`, continue |
| xlsx parse | openpyxl raise (file corrupt) | `st.error` per-file, skip file, continue |
| xlsx parse | Missing expected sheet (detector OFF) | `st.info("Detector tidak aktif untuk range ini.")` |
| Rendering | Empty DataFrame post-filter | `st.info("Tidak ada data sesuai filter.")` |
| Rendering | Matplotlib heatmap fail | `st.error` + traceback di expander, continue |
| Rendering | Altair encoding error | `st.error` + fallback ke `st.dataframe` |
| Cache | Stale entry during fetch | Cache key = date; manual Refresh clears |
| Auth | Invalid password | `st.error("Password salah.")` |
| Auth | Session expired | Auto-redirect ke login form |

**Top-level wrap di `app.py`:**

```python
try:
    render_page()
except Exception as e:
    st.error("Terjadi kesalahan tidak terduga.")
    with st.expander("Detail traceback (untuk developer)"):
        st.exception(e)
```

**Data layer wrap pattern:**

```python
try:
    df = _parse_xlsx(bytes_io)
except Exception as e:
    st.warning(f"Gagal parse {filename}: {e}")
    return pd.DataFrame()
```

No silent fallback to empty data tanpa UI signal — dashboard semantics berbeda dari pipeline (yang OK silent-skip karena batch context).

## 7. Testing Strategy

| Layer | Test Type | Coverage Target |
|-------|-----------|-----------------|
| `data/gdrive.py` | unit + mock client | 80%+ line (list, download, retry/backoff branches) |
| `data/loader.py` | unit + in-memory xlsx fixtures | 90%+ line (happy path, concat, missing days, malformed) |
| `data/cache.py` | unit | 70%+ (cache key, clear propagation) |
| `auth.py` | unit (test `_check_password(input, expected) -> bool` pure helper, separate dari Streamlit IO) | 100% (auth = security critical) |
| `widgets/*` transformers | unit kalau ada data prep | skip pure-render widgets |
| `pages/*.py` | integration via `streamlit.testing.v1.AppTest` | smoke "renders without exception" (1 test per page) |
| End-to-end | manual smoke checklist | pre-merge (`docs/dashboard/smoke.md`) |

**Test infrastructure:**
```
tests/
├── unit/dashboard/
│   ├── test_loader.py
│   ├── test_gdrive.py
│   ├── test_auth.py
│   └── test_cache.py
├── integration/dashboard/
│   ├── test_overview_page.py
│   ├── test_heatmap_page.py
│   ├── test_findings_page.py
│   └── test_detectors_page.py
└── fixtures/dashboard/
    ├── sample_findings.xlsx       # ~10 KB, 3-day × 10 findings × 8 sheets
    └── sample_drive_response.json # GDrive list API mock
```

**Markers** (`pytest.ini`):
```ini
markers =
    dashboard: dashboard-specific (require streamlit)
    gdrive_live: hit real GDrive API (skip by default)
```

**Principles:**
1. **No regression target.** Existing 418 tests harus tetap green.
2. **Mock GDrive at module boundary.** Test `loader.py` dengan in-memory xlsx (BytesIO + openpyxl); test `gdrive.py` dengan `unittest.mock.patch('pv_pipeline.dashboard.data.gdrive.build')`.
3. **AppTest minimal usage.** Smoke "no exception", tidak verify exact pixel.
4. **Synthetic fixtures kecil** untuk test speed.
5. **CI gate.** Dashboard tests bagian dari default `pytest tests/` run; fail = PR block.
6. **Coverage report** (optional M0, recommended M1).

## 8. Deployment

**Setup phases:**

1. **GCP service account** — Create GCP project → enable Drive API → create service account → download JSON key → share GDrive folder dengan service account email (Viewer cukup, read-only).
2. **Streamlit Community Cloud** — Login pakai GitHub → connect private repo → entry point `streamlit_app.py` → branch `main` (post-merge).
3. **Secrets** (Streamlit Cloud UI):
   ```toml
   [dashboard]
   password = "..."
   [gdrive]
   folder_id = "..."
   service_account_json = """<JSON>"""
   ```
4. **Requirements** — Streamlit Cloud auto-detect `requirements.txt`. Append dashboard deps OR keep separate `requirements-dashboard.txt` + symlink/include.
5. **`.streamlit/config.toml`:**
   ```toml
   [theme]
   base = "light"
   primaryColor = "#3b3b58"
   [server]
   maxUploadSize = 50
   [browser]
   gatherUsageStats = false
   ```
6. **First deploy** ~3-5 min. URL: `<appname>.streamlit.app`.
7. **Smoke checklist** (`docs/dashboard/smoke.md`):
   - URL loads, login form muncul
   - Password works → Overview
   - KPI angka realistik
   - Heatmap dropdown populated, render OK
   - Findings filter work
   - Detectors 8 tabs load
   - Refresh re-fetches GDrive
   - Logout → kembali ke login

**Free tier constraints (Streamlit Community Cloud):**

| Resource | Limit | M0 Impact |
|----------|-------|-----------|
| RAM | 1 GB | 30-day load ~150 MB peak. OK. |
| CPU | Shared | Heatmap ~3s/render. Acceptable. |
| Storage | ephemeral tmpfs ~500 MB | Cache di RAM, no disk writes M0. |
| Concurrent sessions | ~5-10 | Internal team, fine. |
| GDrive API | 1B/day | ~50 reads × 10 sessions = 500/day. Negligible. |
| Auto-sleep | 7 days no traffic | First user wake delay ~10s. |

**Custom domain:** M0 stay on `*.streamlit.app` subdomain. Custom domain = paid tier atau self-host VPS — defer.

**Rollback:** revert commit → push → auto-redeploy ~3 min. No blue/green; ~30s downtime saat redeploy.

**Pipeline → GDrive sync** (out of scope dashboard, documented in `docs/dashboard/README.md`):
- M0: manual upload xlsx ke GDrive folder setelah pipeline run.
- M1: pipeline tambah optional `--push-to-gdrive` flag pakai same service account.
- Dashboard tidak care — selama file landing di folder, akan dibaca.

## 9. Decisions Captured (Brainstorm Trail)

| Question | Decision |
|----------|----------|
| Target user role | Multi-role (operator + engineer + manager) — multi-page layout |
| Data source | Hybrid xlsx → SQLite later (M0 = xlsx direct) |
| Deploy target | Cloud (Streamlit Community Cloud) |
| Storage backend | Google Drive (service account) |
| Auth | Single shared password (`st.secrets`) |
| M0 page set | All 4: Overview + Heatmap + Findings + Detectors |
| Language | Mix natural — ID labels + EN technical terms |
| Refresh strategy | Manual button (no auto-polling) |
| Visual style | A "clean / minimal" Overview; B "dense / terminal" Findings; neutral Heatmap + Detectors |
| Findings actions | Read-only + export M0 (full state machine deferred to M1, gates on Task 1) |
| Time scope | Multi-day date range aggregation + single-day fast path (Heatmap) |
| Architecture | Pendekatan A — Streamlit native multipage di `pv_pipeline.dashboard` |
| Loader API | Both `load_single_day(date)` + `load_date_range(start, end)` |

## 10. Open Questions / M1 Carryover

- **Visual style untuk Heatmap & Detectors** — M0 pakai default neutral. Kalau dirasa terlalu plain, M1 push mockup options.
- **Auto-sync pipeline → GDrive** — M1, paralel ke dashboard.
- **POA overlay di Heatmap** — M1 nice-to-have.
- **Ack/snooze/resolve actions** — M1, gates on Task 1 AlertManager.
- **Coverage report di CI** — M1.
- **Custom domain** — defer (paid tier).
- **Rate limit / brute force protection di auth** — M1 kalau jadi concern.
- **SQLite intermediate layer** — defer; xlsx cukup untuk M0 dataset size.
- **i18n formal (bilingual toggle)** — defer; mix natural cukup.

## 11. Implementation Plan Handoff

Spec ini siap untuk transition ke `superpowers:writing-plans` skill. Plan akan break down implementation per-file dengan urutan dependency, mengikuti subagent-driven-development pattern (independent tasks executable parallel by sub-agents).

Expected high-level plan structure:
1. **Foundation:** `requirements-dashboard.txt`, `.streamlit/`, `streamlit_app.py`, `pv_pipeline/dashboard/__init__.py`.
2. **Data layer:** `gdrive.py` → `loader.py` → `cache.py` (sequential, each with tests).
3. **Auth:** `auth.py` (independent, with tests).
4. **Widgets:** `kpi.py`, `severity_badge.py`, `date_picker.py`, `filters.py` (parallel).
5. **Styles:** `styles.py` (independent).
6. **Pages:** `app.py` (Overview) → `pages/2_Heatmap.py` → `pages/3_Findings.py` → `pages/4_Detectors.py` (parallel after data + auth + widgets).
7. **Docs:** `docs/dashboard/README.md`, `gdrive-setup.md`, `smoke.md`.
8. **CI integration:** pytest markers di `pytest.ini`.
9. **Manual deploy smoke.**

End of spec.
