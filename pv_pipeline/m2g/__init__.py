"""M2g -- temuan dari citra udara, bukan dari telemetri listrik.

Satu-satunya jalur masuknya adalah kontrak tabel di
:mod:`pv_pipeline.m2g.visual_cv`. Pemrosesan citra tinggal di repositori CV
yang terpisah; repo ini tidak memuat OpenCV, torch, maupun bobot model.
"""
