"""Sprint 4 - Training LSTM-AE dari baseline CSV (Google Drive).

BaselineAccumulator (Sprint 3.3) menyimpan hari NORMAL sebagai
``baseline/{YYYY-MM}/{YYYY-MM-DD}.csv``. ``BaselineLoader`` di
pv_pipeline.training_data hanya membaca parquet, sedangkan file di Drive
mayoritas CSV -- script ini membaca CSV-nya langsung.

Cara pakai di Google Colab (torch sudah tersedia, data via Drive mount):

    from google.colab import drive
    drive.mount("/content/drive")
    !git clone https://github.com/nabilhaidr/PVStringHeatmapCheck.git
    %cd PVStringHeatmapCheck
    !python train_lstm_ae.py \
        --baseline-dir "/content/drive/MyDrive/<path-ke>/baseline" \
        --output-dir   "/content/drive/MyDrive/<path-ke>/models"

Lokal juga bisa (torch akan auto-install ~200MB saat pertama dipakai)
dengan folder baseline hasil download dari Drive.

Pipeline:
    1. Discover ``{YYYY-MM}/{YYYY-MM-DD}.csv`` (subfolder bulan; file flat
       ``YYYY-MM-DD.csv`` di root folder juga diterima). manifest.csv skip.
    2. Per inverter-hari: resample 5-min -> 15-min, reindex ke grid penuh
       00:00..23:45 (96 slot), NaN -> 0.0 A. Baseline hanya menyimpan jam
       operasional (~12 jam); malam memang harus diisi 0 A supaya window
       24 jam per spec (96 timesteps @ 15-min) terbentuk.
    3. Split temporal 70/15/15, fit z-score normalization di train saja.
    4. train_lstm_ae (MSE, Adam, early stopping di val loss).
    5. threshold = mean + sigma*std reconstruction error di train set.
    6. save_model_artifacts -> {output_dir}/{name}_{timestamp}.pt + .json.

Setelah model tersimpan, wire ke notebook (Cell 4 submodules):

    from pv_pipeline.lstm_ae import M2bIntermittentDetector
    sm_lstm = M2bIntermittentDetector(
        model_path="models/lstm_ae_<ts>.pt",
        meta_path="models/lstm_ae_<ts>.json",
        enabled=True,
    )
"""
from __future__ import annotations

import argparse
import glob
import os
import re
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from pv_pipeline.training_data import (
    SequenceMetadata,
    build_day_windows,
    fit_normalization,
    train_val_test_split,
)

DEFAULT_FEATURE_PATTERN = "PV{n} input current(A)"
DEFAULT_PV_RANGE: Tuple[int, int] = (1, 28)
DEFAULT_RESAMPLE_FREQ = "15min"
TIMESTAMP_COL = "Start Time"
INVERTER_COL = "Inverter_ID"

_BASELINE_CSV_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.csv$")


def discover_baseline_csvs(
    base_dir: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Tuple[pd.Timestamp, str]]:
    """Return [(date, path)] baseline CSV, sorted by date.

    Scan subfolder bulan (``*/*.csv``) lalu root (``*.csv``). Nama selain
    ``YYYY-MM-DD.csv`` (mis. manifest.csv) di-skip. Kalau satu tanggal muncul
    di subfolder dan root sekaligus, file subfolder yang dipakai.
    """
    paths = sorted(glob.glob(os.path.join(base_dir, "*", "*.csv")))
    paths += sorted(glob.glob(os.path.join(base_dir, "*.csv")))
    by_date: Dict[pd.Timestamp, str] = {}
    for path in paths:
        match = _BASELINE_CSV_RE.match(os.path.basename(path))
        if not match:
            continue
        try:
            day = pd.Timestamp(match.group(1)).normalize()
        except ValueError:
            continue
        if start_date and day < pd.Timestamp(start_date):
            continue
        if end_date and day > pd.Timestamp(end_date):
            continue
        by_date.setdefault(day, path)
    return sorted(by_date.items())


def feature_columns(
    pv_range: Tuple[int, int] = DEFAULT_PV_RANGE,
    pattern: str = DEFAULT_FEATURE_PATTERN,
) -> List[str]:
    return [pattern.format(n=n) for n in range(pv_range[0], pv_range[1] + 1)]


def load_training_windows(
    files: List[Tuple[pd.Timestamp, str]],
    feature_cols: List[str],
    *,
    resample_method: str = "mean",
) -> Tuple[np.ndarray, List[SequenceMetadata]]:
    """Baca tiap CSV (kolom seperlunya saja) lalu concat window semua hari."""
    wanted = set(feature_cols) | {TIMESTAMP_COL, INVERTER_COL}
    all_windows: List[np.ndarray] = []
    all_metas: List[SequenceMetadata] = []
    for i, (day, path) in enumerate(files, start=1):
        df = pd.read_csv(path, usecols=lambda c: c in wanted)
        windows, metas = build_day_windows(
            df, day, feature_cols, resample_method=resample_method,
        )
        all_windows.append(windows)
        all_metas.extend(metas)
        print(f"[{i}/{len(files)}] {day.date()}  windows={len(metas)}")
    if not all_metas:
        n_steps = int(pd.Timedelta("1D") / pd.Timedelta(DEFAULT_RESAMPLE_FREQ))
        return np.empty((0, n_steps, len(feature_cols)), dtype=np.float32), []
    return np.concatenate([w for w in all_windows if len(w)], axis=0), all_metas


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Train LSTM-AE intermittent detector dari baseline CSV.",
    )
    parser.add_argument(
        "--baseline-dir", required=True,
        help="Folder baseline berisi {YYYY-MM}/{YYYY-MM-DD}.csv "
             "(mis. Drive mount: /content/drive/MyDrive/.../baseline)",
    )
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--output-dir", default="models")
    parser.add_argument("--name", default="lstm_ae")
    parser.add_argument(
        "--resample-method", default="mean", choices=["mean", "median", "last"],
    )
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--hidden-size", type=int, default=64)
    parser.add_argument("--num-layers", type=int, default=2)
    parser.add_argument("--sigma", type=float, default=3.0,
                        help="threshold = mean + sigma*std error train set")
    parser.add_argument("--device", default="auto", help="auto | cpu | cuda")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = _parse_args(argv)

    files = discover_baseline_csvs(args.baseline_dir, args.start_date, args.end_date)
    if not files:
        raise SystemExit(
            f"[train_lstm_ae] tidak ada baseline CSV di {args.baseline_dir!r} "
            f"(range {args.start_date}..{args.end_date}). "
            "Cek path Drive mount / nama file YYYY-MM-DD.csv."
        )
    print(f"[train_lstm_ae] {len(files)} hari baseline "
          f"({files[0][0].date()} .. {files[-1][0].date()})")

    feature_cols = feature_columns()
    sequences, metas = load_training_windows(
        files, feature_cols, resample_method=args.resample_method,
    )
    if len(sequences) == 0:
        raise SystemExit("[train_lstm_ae] 0 window terbentuk -- cek isi CSV.")
    n_inverters = len({m.inverter_id for m in metas})
    print(f"[train_lstm_ae] sequences={sequences.shape} "
          f"(windows, steps, features), inverters={n_inverters}")

    splits = train_val_test_split(sequences, metas, train_frac=0.7, val_frac=0.15)
    norm_stats = fit_normalization(splits["train"], method="zscore")
    train_n = norm_stats.transform(splits["train"]).astype(np.float32)
    val_n = norm_stats.transform(splits["val"]).astype(np.float32)
    test_n = norm_stats.transform(splits["test"]).astype(np.float32)
    print(f"[train_lstm_ae] split train={len(train_n)} val={len(val_n)} test={len(test_n)}")

    # Torch baru dibutuhkan dari sini (lazy auto-install di pv_pipeline.lstm_ae).
    from pv_pipeline.lstm_ae import (
        build_lstm_autoencoder,
        compute_anomaly_threshold,
        compute_reconstruction_errors,
        save_model_artifacts,
        train_lstm_ae,
    )

    model = build_lstm_autoencoder(
        n_features=sequences.shape[2],
        seq_len=sequences.shape[1],
        hidden_size=args.hidden_size,
        num_layers=args.num_layers,
    )
    history = train_lstm_ae(
        model,
        train_n,
        val_sequences=val_n if len(val_n) else None,
        epochs=args.epochs,
        batch_size=args.batch_size,
        patience=args.patience,
        device=args.device,
    )
    print(f"[train_lstm_ae] best_epoch={history.best_epoch + 1} "
          f"best_val_loss={history.best_val_loss:.6f}")

    errors_train = compute_reconstruction_errors(
        model, train_n, batch_size=args.batch_size, device=args.device,
    )
    threshold = compute_anomaly_threshold(errors_train, sigma=args.sigma)
    print(f"[train_lstm_ae] train error mean={errors_train.mean():.6f} "
          f"std={errors_train.std():.6f} -> threshold={threshold:.6f}")
    if len(test_n):
        errors_test = compute_reconstruction_errors(
            model, test_n, batch_size=args.batch_size, device=args.device,
        )
        pct_flag = float((errors_test > threshold).mean() * 100.0)
        print(f"[train_lstm_ae] sanity test-set: {pct_flag:.2f}% window > threshold "
              "(data NORMAL, ekspektasi kecil ~<2%)")

    paths = save_model_artifacts(
        model, norm_stats, threshold, feature_cols,
        output_dir=args.output_dir, name=args.name,
    )
    print(f"[train_lstm_ae] model : {paths['model']}")
    print(f"[train_lstm_ae] meta  : {paths['meta']}")
    print(
        "[train_lstm_ae] wiring notebook:\n"
        "    from pv_pipeline.lstm_ae import M2bIntermittentDetector\n"
        "    sm_lstm = M2bIntermittentDetector(\n"
        f"        model_path={paths['model']!r},\n"
        f"        meta_path={paths['meta']!r},\n"
        "        enabled=True,\n"
        "    )"
    )


if __name__ == "__main__":
    main()
