"""Sprint 4 - Training data builder untuk LSTM-AE intermittent fault detector.

Skeleton implementation - blocked on Sprint 3.3 baseline accumulation reach >=3 months.
Sampai data cukup, file ini berfungsi untuk:
    - Define BaselineLoader API (read parquet dari Sprint 3.3 output)
    - Define SequenceBuilder API (resample 5-min -> 15-min, build sequences)
    - Synthetic test untuk verify interface bekerja sebelum data real available

Pipeline:
    baseline/{YYYY-MM}/*.parquet
        -> BaselineLoader.load_range(start, end) -> concat DataFrame
        -> SequenceBuilder.resample(freq="15min", method="mean|median|last")
        -> SequenceBuilder.build_sequences(window_size=96, stride=1)
        -> (n_windows, 96, n_features) numpy array
        -> normalize_sequences(method="zscore") -> normalized + stats
        -> train_val_test_split(temporal=True)

Sequence shape default (per spec):
    window_size = 96 timesteps (24 jam @ 15-min)
    features    = PV1..PV28 input current per inverter (atau aggregate metrics)

Resample comparison (per user earlier): mean, median, last.
"""
from __future__ import annotations

import glob
import os
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


DEFAULT_BASELINE_DIR: str = "baseline"
DEFAULT_RESAMPLE_FREQ: str = "15min"
DEFAULT_WINDOW_SIZE: int = 96   # 24h @ 15-min
DEFAULT_STRIDE: int = 1
DEFAULT_NORMALIZE_METHOD: str = "zscore"   # "zscore" | "minmax"


def _ensure_pyarrow() -> None:
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: pyarrow")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyarrow"])


# ============================================================================
# BaselineLoader
# ============================================================================


@dataclass
class BaselineLoader:
    """Load akumulasi daily parquet dari Sprint 3.3 output.

    Parameters
    ----------
    base_dir : str
        Root directory (default ``"baseline"``).
    glob_pattern : str
        Pattern untuk discover files (default ``"*/*.parquet"`` -> {YYYY-MM}/{YYYY-MM-DD}.parquet).
    """

    base_dir: str = DEFAULT_BASELINE_DIR
    glob_pattern: str = "*/*.parquet"

    def list_files(self) -> List[str]:
        """Return semua parquet files yang match pattern, sorted by filename."""
        pattern = os.path.join(self.base_dir, self.glob_pattern)
        return sorted(glob.glob(pattern))

    def load_all(self) -> pd.DataFrame:
        """Concat semua daily parquet jadi satu DataFrame."""
        _ensure_pyarrow()
        files = self.list_files()
        if not files:
            warnings.warn(
                f"[training_data] no parquet files di {self.base_dir!r} (pattern {self.glob_pattern!r})",
                stacklevel=2,
            )
            return pd.DataFrame()
        dfs = [pd.read_parquet(f) for f in files]
        return pd.concat(dfs, ignore_index=True)

    def load_range(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Load parquet di range tanggal (inclusive)."""
        _ensure_pyarrow()
        files = self.list_files()
        if not files:
            return pd.DataFrame()

        filtered = []
        for f in files:
            # Filename pattern: {YYYY-MM-DD}.parquet
            basename = os.path.basename(f).replace(".parquet", "")
            try:
                file_date = pd.Timestamp(basename).normalize()
            except Exception:
                continue
            if start_date and file_date < pd.Timestamp(start_date):
                continue
            if end_date and file_date > pd.Timestamp(end_date):
                continue
            filtered.append(f)

        if not filtered:
            warnings.warn(
                f"[training_data] no files di range {start_date!r}..{end_date!r}",
                stacklevel=2,
            )
            return pd.DataFrame()

        dfs = [pd.read_parquet(f) for f in filtered]
        return pd.concat(dfs, ignore_index=True)

    def summary(self) -> Dict[str, Any]:
        """Print manifest summary: dates covered, row counts."""
        files = self.list_files()
        if not files:
            return {"n_files": 0, "dates": []}
        dates = []
        for f in files:
            basename = os.path.basename(f).replace(".parquet", "")
            try:
                dates.append(pd.Timestamp(basename))
            except Exception:
                pass
        return {
            "n_files": len(files),
            "dates": dates,
            "date_min": min(dates) if dates else None,
            "date_max": max(dates) if dates else None,
            "coverage_days": (max(dates) - min(dates)).days + 1 if dates else 0,
        }


# ============================================================================
# SequenceBuilder
# ============================================================================


@dataclass
class SequenceMetadata:
    """Per-window metadata (inverter_id, start/end timestamps, feature names)."""

    inverter_id: str
    window_start: pd.Timestamp
    window_end: pd.Timestamp
    n_features: int
    feature_cols: List[str] = field(default_factory=list)


class SequenceBuilder:
    """Build (n_windows, seq_len, n_features) tensor dari DataFrame.

    Step-by-step:
        1. resample(freq) : 5-min -> 15-min (mean/median/last comparison)
        2. select feature columns (default: PV{1..28} input current(A))
        3. build sliding windows (window_size timesteps, stride steps)
        4. emit np.ndarray + list of SequenceMetadata

    Pakai per-inverter mode: build windows independent per Inverter_ID.
    """

    def __init__(
        self,
        window_size: int = DEFAULT_WINDOW_SIZE,
        stride: int = DEFAULT_STRIDE,
        resample_freq: str = DEFAULT_RESAMPLE_FREQ,
        resample_method: str = "mean",  # "mean" | "median" | "last"
        feature_cols_pattern: str = "PV{n} input current(A)",
        feature_range: Tuple[int, int] = (1, 28),
        timestamp_col: str = "Start Time",
        inverter_col: str = "Inverter_ID",
    ):
        self.window_size = int(window_size)
        self.stride = int(stride)
        self.resample_freq = str(resample_freq)
        if resample_method not in {"mean", "median", "last"}:
            raise ValueError(
                f"resample_method must be 'mean'|'median'|'last', got {resample_method!r}"
            )
        self.resample_method = resample_method
        self.feature_cols_pattern = feature_cols_pattern
        self.feature_range = feature_range
        self.timestamp_col = timestamp_col
        self.inverter_col = inverter_col

    def _select_feature_cols(self, df: pd.DataFrame) -> List[str]:
        out = []
        for n in range(self.feature_range[0], self.feature_range[1] + 1):
            col = self.feature_cols_pattern.format(n=n)
            if col in df.columns:
                out.append(col)
        return out

    def resample(self, df: pd.DataFrame) -> pd.DataFrame:
        """Resample 5-min -> resample_freq, per Inverter_ID."""
        if df.empty:
            return df
        df = df.copy()
        df[self.timestamp_col] = pd.to_datetime(df[self.timestamp_col], errors="coerce")
        df = df.dropna(subset=[self.timestamp_col])
        df = df.set_index(self.timestamp_col)

        feature_cols = self._select_feature_cols(df)
        if not feature_cols:
            return pd.DataFrame()

        agg_func = {
            "mean": "mean",
            "median": "median",
            "last": "last",
        }[self.resample_method]

        out_dfs = []
        for inv, grp in df.groupby(self.inverter_col):
            resampled = grp[feature_cols].resample(self.resample_freq).agg(agg_func)
            resampled[self.inverter_col] = inv
            out_dfs.append(resampled)

        if not out_dfs:
            return pd.DataFrame()
        out = pd.concat(out_dfs)
        out = out.reset_index().rename(columns={"index": self.timestamp_col})
        return out

    def build_sequences(
        self,
        df: pd.DataFrame,
    ) -> Tuple[np.ndarray, List[SequenceMetadata]]:
        """Build (n_windows, window_size, n_features) array + per-window metadata.

        Returns
        -------
        sequences : np.ndarray shape (n_windows, window_size, n_features)
        metadata  : list of SequenceMetadata (length n_windows)
        """
        if df.empty:
            return np.empty((0, self.window_size, 0)), []

        df = df.copy()
        df[self.timestamp_col] = pd.to_datetime(df[self.timestamp_col], errors="coerce")
        df = df.dropna(subset=[self.timestamp_col]).sort_values(self.timestamp_col)

        feature_cols = self._select_feature_cols(df)
        if not feature_cols:
            warnings.warn(
                "[training_data] no feature cols found, return empty.",
                stacklevel=2,
            )
            return np.empty((0, self.window_size, 0)), []

        windows = []
        metas = []
        for inv, grp in df.groupby(self.inverter_col):
            grp = grp.sort_values(self.timestamp_col).reset_index(drop=True)
            feats = grp[feature_cols].to_numpy(dtype=np.float32)
            n_rows = len(feats)
            if n_rows < self.window_size:
                continue
            for start in range(0, n_rows - self.window_size + 1, self.stride):
                end = start + self.window_size
                windows.append(feats[start:end])
                metas.append(SequenceMetadata(
                    inverter_id=str(inv),
                    window_start=grp.iloc[start][self.timestamp_col],
                    window_end=grp.iloc[end - 1][self.timestamp_col],
                    n_features=len(feature_cols),
                    feature_cols=list(feature_cols),
                ))

        if not windows:
            return np.empty((0, self.window_size, 0)), []
        return np.stack(windows, axis=0), metas

    def process(
        self,
        df: pd.DataFrame,
    ) -> Tuple[np.ndarray, List[SequenceMetadata]]:
        """End-to-end: resample -> build_sequences."""
        resampled = self.resample(df)
        return self.build_sequences(resampled)


# ============================================================================
# Normalization
# ============================================================================


@dataclass
class NormalizationStats:
    """Fitted normalization parameters (per-feature)."""

    method: str
    mean: Optional[np.ndarray] = None
    std: Optional[np.ndarray] = None
    min: Optional[np.ndarray] = None
    max: Optional[np.ndarray] = None

    def transform(self, x: np.ndarray) -> np.ndarray:
        if self.method == "zscore":
            return (x - self.mean) / np.where(self.std == 0, 1.0, self.std)
        if self.method == "minmax":
            rng = self.max - self.min
            return (x - self.min) / np.where(rng == 0, 1.0, rng)
        raise ValueError(f"unknown method: {self.method}")

    def inverse_transform(self, x: np.ndarray) -> np.ndarray:
        if self.method == "zscore":
            return x * self.std + self.mean
        if self.method == "minmax":
            return x * (self.max - self.min) + self.min
        raise ValueError(f"unknown method: {self.method}")


def fit_normalization(
    sequences: np.ndarray,
    method: str = DEFAULT_NORMALIZE_METHOD,
) -> NormalizationStats:
    """Fit normalization params dari training sequences.

    sequences shape: (n_windows, seq_len, n_features) -> reduce over (0, 1) ke n_features.
    """
    if sequences.size == 0:
        return NormalizationStats(method=method)
    flat = sequences.reshape(-1, sequences.shape[-1])
    if method == "zscore":
        return NormalizationStats(method="zscore", mean=flat.mean(axis=0), std=flat.std(axis=0))
    if method == "minmax":
        return NormalizationStats(method="minmax", min=flat.min(axis=0), max=flat.max(axis=0))
    raise ValueError(f"unknown method: {method}")


def train_val_test_split(
    sequences: np.ndarray,
    metadata: List[SequenceMetadata],
    train_frac: float = 0.7,
    val_frac: float = 0.15,
    temporal: bool = True,
) -> Dict[str, Any]:
    """Split sequences ke train/val/test.

    temporal=True: chronological split (no shuffle) - preserved untuk time-series.
    temporal=False: random shuffle.
    """
    n = len(sequences)
    if n == 0:
        return {"train": np.empty((0, *sequences.shape[1:])),
                "val": np.empty((0, *sequences.shape[1:])),
                "test": np.empty((0, *sequences.shape[1:])),
                "train_meta": [], "val_meta": [], "test_meta": []}

    if temporal:
        # Sort by window_start timestamp
        order = sorted(range(n), key=lambda i: metadata[i].window_start)
    else:
        rng = np.random.default_rng(42)
        order = list(range(n))
        rng.shuffle(order)

    n_train = int(n * train_frac)
    n_val = int(n * val_frac)
    train_idx = order[:n_train]
    val_idx = order[n_train:n_train + n_val]
    test_idx = order[n_train + n_val:]

    return {
        "train": sequences[train_idx],
        "val": sequences[val_idx],
        "test": sequences[test_idx],
        "train_meta": [metadata[i] for i in train_idx],
        "val_meta": [metadata[i] for i in val_idx],
        "test_meta": [metadata[i] for i in test_idx],
    }


if __name__ == "__main__":
    # Synthetic smoke test (tanpa real parquet baseline).
    import sys
    sys.path.insert(0, ".claude/worktrees/modest-shockley-9c31f4")

    # Buat synthetic 3-day baseline (3 inverters × 3 days × 288 timestamps @ 5-min).
    rng = np.random.default_rng(42)
    all_rows = []
    for day_offset in range(3):
        start = pd.Timestamp("2026-05-14") + pd.Timedelta(days=day_offset)
        t = pd.date_range(start, start + pd.Timedelta(hours=23, minutes=55), freq="5min")
        hours_of_day = (t.hour + t.minute / 60.0)
        sun = np.where(
            (hours_of_day >= 6) & (hours_of_day <= 18),
            np.sin(np.pi * (hours_of_day - 6) / 12) ** 2,
            0.0,
        )
        for inv in ["WB05-INV01", "WB05-INV02", "WB02-INV05"]:
            for ts_i, ts in enumerate(t):
                row = {"Inverter_ID": inv, "Start Time": ts}
                for pv_n in range(1, 11):
                    row[f"PV{pv_n} input current(A)"] = 13.0 * sun[ts_i] + rng.normal(0, 0.1)
                all_rows.append(row)
    df = pd.DataFrame(all_rows)
    print(f"[training_data] synthetic baseline: {df.shape}")

    # SequenceBuilder
    builder = SequenceBuilder(
        window_size=96,        # 24h @ 15-min
        stride=1,
        resample_freq="15min",
        resample_method="mean",
        feature_range=(1, 10),
    )
    sequences, metas = builder.process(df)
    print(f"[training_data] sequences shape: {sequences.shape}")
    print(f"[training_data] n_metadata: {len(metas)}")
    assert sequences.shape[1] == 96
    assert sequences.shape[2] == 10  # 10 features (PV1..PV10)

    # Normalize
    stats = fit_normalization(sequences, method="zscore")
    print(f"[training_data] zscore mean shape: {stats.mean.shape}, std shape: {stats.std.shape}")
    normalized = stats.transform(sequences)
    assert abs(normalized.mean()) < 0.1, f"normalized mean should be ~0, got {normalized.mean()}"

    # Split
    splits = train_val_test_split(sequences, metas, train_frac=0.7, val_frac=0.15)
    print(f"[training_data] split shapes: "
          f"train={splits['train'].shape} val={splits['val'].shape} test={splits['test'].shape}")
    assert splits["train"].shape[0] + splits["val"].shape[0] + splits["test"].shape[0] == sequences.shape[0]

    print("\n[training_data] smoke OK")
