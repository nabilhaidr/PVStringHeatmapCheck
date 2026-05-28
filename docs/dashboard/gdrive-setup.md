# Google Drive Setup

## Public Manifest Mode

Mode ini tidak butuh Google Cloud Console dan cocok untuk Streamlit Community
Cloud.

1. Upload file data ke Google Drive.
2. Set file data yang akan dibaca dashboard ke "Anyone with the link can view".
3. Buka manifest Google Sheet yang sudah ada.
4. Jalankan Apps Script `docs/dashboard/apps-script-manifest-sync.js` untuk
   mengisi kolom URL/file ID otomatis dari folder Drive.
5. Publish sheet sebagai CSV.
6. Isi Streamlit secrets:

```toml
[gdrive_public]
manifest_csv_url = "https://docs.google.com/spreadsheets/d/e/.../pub?output=csv"

[gdrive]
use_service_account = false
```

Manifest lama dari baseline tetap boleh dipakai. Kolom `file_csv` yang berisi
path seperti `baseline/2026-05/2026-05-14.csv` dipakai sebagai nama/path
display. Agar file bisa didownload tanpa API, sheet manifest harus punya salah
satu kolom berikut:

```csv
date,file_csv,baseline_csv_file_id,findings_xlsx_file_id,findings_jsonl_file_id
2026-05-14,baseline/2026-05/2026-05-14.csv,DRIVE_ID_CSV,DRIVE_ID_XLSX,DRIVE_ID_JSONL
```

Atau pakai public sharing URL:

```csv
date,file_csv,baseline_csv_url,findings_xlsx_url,findings_jsonl_url
2026-05-14,baseline/2026-05/2026-05-14.csv,https://drive.google.com/file/d/.../view,https://drive.google.com/file/d/.../view,https://drive.google.com/file/d/.../view
```

Kolom findings boleh kosong per tanggal. Dashboard tetap memilih xlsx sebagai
primary input jika `findings_xlsx_*` tersedia, dan memakai jsonl hanya sebagai
fallback Findings-only.

### Auto-fill Manifest Gratis Dengan Apps Script

Google Sheet bisa mengisi kolom link tanpa Google Cloud Console:

1. Buka Google Sheet manifest.
2. Pilih `Extensions > Apps Script`.
3. Paste isi `docs/dashboard/apps-script-manifest-sync.js`.
4. Isi:

```javascript
FINDINGS_FOLDER_ID: "folder-output-id",
BASELINE_FOLDER_ID: "folder-baseline-id",
```

5. Run function `syncDashboardManifest`.
6. Approve permission prompt dari Google.
7. Publish sheet sebagai CSV, lalu pakai URL publish itu di
   `[gdrive_public].manifest_csv_url`.

Script ini scan folder output untuk:

- `m2_findings_YYYYMMDD.xlsx`
- `m2_findings_YYYYMMDD.jsonl`

Script juga scan baseline folder beserta subfolder bulan untuk:

- `YYYY-MM-DD.csv`

Kolom yang ditambahkan/diisi:

```csv
baseline_csv_name,baseline_csv_file_id,baseline_csv_url
findings_xlsx_name,findings_xlsx_file_id,findings_xlsx_url
findings_jsonl_name,findings_jsonl_file_id,findings_jsonl_url
```

Kolom metric lama seperti `rows_kept`, `rows_skipped_findings`, dan `file_csv`
tetap dipertahankan.

`use_service_account = false` mematikan fallback service account. Key
`service_account_json` boleh tetap ada di secrets, tetapi tidak akan dipakai
selama opsi ini false.

## Service Account Mode

Mode ini tetap didukung untuk deployment lama.

1. Buat Google Cloud project.
2. Enable Google Drive API.
3. Buat service account.
4. Download JSON key.
5. Share Drive folder output dan folder baseline ke email service account.
6. Paste JSON key ke Streamlit secrets sebagai multiline string:

```toml
[gdrive]
use_service_account = true
findings_folder_id = "folder-output-id"
baseline_folder_id = "folder-baseline-id"
service_account_json = '''
{ ... }
'''
```

Findings/output folder berisi:

- `m2_findings_YYYYMMDD.xlsx`
- `m2_findings_YYYYMMDD.jsonl` (fallback Findings-only)

Baseline folder berisi subfolder bulan:

```text
baseline-folder/
  2026-05/
    2026-05-14.csv
    2026-05-15.csv
  2026-06/
    2026-06-01.csv
```

Kalau xlsx dan jsonl tersedia untuk tanggal yang sama, dashboard memakai xlsx.
JSONL hanya dipakai jika xlsx tidak ada atau gagal dibaca.

Untuk struktur lama dengan semua file dalam satu folder, gunakan:

```toml
[gdrive]
folder_id = "shared-folder-id"
service_account_json = '''
{ ... }
'''
```
