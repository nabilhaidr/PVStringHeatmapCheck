# PV Pipeline Dashboard

Streamlit dashboard untuk output M2 pipeline.

## Data Files

Dashboard bisa membaca dua Google Drive folder terpisah:

- Findings/output folder:
  - `m2_findings_YYYYMMDD.xlsx` dari folder `outputs/`
  - `m2_findings_YYYYMMDD.jsonl` dari folder `outputs/` sebagai fallback
    Findings-only kalau xlsx tidak tersedia atau gagal dibaca.
- Baseline folder:
  - subfolder `YYYY-MM/`
  - file `YYYY-MM-DD.csv` di dalam subfolder bulan tersebut.

Kalau `m2_findings_YYYYMMDD.xlsx` tersedia, dashboard selalu memakai xlsx
sebagai primary input supaya detector artifact sheets tetap tersedia. JSONL
hanya mengisi sheet `Findings`, jadi Detectors page akan menampilkan info state
untuk artifact sheets yang tidak ada.

Heatmap M0 memakai baseline CSV. File ini sudah difilter oleh
`BaselineAccumulator`, jadi row fault/high-severity yang dibuang oleh baseline
filter tidak akan terlihat di heatmap.

## Local Run

```bash
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

Copy `.streamlit/secrets.toml.example` ke `.streamlit/secrets.toml`, lalu isi
password, folder ID, dan service account JSON.

```toml
[gdrive]
findings_folder_id = "folder-output-id"
baseline_folder_id = "folder-baseline-id"
service_account_json = '''
{ ... }
'''
```

Untuk deployment lama yang masih memakai satu folder bersama, `folder_id = "..."`
tetap didukung sebagai fallback.

## Streamlit Cloud

Entry point: `streamlit_app.py`.

Secrets di Streamlit Cloud mengikuti format `.streamlit/secrets.toml.example`.
Share kedua Google Drive folder ke `client_email` service account dengan
minimal Viewer permission.
