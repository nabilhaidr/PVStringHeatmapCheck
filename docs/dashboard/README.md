# PV Pipeline Dashboard

Streamlit dashboard untuk output M2 pipeline.

## Data Files

Upload dua tipe file ke Google Drive folder yang sama:

- `m2_findings_YYYYMMDD.xlsx` dari folder `outputs/`
- `YYYY-MM-DD.csv` dari folder `baseline/YYYY-MM/`

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

## Streamlit Cloud

Entry point: `streamlit_app.py`.

Secrets di Streamlit Cloud mengikuti format `.streamlit/secrets.toml.example`.
Share Google Drive folder ke `client_email` service account dengan minimal
Viewer permission.
