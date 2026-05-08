"""Per-inverter heatmap visualization.

Heatmap layout:
- Sumbu Y: PV1..PV{pv_max_allowed}
- Sumbu X: Start Time
- Warna  : Normalized PV power per timestamp (0=Min, 1=Max), RdYlGn.
  PV string yang ditandai *empty* di EMPTY_PV_MAP di-overlay putih.

Catatan:
- Normalisasi dilakukan per-kolom (per timestamp) -> green = strongest among
  PV strings in that moment, red = weakest. Berguna sebagai *peer comparison*
  cepat. Behaviornya identik dengan notebook v1.2 (parity-preserving).
"""

from __future__ import annotations

import time
import traceback
import warnings
from typing import Dict, List, Optional, Tuple

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.patches import Rectangle


# ---------------------------------------------------------------------------
# Defaults (sesuai Cell 3 notebook v1.2)
# ---------------------------------------------------------------------------
PV_MAX_ALLOWED_DEFAULT = 28
CELL_SIZE_DEFAULT = 0.22
PAUSE_SECONDS_DEFAULT = 0.15
BLACK_VALUE = -0.1  # value yang dipetakan ke warna hitam (no-data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _suppress_mpl_warnings() -> None:
    try:
        warnings.filterwarnings(
            "ignore", category=mpl.MatplotlibDeprecationWarning  # type: ignore[attr-defined]
        )
    except Exception:
        warnings.filterwarnings(
            "ignore", message=".*get_cmap.*", category=DeprecationWarning
        )


def _build_black_rdylgn_cmap() -> mpl.colors.ListedColormap:
    """Buat colormap RdYlGn dengan slot pertama warna hitam (untuk no-data)."""
    cmap_base = mpl.colormaps.get_cmap("RdYlGn")
    base_colors = cmap_base(np.linspace(0, 1, 256))
    black = np.array([0.0, 0.0, 0.0, 1.0])
    return mpl.colors.ListedColormap(np.vstack([black, base_colors]), name="Black_RdYlGn")


def _resolve_date_str(pivot_plot: pd.DataFrame, df_inv: pd.DataFrame) -> str:
    """Tentukan label tanggal untuk title heatmap."""
    try:
        if pivot_plot.shape[1] > 0:
            first_col = pivot_plot.columns[0]
            date_dt = pd.to_datetime(first_col, errors="coerce")
            if not pd.isna(date_dt):
                return date_dt.strftime("%Y%m%d")
        smin = df_inv["Start Time"].min()
        if not pd.isna(smin):
            return pd.to_datetime(smin).strftime("%Y%m%d")
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# Single-inverter plot
# ---------------------------------------------------------------------------
def plot_single_inv_heatmap(
    inv_id: str,
    df: pd.DataFrame,
    pv_max_allowed: int = PV_MAX_ALLOWED_DEFAULT,
    cell_size: float = CELL_SIZE_DEFAULT,
    show: bool = True,
    close_after_show: bool = False,
    empty_pv_map: Optional[Dict[str, List[int]]] = None,
    availability_overlay: Optional[Dict[str, object]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Plot heatmap PV power untuk satu inverter.

    Parameters
    ----------
    inv_id : str
        Inverter id (mis. ``"WB02-INV14"``).
    df : pd.DataFrame
        DataFrame yang sudah dipersiapkan via :func:`prepare_df_work`.
        Harus berisi kolom ``WB``, ``Inverter_ID``, ``Start Time``, dan
        kolom ``PV{n} Power(kW)``.
    pv_max_allowed : int
        Jumlah PV string maksimum yang ditampilkan.
    cell_size : float
        Ukuran sel heatmap (inci) untuk auto-sizing fig.
    show : bool
        Bila True, panggil ``plt.show()``.
    close_after_show : bool
        Bila True, panggil ``plt.close()`` setelah show (hemat memori untuk batch).
    empty_pv_map : dict, optional
        Mapping ``inv_id -> list[int]`` PV indices yang dianggap "empty"
        (di-overlay putih).

    Returns
    -------
    (pivot, pivot_norm)
        ``pivot`` = nilai PV power asli (kW) per (PV_label, Start Time).
        ``pivot_norm`` = nilai dinormalisasi 0-1 per kolom waktu.
    """
    _suppress_mpl_warnings()

    if df is None:
        raise RuntimeError("DataFrame argument cannot be None.")

    wb_prefix = inv_id.split("-")[0]
    wb_data = df[df["WB"] == wb_prefix]
    if wb_data.empty:
        raise ValueError(f"No data found for WB={wb_prefix}. Check your data.")

    pv_labels = [f"PV{i} Power(kW)" for i in range(1, pv_max_allowed + 1)]
    df_inv = wb_data[wb_data["Inverter_ID"] == inv_id].copy()
    if df_inv.empty:
        raise ValueError(f"No rows for inverter {inv_id}")

    # Pseudo-time fallback: bila Start Time semua NaN, generate index sintetis
    if not df_inv["Start Time"].notna().any():
        pseudo_times = pd.Index(
            [pd.Timestamp(0) + pd.Timedelta(seconds=i) for i in range(len(df_inv))],
            name="Start Time",
        )
        df_inv = df_inv.reset_index(drop=True)
        df_inv["Start Time"] = pseudo_times
    else:
        df_inv = df_inv[df_inv["Start Time"].notna()].copy()

    # Treat 0 as NaN, lalu mask EMPTY_PV_MAP -> NaN supaya tidak ikut normalisasi
    df_inv[pv_labels] = df_inv[pv_labels].replace(0, np.nan)
    if empty_pv_map and inv_id.upper() in empty_pv_map:
        for pv_num in empty_pv_map[inv_id.upper()]:
            lbl = f"PV{pv_num} Power(kW)"
            if lbl in df_inv.columns:
                df_inv[lbl] = np.nan

    # Long-form -> pivot (PV_label x Start Time)
    pivot = (
        df_inv.reset_index()
        .melt(
            id_vars="Start Time",
            value_vars=pv_labels,
            var_name="PV_label",
            value_name="PV_power_kW",
        )
        .pivot_table(
            index="PV_label",
            columns="Start Time",
            values="PV_power_kW",
            aggfunc="mean",
        )
    )
    pivot = pivot.reindex(pv_labels).astype(float)

    if pivot.shape[1] == 0:
        raise RuntimeError(f"Pivot has no time columns for inverter {inv_id}")

    # Normalisasi per kolom (per timestamp) -> peer comparison antar PV string
    pivot_norm = pivot.apply(
        lambda col: (col - col[col > 0].min()) / (col[col > 0].max() - col[col > 0].min())
        if col[col > 0].notna().any()
        else col,
        axis=0,
    )
    pivot_norm = pivot_norm.clip(lower=0).fillna(-0.1)

    pivot_plot = pivot_norm.fillna(BLACK_VALUE)
    cmap_wb = _build_black_rdylgn_cmap()

    n_pv, n_time = pivot_plot.shape
    fig_w = max(6, min(160, int(n_time * (cell_size or CELL_SIZE_DEFAULT))))
    fig_h = max(3, min(40, int(n_pv * (cell_size or CELL_SIZE_DEFAULT))))
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    sns.heatmap(
        pivot_plot,
        ax=ax,
        cmap=cmap_wb,
        vmin=BLACK_VALUE,
        vmax=1.0,
        center=0.5,
        cbar_kws={"label": "Normalized PV power (per PV row, 0=Min,1=Max)"},
        linewidths=0.25,
        linecolor="lightgray",
    )
    ax.set_ylabel("")
    ax.set_xlabel("Start Time (All times)")
    y_labels_short = [f"PV{i}" for i in range(1, n_pv + 1)]
    ax.set_yticklabels(y_labels_short, fontsize=8, rotation=0)

    # White overlay untuk row PV yang memang empty
    if empty_pv_map and inv_id.upper() in empty_pv_map:
        for pv_num in empty_pv_map[inv_id.upper()]:
            lbl = f"PV{pv_num} Power(kW)"
            try:
                ridx = list(pivot_norm.index).index(lbl)
                rect = Rectangle(
                    (0, ridx),
                    n_time,
                    1,
                    facecolor="white",
                    edgecolor="lightgray",
                    linewidth=0.35,
                    zorder=5,
                )
                ax.add_patch(rect)
            except ValueError:
                pass

    date_str = _resolve_date_str(pivot_plot, df_inv)
    ax.set_title(f"{inv_id} stringmap {date_str}")

    # X-axis labels (timestamp)
    try:
        x_dt = [pd.to_datetime(x) for x in pivot_plot.columns]
        if n_time > 24:
            step = max(1, n_time // 24)
            pos = np.arange(0, n_time, step) + 0.5
            labels = [x_dt[i].strftime("%Y-%m-%d\n%H:%M") for i in range(0, n_time, step)]
            ax.set_xticks(pos)
            ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
        else:
            ax.set_xticks(np.arange(n_time) + 0.5)
            ax.set_xticklabels(
                [x.strftime("%Y-%m-%d\n%H:%M") for x in x_dt],
                rotation=45,
                ha="right",
                fontsize=8,
            )
    except Exception:
        ax.set_xticks(np.arange(n_time) + 0.5)
        ax.set_xticklabels(
            [str(x) for x in pivot_plot.columns],
            rotation=45,
            ha="right",
            fontsize=8,
        )

    if availability_overlay:
        try:
            inv_status_per_ts = availability_overlay.get("inv_status_per_ts", {})
            proxy_down_cells = availability_overlay.get("proxy_down_cells", set())
            ts_index = list(pivot_plot.columns)
            for ci, ts in enumerate(ts_index):
                cls = inv_status_per_ts.get((inv_id, ts), "UNKNOWN")
                if cls != "ON":
                    rect = Rectangle((ci, 0), 1, n_pv,
                                     facecolor="gray", alpha=0.25, edgecolor="none",
                                     zorder=4)
                    ax.add_patch(rect)
            for (inv, pv_name, ts) in proxy_down_cells:
                if inv != inv_id or ts not in ts_index:
                    continue
                ci = ts_index.index(ts)
                pv_label = f"{pv_name} Power(kW)"
                if pv_label not in pivot_plot.index:
                    continue
                ri = list(pivot_plot.index).index(pv_label)
                rect = Rectangle((ci, ri), 1, 1,
                                 facecolor="none", edgecolor="red", linewidth=1.5,
                                 zorder=6)
                ax.add_patch(rect)
        except Exception as e:
            print(f"[viz] availability overlay skipped: {e}")
    plt.tight_layout()
    if show:
        plt.show()
    if close_after_show:
        plt.close(fig)
    return pivot, pivot_norm


# ---------------------------------------------------------------------------
# Batch loop
# ---------------------------------------------------------------------------
def plot_all_inverters(
    df_plot: pd.DataFrame,
    cell_size: float = CELL_SIZE_DEFAULT,
    empty_pv_map: Optional[Dict[str, List[int]]] = None,
    pv_max_allowed: int = PV_MAX_ALLOWED_DEFAULT,
    max_to_plot: Optional[int] = None,
    pause_seconds: float = PAUSE_SECONDS_DEFAULT,
    close_after_show: bool = False,
    availability_overlay: Optional[Dict[str, object]] = None,
) -> Tuple[int, List[Tuple[str, str]]]:
    """Iterate :func:`plot_single_inv_heatmap` untuk seluruh ``Inverter_ID`` di df_plot.

    Returns
    -------
    (count, errors)
        ``count``  = jumlah inverter yang berhasil di-plot.
        ``errors`` = list of (inverter_id, message) untuk yang gagal.
    """
    all_invs = sorted(set(df_plot["Inverter_ID"].dropna().unique()))
    if max_to_plot is not None:
        all_invs = all_invs[:max_to_plot]

    count = 0
    errors: List[Tuple[str, str]] = []
    for idx, inv in enumerate(all_invs, start=1):
        try:
            print(f"[{idx}/{len(all_invs)}] Plotting: {inv}")
            plot_single_inv_heatmap(
                inv_id=inv,
                df=df_plot,
                pv_max_allowed=pv_max_allowed,
                cell_size=cell_size,
                show=True,
                close_after_show=close_after_show,
                empty_pv_map=empty_pv_map,
                availability_overlay=availability_overlay,
            )
            count += 1
            if pause_seconds:
                time.sleep(pause_seconds)
        except Exception as err:
            print(f"Error for {inv}: {err}")
            traceback.print_exc()
            errors.append((inv, str(err)))
            continue

    print("All plots finished.")
    print(f"Plotted: {count}  |  Errors: {len(errors)}")
    if errors:
        print("First errors:")
        for err in errors[:10]:
            print(" -", err[0], ":", err[1])
    return count, errors
