# Google Drive Setup

1. Buat Google Cloud project.
2. Enable Google Drive API.
3. Buat service account.
4. Download JSON key.
5. Share Drive folder output dan folder baseline ke email service account.
6. Paste JSON key ke Streamlit secrets sebagai multiline string:

```toml
[gdrive]
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
