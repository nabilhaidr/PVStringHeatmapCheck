# PV Pipeline Dashboard

Streamlit dashboard untuk output M2 pipeline.

## Data Files

Dashboard mendukung dua mode data source:

1. Public manifest mode: gratis, tanpa Google Cloud Console. Manifest Google
   Sheet dipublish sebagai CSV dan berisi public Drive URL/file ID untuk tiap
   tanggal.
2. Service account mode: fallback lama, memakai Google Drive API dan folder ID.

Kalau `[gdrive_public]` berisi `manifest_csv_url` atau `manifest_csv_path`,
dashboard memakai public manifest. Kalau tidak ada, dashboard memakai `[gdrive]`
service account seperti sebelumnya.

Data yang didukung:

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
password dan salah satu mode data source.

Public manifest mode:

```toml
[gdrive_public]
manifest_csv_url = "https://docs.google.com/spreadsheets/d/e/.../pub?output=csv"

[gdrive]
use_service_account = false
```

Kolom manifest minimal mengikuti file `manifest.csv` dari baseline. Kolom baru
bisa ditambahkan di Google Sheet yang sama, tanpa membuat manifest baru:

```csv
date,file_csv,baseline_csv_file_id,findings_xlsx_file_id,findings_jsonl_file_id
2026-05-14,baseline/2026-05/2026-05-14.csv,DRIVE_ID_CSV,DRIVE_ID_XLSX,DRIVE_ID_JSONL
```

Alternatif kolom `*_url` juga didukung, misalnya `baseline_csv_url`,
`findings_xlsx_url`, dan `findings_jsonl_url`.

Untuk auto-fill gratis tanpa Google Cloud Console, pakai template
`docs/dashboard/apps-script-manifest-sync.js` di Google Sheet manifest. Script
itu membaca folder Drive output dan baseline, lalu mengisi:

```csv
baseline_csv_name,baseline_csv_file_id,baseline_csv_url
findings_xlsx_name,findings_xlsx_file_id,findings_xlsx_url
findings_jsonl_name,findings_jsonl_file_id,findings_jsonl_url
```

Dashboard juga menerima alias `*_drive_link` / `*_drive_url` kalau manifest
dibuat dengan nama kolom tersebut.

Service account fallback:

```toml
[gdrive]
use_service_account = true
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
Untuk public manifest mode, set file data Google Drive ke "Anyone with the link
can view", lalu publish Google Sheet manifest sebagai CSV. Untuk service account
mode, share kedua Google Drive folder ke `client_email` service account dengan
minimal Viewer permission.
