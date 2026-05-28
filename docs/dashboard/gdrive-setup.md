# Google Drive Setup

1. Buat Google Cloud project.
2. Enable Google Drive API.
3. Buat service account.
4. Download JSON key.
5. Share Drive folder yang berisi dashboard artifacts ke email service account.
6. Paste JSON key ke Streamlit secrets sebagai multiline string:

```toml
[gdrive]
folder_id = "..."
service_account_json = """
{ ... }
"""
```

Folder harus berisi:

- `m2_findings_YYYYMMDD.xlsx`
- `YYYY-MM-DD.csv`
