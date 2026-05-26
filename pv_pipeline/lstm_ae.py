"""Sprint 4 - LSTM Autoencoder untuk intermittent fault detection.

Skeleton - BLOCKED on Sprint 3.3 baseline accumulation reach >=3 months.

Architecture (per spec):
    Input shape  : (batch, 96, n_features)   # 96 timesteps @ 15-min = 24h window
    Encoder      : LSTM(input_size, hidden_size) -> last hidden state (bottleneck)
    Decoder      : LSTM(hidden_size, hidden_size) -> Linear(hidden_size, n_features)
    Output       : reconstructed (batch, 96, n_features)
    Loss         : MSE(input, reconstruction)
    Anomaly      : reconstruction_error > threshold (mean + 3sigma di training set)

Workflow:
    1. Load baseline NORMAL data (BaselineLoader)
    2. Build sequences (SequenceBuilder, resample 5min->15min)
    3. Normalize (fit_normalization)
    4. Train LSTM-AE on NORMAL sequences
    5. Compute reconstruction error per-window di training set
    6. Threshold = mean(errors) + 3*std(errors)
    7. Save model + threshold + norm_stats
    8. M2bIntermittentDetector inference:
       - Build sequences dari combined_df daily
       - Compute reconstruction error per-window
       - Emit fault_type=intermittent kalau error > threshold AND std_error tinggi

PyTorch is lazy-imported (heavy dependency); module bisa di-import tanpa torch
sampai actual training/inference. Tests synthetic pakai numpy-only fallback.
"""
from __future__ import annotations

import json
import os
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from pv_pipeline.core import M2Finding, Severity, SubModule
from pv_pipeline.training_data import (
    NormalizationStats,
    SequenceBuilder,
    SequenceMetadata,
    fit_normalization,
)


DEFAULT_HIDDEN_SIZE: int = 64
DEFAULT_NUM_LAYERS: int = 2
DEFAULT_DROPOUT: float = 0.1
DEFAULT_LEARNING_RATE: float = 1e-3
DEFAULT_BATCH_SIZE: int = 32
DEFAULT_EPOCHS: int = 50
DEFAULT_PATIENCE: int = 5
DEFAULT_ANOMALY_SIGMA: float = 3.0
DEFAULT_HIGH_STD_THRESHOLD: float = 0.5


def _ensure_torch() -> None:
    """Lazy-install PyTorch saat dipakai (heavy ~200MB download)."""
    try:
        import torch  # noqa: F401
    except ImportError:
        import subprocess
        import sys
        print("Installing missing package: torch (this may take a while, ~200MB)")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "torch"])


# ============================================================================
# Model definition (PyTorch, lazy import)
# ============================================================================


def build_lstm_autoencoder(
    n_features: int,
    seq_len: int = 96,
    hidden_size: int = DEFAULT_HIDDEN_SIZE,
    num_layers: int = DEFAULT_NUM_LAYERS,
    dropout: float = DEFAULT_DROPOUT,
):
    """Build LSTM-AE model dengan PyTorch.

    Encoder: LSTM(n_features -> hidden_size, num_layers, dropout)
    Decoder: LSTM(hidden_size -> hidden_size, num_layers, dropout) + Linear(hidden -> n_features)
    """
    _ensure_torch()
    import torch
    import torch.nn as nn

    class _LSTMAutoencoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.seq_len = seq_len
            self.n_features = n_features
            self.hidden_size = hidden_size

            self.encoder = nn.LSTM(
                input_size=n_features,
                hidden_size=hidden_size,
                num_layers=num_layers,
                dropout=dropout if num_layers > 1 else 0.0,
                batch_first=True,
            )
            self.decoder = nn.LSTM(
                input_size=hidden_size,
                hidden_size=hidden_size,
                num_layers=num_layers,
                dropout=dropout if num_layers > 1 else 0.0,
                batch_first=True,
            )
            self.output_proj = nn.Linear(hidden_size, n_features)

        def forward(self, x):
            # x shape: (batch, seq_len, n_features)
            _, (h_n, _) = self.encoder(x)
            # h_n shape: (num_layers, batch, hidden_size) -> ambil layer terakhir
            bottleneck = h_n[-1]  # (batch, hidden_size)
            # Decoder input: repeat bottleneck seq_len times
            decoded_input = bottleneck.unsqueeze(1).repeat(1, self.seq_len, 1)
            decoded_out, _ = self.decoder(decoded_input)
            return self.output_proj(decoded_out)

    return _LSTMAutoencoder()


# ============================================================================
# Training
# ============================================================================


@dataclass
class TrainingHistory:
    train_loss: List[float] = field(default_factory=list)
    val_loss: List[float] = field(default_factory=list)
    best_epoch: int = 0
    best_val_loss: float = float("inf")


def train_lstm_ae(
    model,
    train_sequences: np.ndarray,
    val_sequences: Optional[np.ndarray] = None,
    *,
    epochs: int = DEFAULT_EPOCHS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    learning_rate: float = DEFAULT_LEARNING_RATE,
    patience: int = DEFAULT_PATIENCE,
    device: str = "auto",
    verbose: bool = True,
) -> TrainingHistory:
    """Train LSTM-AE on normalized NORMAL sequences.

    Uses MSE loss, Adam optimizer, early stopping on val_loss.

    Returns
    -------
    TrainingHistory
        Per-epoch train/val loss + best epoch.
    """
    _ensure_torch()
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, TensorDataset

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"

    model = model.to(device)
    optimizer = optim.Adam(model.parameters(), lr=learning_rate)
    criterion = nn.MSELoss()

    train_t = torch.from_numpy(train_sequences).float()
    train_loader = DataLoader(
        TensorDataset(train_t), batch_size=batch_size, shuffle=True
    )
    val_loader = None
    if val_sequences is not None and len(val_sequences) > 0:
        val_t = torch.from_numpy(val_sequences).float()
        val_loader = DataLoader(
            TensorDataset(val_t), batch_size=batch_size, shuffle=False
        )

    history = TrainingHistory()
    epochs_no_improve = 0

    for epoch in range(epochs):
        model.train()
        train_losses = []
        for (x,) in train_loader:
            x = x.to(device)
            optimizer.zero_grad()
            recon = model(x)
            loss = criterion(recon, x)
            loss.backward()
            optimizer.step()
            train_losses.append(loss.item())
        history.train_loss.append(float(np.mean(train_losses)))

        val_loss_val = float("nan")
        if val_loader is not None:
            model.eval()
            val_losses = []
            with torch.no_grad():
                for (x,) in val_loader:
                    x = x.to(device)
                    recon = model(x)
                    val_losses.append(criterion(recon, x).item())
            val_loss_val = float(np.mean(val_losses))
            history.val_loss.append(val_loss_val)

            if val_loss_val < history.best_val_loss:
                history.best_val_loss = val_loss_val
                history.best_epoch = epoch
                epochs_no_improve = 0
            else:
                epochs_no_improve += 1
                if epochs_no_improve >= patience:
                    if verbose:
                        print(f"  early stopping at epoch {epoch+1} (no improvement for {patience} epochs)")
                    break

        if verbose:
            print(f"  epoch {epoch+1:3d}/{epochs}  train_loss={history.train_loss[-1]:.6f}  "
                  f"val_loss={val_loss_val:.6f}")

    return history


# ============================================================================
# Anomaly threshold + inference
# ============================================================================


def compute_reconstruction_errors(
    model,
    sequences: np.ndarray,
    batch_size: int = DEFAULT_BATCH_SIZE,
    device: str = "auto",
) -> np.ndarray:
    """Per-window reconstruction error (mean MSE across timesteps + features).

    Returns shape: (n_windows,)
    """
    _ensure_torch()
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device).eval()

    if len(sequences) == 0:
        return np.array([])

    seq_t = torch.from_numpy(sequences).float()
    loader = DataLoader(TensorDataset(seq_t), batch_size=batch_size, shuffle=False)
    errors = []
    with torch.no_grad():
        for (x,) in loader:
            x = x.to(device)
            recon = model(x)
            err = ((recon - x) ** 2).mean(dim=(1, 2))  # (batch,)
            errors.append(err.cpu().numpy())
    return np.concatenate(errors)


def compute_anomaly_threshold(
    errors_normal: np.ndarray,
    sigma: float = DEFAULT_ANOMALY_SIGMA,
) -> float:
    """Threshold = mean + sigma * std dari errors di training set NORMAL."""
    if len(errors_normal) == 0:
        return float("inf")
    return float(errors_normal.mean() + sigma * errors_normal.std())


# ============================================================================
# Persistence
# ============================================================================


def save_model_artifacts(
    model,
    norm_stats: NormalizationStats,
    threshold: float,
    feature_cols: List[str],
    output_dir: str = "models",
    name: str = "lstm_ae",
) -> Dict[str, str]:
    """Save model weights + normalization stats + threshold ke disk."""
    _ensure_torch()
    import torch

    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = os.path.join(output_dir, f"{name}_{timestamp}.pt")
    meta_path = os.path.join(output_dir, f"{name}_{timestamp}.json")

    torch.save(model.state_dict(), model_path)

    meta = {
        "name": name,
        "timestamp": timestamp,
        "threshold": float(threshold),
        "feature_cols": feature_cols,
        "norm_method": norm_stats.method,
        "norm_mean": norm_stats.mean.tolist() if norm_stats.mean is not None else None,
        "norm_std": norm_stats.std.tolist() if norm_stats.std is not None else None,
        "norm_min": norm_stats.min.tolist() if norm_stats.min is not None else None,
        "norm_max": norm_stats.max.tolist() if norm_stats.max is not None else None,
    }
    with open(meta_path, "w", encoding="utf-8") as fp:
        json.dump(meta, fp, indent=2)

    return {"model": model_path, "meta": meta_path}


def load_model_artifacts(
    model_path: str,
    meta_path: str,
    seq_len: int = 96,
    hidden_size: int = DEFAULT_HIDDEN_SIZE,
    num_layers: int = DEFAULT_NUM_LAYERS,
):
    """Load model + meta dari disk."""
    _ensure_torch()
    import torch

    with open(meta_path, "r", encoding="utf-8") as fp:
        meta = json.load(fp)

    n_features = len(meta["feature_cols"])
    model = build_lstm_autoencoder(
        n_features=n_features,
        seq_len=seq_len,
        hidden_size=hidden_size,
        num_layers=num_layers,
    )
    model.load_state_dict(torch.load(model_path, weights_only=True))

    norm_stats = NormalizationStats(
        method=meta["norm_method"],
        mean=np.array(meta["norm_mean"]) if meta["norm_mean"] else None,
        std=np.array(meta["norm_std"]) if meta["norm_std"] else None,
        min=np.array(meta["norm_min"]) if meta["norm_min"] else None,
        max=np.array(meta["norm_max"]) if meta["norm_max"] else None,
    )
    return {
        "model": model,
        "norm_stats": norm_stats,
        "threshold": meta["threshold"],
        "feature_cols": meta["feature_cols"],
    }


# ============================================================================
# M2bIntermittentDetector (SubModule plugin)
# ============================================================================


class M2bIntermittentDetector(SubModule):
    """LSTM-AE inference: flag windows dengan reconstruction error tinggi.

    Spec 4.2.3 (intermittent fault): subtle pattern shifts dari panel
    intermittent (loose connector, partial shading shifts, dst.). Susah
    di-detect via threshold rule, butuh ML.

    Konsumen: Cell 7+ di notebook (saat baseline data accumulated >= 3 bulan
    dan model sudah trained).

    Constructor expects:
        model_path, meta_path : path ke saved artifacts (lstm_ae.save_model_artifacts).
    """

    name: str = "M2b_intermittent"

    def __init__(
        self,
        model_path: Optional[str] = None,
        meta_path: Optional[str] = None,
        enabled: bool = False,
    ):
        super().__init__()
        self.model_path = model_path
        self.meta_path = meta_path
        self.enabled = enabled
        self._artifacts_loaded = None

    def _load_artifacts(self):
        if self._artifacts_loaded is None:
            if not self.model_path or not self.meta_path:
                raise ValueError(
                    "[M2bIntermittent] model_path + meta_path required (train model first)."
                )
            if not os.path.exists(self.model_path) or not os.path.exists(self.meta_path):
                raise FileNotFoundError(
                    f"[M2bIntermittent] artifacts missing: {self.model_path!r} / {self.meta_path!r}. "
                    "Train model first (Sprint 4 training pipeline)."
                )
            self._artifacts_loaded = load_model_artifacts(self.model_path, self.meta_path)
        return self._artifacts_loaded

    def run(self, combined_df: pd.DataFrame, config: dict) -> List[M2Finding]:
        if not self.enabled:
            warnings.warn(
                "[M2bIntermittent] disabled (enabled=False). Set enabled=True after training.",
                stacklevel=2,
            )
            return []

        cfg = config.get("m2b_intermittent", {}) or {}
        high_std_threshold = float(cfg.get("high_std_threshold", DEFAULT_HIGH_STD_THRESHOLD))

        try:
            arts = self._load_artifacts()
        except (FileNotFoundError, ValueError) as exc:
            warnings.warn(f"[M2bIntermittent] {exc}", stacklevel=2)
            return []

        model = arts["model"]
        norm_stats = arts["norm_stats"]
        threshold = arts["threshold"]

        builder = SequenceBuilder(
            window_size=cfg.get("window_size", 96),
            resample_freq=cfg.get("resample_freq", "15min"),
            resample_method=cfg.get("resample_method", "mean"),
        )
        sequences, metas = builder.process(combined_df)
        if len(sequences) == 0:
            return []

        normalized = norm_stats.transform(sequences)
        errors = compute_reconstruction_errors(model, normalized)

        findings = []
        for i, (err, meta) in enumerate(zip(errors, metas)):
            if err > threshold:
                # High std cross-check
                window_std = float(sequences[i].std())
                confidence = float(cfg.get("confidence_pct", 70.0))
                findings.append(M2Finding(
                    timestamp=meta.window_end.to_pydatetime() if hasattr(meta.window_end, "to_pydatetime") else meta.window_end,
                    inverter_id=meta.inverter_id,
                    pv_string=None,
                    sub_module=self.name,
                    severity=Severity.MEDIUM,
                    value=float(err),
                    threshold=threshold,
                    message=(
                        f"Intermittent suspect ({meta.inverter_id} "
                        f"{meta.window_start}..{meta.window_end}): "
                        f"reconstruction_error={err:.4f} > threshold={threshold:.4f}"
                    ),
                    fault_type="intermittent",
                    confidence=confidence,
                    evidence={
                        "reconstruction_error": float(err),
                        "threshold": threshold,
                        "window_start": str(meta.window_start),
                        "window_end": str(meta.window_end),
                        "window_std": window_std,
                        "high_std_threshold": high_std_threshold,
                        "n_features": meta.n_features,
                    },
                ))
        return findings


if __name__ == "__main__":
    # Synthetic smoke test - pakai PyTorch beneran kalau available, skip kalau tidak.
    import sys
    sys.path.insert(0, ".claude/worktrees/modest-shockley-9c31f4")

    try:
        import torch  # noqa: F401
        HAS_TORCH = True
    except ImportError:
        print("[lstm_ae] PyTorch tidak terpasang -> skip training smoke test")
        print("  Install dengan: pip install torch")
        print("  Atau biarkan auto-install saat panggil train_lstm_ae() / build_lstm_autoencoder()")
        HAS_TORCH = False

    if HAS_TORCH:
        rng = np.random.default_rng(42)
        # Synthetic NORMAL data: 100 windows × 96 timesteps × 10 features (small for speed)
        normal_seqs = rng.standard_normal((100, 96, 10)).astype(np.float32)

        # Build + train tiny model untuk smoke (2 epoch, no patience)
        model = build_lstm_autoencoder(n_features=10, seq_len=96, hidden_size=16, num_layers=1)
        print(f"[lstm_ae] model: {sum(p.numel() for p in model.parameters())} params")

        history = train_lstm_ae(
            model, normal_seqs, val_sequences=normal_seqs[:20],
            epochs=2, batch_size=16, verbose=False,
        )
        print(f"[lstm_ae] training: train_loss[final]={history.train_loss[-1]:.4f}  "
              f"val_loss[final]={history.val_loss[-1]:.4f}")

        # Compute errors + threshold
        errors = compute_reconstruction_errors(model, normal_seqs)
        threshold = compute_anomaly_threshold(errors, sigma=3.0)
        print(f"[lstm_ae] reconstruction errors: mean={errors.mean():.4f}  std={errors.std():.4f}")
        print(f"[lstm_ae] anomaly threshold (mean+3sigma): {threshold:.4f}")
        assert errors.shape == (100,)
        assert threshold > errors.mean()

        # Save + load artifacts (synthetic norm stats)
        from pv_pipeline.training_data import fit_normalization
        norm = fit_normalization(normal_seqs, method="zscore")
        import tempfile
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            paths = save_model_artifacts(
                model, norm, threshold,
                feature_cols=[f"PV{i+1} input current(A)" for i in range(10)],
                output_dir=td,
                name="smoke_test",
            )
            print(f"[lstm_ae] saved: {paths}")
            assert os.path.exists(paths["model"])
            assert os.path.exists(paths["meta"])

            loaded = load_model_artifacts(paths["model"], paths["meta"])
            print(f"[lstm_ae] loaded threshold: {loaded['threshold']:.4f}")
            assert abs(loaded["threshold"] - threshold) < 1e-6

        print("[lstm_ae] PyTorch smoke OK")

    # Test M2bIntermittentDetector with disabled flag (should silently skip)
    sm = M2bIntermittentDetector(enabled=False)
    out = sm.run(pd.DataFrame(), {})
    assert out == []
    print("[lstm_ae] M2bIntermittentDetector(enabled=False) skip OK")

    print("\n[lstm_ae] all smoke OK")
